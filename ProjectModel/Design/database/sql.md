# 数据库建表 SQL

> **本文档职责**：保存所有表的原生建表 SQL（DDL），含字段、类型、约束、索引、注释。
> 表关系文字说明见 `database-relations.md`；ER 图见 `data-er.md`。
> 由 `to-design` 生成（正向 0-to-1 / 逆向）、由 `design-doc-sync` 在 schema 变更时同步更新（含必要的迁移说明）。
> **「约定」章节例外**：它是数据库层的项目级代码规范，只由 `to-design` 首次建立，或由 `tdd` / `read-design-before-code` 列出规范候选、经你显式判定后写入；`design-doc-sync` 不改动它。
>
> 以下为约定骨架与示例，按实际项目填充；每张表一个代码块，字段务必带注释。
>
> **方言：PostgreSQL 16**〔来源：用户指定〕

## 约定（数据库代码规范）

- 表名 snake_case 复数：`books`、`retrieval_items`。〔来源：PostgreSQL 社区基线，用户确认 2026-09-16〕
- 列名 snake_case 单数。后端实体同名不转换，序列化时才转 camelCase（见 `backend.md`「跨层命名对照」）。〔来源：用户确认 2026-09-16〕
- 主键一律 `id BIGINT GENERATED ALWAYS AS IDENTITY`，不用 UUID、不用业务主键。〔来源：PostgreSQL 16 社区基线（`SERIAL` 已不推荐）〕
- 外键列名 `<单数表名>_id`：`book_id`、`conversation_id`。〔来源：社区基线，用户确认 2026-09-16〕
- 时间列一律 `TIMESTAMPTZ`，不用 `TIMESTAMP`；每张表带 `created_at`，可变表另带 `updated_at`。〔来源：PostgreSQL 社区基线〕
- 布尔列不加 `is_` 前缀，用自然语义词：`selected`、`resolved`、`whole_book`。〔来源：与引擎一致——`library.py` 用 `partial`、`retrieve.py` 用 `whole`，抽样 4/4〕
- 枚举用 `CREATE TYPE ... AS ENUM`，**值一律英文**；中文展示名由前端映射，不入库。〔来源：社区基线，用户确认 2026-09-16〕
- 金额 / 分数用 `NUMERIC`，不用 `FLOAT`。BM25 分数用 `NUMERIC(10,4)`。〔来源：PostgreSQL 社区基线〕
- 半结构化数据用 `JSONB` 不用 `JSON`。〔来源：PostgreSQL 社区基线〕
- 级联：归属关系（用户→书→章节）用 `ON DELETE CASCADE`；引用关系（引文→章节）用 `ON DELETE SET NULL`，避免删章节连带删对话历史。〔来源：用户确认 2026-09-16〕
- 每个外键列都要有索引（PostgreSQL 不自动建）。〔来源：PostgreSQL 社区基线〕
- 字段注释用 `COMMENT ON COLUMN`，非平凡字段必须写。〔来源：社区基线，用户确认 2026-09-16〕
- **不存任何密钥**：库中只允许出现环境变量名等标识符，不得出现 API key 本身，即使加密也不行。〔来源：`backend.md`「认证授权」硬规则〕
- **承载数组的 JSONB 列用复数**：`selected_chapter_ids`、`query_terms`、`matched_terms`、`chapter_ids`。这是上面「列名 snake_case 单数」那条的明文例外——不写出来，下一个照约定审查的人还会再撞一次。〔来源：Plan S1 规范候选 #4，用户 2026-09-20 裁定〕
- **`updated_at` 由数据库触发器唯一负责**（`touch_updated_at()` + 每张可变表一个 `BEFORE UPDATE` 触发器），应用层不写、ORM 侧也不配 `onupdate`。两边都写就是两份真相，而不一致时不会报错——只会让「最近问过的」那类按 `updated_at DESC` 排的列表静默退化成创建时间序。〔来源：Plan S1 规范候选 #2，用户 2026-09-20 裁定；实现见 `migrations/versions/0001_init.py`〕
- **指向「代码里定义的东西」的行，按名字弱关联、不做外键；对不上的行在读的时候忽略，不报错、不清理。** 内置预设的真相在引擎的 `booksoul.config`，不在库里，没有可以指向的行；`brain_choices.brain_name` 因此只是一个名字。预设被改名或删掉之后，那一行在解析时被跳过——改回原名，选择也就回来了；清理它反而会在一次误改配置之后丢掉用户的选择。〔来源：Todo/Plans/brain-model-picker-plan.md Slice 8，用户 2026-09-22 裁定；实现见 `migrations/versions/0004_brain_choices.py`〕
- **要读的旧状态，必须在破坏它的操作之前读。** `ON DELETE SET NULL` 与级联删除会**静默**抹掉事实：重解析时若先 `replace_chapters` 再去读引文原本落在第几节，那一列已经被置空，再也读不回来。顺序错了不报错，只是让事后的报告失去意义。〔来源：Plan S12 规范候选 #39，用户 2026-09-20 裁定；实现见 `services/resettle.py:swap()`：拍下引文落点 → `replace_chapters()` → 重定位，次序只写在这一处（原为 `snapshot()`，按 Todo/Plans/turn-state-plan.md Slice 6 更新，用户 2026-09-23 裁定；按 Todo/Plans/book-lifecycle-plan.md Slice 1 收进 `swap()`，用户 2026-09-25 裁定）〕

