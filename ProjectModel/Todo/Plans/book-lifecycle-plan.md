---
status: done
---

# 一本书的生命周期（BookLifecycle）— Implementation Plan

## Parent Spec / RFC

- File: `ProjectModel/Todo/Rfcs/book-lifecycle-rfc.md`（实施时 `accepted`，收尾后 `implemented`）
- 模式：**RFC 快车道**。切片按绞杀式迁移路径组织，不按 schema → API → UI。
- 前置条件核对（2026-09-25）：
  - 该 RFC 是唯一未实施的提案（`turn-state-rfc.md` / `turn-runner-rfc.md` 均已 `implemented`，本 RFC 继承
    前者的锁序，不置 superseded）。
  - Design 与代码同步：本轮 read-design-before-code 改码之后已跑 `design-doc-sync`，其后的
    `improve-codebase-architecture` 不改代码。
  - 已知两处规范章节与代码不符（已在本轮报告，未改）：`backend.md`「异常处理」写的全局异常处理器
    `main.py` 里没有；`frontend.md`「命名约定」的两个例子不存在。与本计划无关，不影响 tdd 的 Pre-read 基线。
- 用户裁定（2026-09-25，拆解评审时一并确认）：
  - 按本计划 13 片拆（RFC 建议 7 片，见下「与 RFC 的差异」）；可选的第 12 片**做**。
  - RFC「实施时要当面确认的行为变化」**全部同意**：重新解析撞上正在解析、原件不在都从 400 改为
    409（带 `book_parsing` / `source_missing`），开会话遇到没解析好的书也带 code；`error_message` 只来自码表；
    上传时对「认得但本部署没装后端」的格式回 415；书目字段超列宽截断加「…」；阅读页底部路径从书库根目录名
    起算；进度推送 10 分钟无终局静默收尾。
  - 两项可选的数据库改动（部分唯一索引、按码重写旧行 `error_message` 的数据迁移）**不做**：本计划没有 schema 变更。

### 与 RFC 的差异

- **切得更细**：RFC 第 3 片拆成 3（搬家，行为不变）/ 4（比较交换）/ 6（终局保证）/ 7（码表与 415）；
  RFC 第 5 片拆成 5（状态）/ 9（账本）；`state()` 提前到第 5 片，此后的测试读结局都走它，不再 `refresh()`
  ORM 对象（撤回之后碰过期属性会抛 MissingGreenlet，本轮踩过）。
- `UsableBook.display()`（阅读页的容器绝对路径）随阅读页迁移放进第 2 片（RFC 放在第 5 片）。
- 新增第 11 片（收尾）与第 13 片（真实环境走查），与 turn-state 计划同形。
- **两处更正**：
  - `test_书库卡片带上未核对的存疑条数` 搬去 `tests/test_book_detail.py`：RFC 写的 `tests/test_library.py`
    测的是引擎的 `booksoul.library`，不是书库服务。
  - 测试 6 的触发方式：引擎层替身造不出 `idx=0`——章节序号由 importer 从 1 编；改用一个真能到数据库的坏值
    （如 `level` 超出 smallint）。

## Plan Overview

十三片，主干是一条链，两处分叉。

**切片 1–2 先收「次序」与「门」**，都不碰解析本身：换章节的善后次序在全仓库只写一份（`resettle.swap()`），
能不能读 / 问由一张门表说了算（`require()`），对话与阅读两个调用方先迁过来。两片互不依赖。

**切片 3 是搬家**：上传与重解析连同后台执行整体搬进 `BookLifecycle`，模块自己排任务，路由不再碰
`BackgroundTasks`——**这一片行为不变**，测试夹具（引擎层替身、每测清空根目录、任务泄漏守卫）在这里就位，
`test_parse_service.py` 并入新文件。**切片 4 起逐项改行为**：重解析改比较交换（4）、状态读取统一（5）、
每条终局都落地（6）、给人看的话只来自码表并接上 415（7）、进度推送离开路由（8）、账本类型化（9）。

**切片 10 是正文落盘方式的换代**：暂存、提交后换入、启动清扫对账——此后「重解析中照读旧版」才真正安全。
**切片 11** 删旧实现与被取代的旧测试，**切片 12** 让书籍详情轮询轻量状态接口（全程唯一改前端的一片），
**切片 13** 是真实环境的人工走查。

每片的验收标准统一为：**可观察行为不变（该片明确改掉的除外）+ 新接口边界测试通过 + 全量测试保持绿色 +
ruff 干净**。基线：全量 464 passed。RFC 的边界测试编号（1–19）在下文沿用。

## Slices

### Slice 1: 换章节的次序只写一份：`resettle.swap()`

- **Type**: AFK
- **Blocked by**: None
- **接口契约 / 调用方分组**: RFC「两个协作者 · `resettle.swap`」；Problem 三；边界测试 15；调用方：
  `parse_service._record_success`；手抄：`test_reparse._reparse_with`、`test_turn_state` 死锁测试内联的三步、
  `test_turn_state._replace_chapters_elsewhere`（两步）
- **Complexity**: S

**What to build**:

- `resettle.swap(session, *, book_id, records, root) -> Swapped(chapters, relocation)`：清锁定 + 拍引文落点 →
  `replace_chapters` → 按 `root` 下的**新正文**重定位。先会话行、后章节行。不提交。`prepare` / `apply`
  改成私有；模块注释「调用顺序不是可选的」一段改写为「次序只在 `swap()` 里出现一次」。
- `_record_success` 里那三步换成一次 `swap()`。`root` 由调用方给：这一片仍是 `library_root / storage_path`
  （原地写），切片 10 换成暂存目录。存疑条目的 `chapter_id` 从 `Swapped.chapters` 取。
- 测试里的手抄改调 `swap()`：`_reparse_with`（`_write` 暂留，切片 4 删）；死锁测试那三步改成一次 `swap()`，
  放行模型与等锁的时机不变（swap 之后、提交之前）；`_replace_chapters_elsewhere` 同样改调它（切片 4 再改走
  重解析）。

**Acceptance criteria**:
- [ ] 测试 15 `test_换章节之前已经握着会话行`（黑盒锁序）：第三条 session 对一行章节加 `FOR KEY SHARE`，让
      `swap()` 卡在删章节上；此时第四条 session 对会话行 `SELECT … FOR UPDATE NOWAIT` **必须失败**。
      变异验证：把 `swap()` 里清锁定挪到换章节之后，这条当场变红
- [ ] `src/` 与 `tests/` 里不再调用 `resettle.prepare` / `resettle.apply`
- [ ] `test_reparse.py`、`test_turn_state.py` 原样绿（断言不改，只换调用方式）
- [ ] 全量绿，ruff 干净

**Implementation hints**:
- 测试 15 放进新文件 `tests/test_book_lifecycle.py`（这一片它直接调 `swap()`）。
- 「卡住了」的判定照 turn-state 切片 1：`pg_locks WHERE NOT granted`，每次新开 session 查
  （`pg_stat_activity` 在一个事务里只取一次快照）。`NOWAIT` 失败是 SQLSTATE 55P03。
- `clear_locks_for_book` 对这本书的**全部**会话行 UPDATE（不看锁定是否为空），书上有一个会话就够。

**完成记录（2026-09-25）**:
- `resettle.swap(session, *, book_id, records, root) -> Swapped(chapters, relocation)`：`_prepare`（清锁定、拍引文落点）→
  `BookRepository.replace_chapters` → `_relocate`（按 `root` 下的新正文重定位）。原 `prepare` / `apply` 变成这两个私有函数，
  `_relocate` 不再自己拼 `library_root`（`root` 由调用方给），`resettle` 不再 import `get_settings`。模块注释「调用顺序不是
  可选的」改写成「次序只在 `swap()` 里出现一次」，写明原先锁序测试钉的是抄本。
- `parse_service._record_success` 改调一次 `swap()`，`root = library_root / outcome.storage_path`（仍原地写）；存疑条目的
  `chapter_id` 取自 `Swapped.chapters`。重定位现在发生在写存疑条目之前（原先在之后）——两者不碰同一张表，结果不变。
- 测试手抄改调 `swap()`：`test_reparse._reparse_with`；`test_turn_state` 的 `_replace_chapters_elsewhere`（改收 `book`，
  要它的 `storage_path` 当 `root`）与死锁那条（`swap()` 之后、提交之前放行模型，时序不变）；两处共用新抽的 `_same_chapters`。
- 测试 15 `tests/test_book_lifecycle.py::test_换章节之前已经握着会话行`：先 RED（`swap` 不存在）后 GREEN。**变异验证过**：把
  `swap()` 里 `_prepare` 挪到换章节之后，当场红——「swap() 卡在删旧章节上时，会话行还不在它手里：None」。第一次变异时真正的
  失败被收尾的连接错误盖住（`swap` 任务还压在 `parse` 连接上就退出了 `async with`），改成在 `finally` 里放行并等它走完之后，
  报出来的就是那句断言。
- 全量 **465 passed**（464 + 1），ruff 干净；改动的文件各自保持原换行（`resettle` / `parse_service` / `test_reparse` CRLF，
  `turn_state` / `test_turn_state` / 新文件 LF）。
- **设计读取**：`backend.md` 全文（含「设计模式与约定」全部小节）、`database/sql.md`「约定」与 books / chapters / parse_jobs /
  parse_notes 表、`database-relations.md`、`data-er.md`。应用：锁序「会话行在前、章节行在后」、「要读的旧状态必须在破坏它的
  操作之前读」、「同一条规则只许有一份实现」、事务边界在 services（`swap()` 不提交）、私有函数 `_` 前缀、关键字参数强制。
- **偏差**：无。
- **规范候选（累积）**：
  - #1 `backend.md`「并发与后台任务」锁序那条的实现引用 `services/resettle.py:prepare()` 与钉它的
    `test_turn_state.py::test_重解析与收尾同时进行_两边同序拿锁_不死锁也不丢回答` 已过期：实现改在 `resettle.swap()`，在库里钉
    生产代码次序的是 `test_book_lifecycle.py::test_换章节之前已经握着会话行`（原测试只看两边同序时的结局，保留）。
  - #2 `database/sql.md`「约定」「要读的旧状态…」一条的实现引用 `prepare()` → `replace_chapters()` → `apply()` 改为
    `resettle.swap()`。
  - #3（新）**测次序的测试要驱动生产代码的那一份，不许在测试里自己排一遍步骤再测**：抄本对了不说明生产代码对——实测旧锁序测试
    在生产代码两步对调后照样绿。适用于以后所有「次序敏感」的测试（锁序、先读后删、先落库后宣布）。

---

### Slice 2: 能不能读、能不能问，一道门说了算：`require()` + 门表

