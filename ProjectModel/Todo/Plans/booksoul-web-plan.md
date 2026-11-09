---
status: done
---

# BookSoul Web — Implementation Plan

## Parent Spec / RFC

- File: `Todo/Specs/booksoul-web-spec.md`
- 上游拷问：`Todo/Grills/booksoul-web-grill.md`（D1–D9）

## Plan Overview

把现有 CLI 解析引擎包成多用户 Web 产品。**引擎不重写**，只做三处向后兼容的小改
（`config.repr=False`、`writer.dir_name`、`library.load_from_path/only_idx`），
这三处分别并入需要它们的切片，不单独成片——单独做会变成没有用户可见行为的横切。

13 个垂直切片，每片贯穿 schema → API → UI → tests，完成即可独立演示。整体分四段推进：

1. **S1** 打通空管道（账号 + 前后端骨架），解锁其余一切
2. **S2–S5** 解析侧闭环：能上传、看得见过程、能核对、能读
3. **S6–S7** 对话侧最小闭环：有大脑、能流式问答且断流不丢
4. **S8–S13** 把「信得过」做实：选章可查、引文可跳、对读、重解析自愈

范围约束：**先在本地跑通**（Grill D7）。部署、多实例、Redis 队列不在本计划内。

## Slices

### Slice 1: 骨架与账号

- **Type**: HITL
- **Blocked by**: None
- **User stories**: #1, #2, #3, #4
- **Complexity**: M

**What to build**:
一条最薄的贯穿路径，证明 PostgreSQL → FastAPI → Vue 三层接得通。注册、登录、刷新保持登录、
看到一个属于自己的空书库页（对应界面稿⓪屏「初次启动」）。所有后续切片的资源都按 `user_id` 隔离，
这一片建立那个地基。

**人工卡点（两件，都必须在写第一行业务代码前定）**：

1. **Grill R2 未裁决**：是否引入 ruff / black。项目现无任何格式化或静态检查配置，
   `backend.md` 里的 4 空格 / 双引号 / 行宽 88 是从现有代码实测统计出来的（98%–100%），
   不是工具强制的。13 个切片写完再回头统一格式，代价远高于现在定。
2. **登录 / 注册页无界面稿**（Spec Out of Scope 已列）。Spec 定为「按既有 design token 直接实现」，
   实现后需人工看一眼视觉是否成立。

**Acceptance criteria**:
- [ ] Alembic `0001_init` 建出 Spec 定义的全部 11 张表与 5 个枚举类型（含本次 Schema 变更后的最终形态）
- [ ] 注册、登录返回 access / refresh token 对；`GET /api/auth/me` 返回当前用户
- [ ] 前端刷新页面后仍保持登录；未登录访问受保护路由跳 `/login` 并记录 `redirect`
- [ ] 空书库页按界面稿⓪屏渲染，含格式列表与两条命令行说明
- [ ] 密码以 bcrypt（cost 12）存储，库中无明文；最短 8 位校验在 Pydantic schema 完成
- [ ] Python 侧格式化 / 静态检查工具的取舍已裁决并落到配置文件（或明确记录「本轮不引入」）

**Implementation hints**:
- 新建 `src/booksoul_api/`，分层 `routers → services → repositories → models`
- 新建 `web/`，Vue 3 + TS + Vite + Pinia + Vue Router
- `styles/tokens.css` 按 `frontend.md`「样式约定」的色彩语义表建立，后续所有切片复用
- 越权一律返回 404 而非 403（`backend.md`「认证授权」）
- 本片不碰引擎，`src/booksoul/` 零改动

---

### Slice 2: 上传一本 EPUB 并入库

- **Type**: AFK
- **Blocked by**: Slice 1
- **User stories**: #5, #7, #12
- **Complexity**: L

**What to build**:
拖一本 epub 进浏览器，后台解析完成后刷新书库能看到它。这是解析侧的 tracer bullet：
上传 → 落临时盘 → `BackgroundTasks` → 引擎解析 → 写盘到 `<user_id>/<book_id>-<slug>/` →
落 `books` / `chapters` / `parse_notes` → 书库列表渲染。

本片**不做** SSE 进度（S3 做），前端先用 `GET /api/jobs/{id}` 轮询兜底。

**Acceptance criteria**:
- [ ] `POST /api/books`（multipart）返回 202 与 `{bookId, jobId}`
- [ ] 目录名为 `<user_id>/<book_id>-<slug>`；同一用户上传两本同名书互不覆盖，各自独立成目录
- [ ] `books.storage_path` 唯一约束生效
- [ ] 格式按魔数判定，扩展名错误（如 epub 改名为 .txt）仍能正确解析
- [ ] 一批文件中单个失败不影响其余文件入库
- [ ] 书库列表按 `(user_id, created_at DESC)` 返回，卡片按界面稿③屏渲染
- [ ] 引擎新增测试：`write_book` 给了 `dir_name` 用它、不给保持原行为、同名书不同 `dir_name` 互不覆盖
- [ ] 引擎新增测试：`load_from_path` 按路径直取的结果等价于原 `load()`；章节文件缺失时 `warn` 后继续而非抛错
- [ ] 现有 40 条测试全部保持绿色

**Implementation hints**:
- 引擎改动两处：`write_book(book, out_root, *, dir_name: str = "")`；
  拆出 `library.load_from_path(path, *, only_idx=None)` 并让 `load()` 调用它
- `books` 行必须在解析**之前**插入（`status='parsing'`）才能拿到 id 组目录名
- `meta.json` 的中文键 → 英文列的映射复用 `library.py:_FIELD_MAP` 的思路

---

### Slice 3: 解析进度、失败态与格式能力

- **Type**: AFK
- **Blocked by**: Slice 2
- **User stories**: #6, #8, #9, #10, #11, #12, #13
- **Complexity**: M

**What to build**:
把解析过程摊开给人看。五阶段实时进度、大部头的分节进度、失败时给出用户自己能执行的补救、
服务重启后不留永久孤儿、上传前就知道这个部署支持哪些格式。

**Acceptance criteria**:
- [ ] `GET /api/jobs/{jobId}/events` 以 SSE 推 `stage` 事件；**handler 轮询数据库**（约 800ms）而非订阅进程内状态
- [ ] SSE 与 `GET /api/jobs/{jobId}` 轮询端点读同一份数据，不会给出互相矛盾的答案
- [ ] 进度显示到分节粒度（如「转换正文 41/64 节」），界面稿①屏五阶段条
- [ ] 一次拖入多个文件，各自独立进度
- [ ] 扫描版 PDF 报 `error_code='no_text_layer'`，界面以重墨整块呈现，并给出可复制的 `ocrmypdf` 命令
- [ ] `parse_jobs.heartbeat_at` 随解析推进更新；服务启动时把心跳停滞的任务标为 `orphaned`、
      `books.status='failed'`，并给出「重新解析」这一用户可执行的出路
- [ ] `GET /api/formats` 返回**运行时探测**得到的可用格式、体积上限（100 MB）、azw3 的实验性标注
- [ ] 前端拖放提示文案、文件选择器 `accept`、上传前体积与格式校验，全部由 `/api/formats` 驱动，不硬编码后缀
- [ ] 缺失可选依赖时打 ERROR 日志（给管理员）并从对外列表摘掉；用户看到的是「这个部署暂不支持 PDF」，
      而非 `pip install pymupdf`

**Implementation hints**:
- 心跳超时尺度（2 分钟 / 10 分钟 / 按章更新）随 Grill D7 推迟，本片先取一个值并留成配置项
- `parse_jobs.remedy` 只放用户自己能执行的补救（Spec N1 / Grill D6）

---

### Slice 4: 书籍详情与质检

- **Type**: AFK
- **Blocked by**: Slice 2
- **User stories**: #14, #15, #16, #17, #18
- **Complexity**: M

**What to build**:
界面稿④屏与②屏。字数条不是装饰，是质检工具——异常短的章节要能被一眼看见并点开核对。
解析报告是一本可逐条核对的账：合并了多少硬换行、剥了多少全角空格、脚注接上几条、目录来源是哪条路线。

**Acceptance criteria**:
- [ ] `GET /api/books/{bookId}` 返回元信息 + 章节树 + 字数 + 未解决的 `parse_notes`
- [ ] 目录树每章带字数条；异常短的章节（如只有 5 字的「第一部」）以黄檗色标注并提示核对
- [ ] `PATCH /api/books/{bookId}/notes/{noteId}` 把存疑项标为已核对，标过的不再打扰
- [ ] 元信息区分「原文标注」与「系统未标注」——译者字段为空时显示「未标注」而非猜测
- [ ] `GET /api/books/{bookId}/report` 返回清洗账本与产出文件清单，按界面稿②屏渲染
- [ ] 「装得进哪个大脑」一栏用 `books.word_count` 与 `brains.context_tokens` 计算

---

### Slice 5: 阅读器

- **Type**: AFK
- **Blocked by**: Slice 2
- **User stories**: #21, #22, #23, #24, #25
- **Complexity**: M

**What to build**:
界面稿⑤屏的阅读部分（溯源在 S9）。正文渲染、脚注就地可见、目录跳转、位置条、md 源路径可见。

**Acceptance criteria**:
- [ ] `GET /api/books/{bookId}/chapters/{idx}` 返回单章 Markdown
- [ ] 前端用 `markdown-it` + `markdown-it-footnote` 渲染；脚注 `[^n]` 就地展开可见，不必跳页尾
- [ ] 目录树点击跳章；当前章在目录中高亮
- [ ] 位置条显示这一章在全书的位置（第 n/m 节、前面多少字）
- [ ] 显示该章对应的 md 源文件路径，用户可据此自行取用或手工修订
- [ ] 正文排版符合中文习惯：无全角空格残留、无被误判为代码块的行

---

### Slice 6: 大脑与密钥

- **Type**: AFK
- **Blocked by**: Slice 1
- **User stories**: #47, #48, #49, #50, #51, #53
- **Complexity**: M

**What to build**:
界面稿⑧屏左半。选大脑、填自己的 key、key 只留在这台浏览器、知道这本书装不装得进。
这一片必须先于对话——没有 key 对话跑不通。

**Acceptance criteria**:
- [ ] `GET /api/brains` 返回预设列表：provider、model、上下文窗口、需要哪个 key
- [ ] key 存 `localStorage`（键名 `booksoul.brainKey.<brainName>`），输入框 `type="password"`，保存后掩码回显
- [ ] 调用模型的请求由前端在 `X-Brain-Key` 头带上；key 不出现在 URL、不进任何日志或埋点
- [ ] `BrainConfig.api_key` 设 `repr=False`；新增测试断言 `repr(cfg)` 不含 key
- [ ] 日志中间件对请求头**白名单制**（只记 method / path / status / 耗时）
- [ ] 全局异常处理器不携带 locals、不回显请求头
- [ ] 设置页文案为事实陈述：「保存在这台浏览器里，不会存到服务器。每次提问时经服务器转发给模型厂商——
      服务器只是转发，不保存、不记录。」
- [ ] 「这本书装得下吗」按 `books.word_count` 与 `brains.context_tokens` 给出结论
- [ ] 可添加自定义 OpenAI 兼容端点

**Implementation hints**:
- 引擎改动一处：`config.py` 的 `api_key: str = field(default="", repr=False)`
- 规范同步写入 `backend.md`：任何持有密钥的 dataclass 字段必须 `repr=False`

---

### Slice 7: 单人格对话流式跑通

- **Type**: AFK
- **Blocked by**: Slice 2, Slice 6
- **User stories**: #29, #30, #33, #36, #37, #38, #39
- **Complexity**: L

**What to build**:
对话侧的 tracer bullet。选一本书、选一种人格、逐字流式看到回答、历史留得住、
断流不丢已生成内容。本片选章走「整本装得下」的简单路径，完整的 ChapterSelector 在 S8。

**深模块 StreamRecorder 在本片交付**：以上下文管理器提供文本累积器，正常退出一次性持久化完整消息，
异常退出持久化残缺消息并置 `complete = false`。

**Acceptance criteria**:
- [ ] `POST /api/conversations` 开会话，锁定 book + persona + brain
- [ ] `POST /api/conversations/{id}/ask` 以 SSE 流式返回；事件 `token` / `done` / `error`
- [ ] 服务层每次请求重建 `Session`：载入书 → 从 `messages` 灌回 history
- [ ] user 消息在调用模型**之前**落库
- [ ] 正常结束一次性写完整消息（正常路径**零额外 UPDATE**）
- [ ] 中途断连或异常时，`finally` 落一条 `complete = false` 的残缺消息，保住已累积文本
- [ ] 前端对残缺消息渲染「这条回答没说完」，提供「重问」与「接着往下」两个按钮
- [ ] **重连回放已有内容，绝不重新调用模型**（同一问题不得让用户付两次钱）
- [ ] 书库首页显示「最近问过的」
- [ ] StreamRecorder 测试：正常结束落 `complete=true`；抛异常落 `complete=false` 且保住已累积文本；零 token 时不落空消息
- [ ] 模型调用在测试中用替身，不打真实 API

**Implementation hints**:
- 系统提示拼装顺序必须保持「框架语 → 全书 →（缓存断点）→ 人格」不变，它直接决定用户账单
- `ask.py:Session.ask()` 的 `history.append` 在 `chat()` 返回之后，异常即丢——服务层不能照搬这个形状

---

### Slice 8: 选章锁定、预算与检索明细

- **Type**: AFK
- **Blocked by**: Slice 7
- **User stories**: #40, #41, #42, #43, #44, #45, #46
- **Complexity**: M

**What to build**:
界面稿⑥屏。把「这次回答读了什么」变成可查的账：BM25 排名、命中词、字数、累计、预算线画在哪、
下一节差多少字装不下。

**深模块 ChapterSelector 在本片交付**：输入章节、问题、预算、已锁定集合；
输出选中集合与理由对象（整本装下 / BM25 选出 / 复用会话锁定 / 命中未载入章节需重选）。
不碰数据库、不碰模型 API。

**Acceptance criteria**:
- [ ] 第一轮选章结果写入 `conversations.selected_chapter_ids`，后续轮次原样复用，不重跑 BM25
- [ ] 后续轮次通过 `load_from_path(only_idx=...)` 只读选中的章节文件，不再全书读盘
- [ ] 按 `books.word_count` 分流：装得下走同步路径不跑 BM25；装不下走后台选章，SSE 推「正在挑章节…」
- [ ] `retrievals` 挂 `conversation_id` + `turn`，唯一约束 `(conversation_id, turn)`
- [ ] `retrieval_items` 为全书每一节各留一行（含未选中的），带 rank / score / matched_terms / selected
- [ ] `GET /api/conversations/{id}/retrieval` 返回明细；界面渲染排名表与预算线，
      并给出「下一节要 X 字，只剩 Y，差 Z 字装不下」
