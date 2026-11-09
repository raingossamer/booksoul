---
status: done
---

# Brain Model Picker — Spec

> 上游：`Todo/Grills/brain-model-picker-grill.md`（D1–D7 已逐条确认）。
> 需求原话：「填入 key 之后能根据填入的 key 做下拉列表去选模型，选择模型去当大脑。」

## Problem Statement

九个内置大脑（claude / qwen / deepseek / kimi / glm / siliconflow / openai / ollama / lmstudio）
各自的模型写死在引擎的预设里 —— qwen 永远是 `qwen-plus`，claude 永远是 `claude-opus-5`。
用户在⑧屏填好某家的 key 之后，**没有办法用这家的其他模型**。今天唯一的出口是「添加自定义
端点」：自己去查那家的 base_url、手敲模型名、手填上下文窗口 —— 而且大多数人根本不知道
这个 key 下都有哪些模型可选。

更糟的是，就算用户这样绕过去了，系统在两处会**悄悄说错话**，且都不报错：

1. **自定义端点的窗口在提问时被无视。** ⑧屏「装得下吗」按用户填的窗口算，提问时的真实
   预算却只查内置表；表里认不出的模型名一律按 10 万字算。于是⑧屏说「整本装得下」，
   真提问时却在挑章节；反过来小窗口模型会被塞超长请求，供应商直接报错。
2. **会话的模型有两份真相。** 开会话时存了一份模型名的快照，界面显示和预算都读它；
   真正调模型却每次现查当下的配置。今天预设写死所以两份永远一样；一旦模型可以换，
   旧会话的下一轮会**调新模型、显示和预算却还按旧模型**。

## Solution

在⑧屏每个已存在的大脑（内置预设与已保存的自定义端点）的「模型」格旁加一个「换模型」：
展开后点「列出模型」，服务端拿用户这次带来的 key 去问那家「你有哪些模型」，把结果填进
下拉框（可搜索，对话类模型排前）；列不出来就地退回输入框，并说清楚是哪一类失败。
选定后再给出这个模型的上下文窗口 —— 供应商报了就用、内置表认得就用、都不知道就明说
「这是估计值」并请用户填真实的。保存之后：

- ⑧屏、书籍详情「装得进哪个大脑」立刻按新模型与新窗口判断；
- **以后新开的会话**用新模型，并把模型和正文额度一起锁进会话；
- **已经开着的会话**继续用开会话时那个模型 —— 显示的、算预算的、真正调用的，永远是同一个；
- 随时可以「恢复默认」。

key 的纪律不变：只存在浏览器，经请求头送来，服务端用完即丢。

## User Stories

**列出模型**

1. As a 读者, I want 填好某家的 key 之后点一下就看到这个 key 下能用的模型, so that 我不必去翻那家的文档才知道有哪些可选
2. As a 读者, I want 下拉列表可以搜索, so that 聚合平台返回几百个条目时我也能很快找到想要的那个
3. As a 读者, I want 对话类模型（名字里带 chat / instruct / turbo 的）排在前面, so that 我不用在一堆 embedding、rerank、图像模型里翻找
4. As a 读者, I want 看到「共 N 个，来自 {地址}」, so that 我知道这份列表是从哪来的、是不是我以为的那个服务
5. As a 读者, I want 还没填 key 时点「列出模型」得到一句「先填 key」, so that 我不会以为是功能坏了
6. As a 用本机 Ollama / LM Studio 的读者, I want 不填 key 也能列出本机的模型, so that 本地模型的选择和云端一样顺手

**列不出来的时候**

7. As a 读者, I want 在「这个服务不提供模型列表」时直接得到一个输入框, so that 我照样能手填模型名，功能不比今天更差
8. As a 读者, I want 在 key 被拒时被告知「key 没通过验证」而不是「列不出模型」, so that 我知道该回去检查 key 而不是去猜别的
9. As a 读者, I want 在超时或连不上时被告知是网络的问题, so that 我知道可以稍后再试
10. As a 读者, I want 手填的模型名不在刚拉到的列表里时得到一句提醒但不被拦住, so that 拼错能被发现，而列表本来就不全的那些服务我也照样能用

**上下文窗口**