- **Type**: AFK
- **Blocked by**: None
- **接口契约 / 调用方分组**: `Use` / `UsableBook` / `BookLifecycle.require()`；门表；`BookNotReady(ConflictError)`；
  RFC Problem 二、五（阅读页路径）；调用方：`ChatService._open` / `ask`、`ReaderService.chapter` / `image`；
  边界测试 11（`require()` 那一半）
- **Complexity**: M

**What to build**:

- 新建 `services/book_lifecycle.py`：`Use`（read / ask / reparse）、`UsableBook`（id / title / word_count /
  chapter_count / root；`file(*parts)` = `core.paths.under(root, …)`，越界为 None；`display(rel)`）、
  `BookLifecycle(session).require(*, book_id, user_id, use)`。门表是一份数据（状态 × 有没有已发布的一版 ×
  用途 → 放行 / 404 / 409 + code），**措辞只写在这里**。「有已发布的一版」这一片仍按今天的判据：
  `storage_path` 非空（切片 10 之后才真正安全）。
- `core/errors.py` 加 `BookNotReady(ConflictError)`，带 `code`：`book_parsing` / `book_failed`
  （`source_missing` 切片 4 用）。
- 对话：`_open` 与 `ask` 改调 `require(use=Use.ASK)`，删 `_check_readable`；`BookRef.storage_path = str(book.root)`，
  不再自己拼 `library_root`。
- 阅读：`chapter` / `image` 改调 `require(use=Use.READ)`，删 `_STATUS_HINT` 与 `_resolve` / `_display_path`
  里的拼接；正文走 `book.file(here.file_path)`（None → 409「指向了书目录外面」，阅读器自己的话），图片走
  `book.file("images", name)`，⑤屏路径走 `book.display(here.file_path)`。
- **行为变化（已确认）**：
  - 开会话遇到没解析好的书：409 的措辞由门表给出，并带 `code`（原先一种措辞、无 code）。
  - `display()` 从书库根的**目录名**起算、一律正斜杠：`{Path(library_root).name}/{storage_path}/{rel}`。本地开发
    仍是 `library/…`；compose 下由 `/data/library/…` 变成 `library/…`，与 `schemas/book.py` 注释「从 library/
    起算」一致。

**Acceptance criteria**:
- [ ] 测试 11（前一半）`test_能不能读能不能问_一张表说了算`：参数化门表五行 × 三种用途，断言放行 / 404 /
      409 与 `code`；「首次解析中」「首次失败」读正文 404，「重解析中」「重解析失败」（有已发布的一版）读得到；
      别人的书一律 404
- [ ] 书在解析中提问：409 `book_parsing`，提问不落库、模型零次（`test_turn_state::test_书正在解析时不开新的一轮`
      原样绿，另断言 code）
- [ ] `test_reader::test_源文件路径给的是能照着找到的那一条` 改为：以书库根目录名开头、没有反斜杠、
      `Path(library_root).parent / source_path` 是那个文件
- [ ] `chat_service.py` / `reader_service.py` 里不再出现 `library_root`、对 `status` 的比较、`_check_readable`、
      `_STATUS_HINT`
- [ ] 全量绿，ruff 干净

**Implementation hints**:
- `require()` 经 `BookRepository.get_for_user` 取行、当场拍成冻结的 `UsableBook`：它不是 ORM 行，出了请求照样能用。
- 措辞只搬家不改：READ 的两句 404 沿用 `_STATUS_HINT`，ASK 的两句 409 沿用 `_check_readable`。
- `turn.py` 里「`storage_path` 是绝对路径（已经拼过 `library_root`）」照实改成「已经拼过书库根」。
- `ck_books_ready_has_path` 保证 ready 一定有路径：门表里没有「ready 但没有已发布版」这一格。

**完成记录（2026-09-25）**:
- 新建 `services/book_lifecycle.py`：`Use`（StrEnum）、`UsableBook`、门表 `_GATE`（`(状态, 有没有已发布的一版)` × 用途 →
  `_Refusal(code, text)`，表里没有的格子即放行；`code` 为空是 404）、`BookLifecycle.require()`。`core/errors.py` 加
  `BookNotReady(ConflictError)`，带 `code`；`main.py` 的 `ApiError` 处理器本来就把 `code` 放进响应体。
- 对话：`_open` 与 `ask` 改调 `require(use=ASK)`，`_check_readable` 删除，`BookRef.storage_path = str(book.root)`；
  `chat_service` 不再 import `Path` / `get_settings` / `ConflictError`。
- 阅读：`chapter` / `image` 改调 `require(use=READ)`；`_STATUS_HINT`、`_display_path`、`_resolve` 删除；正文走
  `book.file(here.file_path)`，路径那行走 `book.display()`。`display()` = `{书库根目录名}/{storage_path}/{rel}`。
- 测试：测试 11 的前一半（门表 15 格，参数化）与「别人的书过不了门」（三种用途各一）先 RED（`BookNotReady` 不存在）后 GREEN；
  `test_turn_state::test_书正在解析时不开新的一轮` 补 `code == "book_parsing"`（先 RED：原先 409 不带 code）；
  `test_reader::test_源文件路径给的是能照着找到的那一条` 改为新说法（先 RED：原先给的是 `C:/Users/…` 绝对路径）。
- 全量 **483 passed**（465 + 18），ruff 干净；`chat_service` / `turn` / `test_reader` 保持 CRLF，其余 LF。
- **设计读取**：同会话已读 `backend.md` 全文（含规范章节）。应用：「要让代码分支的东西必须机读」（`code` 写死字面量，只来自
  门表）、越权与不存在同一个 404、「存在数据库里的路径，必须 resolve 之后校验仍在根目录内」（`file()` 走 `core/paths.under()`）、
  「同一条规则只许有一份实现」（门表一张、路径拼法一处）、`ChatService.ask` 仍是普通协程（检查在 await 那一刻）。
- **偏差**：
  - `UsableBook` 存 `shelf`（即 `storage_path`），`root` 是属性而不是 RFC 写的字段：门表里「首次失败 × 重新解析」那一格放行，
    这本书却没有已发布的一版。`root` 在那一格抛 `LookupError`——那样的书重新解析要的是原件，不是这里；读与问过得了门的书
    一定有它。对外的 `root` / `file()` / `display()` 与 RFC 一致。
  - 书里的图以 `images/` 为界（`under(book.root / "images", name)`），没按计划提示写成 `book.file("images", name)`：后者以书目录
    为界，一个指向正文或 `meta.json` 的符号链接就能被当成图端出去。保留原先更严的边界。
  - 措辞只搬家，新增两句：首次解析中 / 首次失败时「问」被拦下的说法（原先 `_open` 对一切非 ready 只有一句「还没有解析好的正文」）。
- **规范候选**：无新增。

---

### Slice 3: 写入口归模块：上传、重解析搬进 `BookLifecycle`，自己排任务

- **Type**: AFK
- **Blocked by**: Slice 1, Slice 2
- **接口契约 / 调用方分组**: `BookLifecycle.upload()` / `reparse()`（这一片行为照旧）、`drain()`、`source_path()`、
  `sweep_orphans()`（搬家）；后台任务表 `_RUNNING`；RFC Problem 一、「藏了什么」第 1–2 条、Dependency Strategy；
  调用方：`routers/books.py` 两个端点、`main.lifespan`、`library_service._source_size`；边界测试 1、2
- **Complexity**: L

**What to build**:

- `upload(*, user_id, filename, source) -> UploadAccepted`：`accept()` 原样搬来（先落临时盘 → 413 / 400 →
  建书行、任务行 → 原件搬进上传根 → 提交），**提交返回后同步 `asyncio.create_task`** 排解析，强引用进
  `_RUNNING`，done-callback 把逃出来的异常记 ERROR。顺带修掉：提交失败时删掉已经搬过去的原件（今天会留孤儿文件）。
- `reparse(*, book_id, user_id) -> UploadAccepted`：今天的先查再改原样搬来（400 照旧，切片 4 改），同样自己排任务，
  回执带 `affected_conversations`。
- 后台执行（`execute`、`_record_success` / `_record_failure` / `_fail_after_rollback`、进度泵、`_stop`、`_read_chunk`）
  搬进 `book_lifecycle`，全部私有；`run_in_background` 消失。
- 模块函数：`drain()`（等所有在跑的解析收尾，测试用）、`source_path(book)`（取代 `source_path_for`）、
  `sweep_orphans()`（原样搬，`main.py` 只换 import）。
- `routers/books.py`：两个端点去掉 `BackgroundTasks`，只剩一句 `await BookLifecycle(session).upload / reparse(...)`。
  `parse_service.py` 只剩 `ParseService.job_status`（切片 8 删）。
- **测试夹具**（`tests/conftest.py`）：
  - `BOOKSOUL_LIBRARY_ROOT` / `BOOKSOUL_UPLOAD_ROOT` 从 `setdefault` 改为**赋值**（见 Risk Notes #1），然后在
    `_fresh_engine` 里每个测试前清空这两个根目录；
  - `fake_parser`：monkeypatch `booksoul.parsers.detect_format` 与 `booksoul.parsers.parse`，按字典（章节标题 → 正文）
    造一本引擎 `Book` 交回；可挂 `threading.Event` 闸门、可抛指定异常。清洗、`write_book`、落库、`swap()` 全走真代码；
  - autouse `_no_parse_left_running`：测试结束时 `_RUNNING` 为空（与 `_no_conversation_left_occupied` 同类）；
  - `imported_book`：真引擎 + 样书，经 `upload()` + `drain()`，合并 `test_book_detail` / `test_reader` 里的两份
    `_imported`；
  - `BOOKSOUL_PARSE_HEARTBEAT_SECONDS` 调小。
- **旧测试迁移**：`tests/test_parse_service.py` 并入 `tests/test_book_lifecycle.py`——`_upload` + `service.execute`
  改为 `upload()` + `drain()`；`_parsed_as`、`slow_import` 删除，三条用它们的（写库那一半出错、定位与出版信息多长
  都存得下、解析中途的进度）改用 `fake_parser`；`test_书库卡片带上未核对的存疑条数` 搬去 `test_book_detail.py`；
  `test_清扫失败不拦着服务起来` 保留，只换 import。`test_reparse::test_重解析回执与书详情都说得出受影响几个会话`
  改调 `BookLifecycle.reparse` + `source_path`，排出去的解析 `drain()` 收尾。

**Acceptance criteria**:
- [ ] 测试 1 `test_上传之后不用谁再排一次解析就会跑完`：真引擎 + 样书；只调 `upload()` 再 `drain()` → ready，
      `require(ASK)` 放行，`storage_path` 形如 `{uid}/{bid}-悉达多`，`UsableBook.root` 就是那个目录
      （取代「上传一本epub后三张表都落了索引」）