- [ ] 逃生门：新问题命中未载入章节的标题时提示用户，确认后才重选并更新锁定集合
- [ ] 明确告知重选会重置缓存（这一轮更贵）
- [ ] 整本装得下时 `whole_book = true`，界面说明模型拿到的是全书
- [ ] 底部仪表显示输入 / 输出 token 与缓存命中
- [ ] ChapterSelector 测试：整本装得下不跑 BM25；装不下按预算截断且返回原书顺序；
      已锁定时原样复用；命中未载入章节标题时给出需重选信号；预算连一章都装不下时至少给最相关的一章

---

### Slice 9: 溯源

- **Type**: AFK
- **Blocked by**: Slice 7
- **User stories**: #26, #27
- **Complexity**: M

**What to build**:
回答里的引文能跳回原文。界面稿⑤屏上半那条朱批色的来路条，以及正文里被引段落的标记。

**Acceptance criteria**:
- [ ] assistant 消息落库时，引文与章节正文比对定位到段落序号，写入 `citations`；对不上填 `-1`，**不猜**
- [ ] 回答中的引文块显示「去原文 · {章节名}」，点击跳 `/books/:id/read/:idx` 并高亮该段
- [ ] 阅读页显示是哪个问题把用户带过来的，并提供「回到对话」
- [ ] `GET /api/messages/{messageId}/citations` 返回该条回答的全部引用
- [ ] 被引段落用朱批色左栏标记（人的标记），不与存疑的黄檗、阻断的重墨混用

---

### Slice 10: 对读

- **Type**: HITL
- **Blocked by**: Slice 7
- **User stories**: #31, #32
- **Complexity**: M

**What to build**:
同一个问题，三种人格并排看——这是产品的立意，标签页每次只给三分之一，讲不清差别。
看完可以挑一种接着聊下去。

**人工卡点**：`frontend.md` 待确认 2 未决——对读一次是三次调用，点击前要不要弹确认？
这是产品判断不是实现细节。补充事实供决策：三次调用**共享同一份整本书的缓存**
（缓存断点在全书之后、人格之前），真实成本 ≈ 1 份书 + 3 份人格与问答，不是三倍。

**Acceptance criteria**:
- [ ] `messages` 增 `turn` / `persona`，唯一约束改为 `UNIQUE NULLS NOT DISTINCT (conversation_id, turn, persona)`
- [ ] `POST /api/conversations/{id}/ask` 支持 `compare: true`，一轮产生 1 条 user + 3 条 assistant，同 turn 不同 persona
- [ ] SSE `token` 事件带 `persona` 字段以区分三路
- [ ] 前端三列并排渲染，三种人格用填法区分身份（实朱 / 实墨 / 朱框空心），不借用状态色
- [ ] 拼 history 只取 `conversations.persona` 对应的主干那一支，旁支不进上下文
- [ ] 「跟这个继续聊」更新主干人格，旧旁支折叠可查
- [ ] 成本提示的形态按人工卡点的裁决实现

---

### Slice 11: 处境模式与人格编辑

- **Type**: AFK
- **Blocked by**: Slice 7
- **User stories**: #34, #35, #54
- **Complexity**: S

**What to build**:
「处境模式」开关，让读者把自己的困惑而不是关于书的知识问题抛给它；以及查看和修改三种人格的提示词。

**Acceptance criteria**:
- [ ] 开关置 true 时 `conversations.reflection = true`，人格提示词后附加 `REFLECTION_SUFFIX`
- [ ] `GET /api/personas` 返回三种人格；`GET/PUT /api/personas/{key}` 查看与覆写提示词
- [ ] 提示词编辑在界面稿⑧屏右栏，改完即时生效
- [ ] 提示词仍是 `prompts/` 下的三个 md 文件，**不硬编码回 Python**
- [ ] 本轮维持全局共享（Spec N5）：界面上须说明改动对所有用户生效

---

### Slice 12: 重解析与引文重定位

- **Type**: HITL
- **Blocked by**: Slice 4, Slice 9
- **User stories**: #19, #20, #28
- **Complexity**: M

**What to build**:
重新解析一本书，而不毁掉用户已有的对话。这一片处理 Grill C10 那条连锁：
会话级选章锁定 + 复用 book_id 覆盖目录，叠加出「重解析后载入 0 节 → 模型手里没书 → 悄悄瞎编」。

**深模块 CitationRelocator 在本片交付**：输入一批引文文本与一组新章节，
输出每条的 (章节, 段落序号) 或未命中。纯字符串处理，无副作用。

**人工卡点**：Grill R6 要求实测——引文重定位的命中率无人验证过。
若重解析同时改了清洗规则（如合并硬换行策略变化），子串匹配可能大面积落空。
需人看实测数字后决定是否接受这个降级方案。

**Acceptance criteria**:
- [ ] `POST /api/books/{bookId}/reparse` 复用同一 book_id 与目录，响应带受影响会话数
- [ ] 重解析前提示用户「这本书有 N 个进行中的会话会受影响」
- [ ] 重解析后清空该书所有会话的 `selected_chapter_ids`；下一轮按新章节重跑 BM25 并明确告知缓存已失效
- [ ] **服务层断言：载入章节数为 0 时直接抛错，不往模型送**（写入 `backend.md` 规范章节）
- [ ] 引文重定位：多数引文能重新定位并补上 `chapter_id` / `para_index`；未命中的降级为
      灰色按钮 + 「原位置已变更」，而非点了没反应
- [ ] CitationRelocator 测试：正文未变时全部命中；章节切分变化后仍能跨章命中；
      正文被改写后未命中且不误报；引文含全角空格 / 脚注标记时的归一化
- [ ] 实测报告：在《悉达多》上改动清洗参数重解析，记录重定位命中率

---

### Slice 13: 内容审核与错误出路

- **Type**: AFK
- **Blocked by**: Slice 7
- **User stories**: #52
- **Complexity**: S

**What to build**:
某些后端（实测：通义 DashScope）会对长篇文学内容触发审核拦截。拦截要能被识别出来，
并给出可操作的建议，而不是抛一个看不懂的 400。

**Acceptance criteria**:
- [ ] 识别出审核类拒绝（区别于认证失败、限流、模型不存在）
- [ ] 给出可操作建议：换一个大脑，或调小载入预算
- [ ] 若能定位到触发章节，在书籍详情页以重墨提示标注该章（如《悉达多》的「轮回」）
- [ ] 建议按 Spec N1 的分层规则：这是用户自己能执行的，放在用户可见的位置

---

## Dependency Graph

```
S1 ─┬─ S2 ─┬─ S3
    │      ├─ S4 ──────────────┐
    │      ├─ S5               │
    │      └──┐                │
    └─ S6 ────┴─ S7 ─┬─ S8     │
                     ├─ S9 ────┴─ S12
                     ├─ S10
                     ├─ S11
                     └─ S13
```

关键路径：`S1 → S2 → S6 → S7 → S9 → S12`。
S3 / S4 / S5 在 S2 之后可与 S6 / S7 并行；S8 / S10 / S11 / S13 在 S7 之后互相独立。

## Risk Notes

| 风险 | 缓解 |
|------|------|
| **Grill R2 未裁决**（ruff / black）会污染所有新代码的写法 | 提前到 S1 的人工卡点，写第一行业务代码前定 |
| **引文重定位命中率未知**（Grill R6）。清洗规则变化可能让子串匹配大面积落空 | S12 设为 HITL，要求在《悉达多》上实测并出数字；不达预期则退化为「一律标记原位置已变更」 |
| **断流续写效果不稳**（Grill R5）。把残文塞回 assistant 角色让模型续写，多数模型支持但质量不保证 | S7 先实现两个按钮，效果不佳则砍掉「接着往下」只留「重问」 |
| **缓存是成本结构核心**。任何改动系统提示拼装顺序的行为都直接体现在用户账单上 | S7 验收明确锁定拼装顺序；S8 验收核对缓存命中数 |
| **心跳超时尺度未定**（Grill R7）。大部头单阶段可能跑很久，超时太短会误判为孤儿 | S3 先取一个值并留成配置项，随 Grill D7 一并推迟决策 |
| **人格提示词全局共享**（Spec N5）与「多用户各自书库」的立意相悖 | 本轮 Out of Scope，S11 验收要求界面显式说明「改动对所有用户生效」；多用户上线前必须回头建表 |
| S2 标 L，同时含引擎两处改动与第一条完整上传链路 | 保持不拆——单独切引擎改动会变成无用户可见行为的横切，违背垂直切片原则 |
| 本地 `uvicorn --reload` 存文件即重启，会同时触发孤儿解析（S3）与断流（S8/S7） | 这两条不是为生产设计的，是本地每天都会踩的，因此都排进了计划而非推迟 |

## Status Tracking

| Slice | Status | Assignee | Notes |
|-------|--------|----------|-------|
| 1 骨架与账号 | 🟢 Done | - | R2 已裁决（引入 ruff 全仓统一）。登录页视觉待人工过目。偏差见下 |
| 2 上传入库 | 🟢 Done | - | 引擎 writer.dir_name / library.load_from_path 已落地。only_idx 语义见下 |
| 3 进度与失败态 | 🟢 Done | - | 引擎增第 4 处改动（on_progress）；反向做了 S12 的 reparse 骨架 |
| 4 详情与质检 | 🟢 Done | - | 引擎增 Book.stats；账本只列真数出来的项 |
| 5 阅读器 | 🟢 Done | - | 引擎零改动。脚注就地展开、看 Markdown 源 |
| 6 大脑与密钥 | 🟢 Done | - | 引擎增 #7 repr=False、#8 各模型上下文窗口 |
| 7 单人格对话 | 🟢 Done | - | StreamRecorder 已交付。引擎增 #9 截断检测 |
| 8 选章与预算 | 🟢 Done | - | ChapterSelector 已交付。引擎增 #10 plan_selection、#11 preselected_idx、#12 stated_words |
| 9 溯源 | 🟢 Done | - | 引擎零改动。定位规则「归一化之后的精确包含」，对不上填 -1 |
| 10 对读 | 🟢 Done | - | 两条人工卡点已裁决（不弹确认 / 每轮取一条）。StreamRecorder 改为多轨 |
| 11 处境模式与人格编辑 | 🟢 Done | - | 引擎零改动。深模块 PromptStore（占位符体检 + 原子写 + 出厂基线） |
| 12 重解析与引文重定位 | 🟢 Done | - | 引擎零改动。深模块 CitationRelocator。实测已裁决（接受降级方案 / 账本写一行） |
| 13 内容审核出路 | 🟢 Done | - | 引擎增 #13 `BrainError.kind`。新表 moderation_events：交集 − 并集把触发章节收窄到一节 |

Status: 🔴 Not started | 🟡 In progress | 🟢 Done | ⚠️ Blocked


---

## Slice 1 完成记录（2026-09-16）

### 裁决

| 项 | 结论 |
|----|------|
| Grill R2 · 格式化与静态检查 | **引入 ruff，全仓统一**。配置在 `pyproject.toml`：line-length 88 / 双引号 / 4 空格 —— 与 `backend.md` 实测统计值一致。前端对应 ESLint + Prettier（frontend.md 既有约定），排版归 Prettier，ESLint 只管正确性 |
| 数据库 | Docker `postgres:16` 容器 `booksoul-pg`，端口 5432，开发库 `booksoul` / 测试库 `booksoul_test` |
| 测试边界 | 纯逻辑（`core/security`、Pydantic schema）+ 认证四个端点，其余按 Spec 手工走查 |

### 相对设计文档的偏差（**影响下游切片，需回写 `sql.md`**）

`0001_init` 相对 `Design/database/sql.md` 基线有下列刻意偏离。每条都在迁移脚本里就地注明了理由。

| # | 变更 | 为什么 | 影响 |
|---|------|--------|------|
| 1 | `books.storage_path` 由 `NOT NULL` 改为**可空**，UNIQUE 具名为 `uq_books_storage_path`，加 `ck_books_ready_has_path` | 路径是 `<user_id>/<book_id>-<slug>`，含自身主键；IDENTITY 主键 INSERT 之前拿不到。NOT NULL 逼出字面量占位，UNIQUE 立刻把同时在途的第二本书挡成 23505 —— S2「一次拖入多个文件」必然撞上 | **S2**：建行时 storage_path 留 NULL，落盘后同事务回填 |
| 2 | `books.source_sha256` 由 `CHAR(64)` 改为 `VARCHAR(64)`，加形状 CHECK | 实测：`CHAR(64) DEFAULT ''` 落库是 64 个空格。SQL 侧 `= ''` 仍为真（bpchar 忽略尾随空格），Python 侧 `not v` 为假、`v == ""` 为假 —— 去重提醒的判空分支会静默走错边 | S2 计算哈希时不必再做 strip |
| 3 | `messages` 的 `(conversation_id, turn, persona)` 唯一性落成**部分唯一索引** `WHERE complete`，而非表级约束 | 表级约束会让断流残文（`complete=false`）占死坑位，用户点「重问」就是 23505，服务层只剩「删掉残文」一条路 —— 那恰好毁掉 S7 验收「已生成的部分被保住」。PostgreSQL 表约束语法不支持 WHERE，只能用索引。已实测 PG16 上 `NULLS NOT DISTINCT` + `WHERE` 可共存 | **S7**：重问是同轮再 INSERT 一条，残文保留 |
| 4 | 保留 `uq_messages_conv_seq`（Spec 字面是「唯一约束**改为**」） | seq 是拉历史的物理全序地基，与新约束数学上可同时满足。删约束是削弱 | **S10**：对读三路并发落库时 seq 必须在同一事务内分配，禁各算各的 `max(seq)+1` |
| 5 | 加 `ck_messages_persona_matches_role` | 「user 的 persona 为 NULL、assistant 非 NULL」原本只是注释，而部分唯一索引的 NULLS NOT DISTINCT 语义整个建立在它之上。S7 早于 S10 落地，那时很容易把 assistant 写成 persona NULL，它会和同轮的提问撞索引 | **S7**：assistant 消息必须写 persona，哪怕还没有对读 |
| 6 | 加 `parse_jobs.cleanup_stats JSONB` | S4 验收要的「清洗账本」（合并多少硬换行、剥多少全角空格、脚注接上几条）在基线里没有任何落点，而这些数字在引擎里是瞬时的 | **S3/S4**：解析过程中填，报告页读它 |
| 7 | 加 `retrieval_items.chapter_idx` / `chapter_title`、`citations.chapter_title` 快照 | `chapter_id` 走 `ON DELETE SET NULL`，重解析后那一行只剩 rank/score，⑥屏排名表会变成一列没有名字的匿名行 —— 而「读了哪几节可查」正是产品的信任基础 | **S8/S12**：写留档时一并写快照 |
| 8 | `brains` 加 `ck_brains_api_key_env_is_identifier` 与 `ck_brains_base_url_no_userinfo` | 「库里绝不出现 API key」这条硬规则原本只落在注释上，而注释挡不住 INSERT。实测：贴 `sk-ant-api03-…` 进 api_key_env、或 `https://sk-xxx@host/v1` 进 base_url，现在都会被数据库拒绝；合法的 `DASHSCOPE_API_KEY` + DashScope 地址照常通过 | **S6**：查询串里的 key（`?api-key=`）没挡，因为 Azure 的 `?api-version=` 合法，这一侧留给服务层 |
| 9 | 新增 `touch_updated_at()` 触发器 + 8 张表的 BEFORE UPDATE 触发器 | `DEFAULT now()` 只在 INSERT 时生效。`idx_conversations_user (user_id, updated_at DESC)` 服务的正是③屏「最近问过的」—— 服务层漏刷一次，列表静默退化成创建时间序，不报错、只是排错 | 全体：ORM 侧**不要**再配 `onupdate`，两边都写就是两份真相 |
| 10 | 补 `created_at` / `updated_at`（`parse_jobs` / `parse_notes` / `brains` / `citations` / `retrieval_items`）；`parse_jobs.started_at` 改为可空 | 基线违反自己「约定」第 5 条。`created_at`（入队）与 `started_at`（开跑）分开，S3 的孤儿清扫才能区分「排上队没起来」和「跑起来又停滞」 | **S3** |
| 11 | 删除 `idx_chapters_book`、`idx_messages_conv` | 与同表唯一约束的隐含索引列与列序完全相同，纯冗余。这两张恰是全库写入最热的表 | — |
| 12 | 补值域 CHECK（seq/turn/rank/idx ≥ 1）与三处 JSONB 数组形状 CHECK | 注释都写「1 起」，DDL 却允许 0 和负数 | — |