11. As a 读者, I want 供应商在列表里报了窗口时直接用它，并标「来自供应商」, so that 我不用自己查
12. As a 读者, I want 内置表认得的模型直接用内置值，并标「内置」, so that 常见模型开箱即用
13. As a 读者, I want 系统不知道窗口时明说「估计值 —— 建议填上真实窗口」, so that 我不会被一句底气十足的「整本装得下」骗了
14. As a 读者, I want 能把估计值改成我在供应商文档上查到的真实窗口，并标「你填的」, so that 「装得下吗」按真实情况判断
15. As a 读者, I want 下次打开⑧屏时仍能看到这个窗口是从哪来的, so that 我知道哪一格该复核

**选定之后**

16. As a 读者, I want 保存后⑧屏「正文额度」「这本书装得下吗」立刻按新模型更新, so that 我在开会话之前就知道这本书会整本读还是挑章节
17. As a 读者, I want 书籍详情页「装得进哪个大脑」也按我选的模型判断, so that 两个页面不会给出两个结论
18. As a 读者, I want 之后新开的会话用我选的模型, so that 选择真的生效
19. As a 读者, I want 已经开着的会话继续用它开始时的那个模型, so that 旧对话的整本书缓存不作废、也不会被悄悄算成全价
20. As a 读者, I want 会话里显示的模型、算预算用的模型、实际调用的模型永远是同一个, so that 界面不会对我说谎
21. As a 读者, I want 想用新模型聊同一本书时开一个新会话就行, so that 旧会话的历史仍然如实记着「当时是谁答的」
22. As a 读者, I want 一键「恢复默认」, so that 试坏了能回到开箱时的状态
23. As a 读者, I want 在另一台电脑上登录时看到同样的模型选择, so that 选择跟着账号走（key 仍需在那台机器上重填）

**自定义端点**

24. As a 加过自定义端点的读者, I want 保存之后的端点也能「列出模型」来换, so that 不必删掉重建
25. As a 加过自定义端点的读者, I want 我填的窗口在提问时真的被用上, so that ⑧屏说「整本装得下」时提问确实整本读
26. As a 新建自定义端点的读者, I want 新建表单保持手填模型名, so that 流程不变（存下来之后再换即可）

**安全与边界**

27. As a 读者, I want 我的 key 在列模型时同样不被存下、不进日志、不出现在任何响应里, so that 新功能不削弱既有的承诺
28. As a 读者, I want 列模型失败时服务端不把对方返回的原文回显给我, so that 一个指向内网的自定义地址不会被用来窥探内网
29. As a 读者, I want 看不到、也改不了别人的模型选择和自定义端点, so that 账号之间互相隔离
30. As a 读者, I want 模型名过长或为空时被当场拦下并说清楚限制, so that 不会存进一个下次开会话才炸的值

**不变的东西**

31. As a 老用户, I want 已有的会话在升级之后行为完全不变, so that 升级不改写任何历史
32. As a 命令行用户, I want `booksoul ask` 的行为完全不变, so that 网页版的功能不影响 CLI
33. As a 运维者, I want 内置预设的地址永远以代码为准, so that 供应商换地址时不会有人手里攥着一份过期的抄本

## Implementation Decisions

### 采信 Grill 报告（D1–D7，不再重复论证）

D1 下拉 + 手填并存、失败分类 / D2 选择只影响新会话、修调用与快照的分叉 /
D3 新表存「对内置预设的模型选择」、解析 = 预设 + 选择 / D4 窗口三级取值、未知时明示估计、
随模型锁进会话 / D5 后端代理、列模型逻辑进引擎、只回 id 不回原文、复用 `BrainError.kind` /
D6 第一版只给已存在的大脑 / D7 何时拉、不缓存、手填提示不拦、本机端点不带 key。
细节见 Grill 报告第 1 节。

### 新增决策（本次访谈确定，未经拷问）

- **N1 · 会话快照的是「正文额度（字）」而不是窗口（token）。** Grill D4 写的是
  `conversations.context_tokens`，改为 `conversations.context_chars`。理由：提问时的预算和
  「装得下吗」用的都是字数；内置表命中时手里**只有字数、没有 token**，从字数倒推 token 是有损的。
  0 表示历史会话，走今天的老路径。