- [ ] 测试 2 `test_扩展名是错的也按内容解析`：epub 改名 .txt 上传 → ready、`source_format == "epub"`
      （取代 `test_import::test_按内容判格式而不是扩展名`，后者删除）
- [ ] 提交失败时原件不留：上传根下没有这本书的目录（先 RED）
- [ ] `routers/books.py` 里没有 `BackgroundTasks`；`src/` 里没有 `run_in_background`、`source_path_for`、
      `ParseService.accept` / `execute` / `reparse`
- [ ] 每个测试结束时 `_RUNNING` 为空，全量跑下来没有一条测试漏任务
- [ ] 全量绿（测试数因合并而变），ruff 干净

**Implementation hints**:
- `create_task` 必须紧跟提交、中间没有可被取消的 await：客户端断开会取消请求协程，排在一个 await 后面的任务
  可能永远排不出去——书就停在「解析中」。
- 后台任务自己开 session（规范「session 不跨边界」）；进线程前取出标量（`execute` 今天的做法原样保留）。
- `reset_engine()` 会关掉引擎：还有解析在跑时换引擎，得到的是偶发的连接错误，症状出现在下一条测试里——
  需要时在 `db_session` / `client` 夹具退出前先 `drain()`。
- `fake_parser` 挂在引擎这一层，与 `fake_brain` 换在 `booksoul.brain.create` 同一个做法；`write_book` 的
  `images_from` 给一个没有图片的替身。
- `importer.py`、`tests/test_import.py` 模块注释里指向 `ParseService` 的说法一并改掉。

**完成记录（2026-09-25）**:
- `book_lifecycle` 收进：`upload()`（原 `accept` + 提交后同步 `_start`）、`reparse()`（先查再改与两个 400 **原样**，切片 4 改）、
  后台执行 `_execute` / `_record_success` / `_record_failure` / `_fail_after_rollback`（`BookLifecycle` 的私有方法）、模块级
  `_start`（`create_task`，强引用进 `_RUNNING`，done-callback 记下逃出来的异常）/ `drain()` / `_parse()`（自开 session）/
  `source_path()` / `sweep_orphans()` / 进度泵 / `_stop` / `_read_chunk` / `UploadTooLarge`。模块注释写明「排任务归这里、
  不归路由」与原 `parse_service` 的四条决定。
- 路由：`routers/books.py` 两个端点去掉 `BackgroundTasks`，各一句 `await BookLifecycle(session).upload / reparse(...)`；
  `main.py` 从 `book_lifecycle` 取 `sweep_orphans`；`library_service._source_size` 改用 `source_path()`。`parse_service.py`
  只剩 `ParseService.job_status`（`routers/jobs.py` 还在用，切片 8 删）。
- **新行为（先 RED 的等价验证）**：提交失败时删掉已搬进上传根的原件。实现随搬家一起写的，事后做了变异验证：去掉清理那段，
  `test_提交没成_搬过去的原件不留下` 当场红（「那一行没落下，原件却留在了上传目录里」）。只接 `Exception`：被取消时提交可能
  已经落了，那时删原件会让这本书再也重新解析不了。
- **发现并修掉的缺陷（引擎，向后兼容）**：EPUB 解析器握着原件的 zip 句柄从不关，要等垃圾回收（解析器与产出之间有引用环）。
  「每个测试前清空根目录」一加上，Windows 上就在清空时撞 `PermissionError [WinError 32]`（6 条 setup 报错）——线上同理：
  重新解析、删书都要动那份原件。`EpubParser.close()`；构造与 `parse()` 半路失败时当场关；`importer.import_file` 写完盘
  （成功或失败）都关。新测试 `test_import::test_解析完就放开原件_不等垃圾回收`（关掉 GC 后删原件）先 RED 后 GREEN。
  CLI 那条路不受影响（进程随即退出）。
- 测试夹具（conftest）：两个根目录的环境变量改为**赋值**（`BOOKSOUL_TEST_*` 可覆盖），`_fresh_engine` 每个测试前清空两个根；
  `BOOKSOUL_PARSE_HEARTBEAT_SECONDS=0.05`；`fake_parser`（`fakes.FakeParser` + `engine_book()`：替身挂在
  `booksoul.parsers.detect_format` / `parse`，每次交深拷贝，`hold()` 闸门、`progress`、`fail`）；`imported_book`（真引擎 + 样书，
  取代 `test_book_detail` / `test_reader` 两份 `_imported`，13 条测试改用它）；autouse `_no_parse_left_running`。
- 测试迁移：`tests/test_parse_service.py` 删除，11 条并入 `tests/test_book_lifecycle.py`（上传在自己的 session 里、`drain()` 收尾、
  读结局用 `populate_existing`；`_parsed_as` / `slow_import` 换成 `fake_parser`；「解析中途的进度」改为闸门 + 轮询，不再 sleep；
  「重新解析复用同一个 book_id」改用替身，确定性地看到重解析期间是 parsing）；`test_书库卡片带上未核对的存疑条数` 搬去
  `test_book_detail.py`；`test_import::test_按内容判格式而不是扩展名` 由测试 2 取代；`test_reparse` 的回执测试改走
  `BookLifecycle.reparse` + `source_path` + 替身。
- 新增测试 1（`test_上传之后不用谁再排一次解析就会跑完`，先 RED：`drain` 不存在）、测试 2、提交失败不留原件。
- 全量 **485 passed**，ruff 干净；全量跑下来没有一条测试漏任务。换行：新文件与 `book_lifecycle` LF，其余保持原样（CRLF 的
  `parse_service` / `routers/books` / `main` / `library_service` / `importer` / `conversation_repo` / `epub.py` / `conftest` /
  `fakes` / `test_book_detail` / `test_reader` / `test_import` / `test_reparse` 仍是 CRLF）。
- **设计读取**：同会话已读 `backend.md` 全文（含规范章节）。应用：「session 不跨边界…活得比调用方长的活儿自己开一条 session」
  （`_parse`、进度泵）、`sweep_orphans` 失败照抛由 lifespan 接、「写文件要么整体成功、要么一个字不动」（原件先落临时盘）、
  事务边界在 services。
- **偏差**：
  - 引擎改了一处（EPUB 解析器的 `close()`），计划里没有；不修的话本片的「每测清空根目录」在 Windows 上跑不通，线上也同样泄漏。
    `backend.md`「对引擎做的改动」表由收尾 sync 补一行。
  - `UploadTooLarge` 仍放在 `book_lifecycle`，没有挪进 `core/errors.py`（只有这里抛它）。
- **规范候选（累积）**：
  - #4（新）**持有外部资源的对象用完当场关，不靠垃圾回收**：Windows 上一个还开着的文件删不掉、也换不掉；而对象之间有引用环时，
    回收要等一阵。实测：EPUB 解析器的 zip 句柄。适用于以后所有读写文件、压缩包、子进程管道的代码（`backend.md`「写外部状态」）。
  - #5（新）**测试会清空的外部位置（库、目录），由测试自己指定，不沿用外部环境变量**（赋值，不 `setdefault`）：沿用的话，一次
    pytest 就可能清掉开发者的真库、真书库。数据库连接串早就这么做了，这次书库根与上传根跟上。
  - #6（新）**后台任务由拥有那份状态的模块自己排，不归路由**：提交之后同步 `create_task`、中间没有可被取消的 await，强引用进模块
    级集合、done-callback 记下逃出来的异常。`turn_state._RUNNING` 与 `book_lifecycle._RUNNING` 两处同形（`backend.md`「并发与后台
    任务」）。

---

### Slice 4: 重解析改成一次比较交换

- **Type**: AFK
- **Blocked by**: Slice 3
- **接口契约 / 调用方分组**: `reparse()` 的 409 语义；`BookNotReady(code="source_missing")`；RFC Problem 八-2、
  「藏了什么」第 3 条；边界测试 9、10、14（报告那一行在切片 9）；调用方分组：`tests/test_reparse.py`、
  `test_turn_state._replace_chapters_elsewhere`
- **Complexity**: M

**What to build**:

- `reparse()`：先查原件（不在 → 409 `source_missing`，状态不动）；再
  `UPDATE books SET status='parsing' WHERE id=:b AND user_id=:u AND status <> 'parsing' RETURNING …`，
  拿不到行时再查一次，分出 404（没有 / 不是你的）与 409 `book_parsing`。统计受影响会话数、建任务行与这句
  UPDATE 同一个事务。
- `tests/test_reparse.py` 改走真路径：上传 V1（`fake_parser` 照 V1 造书）→ 造会话与引文 → 重新解析成 V2 / V3 →
  `drain()`；删掉 `_reparse_with` 与 `_write`。**段号期望要重算**：真 writer 在标题之后多写一块元信息引用块，
  `paragraphs()` 会把它算作一块。
- `test_turn_state._replace_chapters_elsewhere` 改为经 `reparse()` 用同一份正文重解析一次（正文不变、章节 id 全新）；
  `_open` 造的书若没有原件，先让它经 `upload()`（替身引擎）造书。死锁那条保留切片 1 的 `swap()` 写法——
  它要在提交之前放行模型。
- **行为变化（已确认）**：重新解析撞上正在解析 400 → 409 `book_parsing`；原件不在 400 → 409 `source_missing`。

**Acceptance criteria**:
- [ ] 测试 9 `test_同时两次重新解析只有一个开跑`：两条 session gather → 一个拿到回执、一个 409 `book_parsing`，
      `parse_jobs` 只多一行（先 RED：今天两个都过）
- [ ] 测试 10 `test_原件不在时重新解析409且状态不动`：409 `source_missing`，`status` 与任务数都不变
- [ ] 测试 14 `test_重新解析之后锁定清空_引文按原地不动位置变了丢了分开记账`：V1 → V2 / V3，另开 session 经
      `CitationService` 看落点与 `relocated`；锁定为空
- [ ] `test_reparse.py` 里没有 `_reparse_with`、`_write`；`test_turn_state.py` 里换章节只经 `reparse()` 或 `swap()`
- [ ] 全量绿，ruff 干净

**Implementation hints**:
- 并发的两个请求各用一条 session（`get_session_factory()`）。比较交换在库里串行：第二个 UPDATE 等第一个提交后
  重新求值条件，拿不到行——不靠 sleep 赌时序。
- 先查原件再比较交换：原件不在时一行都不写，「状态不动」就不需要撤回。
- `test_reparse.py` 里两条直调 `_ledger_rows` 的测试留到切片 9。