其余偏差：`alembic.ini` 必须保持纯 ASCII（configparser 按系统区域编码读它，中文 Windows 上是 GBK）；迁移用 `exec_driver_sql` 而非 `op.execute`（后者会把注释里的 `{"count":14}` 当成绑定参数 `:14`）。

### 未采纳但必须记下的发现

**`conversations.persona` 与 `messages.persona` 语义打架（留给 S10 裁决，不在 S1 替它定）。**
S10 两条验收同时成立时会互相拆台：「拼 history 只取 `conversations.persona` 对应的主干那一支」+「『跟这个继续聊』更新主干人格」。
具体失败：单人格聊 4 轮后切到 author，切换前的所有轮次集体落选主干，历史从 4 条塌到 2 条——**界面上历史还完整显示着，没有任何报错**。
候选解法是给 `messages` 加一列 `main_branch BOOLEAN`（历史属于哪一支是消息自己的属性），或改 S10 的验收标准（拼 history 不按人格过滤，旁支另行折叠）。
这是 Spec 与 Plan 都没点名的结构性改动，按「不替下游切片做设计裁决」的原则留到 S10，届时是一个独立 revision。

### 待回写设计文档（交 `design-doc-sync` 收尾时处理，不在本片改）

- **`sql.md`**：11 个 DDL 代码块整体替换为 `0001_init` 的最终形态；「迁移记录」表补上本次内容
- **`database-relations.md`**：`retrievals` 改锚后 `messages 1:1 retrievals` 整条失效，应为 `conversations 1:N retrievals`；书库路径由 `<书库根>/<user_id>/<slug>/` 改为 `<user_id>/<book_id>-<slug>/`
- **`data-er.md`**：`messages |o--|| retrievals` → `conversations ||--o{ retrievals`；retrievals 实体块的 `message_id` 换成 `conversation_id` + `turn`；messages 补 `turn` / `persona` / `complete`；conversations 补 `selected_chapter_ids`
- **`backend.md` 架构章节**：`GET /api/messages/{messageId}/retrieval` → `GET /api/conversations/{id}/retrieval`；「待确认问题 3（prompts 多用户归属）」已由 Spec N5 裁决、「待确认问题 4（书籍去重）」已由 Grill D3 裁决，两条应改为已裁决

### 规范候选（auto 模式累积，全部切片完成时一次性交裁定）

1. **ruff 全仓统一** → `backend.md`「代码格式化」把「格式化 / 静态检查工具：待确认」替换为已裁决（`pyproject.toml [tool.ruff]`，忽略 E501 / B008 / SIM105 并各附理由）
2. **`updated_at` 由数据库触发器唯一负责**，ORM 侧不配 `onupdate` → `backend.md`「注释与事务」
3. **`messages.seq` 确定性分配**：持 `conversations` 行锁发放，禁 `max(seq)+1` → `backend.md`「设计模式与约定」
4. **承载数组的 JSONB 列用复数**（`selected_chapter_ids` / `query_terms` / `matched_terms`）→ `sql.md`「约定」的「列名 snake_case 单数」需要这条例外，否则下一个照约定审查的人还会再撞一次
5. **configparser 读的配置文件保持纯 ASCII**（`alembic.ini`），中文说明写进对应的 `.py` → `backend.md`「代码格式化」
6. **N1 异常消息按层区分**（Spec 已裁决，`backend.md` 待确认问题 1 需回写为已裁决）

### 本片留白（已就地注明，不是遗漏）

- `EmptyLibrary.vue` 的格式列表暂时写死 —— **S3** 接上 `GET /api/formats` 后按运行时探测驱动
- 上传入口置灰 —— 上传链路是 **S2**
- 书库有书时是一个朴素列表 —— ③屏四种状态的 `BookCard` 随 **S2** 一起做
- 图标栏除书库外的项渲染为非交互（界面稿里它们本就是非激活态），对应路由随各切片补上


---

## Slice 2 完成记录（2026-09-16）

### 引擎两处改动（签名向后兼容，CLI 那条路零影响）

| 模块 | 改动 |
|------|------|
| `writer.py` | `write_book(book, out_root, *, images_from=None, dir_name="")`。不给 `dir_name` 仍按书名取目录，CLI 行为不变 |
| `library.py` | 拆出 `load_from_path(path, *, only_idx=None)`，`load()` 改为调用它 |

### **影响 S8 的语义决定：`only_idx` 保留未选中章节的空壳**

Spec 的字面是「`only_idx` 只读指定章节」。最直觉的实现是返回值里只有选中的那几章——
**我没有那样实现**，未选中的章节仍然留在 `book.chapters` 里，只是 `content` 为空。

理由是 `as_context()` 的形状：它靠 `book.chapters` 渲染**全书目录**并给未载入的章节
打上「（本次未载入全文）」，还会追加一句「不要凭目录名臆测内容」。若把没选中的章节
整个丢掉，那份目录就变成了残缺的全书目录，而模型无从知道——它会以为手里这几节就是
全书，然后对着残缺目录凭章节名编内容。那正是 Grill C10 那条连锁要防的终点。

**S8 实现 ChapterSelector 时据此写**：
```python
book = load_from_path(path, only_idx=selected_idx)
loaded = [c for c in book.chapters if c.content]
context = as_context(book, loaded)  # 目录完整，未载入的自动标出来
```
另外 `only_idx` 收的是 `chapters.idx`（1 起的阅读序号）**不是 `chapters.id`**，
而 `conversations.selected_chapter_ids` 存的是 id —— 中间必须 JOIN 一次换算。
一节都没命中时 `load_from_path` 直接抛错，不返回一本空书。

### 本片其余产出

- `services/importer.py`：解析这一步**不碰数据库也不碰 HTTP**，文件进、目录出。
  失败归一成 `ImportFailed(code, remedy)`，引擎的中文错误按「用户自己能做什么」翻译一次。
  `dir_name` 可以给一个函数 —— 目录名要同时含 book_id（建行时有）与真书名（解析完才有）。
- `services/parse_service.py`：DB 编排。解析跑在 `asyncio.to_thread` 里，
  否则同步的 CPU + 磁盘活会把事件循环钉住。
- `POST /api/books`（202）、`GET /api/jobs/{jobId}`（轮询，S3 换 SSE 读同一份记录）
- `BookOut` 增 `sourceFormat` / `noteCount` / `errorCode`，供③屏四种卡片状态
- 前端：`BookCard`（就绪 / 存疑 / 解析中 / 停下了）、整个内容区可拖放、
  `useLibrary` 只在有书解析中时轮询（判据是书库状态而非会话记住的 jobId，刷新页面照样接得上）

### 实测走查

伪装成 `.txt` 的 epub → 按魔数认出 epub，16 节 48,155 字，落在 `library/3/1-悉达多/`；
同一用户再传一本同名书 → 两个独立目录，互不覆盖；
扫描版 PDF → `error_code=no_text_layer` + 可复制的 ocrmypdf 命令，不留半成品目录；
别人的解析进度 → 404（不是 403）；别人的书库 → 0 本。

### 留白（已就地注明）

- 卡片上的进度是一段来回走的条，不是真进度。分节粒度（「转换正文 41/64」）是 **S3**
- ⓪屏格式列表仍写死。**S3** 接上 `GET /api/formats` 的运行时探测后删掉那个常量
- 失败卡片只给短语，完整的补救命令留给 **S3** 的①屏


---

## Slice 3 完成记录（2026-09-16）

### 九条验收全部达成

SSE `GET /api/jobs/{jobId}/events`（handler 轮询数据库，0.8s）· 与轮询端点读同一份记录 ·
分节进度 · 多文件各自独立 · 扫描版 PDF 的重墨块与可复制命令 · 心跳与启动清扫 ·
`GET /api/formats` 运行时探测 · 前端不硬编码后缀 · 缺依赖走 ERROR 日志而非用户界面。

### 引擎第 4 处改动（Spec 只列了三处）

`parsers.parse(path, *, keep_images=True, on_progress=None)`，逐节回报进度：
epub 按 spine 文件、pdf 按页。关键字参数、默认 None，CLI 那条路零影响
（产物仍逐字节一致，只差解析时间戳）。

**没有这处改动，S3 的「进度显示到分节粒度」做不出来** —— 解析在引擎里是个黑盒，
外面只能编一个假的百分比，而那比没有进度更误导人。

顺带修掉一个坑：`pdf.parse(path, **_)` 会**静默吞掉**任何关键字参数，传了
`on_progress` 也不报错、只是不生效。现在它是具名参数。

### 反向做进了 S12 的一部分：`POST /api/books/{bookId}/reparse`

S3 的验收原文要求孤儿任务「给出**用户可执行的**出路」。没有这个端点，界面上那句
「重新解析」就是一句空话，而那正是 S1 里被否掉的「点了没反应的按钮」。
所以本片实现了最朴素的一层：用原始文件重跑，复用同一个 `book_id` 与同一个目录。

**S12 仍然拥有**：重解析前提示「有 N 个进行中的会话会受影响」、清空
`selected_chapter_ids`、以及引文重定位与命中率实测。

### 由此带出的一个新约束：**原始上传文件必须留着**

重解析要从原始文件开始，而 S2 的实现是解析完就把临时文件删了。现在它落在
`uploads/<user_id>/<book_id>/<原文件名>`，路径**算出来**而不是存一列
（`user_id` 与 `id` 都在行上，再存一列只会多一处可能对不上的真相）。

影响：**将来做「删除书籍」时要同时删 `library/` 与 `uploads/` 两处**，
只删一处会留下孤儿文件。这条记在这里，做删除那一片时回来看。

### 相对验收字面的一处偏离：启动清扫不看心跳新旧

验收写的是「把**心跳停滞**的任务标为 orphaned」。实现是：**进程刚起来，那么任何
一个还没收尾的任务都已经没人在跑了** —— 单实例（Grill D7）下这个判据更干脆也更对。
按心跳新旧判会漏掉一种情况：上一个进程刚写完心跳就被 kill，那本书会卡到超时之后。

`heartbeat_at` 仍然一路维护着（实测泵在解析中途确实写进了 `convert 20/64`）。
多实例部署时判据才要换成「心跳停滞超过 N 秒」，**Grill R7 的超时尺度到那时才真正需要裁决**，
现在值留在 `parse_orphan_timeout_seconds` 配置项里没人用。

### 一个走查看不出、靠测试才能分开的问题

手工走查里阶段轨迹只有 `read → write`，中间三个阶段没出现。这既可能是「书太快」
（《悉达多》150ms 就解析完，快过 0.5s 的落库节拍），也可能是「泵根本没工作」。
走查分不开这两件事，所以加了 `test_解析中途的进度就会落库而不是只在结束时出现`：
用一个慢的假解析，在解析进行中从**另一个 session** 读，确认读到的是 `convert 20/64`。

### 规范候选追加（累积到全部切片完成时一次性裁定）

7. **工作线程绝不碰 `AsyncSession`**。跨线程的进度用几个标量传递，落库由事件循环
   侧一个独立 session 负责 —— 跨线程共用 session 会变成偶发、难查、看起来像
   数据库抽风的 bug。→ `backend.md`「注释与事务」
8. **SSE handler 轮询数据库，不订阅进程内状态**。订阅看起来更快，但解析跑在另一个
   任务里，两边的真相迟早分叉，而分叉之后进度条和刷新后的状态就对不上。
   → `backend.md`「接口与响应」
9. **面向用户的补救与面向管理员的补救分两条路**（Spec N1 的延伸）：用户做不了
   `pip install`，缺依赖的安装命令走 ERROR 日志；用户看到的是「这个部署暂不支持 PDF」。
   → `backend.md`「日志」

### 留白

- ①屏完成那一行的「看解析报告」暂时指回书库 —— ②屏是 **S4**，指一个 404 比没链接更糟
- 存疑条目在①屏还没展开显示（界面稿里那块黄檗面板），随 **S4** 的质检一起做


---

## Slice 4 完成记录（2026-09-16）

### 六条验收全部达成

`GET /api/books/{id}`（元信息 + 目录树 + 字数 + 存疑）· 字数条标黄异常短的章节 ·
`PATCH .../notes/{id}` 核对后不再打扰 · 元信息区分「原文标注」与「未标注」 ·
`GET .../report` 清洗账本与产出清单 · 「装得进哪个大脑」。

实测正好命中界面稿当初预言的那个例子：**「第一部」「第二部」各 5 字**，
自动标黄并落成两条带参照（「全书中位数 3,659 字」）的存疑。

### 引擎第 5 处改动：`Book.stats`

解析过程中各处记一笔（目录来源走 nav 还是 ncx、spine 多少个文件、剔了几个目录页），
供②屏逐条核对。默认空字典，不写进 `meta.json`，CLI 那条路零影响。

顺带把两个**本来就返回计数、却一直被丢掉**的返回值接了起来：
`clean.drop_duplicate_headings(book) -> int` 与 `footnotes.normalize_book(book) -> int`。

### 引擎第 6 处改动：`ask._budget_chars` → `ask.context_budget_chars`

④屏要回答「这本书装得进哪个大脑」，而那个答案**必须和选章时用的判据来自同一张表**。
在 Web 层复制一份的话，「界面说装得下、选章说装不下」的那天一定会到。

改名而不是直接公开原名，是因为 `Session` 有个同名字段、`open_session` 有个同名参数，
重名会在那两处把函数遮掉 —— 谁将来在里面调它就是一个 TypeError。

### 账本的形状：**只列真数出来的项**

②屏界面稿上有八行（合并硬换行数、全角空格行数、剥离命名空间数、站内链接转换数……），
本片只填得出其中五行加「读进来的」那一段。**缺的那几行整行不出现，而不是填 0。**

