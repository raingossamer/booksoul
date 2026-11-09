# 后端设计文档

> **本文档职责**：记录后端架构、技术栈、模块边界、API 契约、认证授权与关键业务流程（架构章节），以及后端**项目级代码规范**（「设计模式与约定」章节）。
> 架构章节由 `to-design` 生成（正向 0-to-1 / 逆向）、由 `design-doc-sync` 在每轮开发收尾时同步更新。
> **「设计模式与约定」章节例外**：它是项目级代码规范，只由 `to-design` 首次建立，或由 `tdd` / `read-design-before-code` 列出规范候选、经你显式判定后写入；`design-doc-sync` 不改动它。
> 是 `grill-me-with-design` / `write-a-spec` / `tdd`（GREEN 阶段）读取的约束源之一。
>
> 以下为约定骨架，章节标题固定，内容按实际项目填充；不适用的章节保留标题并标注"暂无"。占位词约定见 `ProjectModel/README.md`。
>
> **本次生成为混合模式**：`booksoul` 引擎部分为**逆向**（每条附源码证据），HTTP / 持久化 / 认证部分为**正向**（每条附社区基线来源）。

## 技术栈

| 项 | 选型 | 来源 |
|----|------|------|
| 语言 | Python ≥ 3.11 | 〔来源：`pyproject.toml` `requires-python = ">=3.11"`〕 |
| Web 框架 | FastAPI | 〔来源：用户指定〕 |
| ASGI 服务 | Uvicorn | 〔来源：FastAPI 社区基线〕 |
| 校验 / 序列化 | Pydantic v2 | 〔来源：FastAPI 社区基线〕 |
| ORM | SQLAlchemy 2.0（async）+ Alembic 迁移 | 〔来源：FastAPI 社区基线〕 |
| 数据库 | PostgreSQL 16 | 〔来源：用户指定〕 |
| 认证 | JWT（access + refresh） | 〔来源：用户指定〕 |
| 解析引擎 | 本仓库 `booksoul` 包，**同进程 import**，非独立服务 | 〔来源：`src/booksoul/`，`pyproject.toml` `[project.scripts]`〕 |
| 解析依赖 | lxml（必需）／pymupdf、python-docx、mobi、chardet（可选） | 〔来源：`pyproject.toml` `[project.optional-dependencies]`〕 |
| 模型后端 | anthropic SDK ／ 任意 OpenAI 兼容端点（标准库 HTTP） | 〔来源：`src/booksoul/brain.py` 模块 docstring〕 |
| 任务队列 | 待确认（见「待确认问题」） | — |
| 部署 | `docker compose`：`db`(postgres:16) + `api`(uvicorn) + `web`(nginx 提供静态并反代 `/api`)。**`db` 对本机开 `127.0.0.1:5432`**：本机开发（`uvicorn --reload`）与测试套件都连它，不再另起一个 Postgres；测试用其中的 `booksoul_test` 库 | 〔来源：`compose.yml` / `Dockerfile` / `tests/conftest.py`〕 |

**为什么后端也是 Python**：解析引擎已经是 Python 且是本项目的核心资产（中文排版清洗、BM25 调优、多后端接入）。换语言等于隔一层 RPC 调自己，徒增一次序列化和一个部署单元。API 层直接 `import booksoul`。

## 目录结构与分层

```
src/
├── booksoul/                  # 现有引擎（逆向）—— 不依赖 Web 框架，可独立作为库/CLI 使用
│   ├── model.py               #   Book / Chapter / BookMeta，所有解析器的统一产出
│   ├── parsers/               #   各格式解析，__init__.py 按魔数探测并分发
│   ├── html2md.py             #   (X)HTML → Markdown
│   ├── xmlutil.py             #   XML 剥命名空间
│   ├── clean.py               #   正文清洗：缩进、水印、硬换行、PDF 页眉页脚
│   ├── structure.py           #   无目录时从正文推断章节
│   ├── footnotes.py           #   脚注归位成标准 Markdown 脚注
│   ├── writer.py              #   写出书库目录（book.md / INDEX.md / meta.json / chapters/）
│   ├── library.py             #   把书从书库读回 Book
│   ├── retrieve.py            #   装不下时挑章节（BM25）
│   ├── persona.py             #   加载 prompts/ 下的人格模板
│   ├── brain.py               #   可插拔模型后端（anthropic / openai 兼容）
│   ├── config.py              #   大脑配置与预设
│   ├── ask.py                 #   Session：把书、人格、大脑接到一起
│   └── cli.py                 #   命令行入口
└── booksoul_api/              # 新增 HTTP 层（正向）
    ├── main.py                #   FastAPI app 装配、CORS、异常处理器
    ├── core/                  #   config（Settings）、security（JWT、密码哈希）、errors、logging、paths
    ├── db/                    #   engine、session、base、unit_of_work（脱离请求的工作单元）
    ├── models/                #   SQLAlchemy ORM 实体
    ├── schemas/               #   Pydantic 请求/响应模型
    ├── repositories/          #   只做数据存取，不含业务判断
    ├── services/              #   业务编排，唯一允许 import booksoul 的层
    │                          #   （book_lifecycle.py：一本书从收件到可用——门表、收件、重解析、
    │                          #     后台解析、暂存后发布、启动清扫；ledger.py：解析账本的类型；
    │                          #     turn.py：Turn 值对象 + TurnRunner，一轮提问的执行；
    │                          #     turn_state.py：会话的轮次状态——准入、事实、写提问、收尾落库；
    │                          #     stream_recorder.py：一轮回答的流，只在内存里；
    │                          #     context_window.py：正文额度与它的来路）
    ├── routers/               #   FastAPI 路由，一个资源一个文件
    └── deps.py                #   依赖注入：当前用户、DB 会话、大脑配置

Dockerfile                     # 三阶段：web-build(node) → web(nginx) → api(python)
compose.yml                    # db + api + web；web 与 db 各对本机开一个口（8317 / 5432），都只绑 127.0.0.1
docker/
├── nginx.conf                 #   静态 + SPA 回退 + /api 反代（SSE 关缓冲）
└── api-entrypoint.sh          #   alembic upgrade head 之后再起 uvicorn

<书库根>/                       # BOOKSOUL_LIBRARY_ROOT；compose 下是绑定挂载的 ./data/library
├── <user_id>/<book_id>-<书名>/ #   在用的那一版（books.storage_path），引擎写出的书库目录
├── .staging/job-<任务 id>/     #   解析中的新版：引擎只往这里写，提交之后整个换上去
└── .retired/                   #   换上去那一刻挪开的旧版，随即删掉；启动清扫兜底清空
<上传根>/<user_id>/<book_id>/<原文件名>   # BOOKSOUL_UPLOAD_ROOT（compose：./data/uploads）；原件，重新解析从它开始
```

**书库根与上传根只在 `services/book_lifecycle.py` 里和一本书的路径拼在一起**（外加 `core/config.py` 的定义），`tests/test_book_lifecycle.py::test_书库根与上传根只在一处拼` 扫 `src/booksoul_api` 守着这一条。

**前端静态文件由 nginx 提供，不由 FastAPI 提供。** 路由是 `createWebHistory()`，深链
（`/books/3/read/2`）必须回 `index.html`；在 Python 里做要同时处理条件挂载、SPA 回退、
以及「别让回退吞掉 `/api/*` 的 404」——漏掉最后一条，一个打错的接口路径会变成 200 的
HTML。nginx 的 `try_files` 两行解决，且 API 层不必知道前端构建产物在哪。

分层：`routers → services → repositories → models`。`services` 是唯一调用 `booksoul` 引擎的层，`routers` 和 `repositories` 都不得直接 import 引擎。

## 模块边界

