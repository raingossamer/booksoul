# 数据库关系文档

> **本文档职责**：用文字说明各表之间的关系与基数（一对一 / 一对多 / 多对多），以及外键、级联、关键约束。
> 可视化 ER 图见 `data-er.md`（Mermaid erDiagram）；建表语句见 `sql.md`。
> 由 `to-design` 生成（正向 0-to-1 / 逆向）、由 `design-doc-sync` 在实体 / schema 变更时同步更新。
>
> 以下为约定骨架，章节标题固定，内容按实际项目填充。

## 表清单

| 表 | 说明 | 所属域 |
|----|------|--------|
| `users` | 账号 | 账号 |
| `books` | 一本已入库的书（元信息 + 磁盘路径），归属某个用户 | 解析 |
| `chapters` | 章节索引。正文在磁盘，此表只存定位与统计 | 解析 |
| `parse_jobs` | 一次解析任务的阶段、进度与失败原因 | 解析 |
| `parse_notes` | 解析产生的存疑 / 阻断条目，来自 `Book.warnings` | 解析 |
| `brains` | 用户自己加的 OpenAI 兼容端点。**不存 key**。内置预设不在这里（在引擎 `booksoul.config`） | 对话 |
| `brain_choices` | 用户为内置预设选的模型与窗口（brain-model-picker，迁移 `0004`）。**不存 key**，也没有地址列 | 对话 |
| `conversations` | 一次读书对话：某本书 + 某个人格 + 某个大脑 | 对话 |
| `messages` | 对话中的一条消息，含 token 用量 | 对话 |
| `retrievals` | 一条 assistant 回答的选章决策（预算、选中几节） | 对话 |
| `retrieval_items` | 选章明细：每一节的 BM25 名次、分数、是否选中 | 对话 |
| `citations` | 溯源：回答引了哪一章哪一段 | 对话 |
| `moderation_events` | 内容审核台账：某次送了哪几节、结果是拦还是通（S13，迁移 `0002`） | 对话 |

**边界说明**：书的正文（`chapters/*.md`、`book.md`、`INDEX.md`、`meta.json`、`images/`）**不入库**，落在 `<书库根>/<user_id>/<book_id>-<slug>/`（即 `books.storage_path`）下。数据库只是这堆文件的索引。这样现有引擎（`writer.py` / `library.py`）一行不用改，用户手改 md 也能直接生效。**库与盘的先后**：解析时引擎只写 `<书库根>/.staging/job-<parse_jobs.id>/`，成功那个事务提交之后才把它整个换到 `storage_path`（旧的一版挪进 `.retired` 再删）——库先提交、盘后动；崩在中间，由启动清扫照库里已提交的事实补完或删掉（`services/book_lifecycle.py`）。

## 关系说明

| 父 | 子 | 基数 | 外键 | 级联 | 说明 |
|----|----|------|------|------|------|
| `users` | `books` | 1 : N | `books.user_id` | CASCADE | 删号连书一起删；磁盘目录由应用层同步删除 |
| `users` | `conversations` | 1 : N | `conversations.user_id` | CASCADE | |
| `users` | `brains` | 1 : N | `brains.user_id` | CASCADE | 列可空是 `0001` 的历史设计（原打算用 `NULL` 表示内置全局预设）；**实际没有 `NULL` 行**——内置预设的真相在引擎 `booksoul.config`，CLI 与网页共用一份 |
| `users` | `brain_choices` | 1 : N | `brain_choices.user_id` | CASCADE | 每人每个内置预设至多一行（`UNIQUE (user_id, brain_name)`）。**与预设按名字弱关联、不做外键**：预设不在库里；对不上任何预设的行在解析时忽略 |
| `books` | `chapters` | 1 : N | `chapters.book_id` | CASCADE | 重解析时先删旧章节再写新的（`resettle.swap()`：先清会话锁定、拍下引文落点，再换章节，再重定位） |
| `books` | `parse_jobs` | 1 : N | `parse_jobs.book_id` | CASCADE | 重解析会产生多条，按 `created_at DESC, id DESC` 取最新 |
| `books` | `parse_notes` | 1 : N | `parse_notes.book_id` | CASCADE | |
| `books` | `conversations` | 1 : N | `conversations.book_id` | CASCADE | 删书连带删该书的对话 |
| `chapters` | `parse_notes` | 1 : N（可空） | `parse_notes.chapter_id` | SET NULL | 能定位到具体章节的存疑项才填 |
| `conversations` | `messages` | 1 : N | `messages.conversation_id` | CASCADE | |
| `conversations` | `retrievals` | 1 : N | `retrievals.conversation_id` + `turn` UNIQUE | CASCADE | **挂会话不挂消息**：一轮里三种人格各答一次，却只选一次章。复用锁定的轮次不写行 |
| `retrievals` | `retrieval_items` | 1 : N | `retrieval_items.retrieval_id` | CASCADE | 一条 retrieval 对应全书每一节各一行（含未选中的） |
| `chapters` | `retrieval_items` | 1 : N（可空） | `retrieval_items.chapter_id` | **SET NULL** | 章节被删（重解析）不该连带毁掉历史对话的检索留档 |
| `messages` | `citations` | 1 : N | `citations.message_id` | CASCADE | 一条回答可引多处 |
| `chapters` | `citations` | 1 : N（可空） | `citations.chapter_id` | **SET NULL** | 同上，重解析后引文降级为「原章节已变更」而非消失。**S12 起会尝试重新定位**：找得回来就补上新的 `chapter_id` 与 `para_index`（`updated_at != created_at` 即说明被重定位过），找不到才保持降级 |
| `books` | `moderation_events` | 1 : N | `moderation_events.book_id` | CASCADE | append-only 台账。`chapter_ids` 是 jsonb **快照**不是关联表——重解析之后那些 id 会不存在，而这条证据说的正是「当时送的是那几节」；做成关联表会被重解析连带删掉 |