- **N2 · 选择表存窗口的来源**（空串 / `vendor` / `manual`）。不存的话，下次打开⑧屏就说不出
  这个数字是供应商报的还是用户填的（要重新拉一次列表才知道）。窗口为 0 时来源由解析时推出
  （`builtin` / `estimate`），不落库。用 VARCHAR + CHECK 而非 ENUM —— 沿用
  `moderation_events.outcome` 的先例：PostgreSQL 加枚举值不能在事务里回滚。
- **N3 · 引擎新增「认不认得这个模型」的查询。** 今天的 `context_budget_chars(model)` 只回一个数，
  分不清「内置表命中」和「落到 default」，而 D4 要在界面上把这两者分开说。新增
  `known_context_chars(model) → int | None`，`context_budget_chars` 改为建在它之上 ——
  内置表仍是**选章判据的唯一来源**，没有第二份。
- **N4 · API 用 `{name}` 而不是 `{id}` 定位大脑。** 内置预设不在库里、没有 id；名字在同一用户下
  唯一（预设之间唯一，自定义端点被禁止与预设重名）。既有的 `DELETE /api/brains/{id}` 不动。
- **N5 · 自定义端点换模型直接改它自己那一行**，不进选择表；「恢复默认」只对内置预设有意义，
  对自定义端点返回 400 并说明「自定义端点没有默认模型，要换就直接选一个」。
- **N6 · 列表排序在服务端做**（对话类排前、其余保持供应商原序），以便有测试钉着；搜索在前端做。
- **N7 · 表名 `brain_choices`。**
- **N8 · ⑧屏的交互位置**：`BrainRow` 的「模型」格加「换模型」，在该行下方展开 `ModelPicker`；
  同一时刻只展开一行。
- **N9 · 预算的优先级明确为三级**：会话上用户手调的载入预算（`budget_chars`，今天已有）>
  会话快照的正文额度（`context_chars`，本功能新增）> 老路径（按模型名查内置表）。

### 模块

**引擎（`booksoul`，CLI 与网页共用，不依赖 Web 框架）**

- **大脑模块新增「列出模型」**：输入一份大脑配置（provider、base_url、key），输出模型条目列表
  （id，以及供应商若报了的窗口）。Anthropic 走官方 SDK 的模型列表接口；OpenAI 兼容端点走标准库
  HTTP 的 `GET {base_url}/models` —— 与现有 `chat` 的分工一致。失败抛带 `kind` 的 `BrainError`：
  `auth`（401 / 403）、`unsupported`（404 / 405 / 返回格式不认识）、`timeout`、`network`；
  沿用现有 HTTP 错误分类，不另写一套。**绝不把对方响应体放进异常消息。**超时约 10 秒。
  供应商报窗口所用的字段名各家不同，以实测为准，逐家记入计划完成记录（Grill R1）。
- **问答模块新增 `known_context_chars(model)`**（见 N3）。

**服务（`booksoul_api`）**

- **新的纯逻辑深模块 `context_window`**：输入（模型名、窗口 token 数、存下的来源），输出（正文额度、
  来源 ∈ `vendor` / `manual` / `builtin` / `estimate`）。规则：窗口 > 0 → 按引擎的
  `budget_from_context_tokens` 折算，来源取存下的那个；否则内置表认得 → `builtin`；
  否则保守估计 → `estimate`。**⑧屏列表、书籍详情的「装得下吗」、开会话快照，三处只调这一个函数。**
  自定义端点同样走它（`brains.context_tokens` > 0 时来源视为 `manual`）。
- **`brain_service`**：
  - 列表：每个内置预设与该用户的选择合并（模型与窗口取选择的，provider / base_url / key 名永远取预设的）；
    自定义端点的窗口也改走 `context_window`。每项带出 `defaultModel`、`modelChosen`（是否改过）、
    `contextSource`。
  - `config_for`：内置预设返回合并后的模型。
  - 列出模型：按名字取到大脑（找不到 → 404，越权同样 404）；需要 key 而没带 → 400「先填 key」；
    调引擎；按 N6 排序；只回 id 与窗口。
  - 存选择：内置预设 → 写或覆盖选择表那一行；自定义端点 → 改它自己那行的 `model` / `context_tokens`（N5）。
  - 恢复默认：内置预设 → 删掉那一行；自定义端点 → 400（N5）。
  - 选择表里名字对不上任何预设的行（预设被改名或删除）→ 解析时忽略，列表里不显示，不报错（Grill R4）。