| 模块 | 职责 | 不负责 |
|------|------|--------|
| `booksoul`（引擎） | 文件 → Book → 书库目录；书库 → Book；选章；人格渲染；模型调用 | HTTP、用户、权限、数据库 |
| `services/book_lifecycle` | **一本书从收件到可用的全部状态迁移**，取代原先的 `parse_service`。对外只有：`BookLifecycle(session)` 的 `require` / `state` / `states` / `job` / `watch` / `upload` / `reparse`，模块函数 `drain` / `book_dir` / `source_path` / `sweep_orphans`，值类型 `Use` / `UsableBook` / `BookState`（外加 `UploadTooLarge`）。**门**：`(状态, 有没有已发布的一版)` × 用途（READ / ASK / REPARSE）一张表（`_GATE`），拦下时的 404 / 409 与 `code`（`book_parsing` / `book_failed`；原件不在另有 `source_missing`）、措辞都只写在这里；`require()` 过门并交回**路径已经拼好**的 `UsableBook`（`root` / `file()` 走 `core/paths.under()` / `display()`），`BookState.allows()` 查同一张表。**收件与重解析**：415 / 413 / 400 都在任何一次写库之前；重解析是一次比较交换（`BookRepository.claim_for_parsing`）；提交之后同步 `create_task` 排上后台解析（`_RUNNING`、`drain()`）。**后台解析**：自开 session，引擎在工作线程里只写暂存目录，进度泵出错不决定结局；成功一个事务（书目按列宽截断、`resettle.swap()`、存疑条目、`Ledger`），提交之后 `_publish()` 换上去；**每条结局都落在 ready 或 failed**，给人看的话只来自码表 `_FAILURES`（读的时候也按码映射），引擎原话只进 ERROR 日志。**状态**：`states()` 一页书一条 LATERAL 查询（`ParseRepository.book_states`）。**进度推送**：`watch()` 每拍一条短 session 新读、有变化才推、结局推一次、到 `job_stream_max_seconds` 静默收尾。**启动清扫**：孤儿收口、暂存对账。书库根与上传根下的全部路径都在这里定 | 解析算法本身（在引擎，经 `importer`）；换章节的善后次序（在 `resettle`）；账本的键与渲染（在 `ledger`）；SSE 字节拼装（在路由层） |
| `services/ledger` | 解析账本的类型 `Ledger`：`to_json()` / `from_json()`（`parse_jobs.cleanup_stats` 的存储格式不变，旧行照读）、`rows(book=)`（②屏那几行）、`moved(report)`（引文那一笔只在真有引文搬家时才记）。账本的中文键全仓库只在这里 | 数都是谁数的（引擎、importer、重定位） |
| `services/importer` | 文件进、书库目录出：调引擎解析并写进调用方给的 `out_root/dir_name`（Web 层给的是暂存目录 `job-<id>`），返回 `ImportOutcome`（带 `Ledger`）。按魔数判出格式之后再问一次 `format_service.available()`，这个部署解不了 → `unsupported_format`；失败时归成一个 `error_code` 与一句补救（`ImportFailed`，引擎原话留在异常里、只进日志）；写完盘（成功或失败）就关掉解析器（`close()`）。不碰数据库、不碰 HTTP | 数据库编排与目录换代（在 `book_lifecycle`） |
| `services/format_service` | `/api/formats` 的运行时探测：这个部署装了哪几种解析后端、上限多少 MB。`unsupported(filename)`（认得扩展名、后端没装才交回；认不出的不拦，交给魔数判定）供上传当场拒收（415），`available(key)` 供 importer 判完格式后复核，`admin_hint(key)`（缺哪个包）只进 ERROR 日志，给用户的只说「暂不支持」（`rejection_reason`） | 解析本身 |
| `services/quality` | **纯函数**：字数条的质检判据（哪一节短得不像正文） | 数据库、HTTP |
| `services/library_service` | 书库列表 / 详情、解析报告。状态与失败码读 `BookLifecycle.states()` / `state()`；账本的行读 `Ledger.from_json(...).rows()`，产出清单读 `book_dir()`，原件体积读 `source_path()` | 解析；章节正文与书里的图（在 `reader_service`）；书的状态、磁盘位置与失败说明（在 `book_lifecycle`） |
| `services/reader_service` | ⑤屏：单章正文**原样**读出；书里的图（`image()`：只端 `images/` 下那一层的位图白名单，名字里不许有目录，SVG 不端——前端取成的 `blob:` 地址继承本站的源）。过门走 `BookLifecycle.require(use=READ)`：重解析中、重解析失败之后照读上一次成功的那一版，首次解析中 / 首次失败 404。正文走 `UsableBook.file()`（先 resolve 再核对没出书目录，越界 409；文件不在是 409 不是空串），图以 `images/` 为界（`under(book.root / "images", name)`），⑤屏底下那行路径走 `display()`：从书库根的目录名起算、一律正斜杠（本地与 compose 都是 `library/…`） | 解析；渲染（在前端）；书的状态与磁盘位置（在 `book_lifecycle`） |
| `services/chat_service` | 会话的开合与读取侧（开、列最近的 / 按书列、详情、删——删经 `turn_state.forget_conversation`）。开会话是 `begin`：会话那一行只 flush 出 id、不提交，随即当作第一问交给 `ask`，由 `turn_state.admit` 写下第一问时一起提交——第一问问不出去就什么都不留；`start` 只建一行并提交，网页那条路不用它（测试里要一个现成会话时从它起）。另有**一轮提问请求期的那些活**：鉴权、书能不能问（`BookLifecycle.require(use=ASK)`：解析中 / 停下了 → 409 带 `code` `book_parsing` / `book_failed`；书的目录取 `UsableBook.root`，不自己拼）、人格、大脑配置、章节与预算，算成一个 `Draft` 交给 `turn_state.admit` | 这一轮能不能开、第几轮、锁定、逃生门、写提问（在 `services/turn_state`）；一轮的执行（在 `services/turn`）；人格提示词内容（在 `prompts/`） |
| `services/turn` | 按「谁算出来的」分组的 `frozen` 值对象：`BookRef` / `Budget` / `BrainRef` / `Draft`（请求期的全部输入，`ChatService.ask` 算）/ `Standing`（占住会话之后才读得对的：第几轮、历史、锁定、告示，`turn_state.admit` 算）/ `Notice`；`Turn = Draft + standing`。以及 **`TurnRunner.run(recorder, turn)`**：选章的触发、读盘、引擎 `Session` 装配、三路并发与错误边界、错误翻译——大脑出的事按 `BrainError.kind` 换成网页上的一句（`_ASK_FAILURES`：key 没认、限流、没有这个模型、服务出错、连不上、太久没回音、这台服务器用不了；认不出的归「这次没答完」），**不带对方的原话**，日志也只记类别；审核另走 `_moderation_advice`；缺 SDK 时装什么包进 ERROR 日志 | 这一轮能不能开、落库、断线重连（在 `services/turn_state`）；选章算法（在 `chapter_selector`）；SSE 字节拼装（在路由层） |
| `services/turn_state` | **会话的轮次状态**，对外只有 `admit(session, draft)`、`forget_locks(session, book_id=)` 与 `forget_conversation(session, conversation_id=)`。`admit`：进程内席位做单飞（同一个问题正在答 → 挂上去回放；另一个问题 → 409；首领在准备阶段失败，等着的人自己重来；让位认人、在收尾提交之后；孤儿守卫）→ 占位之后先锁会话行、再另起语句读锁定 / 最近留档 / 轮号 / seq 与历史 → 逃生门 → 写提问 → 本轮告示 → 启动后台任务。收尾（`_settle`，作为 recorder 的 `persist`）：**一个事务**，先锁会话行、发号，已不存在的章节 id 置空，写留档、锁定、回答、引文。`selected_chapter_ids` 的**唯一写者**。删会话（`forget_conversation`）也占同一张席位表：有一轮在跑 → 409；占住之后再删，删的这一小会儿里来的提问听到的是「正在删除」 | 选章算法、引文定位算法；SSE 格式 |
| `services/stream_recorder` | 一轮回答的流：累积、广播、重连回放（告示全量回放，stage 只留最后一句）；退出时把 `Outcome` 交给 `persist`。**不碰数据库** | 落库（在 `turn_state`）；这一轮正在不正在跑（`turn_state` 的席位） |
| `db/unit_of_work` | `Detached`：脱离请求的工作单元——session 从哪来、什么时候提交、失败吞还是抛（`raise` / `log`）、日志怎么写，四件事收成一个名字。原先的 `UnitOfWork` 端口与测试适配器 `Attached` 已随 turn-state RFC 撤掉：没有生产代码注入它，后台写入的测试缝上移到 recorder 的 `persist` 与真库 | 长活的 session（解析任务、SSE 轮询各有自己的生命周期，见其就地注释） |
| `services/brain_service` | ⑧屏那张表的唯一来源：内置预设（引擎 `available_brains()`）**+ 这个人的模型选择**（`brain_choices`，只覆盖模型与窗口）+ 自定义端点。`describe(user_id, name)` 是按名字取一个大脑的**唯一**入口——开会话快照、`config_for`（组 `BrainConfig`、装 key）、列模型都经它。另管：列模型（带 key 调引擎、三档排序、失败归类成写死的 `code`）、存 / 恢复选择（`set_model` / `reset_model`；自定义端点改它自己那一行） | 存储 key；窗口折算（在 `context_window`） |
| `services/context_window` | **纯逻辑深模块**：模型名 + 存下的窗口与来路 → `Window(chars, source)`，来路 ∈ vendor / manual / builtin / estimate。⑧屏列表、书籍详情「装得下吗」、开会话快照三处只调它 | 数据库、网络；折算比例本身（在引擎 `budget_from_context_tokens`） |
| `services/chapter_selector` | **纯逻辑深模块**：章节 + 问题 + 预算 + 锁定 → 一份选章决策与一本可查的账 | 数据库、模型 API |
| `services/citations` | **纯逻辑深模块**：回答 + 几节正文 → 引文落在哪一节哪一块；对不上就说对不上，不猜 | 数据库、模型 API |
| `services/relocate` | **纯逻辑深模块**（CitationRelocator）：旧引文 + 新章节 → 新落点 + 一份命中率报告 | 数据库、模型 API |
| `services/sources` | 章节文件 → 可比对的块。**定位与重定位共用这一份切法**，否则命中率测的是两套算法的差 | 业务判断 |
| `services/prompt_store` | 人格提示词的读写。**唯一一处往 `prompts/` 写字的地方**：占位符体检、原子替换、出厂基线 | 提示词内容本身 |
| `services/resettle` | 换章节连同它的善后，对外只有 `swap(session, *, book_id, records, root) -> Swapped(chapters, relocation)`：换章节**之前**经 `turn_state.forget_locks` 清掉会话的选章锁定（先锁会话行，与一轮收尾同序）并拍下引文当下的落点 → `replace_chapters` → 按 `root` 下的**新正文**把引文重新定位（`root` 由调用方给：解析时是暂存目录）。**次序只在 `swap()` 里写一次**，`tests/test_book_lifecycle.py::test_换章节之前已经握着会话行` 钉着生产代码里的这份次序。不提交 | 解析本身；什么时候换（在 `book_lifecycle` 的成功事务里） |
| `services/moderation` | 内容审核台账：交集 − 并集，把触发章节收窄到一节 | 判断某次失败是不是审核（那在引擎的 `BrainError.kind`） |
| `services/citation_service` / `retrieval` 等 | 读取侧：把上面几个纯逻辑模块的产物交给界面 | 写入（写入跟着回答一起落库） |
| `repositories/*` | CRUD + 查询，返回 ORM 实体或 dict；**每个查询强制带 `user_id`** | 业务规则、跨表事务编排 |
| `repositories/conversation_repo` | 别的子系统要动会话时那几笔。`clear_locks_for_book()`（唯一调用方是 `turn_state.forget_locks`）是 `user_id` 规则的一处**有理由的例外**——它跑在解析任务里，那时没有「当前用户」，而 `book_id` 已经把范围钉死 | 会话本身的读写（`chat_service` 手里就有整行，不绕这一层） |

### 这一轮对引擎做的 13 处改动

全部**签名向后兼容，CLI 那条路零影响**——新增参数一律带默认值、新增属性一律有默认值。逐条的理由与实测在 `Todo/Plans/booksoul-web-plan.md` 各片完成记录里。

| # | 模块 | 改动 | 为什么 Web 层需要它 |
|---|------|------|-------------------|
| 1 | `writer.py` | `write_book(*, dir_name="")` | 目录名要按 `book_id` 组，不能让书名决定路径 |
| 2 | `library.py` | `load_from_path(path, *, only_idx=None)` | 多用户书库里书名不唯一，模糊匹配走不通；调用方手里本来就有确切路径 |
| 3 | `parsers/` | `on_progress` 回调 | ①屏那条五阶段进度条 |
| 4 | `model.py` | `Book.stats` | ④屏质检账本要「真数出来的」那几项 |
| 5 | `ask.py` | `_budget_chars` → 公开的 `context_budget_chars` | ⑧屏要在提问之前就回答「这本书装不装得进这个模型」 |
| 6 | `config.py` | 各模型的上下文窗口 | 同上 |
| 7 | `config.py` | `BrainConfig.api_key` → `field(repr=False)` | 一次 `repr(cfg)` 就能把 key 抄进任何一条日志 |
| 8 | `brain.py` | 截断检测：流结束但既无 `[DONE]` 也无 `finish_reason` 就抛错 | 一段截断的回答读起来和完整回答一模一样 |
| 9 | `retrieve.py` | `BM25.terms_for()` / `weight()` / `plan_selection()` | ⑥屏那张排名表和模型真正收到的东西，必须是同一次计算的两面 |
| 10 | `retrieve.py` | `always_include` 装不下时不再硬塞 | 预算小到连序言都放不下时，硬塞会把用户问的那一章整个挤掉，且全程不报错 |
| 11 | `ask.py` | `Session.preselected_idx` | 选章已经在外面做过并落了账，本地不能再挑第二次 |
| 12 | `model.py` / `library.py` | `Chapter.stated_words` | `Book.words` 随载入方式而变，会让人格提示词里那句「篇幅 N 字」变动，**整本书的前缀缓存作废** |
| 13 | `brain.py` | `BrainError.kind` + `_kind_of_http()` | 引擎本来就判得出是不是内容审核，但把结论揉进中文里就扔了；上层只能去匹配文案 |

### brain-model-picker 对引擎的 4 处改动

同样**全部向后兼容，CLI 行为不变**（`context_budget_chars` 对既有输入逐一不变，有 16 项回归表钉着）。理由与实测在 `Todo/Plans/brain-model-picker-plan.md` 各片完成记录里。

| # | 模块 | 改动 | 为什么 Web 层需要它 |
|---|------|------|-------------------|
| 14 | `ask.py` | `known_context_chars(model) -> int \| None`；`context_budget_chars` 改为建在它之上 | 界面要分开说「内置表查到的」和「认不出、按默认值估的」；表仍只有一张 |
| 15 | `brain.py` | `ModelEntry(id, context_tokens)` + `list_models(cfg)`；两个后端各有 `models()`（OpenAI 兼容走 `GET {base_url}/models`，与 `chat` 共用 `_url()` / `_headers()`；Anthropic 走 SDK `models.list`，窗口取 `max_input_tokens`），超时 10 秒 | ⑧屏「换模型」的下拉 |
| 16 | `brain.py` | 列模型的失败归类 `_list_kind()` / `_list_failure()`：`auth` / `unsupported` / `timeout` / `network` / `rate_limit` / `server`；**不读错误体、不带原因短语、`from None`** | 前端按类别给出路；按用户存的地址发请求，回显原文就是窥探内网的口子 |
| 17 | `brain.py` | `BrainError.kind` 新值 `dependency`（缺 Anthropic SDK） | 那是管理员的事：网页给用户「先换一个大脑」，`pip install` 进 ERROR 日志 |