**级联策略的分野**：归属关系（用户→书→章节、会话→消息）用 `CASCADE`，删父即删子；**引用关系**（检索明细→章节、引文→章节）用 `SET NULL`，因为重新解析一本书会重建 `chapters` 行，若用 CASCADE，用户过去所有对话的检索记录和溯源会被一次重解析清空。

## 多对多关系

本 schema **暂无多对多关系**，无中间表。

两处看似多对多、实为一对多的地方：

- `retrieval_items` 连接 `retrievals` 与 `chapters`，形似中间表，但它带 `rank` / `score` / `selected` / `matched_terms` 四个自有属性，是独立实体（一次检索对某一章的评分结果），不是纯连接表。
- `conversations` 同时指向 `users` 与 `books`，但一个会话只属于一个用户的一本书，是两条独立的一对多。

## 关键约束与索引

**唯一约束**

| 表 | 约束 | 用途 |
|----|------|------|
| `users` | `email` UNIQUE（CITEXT，天然大小写不敏感） | 登录账号 |
| `books` | `uq_books_storage_path`：`storage_path` UNIQUE（可空，NULLS DISTINCT） | 两本书不会写进同一个目录；解析中还没有目录的多本书互不冲突。另有 `ck_books_ready_has_path`：ready 一定有 `storage_path`（门表因此没有「ready 却没有已发布一版」这一格） |
| `chapters` | `UNIQUE (book_id, idx)` | 一本书里阅读序号不重复 |
| `messages` | `UNIQUE (conversation_id, seq)` | 会话内消息顺序不重复 |
| `messages` | `uq_messages_conv_turn_persona`：`(conversation_id, turn, persona) NULLS NOT DISTINCT WHERE complete`（部分唯一索引） | 一轮一条提问、每种人格一条完整回答；残缺回答不占坑位，重问才有路可走 |
| `retrievals` | `message_id` UNIQUE | 保证 1:1 |
| `brains` | `UNIQUE NULLS NOT DISTINCT (user_id, name)` | 同一用户下端点名唯一（`NULLS NOT DISTINCT` 是为从未落地的「全局预设行」准备的）。与内置预设重名由应用层 `add_custom()` 拒绝，所以一个名字在同一用户下只指向一个大脑 |
| `brain_choices` | `UNIQUE (user_id, brain_name)` | 每人每个内置预设一行；保存走 `INSERT … ON CONFLICT DO UPDATE`，两个标签页同时保存不会撞约束 |

**索引**

| 表 | 索引 | 服务的查询 |
|----|------|-----------|
| `books` | `(user_id, created_at DESC)` | 书库列表（③屏） |
| `books` | `(user_id, status)` | 筛「解析中」「阻断」 |
| `chapters` | `(book_id, idx)` | 目录树、按序号取正文 |
| `parse_jobs` | `(book_id, created_at DESC)` | 取最近一次解析（书库卡片、书籍详情、`/state` 那一条 LATERAL 查询） |
| `parse_jobs` | `(heartbeat_at) WHERE finished_at IS NULL`（部分索引） | 没收尾的任务：启动清扫；多实例时按心跳判孤儿 |
| `parse_notes` | `(book_id, resolved)` | 详情页未核对的存疑项 |
| `conversations` | `(user_id, updated_at DESC)` | 「最近问过的」 |
| `conversations` | `(book_id)` | 某本书的全部会话 |
| `messages` | `(conversation_id, seq)` | 拉历史 |
| `retrieval_items` | `(retrieval_id, rank)` | ⑥屏排名表 |
| `retrieval_items` | `(chapter_id)` | 反查某章被引用过几次 |
| `citations` | `(message_id)` / `(chapter_id)` | 溯源正查与反查 |
| `brain_choices` | `UNIQUE (user_id, brain_name)` 那条唯一索引 | ⑧屏按用户取全部选择；首列即外键列，兼作外键索引 |