填 0 会被读成「一处都没动」，那是假话 —— 而这份报告的全部价值就在于它没有假话。
界面上那句脚注把这条说明白了。

要补齐其余几行，得给 `html2md` / `xmlutil` / `clean.tidy` 都加计数（其中 `tidy` 那几项
只对 TXT/PDF 有意义，epub 走的是 html2md）。这是一次独立的引擎改造，不在本片强行塞进来。

### 一处被约束抓住的自造错误

`test_别人的书详情与报告都取不到` 第一版建了一行 `status='ready'` 却没有
`storage_path` 的书，被 S1 加的 `ck_books_ready_has_path` 当场拒绝。
那条 CHECK 正是用来挡「看起来解析成功了其实什么都没写」的 —— 它先挡住了我自己的测试夹具。

### 留白

- ④屏的「开始对话」「打开阅读」两个按钮**没有渲染**：对应 S7 / S5，
  点了没反应的按钮比暂时没有按钮更糟（与 S1 的判断一致）
- ②屏「被拦过」那条重墨提示（内容审核定位到某一节）属于 **S13**

---

## Slice 5 完成记录（2026-09-16）

### 六条验收全部达成

`GET /api/books/{id}/chapters/{idx}` 返回单章 Markdown · `markdown-it` +
`markdown-it-footnote` 渲染且脚注**就地展开** · 目录树跳章与当前章高亮 ·
位置条（第 n/m 节、前面多少字）· md 源文件路径可见 · 中文排版无残留。

实测（《悉达多》第 3 节）：

```
GET /api/books/7/chapters/3   200
  《悉达多》婆罗门之子   第 3/16 节 · 3,710 字
  前面 259 字 · 上一节 2 · 下一节 4
  源文件 library/7/7-悉达多/chapters/003-婆罗门之子.md
  11 处脚注标记 · 渲染后 typographer 没动过中文引号
第 1 节 prev=None · 第 16 节 next=None · 第 99 节 → 404「它一共 16 节」
```

### 引擎改动：**零**

这一片什么都没动引擎。写在这里是因为前四片每片都动了一到两处，容易被当成趋势 ——
阅读器要的东西（章节文件、标准 Markdown 脚注、字数）解析侧早就产出了。

### 这一屏的承诺：屏幕上的字 = 磁盘上的文件

`markdown` 字段是文件原文，**一个字没动**：不 strip 标题、不吃掉那句
`> 悉达多 · 第 3/16 节 · 3,710 字`、不重新排版。底下那行源文件路径因此是
一句可验证的话，而不是好听的场面话。界面上的「看 Markdown 源」就是验证方式：
一键切到无高亮的纯文本，和照着路径打开的文件逐字对得上。

代价是正文里会多出那一行文件自带的题注（界面稿的节信息栏也说了一遍同样的数字）。
两处重复胜过一处删改 —— 删掉它，上面那句承诺就不成立了。

### 脚注不放页尾

`markdown-it-footnote` 默认在文末堆一个 `<section class="footnotes">`。读到
「阿特曼[3]」想知道那是什么，跳到页尾再跳回来，这一下就把一段话读断了 ——
而那正是⑤屏要消掉的那次跳转。

所以 `footnote_ref` 渲成 `<button class="fn-ref">`（不是 `<a>`：它不去任何地方，
用链接的话键盘用户按下去会跳走），注文抽成数据，由界面在**它出现的那一段下面**
就地展开一张卡。注文本身一个字没改，只是换了个位置出现。

卡片上那行「原书的注 · 在文件里是 `[^3]`」是可验证的说法，不是来源断言 ——
用户打开那个 md 就能找到 `[^3]`。

### 两个中文排版的坑，各守在该守的地方

- **行首 4 空格会被当成代码块、全角空格缩进会污染正文**：后端加了一条测试，
  遍历全书 16 节断言送到渲染器手上时没有这两种行（`test_正文对中文排版是安全的`）
- **typographer 必须关**：它把引号换成「更漂亮的」样子，那是给英文设计的；
  中文的“”本来就是对的，替一遍只会替坏。`breaks` 同理保持 false ——
  硬换行 `clean.py` 里已经接过了，再把单换行变 `<br>` 等于把它拆回去
- 顺带：`--mark-bg` / `--text-prose` 两个 token 补进 `tokens.css`，
  组件里不写字面色值这条保持不破
- ESLint 的 `no-irregular-whitespace` 抓到我自己在 Vue 模板里用 U+3000 排版。
  改成 flex `gap` —— 看不见的字符不该承担布局职责，它会跟着复制粘贴跑

### 安全：数据库里的路径不能直接拼进 open()

`chapters.file_path` 平时是引擎写的、老实的相对路径。但它终究是一条**存在库里**的
字符串，无条件拼进 `open()` 等于把「读服务器上任意文件」的开关交给数据库的写入方。
`_resolve()` 在 `resolve()` 之后判断是否还在书目录内（`..` 与绝对路径都要到那一步
才现形，拼接前查字符串里有没有 `..` 绕过方式太多），并且**不回显**那条越界路径。

### 索引说有、磁盘说没有 → 409，不是空正文

返回空字符串是最省事也最有害的做法：用户会读成「这一章本来就没内容」，
然后带着这个错判去重新解析一本其实解析得好好的书。实测：

```
文件被移走后 -> 409
  这一节的正文文件不在了：chapters/003-婆罗门之子.md。
  多半是文件被移动或删掉了 —— 在书籍详情页点「重新解析」可以照着原始文件重写一遍。
```

用 409 而不是 404：404 会被读成「这一节本来就不存在」，可它明明在目录里。

### 规范候选追加（累积到全部切片完成时一次性裁定）

10. **存在数据库里的路径，必须 `resolve()` 之后校验仍在预期根目录内，才能拼进
    文件操作**。拼接前检查字符串里有没有 `..` 不算数（绕过方式太多），越界时
    不回显那条路径。→ `backend.md`「认证授权」
11. **Vue 模板里不用全角空格（U+3000）做排版**，间距一律交给 flex `gap` /
    padding。看不见的字符不该承担布局职责 —— 它会跟着复制粘贴跑，而且
    ESLint `no-irregular-whitespace` 本来就会拦。→ `frontend.md`「设计模式与约定」
12. **Markdown 渲染一律 `html: false` + `typographer: false` + `breaks: false`**。
    前者让原文里的标签只会被转义显示；后两者是中文排版的硬约束（智能引号会替坏
    中文的“”，`breaks` 会把 `clean.py` 接好的硬换行重新拆开）。
    → `frontend.md`「设计模式与约定」

### 留白

- ⑤屏顶上那条**朱批色「从对话跳来」来路条**属于 **S9**（溯源），本片不渲染
- 节信息栏的「就这一节提问」属于 **S7**，同样不渲染
- 图标栏「阅读」那一格做成**只指示位置、不做入口**：阅读永远是「某本书的某一节」，
  没有一个不带书的入口。它在阅读时亮起，但不可点
- `renderChapter()` 依赖 `DOMParser`，没有对应的自动化测试（本轮测试边界为
  「纯逻辑 + 认证端点，其余手工走查」）。markdown-it 那一段已在 Node 里单独跑过
  真实章节验证：11 处脚注标记、页尾 section 结构、引号未被改写

---

## Slice 6 完成记录（2026-09-16）

### 九条验收全部达成

`GET /api/brains`（provider / model / 上下文 / 需要哪个 key）· key 存
`localStorage.booksoul.brainKey.<name>` 且 `type="password"` + 掩码回显 ·
`X-Brain-Key` 由 `api/client.ts` 统一注入 · `BrainConfig.api_key` 设 `repr=False` ·
日志中间件白名单制 · 异常处理器不带 locals 不回显请求头 · 设置页文案为事实陈述 ·
「这本书装得下吗」按 word_count 与上下文额度 · 可添加自定义 OpenAI 兼容端点。

实测：

```
GET /api/brains?bookId=7   200   （请求带着 X-Brain-Key）
  claude      claude-opus-5             900,000  整本装得下，而且能缓存住不重复计费
  qwen        qwen-plus                 110,000  整本装得下
  kimi        moonshot-v1-128k          108,000  整本装得下
  deepseek    deepseek-chat              54,000  整本装得下
  ollama      qwen2.5:14b                27,000  装不下，要挑章节 —— 额度约够这本书的 56%
  响应里有没有 key 字段：False

POST /api/brains
  正常的本机端点        201  书房那台 · 额度 27,852 字 · 要 key=True
  地址里夹带 key        400  端点地址里带着 key……key 填在下面那个框里
  userinfo 段夹带       400  同上
  真 key 填进环境变量名  400  那一栏填的是名字（例如 DASHSCOPE_API_KEY）
  和内置预设重名         409  「claude」和一个内置预设重名
  Azure 合法查询串       201  ?api-version=2024-02-01 不误伤
```

### 引擎第 7 处改动：`BrainConfig.api_key` → `field(repr=False)`

`repr()` 会在四个地方被自动调用：日志里的 `%r`、异常回溯里的局部变量、
pytest 断言失败时打印的对象、调试器的变量面板。每一处都是「没人打算打印 key」
却把 key 打印了出来。

### 引擎第 8 处改动：`_CONTEXT_BUDGETS` 按模型给出真实窗口

**这是一个潜伏的正确性问题，不只是显示问题。** 原来的表只认 claude，其余一律
落到兜底的 100,000 字。而 deepseek-chat 的窗口是 64K token —— 按 100,000 字选章
会选出塞不进去的量，那是 S7/S8 真跑起来才会炸的一发。

现在按各家公布的窗口（取偏保守的一档）折算，`_RESERVE = 0.85` 给框架语、人格、
多轮历史和回答本身留地方。新增 `budget_from_context_tokens()`：自定义端点填的是
厂商文档上的窗口，折算规则必须和上面那张表同一条，否则两种大脑的「装不装得下」
就不是一把尺子量出来的。

claude 那两档保持 900,000 不变（原值就是手工写的 1M 窗口折算）。
认不出的模型仍是 100,000，比 `_RESERVE` 更保守 —— 不知道对面是什么就别顶格用。

### 与界面稿的一处有意偏离：那一列标的是**正文额度**，不是上下文窗口

界面稿⑧屏那一列写「上下文 131k / 128k / 64k」，是厂商公布的 token 窗口。
这里标的是折算后的**正文字数额度**（110,000 / 108,000 / 54,000）。

理由：那一列右边紧挨着「这本书装不装得下」，而那个判断用的就是额度。
标窗口就等于让两个相邻的格子用两把尺子，读的人得自己在心里换算一次。

### 界面稿另外两种状态没做，因为它们要真发一次请求才知道

界面稿上还有「装得下，但整本发过去会被内容审核拦」（S13）和「key 有了，但当前
IP 没权限 —— 去控制台放行」（要真调一次模型，S7）。**一个猜出来的状态比没有
状态更糟**，所以这一片只显示算得出来的那一种。

### 一处自己写出来又被自己抓住的泄漏

「把真 key 填进环境变量名那一栏」这条分支，第一版的报错是
`f"「{env}」不像一个环境变量名……"` —— 它**把用户刚粘进来的那串东西原样抄回了
响应体**。而这条分支存在的理由恰恰是「有人往这一栏粘了 key」。

原样回显意味着它会经响应体落进前端的错误提示、浏览器控制台，以及任何错误上报
管道。改成不回显，并补了一条断言钉住。

### 一个框架默认打开的洞：uvicorn 自带的访问日志

白名单中间件写得再克制也没用 —— uvicorn 默认那条访问日志记的是**完整 URL**：

```
INFO: 127.0.0.1 - "GET /api/brains?api-key=sk-… HTTP/1.1" 200 OK
```

只要它还在，「key 不进日志」就是一句空话，而它是**不做任何事就开着**的。
`core.logging.configure()` 把它关掉，换成我们自己那条只到 path 为止的：

```
INFO:     GET /api/health 200 28ms        ← 带 ?api-key= 与 X-Brain-Key 请求之后
里面出现过 key / cookie 吗：0
```

四样信息（method / path / status / 耗时）一样不少，少的只有 query string
和客户端端口。

### 自定义端点的两个判断

- **`needs_key` 对自定义端点默认为真**，除非地址指向本机（localhost / 127.0.0.1
  / `.local`）。判错的两个方向代价差得很远：多出一个填了也没用的输入框只是噪音，
  少了那个输入框就是一条没有出路的死胡同
- **`key_in_url()` 宁可漏判也不误判**。误判会让一个正常端点加不进来而用户没有
  别的出路；漏判只是少一层提醒，key 本来也不会被存下（库里还有两条 CHECK 兜着）。
  判据两条：参数名像密钥（key / token / secret…），或值是一长串 URL-safe 字符
  （≥20 字符；Azure 的 `2024-02-01` 只有 10 个）

### 留白

- **`X-Brain-Key` 的往返只在 S7 才被真正走一遍**。这一片把前端注入（`request()`
  的 `brain` 选项，key 只从 localStorage 取、只走请求头）与服务端的两条「不泄漏」
  都做好并测好了，但还没有任何一个端点需要这个头 —— 第一个是 S7 的提问
- **没有「试一下这个 key 通不通」按钮**。界面稿上那些 key 状态要真调一次模型，
  那是 S7 的大脑装配；提前做会把模型调用这件事拆到两片里
- ⑧屏右半的**人格编辑**是 **S11**，本片左半按全宽排；「两条路」那一栏保留
- 内置预设仍**不落 `brains` 表**（沿用 S4 的判断）：表只装自定义端点。
  代价是 `brains.context_tokens` 只对自定义端点有意义，验收条目里那句
  「按 `brains.context_tokens`」按其意图（每个大脑各自的窗口）达成

### 规范候选追加（累积到全部切片完成时一次性裁定）

13. **任何持有密钥的 dataclass 字段必须 `repr=False`**（计划里已写明，此处确认）。
    → `backend.md`「设计模式与约定」
14. **日志白名单制**：请求日志只记 method / path / status / 耗时，且必须一并关掉
    框架自带的访问日志（它记完整 URL）。黑名单挡不住下一个还没人想到的密钥头。
    → `backend.md`「日志」
15. **校验失败的报错不回显被校验的值**，当那个值可能是密钥时。报错要说清该填什么、
    该填到哪里，而不是把用户填错的东西抄回去。→ `backend.md`「接口与响应」

---

## Slice 7 完成记录（2026-09-17）

### 十一条验收全部达成

`POST /api/conversations` 锁定 book+persona+brain · `POST .../ask` SSE（`token`/`done`/`error`）·
每次请求重建 Session 并从 `messages` 灌回 history · user 消息在调模型**之前**落库 ·
正常结束一次 INSERT 零 UPDATE · 断流落 `complete=false` 残文 · 前端渲染「这条回答没说完」
并给「重问 / 接着往下」· 重连回放不重调模型 · ③屏「最近问过的」·
StreamRecorder 三条契约测试 · 模型调用全用替身。