### book-lifecycle 对引擎的 1 处改动

同样**向后兼容，CLI 行为不变**（CLI 解析完进程随即退出，句柄本来就会被收回）。理由与实测在 `Todo/Plans/book-lifecycle-plan.md` 切片 3 完成记录里。

| # | 模块 | 改动 | 为什么 Web 层需要它 |
|---|------|------|-------------------|
| 18 | `parsers/epub.py` | `EpubParser.close()`：关掉原件的 zip 句柄；构造与 `parse()` 半路失败时当场关。`importer.import_file` 写完盘（成功或失败）在 `finally` 里调它 | 句柄原先要等垃圾回收才放（解析器与产出之间有引用环）；Windows 上在那之前，重新解析、删书都动不了那份原件 |

**引擎与 API 的边界是磁盘和内存对象，不是网络。** `book_lifecycle` 经 `importer.import_file()` 调 `booksoul.parsers.parse()` 得到 `Book`、调 `booksoul.writer.write_book()` 写进暂存目录，再把返回的摘要写进数据库、提交之后把暂存目录换上去；`chat_service` 调 `booksoul.ask.open_session()` 得到 `Session`，逐 token 转成 SSE。

## API 端点

统一前缀 `/api`。除注册/登录外均需 `Authorization: Bearer <access_token>`。

### 认证

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/auth/register` | 注册，返回 token 对 |
| POST | `/api/auth/login` | 登录，返回 token 对 |
| POST | `/api/auth/refresh` | 用 refresh_token 换新 access_token |
| GET | `/api/auth/me` | 当前用户 |

### 书库与解析

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/books` | 当前用户的书库列表（③屏）。分页 `?page=&size=`（按状态、按书名筛选未实现） |
| POST | `/api/books` | 上传书籍文件（multipart），创建 book + parse_job，**202** 返回 `{bookId, jobId}`。三道检查都在任何一次写库之前：认得扩展名、这个部署却没装后端 → **415** `unsupported_format`（不建行、不存原件；装包命令只进 ERROR 日志）；超限 413；空文件 400 |
| GET | `/api/books/{bookId}` | 详情：元信息 + 章节树 + 字数 + parse_notes（④屏）。另带 `conversationCount`（重解析前那句「有 N 个会话会受影响」）、`moderation`（被内容审核拦过的账，没被拦过是 `null`）、`chapters[].blocked`（该节被审核点名，**只在嫌疑收窄到一节时为真**） |
| DELETE | `/api/books/{bookId}` | 删除书与其磁盘目录。**本轮未实现** —— 删除不在 S1–S13 任何一片的验收里。真做时注意：要同时删 `library/` 与 `uploads/` 两处，只删一处会留下孤儿文件（S3 记录） |
| POST | `/api/books/{bookId}/reparse` | 用原始上传文件重解析，复用同一 `book_id`（书名没变时目录也不变）；**202** 返回 `{bookId, jobId, affectedConversations}`。正在解析 → **409** `book_parsing`（「不在解析中才改成解析中」是一句 UPDATE，两个几乎同时的请求只有一个开跑）；原件不在 → **409** `source_missing`，一行都不写、书照旧可读可问；别人的 / 不存在 404 |
| GET | `/api/books/{bookId}/state` | 这本书此刻的状态，一条查询：`{bookId, status, published, jobId, stage, done, total, errorCode, message, remedy}`。`published` = 有没有上一次成功的那一版（重解析中照读它）；`message` 按 `errorCode` 从码表取，在跑或成功时为空串。书籍详情解析中每一拍只问它；别人的 / 不存在 404 |
| GET | `/api/books/{bookId}/report` | 解析报告：清洗账本 + 产出文件清单（②屏） |
| GET | `/api/books/{bookId}/chapters/{idx}` | 单章正文 Markdown（⑤屏）。`sourcePath` 从书库根的目录名起算、一律正斜杠（`library/<user>/<book>-<书名>/chapters/…`），照着它能在宿主机的书库目录下找到那个文件。重解析中、重解析失败之后读的是上一次成功的那一版；首次解析中 / 首次失败 404 |
| GET | `/api/books/{bookId}/images/{name}` | 章节里引的一张图（⑤屏，正文里写作 `![](images/<name>)`）。只端书目录 `images/` 下那一层的位图（jpg / png / gif / webp / bmp / avif），带 `X-Content-Type-Options: nosniff` 与 `Cache-Control: private`；SVG、带目录的名字、不存在、别人的书一律 404。要登录——`<img src>` 带不上 token，前端取成 blob 再显示 |
| PATCH | `/api/books/{bookId}/notes/{noteId}` | 把存疑项标记为已核对 |
| GET | `/api/jobs/{jobId}` | 解析进度轮询兜底。`errorMessage` 按 `errorCode` 从码表取，不读库里那一列（旧行里可能还留着引擎原话） |
| GET | `/api/jobs/{jobId}/events` | **SSE** 解析进度实时推送（①屏进度条）。有变化才推 `stage`；结局推一次：`done`，或带 `errorCode / remedy / bookId` 的 `error`；超过 `job_stream_max_seconds`（环境变量 `BOOKSOUL_JOB_STREAM_MAX_SECONDS`，默认 600 秒）仍无结局就静默收尾（流结束、不发结局事件），前端接着轮询上一行；推送自己出事时的 `error` 不带 `errorCode`。别人的 / 不存在在开流之前就 404（JSON，不是一条空流） |
| GET | `/api/formats` | 运行时探测：这个部署支持哪几种格式、上限多少 MB（①屏那张格式表由它驱动，不写死） |

### 对话

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/conversations` | 会话列表，按最后一次提问排（③屏「最近问过的」）。带 `?bookId=` 只列这本书的（④屏「这本书的会话」：接着上次聊从这里进，「开始对话」每次开新的） |
| POST | `/api/conversations` | **SSE** 开会话，**连同第一问**：`{bookId, persona, brainName, budgetChars, reflection, question, compare}`，key 经 `X-Brain-Key`。回 **201** + `Location: /api/conversations/{id}`（头在第一个字之前到；CORS 已 expose），事件同 `/ask`。会话与第一问一个事务：没有 `question` → 422、没填 key → 400、书在解析 / 停下了 → 409（带 `code` `book_parsing` / `book_failed`）、人格不对 → 400，都**不建会话**。没有「只建一个空会话」的入口 |
| GET | `/api/conversations/{id}` | 会话详情 + 历史消息 |
| DELETE | `/api/conversations/{id}` | 删除会话，连同问答、引文、选章留档（外键级联），204。**正在回答 → 409**：收尾要往这个会话里写回答，删在它前面，付过钱的回答就无处可落；别人的 / 不存在 → 404 |
| PATCH | `/api/conversations/{id}` | 两个可选旋钮，**没给的那个不动**：`persona`（「跟这个继续聊」，旁支一条不删）、`reflection`（处境模式）。`reflection=false` 是「关掉」，和「没提这件事」用 `null` 区分 |
| POST | `/api/conversations/{id}/ask` | **SSE** 提问并流式返回（⑦屏）。入参 `{question, persona?, compare?, reselect?}`。`reselect=false` 时问到未载入章节会被 409 + `code=reselect_required` 拦下（**还没花钱**）。同一个会话正在答**同一个**问题（断线重连、两个标签页）→ 挂上去回放，**不再调模型**；正在答**另一个**问题 → 409。书在解析中 / 上一次解析停下了 → 409（`code` 为 `book_parsing` / `book_failed`，措辞来自 `book_lifecycle` 的门表） |
| GET | `/api/conversations/{id}/retrieval` | 这个会话**最近一次真正做过的**选章决策（⑥屏）。挂在会话上而不是某条回答上：一轮里三种人格各答一次，却只选一次章 |
| GET | `/api/messages/{messageId}/citations` | 这条回答引了哪章哪段（⑤屏溯源）。`MessageOut.citations` 也随会话详情一起给，免得一屏几十条回答各问一次 |
| GET | `/api/citations/{id}` | 单条引文：⑤屏那条来路条要的「跳过来之前问的是什么、属于哪个会话」 |

### 大脑与人格

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/brains` | 可用大脑：内置预设（已合并你的模型选择）+ 自定义端点。每项有 provider、`model`（实际在用的）、`defaultModel`（预设本来的；自定义端点为空）、`modelChosen`、`contextChars`（正文额度）、`contextTokens`（存下的窗口，内置 / 估计时为 0）、`contextSource`（vendor / manual / builtin / estimate）、需要哪个 key（⑧屏）。带 `?bookId=` 多给 `fits` / `fitNote`；**窗口是估计的那一行 `fits` 为 `null`**、不下结论。书籍详情「装得进哪个大脑」直接读这一份 |
| GET | `/api/brains/{name}/models` | 这个大脑在这个 key 下有哪些模型。key 经 `X-Brain-Key`（本机端点不需要）。响应 `{items: [{id, contextTokens}], source}`，三档排序（对话类 → 看不出来的 → 明显不是对话的），**只有 id 与窗口**。需要 key 而没带 → 400「先填 key」且不发请求；没有 / 别人的 → 404；失败 → 502 带 `code` ∈ `brain_auth` / `brain_unsupported` / `brain_timeout` / `brain_network`（限流与 5xx 归 `brain_network`），**不含对方原文**；服务器缺 SDK → 502 无 `code` |
| PUT | `/api/brains/{name}/model` | 换模型并存下：`{model, contextTokens, contextSource}`。内置预设写 `brain_choices`；自定义端点改它自己那一行。**只影响以后开的会话**。校验失败 422（说清限制，不回显值）。返回更新后的那一行 |
| DELETE | `/api/brains/{name}/model` | 恢复默认（仅内置预设），204，没选过也 204；自定义端点 400「没有默认模型」 |
| GET | `/api/personas` | 三种人格的 key / 名称 / 描述 |
| GET | `/api/personas/{key}` | 人格提示词原文 |
| POST | `/api/brains` / DELETE `/api/brains/{id}` | 用户自加的 OpenAI 兼容端点（⑧屏左半）。**新建表单仍手填模型名**；存下来之后再用上面的 PUT 换 |
| PUT | `/api/personas/{key}` | 覆写人格提示词。占位符拼错在这里就被 **422** 拦下并点名那个占位符——不拦的话它会在下一次提问时炸在后台任务里 |
| POST | `/api/personas/{key}/restore` | 还原到**第一次从界面改动之前**那一版（`prompts/.baseline/`）。`prompts/` 不在版本控制里，覆写必须留退路 |

> **SSE 事件（`/ask`）**：`stage`（「正在挑章节…」「挑好了…」；只有一个位置，重连只回放最后一句）· `notice`（`{code, text}`，这一轮有什么不一样，如 `reparsed`「这本书重新解析过了…这次按全价算」；存成列表，回放时排在一切之前）· `token`（带 `persona`）· `answered` / `failed`（**某一路**的结局，当场发，带 `persona`）· `done` / `error`（**整轮**的结局，**落库之后**才发）。
> 两层不能合成一层：客户端收到 `done` 的第一反应是回头拉一次会话，合并之后那一刻数据还没进库。

## 核心业务流程

