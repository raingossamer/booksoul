---
feature: booksoul-web
mode: standard
date: 2026-09-16
---

# BookSoul Web — Grill Report

把现有 CLI 解析引擎做成多用户 Web 产品：上传 → 解析入库 → 个人书库 → 阅读溯源 → 选书选人格对话。

拷问基于本日生成的五份 Design（混合模式：`src/booksoul/` 逆向 + HTTP/前端/持久化正向），
逐条与引擎实际代码交叉比对。九个问题全部得到用户确认。

## 1. 确认的决策

### D1 — 选章锚定在会话，不在消息

`ask.py:96` 刻意用第一轮的选章结果（「避免每轮换书导致缓存全废」），而 `brain.py:build_system()`
把缓存断点放在全书之后，要求断点前的前缀逐字节稳定。原 schema 把 `retrievals` 挂 `message_id`
UNIQUE，暗示每轮可重选，与引擎设计相反。

- `conversations` 增列 `selected_chapter_ids JSONB`，第一轮定下不再变
- 服务层每次请求重建 `Session`：按该列只载入选中章节 → 从 `messages` 灌回 history → 跳过选章
- 逃生门：新问题命中未载入章节的标题时（复用 `retrieve.py` 已有的标题命中加成逻辑），
  提示用户「重选会重置缓存，继续吗」，确认后才重选并更新该列
- `whole_book = true` 时该列留空

### D2 — 对读 = 同一轮上的三个分支，主干由会话决定

`frontend.md` 与界面稿⑦屏把「对读」作为产品立意，但 `conversations.persona` 是单值、
`messages` 无 persona 列、`UNIQUE(conversation_id, seq)` 一轮一条——数据层没有落点。

- `messages` 增列 `turn INTEGER`、`persona persona_key NULL`（user 消息为 NULL）
- 唯一约束改为 `UNIQUE NULLS NOT DISTINCT (conversation_id, turn, persona)`（PG 16，`brains` 已用同手法）
- 对读一轮 = 1 条 user + 3 条 assistant，同 turn 不同 persona
- `conversations.persona` 语义改为「当前主干人格」；拼 history 只取主干那一支，旁支不进上下文
- 用户点「跟这个继续聊」→ 更新主干人格，旧旁支折叠可查
- 成本事实：三次调用共享同一份整本书的缓存，真实成本 ≈ 1 份书 + 3 份人格与问答，不是三倍

**回改 D1**：`retrievals` 不再挂 `message_id`，改挂 `conversation_id` + `turn`，
唯一约束 `(conversation_id, turn)`；前端路由 `/chat/:id/retrieval/:messageId` → `/chat/:id/retrieval`

### D3 — 目录名带 book_id；引擎增开按路径直取的入口

`writer.py:write_book()` 的目录名只由书名决定（`slugify(book.display_title())`，
`fallback="untitled"`、`maxlen=60`），同一用户传两本同名书会**静默覆盖**，
而 `books.storage_path` 无唯一约束，DB 会留下两行指向同一目录。

- 目录改为 `<user_id>/<book_id>-<slug>`；`books` 行在解析前插入（`status='parsing'`），id 那时已有
- `storage_path` 加 `UNIQUE`
- 引擎增参：`write_book(book, out_root, *, dir_name: str = "")`，不给则保持现行为
- 引擎拆函数：`library.load_from_path(path) -> Book`；`load()` 改为一行调用它。
  服务层走 path，CLI 保持 `load(query)` 不变
- 连带：重复上传天然并存而非覆盖；`source_sha256` 唯一约束降级为纯产品选择，
  决定为**只提醒不阻止**（不同译本/版本是正当需求）

### D4 — 按 word_count 分流；后续轮次按需读章节

`library.load()` 逐个读取全部章节文件，无按需加载；`retrieve.select_chapters()` 对装不下的书
全量 `tokenize`（中文一元+二元，n 字约产出 2n token）。500 万字全集意味着约 1000 万 token
进 Counter，全在一个同步 HTTP 请求里。

- `load_from_path(path, *, only_idx: set[int] | None = None)`，第二轮起只打开选中的文件
- 第一轮按 `books.word_count`（DB 现成，不读盘）分流：装得下 → 同步直读，根本不跑 BM25；
  装不下 → 选章走后台任务，复用解析的 SSE，`stage` 事件推「正在挑章节…」