```sql
CREATE EXTENSION IF NOT EXISTS citext;

CREATE TYPE book_status    AS ENUM ('parsing', 'ready', 'failed');
CREATE TYPE parse_stage    AS ENUM ('read', 'toc', 'convert', 'clean', 'write');
CREATE TYPE note_severity  AS ENUM ('doubt', 'block');
CREATE TYPE persona_key    AS ENUM ('scholar', 'author', 'book');
CREATE TYPE message_role   AS ENUM ('user', 'assistant');
-- moderation_events.outcome 用 VARCHAR + CHECK 而不是 ENUM：它只有两个值、且将来可能加档（如 'unknown'），
-- 而 PostgreSQL 加枚举值不能在事务里回滚，改 CHECK 约束可以。
```

## 表：users

```sql
CREATE TABLE users (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email         CITEXT       NOT NULL UNIQUE,          -- 大小写不敏感，登录账号
    password_hash TEXT         NOT NULL,                 -- bcrypt，绝不存明文
    display_name  VARCHAR(64)  NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);

COMMENT ON TABLE  users IS '账号。模型 API key 不在此表，也不在任何表——只存用户浏览器';
COMMENT ON COLUMN users.password_hash IS 'bcrypt 哈希，与模型 API key 无关';
```

## 表：books

```sql
CREATE TABLE books (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id       BIGINT       NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title         VARCHAR(255) NOT NULL,                 -- 书里写的超过列宽就截断、结尾加「…」（author 等 VARCHAR 几列同）
    author        VARCHAR(255) NOT NULL DEFAULT '',
    translator    VARCHAR(255) NOT NULL DEFAULT '',      -- 仅当来源明确标了译者角色
    contributor   VARCHAR(255) NOT NULL DEFAULT '',      -- 角色不明的贡献者，不猜成译者
    publisher     VARCHAR(255) NOT NULL DEFAULT '',
    published     TEXT         NOT NULL DEFAULT '',      -- 原样保留，格式不统一，不转 DATE；长度由书决定（0005 起 TEXT）
    isbn          TEXT         NOT NULL DEFAULT '',      -- 书里写什么存什么，可能带前缀与说明（0005 起 TEXT）
    language      VARCHAR(32)  NOT NULL DEFAULT '',
    description   TEXT         NOT NULL DEFAULT '',
    source_file   VARCHAR(255) NOT NULL DEFAULT '',      -- 原始上传文件名
    source_format VARCHAR(16)  NOT NULL DEFAULT '',      -- epub/mobi/azw3/pdf/docx/txt/html/markdown
    source_sha256 VARCHAR(64)  NOT NULL DEFAULT '',      -- 上传文件哈希，去重策略未定（见待确认）
    storage_path  TEXT,                                  -- 相对书库根：<user_id>/<book_id>-<slug>；可空，解析成功才写
    chapter_count INTEGER      NOT NULL DEFAULT 0,
    word_count    INTEGER      NOT NULL DEFAULT 0,       -- 汉字数 + 西文词数，见 model.count_words
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

COMMENT ON COLUMN books.storage_path IS '正文不入库。章节 md 落在 <书库根>/<storage_path>/chapters/。目录名含 book_id，而 id 要 INSERT 之后才有——所以此列可空：建行时还没有目录，解析成功的那个事务里才写上。有它就是「有已发布的一版」：重解析中、重解析失败之后照读它（门表的另一半）。新版先写进 <书库根>/.staging/job-<id>/，提交之后才整个换过来——这个目录从不被原地改写。UNIQUE 防两本书写进同一个目录互相覆盖；PostgreSQL 默认 NULLS DISTINCT，多本书同时处于 parsing 态互不冲突';
COMMENT ON COLUMN books.source_sha256 IS 'VARCHAR 而非 CHAR：CHAR(64) 会把空值补成 64 个空格，Python 侧判空恒为假';
COMMENT ON COLUMN books.published    IS '出版日期原样保留：来源格式不统一（2017 / 2017-01 / 2017-01-01T00:00:00+08:00），转 DATE 会丢信息；长度由书决定，所以是 TEXT';
COMMENT ON COLUMN books.word_count   IS '一个汉字算一个字，连续西文算一个词';
```