**上传解析**：`POST /api/books`（multipart）→ `BookLifecycle.upload()`：认得扩展名、这个部署却没装后端 → 415（不建行、不存原件）→ 流式落临时盘（超限 413、空文件 400）→ 建 `books`(status=parsing) 与 `parse_jobs` → 原件搬进 `<上传根>/<user_id>/<book_id>/` → 提交（提交失败就删掉已搬过去的原件）→ **提交之后同步** `create_task` 排上后台解析 → 202。后台任务自开 session，`importer.import_file()` 在工作线程里跑：`parsers.detect_format()` 按魔数判格式（这个部署解不了 → `unsupported_format`）→ `parsers.parse()` → `clean.tidy` / `footnotes.normalize` / `clean.drop_duplicate_headings` → `writer.write_book()` 写进**暂存目录** `<书库根>/.staging/job-<id>/`。线程只写三个进度标量，事件循环里的进度泵定期落库、经 SSE 推给前端；泵某一拍写库失败只记 WARNING，**不决定结局**。成功：一个事务写书目（超列宽的截断加「…」）、`resettle.swap()` 换章节、`parse_notes`、账本（`Ledger`）、任务终局 → 提交 → `_publish()` 把暂存目录换成 `{user_id}/{book_id}-{书名}`（旧的一版先挪进 `.retired` 再删，书名变了旧目录一并收掉；这一步失败留给启动清扫补完）。失败：删暂存，记 `error_code`；`error_message` 只取码表那句，`remedy` 给用户自己能做的一步——抽不出文字的扫描版 PDF 给带真文件名的 OCR 命令，**不写出空 md**；引擎原话连同回溯只进 ERROR 日志（带书 id、任务 id）。**每条结局都落在 ready 或 failed**：引擎失败、引擎之外的意外（`internal`）、写库那一半出事（撤回、重新取行、记 `internal`——原先会卡在 `parsing`，提问永远 409、也不许重新解析）、进程重启（启动清扫记 `orphaned`）。

**提问**：`POST /api/conversations/{id}/ask` → 从 header 取本次请求的模型 key → **在建响应之前**跑完所有会抛错的检查，让它们变成规规矩矩的 4xx：`ChatService.ask()` 鉴权、看书能不能问（过门表：解析中 / 停下了 → 409 带 `code`）、组 `BrainConfig`（没填 key → 400）、读章节、算预算，得到只读的 `Draft` → `turn_state.admit(session, draft)`：同步占住这个会话（同一个问题正在答 → 挂上去回放；另一个问题 → 409）→ 占位之后先锁会话行，再读锁定 / 最近留档 / 轮号 / seq 与历史 → 逃生门（问到了没载入的章节 → 409 `reselect_required`）→ 写提问并提交 → 判出锁定被弄没了就发 `notice: reparsed` → 开 `StreamRecorder`，把 `TurnRunner.run` 交给一个**不跟着响应走**的后台任务 → `chapter_selector.choose()` 选章（复用锁定的轮次不重选）→ `_must_have_content()` 断言模型手里真有正文，**0 节就地停住不往模型送** → 一到三路并发调模型（对读三路共享整本书的缓存）→ token 边到边经 SSE 推给前端 → recorder 退出时把 `Outcome` 交给 `turn_state` 的收尾：**一个事务里**先锁会话行、发号、把已不存在的章节 id 置空，落库 `messages` / `citations` / `retrievals` / `retrieval_items` **与会话的选章锁定**，提交之后让出席位，然后才发 `done`。

> **开会话走的是同一条路。** `POST /api/conversations` → `ChatService.begin()`：校验书与人格、快照大脑（`_open`，与 `start` 共用），把会话那一行 flush 出 id 但**不提交** → 当作这个会话的第一问交给 `ask()`，上面每一步照走 → `turn_state.admit` 写下第一问时那次提交把会话一起提交。之前任何一处抛出，请求那条 session 一关，这一行就回滚了——所以没有「会话建了、问题没问出去」的空会话。路由回 201 + `Location`，客户端据此在流还在走时换到新会话的地址。
>
> **`ChatService.ask()` 是普通协程，不是异步生成器。** 写成生成器，上面那些检查要等到第一次迭代才跑，而那时 `200 text/event-stream` 的响应头已经发出去了——用户看到的是一条莫名断掉的空流，不是一句「到设置页填 key」。`turn_state.admit()` 同理。

> **一个会话同一时刻只有一轮。** 同一个问题付两次钱不能接受（Spec #39），而原先「有没有一轮在跑」的检查与登记之间隔着七八次查库和一次提交（两个标签页同时问都能穿过去），那一轮又在落库**之前**就把自己摘掉了（落库那几秒里重连，又是一轮新的）。现在占位是 `admit` 的第一件事、查与占之间没有 await；让位在收尾提交之后、`done` 之前，而且认人；任务还没跑起来就被取消（服务关停），由任务的完成回调让位。席位在进程内，单实例前提见待确认 #2。删会话也占同一个席位（`forget_conversation`），规矩相同：有一轮在跑就 409。另有一条缝：提问先查会话、隔着几次 await 才占位，这当中会话可以被删——锁会话行时锁不到，回 404 而不是后面某条 `.one()` 抛出来的 500。
>
> **轮中重解析**：这一轮挑章、读正文在重解析之前，落库在之后，手里的 chapter id 可能已经不存在了。收尾时拿到会话行锁之后再查，不在的置空（与 `ON DELETE SET NULL` 同效），锁定只写活着的那部分——回答照样落下，不因为撞外键丢掉。两个事务碰会话行与章节行的顺序一致（先会话、后章节），只会排队、不会互等。

> 那一个事务是这一轮的**全部**落库：五样东西同时存在或同时不存在。选章留档原先在调模型**之前**另开一个事务写，崩在中间会留下一行孤立的 `retrievals`——一份配不上任何回答的「凭什么是这几节」，而锁定已经落下、下一轮照着它走。代价是⑥屏 `GET /api/conversations/{id}/retrieval` 要等这一轮结束才查得到；该端点的语义本来就是「这个会话**最近一次真正做过的**选章决策」。
>
> 落库失败时整轮的结局信号是 `error` 而不是 `done`：宣布 `done` 而库里什么都没有，客户端会立刻回头拉一次，看到一个不存在的空洞。审核台账 `moderation_events` **刻意不在这个事务里**——它故意吞异常（台账记不上是遗憾，把一条已经说完的回答变成错误不是），并进来会让一次台账写失败回滚掉整轮。

> 用户关掉页面、网络断了，后台任务照样跑完——那次调用的钱已经花出去了，把结果写下来才对得起它。断流留下的残文落 `complete=false`。

> **提问的模型与预算都取会话自己的快照。** 真正调模型时用 `conversations.model` 覆盖 `config_for` 给的当下配置（`dataclasses.replace`，不改那份被多处读的配置）；预算三级，前一级有就不看后一级：`budget_chars`（用户手调）> `context_chars`（开会话时快照的正文额度）> 按模型名查引擎内置表（`context_chars = 0` 的历史会话走这条，逐字不变）。于是会话里显示的、算预算用的、真正调用的永远是同一个模型——换了模型选择，已开的会话不受影响。

**换模型**：⑧屏点模型名 → 展开 `ModelPicker` →「列出模型」（或保存 key 后自动）→ `GET /api/brains/{name}/models` → `brain_service.list_models()` 经 `describe` 取配置、在工作线程里调引擎 `list_models()`、三档排序、只回 id 与窗口 → 用户点一个或手填 →「用这个」→ `PUT /api/brains/{name}/model` → 内置预设 upsert `brain_choices` / 自定义端点改 `brains` 那一行 → 之后 `describe` 读到的就是新模型：⑧屏、书籍详情、**新开的会话**（`ChatService.begin` 经 `_open` 把 `model` 与 `context_chars` 一起快照）立刻按它说话。

**溯源**：回答里的引文在落库时与**这一轮真载入的那几节的原始文件**比对（`services/sources` 切块 → `services/citations.locate()`），定位到块序号存进 `citations`；对不上填 `para_index=-1`，**不猜**。前端据此跳 `/books/:id/read/:idx?cite=<id>` 并高亮（⑤屏）。

**重解析**：`POST /api/books/{id}/reparse` → `BookLifecycle.reparse()`：先查原件（不在 → 409 `source_missing`，一行都不写）→ 比较交换把书置 `parsing`（`UPDATE … WHERE status <> 'parsing'`，拿不到行 → 409 `book_parsing`）→ 数受影响会话、建任务，同一个事务提交 → 排上后台解析，之后与上传同一条路。**解析中照读旧版**：`storage_path` 仍指着上一次成功的那一版、新正文写在暂存目录里，所以阅读照常，这期间 `ask` 一律 409 `book_parsing`；失败之后同样照读旧版，`ask` 409 `book_failed`。解析成功那一刻、在成功事务里做善后（`resettle.swap()`）：**先**清掉该书所有会话的选章锁定（经 `turn_state.forget_locks`，先锁会话行，与一轮收尾同序）并拍下引文当下的落点（旧章节一删，`ON DELETE SET NULL` 就把它抹了），**再** `replace_chapters`，**最后**按暂存目录里的新正文把引文重新定位。找不到的降级成「原位置已变更」，不猜。重定位的账写进 `parse_jobs.cleanup_stats`（只在真有引文要搬家时，见 `Ledger.moved()`），②屏账本上显示一行。

> 下一轮怎么认出「锁定是被清掉的」：`selected_chapter_ids == []` 有两种来路——整本装得下的书本来就锁空的，和这里被清掉的。判据在 `turn_state`（`_lock_was_wiped`）：去看最近一次**真做过决策**的那一轮留的档，留的是「挑了章节」而现在锁定为空，才是被清掉的。靠「问过好几轮却没有锁定」反推会把两件事压成一个值。另一种「被弄没」是锁定非空、里面却一个活的章节都没有（锁定落下之后章节被换掉）——同样重挑、同样告示；只死了一部分就照活着的走。认出来之后发 `notice`（`code=reparsed`），前端在对话页顶上画一条横幅。

**能不能读 / 问 / 重解析**：一张门表（`book_lifecycle._GATE`）说了算——`(状态, 有没有已发布的一版)` × 用途 → 放行 / 404 / 409 + `code`，措辞只写在这一处。阅读、对话、重解析经 `require()` 过门；书库卡片、书籍详情、`GET /api/books/{id}/state` 读的 `BookState` 用 `allows()` 查同一张表。首次解析中 / 首次失败：读正文 404、问 409；重解析中 / 重解析失败之后：读的是上一次成功的那一版，问 409；ready：都放行。「有已发布的一版」看 `storage_path`（`ck_books_ready_has_path` 保证 ready 一定有它）。

**启动清扫**（`main.lifespan` → `sweep_orphans()`）：单实例判据——进程刚起来，没收尾的任务都已没人在跑：任务记 `orphaned`（话从码表来），`parsing` 的书记 `failed`（有已发布一版的照读旧版）。库这一半提交之后才对账磁盘：`.staging` 下已成功提交、还没换上去的那一版照库里的 `storage_path` 补完发布（roll-forward，记 WARNING），其余删掉；`.retired` 清空。清扫失败照抛，由 lifespan 那层接住，不拦着服务起来。

**内容审核**：模型后端因内容审核拒绝时，引擎把 `BrainError.kind` 置为 `moderation` → 服务层记一笔台账（这一次送了哪几节）并把**网页版的出路**发给这一路的 `failed`。往后每被拦一次取交集、每成功一次做减法，范围自己收窄；收窄到一节就在④屏目录树上给那一节挂重墨。

## 认证授权

- **用户认证**：JWT。access_token 短期（30 分钟），refresh_token 长期（14 天）。密码用 `bcrypt` 哈希。〔来源：FastAPI 社区基线〕
- **资源授权**：所有 `books` / `conversations` 查询强制带 `user_id` 条件，在 `repositories` 层拼进 WHERE，不依赖上层记得过滤。越权一律返回 404 而非 403，不泄露资源是否存在。〔来源：OWASP 基线〕
- **模型 API key —— 本项目的硬规则**：

  > key **只存在用户浏览器的 localStorage**，每次调用模型的请求由前端放在 `X-Brain-Key` 请求头里带上（目前三处：开会话（连同第一问）、提问、⑧屏「列出模型」）。后端**用完即丢**：不写数据库、不写文件、不写日志、不进异常上报、不出现在任何响应体里。`brains` 表只存 `api_key_env` 这类**名字**，不存值。
  >
  > 这是现有引擎设计的延伸——`config.py` 模块 docstring 明写「api key 不进配置文件，配置里写的是环境变量名」。多用户场景下环境变量这条路走不通，等价替代是「服务端不持有」。
  >
  > 推论：**HTTPS 是硬前提**；日志中间件必须显式剔除 `X-Brain-Key`；异常处理器不得回显请求头。
  > 〔来源：`src/booksoul/config.py` 模块 docstring + `CLAUDE.md`「api key 不进配置文件」+ 用户指定〕