- **`chat_service`**：
  - 开会话：把合并后的**模型**与 `context_window` 算出的**正文额度**一起快照进会话。
  - 提问：真正调模型时用会话快照的模型覆盖配置里的那个（修 Grill C2）；预算按 N9 三级取
    （修 Grill C3）。其余行为不变。
- **仓储**：新增选择表的仓储；每个查询带 `user_id`（`backend.md`「分层职责」）。

**前端**

- `api/brains`：列出模型、存选择、恢复默认三个方法。
- `api/client`：`X-Brain-Key` 的注入白名单增加「列出模型」这一个端点。
- `BrainRow` + 新组件 `ModelPicker`：下拉 + 搜索 + 手填 + 窗口输入与来源标注 + 保存 / 恢复默认 +
  按失败类别给出的提示；「正文额度」「这本书」两格随保存后的列表刷新。
- 类型：大脑列表项增加 `defaultModel` / `modelChosen` / `contextSource`；模型条目类型。

### Schema 变更（迁移 `0003`）

- **新表 `brain_choices`**：`id`（BIGINT IDENTITY）、`user_id`（→ `users`，CASCADE）、
  `brain_name` VARCHAR(32)、`model` VARCHAR(64)、`context_tokens` INTEGER 默认 0、
  `context_source` VARCHAR(8) 默认空串（CHECK ∈ 空串 / `vendor` / `manual`；且窗口为 0 时来源必须为空串）、
  `created_at`、`updated_at`（由 `touch_updated_at` 触发器唯一负责，按 `sql.md` 约定）。
  约束：`UNIQUE (user_id, brain_name)`（其首列同时满足「外键列要有索引」）。
  **与内置预设按名字弱关联、不做外键** —— 预设不在库里。非平凡列写 `COMMENT ON COLUMN`。
- **`conversations` 新增 `context_chars` INTEGER NOT NULL DEFAULT 0**：开会话时快照的正文额度；
  0 = 历史会话走老路径。PostgreSQL 11+ 带常量默认值加列是元数据操作，不重写表。不回填。
- `brains` 表不变。