> PostgreSQL 不为外键自动建索引，上表已覆盖全部外键列。

**授权约束（应用层强制，非数据库约束）**：所有对 `books` / `conversations` 的查询必须在 `repositories` 层拼入 `user_id` 条件。越权返回 404 而非 403。

## 枚举值

数据库里一律存英文，中文展示名由前端映射（见 `frontend.md`）。

| 类型 | 值 | 中文展示 | 说明 |
|------|----|---------|------|
| `book_status` | `parsing` | 解析中 | 靛色，进度条 |
| | `ready` | 就绪 | **不着色**——解析成功是默认状态，不是成就 |
| | `failed` | 停下了 | 重墨整块，如扫描版 PDF |
| `parse_stage` | `read` | 读取 | |
| | `toc` | 解析目录 | |
| | `convert` | 转换正文 | |
| | `clean` | 清洗 | 缩进、水印、硬换行、页眉页脚 |
| | `write` | 写入 | |
| `note_severity` | `doubt` | 存疑 | 黄檗色，等人判断 |
| | `block` | 阻断 | 重墨 |
| `persona_key` | `scholar` | 研读者 | 引原文、分清事实与推演 |
| | `author` | 作者 | 作者本人的语气与困惑 |
| | `book` | 书 | 书本身，只有自己这些字 |
| `message_role` | `user` | — | |
| | `assistant` | — | |

**非枚举但取值受限的列**（保持 VARCHAR，因为会随供应商变动，改枚举要迁移）：

| 列 | 常见取值 |
|----|---------|
| `brains.provider` | `anthropic` / `openai` |
| `books.source_format` | `epub` / `mobi` / `azw3` / `pdf` / `docx` / `txt` / `html` / `markdown` |
| `parse_jobs.error_code` | `no_text_layer` / `no_content` / `bad_archive` / `unknown_format` / `parse_failed`（`services/importer.py` 归类）· `unsupported_format`（`importer` 按魔数判出的格式这个部署解不了——扩展名认不出或与内容不符、或重解析时后端已被卸掉；扩展名本身就指向没装的格式的，上传时就 415 了，不建行）· `internal`（引擎之外的意外、写库那一半出事）/ `orphaned`（进程重启时没人在跑）。都由 `services/book_lifecycle.py` 落库；`error_message` 只取它的码表 `_FAILURES` 那句。前端 `BookCard` 为 `no_text_layer` / `bad_archive` / `no_content` / `unsupported_format` / `internal` 写了专门的说法，其余一律「停下了」；`unsupported_format` 不给「重新解析」 |
| `parse_notes.code` | `engine_warning`（引擎 `book.warn()` 的原话）/ `short_chapter`（`services/quality` 判的偏短章节）。都由 `services/book_lifecycle.py` 在解析成功的事务里写 |
| `brain_choices.context_source` | `''` / `vendor` / `manual`（CHECK 约束；窗口为 0 时必须为空串）。`builtin` / `estimate` 两种来路由解析推出、不落库 |

## 待确认问题

1. **`books.source_sha256` 未加唯一约束。** 同一用户重复上传同一本书是覆盖、并存还是拒绝，尚未裁决（与 `backend.md` 待确认问题 4 同一条）。加 `UNIQUE (user_id, source_sha256)` 即为「拒绝重复」。
2. **`prompts/` 未建表。** 人格提示词当前是全局三个 md 文件，未按用户隔离。若允许用户各自覆写，需新增 `user_personas(user_id, persona_key, template, updated_at)`（与 `backend.md` 待确认问题 3 同一条）。
3. **重解析时 `chapters` 的处理策略未定。** 当前设计是删旧建新，代价是 `retrieval_items.chapter_id` / `citations.chapter_id` 全部变 NULL，历史溯源失效。备选：按 `(book_id, idx)` 做 upsert 保留 id，但章节数变化时对不齐。请裁决。
4. **无软删除。** 删书 / 删会话是物理删除。若需要回收站，全部归属表要加 `deleted_at` 并改所有查询。本轮未设计。
5. **`brains` 没有窗口来路一列。** 自定义端点换模型时，从列表里点来的供应商窗口也只能存成一个数，界面一律标「你填的」（内置预设在 `brain_choices` 里有 `context_source`，标得出「供应商」）。数字本身是对的；要精确需给 `brains` 加 `context_source`，本轮 Spec 定为 `brains` 表不变。
6. **未设计用量统计表。** key 由用户自带、成本落在用户身上，故无配额需求。`messages` 的四个 token 列已够做事后统计。若将来改服务端统一 key，需要单独的用量与限额表。