### 深模块 StreamRecorder

对外只有一个 `async with`，里面藏着四件事：

1. **退出路径只有一条。** 正常退出落 `complete=true`，异常退出落 `complete=false`，
   两条路走同一段落库代码。引擎里 `Session.ask()` 是 `history.append` 在 `chat()`
   **返回之后**——异常即丢，那个形状在这里不能照搬（Plan 的 hint 点名了这一条）
2. **流的时候不碰数据库。** 一次回答几十秒，一边流一边 UPDATE 就是几百次写库，
   还整段时间占着一个连接。测试直接盯 SQL：`assert statements == ["INSERT"]`
3. **跨线程交接要收尾。** 模型调用跑在工作线程，`sink()` 从那边被调。最容易丢的是
   最后几个 chunk——事件循环还没收，上下文就退出了
4. **同一条流可以被听第二次。** 断线重连时挂上去先回放再跟进，不重新调模型

### 写这个模块时抓到的两个自己的 bug

**① 先宣布 done、后落库。** 客户端收到 `done` 的那一刻这条回答还没进库；它转头
重新拉会话，看到的是一个没有回答的问题——一条它刚刚亲眼看着流完的回答。
改成先落库再 `close()`，并加了一条测试钉住顺序。

**② `close()` 没收尾就宣布结束。** `sink()` 走 `call_soon_threadsafe`，排在下一轮
循环才跑；不先 `_drain()` 就 close，最后投进来的几个 chunk 会被结束信号越过去——
订阅者收到 done 就不再读了，那几个字在屏幕上永远不会出现。

### 引擎第 9 处改动：认出被截断的流

走查时假模型在第 3 段之后掐断连接，服务端**照样落了 `complete=true`**。
根因在引擎：`OpenAICompatBrain` 用 `for raw in resp` 读流，对面中途断开时迭代
只是静静地结束，`chat()` 正常返回一个残缺的 `Reply`。

这是「不要假装成功」在对话这一侧的形态，而且比解析那一侧更隐蔽：
**一段被截断的回答读起来和完整回答一模一样**——模型停下时，最后一句常常正好
是个句号。用户会把半截话当成完整答案，然后奇怪它怎么答得这么浅。

OpenAI 兼容协议里正常结束一定有 `[DONE]` 或 `finish_reason`；两者都没有就是对面
把线掐了。现在这种情况抛 `BrainError`，已经流出去的字由 `on_text` 保住。

```
改之前：模型在第 3 段之后被掐断  ->  末事件 done      · complete=true  ← 假的
改之后：模型在第 3 段之后被掐断  ->  末事件 error     · complete=false
        「断流用 在说完之前断开了连接。已经说出来的部分保留着，
          可以让它接着往下说，或者重问一次。」
```

### 实测（假模型端点，不打真实 API）

走查用一个讲 OpenAI `/chat/completions` 的本地假端点，真发 HTTP、真走 SSE ——
不是 monkeypatch，所以 `X-Brain-Key` 一路传到模型请求上这件事是真验过的。

```
没带 X-Brain-Key  ->  400  「走查用」需要你自己的 key 才能用。到设置页把 key 填进去…
POST .../ask (SSE)    15 个事件，末事件 done
  第一个 token 在 55ms 到，整条流 778ms —— 不是等生成完才一次性给
  逐字流 14 段 · 合起来 82 字
库里：seq1 turn1 问 / seq2 turn1 答（scholar）· 输入 13,204 · 输出 286 · 缓存命中 12,904

假模型那头看到的：Authorization=Bearer sk-walkthro…     ← key 到了
服务端日志里出现 key 的次数：0                            ← 没进日志

重连（同一个问题、相隔 250ms 再 POST 一次）：
  第一次收到 82 字 / 重连收到 82 字（先回放已说的，再跟上后续）
  两边收到的是同一段话：True
  库里的回答条数：1        ← 同一个问题只产生一条回答
  假模型被调次数：+1       ← 重连没有再调一次模型
```

### 几个刻意的判断

- **生成任务不跟着响应走。** 用户关掉页面、网络断了，它照样跑完并落库——那次
  调用的钱已经花出去了，把结果写下来才对得起它。下次打开会话就能看到完整回答
- **`ask()` 是普通协程返回异步迭代器，不是异步生成器。** 鉴权、没填 key、上一轮
  还没说完这些检查因此发生在 `await` 那一刻，变成规规矩矩的 4xx；写成异步生成器
  的话它们要等第一次迭代才跑，那时 `200 text/event-stream` 已经发出去了，
  用户看到的是一条莫名其妙断掉的流，而不是一句「到设置页填 key」
- **前端流断了不自动重试。** 自动重试看起来贴心，实际是让同一个问题计两次费
- **「最近问过的」按最后一次提问的时间排**，不按 `conversations.updated_at`：
  加一条消息并不会 UPDATE 会话行，那个时间戳会一直停在开会话那一刻
- **绝不从环境变量兜底取 key。** 引擎的 `config.resolve()` 会那么做，对 CLI 是对的；
  在服务端那意味着用户没填 key 时悄悄花的是运维的钱，而且没有任何人会发现

### 留白

- **对读三列并排**是 **S10**；⑦屏本片只渲染单列
- **引文溯源**（回答里的引文块 + 「去原文」）是 **S9**
- **选章明细**（「读了 5/16 节 · 看怎么挑的」）是 **S8**；本片走「整本装得下」，
  底部仪表写的是「全书载入 · 本片不挑章节」，不写一个假的比例
- **处境模式**开关是 **S11**，人格切换条上不渲染
- **`retrievals` 表本片不写**：一轮的选章决策属于 S8，现在写进去等于先编一个
- **「接着往下」是把残文交回给模型让它续**，不是断点续传——模型那头没有断点。
  文案没有承诺「从断掉的那个字接上」

### 规范候选追加（累积到全部切片完成时一次性裁定）

16. **流式接口先落库、后宣布结束。** 反过来的话，客户端收到 done 时数据还没进库，
    它立刻重新拉一次就会看到一个不存在的空洞。→ `backend.md`「接口与响应」
17. **长流的持久化自己开 session，不用请求那一条。** 请求的 session 随响应结束而关，
    而落库可能发生在客户端早就断开之后。→ `backend.md`「注释与事务」
18. **协议层面「没有正常结束标记」必须当作失败**，不能当作成功返回残缺结果。
    截断的回答读起来和完整回答一模一样，不说破就是假装成功。→ `backend.md`「设计模式与约定」


---

## Slice 8 完成记录（2026-09-17）

全套 **185 passed**（S7 结束 147 → +38），ruff / eslint / vue-tsc / vite build 全干净。
十一条验收逐条达成。

### 深模块 ChapterSelector

`services/chapter_selector.py`。接口只有一个 `choose()`，进来的是章节、问题、预算、
已锁定集合，出去的是一份决策加一本账。**不碰数据库，也不碰模型 API**——所以
`tests/test_chapter_selector.py` 整个文件没有夹具、不连库、0.06 秒跑完 14 条。

四条分叉，走错哪一条都不报错，只会悄悄改变模型手里有什么：

| 结论 | 什么时候 | 留档 | 锁定 |
|------|---------|------|------|
| `whole_book` | 整本装得下 | 写 | **不锁**（下一轮照样装得下，锁它只会在重解析后过期） |
| `bm25` | 装不下 | 写 | 写 |
| `locked` | 已经挑过 | **不写** | 不动 |
| `relock_needed` | 问到了没载入的那几节 | 不写 | 不动，等用户点头 |

`Choice.records` 那个属性就是「这一轮有没有做决定」。复用锁定的轮次什么都不写——
替它们编一行出来，`retrievals` 就不再是决策留档，而是一份每轮复制粘贴的流水。

### 引擎三处改动（CLI 那条路行为不变）

| # | 模块 | 改动 |
|---|------|------|
| 10 | `retrieve.py` | 拆出 `plan_selection()`，连「每章几分、命中什么词、为什么没选它」一起给出；`select_chapters()` 成为它的简版出口。另加 `BM25.weight()` 与 `terms_for()` |
| 11 | `ask.py` | `Session.preselected_idx`：选章已经在外面做过了，本地不再挑第二次 |
| 12 | `model.py` / `library.py` | `Chapter.stated_words`：没载入正文的章节靠索引里的字数报数 |

**#10 为什么必须做在引擎里**：⑥屏那张排名表和模型真正拿到的那几节，必须是
同一次计算的两个面。在 Web 层照着 BM25 重算一遍，「界面说读了 5 节、模型实际
拿到 4 节」的那天一定会来，而且没有任何报错。

**#11 堵的是一个很隐蔽的坑**：按 `only_idx` 载入的书里，没选中的章节留着空壳。
引擎再挑一次会把空壳也算进「装得下」，于是 `as_context` 拿到一份「全都载入了」
的名单——未载入的那些既丢了「本次未载入全文」的标注，又渲成有标题没正文的空段。
那正是 Grill C10 那条连锁要防的终点。

顺带修掉 `always_include` 的一个旧行为：开头几章原本**无条件**保留，预算小到
连序言都放不下时照塞不误。后果是模型拿到一页扉页、用户问的那一章一个字都没进去。
现在改成「只在装得下的前提下」——这也正是验收里「预算连一章都装不下时至少给
最相关的一章」那条要的。

### 走查里炸出来的四个自己的 bug

**① 复用锁定的那一轮也报「挑好了」。** 它什么都没挑。报一句「挑好了」是替一个
没发生的动作邀功，而且会让用户以为每轮都在重挑——正好和这一片给他的保证相反。

**② 「命中的词」那一列没法看。** 按出现次数排，十六行全是
`悉达×46 达多×46`——书名里的字每一节都有，idf 接近 0，对排名毫无影响，却次次
排第一。改成按对分数的贡献排（`BM25.weight`）之后，头条变成了
`随佛×2`——「追随佛陀」中间切出来的跨词碎片，全书只出现两次，idf 高得离谱。
**两头都试过**，最后落在「按贡献排 + 出现少于 3 次的不算理由」：

```
按次数排  乔达摩  悉达×46 达多×46 佛陀×42     ← 十六行长得一样
按贡献排  乔达摩  随佛×2  乔达×14 达摩×14     ← 头条是个碎片，用户看不懂
现在      乔达摩  乔达×14 达摩×14 佛陀×42
```

**③ 复用那一轮的系统提示和第一轮不一样。** 这是本片最要紧的一个 bug，而且
**长度完全相同**，只有哈希对不上：`Book.words` 是从已载入的正文数出来的，
按 `only_idx` 载入时没选中的章节只剩空壳，于是人格提示词里那句「篇幅 48,155 字」
缩水成两万。两个后果都不报错——一是告诉模型一本它其实没拿全的书只有这么长，
二是**提示词一变，前缀缓存整个作废**，而会话锁定的全部意义正是保住那个缓存。
引擎改动 #12 就是为它做的。

**④ 会话锁定读的是手上那个 ORM 对象。** 锁定由后台任务在另一条 session 里写，
而 session 工厂配的是 `expire_on_commit=False`——已加载的对象不会失效，后续
`select()` 也不会覆盖它已有的属性。于是第二轮会当成没锁过，重跑一次 BM25、
换一套章节。同样不报错，账单上多出来的那笔钱界面上没有任何地方会显示。
改成取单列（`select(Conversation.selected_chapter_ids)`），身份映射管不着它。

### 走查（真 HTTP、真 SSE、真库、假模型端点 :8899）

```
① 会话 11 · 悉达多 16 节 48,155 字 · 预算 20,000 字 —— 装不下
② stage@3ms「正在挑章节…」→ stage@69ms「挑好了：16 节里读 7 节」
   第一个 token 在 163ms 到，整条流 895ms
③ 明细：选中 7/16 · 用掉 19,552 · 参与打分的词 26 个
   ●1 乔达摩 27.95 4,745  4,745  乔达×14 达摩×14 佛陀×42
   ●2 沙门   17.01 4,890  9,635  乔达×7  达摩×7  佛陀×11
   ○4 乔文达 14.43 5,191    —    （落选的照样有分数、字数、名次）
   预算线：下一节「乔文达」要 5,191 字，只剩 448 —— 差 4,743 字装不下
④ 第二轮：stage 事件 0 个，retrievals 不加行，selected_chapter_ids 不动
⑤ 逃生门：HTTP 409 · code='reselect_required' · 拦在写下这条提问之前
⑥ reselect=true：turn 3 新留档，这次读的是「迦摩罗、尘世间、船夫、觉醒…」

假模型收到的 system prompt：
  第一轮  20,838 chars  sha=cd176aad940d
  第二轮  20,838 chars  sha=cd176aad940d   ← 一模一样，缓存保住了
  重选后  21,224 chars  sha=a01eaa1dd854   ← 换了章节，本该不一样

库里：retrievals 2 行（turn 1 / turn 3，turn 2 复用不写）· retrieval_items 32 行
（16 × 2，全书每一节都有）· selected_chapter_ids 7 个 id
服务端日志 13 行，sk-walkthrough / X-Brain-Key / 密码 / 邮箱 / DSN 各出现 0 次
```

### 两个刻意的决定

- **`GET /api/conversations/{id}/retrieval` 挂会话，不挂消息。** 一轮里三种人格
  各答一次却只选一次章（S10 会用到），挂 message 会存三份一模一样的。返回的是
  **最近一次真正做过的**决策——复用的轮次没有自己的明细可言，界面照实写出是
  哪句话挑的章，而不是假装排名是刚才那个问题算出来的。
- **⑥屏一律写「字符」不写「字数」。** 预算算的是真正送出去的那些字，标点、换行、
  Markdown 标记都占上下文；目录页那个字数是解析时统计的正文字数。两个数量的是
  两件事（乔达摩：4,745 vs 3,948），用同一个词会让「累计」和预算线看起来算错。

### 留白

- **引文溯源**是 **S9**，**对读三列**是 **S10**，**处境模式**是 **S11**
- **「改预算重问」那个按钮**（界面稿⑥屏右上）本片不做：改预算等于重选，
  而重选的入口已经在⑦屏的逃生门里了。两个入口做同一件事、其中一个还能改参数，
  属于 S11 设置面板那一摊
- **`citations` 表本片不写**，S9 的事

### 规范候选追加（累积到全部切片完成时一次性裁定）

19. **同一个状态码下有两种前端必须区别对待的情况时，给它一个机读 `code`。**
    靠中文文案分辨，改个措辞就把分支改坏，而且不报错。code 一律写死的字面量，
    绝不来自请求。→ `backend.md`「接口与响应」
20. **后台任务写过的字段，请求侧必须重新查，不能读手上那个 ORM 对象。**
    `expire_on_commit=False` 下已加载的对象不会失效，后续 `select()` 也不覆盖
    它已有的属性。取单列可以绕开身份映射。→ `backend.md`「注释与事务」
21. **提示词里任何一个数字都可能是缓存杀手。** 「全书多少字」这种看起来纯装饰的
    统计量，一旦随载入方式而变，前缀缓存整个作废，而长度可能完全相同——
    比对要比哈希，不能比长度。→ `backend.md`「设计模式与约定」