## 表：chapters

```sql
CREATE TABLE chapters (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    book_id    BIGINT       NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    idx        INTEGER      NOT NULL,                   -- 1 起的阅读序号，与文件名前缀一致
    title      VARCHAR(255) NOT NULL DEFAULT '',
    level      SMALLINT     NOT NULL DEFAULT 1,         -- 目录树深度，1 = 顶层
    word_count INTEGER      NOT NULL DEFAULT 0,
    file_path  TEXT         NOT NULL,                   -- 相对 books.storage_path，如 chapters/003-婆罗门之子.md
    source_id  TEXT         NOT NULL DEFAULT '',        -- 来源定位，原样照抄：epub「路径#锚点」/ pdf「p.3-7」/ 纯文本「line:12」（0005 起 TEXT）
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT uq_chapters_book_idx UNIQUE (book_id, idx)
);

CREATE INDEX idx_chapters_book ON chapters (book_id, idx);

COMMENT ON TABLE  chapters IS '章节索引。正文在磁盘上，此表只存定位信息与统计';
COMMENT ON COLUMN chapters.source_id IS '来源定位，原样照抄：epub 是压缩包里的「路径#锚点」，pdf 是页码区间（p.3-7），纯文本是行号（line:12）。长度由书决定，所以是 TEXT';
COMMENT ON COLUMN chapters.word_count IS '字数条即质检信号：异常短的章节要提示用户核对';
```

## 表：parse_jobs

```sql
CREATE TABLE parse_jobs (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    book_id         BIGINT      NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    stage           parse_stage NOT NULL DEFAULT 'read',
    progress_done   INTEGER     NOT NULL DEFAULT 0,
    progress_total  INTEGER     NOT NULL DEFAULT 0,
    detected_format VARCHAR(16) NOT NULL DEFAULT '',    -- 按魔数探测的结果，可能与扩展名不符
    error_code      VARCHAR(32) NOT NULL DEFAULT '',    -- no_text_layer / no_content / bad_archive / unknown_format / unsupported_format / parse_failed / internal / orphaned（见 database-relations.md）
    error_message   TEXT        NOT NULL DEFAULT '',    -- 给人看的那一句，只取自码表（book_lifecycle._FAILURES），不存引擎原话
    remedy          TEXT        NOT NULL DEFAULT '',    -- 用户自己能做的下一步，如带真文件名的 ocrmypdf -l chi_sim ... 命令
    cleanup_stats   JSONB       NOT NULL DEFAULT '{}'::jsonb,  -- 解析账本，读写只经 services/ledger.py 的 Ledger
    heartbeat_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at      TIMESTAMPTZ,                        -- 可空：排上队、还没开跑
    finished_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_parse_jobs_book  ON parse_jobs (book_id, created_at DESC);
CREATE INDEX idx_parse_jobs_alive ON parse_jobs (heartbeat_at) WHERE finished_at IS NULL;

COMMENT ON COLUMN parse_jobs.detected_format IS '按文件魔数判定，不看扩展名——下载来的书扩展名经常是错的';
COMMENT ON COLUMN parse_jobs.remedy          IS '失败必须给出路。抽不出文字的扫描版 PDF 在此给 OCR 命令，而不是写出空 md';
COMMENT ON COLUMN parse_jobs.cleanup_stats   IS '清洗账本：合并了多少硬换行、剥了多少全角空格、脚注接上几条、目录来源哪条路线。这些数字在引擎里是瞬时的，不落库解析报告就无从谈起';
COMMENT ON COLUMN parse_jobs.heartbeat_at    IS '解析推进时由进度泵更新。单实例下启动清扫不看它（进程刚起来，谁都没在跑）；多实例时孤儿判据要换成「心跳停滞超过 N 秒」';
COMMENT ON COLUMN parse_jobs.created_at      IS '入队时刻。与 started_at 分开是为了让孤儿清扫能区分「排上队了但后台任务没起来」和「跑起来又停滞」';
COMMENT ON COLUMN parse_jobs.started_at      IS '后台任务真正开跑的时刻。可空——排队中的任务还没开始';
```