- **持有密钥的 dataclass 字段必须 `repr=False`。** 一次 `repr(cfg)` 就能把 key 抄进任何一条日志、任何一次异常回溯。〔来源：Plan S6 规范候选 #13，用户 2026-09-20 裁定；实现见 `config.py:BrainConfig.api_key`〕
- **存在数据库里的路径，必须 `resolve()` 之后校验仍在预期根目录内，才能拼进文件操作**；越界时不回显那条路径。拼接前检查字符串里有没有 `..` 不算数——绕过方式太多，而符号链接在字符串上根本看不出来。这条规则只允许有一份实现（`core/paths.under()`），第二处要用就 import。〔来源：Plan S5 规范候选 #10 + S9 #28，用户 2026-09-20 裁定〕

## 设计模式与约定（后端代码规范）

> 本章节约束"代码怎么写"，与上文架构章节的"做什么、边界在哪"正交。每条已填规范末尾以 `〔来源：…〕` 注明依据：配置文件路径 / 代表性源码路径（抽样 n/m）/ 决策产物路径（Grill、Plan、RFC）/ 手工约定。没有来源的条目视为待核实。
> 逆向模式只写本项目代码与配置的证据，无证据处保留占位词，禁止用社区惯例填充。多种写法并存的项记入「待确认问题」待裁决。

### 架构级模式

- 引擎层（`booksoul/`）**不依赖任何 Web 框架**，可独立作为库与 CLI 使用；新增 HTTP 层不得反向污染引擎。〔来源：`src/booksoul/` 21 个模块无一 import web 框架，抽样 21/21〕
- 数据结构一律用 `@dataclass`，不用裸 dict 或 NamedTuple 传递领域对象。〔来源：`model.py` `Chapter`/`BookMeta`/`Book`、`config.py` `BrainConfig`、`brain.py` `Usage`/`Reply`、`ask.py` `Session`、`persona.py` `Persona`，抽样 5/5 个含数据结构的模块〕
- 可选依赖延迟导入：格式解析器在分发函数内部 `import`，缺依赖时给出 `pip install` 提示而非启动即崩。〔来源：`parsers/__init__.py:parse()` 每个分支内 import；`brain.py:AnthropicBrain.__init__` try-import〕
- 配置分层覆盖，后者覆盖前者：内置预设 → 用户级配置文件 → 项目配置文件 → 环境变量 → 调用参数。〔来源：`config.py` 模块 docstring 与 `resolve()`〕
- 依赖注入：HTTP 层用 FastAPI `Depends`（DB 会话、当前用户、大脑配置），不用全局单例。〔来源：FastAPI 社区基线〕
- **同一条规则只许有一份实现。** 第二处要用就 import，不许抄——抄错的那一份不会报错。两处实测：路径拼接的边界校验收在 `core/paths.under()`（阅读页与溯源共用）；引文定位与重解析后的重定位共用 `services/citations.locate()` 与 `services/sources.read_sources()`。后者尤其要紧——各写一份的话，「重定位命中率」测出来的就成了两套算法的差，而那个数字是一条人工裁决的依据。〔来源：Plan S9 规范候选 #28 + S12 #37，用户 2026-09-20 裁定〕
- **成功率 / 命中率一类的报告，要分得清「成功」和「真的做过事」。** 输入没变时成功率必然 100%，而那个数字什么都没证明——它连一次真正的处理都没做过。报告必须同时给出「实际发生了变化的条数」。〔来源：Plan S12 规范候选 #38，用户 2026-09-20 裁定；实现见 `services/relocate.py:Report` 的 `shifted` / `unchanged` 两列〕
- **诊断先榨干已有的账，别为了定位再去多花一次调用。** 内容审核不说是哪一节触发的，但「每次送了哪几节、结果是拦还是通」是我们自己已经有的数据；交集减并集就能把范围收窄到一节，一分钱不用多花。〔来源：Plan S13 规范候选 #46，用户 2026-09-20 裁定；实现见 `services/moderation.py`〕
- **消费一次性资源（流、游标）的函数，要把「取值」和「判断」拆开。** `HTTPError.read()` 只能读一次，揉在一个函数里就没法把同一份数据喂给第二个判断。拆成 `_detail_of()` 取值、`_kind_of_http()` 判类别、`_http_error_message()` 组人话。〔来源：Plan S13 规范候选 #44，用户 2026-09-20 裁定〕

- **值对象按「谁算出来的」分组，不按「字段看起来像一类」。** 问「它是谁算出来的」有唯一答案，问「它像哪一类」每个人的答案都不一样——于是那组对象会慢慢退化回一堆扁平字段，而它本来就是那样开始的。实测：`_Turn` 的 18 个扁平字段按来源收成 `BookRef`（读书与读章节）/ `Budget`（预算与锁定）/ `BrainRef`（大脑配置）三个，剩下的才是这一轮自身的性质。〔来源：Plan turn-runner 切片 6，用户 2026-09-21 裁定〕
- **端口的测试适配器与生产适配器，错误约定必须逐条对齐，并且有一条 parity 测试钉着。**差一条，测试证明的就是另一套东西——而测试是绿的，偏差从来不会自己报出来。实测（当时的那一对；`UnitOfWork` 端口与 `Attached` 已随 turn-state RFC 撤掉——没有生产代码注入它，教训仍在）：`Detached`（生产）与 `Attached`（测试，走 SAVEPOINT）两边都漏了「提交那一刻炸的失败」，是 parity 测试把第二处抓出来的。〔来源：Plan turn-runner 切片 2，用户 2026-09-21 裁定；例子按 Todo/Plans/turn-state-plan.md Slice 7 修订，用户 2026-09-23 裁定〕
- **测次序的测试，要驱动生产代码里的那一份次序，不许在测试里把步骤自己排一遍再测。** 抄本排对了只说明抄本对：生产代码里的两步对调，测试照样绿。实测：锁序测试原先在测试里内联了「清锁定 → 换章节 → 重定位」，把生产代码里前两步对调之后它仍然全绿；改成调 `resettle.swap()` 本身、趁它卡在删章节上去抢会话行锁，一对调就当场变红。适用于一切次序敏感的规则（锁序、先读后删、先落库后宣布）；写完做一次变异——把生产代码的次序改错，看它红不红。〔来源：Todo/Plans/book-lifecycle-plan.md Slice 1，用户 2026-09-25 裁定；`tests/test_book_lifecycle.py::test_换章节之前已经握着会话行` 是第一例〕

- **间接依赖也要显式声明，哪怕开发机恰好装着。** 应用连库走 `asyncpg`，但 alembic 迁移走同步的 `psycopg2`（`core/config.py:sync_database_url` 把 `+asyncpg` 换成 `+psycopg2`）——而它在 `pyproject.toml` 与 `requirements.txt` 里**哪儿都没声明**，一直没暴露只因为开发机的 Anaconda 自带。干净环境里 `alembic upgrade head` 会当场失败，而那时人已经在一个陌生的机器上了。判据很简单：**代码里 import 到的、或配置串里点名的，都要能在依赖清单里找到。**〔来源：2026-09-21 docker compose 交付，read-design-before-code 实测，用户裁定〕


### 代码格式化

- 4 空格缩进，无 Tab。〔来源：全项目 938 处缩进行，Tab 0 处，抽样 21/21〕
- 字符串统一双引号。〔来源：双引号 1719 处 / 单引号 31 处 = 98.2%〕
- 行宽 88。〔来源：P99 = 86 字符，>88 仅 25 行，>100 仅 2 行 = 99.4% 合规，与 black/ruff 默认值一致〕
- 每个模块首行是中文 docstring，讲**为什么这样做**而非罗列做了什么。〔来源：抽样 21/21；典型如 `retrieve.py` 开篇解释「能整本就整本」、`writer.py` 解释「为什么既要 book.md 又要 chapters/」〕
- 文件顶部 `from __future__ import annotations`。〔来源：抽样 21/21 = 100%〕
- 类型注解用现代写法（`list[dict]`、`str | None`、`Callable[[str], None] | None`），不用 `typing.List` / `Optional`。〔来源：`model.py:59`、`ask.py:96`、`brain.py:107`，配合 future import，抽样 21/21〕
- 格式化 / 静态检查工具：**ruff 全仓统一**（`ruff format` + `ruff check`），配置在 `pyproject.toml [tool.ruff]`。忽略 `E501`（行宽由 formatter 保证，中文注释里的 URL 不值得为它折行）、`B008`（FastAPI 的 `Depends()` 本来就写在默认值位置）、`SIM105`（`contextlib.suppress` 在这里不如 `try/except: pass` 直白）。上面那几条格式规范由它强制，不再靠人记。〔来源：Plan S1 规范候选 #1（Grill R2 裁决），用户 2026-09-20 确认既成事实〕
- **configparser 读的配置文件保持纯 ASCII**（`alembic.ini`），中文说明写进对应的 `.py`。〔来源：Plan S1 规范候选 #5，用户 2026-09-20 确认既成事实〕

### 分层职责

- `routers` 只做参数绑定、调 service、包装响应，不含业务判断、不直接访问 DB。〔来源：FastAPI 社区基线〕
- `services` 是唯一允许 `import booksoul` 的层；跨表事务编排在此完成。〔来源：本文档「模块边界」决策〕
- `repositories` 只做数据存取，每个查询强制带 `user_id` 条件。〔来源：本文档「认证授权」决策〕
- 引擎内部的分层：`parsers/*` 只产出 `Book`，不写盘；写盘只在 `writer.py`；读回只在 `library.py`。〔来源：`parsers/__init__.py:parse()` 返回 `(Book, 解析器实例)`，写盘调用集中在 `cli.py`〕

### 异常处理