**完成记录（2026-09-25）**:
- `BookRepository.claim_for_parsing(book_id, user_id) -> bool`：`UPDATE books SET status='parsing' WHERE id AND user_id AND
  status <> 'parsing' RETURNING id`。`reparse()`：先查原件（不在 → `_SOURCE_MISSING`，409 `source_missing`，一行都不写）→
  比较交换（拿不到行 → 门表 `(parsing, 有没有已发布版)` × REPARSE 那句，409 `book_parsing`）→ 受影响会话数、建任务、提交 →
  `_start`。原先的两个 400（`ApiError`）消失。`_SOURCE_MISSING` 与门表同处写，措辞也只在那里。
- 测试（`test_book_lifecycle.py`「重新解析」一节）：
  - 测试 9 `test_同时两次重新解析只有一个开跑`：先 RED——两个都拿到回执（job 2 与 3），库里多了两个任务；替身闸门挡住开跑的
    那一个，免得它在第二个到来之前就跑完。GREEN 后一个回执、一个 409 `book_parsing`，只多一个任务。
  - 测试 10 `test_原件不在时重新解析409且状态不动`：先 RED（400 `ApiError`），GREEN 后 409 `source_missing`、话里有
    「再传一次」、书仍是 ready、任务数不变。
- **测试 14 由改写后的 `tests/test_reparse.py` 承担**，没有另写一条堆在旁边：`_write` / `_reparse_with` 删除；`_book_with_talk`
  用替身照 V1 上传解析，`_reparse_to` 照 V2 / V3 经 `BookLifecycle.reparse()` + `drain()` 走真路径，重定位的账从新任务的
  `cleanup_stats` 读（切片 9 换成 `Ledger`）。**段号期望重算**：真 writer 在标题后写一行元信息引用块，块 0 标题、块 1 元信息，
  原先的 1 / 2 变成 2 / 3，V2 之后搬家的那条是 3。锁定清空、原地不动 / 位置变了 / 丢了分开记账、`relocated` 标记、回执与
  详情的受影响会话数——8 条全绿。
- 全量 **487 passed**（485 + 2），ruff 干净；`book_repo` / `test_reparse` 保持 CRLF。
- **设计读取**：同会话已读。应用：「要让代码分支的东西必须机读」（`source_missing` / `book_parsing` 写死）、「repositories
  只做数据存取、每个查询带 user_id」（比较交换在仓储里，带 `user_id`）、事务边界在 services（受影响会话数、建任务与比较交换
  同一个事务）。
- **偏差**：`test_turn_state._replace_chapters_elsewhere` 没有改走 `reparse()`，保留切片 1 的 `swap()`（验收写的就是「换章节只经
  `reparse()` 或 `swap()`」）：那本书是 `test_retrieval` 手工造的，没有原件；它测的是一轮提问对「章节被换掉」的反应，经完整
  重解析只会多出原件与替身两层布置，测不到更多东西。
- **规范候选**：无新增（比较交换是「先锁、后读」规范之外的另一种写法，但只此一处，先不提）。

---

### Slice 5: 书库卡片、详情读同一份状态：`state()` / `states()`

- **Type**: AFK
- **Blocked by**: Slice 3
- **接口契约 / 调用方分组**: `BookState` + `allows()`；`BookLifecycle.state()` / `states()`；调用方：
  `LibraryService.list_books` / `book_detail`；RFC「藏了什么」第 10 条；边界测试 8、11（后一半）
- **Complexity**: M

**What to build**:

- `BookState`（book_id / status / job_id / stage / done / total / error_code / message / remedy），`allows(use)`
  与 `require()` 查同一张门表。
- `state()` 列级查询、不经身份映射；`states()` 一页一条 LATERAL 查询取齐各书的最近任务，放在 `ParseRepository`、
  带 `user_id`。
- 书库列表的 `status` / `errorCode` 改读 `states()`，`ParseRepository.latest_error_codes` 删除；书籍详情的
  `status` 改读 `state()`。
- 这一片 `message` / `remedy` 读任务行上的列；切片 7 起 `message` 按码表给。

**Acceptance criteria**:
- [ ] 测试 8 `test_解析中途的进度从另一条连接看得见`：替身停在闸门上，另一条 session 读 `state()` 看到
      `convert 20/64`（取代切片 3 改写过的「解析中途的进度」那条直读 `ParseJob` 的写法）
- [ ] 测试 11（补全）：`state().allows(u)` 为真当且仅当 `require(use=u)` 不抛——门表五行 × 三种用途
- [ ] `states()` 一页 N 本书只发一条查询
- [ ] 书库卡片的 `status` / `errorCode` 与今天逐字相同（现有书库测试原样绿）
- [ ] 全量绿，ruff 干净

**Implementation hints**:
- LATERAL 子查询的排序与 `latest_job_for_book` 一致（`created_at DESC, id DESC`）。
- 列级查询绕开身份映射（规范「写发生在别处时，手上那个 ORM 对象就是旧的」）：进度是后台任务在另一条
  session 里写的。
- 从这一片起，新写的测试读结局一律走 `state()`。

**完成记录（2026-09-25）**:
- `ParseRepository.book_states(user_id, book_ids)`：一条 `LEFT JOIN LATERAL (… ORDER BY created_at DESC, id DESC LIMIT 1)`，
  列级、带 `user_id`；`latest_error_codes` 删除。`BookState`（`book_id / status / published / job_id / stage / done / total /
  error_code / message / remedy`，`allows(use)` 查门表）与 `BookLifecycle.state()`（没有 / 不是你的 → 404）/ `states()`。
- 书库列表的 `status` / `errorCode` 改读 `states()`；书籍详情的 `status` 改读 `state()`。
- 测试：
  - 门表那条参数化测试补上 `state().allows(use) is (expected is None)`——测试 11 补全（先 RED：`state` 不存在）。
  - 测试 8 `test_解析中途的进度从另一条连接看得见`：替身停在 `convert 20/64`，另一条连接轮询 `state()` 看到它（取代切片 3 那条
    直读 `ParseJob` 的写法）。
  - `test_一页书的状态一条查询取齐_取的是最近那一次任务`：三本书各两次任务，挂 `before_cursor_execute` 数语句——一条；每本取的是
    较新的那一次。
- 全量 **488 passed**，ruff 干净；`parse_repo` / `library_service` 保持 CRLF。
- **设计读取**：同会话已读。应用：「写发生在别处时，手上那个 ORM 对象就是旧的」（列级查询）、「repositories 每个查询带 user_id」、
  「同一条规则只许有一份实现」（`allows()` 与 `require()` 查同一张 `_GATE`）。
- **偏差**：
  - `BookState` 多一个 `published` 字段（RFC 的字段表里没有）：`allows()` 要查门表，门表的另一半就是「有没有已发布的一版」。
  - 「解析中途的进度」不再断言心跳推进：`state()` 不给心跳，心跳眼下只是多实例孤儿判据的预留（`backend.md` 待确认 #2），界面上
    看不见；进度在另一条连接上看得见，已经证明泵在工作。
  - `message` 这一片仍读任务行的 `error_message` 列；切片 7 改成按码表给。
- **规范候选**：无新增。

---

### Slice 6: 每条终局都落在 ready / failed

- **Type**: AFK
- **Blocked by**: Slice 3, Slice 5
- **接口契约 / 调用方分组**: RFC Problem 八-1、「藏了什么」第 4、6、7 条；边界测试 6、7
- **Complexity**: M

**What to build**:

- 进度泵：每一拍的写库失败只记日志、不外抛；`_stop()` 不再把泵的异常重新抛出——**泵不决定结局**。
  先停泵、再落终局（照旧）。
- 书目字段按列宽截断：title、author、translator、contributor、publisher、language、chapters.title，结尾加「…」；
  列宽从 ORM 列定义读（`Book.__table__.c.title.type.length` 一类），不另写一份数字。
- 终局清单对齐 RFC 第 7 条：引擎失败、引擎之外的意外、写库失败（`_fail_after_rollback`）、泵出错（不改结局）——
  每条都落在 ready 或 failed，`finished_at` 有值；进程重启由启动清扫收口（照旧）。
- **行为变化（已确认）**：超长字段截断，不再让整本书失败。

**Acceptance criteria**:
- [ ] 测试 7 `test_进度泵出错不会把一次成功的解析记成失败`：让泵写库抛错 → 结局 ready，日志里有泵的错误
      （先 RED：今天记成 `internal`）
- [ ] 测试 6 `test_写库那一半出错_书落在failed_章节撤回_还能再解析`：替身造一节 `level` 超出 smallint 的章节 →
      failed / `internal`、章节撤回、`reparse()` 照样收下
- [ ] 300 字的书名与章节标题 → ready，存下的是截断加「…」的版本，长度等于列宽（取代今天借 300 字标题触发
      写库失败的那条）
- [ ] 全量绿，ruff 干净

**Implementation hints**:
- 泵的写库失败要造得确定：让泵那条 session 的执行抛一次错（例如替换泵用的 session 工厂），不靠 sleep。
- VARCHAR(n) 在 PostgreSQL 里数的是字符不是字节，按字符截。

**完成记录（2026-09-25）**:
- 进度泵：每一拍 `try` 住，写库失败记 WARNING「进度没能落库…下一拍再来」并撤回这条 session，接着转；`_stop()` 不再把泵的异常
  抛回来（泵自己停下了记 ERROR）。泵不决定结局。
- `_fit(column, value)`：按列宽截断、结尾「…」，列宽取 `Book.__table__.c.<列>.type.length` / `Chapter.__table__.c.title`。
  书目字段 title / author / translator / contributor / publisher / language 与章节标题在写库时截；published / isbn / description
  是 TEXT，不截。
- 测试：
  - 测试 7 `test_进度泵出错不会把一次成功的解析记成失败`：替身报一个超出 INTEGER 的进度（`2**40`），泵写库那一句当场失败；挂
    `before_cursor_execute` 等到那句 UPDATE 真的发出去了再放行（不靠 sleep）。先 RED——结局 failed / `internal`「这次解析没能
    完成」；GREEN 后 ready，日志里有那条 WARNING。
  - `test_书目字段超过列宽就截断_不让整本书进不了书库`：300 字书名 / 作者 / 章节标题、40 字 language。先 RED（failed /
    `internal`「存进书库这一步出了错」），GREEN 后 ready，存的是「前 254 字 + …」（language 前 31 + …）。
  - 测试 6 `test_写库那一半出错_书落在failed_章节撤回_还能再解析`：由切片 3 那条改来，触发换成替身造一节 `level=40000`（超出
    SMALLINT）——超长标题现在会被截断，不再能拿来造这个失败。失败保护本来就在，这条换了触发之后原样绿。
- 全量 **490 passed**，ruff 干净。
- **设计读取**：同会话已读。应用：「后台写入的错误策略必须套住提交那一刻」（泵的 `try` 包住 execute 与 commit）、日志级别
  （泵的单拍失败是可继续的 → WARNING；泵整个停下 → ERROR）、「每条终局都落在 ready 或 failed」。
- **偏差**：无。
- **规范候选**：无新增。