> 最近一次任务按 `(created_at DESC, id DESC)` 取：书库卡片、书籍详情、`GET /api/books/{id}/state` 读的都是 `ParseRepository.book_states()` 那一条 LATERAL 查询。`error_message` 只是落库时的快照，对外一律按 `error_code` 从码表取（本轮之前的旧行里可能还留着引擎原话）。

## 表：parse_notes

```sql
CREATE TABLE parse_notes (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    book_id    BIGINT        NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    chapter_id BIGINT        REFERENCES chapters(id) ON DELETE SET NULL,  -- 定位到具体章节时填
    severity   note_severity NOT NULL DEFAULT 'doubt',
    code       VARCHAR(32)   NOT NULL DEFAULT '',       -- engine_warning / short_chapter（见 database-relations.md）
    message    TEXT          NOT NULL,
    resolved   BOOLEAN       NOT NULL DEFAULT false,    -- 用户点开核对过了
    created_at TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE INDEX idx_parse_notes_book ON parse_notes (book_id, resolved);

COMMENT ON TABLE parse_notes IS '解析产生的存疑与阻断项，来自 Book.warnings。解析成功不等于解析正确，这是可核对的账';
```

## 表：brains

```sql
CREATE TABLE brains (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     BIGINT      REFERENCES users(id) ON DELETE CASCADE,  -- 加这个端点的人。可空是 0001 的历史设计，实际没有 NULL 行（见表下说明）
    name        VARCHAR(32) NOT NULL,                   -- claude / qwen / deepseek / ...
    provider    VARCHAR(16) NOT NULL,                   -- anthropic | openai
    model       VARCHAR(64) NOT NULL,
    base_url    TEXT        NOT NULL DEFAULT '',
    api_key_env VARCHAR(64) NOT NULL DEFAULT '',        -- 环境变量【名字】，不是 key 本身
    context_tokens INTEGER  NOT NULL DEFAULT 0,         -- 上下文窗口，用于「这本书装得下吗」
    max_tokens  INTEGER     NOT NULL DEFAULT 8000,
    note        TEXT        NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_brains_user_name UNIQUE NULLS NOT DISTINCT (user_id, name)
);

COMMENT ON TABLE  brains      IS '大脑预设。此表【不存 API key】——key 只在用户浏览器 localStorage，请求时经 X-Brain-Key 头带来，用完即丢';
COMMENT ON COLUMN brains.api_key_env IS '只存环境变量名（如 DASHSCOPE_API_KEY），供界面提示用；绝不存值';
```

> **这张表只装用户自己加的 OpenAI 兼容端点。** `user_id` 可空、唯一约束写成 `NULLS NOT DISTINCT`，是 `0001` 时打算把内置全局预设也放进来（`user_id IS NULL`）；实际落地时**内置预设的真相留在了引擎的 `booksoul.config`**（`brain_service.presets()` 读 `available_brains()`），表里没有任何 `NULL` 行——CLI 与网页共用同一份预设，两边同时当真相只会打架。用户对内置预设的模型选择在 `brain_choices`，不在这里。
> 自定义端点换模型（`PUT /api/brains/{name}/model`）直接改这张表那一行的 `model` / `context_tokens`；本表没有窗口来路一列，所以它的窗口在界面上一律标「你填的」。

## 表：conversations