- 引擎侧：单个章节坏掉不中断整本，用 `book.warn()` 记录并继续；只有整本无法成立时才抛异常。〔来源：`CLAUDE.md`「解析器的失败要局部化」+ `library.py:load()` 章节文件缺失时 `book.warn` 后 `continue`〕
- 引擎侧：抽不出文字的扫描版 PDF 必须显式抛错并给出 OCR 建议，**不得写出空 md 假装成功**。〔来源：`parsers/pdf.py:parse()` `raise ValueError("这个 PDF 里抽不出文字…ocrmypdf")` + `CLAUDE.md`「不要假装成功」〕
- 后端各子系统的错误归一成一个自有异常类型，上层只展示 message，不关心底层是 SDK 错误还是 HTTP 错误。〔来源：`brain.py:61` `class BrainError(RuntimeError)`，12 处 `raise BrainError` 覆盖 anthropic SDK 与标准库 HTTP 两条路径〕
- 异常消息用中文。〔来源：33 处带字面量的 raise，全部中文，100%〕
- HTTP 层：FastAPI 全局异常处理器统一转成错误响应结构；`BrainError` → 502，`FileNotFoundError` → 404，`ValueError` → 400。〔来源：FastAPI 社区基线〕
- **异常消息按层区分**（Spec N1 裁决）：面向用户的边界（HTTP 层、`library`、`config`、`persona`、以及用户可执行补救的解析失败）**必须**给出可选值或下一步动作；引擎内部路径允许只陈述事实，由调用方翻译成用户语言。理由：引擎不知道调用者是 CLI 还是 HTTP，强行统一会让它堆砌自己无从知晓的上下文。〔来源：Spec N1（裁决 Grill R1），Plan S1 规范候选 #6，用户 2026-09-20 确认〕
- **出路要按「谁会读到」写，同一个结论可以有几种说法。** 三条分野：① 用户做得到的补救（`pip install` 之类做不到）走用户可见的位置，管理员才需要的走 ERROR 日志；② CLI 的出路是一条能敲的命令（`--brain` / `--budget`），网页的出路是一个能点的位置（「到『大脑与人格』换一个大脑」）——共用一句话必然有一边执行不了。实现上引擎把类别放进 `BrainError.kind`，两侧各自组自己那句话。〔来源：Plan S3 规范候选 #9 + S13 #43，用户 2026-09-20 裁定〕
- **给人看的失败说明只从写死的码表来，不转述下游的原话。** 引擎、依赖库、数据库的原话里有安装命令、服务器路径、SQL 与参数——对用户没用，对窥探的人有用。落库时 `error_message` 按 `error_code` 从码表取；`remedy` 可以比码表更具体（带真文件名的 OCR 命令），但必须是我们自己组的话；原话连同回溯只进 ERROR 日志，带能查到这一次的 id（书、任务）。**读的那一侧也按码映射**，不读存下来的那一列——旧行里可能还留着原话。与上面「出路要按『谁会读到』写」同源，这条管的是「话从哪来」。〔来源：Todo/Plans/book-lifecycle-plan.md Slice 7，用户 2026-09-25 裁定；实现见 `services/book_lifecycle.py` 的 `_FAILURES`；`tests/test_book_lifecycle.py::test_失败说明只来自码表_原文进日志` 钉着〕
- **协议层面「没有正常结束标记」必须当作失败**，不能当成功返回残缺结果。一段被截断的回答读起来和完整回答一模一样（模型停下时最后一句常常正好是个句号），不说破就是假装成功。〔来源：Plan S7 规范候选 #18，用户 2026-09-20 裁定；实现见 `brain.py` 收到流结束但既无 `[DONE]` 也无 `finish_reason` 时抛错〕

### 接口与响应

- 响应直接返回资源实体，不做 `{code, message, data}` 统一包装；错误用 HTTP 状态码 + `{detail}`（FastAPI 默认结构）。〔来源：FastAPI 社区基线〕
- 入参校验用 Pydantic v2 schema，在 router 签名上完成，service 不再重复校验。〔来源：FastAPI 社区基线〕
- 列表端点分页参数 `?page=1&size=20`，响应 `{items, total, page, size}`。〔来源：社区基线，用户确认 2026-09-16〕
- 流式端点用 **SSE**（`text/event-stream`），不用 WebSocket：解析进度与对话输出都是单向推送。事件名 `token` / `stage` / `notice` / `done` / `error`（另有某一路的结局 `answered` / `failed`，见下面「结束信号」那条）。〔来源：`brain.py` 的 `on_text` 回调本就是单向逐段回调；FastAPI `StreamingResponse` 社区基线；`notice` 由 Todo/Plans/turn-state-plan.md Slice 3 加入，用户 2026-09-23 裁定〕
- **「现在在做什么」与「这一轮有什么不一样」是两个事件。** `stage` 只有一个位置，下一句盖掉上一句，重连只回放最后一条；`notice`（`{code, text}`）一条都不能丢——存成列表，回放时排在一切之前，晚到的订阅者也全部拿到。原先「这本书重新解析过了，这次按全价算」走 `stage`，紧随其后的「正在挑章节…」当场就把它盖掉，恰好在真该说的时候看不见。前端照样显示 `text`，要分支看 `code`。〔来源：Todo/Plans/turn-state-plan.md Slice 3，用户 2026-09-23 裁定；实现见 `services/stream_recorder.py:notice()`〕
- 长耗时任务（解析）返回 **202** + `jobId`，不阻塞请求。〔来源：社区基线〕
- **用户走开就会留下空壳的资源，与第一份内容同一个事务建立；接口上不给「只建空壳」的入口。** 会话从第一问开始存在：`POST /api/conversations` 必须带第一问，会话那一行只 flush、随第一问一起提交，第一问问不出去（没填 key、书在解析……）就什么都不留。先建后问的话，点进「开始对话」又走掉就留下一个「（还没问出第一个问题）」。流式创建回 **201 + `Location`**：头在第一个字之前就到，客户端一拿到就把地址换过去——流着的时候刷新，回到的是这个会话，而不是再建一个、再付一次钱。〔来源：「开始对话」改为问第一句才建会话（read-design-before-code），用户 2026-09-24 裁定；实现见 `services/chat_service.py:begin()`〕
- **结束信号：先落库、后宣布；而且「某一项完成」与「整批完成」是两个事件。** 反过来的话，客户端收到 `done` 时数据还没进库，它立刻回头拉一次就会看到一个不存在的空洞。粒度上：`answered` / `failed` 是某一路的结局，**当场发**（界面要收掉那一列的转圈）；`done` / `error` 是整轮的结局，**等落库之后发**。合成一个事件，其中一条保证必然失效。〔来源：Plan S7 规范候选 #16 + S10 #29，用户 2026-09-20 裁定〕
- **要让代码分支的东西必须机读，不能是文案。** 两处形态：同一个状态码下有两种前端必须区别对待的情况时，给它一个写死的 `code`（`ApiError.code`，如 `reselect_required`）；判断出来的类别要单独带出来，不要揉进一句给人看的话里就扔掉（`BrainError.kind`）。靠中文文案分辨，改个措辞就把分支改坏，而且不报错。**`code` 一律是写死的字面量，绝不来自请求**——它会原样出现在响应体里。〔来源：Plan S8 规范候选 #19 + S13 #42，用户 2026-09-20 裁定〕
- **「没有」有好几种，不能压成同一个值。** 至少要分开三种：上游失败（拿不到数据）、空结果（数据里确实没有）、对象不存在。混成一个，一次上游失败会伪装成一批下游的否定结论；而「这条回答没引原文」（空数组）和「没有这条回答」（404）混成一个，一个打错的 id 会安静地看起来像前者。〔来源：Plan S9 规范候选 #25 + #27，用户 2026-09-20 裁定〕
- **上一条的另一面：同一个「空」有两种来路时，判据不能靠反推。** 反推把两件事压成一个值，而错的那一边不会报错，只会一直说一句假话。实测：`conversations.selected_chapter_ids == []`既可能是「整本装得下，本来就没锁」，也可能是「重解析把它清掉了」；靠「问过好几轮却没有锁定」反推，**整本装得下的每一本书从第二轮起都被告知「这本书重新解析过了，缓存作废，按全价算」**，而书没动过、那笔钱不存在。出路是去找一个能分辨两种来路的既有事实（这里是最近一次真做过决策的那一轮留的档），不是加一个布尔。〔来源：Plan turn-runner 切片 8，用户 2026-09-21 裁定〕
- **流式端点前面的每一层都不得缓冲。** SSE 的价值全在「边生成边到达」，而反向代理默认是**攒够再吐**：nginx 的 `proxy_buffering` 默认 on，开着的话一轮回答会在最后一刻整段出现。它不报错、不掉数据，症状只是「有点慢」——一个永远不会有人去查的症状。新增任何一层（反代、网关、CDN、压缩中间件）时都要重新确认这一条。〔实现见 `docker/nginx.conf`：`proxy_buffering off` + `proxy_cache off` + 放长 `proxy_read_timeout`〕〔来源：2026-09-21 docker compose 交付，read-design-before-code 实测，用户裁定〕
- **SSE handler 轮询数据库，不订阅进程内状态。** 订阅看起来更快，但那件事跑在另一个任务里，两边的真相迟早分叉——分叉之后进度条和刷新后的状态就对不上。〔来源：Plan S3 规范候选 #8，用户 2026-09-20 裁定〕
- **校验失败的报错不回显被校验的值**，当那个值可能是密钥时。报错要说清该填什么、该填到哪里，而不是把用户填错的东西抄回去。自由文本的路径参数要回显时先修剪：去控制字符、截短。〔来源：Plan S6 规范候选 #15 + S11 #36 的回显部分，用户 2026-09-20 裁定〕
- **校验失败不回显被校验的值，由全局处理器统一执行，不靠每个端点记得。** FastAPI 默认的 422 会把被拒的值原样放进 `input`（有时还有 `ctx`）：往「环境变量名」那一栏粘一串超长的 key，默认 422 就把整串 key 抄回响应——落进前端的报错、控制台与错误上报。`main.py` 的 `RequestValidationError` 处理器只回 `loc` / `msg` / `type`，形状不变（前端照旧读 `detail[0].msg`）；限制要说给人听的字段用 `PydanticCustomError` 给中文说明，而不是依赖 Pydantic 的英文默认句。〔来源：Todo/Plans/brain-model-picker-plan.md Slice 8，用户 2026-09-22 裁定〕
- **数字要带着它的来路走；建在估计值上的结论不下。** 同一个数可能是查到的、用户填的、供应商报的，也可能是猜的——在接口上必须分得开（如 `BrainOut.context_source` ∈ vendor / manual / builtin / estimate），界面据此标注。依据是估计值时，由它推出的结论一并不给（估计的那一行 `fits` 为 `null`、写「窗口未知」），而不是带着同样的底气说「整本装得下」。一句猜出来的「装得下」比不说更糟：用户会照着它开会话，然后在供应商那里撞上「上下文超长」。〔来源：Todo/Plans/brain-model-picker-plan.md Slice 2，用户 2026-09-22 裁定〕
- **会在别处才生效的输入，要在录入的那一刻校验。** 人格提示词里的占位符若拼错，会在**下一次提问时**炸在后台任务里——用户看到的症状（「这条回答没说完」）和他的操作（半小时前改了个字）之间没有可见关联，而且往往只影响多条路径中的一条（三种人格只坏那一种）。〔来源：Plan S11 规范候选 #35，用户 2026-09-20 裁定〕

### 日志

- 日志中间件**必须显式剔除 `X-Brain-Key` 请求头**；异常处理器不得回显请求头。〔来源：本文档「认证授权」硬规则〕
- 记录位置：请求出入口（method / path / status / 耗时）、解析各阶段、模型调用的 usage 统计。〔来源：社区基线，用户确认 2026-09-16〕
- 模型用量记录 `input_tokens` / `output_tokens` / `cache_read` / `cache_write` 四项。〔来源：`brain.py:Usage` dataclass 四字段〕
- 级别：`INFO` 业务里程碑，`WARNING` 可继续的异常（对应 `book.warn`），`ERROR` 中断性失败。〔来源：社区基线，用户确认 2026-09-16〕
- **请求日志白名单制，不是黑名单**：只记 method / path / status / 耗时，别的一概不记（不记 query string、不记任何请求头、不记请求体与响应体）。黑名单（「把 `X-Brain-Key` 过滤掉」）只挡得住今天想得到的那个头，明天加一个 `X-Vendor-Token` 它就照样进日志，而且没人会注意到——日志坏掉的时候不会报错，它只是安静地多记了一行。**并且必须一并关掉框架自带的访问日志**（`uvicorn.access` 记的是完整 URL，上面这些克制只要它还在就全是空话）。〔来源：Plan S6 规范候选 #14，用户 2026-09-20 裁定；实现见 `core/logging.py`〕
- **路径写进日志走 `request.url.path`，不用 `request.scope["path"]`。** 两者看着等价，**不等价**：后者是解码后的原样，路径里一个 `%0A` 就能把一行访问日志劈成两行，后一行看起来就是另一次请求——伪造一条「某人登录成功」不需要任何权限。`request.url.path` 走 `urlsplit`，它会剥掉 ASCII 的换行与制表符。`/api/personas/{key}` 是这个应用里第一个自由文本的路径参数，在那之前没人能把控制字符送到这里来。〔来源：Plan S11 规范候选 #36，用户 2026-09-20 裁定；`tests/test_personas.py::test_路径里的换行进不了日志` 钉着这一点〕
- **包一层不得把下一层的 `repr` 隐藏拆掉。** `BrainConfig.api_key` 是 `repr=False` 的，但外面再包一个 dataclass（`BrainRef` → `Turn`）就可能把它重新带出来——而 `Turn` 恰好是后台任务出事时最容易被打印的那个对象：异常回溯里的局部变量、`logger.exception` 里的 `%r`、pytest 断言失败时打印的入参，都是它。每加一层包装就要补一条「打印出来不含 key」的测试。〔来源：Plan turn-runner 切片 6，用户 2026-09-21 裁定〕