### API 契约

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/brains/{name}/models` | 列出这个大脑在这个 key 下的模型。需要 key 的大脑经 `X-Brain-Key` 带来。响应：`{ items: [{ id, contextTokens }], source }`，`contextTokens` 未知为 0，`source` 是被问的地址。失败：404（没有这个大脑）/ 400（需要 key 而没带）/ 502 带 `code` ∈ `brain_auth` / `brain_unsupported` / `brain_timeout` / `brain_network`，**不含对方原文** |
| PUT | `/api/brains/{name}/model` | 存选择。请求：`{ model, contextTokens, contextSource }`。`model` 去首尾空白后 1–64 字、不含控制字符；`contextTokens` 0–10,000,000；`contextSource` ∈ 空串 / `vendor` / `manual`，窗口为 0 时必须为空串。响应：更新后的那一项大脑（与 `GET /api/brains` 同形） |
| DELETE | `/api/brains/{name}/model` | 恢复默认（仅内置预设）。204；自定义端点 400 |
| GET | `/api/brains` | **形状扩展（兼容）**：每项新增 `defaultModel` / `modelChosen` / `contextSource` |

失败的 `code` 是写死的字面量，前端按它分支，不匹配中文文案（`backend.md`「接口与响应」）。

## Testing Decisions

**什么是好测试**：只断言外部可观察的行为 —— 接口响应、落库结果、以及「真正交给引擎去调模型的
那份配置」；不断言私有函数、不断言内部调用次序。替身换在**引擎那一层**（HTTP 与 SDK 的出口），
不在服务层另开测试分支（与 `conftest.fake_brain` 同一思路）。

**会写测试的模块**

- **引擎「列出模型」**：两种供应商形状各一组；四类失败各自映射到正确的 `kind`；**异常消息里永远
  不出现对方响应体**（喂一个带特征串的错误体，断言特征串不出现）；带窗口字段时取到窗口。
- **引擎 `known_context_chars`**：认得的返回额度、认不得返回 None；`context_budget_chars` 对全部
  既有输入的结果不变（回归）。
- **`context_window` 纯逻辑**：四种来源各一条；与既有 `budget_from_context_tokens` 的折算一致。
- **服务 / 接口边界**：
  - 存选择 → `GET /api/brains` 反映新模型、新额度、`modelChosen`、`contextSource`；书籍详情的
    「装得下吗」同步变化。
  - 恢复默认 → 回到预设；自定义端点恢复默认 → 400。
  - 开会话快照模型与额度；**已开会话在改了选择之后下一轮仍用原模型**（断言替身收到的配置里的
    模型名，Grill C2 回归）。
  - **自定义端点填 1M 窗口 + 表里认不出的模型名 → 提问时按 1M 折算的额度走，不按 10 万**
    （Grill C3 回归）。
  - 历史会话（快照为 0）行为与今天逐字相同。
  - 越权：别人的自定义端点 → 404；看不到别人的选择。
  - 校验：空名、超长、控制字符、窗口越界、来源与窗口不一致 → 422 并说清限制，**不回显被校验的值**。
  - 选择表里指向不存在预设的行被忽略。
- **key 卫生**（放在既有 key 规则测试旁）：列模型的请求头不进日志；响应与错误里不出现 key；
  需要 key 而没带时不去打供应商。
- **漂移检测**：新实体由既有测试自动覆盖（`models` 包未导出即红）。

**前端**：不引入测试框架，靠 `vue-tsc` 类型检查与 eslint；交互由人工在⑧屏走一遍并记入计划完成记录。

**先例**：`test_brain_key`（key 不外泄）、`test_brains_api`（大脑接口）、`test_retrieval`
（预算与锁定）、`test_moderation`（`BrainError.kind` 分类）、`conftest.fake_brain`（引擎层替身）。

## Out of Scope

- **新建自定义端点表单里的下拉**（Grill D6）：新建时仍手填，存下来之后再换。不开「拿任意地址去探」的端点。
- **服务端缓存模型列表**（Grill D7）。
- **已开会话中途换模型**（Grill D2）：要换就开新会话。
- **内置预设的地址 / provider 可编辑**：永远以代码为准。
- **平台级内容审核**（通义拦《悉达多》）：与本功能无关，换同平台的模型解决不了。
- **修正 `brains` 表文档的过期描述**（Grill C1）：归收尾的 design-doc-sync，不在实施切片里。
- **前端单元测试框架**。
- **模型的价格、能力标签**：只列 id 与窗口。

## Further Notes

- **兼容性**：无破坏性变更。`GET /api/brains` 只增字段；新增三个端点；迁移只加表加列、不改不删、
  不回填。CLI 路径不受影响（引擎只新增函数，`context_budget_chars` 对既有输入结果不变）。
- **两处既有缺陷随本功能修掉**（Grill C2、C3）：它们今天在生产上存在但不显形（内置预设写死、
  《悉达多》足够小），本功能上线后会被频繁踩中，所以必须同一轮修。建议切片计划把「会话快照」
  排在「新表」之前 —— 它不依赖新表、可以独立验证，而且修的是已存在的问题。
- **各家 `/v1/models` 的真实行为需要真 key 实测**（Grill R1）：DashScope 兼容模式、智谱是否提供；
  返回的是否是 key 真有权限的那批；窗口字段叫什么。结果记入计划完成记录；列不出来的由手填兜底。
- **SSRF 面**（Grill R2）：服务端会按已保存的自定义端点地址发请求 —— 提问那条路今天已然如此；
  列模型这条靠「只回 id 不回原文」限住回显，且不开任意地址探测。部署仍须绑 127.0.0.1。
- **需要更新的设计文档**见 Grill 报告第 3 节；另需在 `backend.md`「API 端点」补三个端点、
  在 `frontend.md`「API 调用层」更新 `X-Brain-Key` 白名单。