```sql
CREATE TABLE conversations (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id      BIGINT       NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    book_id      BIGINT       NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    persona      persona_key  NOT NULL DEFAULT 'scholar',
    brain_name   VARCHAR(32)  NOT NULL,                 -- 冗余存名字，大脑预设改了不影响历史记录
    model        VARCHAR(64)  NOT NULL,
    budget_chars INTEGER      NOT NULL DEFAULT 0,       -- 0 = 按模型上下文自动估
    context_chars INTEGER     NOT NULL DEFAULT 0,       -- 开会话时快照的正文额度（0003）；0 = 历史会话
    reflection   BOOLEAN      NOT NULL DEFAULT false,   -- 处境模式
    title        VARCHAR(255) NOT NULL DEFAULT '',      -- 取第一个问题
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX idx_conversations_user ON conversations (user_id, updated_at DESC);
CREATE INDEX idx_conversations_book ON conversations (book_id);

COMMENT ON COLUMN conversations.reflection IS '读者带来的是自己的处境而非书的知识问题时置 true，附加 REFLECTION_SUFFIX';
COMMENT ON COLUMN conversations.context_chars IS '开会话时快照的正文额度（字），与⑧屏当时显示的是同一个数。提问预算的优先级：budget_chars（用户手调）> context_chars > 按 model 查引擎内置表。0 = 这一列加上之前开的会话，走最后那条老路径';
```

> **`model` 与 `context_chars` 一起在开会话时锁定。** 之后换了模型选择、改了自定义端点，这个会话仍按开的时候那样调用与挑章节——显示的、算预算的、真正调用的永远是同一个（提问时用 `conversations.model` 覆盖当下配置里的模型）。

## 表：brain_choices

```sql
CREATE TABLE brain_choices (
    id             BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id        BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    brain_name     VARCHAR(32) NOT NULL,                  -- 内置预设的名字，弱关联、不做外键
    model          VARCHAR(64) NOT NULL,
    context_tokens INTEGER     NOT NULL DEFAULT 0,        -- 0 = 没人给过窗口
    context_source VARCHAR(8)  NOT NULL DEFAULT '',       -- '' / vendor / manual
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_brain_choices_user_brain UNIQUE (user_id, brain_name),
    CONSTRAINT ck_brain_choices_model CHECK (btrim(model) <> ''),
    CONSTRAINT ck_brain_choices_tokens CHECK (context_tokens BETWEEN 0 AND 10000000),
    CONSTRAINT ck_brain_choices_source CHECK (context_source IN ('', 'vendor', 'manual')),
    CONSTRAINT ck_brain_choices_source_needs_window
        CHECK (context_tokens > 0 OR context_source = '')
);

-- UNIQUE (user_id, brain_name) 的首列就是外键列：那条唯一索引同时满足「外键列要有索引」

CREATE TRIGGER trg_brain_choices_touch BEFORE UPDATE ON brain_choices
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

COMMENT ON TABLE  brain_choices                IS '用户为内置预设选的模型与窗口。解析 = 预设（provider / 地址 / key 名，永远从代码来）+ 这里的选择（只覆盖模型与窗口）。【不存 API key】';
COMMENT ON COLUMN brain_choices.brain_name     IS '内置预设的名字。与预设按名字弱关联、不做外键——预设不在库里。对不上任何预设的行在解析时忽略';
COMMENT ON COLUMN brain_choices.context_tokens IS '上下文窗口（token）。0 = 没人给过，解析时按内置表或保守估计；折算成正文字数由 booksoul.ask.budget_from_context_tokens 唯一负责';
COMMENT ON COLUMN brain_choices.context_source IS '窗口的来路：vendor = 列模型时供应商报的；manual = 用户照着文档填的；空串 = 窗口为 0（此时来路由解析推出，不落库）';
```

> **连放地址与 provider 的列都没有**：选择只能覆盖模型与窗口，供应商换地址时不会有人手里攥着一份过期的抄本。
> 来路用 `VARCHAR + CHECK` 而非 `ENUM`，沿用 `moderation_events.outcome` 的先例。窗口为 0 时的来路（内置表 / 估计）由 `services/context_window` 在读取时推出，不落库。

## 表：messages

> 与迁移 `0001_init` 逐字对齐（此前本节少了 `turn` / `persona` / `complete` / `updated_at` 四列、三条 CHECK 与那条部分唯一索引；turn-state 收尾时补上）。`seq` 持会话行锁发放（`services/turn_state.py`）。

