"""建全部 11 张表与 5 个枚举类型

Revision ID: 0001_init
Revises:

用原生 SQL 而不是 op.create_table()：`Design/database/sql.md` 里的 DDL 是被逐条
评审过的产物，连 COMMENT ON COLUMN 都写了理由。翻译成 SQLAlchemy ops 会丢掉一部分
（注释、NULLS NOT DISTINCT 的写法、部分唯一索引、枚举的创建顺序），而这套 schema
本来就只跑 PostgreSQL 16，没有跨方言的需要。

代价是 ORM 那边成了第二份定义，两份必然会漂。`tests/test_schema_drift.py` 拿
information_schema 跟 Base.metadata 对账，把漂移变成一条红测试而不是线上事故。

本文件相对 `sql.md` 基线有若干**刻意的偏离**，每处都在下面就地注明了理由；
它们需要回写进 `sql.md`，清单见 `Todo/Plans/booksoul-web-plan.md` 的 Slice 1 偏差记录。
"""

from __future__ import annotations

from alembic import op

revision = "0001_init"
down_revision = None
branch_labels = None
depends_on = None


_UPGRADE = """
CREATE EXTENSION IF NOT EXISTS citext;

CREATE TYPE book_status    AS ENUM ('parsing', 'ready', 'failed');
CREATE TYPE parse_stage    AS ENUM ('read', 'toc', 'convert', 'clean', 'write');
CREATE TYPE note_severity  AS ENUM ('doubt', 'block');
CREATE TYPE persona_key    AS ENUM ('scholar', 'author', 'book');
CREATE TYPE message_role   AS ENUM ('user', 'assistant');

-- updated_at 靠触发器刷新，不靠应用记得。
-- PostgreSQL 不会自动维护它：DEFAULT now() 只在 INSERT 时生效。
-- 而 idx_conversations_user (user_id, updated_at DESC) 服务的正是书库首页那条
-- 「最近问过的」——服务层漏刷一次，这个列表就静默退化成创建时间序，不报错、只是排错。
CREATE FUNCTION touch_updated_at() RETURNS trigger AS $fn$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;
COMMENT ON FUNCTION touch_updated_at() IS '所有带 updated_at 的表共用的 BEFORE UPDATE 触发器。updated_at 由数据库刷新，应用层不要也不必自己赋值——两边都写就是两份真相';

-- ---------------------------------------------------------------- users
CREATE TABLE users (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email         CITEXT       NOT NULL UNIQUE,
    password_hash TEXT         NOT NULL,
    display_name  VARCHAR(64)  NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE TRIGGER trg_users_touch BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
COMMENT ON TABLE  users IS '账号。模型 API key 不在此表，也不在任何表——只存用户浏览器';
COMMENT ON COLUMN users.email IS 'CITEXT：大小写不敏感，A@b.com 与 a@b.com 是同一个账号';
COMMENT ON COLUMN users.password_hash IS 'bcrypt 哈希，与模型 API key 无关。超 72 字节的密码先经 SHA-256 压过，见 core/security.py';

-- ---------------------------------------------------------------- books
CREATE TABLE books (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id       BIGINT       NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title         VARCHAR(255) NOT NULL,
    author        VARCHAR(255) NOT NULL DEFAULT '',
    translator    VARCHAR(255) NOT NULL DEFAULT '',
    contributor   VARCHAR(255) NOT NULL DEFAULT '',
    publisher     VARCHAR(255) NOT NULL DEFAULT '',
    published     VARCHAR(32)  NOT NULL DEFAULT '',
    isbn          VARCHAR(32)  NOT NULL DEFAULT '',
    language      VARCHAR(32)  NOT NULL DEFAULT '',
    description   TEXT         NOT NULL DEFAULT '',
    source_file   VARCHAR(255) NOT NULL DEFAULT '',
    source_format VARCHAR(16)  NOT NULL DEFAULT '',
    source_sha256 VARCHAR(64)  NOT NULL DEFAULT '',
    storage_path  TEXT,
    chapter_count INTEGER      NOT NULL DEFAULT 0,
    word_count    INTEGER      NOT NULL DEFAULT 0,
    status        book_status  NOT NULL DEFAULT 'parsing',
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT uq_books_storage_path UNIQUE (storage_path),
    CONSTRAINT ck_books_ready_has_path
        CHECK (status <> 'ready' OR storage_path IS NOT NULL),
    -- sha256 十六进制恒为 64 位小写。空串是「还没算」，也允许。
    CONSTRAINT ck_books_source_sha256_shape
        CHECK (source_sha256 = '' OR source_sha256 ~ '^[0-9a-f]{64}$')
);
CREATE INDEX idx_books_user_created ON books (user_id, created_at DESC);
CREATE INDEX idx_books_user_status  ON books (user_id, status);
CREATE TRIGGER trg_books_touch BEFORE UPDATE ON books
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
COMMENT ON COLUMN books.translator   IS '仅当来源明确标了译者角色；角色不明的进 contributor，不猜';
COMMENT ON COLUMN books.contributor  IS '角色不明的贡献者。界面显示「未标注」而不是猜成译者';
COMMENT ON COLUMN books.storage_path IS '正文不入库，章节 md 落在书库根下这个路径里。目录名含 book_id，而 id 要 INSERT 之后才有——所以此列**可空**：建行时还没有目录，解析写盘后再 UPDATE 填上。UNIQUE 防两本书写进同一个目录互相覆盖；PostgreSQL 默认 NULLS DISTINCT，多本书同时处于 parsing 态互不冲突';
COMMENT ON COLUMN books.source_sha256 IS 'VARCHAR 而非 CHAR：CHAR(64) 会把空值补成 64 个空格，Python 侧判空恒为假';
COMMENT ON COLUMN books.published    IS '出版日期原样保留：来源格式不统一，转 DATE 会丢信息';
COMMENT ON COLUMN books.word_count   IS '一个汉字算一个字，连续西文算一个词';
COMMENT ON COLUMN books.status       IS 'parsing 解析中（靛）/ ready 就绪（不着色）/ failed 停下了（重墨）。成功是默认态，不给颜色';

-- ---------------------------------------------------------------- chapters
CREATE TABLE chapters (
    id         BIGINT       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    book_id    BIGINT       NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    idx        INTEGER      NOT NULL,
    title      VARCHAR(255) NOT NULL DEFAULT '',
    level      SMALLINT     NOT NULL DEFAULT 1,
    word_count INTEGER      NOT NULL DEFAULT 0,
    file_path  TEXT         NOT NULL,
    source_id  VARCHAR(64)  NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT uq_chapters_book_idx UNIQUE (book_id, idx),
    CONSTRAINT ck_chapters_idx_positive CHECK (idx >= 1)
);
COMMENT ON TABLE  chapters IS '章节索引。正文在磁盘上，此表只存定位信息与统计。重解析整批重建，因此无 updated_at';
COMMENT ON COLUMN chapters.book_id    IS 'FK 的索引由 uq_chapters_book_idx (book_id, idx) 的唯一索引承担，不另建——两者列与列序完全相同';
COMMENT ON COLUMN chapters.idx        IS '1 起的阅读序号，与章节文件名前缀一致。注意它不是主键：engine 的 load_from_path(only_idx=...) 收的是这个 idx，不是 id';
COMMENT ON COLUMN chapters.word_count IS '字数条即质检信号：异常短的章节要提示用户核对';
COMMENT ON COLUMN chapters.source_id  IS '来源定位：epub 的 spine idref / pdf 的页码区间';

-- ---------------------------------------------------------------- parse_jobs
CREATE TABLE parse_jobs (
    id              BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    book_id         BIGINT      NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    stage           parse_stage NOT NULL DEFAULT 'read',
    progress_done   INTEGER     NOT NULL DEFAULT 0,
    progress_total  INTEGER     NOT NULL DEFAULT 0,
    detected_format VARCHAR(16) NOT NULL DEFAULT '',
    error_code      VARCHAR(32) NOT NULL DEFAULT '',
    error_message   TEXT        NOT NULL DEFAULT '',
    remedy          TEXT        NOT NULL DEFAULT '',
    cleanup_stats   JSONB       NOT NULL DEFAULT '{}'::jsonb,
    heartbeat_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_parse_jobs_book ON parse_jobs (book_id, created_at DESC);
CREATE INDEX idx_parse_jobs_alive ON parse_jobs (heartbeat_at) WHERE finished_at IS NULL;
CREATE TRIGGER trg_parse_jobs_touch BEFORE UPDATE ON parse_jobs
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
COMMENT ON COLUMN parse_jobs.detected_format IS '按文件魔数判定，不看扩展名——下载来的书扩展名经常是错的';
COMMENT ON COLUMN parse_jobs.error_code      IS '如 no_text_layer / bad_zip / no_content / orphaned';
COMMENT ON COLUMN parse_jobs.remedy          IS '失败必须给出路。抽不出文字的扫描版 PDF 在此给 OCR 命令，而不是写出空 md';
COMMENT ON COLUMN parse_jobs.cleanup_stats   IS '清洗账本：合并了多少硬换行、剥了多少全角空格、脚注接上几条、目录来源哪条路线。这些数字在引擎里是瞬时的，不落库解析报告就无从谈起';
COMMENT ON COLUMN parse_jobs.heartbeat_at    IS '解析推进时更新。服务重启后据此把心跳停滞的任务标为 orphaned，不让书永远卡在解析中';
COMMENT ON COLUMN parse_jobs.created_at      IS '入队时刻。与 started_at 分开是为了让孤儿清扫能区分「排上队了但后台任务没起来」和「跑起来又停滞」';
COMMENT ON COLUMN parse_jobs.started_at      IS '后台任务真正开跑的时刻。可空——排队中的任务还没开始';

-- ---------------------------------------------------------------- parse_notes
CREATE TABLE parse_notes (
    id         BIGINT        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    book_id    BIGINT        NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    chapter_id BIGINT        REFERENCES chapters(id) ON DELETE SET NULL,
    severity   note_severity NOT NULL DEFAULT 'doubt',
    code       VARCHAR(32)   NOT NULL DEFAULT '',
    message    TEXT          NOT NULL,
    resolved   BOOLEAN       NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ   NOT NULL DEFAULT now()
);
CREATE INDEX idx_parse_notes_book    ON parse_notes (book_id, resolved);
CREATE INDEX idx_parse_notes_chapter ON parse_notes (chapter_id);
CREATE TRIGGER trg_parse_notes_touch BEFORE UPDATE ON parse_notes
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
COMMENT ON TABLE  parse_notes IS '解析产生的存疑与阻断项，来自 Book.warnings。解析成功不等于解析正确，这是可核对的账';
COMMENT ON COLUMN parse_notes.severity IS 'doubt 存疑（黄檗，等人判断）/ block 阻断（重墨，停下来了）';
COMMENT ON COLUMN parse_notes.code     IS 'short_chapter / encoding_guessed / no_toc / moderation_blocked';
COMMENT ON COLUMN parse_notes.resolved IS '用户点开核对过了，之后不再打扰。updated_at 即核对时刻';

-- ---------------------------------------------------------------- brains
CREATE TABLE brains (
    id             BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id        BIGINT      REFERENCES users(id) ON DELETE CASCADE,
    name           VARCHAR(32) NOT NULL,
    provider       VARCHAR(16) NOT NULL,
    model          VARCHAR(64) NOT NULL,
    base_url       TEXT        NOT NULL DEFAULT '',
    api_key_env    VARCHAR(64) NOT NULL DEFAULT '',
    context_tokens INTEGER     NOT NULL DEFAULT 0,
    max_tokens     INTEGER     NOT NULL DEFAULT 8000,
    note           TEXT        NOT NULL DEFAULT '',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_brains_user_name UNIQUE NULLS NOT DISTINCT (user_id, name),
    -- 「库里绝不出现 API key」这条硬规则不能只写在注释里——注释挡不住 INSERT。
    -- 环境变量名形如 DASHSCOPE_API_KEY：字母数字下划线，不含连字符；
    -- 而真 key（sk-ant-api03-… / sk-…）必然含连字符，会被这条挡下。
    CONSTRAINT ck_brains_api_key_env_is_identifier
        CHECK (api_key_env = '' OR api_key_env ~ '^[A-Za-z_][A-Za-z0-9_]{0,62}$'),
    -- URL 的 userinfo 段（https://sk-xxx@host/v1）是另一条能把 key 带进库的路。
    -- 查询串里的 key（?api-key=sk-xxx）挡不住——Azure 的 ?api-version= 是合法的，
    -- 一刀切会误伤，那一侧留给 S6 在服务层处理。
    CONSTRAINT ck_brains_base_url_no_userinfo
        CHECK (base_url = '' OR base_url !~ '^[A-Za-z][A-Za-z0-9+.-]*://[^/]*@')
);
CREATE INDEX idx_brains_user ON brains (user_id);
CREATE TRIGGER trg_brains_touch BEFORE UPDATE ON brains
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
COMMENT ON TABLE  brains             IS '大脑预设。此表不存 API key——key 只在用户浏览器 localStorage，请求时经 X-Brain-Key 头带来，用完即丢';
COMMENT ON COLUMN brains.user_id     IS 'NULL = 内置全局预设。UNIQUE NULLS NOT DISTINCT 让内置预设的名字也唯一';
COMMENT ON COLUMN brains.api_key_env IS '只存环境变量【名字】（如 DASHSCOPE_API_KEY），供界面提示「需要哪个 key」；绝不存值。CHECK 从库这一层堵死误填真 key';
COMMENT ON COLUMN brains.base_url    IS '自定义 OpenAI 兼容端点地址。CHECK 禁止 userinfo 段，那是 key 混进库的一条现成通道';
COMMENT ON COLUMN brains.context_tokens IS '上下文窗口，用于回答「这本书装得下吗」';

-- ---------------------------------------------------------------- conversations
CREATE TABLE conversations (
    id                   BIGINT       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id              BIGINT       NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    book_id              BIGINT       NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    persona              persona_key  NOT NULL DEFAULT 'scholar',
    brain_name           VARCHAR(32)  NOT NULL,
    model                VARCHAR(64)  NOT NULL,
    budget_chars         INTEGER      NOT NULL DEFAULT 0,
    reflection           BOOLEAN      NOT NULL DEFAULT false,
    title                VARCHAR(255) NOT NULL DEFAULT '',
    selected_chapter_ids JSONB        NOT NULL DEFAULT '[]'::jsonb,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT ck_conversations_selected_is_array
        CHECK (jsonb_typeof(selected_chapter_ids) = 'array')
);
CREATE INDEX idx_conversations_user ON conversations (user_id, updated_at DESC);
CREATE INDEX idx_conversations_book ON conversations (book_id);
CREATE TRIGGER trg_conversations_touch BEFORE UPDATE ON conversations
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
COMMENT ON COLUMN conversations.persona              IS '主干人格。对读产生的旁支记在 messages.persona 上。注意它可被「跟这个继续聊」改写，因此不能用它反推历史轮次属于哪一支——见 Plan S10';
COMMENT ON COLUMN conversations.brain_name           IS '冗余存名字而不是外键指向 brains：预设被改名或删掉都不该篡改历史记录的真实来源';
COMMENT ON COLUMN conversations.model                IS '同上，与 brain_name 一起冗余存下当时真正调用的模型';
COMMENT ON COLUMN conversations.budget_chars         IS '0 是魔法值 = 按所选模型的上下文窗口自动估；非 0 为用户手动指定';
COMMENT ON COLUMN conversations.reflection           IS '处境模式：读者带来的是自己的困惑而非书的知识问题时置 true，人格提示词后附加 REFLECTION_SUFFIX';
COMMENT ON COLUMN conversations.selected_chapter_ids IS '第一轮选定后锁定的 chapters.id 数组，后续轮次原样复用不重跑 BM25；重解析时清空。注意引擎的 load_from_path(only_idx=...) 收的是 chapters.idx 不是 id，中间必须翻译一次';

-- ---------------------------------------------------------------- messages
CREATE TABLE messages (
    id              BIGINT       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    conversation_id BIGINT       NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    seq             INTEGER      NOT NULL,
    turn            INTEGER      NOT NULL,
    role            message_role NOT NULL,
    persona         persona_key,
    content         TEXT         NOT NULL,
    complete        BOOLEAN      NOT NULL DEFAULT true,
    input_tokens    INTEGER      NOT NULL DEFAULT 0,
    output_tokens   INTEGER      NOT NULL DEFAULT 0,
    cache_read      INTEGER      NOT NULL DEFAULT 0,
    cache_write     INTEGER      NOT NULL DEFAULT 0,
    stop_reason     VARCHAR(32)  NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT uq_messages_conv_seq UNIQUE (conversation_id, seq),
    CONSTRAINT ck_messages_seq_positive  CHECK (seq >= 1),
    CONSTRAINT ck_messages_turn_positive CHECK (turn >= 1),
    -- persona 的可空性不是随意的：唯一索引的 NULLS NOT DISTINCT 语义整个建立在
    -- 「提问 persona 为 NULL、回答 persona 非 NULL」之上。没有这条 CHECK，
    -- S7（早于 S10 落地，那时还没有对读）很容易把 assistant 行写成 persona NULL，
    -- 它会和同一轮的提问撞唯一索引，第一条回答就落不了库。
    CONSTRAINT ck_messages_persona_matches_role CHECK (
        (role = 'user'      AND persona IS NULL) OR
        (role = 'assistant' AND persona IS NOT NULL)
    )
);
-- 部分唯一索引而非表级唯一约束：残缺回答（complete=false）**不占坑位**。
-- 断流时 finally 会落一条 complete=false 的残文保住已生成内容，用户随后点「重问」
-- 要能在同一轮同人格再生成一条。若用表级唯一约束，那条残文会把坑位占死，
-- 服务层只剩「删掉残文」这一条路——而那恰好毁掉 S7「已生成的部分被保住」这条验收。
CREATE UNIQUE INDEX uq_messages_conv_turn_persona
    ON messages (conversation_id, turn, persona) NULLS NOT DISTINCT
    WHERE complete;
CREATE TRIGGER trg_messages_touch BEFORE UPDATE ON messages
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
COMMENT ON COLUMN messages.seq        IS '会话内顺序，1 起。对读一轮产生 4 条消息，seq 各不相同而 turn 相同。并发写三路人格时 seq 需在同一事务里分配，别各算各的 max(seq)+1';
COMMENT ON COLUMN messages.turn       IS '轮次。一个问题连同它引出的一到三条回答同属一个 turn';
COMMENT ON COLUMN messages.persona    IS 'user 消息为 NULL；assistant 消息记它是哪一路人格。由 ck_messages_persona_matches_role 钉死';
COMMENT ON COLUMN messages.complete   IS 'false = 断流留下的残缺回答。已生成的部分要保住，用户花的钱不能白花。残缺行不进唯一索引，重问才有路可走';
COMMENT ON COLUMN messages.cache_read IS '命中的缓存 token。书放在缓存断点之前，切人格不失效，这一列能看出省了多少';
COMMENT ON COLUMN messages.conversation_id IS 'FK 的索引由 uq_messages_conv_seq (conversation_id, seq) 的唯一索引承担，不另建';

-- ---------------------------------------------------------------- retrievals
CREATE TABLE retrievals (
    id              BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    conversation_id BIGINT      NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    turn            INTEGER     NOT NULL,
    budget_chars    INTEGER     NOT NULL,
    used_chars      INTEGER     NOT NULL,
    selected_count  INTEGER     NOT NULL,
    total_count     INTEGER     NOT NULL,
    whole_book      BOOLEAN     NOT NULL DEFAULT false,
    query_terms     JSONB       NOT NULL DEFAULT '[]'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_retrievals_conv_turn UNIQUE (conversation_id, turn),
    CONSTRAINT ck_retrievals_turn_positive CHECK (turn >= 1),
    CONSTRAINT ck_retrievals_terms_is_array CHECK (jsonb_typeof(query_terms) = 'array')
);
COMMENT ON TABLE  retrievals IS '一轮提问的选章决策。挂 turn 而不挂 message：对读一轮有三条回答却只选一次章，挂 message 会存三份一模一样的。同一轮「重问」必须复用这一行（INSERT ... ON CONFLICT (conversation_id, turn) DO UPDATE），不要为重问另起 turn——否则同一个问题会在 messages 里留下两条提问，而会话锁定的本意正是复用不重跑';
COMMENT ON COLUMN retrievals.whole_book  IS '整本都放下了，没走 BM25。界面据此说明模型拿到的是全书';
COMMENT ON COLUMN retrievals.query_terms IS '切出来并参与打分的查询词。中文用一元+二元切分，跨词边界的垃圾二元组已在引擎侧过滤';
COMMENT ON COLUMN retrievals.conversation_id IS 'FK 的索引由 uq_retrievals_conv_turn 的唯一索引承担，不另建';

-- ---------------------------------------------------------------- retrieval_items
CREATE TABLE retrieval_items (
    id            BIGINT        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    retrieval_id  BIGINT        NOT NULL REFERENCES retrievals(id) ON DELETE CASCADE,
    chapter_id    BIGINT        REFERENCES chapters(id) ON DELETE SET NULL,
    chapter_idx   INTEGER       NOT NULL,
    chapter_title VARCHAR(255)  NOT NULL DEFAULT '',
    rank          INTEGER       NOT NULL,
    score         NUMERIC(10,4) NOT NULL DEFAULT 0,
    word_count    INTEGER       NOT NULL DEFAULT 0,
    selected      BOOLEAN       NOT NULL DEFAULT false,
    matched_terms JSONB         NOT NULL DEFAULT '[]'::jsonb,
    created_at    TIMESTAMPTZ   NOT NULL DEFAULT now(),
    CONSTRAINT uq_retrieval_items_rank UNIQUE (retrieval_id, rank),
    CONSTRAINT ck_retrieval_items_rank_positive CHECK (rank >= 1),
    CONSTRAINT ck_retrieval_items_terms_is_array CHECK (jsonb_typeof(matched_terms) = 'array')
);
CREATE INDEX idx_retrieval_items_chap ON retrieval_items (chapter_id);
COMMENT ON COLUMN retrieval_items.rank          IS '按 BM25 分数降序的名次，1 起。(retrieval_id, rank) 唯一，防一次写入重试写出两套完整排名';
COMMENT ON COLUMN retrieval_items.selected      IS '预算线之下的章节也留档：用户要能看到「差 504 字装不下」这种事';
COMMENT ON COLUMN retrieval_items.matched_terms IS '[{"term":"佛陀","count":14}]';
COMMENT ON COLUMN retrieval_items.chapter_id    IS 'SET NULL 而非 CASCADE：重解析会重建 chapters，CASCADE 会把用户过去所有对话的检索留档一次清空';
COMMENT ON COLUMN retrieval_items.chapter_idx   IS '当时的章节序号快照。chapter_id 被 SET NULL 之后，这一列和 chapter_title 让排名表仍然读得出「读了哪几节」——那正是这张表存在的理由';
COMMENT ON COLUMN retrieval_items.chapter_title IS '当时的章节标题快照，同上';

-- ---------------------------------------------------------------- citations
CREATE TABLE citations (
    id         BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    message_id BIGINT      NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    chapter_id    BIGINT   REFERENCES chapters(id) ON DELETE SET NULL,
    chapter_title VARCHAR(255) NOT NULL DEFAULT '',
    quote      TEXT        NOT NULL,
    para_index INTEGER     NOT NULL DEFAULT -1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_citations_message ON citations (message_id);
CREATE INDEX idx_citations_chapter ON citations (chapter_id);
CREATE TRIGGER trg_citations_touch BEFORE UPDATE ON citations
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
COMMENT ON TABLE  citations            IS '溯源：回答引了哪一章哪一段，供阅读页跳转并高亮';
COMMENT ON COLUMN citations.para_index IS '落库时把引文与章节正文比对得出；对不上填 -1，不猜';
COMMENT ON COLUMN citations.chapter_id IS 'SET NULL 而非 CASCADE：重解析后引文降级为「原位置已变更」是可接受的，凭空消失不是';
COMMENT ON COLUMN citations.chapter_title IS '当时的章节标题快照。chapter_id 被 SET NULL 之后，界面还能说出「原《婆罗门之子》里这段，位置已变更」，而不是只剩一个灰按钮';
COMMENT ON COLUMN citations.updated_at IS 'S12 的 CitationRelocator 重定位后回写。与 created_at 不等即说明这条引文被重定位过，界面据此区分';
"""


_DOWNGRADE = """
DROP TABLE IF EXISTS citations, retrieval_items, retrievals, messages,
                     conversations, brains, parse_notes, parse_jobs,
                     chapters, books, users CASCADE;
DROP FUNCTION IF EXISTS touch_updated_at();
DROP TYPE IF EXISTS message_role, persona_key, note_severity, parse_stage, book_status;
"""


def upgrade() -> None:
    # exec_driver_sql 而不是 op.execute：后者会把字符串当 text() 解析绑定参数，
    # 而这段 DDL 的注释里有 '[{"term":"佛陀","count":14}]'，其中的 :14 会被
    # 当成一个叫 "14" 的绑定参数，报「A value is required for bind parameter」。
    # 原生 DDL 本来就不需要参数绑定，直接交给驱动最省事。
    op.get_bind().exec_driver_sql(_UPGRADE)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(_DOWNGRADE)