### 注释与事务

- 注释解释**为什么**，尤其是反直觉的决定；踩过的坑连同复现条件一起写在注释里。〔来源：`retrieve.py:tokenize()` 注释「悉达多为什么」会切出「多什」；`library.py:as_context()` 注释 dataclass 值比较导致 O(n²)；`ask.py` 模块 docstring 解释每轮重发书为何反而便宜；抽样 8/8 个核心模块〕
- 私有函数 / 模块级私有常量用 `_` 前缀。〔来源：67 处 `def _*`，`_STOP` / `_FRAME` / `_PRESETS` 等，抽样 21/21〕
- 关键字参数强制：语义不自明的可选参数一律放在 `*` 之后。〔来源：12/21 个模块使用，含全部公开 API：`writer.write_book(*, images_from)`、`retrieve.select_chapters(*, budget_chars, always_include)`、`ask.open_session(*, persona, cfg, ...)`、`brain.chat(*, on_text)`〕
- 事务边界在 `services` 层，一个用例一个事务；`repositories` 不自行 commit。〔来源：SQLAlchemy 社区基线〕
- **持有资源的上下文管理器，重入要当场抛错，不许静默共用。** 静默共用的话，里层退出时那次 commit 会把外层写到一半的东西一并提交掉，而且一个字都不报——事务边界糊过一次就不再是可读的了。〔来源：Plan turn-runner 切片 2，用户 2026-09-21 裁定〕


### 并发与后台任务

- **`messages.seq` 持 `conversations` 行锁发放，禁不持锁的 `max(seq)+1`。** 不持锁时另一处还没提交的写读不到，两边算出同一个号，而 `(conversation_id, seq)` 上有唯一约束，症状是偶发的 500——落在回答那一侧，就是一条已付费的回答没能存下来。持锁之后取 `max` 再加一正是发放方式（`services/turn_state.py:_allocate_seq`）。**这是拿吞吐换确定性**：同一个会话的写被串行化了。什么时候可以重新考虑——如果将来一个会话出现高频并发写入（本轮不会：一个会话同一时刻只有一轮在跑，由 `turn_state` 的席位保证），再换成序列或客户端生成 id。〔来源：Plan S1 规范候选 #3，用户 2026-09-20 裁定；措辞与理由按 Todo/Plans/turn-state-plan.md Slice 1 / 4 修订，用户 2026-09-23 裁定〕
- **先锁、后读：要在行锁之下读的东西，不能和取锁的 `FOR UPDATE` 放在同一条语句里。** 读已提交隔离级别下，一条语句的快照在它**开始**那一刻就定了；`FOR UPDATE` 等到锁之后只重读被锁的那一行，同一条语句里的子查询看到的仍是等锁之前的世界——`max(seq)` 读不到刚刚提交的那一条，算出同一个号撞唯一约束。它不报别的错，只在「两件事恰好同时发生」时现形。〔来源：Todo/Plans/turn-state-plan.md Slice 5，用户 2026-09-23 裁定；实现见 `services/turn_state.py:_hold()` / `_facts()`；`tests/test_turn_state.py::test_写提问要等会话行上的锁_放开之后接着发号` 钉着〕
- **同时碰多张表的事务，锁的先后全项目一致：会话行在前、章节行在后。** 一轮提问收尾（写锁定、发号、写引用章节的留档与引文）和重解析（清锁定、删旧章节）都要碰这两类行；顺序相反的话，轮中重解析时两边各拿一把、互等另一把，库只能杀掉一边——杀到收尾那边，就是丢一条已付费的回答。实测：把重解析的清锁定挪到删章节之后，`DeadlockDetectedError` 当场复现。新增任何同时写这两类行的事务，照这个顺序拿锁。〔来源：Todo/Plans/turn-state-plan.md Slice 6，用户 2026-09-23 裁定；实现见 `services/resettle.py:swap()` 与 `services/turn_state.py:_settle()`；生产代码里的次序由 `tests/test_book_lifecycle.py::test_换章节之前已经握着会话行` 钉着，`tests/test_turn_state.py::test_重解析与收尾同时进行_两边同序拿锁_不死锁也不丢回答` 看两边同时发生时的结局（引用按 Todo/Plans/book-lifecycle-plan.md Slice 1 更新，用户 2026-09-25 裁定）〕
- **并发跑同一段逻辑的 N 份时，可变的共享状态必须各发一份拷贝。** 对读三路共用一个 `history` 列表的话，`Session.ask()` 结束时那句 `history.append` 会三路一起往同一个 list 里塞，下一轮的上下文变成三种声音交替的一团乱麻——**而且不报错**。〔来源：Plan S10 规范候选 #30，用户 2026-09-20 裁定〕
- **session 不跨边界：不跨线程，也不跨请求生命周期。** 跨线程共用 `AsyncSession` 会变成偶发、难查、看起来像数据库抽风的 bug；跨请求生命周期则是另一回事——请求那条 session 随响应结束就关了，而流式回答的落库可能发生在客户端早就断开之后。两种情形的做法一样：**活得比调用方长的活儿，自己开一条 session**。〔来源：Plan S3 规范候选 #7 + S7 #17，用户 2026-09-20 裁定〕
- **写发生在别处时，手上那个 ORM 对象就是旧的。** 两种来路：后台任务在另一条 session 里写过的字段；以及本 session 里的**批量 `UPDATE` / `DELETE`**（它绕过 ORM 的属性赋值）。两种都不会报错——`expire_on_commit=False` 下 commit 不刷新身份映射，而一次普通 `SELECT` 也**不覆盖**已经载入的属性，于是读到的是写之前那一份。三条出路，按场合挑：取单列（`select(Conversation.selected_chapter_ids)`）绕开身份映射；批量写加 `execution_options(synchronize_session="fetch")`；或者换一条 session 去读。〔来源：Plan S8 规范候选 #20 + S12 #40，用户 2026-09-20 裁定〕
- **后台写入的错误策略必须套住「提交那一刻」，不只是 `with` 体。** 失败最常发生在 commit：唯一约束、外键、连接断掉都在那里报，而不是在 `with` 体里。只把 try 包在 `with` 体上，这条策略就恰好在**最常见的那种失败**上不生效——而它不生效的时候，看起来和生效一模一样。〔实现见 `db/unit_of_work.py:Detached.__aexit__`〕〔来源：Plan turn-runner 切片 2，用户 2026-09-21 裁定〕
- **后台任务由拥有那份状态的模块自己排，不归路由。** 排在路由里（FastAPI `BackgroundTasks`），「有没有人在跑」就散在每个调用方手里——新加一个入口忘了排，书就一直停在「解析中」，直到下次重启清扫。三件事：提交返回之后**同步** `asyncio.create_task`，中间不隔可被取消的 await（客户端断开会取消请求协程，排在 await 后面的任务可能永远排不出去）；任务强引用进模块级集合（事件循环对任务只持弱引用）；done-callback 把逃出来的异常记 ERROR。模块给一个 `drain()` 等它们收尾，测试有一条 autouse 夹具断言每条测试结束时集合为空。〔来源：Todo/Plans/book-lifecycle-plan.md Slice 3，用户 2026-09-25 裁定；实现见 `services/book_lifecycle.py:_start()` / `drain()`，`services/turn_state.py` 的 `_RUNNING` 同形〕


### 写外部状态

- **写文件要么整体成功、要么一个字不动。** 顺序是：先校验，再留退路，最后用临时文件 + `os.replace` 整体换过去。`open(path, "w")` 会先把文件截断，进程在那一刻挂掉就留下一个半截的文件——它不报错，只让此后每一次使用都差一点。〔来源：Plan S11 规范候选 #33，用户 2026-09-20 裁定；实现见 `services/prompt_store.py:_atomic_write()`〕
- **在用的目录不原地改写：写进暂存，提交之后整体换上。** 这是「写文件要么整体成功、要么一个字不动」用在目录、以及「库 + 盘」两段式上：新产出写进同一个卷上的暂存目录，数据库事务提交之后才 rename 上去（旧目录先挪开再删）；失败只删暂存；崩在中间，由启动对账照库里**已提交**的事实补完或删掉——库先提交、盘后动，对账只信库。原地写的话，写的途中读的人读到新旧混杂的正文，失败之后旧版已经写坏，章节变少还留着旧文件。〔来源：Todo/Plans/book-lifecycle-plan.md Slice 10，用户 2026-09-25 裁定；实现见 `services/book_lifecycle.py:_publish()` 与 `sweep_orphans()`；`tests/test_book_lifecycle.py::test_重新解析写完了还没换上去_读到的仍是完整的旧版` 与 `test_重启清扫_孤儿收口_暂存清掉_已提交未发布的补完` 钉着〕
- **覆写用户反复打磨过、又不在版本控制里的资产，必须留一份基线。** `prompts/` 那三个 md 就是这样的东西。基线取**第一次覆写前**那一版，不是上一版——用户点「还原」时多半已经改了三四轮，最想要的正是最初那一版。〔来源：Plan S11 规范候选 #34，用户 2026-09-20 裁定〕
- **同一份磁盘状态不得被两个独立的数据库共用。** 书库的存储路径是 `{user_id}/{book_id}-书名`，两个字段都由数据库发号。于是两套各自发号的部署（本机开发那份库 与 compose 那份库）指向同一个目录时，迟早会编到同一个路径上，**静默覆盖**对方的解析产物——不报错，只是某本书的正文变成了另一本。实测：compose 首次解析写出 `library/2/1-悉达多`，而开发库的书占着 `3/ 5/ 6/ 7/`，这次纯属错开。给每套部署各自的根目录，别让「看得见」这个好处换掉正确性。〔来源：2026-09-21 docker compose 交付，read-design-before-code 实测，用户裁定〕
- **测试会清空的外部位置（数据库、书库根、上传根），由测试自己指定，不沿用外面的环境变量。** conftest 里用赋值，不用 `setdefault`：沿用的话，开发者 shell 里 export 过的那个变量会把测试领进真目录，而测试每条开跑前都要清空它——一次 `pytest` 就能清掉真书库，一个字都不报。要换测试用的位置，另起 `BOOKSOUL_TEST_*` 名字。〔来源：Todo/Plans/book-lifecycle-plan.md Slice 3（Risk Notes #1），用户 2026-09-25 裁定；实现见 `tests/conftest.py`，数据库连接串早先已是这样〕
- **持有外部资源的对象（文件句柄、压缩包、子进程管道）用完当场关，不靠垃圾回收。** Windows 上一个还开着的文件删不掉、也改不了名；而对象之间只要有引用环，回收就要等到下一轮 GC——于是删书、重新解析这类要动原件的操作会偶发地撞上 `PermissionError`，而且只在 Windows 上。实测：EPUB 解析器握着原件的 zip 句柄从不关（解析器与产出之间有引用环），加上「每条测试前清空上传根」之后，Windows 上 6 条测试在准备阶段报 `WinError 32`。做法：给这类对象一个 `close()`，构造或主流程半路失败时当场关，调用方在 `finally` 里关。〔来源：Todo/Plans/book-lifecycle-plan.md Slice 3，用户 2026-09-25 裁定；实现见 `booksoul/parsers/epub.py:EpubParser.close()` 与 `services/importer.py:import_file()`；`tests/test_import.py::test_解析完就放开原件_不等垃圾回收` 钉着〕