```sql
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
    -- 唯一索引的 NULLS NOT DISTINCT 语义建立在「提问 persona 为 NULL、回答 persona 非 NULL」之上
    CONSTRAINT ck_messages_persona_matches_role CHECK (
        (role = 'user'      AND persona IS NULL) OR
        (role = 'assistant' AND persona IS NOT NULL)
    )
);
-- 部分唯一索引而非表级唯一约束：残缺回答（complete=false）不占坑位，
-- 断流留下的残文保住之后，用户「重问」还能在同一轮同人格再生成一条
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
```

## 表：retrievals

```sql
CREATE TABLE retrievals (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    conversation_id BIGINT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    turn           INTEGER NOT NULL,                    -- 第几轮
    budget_chars   INTEGER NOT NULL,
    used_chars     INTEGER NOT NULL,
    selected_count INTEGER NOT NULL,
    total_count    INTEGER NOT NULL,
    whole_book     BOOLEAN NOT NULL DEFAULT false,      -- 整本都放下了，没走 BM25
    query_terms    JSONB   NOT NULL DEFAULT '[]'::jsonb,-- 切出来并参与打分的查询词
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX uq_retrievals_turn ON retrievals (conversation_id, turn);

COMMENT ON TABLE  retrievals IS '一轮的选章决策。回答基于哪几节必须可查，这是产品的信任基础';
COMMENT ON COLUMN retrievals.turn IS '挂 (conversation_id, turn) 而不是 message_id：一轮里三种人格各答一次，却只选一次章。复用锁定的轮次不写行——替一个没发生的决定编一行，这张表就不再是「决策留档」，而是一份每轮复制粘贴的流水';
```

## 表：retrieval_items

```sql
CREATE TABLE retrieval_items (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    retrieval_id  BIGINT        NOT NULL REFERENCES retrievals(id) ON DELETE CASCADE,
    chapter_id    BIGINT        REFERENCES chapters(id) ON DELETE SET NULL,
    rank          INTEGER       NOT NULL,               -- 按分数降序的名次，1 起
    score         NUMERIC(10,4) NOT NULL DEFAULT 0,     -- BM25 得分
    word_count    INTEGER       NOT NULL DEFAULT 0,
    selected      BOOLEAN       NOT NULL DEFAULT false, -- 是否进了本次上下文
    matched_terms JSONB         NOT NULL DEFAULT '[]'::jsonb  -- [{"term":"佛陀","count":14}]
);

CREATE INDEX idx_retrieval_items_ret ON retrieval_items (retrieval_id, rank);
CREATE INDEX idx_retrieval_items_chap ON retrieval_items (chapter_id);

COMMENT ON COLUMN retrieval_items.selected IS '预算线之下的章节也留档：用户要能看到「差 504 字装不下」这种事';
```

## 表：citations

```sql
CREATE TABLE citations (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    message_id  BIGINT  NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    chapter_id  BIGINT  REFERENCES chapters(id) ON DELETE SET NULL,
    quote       TEXT    NOT NULL,                       -- 回答中引用的原文
    chapter_title VARCHAR(255) NOT NULL DEFAULT '',     -- 当时的章节标题快照
    para_index  INTEGER NOT NULL DEFAULT -1,            -- 在该章第几块，-1 = 未能定位
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TRIGGER trg_citations_touch BEFORE UPDATE ON citations
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

CREATE INDEX idx_citations_message ON citations (message_id);
CREATE INDEX idx_citations_chapter ON citations (chapter_id);

COMMENT ON TABLE  citations IS '溯源：回答引了哪一章哪一段，供阅读页跳转并高亮';
COMMENT ON COLUMN citations.para_index IS '落库时把引文与章节正文比对得出；对不上填 -1，不猜';
COMMENT ON COLUMN citations.chapter_id IS 'SET NULL 而非 CASCADE：重解析后引文降级为「原位置已变更」是可接受的，凭空消失不是。S12 起重解析会尝试重新定位补回它——但**读旧落点必须在 replace_chapters 之前**，否则 SET NULL 已经把 chapter 的 idx 抹掉，报告再也分不出「位置变了」和「压根没动过」';
COMMENT ON COLUMN citations.chapter_title IS '当时的章节标题快照。chapter_id 被 SET NULL 之后，界面还能说出「原《婆罗门之子》里这段，位置已变更」，而不是只剩一个灰按钮';
COMMENT ON COLUMN citations.updated_at IS '与 created_at 不等即说明这条引文被 S12 的 CitationRelocator 重定位过。界面据此区分「当时就定位到的」和「重解析之后重新找回来的」';
```

## 表：moderation_events