22. **界面上「为什么是它」这类解释性列表，排序要用产生那个结果的同一个式子。**
    按直觉的量（出现次数）排，排出来的头几个往往恰恰是没起作用的那些。
    → `frontend.md`「设计模式与约定」
23. **两个都叫「字数」但量的不是一回事时，界面上要用两个词。** 预算算字符
    （含标点与标记），目录算正文字数；同名会让相邻两栏的加法看起来算错。
    → `frontend.md`「文案」

### 待回写设计文档（交 `design-doc-sync` 收尾时处理，不在本片改）

| 文档 | 现状 | 应改为 |
|------|------|--------|
| `backend.md` 端点表 | `GET /api/messages/{messageId}/retrieval` | `GET /api/conversations/{conversationId}/retrieval` —— 留档挂 `(conversation_id, turn)`，一轮三条回答只选一次章 |
| `backend.md` 提问那条 | SSE 事件写的是 `token`/`done`/`error` | 加上 `stage`（「正在挑章节…」）；并注明 `reselect` 入参与 409 `code=reselect_required` |
| `frontend.md` 路由表 | `/chat/:conversationId/retrieval/:messageId` | `/chat/:id/retrieval` —— 同上，明细不属于某一条回答 |
| `database/sql.md` | `retrievals` 仍写着 `message_id BIGINT NOT NULL UNIQUE` | 换成迁移里的 `conversation_id + turn` 版本（S1 已记，本片实际用到） |

---

## Slice 9 完成记录（2026-09-17）

全套 **212 passed**（S8 结束 185 → +27），ruff / eslint / vue-tsc / vite build 全干净。
五条验收逐条达成。**引擎零改动**。

### 定位规则：归一化之后的精确包含，仅此而已

`services/citations.py` 是纯逻辑，不碰数据库也不碰模型 API。它只做一件事：
把回答里的引文找出来，在**这一轮模型真拿到的那几节**里做精确查找。

不做模糊匹配、不做相似度阈值、不做「最接近的那一段」。理由很具体：
**一条定位错了的引文比没有引文糟得多。** 用户点「去原文」是为了亲眼核对，
而错误的链接会把他送到一段他没引的话前面——那段话看起来完全像是出处，
他核对完反而更信了。

归一化是**对称**的（两边都做同一套），所以它不是模糊匹配：去掉脚注标记
`[^1]`、去掉强调符号、去掉全部空白。空白整个删而不是合并，因为中文里空白不
承载意义，而模型会把一段话重新折行。

省略号按省略处理——那不是猜，是模型明确标了「这中间我略去了」。但两截必须
落在**同一块**、顺序不能反，否则就成了把两段不相干的话拼起来再给一条链接。

### 三个刻意的判断

**一、行内「」和块引用不是一回事。** 中文的「」既用来引用也用来强调——
「把佛陀当作外在权威来依附」是研读者自己的措辞，不是书里的话。所以：

| | 定位得到 | 定位不到 |
|---|---|---|
| 块引用 `>` | 记一条，带出处 | **记一条，照实说「原文里没找到」** |
| 行内 `「」` | 记一条，带出处 | 不记 —— 当它是强调 |

把行内引号的落空也记成「找不到」，等于对着用户喊一次狼来了；下一次真有引文
对不上时，他已经不看那句提示了。

**二、只在这一轮真载入的章节里找。** 没载入的章节模型根本没看见过正文；
即便书里确实有这句话也不能替它认下来——那不是引用，是背出来的或编出来的。
认下来就等于用一条「去原文」的链接替一段幻觉背书。

**三、`trace(None)` 与 `trace([])` 是两件事。** 前者是「我们根本没拿到书」
（选章那步就出错了），后者是「书在手上、但这句话不在里面」。混成一个的话，
一次载入失败会把整条回答里的每个块引用都标成「原文里没找到」——在最该怀疑
系统的时候去怀疑模型。

### 段号是跨语言的那处耦合

`para_index` 指的是**阅读页渲出来的第几个顶层块**，不是「第几个自然段」。
服务端按空行切 Markdown 源（`citations.paragraphs()`），前端是 markdown-it
渲成 HTML 之后取 body 的直接子元素。两边现在一致，但没有任何机制保证它们永远
一致——而不一致时不会报错，只会把高亮画到隔壁那一段上。

这里最容易差的是**开头两块**：章节文件以 `# 婆罗门之子` 和一行
`> 悉达多　·　第 3/16 节　·　3,710 字` 开头，引擎载入时 `_strip_chapter_header`
把它们剥掉了，而阅读页渲的是原始文件。所以溯源比对读的是**原始文件**，
不是刚载入的 `book.chapters`——差两块，高亮就落在隔壁那一段上。
`test_citations_api.py` 的夹具刻意保留了这两行，就是为了让这个坑暴露出来。

兜底在前端：`lib/citations.ts` 拿到段号之后**先核对那一块里是不是真有这句话**，
不是就自己找一遍，还找不到就不画高亮。**索引是快路，原文是准绳。**
重解析之后（S12）段号必然过期，走的也是这条自己找一遍的路。

### 顺手收拢的一处重复

`core/paths.py` 的 `under()`：先 resolve 再核对在不在书目录里面。这条规则原本
只在 `reader_service._resolve` 里，本片溯源要读同一批文件，抄第二遍就是抄一条
安全规则——而抄错的那一份不会报错，只会在某一天读出书目录外面的文件。

### 走查（真 HTTP、真 SSE、真库，假模型吐一真一假两条引文）

```
① 会话 12 · 预算 20,000 字（全书 48,155，走 BM25 挑了 7 节）
② 这条回答引了什么（跟着会话详情一起来，一屏几十条回答不必逐条再问）
   ● 《乔达摩》第 3 块  「两位朝拜乔达摩的青年沙门，一路探询到达此地。」
     去原文 → /books/7/read/5?cite=1
   ○ 没找到出处        「悉达多在河边见到了瓦稣迪瓦，两人相视而笑。」
     照实说「这句话在这次载入的原文里没找到」，不猜是哪一章
③ GET /api/messages/48/citations  → 200 · 2 条
④ GET /api/citations/1 → 问题「悉达多为什么拒绝追随佛陀？」· 会话 12 · 书 7 · 第 5 节
⑤ 独立核对：把 005-乔达摩.md 自己按空行切开，第 3 块确实包含那句引文 → True
⑥ 不存在的回答 → 404（不是空数组：空数组会被读成「这条回答没引原文」）
```

### 留白

- **对读三列**是 **S10**，**处境模式**是 **S11**
- **重解析之后的引文重定位**是 **S12**（`CitationRelocator`）。本片已经把它需要的
  两样东西备好了：`chapter_title` 快照，以及 `updated_at != created_at` 这个
  「被重定位过」的标记（`CitationOut.relocated`，界面已经在读它）
- **人格提示词没有改。** 让研读者「引原文时另起一段用 `>`」会让溯源命中率高不少，
  但 `prompts/` 是这个项目真正要反复打磨的东西，属于产品判断。现在的实现对
  行内引号和块引用都认，不改提示词也能用
- **「就这一节提问」**（⑤屏那个按钮）仍未做：它要带着章节范围开一个新会话，
  属于选章那一侧，等 S11 的设置面板一起

### 规范候选追加（累积到全部切片完成时一次性裁定）

24. **跨语言重复实现同一条切分/解析规则时，下游必须能自我校正。**
    段号由 Python 数、由 TypeScript 用；两边分叉不报错，只会把标记画到相邻
    元素上。下游拿索引当快路、拿内容当准绳。→ `frontend.md`「设计模式与约定」
25. **「没拿到数据」和「数据里没有」必须是两个值，不能都用空集合。**
    混成一个，一次上游失败会伪装成一批下游的否定结论。
    → `backend.md`「设计模式与约定」
26. **同一个符号在中文里兼做引用与强调（「」）时，落空的处理要分开。**
    明确标记（块引用）落空要说破，模糊标记（行内引号）落空要沉默——
    否则提示会因为狼来了而失效。→ `frontend.md`「文案」
27. **返回空集合与返回 404 要分清。** 「这条回答没引原文」是空数组，
    「没有这条回答」是 404；混成一个，打错的 id 会安静地看起来像前者。
    → `backend.md`「接口与响应」
28. **路径拼接的安全规则只允许有一份实现。** 先 resolve 再核对边界，
    第二处要用就 import，不许抄。→ `backend.md`「设计模式与约定」

### 待回写设计文档（交 `design-doc-sync` 收尾时处理，不在本片改）

| 文档 | 现状 | 应改为 |
|------|------|--------|
| `frontend.md` 路由表 | `/books/:id/read/:idx` | 补上 `?cite=<citationId>`：带它进来就是从对话跳过来的，顶上画来路条、正文标出被引段 |
| `backend.md` 端点表 | 只有 `/api/messages/{messageId}/citations` | 补 `GET /api/citations/{id}`（⑤屏来路条要的问题与会话），并注明 `MessageOut.citations` 随会话详情一起给 |

---

## Slice 10 完成记录（2026-09-18）

全套 **222 passed**（S9 结束 212 → +10），ruff / eslint / vue-tsc / vite build 全干净。
七条验收逐条达成。**引擎零改动。**

### 两条人工卡点的裁决

| 卡点 | 裁决 | 依据 |
|------|------|------|
| 对读前要不要弹确认 | **不弹**，按钮下写「三个一起看 · 三次调用」，底部仪表写「对读一次 = 三次调用」 | 三路共享整本书的缓存，真实成本 ≈ 1 份书 + 3 份人格与问答。对读是这个产品的立意，每次多一步点击会把它变成一个没人用的功能 |
| 多轮历史怎么拼 | **每轮取一条：有当前主干人格那条就用它，没有就用这一轮最早的那条** | 不用改表；S1 记下的那个失败（历史从 4 条塌到 2 条）直接消失——单人格轮次只有一条回答，永远不会落选 |

第二条解决的是 S1 记下、留给本片裁决的结构性冲突：原验收「拼 history 只取主干
人格那一支」与「『跟这个继续聊』更新主干人格」同时成立时互相拆台。**验收标准
按裁决改写**，`messages` 不加 `main_branch` 列。

### StreamRecorder 从「一条回答」改成「一轮」

这是本片最大的一处改动。原来一个 recorder 管一条回答，现在管**一到三条轨**，
单人格只是 N=1 的那个情形——走同一段代码，两套代码迟早会在断流那条路上分叉，
而那条路本来就少有人走。

**事件分成了两层，不是啰嗦：**

| 事件 | 时机 | 谁的结局 |
|------|------|---------|
| `token {persona, text}` | 随时 | 某一路 |
| `answered {persona}` / `failed {persona, message}` | **当场** | 某一路——界面据此把那一列的转圈收掉 |
| `done {}` / `error {message}` | **落库之后** | 整轮 |

合成一层的话，S7 那条「收到 done 的那一刻消息已经在库里」就没了——而客户端
收到 done 的第一反应正是回头去拉一次会话。测试仍然钉着那条保证。

顺带的两处：**一路出错不拖垮另外两路**（那两列的钱已经花了），以及
**seq 在同一个事务里顺序分配**——三条各算各的 `max(seq)+1` 会算出同一个号，
然后撞 `uq_messages_conv_seq`。

### 写的时候躲开的两个坑

**一、三路共用一个 history 列表。** `EngineSession.ask()` 结束时那句
`history.append` 会三路一起往同一个 list 里塞，下一轮的上下文就成了三种声音
交替的一团乱麻——**而且它不报错**。每路发一份拷贝。

**二、历史里两个连着的 user。** 断在半路的那一轮若只进提问、不进回答，
历史里就会出现连续两条 user，而 Anthropic 那边要求角色交替——那是一个只在
「上一轮断过流」时才现形的 500。现在一轮只在**既有提问、又有一条说完了的回答**
时才进历史。残文不丢：用户点「接着往下」时它被原样带回去。

### 走查（真 HTTP、真 SSE、真库、假模型端点）

```
② 对读一轮 compare=true
   事件 82 个，末事件 done
   scholar 首字 344ms · author 首字 340ms · book 首字 340ms
   answered: ['author', 'book', 'scholar']（谁先说完谁先结算）
   整条流 928ms —— 三路并发，不是串行三倍

③ 库里 seq1 turn1 user / seq2 scholar / seq3 author / seq4 book —— 同 turn 不同 persona

④ 假模型收到的三次调用：
   sha=5d2fd9353caa  book=2a19b1a65273
   sha=e4bbb64e303b  book=2a19b1a65273   ← 整段不同（人格不同）
   sha=b137692ab97d  book=2a19b1a65273   ← **书那一段逐字相同**

⑤ PATCH persona=author → 主干换了，旁支 3 条一条不删
⑥ 下一轮只有 author 说话；假模型收到 messages 条数 4
   = system + 问题一 + **一条**回答（对读那轮取了 author 这一路）+ 问题二
   —— 裁决那条规则在真请求上的样子

日志 12 行，sk-walkthrough 出现 0 次
```

### 三种人格用填法区分身份

研读者**实朱**、作者**实墨**、书**朱框空心**。颜色在这套设计里是有归属的
（朱批 = 人、靛 = 机器、黄檗 = 存疑、重墨 = 阻断），拿状态色去区分三个平级的
说话人，会让「作者」那一列看起来像是出了什么事。

### 留白

- **处境模式与人格编辑**是 **S11**（⑦屏那个开关、⑧屏右栏）
- **对读那一轮的引文**照常落库并渲染（S9 的代码对三路一视同仁），但三列并排时
  每列只显示前 3 个命中词、引文块压窄——**没有为对读单独做一套引文界面**
- **旁支「折叠可查」目前是并排显示**，不是折叠：一轮三条回答就三列并排，
  切走主干之后它们仍然在原地。真正的折叠（把旧对读收成一行「三种说法 ▸」）
  等⑦屏历史长起来之后再说，现在折叠等于把刚看过的东西藏起来

### 规范候选追加（累积到全部切片完成时一次性裁定）

29. **「某一项完成」与「整批完成」是两个事件，不能合并。** 前者当场发（界面要
    收掉那一项的转圈），后者要等副作用落地才发（客户端收到它就会回头拉数据）。
    合成一个，其中一条保证必然失效。→ `backend.md`「接口与响应」
30. **并发跑同一段逻辑的 N 份时，可变的共享状态必须各发一份拷贝。**
    `history` 那种「调用结束后往里 append」的列表尤其危险——共用它不报错，
    只是把下一轮的上下文搅成一团。→ `backend.md`「设计模式与约定」
31. **拼给模型的消息必须角色严格交替。** 上一轮断过流留下的落单提问会变成
    连续两条 user，那是一个只在特定历史下才现形的 500。
    → `backend.md`「设计模式与约定」
32. **区分平级的身份用填法（实心/描边/深浅），不借用状态色。**
    语义色有归属；拿它区分身份会让其中一个身份看起来像出了故障。
    → `frontend.md`「设计模式与约定」