---

### Slice 7: 给人看的话只来自码表；上传时就拒收不支持的格式

- **Type**: AFK
- **Blocked by**: Slice 3, Slice 5
- **接口契约 / 调用方分组**: 码表；`UnsupportedFormat(ApiError)`（415，`code = "unsupported_format"`）；RFC Problem 四、
  「藏了什么」第 1、8 条；调用方：`upload()`、解析失败落库、`BookState.message`；边界测试 3、4、5
- **Complexity**: M

**What to build**:

- 码表（`no_text_layer / no_content / bad_archive / unknown_format / unsupported_format / parse_failed / internal /
  orphaned`）：`error_message` 只取码表那一句；`remedy` 只放用户自己能做的事——扫描件给带真文件名的 OCR 命令，
  格式不支持给「换一个格式再传」，其余给「重新解析」或「再传一次」。引擎原话、回溯、SQL、服务器路径只进
  ERROR 日志，带书 id 与任务 id。`_classify` 的子串匹配留在 importer 内部。
- 上传：在建任何一行之前查一次 `format_service`——扩展名认得、这个部署没装后端 → 415 `UnsupportedFormat`，
  不建行、不存原件，装包命令只进 ERROR 日志。认不出的扩展名照旧交给魔数判定（后端不比前端粗筛更严）。
- 解析时：按魔数判出格式之后再问一次它能不能解，不能 → `unsupported_format`。覆盖「扩展名认不出、内容其实是
  PDF」与「重解析时后端已被卸掉」两种——后者今天给的是一条永远走不通的「重新解析」。
- `BookState.message` 按 `error_code` 从码表取（读时映射），旧行里的引擎原话也不会经 `state()` 露出来。
- **行为变化（已确认）**：`error_message` 不再带引擎原话；上传时 415。

**Acceptance criteria**:
- [ ] 测试 3 `test_超限空文件与部署不支持的格式在建行之前就被拒`：413 / 400 / 415；books 行数为 0，上传根为空；
      415 的话里没有 `pip`
- [ ] 测试 4 `test_扫描件停下来并给出带真文件名的OCR命令`：真引擎 + 现造的扫描 PDF（没装 `pymupdf` 就跳过）
- [ ] 测试 5 `test_失败说明只来自码表_原文进日志`：替身抛出带 `pip install`、服务器路径的异常；`error_message` /
      `remedy` / `state().message` 里都没有；caplog 里有原文与任务 id
- [ ] 重解析时后端不在 → `unsupported_format`，remedy 不叫人「重新解析」
- [ ] 删除 `test_import.py` 里被 4、5 取代的两条（扫描版 PDF、下载截断的 epub）；截断 epub 的真引擎覆盖由迁过来的
      「一本失败不影响同一批里的其余各本」接住，它补一条 `error_code` 断言（按实测）
- [ ] 全量绿，ruff 干净

**Implementation hints**:
- `format_service.rejection_reason()` 对**认不出**的扩展名也返回一句话，而上传这里只该拦「认得但不可用」——
  需要一个能分开两种情况的查询。
- `detect_format` 返回的格式键与 `format_service` 的 key 可能不完全一致（如 markdown），接的时候核对。
- `ImportFailed` 的 message 仍是引擎原话，**只在**落库那一刻换成码表那句；原话走 `logger.error(..., exc_info=…)`。
- 前端不用改：导入页上传失败显示 `ApiError.message`，`BookDetailView.reparse` 同样（已核对）。
- `sql.md` 里 `error_code` 的注释词表要加 `unsupported_format`——归收尾的 `design-doc-sync`。

**完成记录（2026-09-25）**:
- 码表 `_FAILURES`（8 个码，各一句 `message` + 一步 `remedy`）与 `_failure(code)`（认不出的旧码按「没能解析完」说）。
  `_record_failure(book, job, code, *, remedy="")`：`error_message` 只取码表那句；`remedy` 用 importer 给的更具体的一步
  （那是我们自己组的话，如带真文件名的 OCR 命令），没有就用码表的。引擎失败在 `_execute` 里记 ERROR「解析停下了：书 X 任务 Y
  （code）：原话」并带回溯；`sweep_orphans` 也改从码表取 `orphaned` 那句。`BookState.message` 按码读时映射，不读那一列。
- `core/errors.py` 加 `UnsupportedFormat`（415，`code = "unsupported_format"`）。`format_service` 加 `unsupported(filename)`（认得
  扩展名且没装后端才交回，认不出的不拦）、`available(key)`、`admin_hint(key)`。`upload()` 在读任何字节之前查
  `unsupported()`：拒收，话用 `rejection_reason()`（不带装包命令），装包命令进 ERROR 日志。`importer.import_file` 在按魔数判出
  格式之后再查一次 `available()`，不可用 → `ImportFailed(code="unsupported_format")`（话里带 admin hint，但只进日志）。
- 测试（`test_book_lifecycle.py`），先 RED（`UnsupportedFormat` 不存在，整个模块收集失败）后 GREEN：
  - 测试 3 `test_超限空文件与部署不支持的格式在建行之前就被拒`：413 / 400 / 415，books 零行、上传根没有文件，415 的话里有「PDF」
    没有「pip」。「没装后端」用夹具 `uninstall` 造（换掉 `_installed`、清探测缓存，测试结束再清一次）。
  - 测试 5 `test_失败说明只来自码表_原文进日志`：替身抛带 `pip install` 与 `/srv/…` 的异常；`state().message / remedy`、
    `job.error_message / remedy` 里都没有，ERROR 日志里有原话与「任务 N」。（原先 `error_message = str(failure)`，这条必红。）
  - `test_重新解析时后端已经没了_说换格式而不是再点一次重新解析`：上传时当作全装了，重新解析前卸掉 PDF 后端 →
    `unsupported_format`，remedy 说换格式、不说「重新解析」。
  - 测试 4 `test_扫描件停下来并给出带真文件名的OCR命令`（由原「解析失败的书标为failed…」升级）：remedy 里有 `ocrmypdf` 与
    「扫描件.pdf」，没有「原文件」；message 是码表那句；书库根下没有这本书的目录。
  - 「一本失败不影响同一批里的其余各本」补断言：截断的 EPUB → `bad_archive`。
  - 删除 `test_import.py` 里被取代的两条（扫描版 PDF、下载截断的 epub）。
- 全量 **491 passed**，ruff 干净；`importer` / `test_import` 保持 CRLF。
- **设计读取**：同会话已读。应用：「出路要按『谁会读到』写：用户做得到的走用户可见的位置，管理员才需要的走 ERROR 日志」、
  「要让代码分支的东西必须机读」（`unsupported_format` 写死）、「服务端…对方说的话一个字都不带回来」的同一精神用在引擎原话上、
  日志级别（解析停下是中断性失败 → ERROR）。
- **偏差**：
  - `internal` 在码表里只有一句「这次解析没能完成，问题出在服务器这边」：本轮早些时候写库失败那句「解析出来了，但存进书库这一步
    出了错」不再单列，区别留在 ERROR 日志（「解析结果没能落库：书 X 任务 Y」）。测试 6 的断言随之改看码表那句。
  - 解析停下（含扫描件这类用户文件的问题）现在都记一条带回溯的 ERROR；原先引擎失败不进日志。
- **规范候选（累积）**：#7（新）**给人看的失败说明只从写死的码表来，不转述下游的原话**：引擎 / 依赖 / 数据库的原话（安装命令、
  服务器路径、SQL 与参数）只进 ERROR 日志，带能查到这一次的 id；`BookState.message` 这类读路径也按码映射，不读存下的原话——
  旧行里可能还有。与既有规范「出路要按『谁会读到』写」同源，这条管的是「话从哪来」（`backend.md`「异常处理」）。

---

### Slice 8: 进度推送离开路由：`job()` / `watch()` 加寿命

- **Type**: AFK
- **Blocked by**: Slice 4
- **接口契约 / 调用方分组**: `BookLifecycle.job()` / `watch()`；新配置 `job_stream_max_seconds`；RFC Problem 六、
  「藏了什么」第 9 条；调用方：`routers/jobs.py`；边界测试 17、18
- **Complexity**: M

**What to build**:

- `job(*, job_id, user_id) -> JobOut`：取代 `ParseService.job_status` 与路由里的 `_to_out`。
- `watch(*, job_id, user_id)`：**普通协程**（同 `ChatService.ask`），404 在 await 时抛、在响应头之前；返回的异步
  迭代器每拍开一条短 session 新读（不再靠每拍 `rollback()` 刷身份映射），有变化才推 `stage`；终局映射成 `done`
  或带 `errorCode / remedy / bookId` 的 `error`，只推一次；超过 `job_stream_max_seconds`（默认 600）仍无终局就
  静默收尾，不带 `errorCode`。
- `routers/jobs.py` 只剩两个端点与 SSE 字节拼装；`parse_service.py` 删除。
- **行为变化（已确认）**：推送 10 分钟无终局静默收尾。

**Acceptance criteria**:
- [ ] 测试 17 `test_进度推送阶段变了才推_结局只推一次_到寿命就收`：失败时 `error` 带 `errorCode / remedy / bookId`；
      寿命调小后静默收尾，最后一个事件不带 `errorCode`
- [ ] 测试 18（HTTP，第一批）：`test_上传202_camelCase回执`、`test_任务查询与事件流都认得它`、
      `test_别人的任务_查询与事件流都是404_都在开流之前`、`test_重新解析撞上正在解析_409`
- [ ] `routers/jobs.py` 里没有 `get_session_factory`、`ParseRepository`、`rollback`、`_to_out`；
      `services/parse_service.py` 不存在
- [ ] 全量绿，ruff 干净

**Implementation hints**:
- 守规范「SSE handler 轮询数据库，不订阅进程内状态」。
- 流中途出错的「进度推送中断了，刷新一下页面」保留，不带 `errorCode`。前端 `useImports` 对不带 `errorCode` 的
  `error` 不当结局，流结束后每 1.5 秒轮询 `getJob` 直到结局——寿命到了静默收尾正好接上，前端不用改。
- 新配置进 `core/config.py`，注释写清它与心跳、轮询间隔三个节拍各管什么。
- HTTP 测试走 `client` 夹具，注册登录照 `test_auth_api` 的做法；上传之后 `drain()`。

**完成记录（2026-09-25）**:
- `BookLifecycle.job()`（→ `JobOut`，别人的 / 不存在 404）与 `watch()`（普通协程：先过 `job()` 的 404，再交回 `_follow()`）。
  `_follow()` 每拍开一条短 session 新读、有变化才推 `stage`、结局推一次（`done` 或带 `errorCode / remedy / bookId` 的
  `error`）、超过 `job_stream_max_seconds` 静默收尾；推送自己出事推一条不带 `errorCode` 的 `error`。`_job_out()` 从路由搬来。