- **不做预建索引**：中文一元+二元的 Counter 序列化后与正文同量级，等于把书存两遍，
  只在「大部头 + 第一轮」这一窄场景有用，不划算
- 判据用已有的 `brains.context_tokens` 与 `books.word_count`，不新增字段
- 待补文档：`backend.md` 补分流说明；`frontend.md` 补大部头第一轮的「正在挑章节」等待态（界面稿未画）

### D5 — 密钥措辞改为事实；堵住 `__repr__` 泄漏

原文档称「key 一行都不落服务端 / 服务端零密钥责任」，不准确——key 每次提问都经服务端内存转一手。
且 `config.py` 的 `BrainConfig.api_key` 是普通 dataclass 字段，**自动生成的 `__repr__` 含它**，
任何 `logger.debug(cfg)` 或抓 locals 的异常上报都会把 key 写进日志。

- `api_key: str = field(default="", repr=False)`；并立规范：**任何持有密钥的 dataclass 字段必须 `repr=False`**
- 全局异常处理器禁止携带 locals；接 Sentry 需关 `send_default_pii` 并把 `X-Brain-Key` 加入 scrub
- 日志中间件请求头**白名单制**（只记 method/path/status/耗时），不用黑名单
- 用户可见文案改为：「你的 key 保存在这台浏览器里，不会存到服务器。每次提问时它会经服务器
  转发给模型厂商——服务器只是转发，不保存、不记录。」
- 已否决的替代：浏览器直连模型厂商。每轮要把整本书从浏览器上传（几 MB 到几十 MB），
  且多数 OpenAI 兼容端点不开 CORS
- ⑧屏设置页那一列从「环境变量名」改为「你填的 key（已保存在本机）」+ 掩码回显

### D6 — 格式支持由运行时探测驱动，错误消息分受众

`parsers/__init__.py` 的 `SUPPORTED` 是静态字典，但能否解析取决于可选依赖
（`pyproject.toml` 把 docx/pdf/kindle 全放在 optional）。本会话实测：`pymupdf`/`mobi`/`anthropic`
三个都未装，PDF 上传直接失败。而界面稿⓪③屏承诺全部 8 种格式。

- 部署依赖写死 `booksoul[all]`，写进 `backend.md` 技术栈表
- 启动时逐个试 import 得出**实际可用**格式集合；缺失打 ERROR 日志（给管理员）并从对外列表摘掉
- 新增 `GET /api/formats`；前端的拖放提示、`accept`、上传前校验全部由它驱动，不再硬编码后缀
- 错误消息分受众：用户看「这个部署暂不支持 PDF」；`pip install pymupdf` 只进服务端日志
- `sql.md` 的 `parse_jobs.remedy` 加注释约束：**只放用户自己能执行的补救**
  （`ocrmypdf` 算，装依赖不算）

### D7 — 范围收窄：先本地跑通（用户决定）

- **进**：SSE handler 改为轮询数据库（每 800ms 查 `parse_jobs`）而非订阅进程内状态——
  比订阅更简单，且天然跨实例；与 `GET /api/jobs/{jobId}` 轮询端点共用同一份数据，不会互相矛盾
- **进**：孤儿清扫。`parse_jobs` 增列 `heartbeat_at TIMESTAMPTZ`，解析推进时更新；
  启动时把心跳停滞的任务标 `error_code='orphaned'`、`books.status='failed'`，
  给用户可执行的出路「重新解析」。本地 `uvicorn --reload` 存文件即重启，不做这个会天天造孤儿
- **推迟**：Redis / arq / 多实例部署，连同心跳超时尺度（2 分钟 vs 10 分钟 vs 按章更新）一并挂起
- `BackgroundTasks` 保留，`backend.md` 写明约束：任务在接收上传的实例上跑，重启即中断，由心跳清扫兜底

### D8 — 断流保住已生成内容，重连绝不重调模型

原设计「结束后一次性落库」，中途断掉则该轮完全不存在（连 user 消息都没有）。
`ask.py:Session.ask()` 同形——`history.append` 在 `chat()` 返回之后，抛异常就到不了。
代价特殊：用的是**用户自己的 key**，断掉浪费的是他的钱。本地 `--reload` 会频繁触发。

- user 消息在调用模型**之前**落库
- assistant 走 `try/finally`：正常结束一次性写（正常路径**零额外开销**，不做定期 UPDATE）；
  异常或断连时在 `finally` 落成残缺消息。实现上把 `on_text` 累积的文本留在外层变量