### 待回写设计文档（交 `design-doc-sync` 收尾时处理，不在本片改）

| 文档 | 现状 | 应改为 |
|------|------|--------|
| `frontend.md` 待确认 2 | 「对读的成本提示未定，请裁决」 | 已裁决：**不弹确认**，代价写在按钮下与底部仪表 |
| `backend.md` SSE 事件 | `stage` / `token` / `done` / `error` | 补 `answered` / `failed`（某一路的结局，当场发）；`token` / `answered` / `failed` 都带 `persona` |
| `backend.md` 端点表 | 无 | 补 `PATCH /api/conversations/{id}`（「跟这个继续聊」，只改主干人格） |
| 本 Plan S10 验收第 5 条 | 「拼 history 只取主干人格那一支」 | 按裁决改为「每轮取一条：优先当前主干人格，没有就取这一轮最早的那条」 |

---

## Slice 11 完成记录（2026-09-18）

`251 passed`（S10 结束 222 → **+29**）。ruff check / format 干净，
vue-tsc + eslint + prettier + vite build 干净。**引擎零改动**——
`REFLECTION_SUFFIX` 与 `Session.reflection` 在 S7 那一片就已经在引擎里，
本片是把它接到界面上，并且新开了提示词的读写那条路。

### 五条验收全部达成

| 验收 | 落点 |
|------|------|
| 开关置 true 时 `conversations.reflection = true`，人格提示词后附加 `REFLECTION_SUFFIX` | `PATCH /api/conversations/{id}` + `ChatService.update()`；⑦屏人格条右端那个开关 |
| `GET /api/personas`；`GET/PUT /api/personas/{key}` | `routers/personas.py`，另加 `POST /{key}/restore` |
| 提示词编辑在⑧屏右栏，改完即时生效 | `components/persona/PersonaEditor.vue`；引擎每轮 `persona_mod.load()` 重读文件，不重启 |
| 提示词仍是 `prompts/` 下三个 md，不硬编码回 Python | 路径由引擎 `persona.prompt_dir()` 说了算，本层不自己拼 |
| 维持全局共享（Spec N5），界面须说明对所有用户生效 | ⑧屏右栏说明栏那一句；`prompt_store` 模块首注释同步记着 |

### 深模块：`services/prompt_store`

本片唯一一处往 `prompts/` 写字的地方。它值得单独成模块，是因为「把一段文字
存进一个文件」在这里有四种各自独立、而且都不显眼的坏法：

**一、占位符写坏了，报应在几小时之后。** 模板要过 `str.format()` 填书名作者。
用户在 md 里写 `{原文}` 或者 `{"答案": ...}` 是再自然不过的事——而它会在
**下一次提问时**抛 KeyError / ValueError，炸在后台任务里。用户看到的是
「这条回答没说完」，跟他半小时前改的那个字看不出任何关系；更糟的是三种人格里
只有那一种会坏，另外两种照常，整件事看起来像模型抽风。

体检因此发生在**保存的那一刻**，分三层：`Formatter().parse()` 挑出未配对的
花括号 → 逐个字段名对白名单（`{}`、`{0}` 一样拦，它们渲染时抛的是 IndexError）
→ 最后拿一份假书真跑一遍 `format()`，挡住前两层没想到的写法。错误消息要
**点名那个占位符**并列出能用的五个——光说「格式不对」等于让用户在三千字里自己找。

**二、写到一半断电。** `open(path, "w")` 先截断。进程在那一刻挂掉，留下的是
一个半截的人格，它不报错，只让此后每一次对话都差一点。改成写临时文件 +
`os.replace`。

**三、改坏了回不去。** 这三个 md 是这个项目最值钱的东西，而 `prompts/`
**不在版本控制里**。第一次从界面覆写之前先存一份基线到 `prompts/.baseline/`，
`POST /{key}/restore` 拿它回来。基线是**出厂那一版，不是上一版**：用户点
「还原」时多半已经改了三四轮，最想要的正是最初那一版。还原之后基线退场，
`edited` 回到 false。

**四、全局共享。** Spec N5：不建 `user_personas` 表，代码里根本没有 user_id。
这条只能靠界面说清楚，以及靠模块首注释提醒下一个人。

顺序是刻意的：**先体检、再留基线、最后换文件**。任何一步失败，磁盘上都还是
上一刻那个样子——有一条测试专门钉这个（`test_被拒的保存一个字都不写进去`，
连基线目录都不许出现）。

### 顺手补上的一处口子：自由文本的路径参数

`/api/personas/{key}` 是这个应用里**第一个自由文本的路径参数**——在它之前
所有路径参数都是整数。自由文本一进来，访问日志就多了一条注入路径：路径里一个
`%0A` 能把一行访问日志劈成两行，后一行看起来就是另一次请求，伪造一条
「某人登录成功」不需要任何权限。

实测结果是**挡住了**，但挡住它的不是我们写的代码：中间件记的是
`request.url.path`，它走 `urlsplit`，而 `urlsplit` 会剥掉 ASCII 的换行与制表符。
`request.scope["path"]` 是解码后的原样，两者看着等价、**不等价**。

这种「靠一个库的顺带行为撑着、而且换成看起来更直接的写法就会塌」的地方必须
有测试钉住。加了 `test_路径里的换行进不了日志`，并做了变异验证：把中间件改成
`request.scope["path"]`，它立刻变红，捕获的日志里能看到伪造出来的第二行。
`core/logging.py` 的模块注释同步记下了为什么是这一个而不是那一个。

另外把 404 里回显的 key 修剪了一道（去控制字符 + 截到 40 字）：Spec N1 要求
错误消息给出路，所以那个词要说出来，但不该把任意长度的一段字原样抄进响应体。

### 两个开关，两种代价，界面上要说的不是同一句话

这一片最值得记的判断：**处境模式和重选章节都是开关，代价差一个量级。**

缓存断点在全书之后、人格之前。处境模式附加的那一段、以及编辑过的提示词，
都只动断点之后那一块——整本书的缓存照常命中。所以：

- **重选章节**换掉整套章节、缓存作废、这一轮输入按全价重算 → **要用户点头**
- **处境模式**只多 141 个字符 → **随手拨，不弹任何东西**

⑦屏那个开关右边写的是「不废缓存」，不是一句代价。这是本片唯一一处主动告诉
用户「这个不要钱」的地方，而它有走查数字撑着（见下）。

### 实测走查（真 HTTP / 真 SSE / 真库 / 假模型端点）

服务端起在 `BOOKSOUL_PROMPTS=scratchpad/prompts-walk` 上。这一段会**真的往磁盘
写字**，指着项目里那三个 md 跑，一次走查就能把它们换掉；走的仍是同一段代码
（`prompt_dir()` 环境变量优先），顺带把那条覆盖路径也验了。跑完确认过项目
`prompts/` 三个文件的大小与 mtime 一个字没动。

假模型那头收到的三次调用（两次独立运行，sha 逐字复现）：

```
轮  状态            system 长度   全段 sha        书那一段 book=
1   处境模式关      59,675       b137692ab97d    2a19b1a65273
2   处境模式开      59,816       a48fbba7f777    2a19b1a65273   ← 逐字相同
3   提示词改过      59,066       6aa470a311dd    2a19b1a65273   ← 逐字相同

59,816 − 59,675 = 141 = len(REFLECTION_SUFFIX)  ——一个字符不多不少
```

两个旋钮都只动了缓存断点之后那一块。`book=2a19b1a65273` 与 S10 走查里的值
相同（同一本书、同一套选章）。

端点那一侧：

```
PATCH {reflection: true} → persona=scholar（没动）· reflection=True
名录三条，正文各 0 字；GET /api/personas/scholar → 794 字
PUT {原文} → 422「认不出占位符 {原文}。能用的只有：{title}、{author}…」
             磁盘上那个文件没动：True
PUT 合法    → 200 edited=True；基线落下且与出厂逐字相同：True
下一轮      → 立刻用新的（不重启）
restore     → 200 edited=False；磁盘回去了：True；基线已退场：True
GET /api/personas/莎士比亚 → 404 ；PUT 不带 token → 401
```

服务端日志 40 行，`sk-walkthrough` / `X-Brain-Key` / `password` / `@` /
`postgresql` / `booksoul:booksoul` 各出现 **0 次**。

### 本片留白（已就地注明，不是遗漏）

- **⑧屏那颗「试一句」按钮没做。** 界面稿上有它。它要一本书、一个大脑、一个
  key 才能跑，等于在设置页里塞进半个对话页。点了没反应的按钮比暂时没有按钮
  更糟，所以整颗不渲染。
- **未保存的草稿只活在这个组件里**，不落库也不进 localStorage。切人格标签页
  时 `confirm()` 拦一下。替用户悄悄记着一份改了一半的人格提示词，比丢掉更糟。
- **「还原」的语义是「回到第一次从这里改动之前」**，不是「回到 git 里那一版」。
  用编辑器直接改过 `prompts/` 的人，基线里存的就是他手改的那一版。这一点在
  按钮的确认文案里说明了，没有第二套真相。
- **⑧屏两栏我只验了接口与构建，没在浏览器里看过。**

### 规范候选追加（累积到全部切片完成时一次性裁定）

33. **写文件要么整体成功、要么一个字不动。** 先校验再落盘，落盘走
    临时文件 + `os.replace`。截断式写入的失败态（半截文件）不报错，
    只是让此后每一次使用都差一点。→ `backend.md`「设计模式与约定」
34. **覆写用户反复打磨过的、不在版本控制里的资产，必须留一份基线。**
    基线取**第一次覆写前**那一版，不是上一版。→ `backend.md`「设计模式与约定」
35. **会在别处才生效的输入，要在录入的那一刻校验。** 延迟到使用时才炸的错，
    用户看到的症状（「回答没说完」）和他的操作（改了个占位符）之间没有可见
    关联，而且往往只影响多条路径中的一条。→ `backend.md`「设计模式与约定」
36. **自由文本的路径参数进日志前必须走 `request.url.path`，不用
    `request.scope["path"]`。** 后者是解码后的原样，一个 `%0A` 就能伪造一行
    访问日志。两者看着等价，不等价。→ `backend.md`「日志与可观测」

### 待回写设计文档（交 `design-doc-sync` 收尾时处理，不在本片改）

| 文档 | 现状 | 应改为 |
|------|------|--------|
| `backend.md` 端点表 | `PUT /api/personas/{key}` 覆写人格提示词 | 补 `POST /api/personas/{key}/restore`（还原到出厂基线） |
| `backend.md` 端点表 | `PATCH /api/conversations/{id}` 只改主干人格 | 改为两个可选旋钮：`persona` / `reflection`，没给的那个不动 |
| `backend.md` 待确认 3 | 「`prompts/` 的多用户归属未定」 | 本轮实现按全局文件落地；界面已显式说明「对所有用户生效」。多用户上线前仍须处理 |
| `backend.md` 模块表 | 无 `prompt_store` | 补一行：`services/prompt_store` —— 人格提示词的读写，唯一一处往 `prompts/` 写字的地方 |
| `frontend.md` 待确认 5 | 「人格编辑器的保存粒度未定（草稿？版本回滚？）」 | 已落地：无草稿持久化（切页 confirm 拦一下），单级「还原到出厂基线」，不做版本历史 |
| `frontend.md` 路由表 | `/settings` 标题「大脑」 | ⑧屏已按设计稿排成两栏，页面标题改为「大脑与人格」 |

---

## Slice 12 完成记录（2026-09-18）

`273 passed`（S11 结束 251 → **+22**）。ruff check / format 干净，
vue-tsc + eslint + prettier + vite build 干净。**引擎零改动**。

### 七条验收全部达成

| 验收 | 落点 |
|------|------|
| `POST /api/books/{id}/reparse` 复用 book_id 与目录，响应带受影响会话数 | S5 已实现前半；本片回执加 `affectedConversations` |
| 重解析前提示「有 N 个会话会受影响」 | `BookDetail.conversationCount` + ④屏 `confirm()`；没有会话就不问 |
| 重解析后清空 `selected_chapter_ids`，下一轮重跑 BM25 并告知缓存失效 | `resettle.apply()`；流开头一句 stage |
| **服务层断言：载入 0 节直接抛错，不往模型送** | `chat_service._must_have_content()` |
| 引文重定位；未命中降级为灰按钮 +「原位置已变更」 | `relocate.relocate()`；⑦屏那段 UI 是 S9 就有的，本片加「重解析后重新找回」标记 |
| CitationRelocator 四类测试 | `tests/test_relocate.py` 16 条 |
| 实测报告 | 见下 |

### 深模块：`services/relocate`（CitationRelocator）

纯字符串处理，无副作用。四条纪律，其中两条是这一片真正的判断：

**用和当初定位时同一套匹配器。** 直接调 `citations.locate()`（本片把它从
`_locate` 提成公开的），不另写一份。另写一份的话，「命中率」这个数字测的就成了
两套算法的差，而不是重解析的影响——而那个数字正是你要拿来裁决的东西。
顺带把切块也统一了：新开 `services/sources`，提问那条路和重定位那条路从此
读同一份文件、切同一套块。分叉不会报错，只会让高亮落到隔壁那一段上。

**报告要分得清「命中」和「真的动了」。** 一本正文一字未改的书，命中率必然
100%，而那个数字什么都没证明——它连一次真正的重定位都没做过。所以 `Report`
把命中拆成 `shifted` / `unchanged` 两列。这一条直接决定了实测怎么设计。

还有一条是实现细节里的判断：**先在同名章节里找，找不到再翻整本书。**
不是为了快。「他学会了静默」这种句子在一本书里前后呼应是常态，一趟扫会把
后一节那条认到前一节去——用户点过去看到的是一段他没引的话。

### 调用顺序不是可选的

```
stale  = await resettle.snapshot(session, book_id)   # ← 必须在 replace_chapters 之前
rows   = await repo.replace_chapters(...)
report = await resettle.apply(session, book, rows, stale)
```

`citations.chapter_id` 走 `ON DELETE SET NULL`。旧章节一删，「这条引文原本在
第几节」就再也读不回来了，报告随之失去 shifted/unchanged 的区分。这一条写在
`resettle` 模块注释里，并且有测试（`_reparse_with` 照这个顺序跑）。

**成功的解析都走一遍，不只是重解析。** 首次解析时两条查询各自空手而归；
多一个 `is_reparse` 分支只会多一条平时走不到、因而没人验过的路。

### 实测（Grill R6）

真《悉达多》EPUB，走 ParseService 解析：16 节、48,155 字。引文不是手写的——
从解析产物里挑出 39 个真实句子拼成带块引用的回答，再交给 S9 落库时用的那个
`citations.find()` 定位，所以这些引文行和线上跑出来的一模一样。
清洗规则的改动用引擎里现成的清洗函数施加。