- `core/config.py` 加 `job_stream_max_seconds = 600`，注释写清它是第三个节拍（推到什么时候为止）。
- `routers/jobs.py` 只剩两个端点与 `_sse()` 字节拼装；`services/parse_service.py` 删除。
- 测试：
  - 测试 18（HTTP 第一批）四条先写、在**旧路由上**就绿，作重构前的安全网：`test_上传202_camelCase回执`、
    `test_任务查询与事件流都认得它`（事件流以 `done` 收尾、带 `X-Accel-Buffering: no`）、
    `test_别人的任务_查询与事件流都是404_都在开流之前`（事件流回的是 JSON 404，不是一条空流）、`test_重新解析撞上正在解析_409`。
  - 测试 17 `test_进度推送阶段变了才推_结局只推一次_到寿命就收`：先 RED（`watch` 不存在）。已失败的任务 → `["stage", "error"]`，
    `error` 带 `errorCode / remedy / bookId`；寿命调到 0.3 秒、替身挡住不让结束 → 只有 `stage`，相邻两条都不相同。
- 全量 **496 passed**，ruff 干净；`jobs.py` / `config.py` 保持 CRLF。
- **设计读取**：同会话已读。应用：「SSE handler 轮询数据库，不订阅进程内状态」、「流式端点前面的每一层都不得缓冲」（
  `X-Accel-Buffering: no`，测试钉着）、`watch()` 是普通协程（同 `ChatService.ask` 的理由）、「routers 只做参数绑定与响应包装」。
- **偏差**：`JobOut.error_message` 也按码从码表读（与 `BookState.message` 同），不再读那一列——导入页显示的是它，旧行里可能
  还有引擎原话。
- **规范候选**：无新增。

---

### Slice 9: 解析报告改读类型化账本 `Ledger` 与 `book_dir()`

- **Type**: AFK
- **Blocked by**: Slice 4
- **接口契约 / 调用方分组**: `services/ledger.py`（`Ledger` / `rows()` / `to_json()` / `from_json()`）；`book_dir()`；
  RFC Problem 七；调用方：`importer._ledger`、解析成功落库、`LibraryService.report` / `_outputs` / `_source_size`；
  `tests/test_ledger.py`；测试 14 的报告那一行
- **Complexity**: M

**What to build**:

- **先写金标准**：`tests/test_ledger.py` 拿今天 `_ledger_rows` 对几份典型 `cleanup_stats`（旧行、含引文重定位、
  缺项）的渲染结果当期望，在旧代码上跑绿，再改实现。
- `Ledger`：source_format / chapters / words / images / footnotes / meta_hit / meta_fields / deduped_headings /
  engine（解析器自记的几笔，原样透传）/ relocation（只在真有引文搬家时才有）。`to_json()` 写出的就是今天的
  中文键字典——库里的存储格式不变，前端 `ImportRow` 读的键不变；`from_json()` 读旧行。
- 解析成功落库时组装 `Ledger`；「引文那一行只在 total > 0 时有」这条规则只剩 `Ledger` 里一份。
- `LibraryService.report`：`Ledger.from_json(job.cleanup_stats).rows(book=book)`；产出清单
  `_outputs(book_dir(book))`；原件体积读 `source_path(book)`。`_ledger_rows` 删除。

**Acceptance criteria**:
- [ ] `tests/test_ledger.py`：行序与「没数出来的不出行」；`from_json(to_json(x)) == x`；旧行照读、渲染出的行与
      今天逐行相同
- [ ] `test_reparse.py` 两条直调 `_ledger_rows` 的改为对 `Ledger.rows()` 断言；39 条引文的算术留作
      `test_ledger.py` 的单元测试
- [ ] 测试 14 补上：重解析之后报告里有「引文重定位」一行；首次解析的报告里没有
- [ ] 账本的中文键只在 `ledger.py` 里读写（`book.stats` 原样透传）；`library_service.py` 里没有中文键
- [ ] 全量绿，ruff 干净

**Implementation hints**:
- `ReportOut.stats` 原样给出 `cleanup_stats`，前端可能读——保持 `to_json()` 输出不变。
- 引擎 `book.stats` 的键（目录来源、spine 文件数、剔除的目录页）也是中文键，由 `engine` 透传、在 `rows()` 里读。

**完成记录（2026-09-25）**:
- **金标准先行**：`tests/test_ledger.py` 先对着旧的 `_ledger_rows` 写（期望值是当时渲染的原样输出：一本 EPUB 的全套、早先只记了三项
  的旧行、搬过引文的重解析、空账），在旧代码上 5 条全绿；`test_reparse` 里那两条直调 `_ledger_rows` 的（含 39 条引文的算术）
  一并挪来。随后 `_render` 换成 `Ledger.from_json(stats).rows(book=book)`，加上三条新的（先 RED：模块不存在）：
  `test_库里的存储格式不变`（`from_json(x).to_json() == x`）、`test_写进库里再读回来是同一本账`、`test_没有引文要搬家就不记那几笔`。
- 新建 `services/ledger.py`：`Ledger`（frozen；`moved(report)` 只在 `total > 0` 时记上引文那一笔——这条规矩原先在解析落库与
  书库渲染各写一遍，现在只在这里）、`to_json()` / `from_json()`（旧行照读，缺的项当作没数出来）、`rows(book=)`。十二个自有键与
  三个读取的引擎键都是模块常量，全仓库只在这里出现。
- 接线：`importer` 的 `ImportOutcome.cleanup_stats` 换成 `ledger: Ledger`，`_ledger()` 直接造 `Ledger`；解析落库
  `job.cleanup_stats = outcome.ledger.moved(swapped.relocation).to_json()`；`LibraryService.report` 读
  `Ledger.from_json(...).rows(book=book)`、产出清单 `_outputs(book_dir(book))`；`_ledger_rows` 删除；`book_lifecycle` 加
  `book_dir()`。`ReportOut.stats` 仍原样给出库里的字典。
- 测试 14 补上报告那一行：`test_reparse::test_解析报告只在真有引文搬家时才有引文重定位那一行`（首次解析的报告里没有，重解析 V2
  之后是「2 条旧引文，找回 2 条（其中 1 条位置变了）」）；`test_reparse` 的重定位断言改读 `Ledger.relocation`；测试 1 改读
  `Ledger.from_json(...).chapters`。
- 验收 grep：`src/` 里账本的中文键只在 `ledger.py`（测试里其余命中的是引擎 `meta.json` 的键，与账本无关）。
- 全量 **503 passed**，ruff 干净；新文件 LF，`importer` / `library_service` / `test_reparse` 保持 CRLF。
- **设计读取**：同会话已读。应用：「成功率一类的报告要分得清『成功』和『真的做过事』」（「位置变了几条」原样保留在那句话里）、
  「数据结构一律用 @dataclass」、「同一条规则只许有一份实现」（引文那一笔的规则收进 `moved()`）。
- **偏差**：`rows()` 里「切出 N 节」在账上没记章节数时退回 `book.chapter_count`（原先是 `stats.get('章节', …)`，只在键缺失时退回）：
  只有「键在、值为 0」时两者不同，而一次成功的解析不会切出 0 节。
- **规范候选**：无新增（「不经类型的跨层字典」是本 RFC 修掉的一个具体问题，不单独立规）。

---

### Slice 10: 暂存后发布：在用的目录从不原地改写

- **Type**: AFK
- **Blocked by**: Slice 4, Slice 6
- **接口契约 / 调用方分组**: RFC Problem 五、「藏了什么」第 5、11 条；`swap()` 的 `root` 改给暂存目录；
  `sweep_orphans()` 的磁盘对账；边界测试 12、13、16
- **Complexity**: L

**What to build**:

- 引擎只写进 `library_root/.staging/job-{id}/`（`import_file` 的 `dir_name` 给定值）；新的 `storage_path` =
  `{user}/{book}-{slugify(title)}`，在成功事务里写。`swap()` 的 `root` 给暂存目录，重定位读的是新正文。
- 成功事务**提交之后**才发布：现有目录挪到 `.retired`，暂存目录换成新的 `storage_path`，再删掉退役的那份；
  失败只删暂存。于是：在用的目录从不被原地改写，章节变少不留旧文件，书名变了旧目录一并收掉。
- 启动清扫：未收尾的任务 → `orphaned`、parsing 的书 → `failed`（照旧）；`.staging` 下按任务终态处理——已成功
  提交但没发布的补完发布（roll-forward），其余删掉；`.retired` 清掉。失败照抛。
- importer 的 `dir_name` 可调用那条路若已没有调用方，连同 `test_import::test_目录名可以由解析出的书名算出来` 删掉。

**Acceptance criteria**:
- [ ] 测试 12 `test_重新解析途中照读旧版_成功后换新且不留旧章节文件`：V1 → V2（节数变少、书名变了）；闸门挡着时
      阅读器逐字读到 V1；`drain()` 后读到 V2，`chapters/` 下文件数等于 V2 节数，旧目录不在
- [ ] 测试 13 `test_重新解析失败_旧版照读_提问409`：阅读器逐字读到 V1，提问 409 `book_failed`
- [ ] 测试 16 `test_重启清扫_孤儿收口_暂存清掉_已提交未发布的补完`
- [ ] 书名不变时重解析目录不变（迁过来的「重新解析复用同一个book_id与同一个目录」原样绿）
- [ ] 全量绿，ruff 干净

**Implementation hints**:
- `.staging` 必须和在用目录在同一个卷上，换目录才是原子的 rename（compose 的 `./data/library` 是一个挂载点）。
  这是规范「写文件要么整体成功、要么一个字不动」用在目录上。
- 发布在提交之后：发布失败（如 Windows 上目录被占用）记 ERROR、留着暂存，交给下次启动清扫补完——与崩溃走
  同一条 roll-forward，不另写一条路。「已提交未发布」的判据：任务 `finished_at` 有值、`error_code` 为空，
  而 `.staging/job-{id}` 还在。
- 本地开发时书库根就是引擎 CLI 用的 `library/`：确认 `booksoul list` / `library.py` 扫书库时跳过点目录。
- 老书（切片 10 之前原地写的）第一次重解析就会换成一份干净的目录，不需要迁移。

**完成记录（2026-09-25）**:
- 引擎只写 `library_root/.staging/job-<id>/`；新版位置 `shelf = {user}/{book}-{slugify(title)}` 在成功事务里写进
  `storage_path`，`swap()` 的 `root` 给暂存目录（重定位读新正文）。提交之后 `_publish(staging, target, book_id)`：在用的目录
  挪进 `.retired/job-<id>` → 暂存 rename 成正式位置 → 删掉退役的那份 → 同一本书旧名字的目录（`{book}-*` 里不是新位置的）一并删。
  引擎失败、意外、写库失败都只删暂存（`_discard`）；发布本身抛 `OSError` 记 ERROR、留着暂存交给下次启动。