- `messages` 增列 `complete BOOLEAN NOT NULL DEFAULT true`，残缺置 false
- 前端渲染「这条回答没说完」，给**重问**与**接着往下**两个按钮（两个都留）
- **重连绝不重调模型**：服务端发现该轮已有 `complete = false` 的 assistant 消息，
  回放已有内容即可。「重连 = 重试」在多数场景对，在这里错——那是让用户为同一个问题付两次钱

### D9 — 重解析：清空选章 + 引文重定位 + 事前提示

D1 的会话级选章锁定 + D3 的 book_id 复用目录，叠加出一个新的坏情况：重解析后旧 `chapters`
行被删，`selected_chapter_ids` 里的 id 全部失效 → 载入 0 节 → 模型手里没书开始瞎编。
**不报错，只是悄悄答得不对**，违反 `CLAUDE.md` 第一条「不要假装成功」。

- 重解析后清空该书所有会话的 `selected_chapter_ids`；下一轮按新章节重跑 BM25，
  并明确告知「已按新章节重选，之前的缓存失效」
- 服务层加断言：**载入章节数为 0 时直接抛错，不往模型送**（写进 `backend.md` 规范章节）
- **引文重定位**：拿每条 `citations.quote` 去新章节正文做子串查找。引文是原文片段，
  重解析改的主要是切分与清洗，正文基本不动，多数能定位回去并补上 `chapter_id`/`para_index`；
  找不到的降级为灰色按钮 + 「原位置已变更」
- **不做版本化**：`book_versions` 意味着旧产物长期留盘，重解析三次就有四份正文，
  对「本地先跑通」阶段是过度设计
- 重解析前提示用户「这本书有 N 个进行中的会话会受影响」

## 2. 发现的冲突

| # | 冲突 | 性质 | 解决 |
|---|------|------|------|
| C1 | `retrievals` 挂 message 级，与 `ask.py` 刻意的「多轮复用第一轮选章」相反，会使 prompt 缓存全程失效 | 设计文档 ↔ 引擎代码 | D1 |
| C2 | 「对读」是产品立意，但 `conversations.persona` 单值、`messages` 无 persona 列 | 前端设计 ↔ 数据库 schema | D2 |
| C3 | 目录名只由书名决定 → 同名书静默覆盖；`storage_path` 无唯一约束 → DB 两行指向同一目录 | 引擎代码缺陷（数据丢失） | D3 |
| C4 | API 持有精确 `book_id`，却要走 `library.find()` 的模糊书名匹配，撞名即抛异常 | 设计文档 ↔ 引擎入口 | D3 |
| C5 | `BrainConfig.api_key` 未设 `repr=False`，`__repr__` 会打印密钥；注释写「不落盘」但无机制保证 | 引擎代码缺陷（安全） | D5 |
| C6 | 文档称「服务端零密钥责任」，事实是每次请求经服务端内存转发 | 文档措辞失实 | D5 |
| C7 | 界面承诺 8 种格式，实际取决于可选依赖是否安装；缺失时把 `pip install` 抛给终端用户 | 前端设计 ↔ 部署现实 | D6 |
| C8 | 解析在进程内跑、SSE 订阅进程内状态；实例重启后任务成永久孤儿 | 完整性缺口 | D7 |
| C9 | 「结束后一次性落库」使断流丢失全部已生成内容，且浪费的是用户自己的 key 额度 | 完整性缺口 | D8 |
| C10 | D1 + D3 叠加出：重解析后会话选章全部失效 → 载入 0 节 → 静默瞎编 | 跨决策连锁 | D9 |

## 3. 需要更新的文档