```
档  清洗规则的改动               总数  找回  位置变了  原地   丢   命中率
─────────────────────────────────────────────────────────────────
A   不改任何东西（对照组）         39    39      0     39    0   100%
B   补回重复标题（块号整体 +1）    39    39     39      0    0   100%   ← 真正的证据
C   打开硬换行合并（改动 4/16 节）  39    39      0     39    0   100%
D   换译本（他→彼）               39     9      0      9   30    23%   ← 下界
```

三处读法：

- **B 档是唯一有意义的那一行。** 39 条位置全部真的动了，而且 39 条全部找回。
  A 档那个 100% 什么都没证明。
- **C 档揭示了一件本来没想到的事**：归一化会把空白整个删掉，所以**排版级的
  清洗改动对匹配器天然不可见**——改了 4 节，命中率与位置一个都没受影响。
- **D 档是正文真的变了时的样子**：30 条降级成灰按钮，**0 条被放到错地方**。
  活下来的 9 条是恰好不含「他」字的那几句。

### 两条人工裁决

1. **接受这个降级方案，按现在这样落地。** 精确匹配 + 找不到就灰按钮，
   不加模糊匹配兜底。加了能把 D 档推到接近 100%，但那推翻的是 S9 写在
   `citations.py` 最前面那条：一条定位错了的引文比没有引文糟得多。
   换译本之后「最像的那一段」往往正是对应段落的新译文，所以有时是对的——
   而没有任何办法让用户知道这一条是猜的还是准的。
2. **重解析回执在②屏那本账里写一行**，不是只在引文自己那里显示。
   账本本来就是逐条可核对的，这一行属于那里。写法上守着账本原有的纪律：
   **没有引文要搬家时整行不出现**，不填 0——填 0 会让人以为发生过一件事。

### 顺带修掉的一个真问题

`apply_moves` / `clear_locks_for_book` 都是批量 UPDATE，绕过 ORM 的属性赋值；
而 session 工厂配的是 `expire_on_commit=False`，**连 commit 都不会刷新身份映射
里那份旧值**，而一次普通 SELECT 不会覆盖已经载入的属性。同一个 session 后面
再读一次会读到写之前那一份，不报错。两处都加了
`execution_options(synchronize_session="fetch")`，测试里读那一下换一个 session
（和线上一样：重定位跑在解析任务里，读引文是另一个请求）。

这是这个项目第三次踩 `expire_on_commit=False`（S10 的会话锁定、S11 没踩上、
本片两处），所以它进了规范候选。

### 本片留白（已就地注明，不是遗漏）

- **④屏那个确认框用的是 `window.confirm`。** 和⑧屏人格编辑器那两处一致。
  这一版没有自建对话框组件，起一个只为这两处用的组件不值。
- **没有会话时不弹确认**：那时重解析只是重跑一遍，没有任何东西会丢。
- **④屏与⑦屏我只验了接口与构建，没在浏览器里看过。**
- **重解析没有「撤销」。** 旧的解析产物被覆盖掉了。真要留退路得存两份产物，
  那是另一片的事——本片的退路是「原始上传文件一直留着，可以再解析一次」。

### 规范候选追加（累积到全部切片完成时一次性裁定）

37. **同一件事只能有一个匹配器 / 一套切法。** 「原定位」和「重定位」各写一份，
    测出来的命中率就成了两套算法的差。→ `backend.md`「设计模式与约定」
38. **成功率报告要能分辨「成功」和「真的做过事」。** 输入没变时成功率必然
    100%，那个数字什么都没证明。报告必须同时给出「实际发生了变化的条数」。
    → `backend.md`「设计模式与约定」
39. **要读的旧状态必须在破坏它的操作之前读。** `ON DELETE SET NULL` /
    级联删除会静默抹掉事实，顺序错了不报错，只是让报告失去意义。
    → `database/sql.md`「约定」
40. **批量 UPDATE / DELETE 之后，同一个 session 里的 ORM 对象是旧的。**
    `expire_on_commit=False` 下 commit 也不刷，而普通 SELECT 不覆盖已载入的
    属性。写 `synchronize_session="fetch"`，或者换一个 session 去读。
    → `backend.md`「设计模式与约定」
41. **凡是「把用户的东西换掉」的操作，动手前要说清楚会波及什么、要花多少钱。**
    没有波及就不要问——每次都弹的确认框等于没有确认框。
    → `frontend.md`「设计模式与约定」

### 待回写设计文档（交 `design-doc-sync` 收尾时处理，不在本片改）

| 文档 | 现状 | 应改为 |
|------|------|--------|
| `backend.md` 模块表 | 无 `relocate` / `resettle` / `sources` | 补三行：`services/relocate`（CitationRelocator，纯逻辑）、`services/resettle`（重解析善后，顺序敏感）、`services/sources`（章节文件 → 可比对的块，定位与重定位共用） |
| `backend.md` 仓储表 | 无 `conversation_repo` | 补一行；并注明 `clear_locks_for_book` 是「仓储层默认把 user_id 拼进 WHERE」的一处**有理由的例外**（跑在解析任务里，没有当前用户；`book_id` 已把范围钉死） |
| `backend.md` 规范章节 | 无 | 写入验收第 4 条：**载入章节数为 0 时直接抛错，不往模型送** |
| `backend.md` 端点表 | `POST /api/books/{id}/reparse` 无回执说明 | 补 `affectedConversations` |
| `backend.md` 端点表 | `GET /api/books/{id}` | 补 `conversationCount` |
| `database/sql.md` | `citations.chapter_id` 注释已写 SET NULL 的理由 | 补一句：读旧落点必须在 `replace_chapters` 之前，否则 idx 随章节一起消失 |

---

## Slice 13 完成记录（2026-09-20）

`292 passed`（S12 结束 273 → **+19**）。ruff check / format 干净，
vue-tsc + eslint + prettier + vite build 干净。**引擎第 13 处改动**（见下）。

### 四条验收全部达成

| 验收 | 落点 |
|------|------|
| 识别出审核类拒绝（区别于认证失败、限流、模型不存在） | 引擎 `BrainError.kind` + `_kind_of_http()` |
| 给出可操作建议：换一个大脑，或调小载入预算 | `chat_service._moderation_advice()`，**网页版的出路** |
| 能定位到触发章节时，④屏以重墨标注该章 | `moderation_events` 台账 + `ModerationService`；⑦屏目录树那一行压墨底 |
| 建议按 Spec N1 分层 | 引擎那句给命令行用户（`--brain` / `--budget`），网页那句给浏览器用户 |

### 引擎第 13 处改动：`BrainError.kind`

引擎本来就在 `_is_moderation()` 里判得出这是不是审核拒绝 —— 但它把结论揉进
一句中文里就扔了。上层要么拿不到，要么只能去匹配那句中文，而那句话改个措辞
就会把分支改坏，并且不报错。

`kind` 取 `moderation` / `auth` / `rate_limit` / `not_found` / `server` /
`network`，认不出来是空串。空串是默认值，所以老的 `BrainError("…")` 原样可用，
CLI 那条路一个字没改。

顺带把 `_http_error_message` 拆成了三段：`_detail_of()` 取详情（**只能调一次**，
`exc.read()` 是个流）、`_kind_of_http()` 判类别、`_http_error_message()` 组人话。
原先那个函数把三件事揉在一起，所以结论取不出来。

**普通的 400 不冒充审核。** 参数写错也是 400，认成审核会把用户支去换大脑，
而那救不了他。

### 出路必须是收到它的人能执行的那一种

引擎给的出路是命令行的：

```
  1) 换一个大脑：booksoul ask --brain deepseek/kimi/claude ……
  2) 用 --budget 调小载入量，让检索避开触发审核的那一章
```

这段照抄到浏览器里就是一句无法执行的建议 —— 而在本片之前，网页那一侧正是
`recorder.fail(persona, str(exc))` 原样转发。同一个结论，两种说法：

```
走查审核 的内容审核把这一问拦下了 —— 不是 key 或配置的问题。文学作品里的暴力、
性、宗教、沉沦等描写常会触发国内平台的过滤。两条出路：到「大脑与人格」换一个
大脑（Claude / DeepSeek / Kimi / 本机 Ollama 都不走国内审核），或者调小载入预算
让检索避开触发的那一节。被拦过几次之后，书籍详情页会指出多半是哪一节。
```

### 怎么在不多花一分钱的前提下指出是哪一节

供应商**不说**是哪一段触发的。硬要定位只有一条路：把章节拆开一节一节地试——
那是一串真金白银的调用，而这个项目从头到尾的立场是「用户花的钱不能白花」。

所以走另一条路。「这一次送了哪几节过去、结果是拦还是通」是我们自己的账：

```
嫌疑 = 每次被拦时都在场的那几节（交集） − 曾经平安送过去的那几节（并集）
```

**两边都需要，而这是走查逼出来的。** 第一版只做交集，实测卡在 3 节再也窄不
下去 —— 因为《悉达多》的「扉页」254 字、「第一部」5 字、「第二部」5 字，
**任何预算都装得下，于是每一次选章它们都在场**，交集永远排除不掉它们。
而一次成功的请求正好证明那几节是干净的。补上减法之后，范围一步收到了一节。

两种证据都来自用户本来就要发的那些请求，一分钱不用多花。

三处配套的判断：

* **按大脑分开算。** 同一本书在通义被拦、在 Claude 不被拦是常态；一次 Claude
  的成功证明不了通义那边什么，拿它去洗清一节就是在编。
* **`passed` 只在这本书被这个大脑拦过之后才记。** 成功的轮次每天几十上百，
  全记下来这张表就成了一份对话流水；而在第一次被拦之前，那些记录一个字都用不上。
  代价是成功那条路上多一次 EXISTS 查询，走 `(book_id, brain_name, …)` 那个索引。
* **没收窄到一节就一节都不点名。** 随便挂一节重墨等于凭空指认，用户会照着去删
  一节没问题的正文。这时该说的是「下一步怎么把范围缩小」——那同样是一个可执行
  的动作，不是一句「暂时无法确定」。

### 实测走查（真 HTTP / 真 SSE / 真库 / 假模型端点装成通义）

假模型在模型名里看到 `block`、且送过去的正文里有那一节的某句话时，回一个
400 + `data_inspection_failed` —— 形状照实测里 DashScope 回的那个抄的。
**按内容拦而不是无条件拦**，否则演示不出「一次成功能洗清那几节」这半边。

```
③ 整本送过去          → failed；消息里的命令行字样：一个都没有 ✓
④ 拦 1 次             → 嫌疑 16 节，不点名，建议教「把预算调小再问一次」
⑤ 调小预算又被拦      → 嫌疑 16 → 4 节（扉页 / 第一部 / 第二部 / 轮回）
⑥ 换个问题，这次说完了 → 嫌疑 4 → 1 节【轮回】，pinned=true
                         目录树上挂重墨的节：['轮回']
                         建议：「每次被拦时都在场的只有《轮回》这一节 —— 多半是它触发的」
拦截次数仍是 2 —— 成功不算进「拦了几次」
```

「轮回」正是 Plan 里写着的、实测中反复被拦的那一节。

台账里存下来的东西（三行）：

```
blocked  走查审核  16 节  '走查审核 请求失败 HTTP 400：Input data may contain inappropriate cont…'
blocked  走查审核   4 节  同上
passed   走查审核   4 节  （空）
```

**不存请求体、不存 key**，`detail` 截到 500 字。服务端日志 41 行，
`sk-walkthrough` / `X-Brain-Key` / `password` / `@` / `postgresql` /
`booksoul:booksoul` / `inappropriate` 各出现 **0 次**。

### 新表 `moderation_events`（迁移 0002）

第二个迁移。三个决定：

* **通过与拦截同表**，`outcome` 分辨。它们说的是同一件事的两面：「送了这几节，
  结果如何」。拆两张表，取交集那段代码就要同时认两种形状。
* **append-only。** 不更新不删除，所以没有 `updated_at`，也不挂 touch 触发器。
  每一行都是一个发生过的事实，而「拦了几次」本身就是界面要显示的东西。
* **`chapter_ids` 存 jsonb 而不是关联表。** 它是一份**快照**：重解析之后那些 id
  就不存在了，而这条证据说的正是「当时送出去的是那几节」。做成外键关联表，
  重解析会把证据连带删掉。界面照样拿 id 去对当下的章节，对不上的自然不显示。

### 本片留白（已就地注明，不是遗漏）

- **②屏那条「被拦过」提示没做。** S6 的记录里把它记在 S13 名下。④屏那条已经
  说清楚了，②屏是解析报告——审核是运行时的事，不是解析发现的，放进那本账会让
  「每一行都对应一件解析时真发生过的事」这条纪律破掉。
- **拦截记录没有「我知道了」。** 它不是 `parse_note`，没有 `resolved`。
  换掉大脑之后那条提示还在——它陈述的是一个仍然成立的事实（这本书在那个大脑
  那里会被拦），不是一条待办。
- **`_MODERATION_MARKS` 是字样匹配，宁可漏判不错判。** 漏判只是少给一条建议，
  错判会把一个 key 填错的人支去换大脑。
- **④屏与⑦屏只验了接口与构建，没在浏览器里看过。**

### 规范候选追加（累积到全部切片完成时一次性裁定）

42. **把结论揉进一句给人看的话里，等于把它扔了。** 判断出来的类别要单独带出来
    （`BrainError.kind`），否则上层只能去匹配文案，而文案一改分支就坏、且不报错。
    → `backend.md`「设计模式与约定」
43. **同一个结论，出路要分场合写。** CLI 的出路是一条能敲的命令，网页的出路是
    一个能点的位置。共用一句话必然有一边执行不了。→ `backend.md`「异常消息规范」
44. **消费一次性资源（流、游标）的函数要把取值和判断拆开。** `exc.read()` 只能
    读一次，揉在一个函数里就没法把同一份数据喂给第二个判断。
    → `backend.md`「设计模式与约定」
45. **分不清就不要点名。** 界面上「指认某一个具体对象」这件事，证据不足时要退成
    「说明怎么把范围缩小」，而不是随便挑一个。挑错的那次不会报错，用户会照着做。
    → `frontend.md`「设计模式与约定」
46. **诊断要先榨干已有的数据。** 台账、日志、已经发生过的请求里往往已经有答案，
    不必为了定位再去多花一次调用。→ `backend.md`「设计模式与约定」

### 待回写设计文档（交 `design-doc-sync` 收尾时处理，不在本片改）

| 文档 | 现状 | 应改为 |
|------|------|--------|
| `backend.md` 引擎改动清单 | 12 处 | 补第 13 处：`BrainError.kind`（向后兼容，CLI 零影响） |
| `backend.md` 模块表 | 无 `moderation` | 补 `services/moderation`（交集 − 并集定位触发章节）、`repositories/moderation_repo` |
| `backend.md` 端点表 | `GET /api/books/{id}` | 补 `moderation`（含 `pinned` / `advice`）与 `chapters[].blocked` |
| `database/sql.md` | 11 张表 | 补第 12 张 `moderation_events`，连同三个设计决定的理由 |
| `database/sql.md` 迁移清单 | 只有 0001 | 补 0002 |