- `sweep_orphans()` 在库这一半提交之后对账磁盘：`.staging` 下成功提交过的任务（`finished_at` 有值、`error_code` 为空、书有
  `storage_path`）照库里的位置补完发布（roll-forward，记 WARNING），其余删掉；不是 `job-<id>` 形状的也删；`.retired` 整个清掉。
- importer 的 `dir_name` 只收字符串（「按解析出的书名算目录名」那条路已无调用方），`test_import::test_目录名可以由解析出的书名算出来`
  一并删除。模块注释改成「在用的目录从不被原地改写」。
- 测试（`test_book_lifecycle.py`），先 RED 后 GREEN：
  - 测试 12 拆成两条。`test_重新解析写完了还没换上去_读到的仍是完整的旧版`：外来事务锁住这本书的会话行，解析写完正文、在换章节
    （先清锁定）那一步排队——正好停在「新正文已写好、还没提交」；此刻另开请求读第一节，逐字等于旧版。RED 时读到的是「新版的第一节」。
    `test_重新解析成功后换新_不留旧章节文件_书名变了旧目录收掉`：同名 2 节 → 1 节，`chapters/` 只剩 1 个文件（RED 时多出
    `002-乔达摩.md`）；再改名，旧目录不在、暂存收干净。
  - 测试 13 `test_重新解析失败_旧版照读_提问409`：失败放在**写完盘之后**（替身造 `level=40000`，写库那一半才撞上）——引擎这一层就
    失败的话还没写盘，分不出新旧做法；旧版逐字照读，提问 409 `book_failed`。
  - 测试 16 `test_重启清扫_孤儿收口_暂存清掉_已提交未发布的补完`：孤儿任务的暂存删掉，已提交那一版的暂存换到库里记的位置（「旧版」
    被「新版」换掉），`.retired` 清掉。
  - 12a、12b 的红逐条看过；13、16 那一轮的输出没有逐条展开，按旧代码推：原地写会让 13 读到新正文，旧清扫不碰磁盘会让 16 的
    暂存留着——两条都必红。
- 全量 **506 passed**，ruff 干净；`importer` / `test_import` 保持 CRLF。
- **设计读取**：同会话已读。应用：「写文件要么整体成功、要么一个字不动」（用在目录上：暂存 → rename）、「同一份磁盘状态不得被两个
  独立的数据库共用」（没有改变目录命名规则）、`sweep_orphans` 失败照抛。
- **偏差**：
  - 测试 12 拆成两条、测试 13 的失败挪到写盘之后，理由见上：RFC 原样的场景（改名的重解析、引擎层失败）在旧做法下也是绿的，钉不住
    「在用的目录从不被原地改写」。
  - 「发布本身失败 → 留给启动补完」这条路没有测试（在 Windows 上稳定造出 rename 失败要占住目录，得多开进程）；启动补完那一半由测试 16 钉着。
- **规范候选（累积）**：#8（新）**在用的目录不原地改写：写进暂存，提交之后整体换上；失败只删暂存；崩在中间，由启动对账照库里已提交的
  事实补完（库先提交、盘后动）**。是规范「写文件要么整体成功、要么一个字不动」在目录与「库 + 盘」两段式上的延伸（`backend.md`「写外部状态」）。

---

### Slice 11: 收尾：删掉旧实现和被取代的旧测试

- **Type**: AFK
- **Blocked by**: Slice 1–10
- **接口契约 / 调用方分组**: RFC「需删除 / 改写的旧测试」+「应暴露」清单；架构守卫（测试 19）
- **Complexity**: S

**What to build**:

- 对照 RFC「需删除 / 改写的旧测试」逐条对账：删在哪一片、改成了什么；RFC 列为保留的
  （`test_import::test_解析时按五个阶段回报进度`、`test_清扫失败不拦着服务起来`、`test_turn_state` 里用
  `_wipe_locks` 的几条）仍在且绿。
- 过渡残留清干净：`book_lifecycle` 对外只有 RFC「应暴露」列的名字，其余 `_` 前缀；`resettle` 对外只有 `swap`；
  `ParseRepository` 里没人用的查询删掉；`src/` 注释里指向旧名的说法改掉（迁移文件是历史，不动）。
- 测试 19 `test_书库根与上传根只在一处拼`：扫描 `src/booksoul_api`，`library_root` / `upload_root` 只允许出现在
  `book_lifecycle.py` 与 `core/config.py`。

**Acceptance criteria**:
- [ ] `src/` 与 `tests/` 里 grep 不到：`parse_service`、`ParseService`、`source_path_for`、`run_in_background`、
      `_check_readable`、`_STATUS_HINT`、`_ledger_rows`、`_reparse_with`、`_parsed_as`、`slow_import`、
      `resettle.prepare`、`resettle.apply`、`BackgroundTasks`、`latest_error_codes`
- [ ] 测试 19 绿
- [ ] 全量绿，ruff 干净

**完成记录（2026-09-25）**:
- 验收 grep：`src/` 与 `tests/` 里 `parse_service`、`ParseService`、`source_path_for`、`run_in_background`、`_check_readable`、
  `_STATUS_HINT`、`_ledger_rows`、`_reparse_with`、`_parsed_as`、`slow_import`、`resettle.prepare`、`resettle.apply`、
  `BackgroundTasks`、`latest_error_codes` 全部为零。`library_root` / `upload_root` 只在 `book_lifecycle.py` 与 `core/config.py`。
- 测试 19 `test_书库根与上传根只在一处拼`：扫 `src/booksoul_api`，只许那两个文件出现这两个名字。写的时候代码已经合规（前几片
  迁完了），所以当场就绿；**变异验证过**：往 `reader_service.py` 末尾加一行带 `library_root` 的注释，它报出这个文件。
- 对外名字：`book_lifecycle` 公开的是 `Use` / `UsableBook` / `BookState` / `BookLifecycle`（`require / state / states / job /
  watch / upload / reparse`）/ `book_dir` / `source_path` / `drain` / `sweep_orphans`，外加原有的 `UploadTooLarge`；其余 `_` 前缀。
  `resettle` 只剩 `swap` / `Swapped`。仓储里没有没人用的查询。
- 对照 RFC「需删除 / 改写的旧测试」逐条：
  | RFC 条目 | 落在哪片、成了什么 |
  |---|---|
  | `test_parse_service.py` 并入 `test_book_lifecycle.py` | 切片 3：文件删除，11 条迁入；`_parsed_as` / `slow_import` 换成 `fake_parser` |
  | `test_书库卡片带上未核对的存疑条数` 搬去书库测试 | 切片 3：搬去 `test_book_detail.py`（RFC 写的 `test_library.py` 测的是引擎，已更正） |
  | `test_清扫失败不拦着服务起来` 保留、换 import | 切片 3：在 `test_book_lifecycle.py`，原样绿 |
  | `test_reparse` 删 `_reparse_with` / `_write`，改走上传 → 重解析 → `drain()`；段号重算 | 切片 4：8 条全走真路径，段号 1/2 → 2/3 |
  | `test_reparse` 两条直调 `_ledger_rows` 的改对 `Ledger` 断言 | 切片 9：挪进 `test_ledger.py`（39 条引文的算术在那里） |
  | `test_turn_state._replace_chapters_elsewhere` 经生命周期重解析 | 切片 1 / 4：改调 `swap()`，未改走 `reparse()`（偏差见切片 4） |
  | `test_turn_state` 死锁测试内联三步 → 一次 `swap()`；锁序由测试 15 钉 | 切片 1 |
  | `test_turn_state` 的 `_wipe_locks` 几条保留 | 原样在、绿 |
  | `test_import` 只留「按五个阶段回报进度」，其余由测试 2 / 4 / 5 取代 | 切片 3 / 7 / 10：该条保留；另留一条新加的「解析完就放开原件」（切片 3 修 EPUB 句柄泄漏时加的） |
  | 三份 `_imported` 合并成 conftest 的 `imported_book` | 切片 3（`test_parse_service` 那份随文件删除） |
  | `test_book_detail` 的「装得下」同义反复不在范围 | 未动 |
- 全量 **507 passed**，ruff 干净；前端 `vue-tsc` / `eslint` / `prettier` 干净（前端到这一片为止没有改动）。
- **设计读取**：同会话已读。应用：「私有函数 / 模块级私有常量用 `_` 前缀」、「同一条规则只许有一份实现」（路径拼法由测试 19 守着）。
- **偏差**：无。
- **规范候选**：无新增。

---

### Slice 12: 书籍详情改为轮询轻量的状态接口

- **Type**: AFK
- **Blocked by**: Slice 5
- **接口契约 / 调用方分组**: `GET /api/books/{book_id}/state`；RFC 建议顺序第 7 条；调用方：`BookDetailView` 的
  解析中轮询、`BookCard` 的错误码说法
- **Complexity**: S

**What to build**:

- 后端：`GET /api/books/{book_id}/state` 回 `BookState` 的对外形状（status / jobId / stage / done / total /
  errorCode / message / remedy，camelCase）；别人的书 404。
- 前端：`api/books.ts` 加 `getBookState`，`types` 加对应类型；`BookDetailView` 解析中每 1.5 秒改轮询它，状态离开
  parsing 时再拉一次整份详情（章节、字数、存疑都随解析结果变了）；「重新解析」之后留在原页的行为不变。
- `BookCard`：为 `internal` 与 `unsupported_format` 配专门的角标与说明，其余照旧走兜底「停下了」。

**Acceptance criteria**:
- [ ] HTTP 测试：解析中 / ready / failed 三种状态的回执；别人的书 404
- [ ] 解析中的详情页每拍只请求 `/state`，离开 parsing 才拉一次整份详情（浏览器 Network 面板核对，归切片 13）
- [ ] `vue-tsc` / `eslint` / `prettier` 干净，前端文件保持 LF
- [ ] 全量绿，ruff 干净

**Implementation hints**:
- 今天解析中每 1.5 秒拉一次整份详情：大脑列表（每次读配置文件）、审核记录、会话数、章节、存疑全算一遍。
- 请求只从 `api/` 层发出；`types/` 只放后端契约类型（`frontend.md` 规范）。
- 本轮用户裁定的前端规范「不把人丢在别处」：重新解析之后留在原页，这一片不得回退。

**完成记录（2026-09-25）**:
- 后端：`schemas/book.py` 加 `BookStateOut`（`BookState` 的对外形状，camelCase：bookId / status / published / jobId / stage / done /
  total / errorCode / message / remedy）；`routers/books.py` 加 `GET /api/books/{book_id}/state` → `BookLifecycle.state()`。顺带把
  `reparse_book` 注释里「复用同一个目录」改成「书名没变时目录也不变」（暂存后发布之后改名会换目录）。