| 文档 | 更新内容 | 来源 |
|------|---------|------|
| `Design/backend.md` | 核心业务流程补 Session 重建与选章复用；补 word_count 分流；补重解析完整流程；「认证授权」密钥措辞改为事实；规范章节加两条（密钥字段 `repr=False`、载入 0 节即抛错）；技术栈表「任务队列」填 `BackgroundTasks` 并写明单实例约束与 `booksoul[all]` 部署依赖；新增 `GET /api/formats`；待确认 1（异常消息）与 3（prompts 归属）仍未决 | D1 D4 D5 D6 D7 D9 |
| `Design/frontend.md` | 对读改为分支模型；检索路由去掉 `:messageId`；补大部头「正在挑章节」等待态；补断流「没说完」态与两个按钮；⑧屏密钥列改掩码回显；格式列表改由 `GET /api/formats` 驱动 | D2 D4 D5 D6 D8 |
| `Design/database/sql.md` | `conversations` 加 `selected_chapter_ids`；`messages` 加 `turn` / `persona` / `complete` 并改唯一约束；`retrievals` 改挂 `conversation_id` + `turn`；`storage_path` 加 UNIQUE；`parse_jobs` 加 `heartbeat_at` 并给 `remedy` 加受众约束注释；迁移记录追加 | D1 D2 D3 D7 D8 |
| `Design/database/database-relations.md` | 关系表随上述改；待确认 1（`source_sha256`）与 3（重解析策略）改为已决；补重解析对 `selected_chapter_ids` 的影响 | D3 D9 |
| `Design/database/data-er.md` | ER 图同步三张表的新列与新关系基数 | D1 D2 D3 D7 D8 |
| `src/booksoul/config.py` | `api_key` 字段加 `repr=False` | D5 |
| `src/booksoul/writer.py` | `write_book` 增 `*, dir_name: str = ""` | D3 |
| `src/booksoul/library.py` | 拆出 `load_from_path(path, *, only_idx=None)`，`load()` 改为调用它 | D3 D4 |
| `CLAUDE.md` / `AGENTS.md` / `.github/copilot-instructions.md` | 代码结构补 `src/booksoul_api/`；三份须同步（该文件规则 6） | 全部 |

## 4. 风险清单

| # | 风险 | 状态 |
|---|------|------|
| R1 | **异常消息是否必须给出可选值/下一步**：实测 16/33 = 48%，不到 80% 门槛。面向用户的路径（`library`/`config`/`persona`/`pdf`）都给出路，引擎内部 17 处裸报错。三个选项见 `backend.md` 待确认 1 | 未裁决 |
| R2 | 项目无任何格式化/静态检查配置（无 `.editorconfig`/`ruff`/`black`）。4 空格、双引号、行宽 88 是实测统计（98%–100%）而非工具强制 | 未裁决 |
| R3 | `prompts/` 多用户归属未定。现为全局三个 md 文件；若允许用户各自覆写需新增 `user_personas` 表 | 未裁决 |
| R4 | azw3 支持是否标为「实验性」。`mobi` 包解析 azw3 会落临时文件，且对新版 KFX 无能为力 | 未裁决（本轮提出，用户未答） |
| R5 | D8 的「接着往下」续写按钮：需把残文塞回 assistant 角色让模型续写，多数模型支持但效果不稳 | 已接受，效果待实测 |
| R6 | D9 的引文重定位命中率未经验证。若重解析同时改了清洗规则（如合并硬换行策略变化），子串匹配可能大面积落空 | 已接受，需实测 |
| R7 | 心跳超时尺度未定（2 分钟 vs 10 分钟 vs 按章更新）。随 D7 一并推迟 | 推迟 |
| R8 | 多实例部署、Redis/arq、SSE 跨实例 pub/sub | 推迟 |
| R9 | 登录/注册页无界面稿；移动端未设计（九屏均为 1280×860） | 未决 |
| R10 | 上传体积上限未定，前后端需一致 | 未决 |

## 5. 下一步建议

按依赖顺序，共四段。前两段不碰引擎，可并行；第三段起才需要动 `src/booksoul/`。

1. **数据层先落地**（无依赖）：按第 3 节改完五份 Design 的 schema 部分，写 Alembic `0001_init`，
   本地起 PostgreSQL 16 建库。这是后面一切的地基，且改起来最便宜。
2. **引擎三处小改**（D3 D4 D5，合计约 30 行）：`config.py` 的 `repr=False`、
   `writer.py` 的 `dir_name` 参数、`library.py` 拆 `load_from_path` 并加 `only_idx`。
   每一处都有现成测试可挂（`tests/test_parsing.py` 现 40 条），先补测试再改。
3. **解析链路端到端**：上传 → `BackgroundTasks` → 写盘 → 落 `chapters`/`parse_notes` →
   SSE 读库推进度 → 孤儿清扫。这一段跑通就能验证 D3 D6 D7，且前端只需要①②③④屏。
4. **对话链路端到端**：会话 → Session 重建 → 选章锁定 → 流式 → `try/finally` 落库 →
   溯源与检索明细。这一段验证 D1 D2 D4 D8 D9，前端补⑤⑥⑦⑧屏。

**先做 R1 的裁决**：它影响第 2 段起所有新代码的异常写法，等写完再统一改代价更大。