```sql
CREATE TABLE moderation_events (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    book_id     BIGINT      NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    brain_name  VARCHAR(32) NOT NULL,
    outcome     VARCHAR(8)  NOT NULL DEFAULT 'blocked',
    chapter_ids JSONB       NOT NULL DEFAULT '[]'::jsonb, -- 这一次送出去的章节 id 快照
    detail      TEXT        NOT NULL DEFAULT '',          -- 供应商回的那句话，截 500 字
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_moderation_book ON moderation_events (book_id, brain_name, created_at DESC);

ALTER TABLE moderation_events
    ADD CONSTRAINT ck_moderation_outcome CHECK (outcome IN ('blocked', 'passed'));

COMMENT ON TABLE  moderation_events            IS '内容审核的证据台账。append-only：每一行是一次「送了这几节，结果如何」。嫌疑 = 被拦时都在场的那几节（交集） − 曾经平安送过去的那几节（并集）';
COMMENT ON COLUMN moderation_events.chapter_ids IS '章节 id 快照，不做外键关联表：重解析之后这些 id 会不存在，而这条证据说的正是「当时送的是那几节」。做成关联表会被重解析连带删掉';
COMMENT ON COLUMN moderation_events.outcome     IS 'blocked=这几节送过去被拦了；passed=这几节平安送过去了。passed 只在这本书被这个大脑拦过之后才记，否则这张表会变成一份对话流水';
COMMENT ON COLUMN moderation_events.brain_name  IS '哪个大脑。同一本书在通义被拦、在 Claude 不被拦是常态，所以交集与并集都按大脑分开算';
COMMENT ON COLUMN moderation_events.detail      IS '供应商回的那句话，截断保存。**不存请求体、不存 key**';
```

> **为什么只有交集不够**：实测《悉达多》的「扉页」254 字、「第一部」5 字，任何预算都装得下，于是每一次选章它们都在场——交集永远排除不掉它们，范围卡在 3 节再也窄不下去。一次成功的请求正好证明那几节是干净的，减去它们才收得到一节。

## 迁移记录

| 版本 | 日期 | 变更 | 说明 |
|------|------|------|------|
| `0001_init` | 2026-09-16 | 建全部 11 张表与 5 个枚举类型 | 已执行。相对本文档基线有若干**刻意偏离**（`retrievals` 挂 `(conversation_id, turn)` 而非 `message_id` 等），每处在迁移文件里就地注明。`books`（`storage_path` 可空 + 唯一 + `ck_books_ready_has_path`、`source_sha256` 为 VARCHAR 带形状检查）与 `parse_jobs`（`cleanup_stats` / `heartbeat_at` / `created_at` / `updated_at`、`started_at` 可空、按 `created_at` 的索引）两张表的偏离已于 2026-09-25 回写进本文（book-lifecycle 收尾）；其余各表未逐一核对 |
| `0002_moderation_events` | 2026-09-20 | 新增 `moderation_events`（第 12 张表） | 已执行。内容审核证据台账（S13） |
| `0003_conversation_context_chars` | 2026-09-22 | `conversations` 加 `context_chars INTEGER NOT NULL DEFAULT 0` | 已在本机 compose 库执行。带常量默认值加列是元数据操作，不重写表；**不回填**（0 = 历史会话走老路径）。可降级 |
| `0004_brain_choices` | 2026-09-22 | 新增 `brain_choices`（第 13 张表） | 已在本机 compose 库执行。用户为内置预设选的模型与窗口（brain-model-picker）。可降级；`tests/test_schema_drift.py::test_本功能的迁移可降可升` 钉着 `0004 → 0002 → head` 的往返 |
| `0005_widen_book_source_text` | 2026-09-25 | `chapters.source_id`（VARCHAR(64)）、`books.published` / `books.isbn`（VARCHAR(32)）改为 `TEXT`，并改写两条列注释 | 已在本机 compose 库执行。三列都是从书的文件里原样照抄的，长度由书决定：EPUB 的「路径#锚点」过 64 字时，解析成了却在写库那一刻整本失败（原先还会卡在「解析中」）。VARCHAR → TEXT 是二进制兼容变更，只改系统目录、不重写表。可降级（`left()` 截回原宽）；同一条往返测试覆盖 |

> 迁移工具：Alembic。每次 schema 变更新增一个 revision，不改历史 revision。