- 前端（全 LF）：`types` 加 `BookState`；`api/books.ts` 加 `getBookState`；`BookDetailView` 解析中每 1.5 秒改问 `watchParse()`
  （只读状态），离开 parsing 才 `refreshBook()` 拉一次整份详情；状态请求失败照样排下一拍，横幅不收走已摆出来的书；「重新解析」后
  留在原页不变。`BookCard` 为 `unsupported_format`（「格式不支持 / 这里暂不支持 · 换个格式再传」）与 `internal`（「没能完成 / 服务器
  这边出了错 · 可以再解析一次」）配了说法；`unsupported_format` 不摆「重新解析」按钮——原样重跑一遍还是一样。
- 测试：`test_书的状态接口_解析中好了停下了都说得清_别人的书404`（HTTP；替身闸门挡着时读到 parsing 与 jobId，之后 ready，另一本失败的
  是 failed / `parse_failed` 带 message 与 remedy，别人 404），先 RED（路由不存在，404）后 GREEN。前端没有测试框架，`vue-tsc` /
  `eslint` / `prettier` / `vite build` 干净；「解析中每拍只请求 /state」「格式不支持的卡片没有重新解析按钮」的肉眼核对归切片 13。
- 全量 **508 passed**，ruff 干净；`routers/books` / `schemas/book` 保持 CRLF。
- **设计读取**：`frontend.md` 全文（含「设计模式与约定」全部小节）。应用：「数据请求只在 `api/` 层发起」「`types/` 只放后端契约
  类型」「失败的措辞只在服务端写一处，前端按 `code` 决定行为」（角标短语照 `BookCard` 既有的按码映射；是否给重试按 `code` 决定）、
  「不把人丢在别处」（重解析后留在原页）、源码 LF 且过 prettier、注释写明与后端契约相关的取舍。
- **偏差**：`BookStateOut` 多带一个 `published`（门表的另一半，重解析中照读旧版要它），计划里的字段表没有。
- **规范候选**：无新增（「走不通的出路不摆按钮」是既有规范「失败的措辞…前端按 code 决定行为」的又一例）。

---

### Slice 13: 真实环境走一遍

- **Type**: HITL
- **Blocked by**: Slice 11, Slice 12
- **接口契约 / 调用方分组**: 端到端确认
- **Complexity**: S

**What to build**:

`docker compose up -d --build api web`（无迁移），在浏览器里走：

1. 导入页拖一本 EPUB：进度条走完，书库里出现；再拖一本扩展名改成 .txt 的 EPUB：照样解析成功
2. 拖一个本部署不支持的格式：导入页当场说「这个部署暂不支持…」，书库里不多一本（格式全装了就跳过，由测试 3 保证）
3. 书籍详情点「重新解析」，解析中打开阅读页：读得到旧版；完成后详情页自己更新、阅读页是新版；解析中详情页
   每拍只请求 `/state`
4. 两个标签页几乎同时点「重新解析」：只跑一次，另一边看到「正在解析」
5. 阅读页底部路径是 `library/…`，照着能在宿主机 `data/library/…` 下找到那个文件
6. 解析失败的书（如扫描版 PDF）：导入页与书库卡片的话里没有 `pip`、没有服务器路径；扫描件给带真文件名的 OCR 命令
7. 解析中重启 api 容器：书变成「停下了」、能重新解析；`data/library/.staging` 里不留东西
8. 常规提问、引文「去原文」、「回到对话」照常

**Acceptance criteria**:
- [ ] 以上逐条由你确认；有偏差就回到对应切片修
- [ ] 只报告观察，不把真实 key 贴进对话

**完成记录（2026-09-25）**:
- 部署：`docker compose up -d --build api web`，无迁移。api healthy；冒烟：`/api/health` 200，未登录时 `/api/books` 与
  `/api/books/1/state` 都是 401（新路由在），不存在的路径 404；容器里是新代码（有 `book_lifecycle.py` / `ledger.py`，没有
  `parse_service.py`），启动日志没有 ERROR；web 包里有 `/state` 与 `unsupported_format`。
- 走查：用户在 http://127.0.0.1:8317 按上面 8 条走完，回复「验过了」，没有报告偏差；对话里没有出现 key。
- **偏差**：无。
- **规范候选**：无新增。累积的 #1–#8 的判定结果见文末「规范候选判定」。

## Dependency Graph

```
Slice 1 ─┐
         ├─→ Slice 3 ─┬─→ Slice 4 ─┬─→ Slice 8
Slice 2 ─┘            │            ├─→ Slice 9
                      │            └─→ Slice 10 ←─┐
                      └─→ Slice 5 ─┬─→ Slice 6 ───┘
                                   ├─→ Slice 7
                                   └─→ Slice 12 ──┐
                 Slice 1–10 ─→ Slice 11 ──────────┴─→ Slice 13
```

编号即一条合法的执行顺序（1 → 13）。

## Risk Notes

1. **清空根目录可能删掉真书库（切片 3，先堵）。** conftest 今天对 `BOOKSOUL_LIBRARY_ROOT` /
   `BOOKSOUL_UPLOAD_ROOT` 用的是 `setdefault`：开发者环境里 export 过这两个变量时，测试就写进真目录。加上「每个
   测试前清空」之后，一次 pytest 就会把真书库清掉。改成**赋值**（与 `BOOKSOUL_DATABASE_URL` 那行同理，那里的注释
   写着同一个坑），要自定义测试目录另起 `BOOKSOUL_TEST_*` 变量名。
2. **后台任务与测试引擎的寿命。** `create_task` 排出去的解析活得比请求长；测试之间 `reset_engine()` 会关掉引擎，
   还在跑的任务得到偶发的连接错误，症状出现在下一条测试里。`_no_parse_left_running` 守住「每条测试结束时没有
   在跑的解析」，夹具退出前 `drain()`。
3. **排任务的时机。** 提交与 `create_task` 之间不得有可被取消的 await（客户端断开会取消请求协程）——否则书停在
   「解析中」、没有任务在跑，直到下次启动清扫。
4. **锁序不变**：会话行在前、章节行在后，只在 `swap()` 里出现一次，测试 15 钉着；切片 10 换 `root` 不动次序。
5. **暂存后发布（切片 10）**：`.staging` 与在用目录同卷；发布失败与崩溃走同一条 roll-forward；Windows 上换目录
   要求目标不存在（先退役、再换入）。
6. **测试数据的形状会变**：`test_reparse` 改走真 writer 后段号期望要重算（标题后多一块元信息引用块）；
   `fake_parser` 造的书经过真清洗（`drop_duplicate_headings` 等），正文里与标题相同的首行会被去掉。
7. **对 RFC 的两处更正**（见「与 RFC 的差异」）：存疑计数测试的去处、测试 6 的触发方式。
8. **`format_service` 的接法**：`rejection_reason()` 分不开「认不出」与「认得但不可用」；`detect_format` 与
   `format_service` 的格式键要核对。
9. **前端没有测试框架。** 切片 12 只有静态检查，肉眼验证在切片 13。
10. **单实例假设不变**（`backend.md` 待确认 #2）。`_RUNNING` 在进程内；启动清扫的判据「进程刚起来就没人在跑」
    照旧——本机 `uvicorn --reload` 与 compose 里的 api 同时连一个库，仍然是两个副本。
11. **规范**：本计划不改任何规范条目。实施中可能提出的候选（如「在用的目录从不原地改写：暂存后发布」「给人看的话
    只来自码表」「后台任务由拥有它的模块排，不归路由」）按 tdd 的规矩列出、经用户判定后才写入；
    `design-doc-sync` 只同步架构章节（`backend.md` 模块边界、上传解析与重解析流程、错误码、新配置、待确认 #2 里
    「当前用 FastAPI `BackgroundTasks`」一句；`frontend.md` 书籍详情的轮询与 API 调用层；`sql.md` 的
    `error_code` 注释词表）。
12. **部署**：无迁移。compose 里的 api 与 web 跑的是旧代码，切片 13 之前重建。老书的目录不动，第一次重解析时
    换成一份干净的。

## Status Tracking

| Slice | Status | Assignee | Notes |
|-------|--------|----------|-------|
| 1 | 🟢 Done | - | 465 passed |
| 2 | 🟢 Done | - | 483 passed |
| 3 | 🟢 Done | - | 485 passed；顺带修掉 EPUB 句柄泄漏 |
| 4 | 🟢 Done | - | 487 passed |
| 5 | 🟢 Done | - | 488 passed |
| 6 | 🟢 Done | - | 490 passed |
| 7 | 🟢 Done | - | 491 passed |
| 8 | 🟢 Done | - | 496 passed |
| 9 | 🟢 Done | - | 503 passed |
| 10 | 🟢 Done | - | 506 passed |
| 11 | 🟢 Done | - | 507 passed |
| 12 | 🟢 Done | - | 508 passed；前端静态检查与构建干净 |
| 13 | 🟢 Done | - | HITL：已重建 api + web（无迁移），用户走查 8 条「验过了」 |

Status: 🔴 Not started | 🟡 In progress | 🟢 Done | ⚠️ Blocked

## 规范候选判定（全部切片完成后，2026-09-25）

auto 模式累积的 8 条逐条交用户判定，**全部写入**（每条末尾带本计划的来源）：

| # | 候选 | 写到哪里 | 怎么写 |
|---|------|---------|-------|
| 1 | 锁序那条的实现引用过期（`prepare()`） | `backend.md`「并发与后台任务」第 3 条 | 只改来源括号：实现改指 `resettle.swap()`，次序由测试 15 钉着；正文不动 |
| 2 | 「要读的旧状态…」的实现引用过期（`prepare()` → `apply()`） | `sql.md`「约定」最后一条 | 只改来源括号：实现改指 `resettle.swap()`；正文不动 |
| 3 | 测次序的测试要驱动生产代码的那一份 | `backend.md`「架构级模式」，接在 parity 那条后面 | 新增 |
| 4 | 持有外部资源的对象用完当场关 | `backend.md`「写外部状态」 | 新增 |
| 5 | 测试会清空的位置由测试自己指定（赋值，不 `setdefault`） | `backend.md`「写外部状态」 | 新增 |
| 6 | 后台任务由拥有那份状态的模块自己排 | `backend.md`「并发与后台任务」末尾 | 新增 |
| 7 | 给人看的失败说明只从码表来 | `backend.md`「异常处理」，接在「出路要按『谁会读到』写」后面 | 新增 |
| 8 | 在用的目录不原地改写：暂存后发布 | `backend.md`「写外部状态」，接在「写文件要么整体成功」后面 | 新增 |