### 与模型打交道

- **拼给模型的消息必须角色严格交替。** 上一轮断过流留下的落单提问（有 user 没 assistant）会让历史里出现连续两条 user，那是一个只在特定历史下才现形的 500。拼历史时一轮要既有提问、又有一条说完了的回答才进得去。〔来源：Plan S10 规范候选 #31，用户 2026-09-20 裁定〕
- **作为缓存前缀的那段内容，不得依赖任何随请求而变的东西。** 一个看起来纯装饰的统计量就能让整本书的前缀缓存作废——实测：人格提示词里那句「篇幅 48,155 字」，按 `only_idx` 载入时 `Book.words` 只数载入的那几节，于是同一本书两轮之间数字不同、缓存全废。**而长度可能完全相同**，所以验证要比哈希，不能比长度。引擎侧的对策是让 `Chapter` 带上 `stated_words`（meta.json 里记的原值），载入方式不再影响它。〔来源：Plan S8 规范候选 #21，用户 2026-09-20 裁定〕

- **服务端按用户存下的地址发出去的请求，对方说的话一个字都不带回来。** 列模型、提问都会按用户保存的自定义端点地址发请求，而那个地址可以指向内网——把对方的响应转回给用户，就是借服务器之手窥探内网。三条做法：成功时只挑需要的字段（列模型只回 `id` 与窗口，`owned_by` 等一概不转）；失败时**不读错误响应体、状态行的原因短语也不进消息**，只给类别、地址与状态码；异常链用 `from None` 截断——`HTTPError` / SDK 异常挂在链上，一条 `logger.exception` 就会把对方的原话写进日志。测试把特征串同时塞进响应体、原因短语与连接错误里，断言它不出现在回溯全文、响应与日志中。〔来源：Todo/Plans/brain-model-picker-plan.md Slice 5 / 6，用户 2026-09-22 裁定；实现见 `brain.py:_list_failure()`〕

### 命名约定

| 对象 | 规范 | 举例 |
|------|------|------|
| 常量 | 全大写 + 下划线，模块私有加 `_` 前缀 | `DEFAULT_PERSONA`、`_TITLE_BOOST`、`_CONTEXT_BUDGETS` 〔来源：`persona.py`/`retrieve.py`/`ask.py`〕 |
| 布尔变量 | 无统一前缀，用自然语义词 | `partial`、`whole`、`cached`、`keep_images` 〔来源：`library.py`/`retrieve.py`/`brain.py`，抽样 4/4〕 |
| 类 | PascalCase | `BookMeta`、`BrainConfig`、`AnthropicBrain` 〔来源：抽样 9/9〕 |
| 方法 / 变量 | snake_case | `select_chapters`、`budget_chars` 〔来源：抽样 21/21〕 |
| 私有方法 / 函数 | `_` 前缀 | `_build_system`、`_front_matter`、`_strip_chapter_header` 〔来源：67 处〕 |
| 模块 | 全小写单词，不用下划线 | `retrieve`、`footnotes`、`xmlutil` 〔来源：21/21 模块名均为单词〕 |
| API 路径 | kebab-case，资源用复数 | `/api/books`、`/api/conversations` 〔来源：REST 社区基线，用户确认 2026-09-16〕 |
| 数据库表 | snake_case 复数 | `books`、`retrieval_items` 〔来源：PostgreSQL 社区基线〕 |

### 跨层命名对照

> 同一业务字段在数据库、后端、API JSON、前端四处的写法与转换位置，避免跨层翻译歧义。数据库侧规范见 `database/sql.md`「约定」。

| 层 | 规范 | 举例 | 转换在哪里完成 |
|----|------|------|--------------|
| 数据库字段 | snake_case | `word_count` | — |
| 后端实体字段 | snake_case（Python 惯例，与 DB 同形，不转换） | `word_count` | SQLAlchemy 默认同名映射 |
| API JSON 字段 | camelCase | `wordCount` | Pydantic schema 上配 `alias_generator=to_camel` + `populate_by_name=True`，序列化时转换 |
| 前端字段 | camelCase，与 JSON 一致，不再转换 | `wordCount` | — |

> 转换只发生在 Pydantic 序列化这一处。Python 侧坚持 snake_case 是为了和引擎保持一致——引擎全部 snake_case（抽样 21/21），若后端实体改用 camelCase，`services` 层每次调引擎都要翻译一次。

> **例外**：引擎写出的 `meta.json` 用中文键（`书籍` / `统计` / `章节` / `警告`），这是给人读的产物格式，不参与上述对照。`repositories` 读它时在 `library.py:_FIELD_MAP` 处完成中→英映射。〔来源：`writer.py:write_book()`、`library.py:_FIELD_MAP`〕

## 待确认问题

1. **【已裁决 2026-09-20】异常消息是否必须给出可选值或下一步动作。** 取 **(b) 按层区分**，已写进「异常处理」章节。下面的实测分布保留作为依据（原 16/33 = 48%，不到 80% 门槛，所以当时没写成规范）：
   - **给出路的**（用户直接触达的路径）：`library.find()`「书库是空的…先解析一本：booksoul parse 你的书.epub」、`config.resolve()`「没有名为 X 的大脑。已知：…」、`persona.load()`「未知人格 X。可选：…」、`brain.py` 缺 SDK 时给 `pip install anthropic`、`pdf.py` 给 ocrmypdf 命令。
   - **只报错的**（引擎内部路径，共 17 处）：`epub.py`「EPUB 缺少 manifest」「EPUB 里没有解析出任何正文」、`docx.py`/`plain.py` 同形、`ask.py`「问题是空的」、`parsers/__init__.py`「文件不存在：X」。
   - 裁决结果：**(b)**。见 Spec N1 与「异常处理」章节。S13 又在它上面加了一层——出路还要按「谁会读到」分：CLI 给能敲的命令，网页给能点的位置。
2. **任务队列选型未定，且「单实例」现在是一条硬约束。** 解析是长耗时任务，当前由 `book_lifecycle` 在同一进程里排（提交之后同步 `asyncio.create_task`，任务表 `_RUNNING`；原先是路由里的 FastAPI `BackgroundTasks`）。多实例部署需要 Celery/RQ/arq + Redis。
   `compose.yml` 因此**不可以 `--scale api=2`**，而它扩起来不会报错，只会坏三件事：① `turn_state` 的席位（「这个会话正在答哪个问题」）是进程内的字典，重连请求落到另一个副本就找不到那条流，于是同一个问题再调一次模型（用户付两次钱，正是 Spec #39 要防的）；② `sweep_orphans` 的判据是「进程刚起来那就没人在跑」，第二个副本启动会把第一个**正在跑**的解析标成孤儿（两者共用书库根时，还会删掉它正写着的暂存目录）——**本机开发时让 `uvicorn --reload` 和容器里的 `api` 同时连着同一个库，也是两个副本**，同样会踩；③ 「一个会话同一时刻只有一轮」同样只在一个进程里成立（`messages.seq` 本身持会话行锁发放，多副本也不会撞号）。切换时席位换成库里的（advisory lock + 回放），`turn_state.admit` 的调用方不必跟着改。何时切换请裁决。
3. **`prompts/` 的多用户归属 —— 本轮按全局文件落地，多用户上线前仍须处理。** 人格提示词是全局三个 md 文件（`persona.py:prompt_dir()` 读项目根），`PUT /api/personas/{key}` 直接改它们，**改动对所有用户生效**（Spec N5，裁决 Grill R3）。⑧屏右栏已显式写明这一点——代码里根本没有 user_id，这条只能靠界面和 `services/prompt_store` 的模块注释存在。真要多用户，需新增 `user_personas` 表或按用户目录隔离。
4. **书籍去重未定。** 同一用户重复上传同一本书（同 ISBN 或同文件哈希）时是覆盖、并存还是拒绝。当前 schema 在 `books` 上留了 `source_sha256` 字段但未加唯一约束。
5. **配额与计费未定。** key 由用户自带，token 成本落在用户身上，因此服务端暂不做配额。若将来改为服务端统一 key（本轮未选），需要新增用量表与限额逻辑。
6. **【已解决 2026-09-23】「这本书重新解析过了」这条告示递送不到用户眼前。** 改走独立的 `notice` 事件（存成列表、全量回放、`stage` 盖不掉），前端 `useAsk.notices` + 对话页横幅；见 `Todo/Plans/turn-state-plan.md` Slice 3。以下为原记录： 判据本身已经修好（见「重解析」），但它产出的那句话走的是 `stage` 事件，而 `stage` 只有一个「现在在做什么」的位置：紧随其后的「正在挑章节…」会当场把它盖掉，`listen()` 回放的也只是最后一条 stage。于是它恰好在**真该说**的时候（真重解析 → 真重挑 → 真按全价重算）看不见。修它要给「本轮告示」一条不会被覆盖的通道，涉及 SSE 事件契约（本文档「接口与响应」列的是 `token` / `stage` / `done` / `error` + `answered` / `failed`）与前端渲染方式。〔来源：Plan turn-runner 切片 8 实施中发现〕
7. **交叉验证结果**：本轮 API 端点与 `frontend.md` 的 API 调用层、`database/*.md` 的表结构逐项对齐，未发现不一致。brain-model-picker 收尾时复核：三个新端点 ↔ `web/src/api/brains.ts` 的 `listModels` / `saveModel` / `resetModel`；`BrainOut` 新字段 ↔ `types/index.ts` 的 `Brain`；`brain_choices` / `conversations.context_chars` ↔ `sql.md` 与迁移 `0003` / `0004`。引擎侧 `meta.json` 中文键与数据库英文列名的映射点已在「跨层命名对照」注明。turn-state 收尾时复核：SSE 新增 `notice` ↔ `web/src/composables/useAsk.ts` 的 `notices` 与 `types/index.ts` 的 `TurnNotice`；`/ask` 新增的两种 409（另一个问题正在答、书在解析中）走既有的 `ApiError` 路径，前端照样显示 `detail`；无 schema 变更。2026-09-24 复核：`POST /api/conversations` 改为连同第一问的 SSE（201 + `Location`）↔ `web/src/composables/useAsk.ts` 的 `{ start }` 目标（读 `Location` 取新会话 id）；`ConversationCreate` 新增 `question` / `compare` ↔ 同处请求体；`api/conversations.ts` 的 `startConversation` 已删；无 schema 变更。2026-09-25 复核：无接口变更；迁移 `0005` 把 `chapters.source_id`、`books.published`、`books.isbn` 放宽成 TEXT（`sql.md` 与 ORM 同步，`tests/test_schema_drift.py` 钉着）；提问路 `failed` / `error` 的 `message` 改由 `TurnRunner._failure_text` 按类别给出，前端照样显示，不受影响。2026-09-25 book-lifecycle 收尾复核：新增 `GET /api/books/{id}/state` ↔ `web/src/api/books.ts` 的 `getBookState` 与 `types/index.ts` 的 `BookState`（`BookDetailView` 解析中轮询它），`BookStateOut` 的字段与 `BookState` 一一对应；重解析的两种 409（`book_parsing` / `source_missing`）、上传的 415（`unsupported_format`）、开会话与提问 409 新带的 `code` 都走既有的 `ApiError` 路径，前端照样显示 `detail`；`BookCard` 为 `unsupported_format` / `internal` 配了说法，`unsupported_format` 不给「重新解析」；`parse_jobs.error_code` 词表新增 `unsupported_format`（`sql.md` / `database-relations.md` 同步）；无 schema 变更。
