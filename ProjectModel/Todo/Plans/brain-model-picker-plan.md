---
status: done
---

# Brain Model Picker — Implementation Plan

## Parent Spec / RFC

- File: `ProjectModel/Todo/Specs/brain-model-picker-spec.md`（`status: active`）
- 上游拷问：`ProjectModel/Todo/Grills/brain-model-picker-grill.md`（D1–D7）
- 模式：**主流水线（Spec 模式）**，垂直切片。

## Plan Overview

十片，三条线最后汇到第 8 片：

- **修缺陷线（1 → 2 → 3）**：先修两处今天就存在、只是还没显形的分叉（Grill C2、C3）。
  **切片 1 修的那处今天就能被触发** —— 改一下 `booksoul.toml` 里某个预设的 `model`，已开会话的
  下一轮就会调新模型、却显示旧的。这条线不依赖任何新表，可以最先做、独立验证。
- **列模型线（4 → 5 / 6 → 7）**：第 4 片是整个功能的曳光弹 —— 填了 key 的 OpenAI 兼容预设，
  点一下看到下拉，端到端最窄的一条。第 5 片补齐失败分类与边界，第 6 片补 Anthropic。
  **第 7 片是 HITL**：只有你手里有真 key，要在⑧屏逐家点一遍，确认各家到底给不给列表、
  窗口放在哪个字段。它排在「存选择」之前 —— 你主要用的 qwen（DashScope 兼容模式）如果不给
  列表，要在建存储之前就知道。
- **存选择线（8 → 9 / 10）**：第 8 片落选择表、让选择生效；第 9 片补手填；第 10 片把同一套
  交互开放给自定义端点。

**迁移拆成两个**（Spec 写的是一个 `0003`）：`0003` 给会话加快照列（切片 3），`0004` 建选择表
（切片 8）。按 `sql.md`「每次 schema 变更新增一个 revision」，两片各自可独立上线。

所有切片的公共验收：**全量测试保持绿色**；涉及前端的切片 `vue-tsc` 与 `eslint` 干净，
并在⑧屏人工走一遍记入完成记录（前端没有测试框架，Spec「Testing Decisions」已裁定不引入）。

## Slices

### Slice 1: 提问用会话快照的模型去调（修 C2）

- **Type**: AFK
- **Blocked by**: None
- **User stories**: #19, #20, #31
- **Complexity**: S

**What to build**:

今天同一个会话的模型有两份真相：开会话时存下的快照（显示与预算读它），和真正调模型时现查的
当下配置。让**真正调模型也用快照**，三者永远是同一个。见 Spec「新增决策 N9」之前的
「`chat_service` · 提问」一条、Grill C2。

这一片**不依赖本功能的任何新东西**：改预设模型今天就能做到（编辑 `booksoul.toml`），
所以测试直接模拟「开会话之后预设的模型被改了」。

**Acceptance criteria**:
- [ ] 开会话之后预设的模型被改，这个会话下一轮真正调用的仍是开会话时的模型（以替身收到的配置为证）
- [ ] 改动之后新开的会话用改过的模型
- [ ] 覆盖模型之后，大脑配置的 `repr` 里仍然不含 key
- [ ] 全量测试绿

**Implementation hints**:
- 在提问路径里拿到大脑配置之后、交给引擎之前，用会话快照的模型替换配置里的模型；用 dataclass 的
  替换方式而不是改字段（配置是被多处读的）
- 替身目前只记 system 与 messages；要断言「调的是哪个模型」，需要让它把收到的配置也记下来
- 不动「开会话」与「显示」那两处 —— 它们本来就读快照

---

### Slice 2: 窗口来源看得见 —— 内置 / 估计 / 你填的

- **Type**: AFK
- **Blocked by**: None
- **User stories**: #12, #13, #17, #32
- **Complexity**: M

**What to build**:

⑧屏的「正文额度」和「这本书装得下吗」今天对所有模型都说得一样有底气，哪怕那个窗口是按默认值
猜的。让界面**说出这个数字是从哪来的**：内置表认得 →「内置」；自定义端点填了窗口 →「你填的」；
都没有 →「估计」，并且那一行不再给出「整本装得下」这种结论，而是「窗口未知（按估计）」。

两处依赖：引擎新增「认不认得这个模型」（Spec N3），服务新增纯逻辑深模块 `context_window`
（Spec「模块」一节）。**⑧屏与书籍详情「装得进哪个大脑」读同一个结果。**

**Acceptance criteria**:
- [ ] 内置预设显示「内置」；填了窗口的自定义端点显示「你填的」；模型名认不出且没填窗口的显示「估计」
- [ ] 「估计」那一行的「这本书」一格是「窗口未知（按估计）」，不是「整本装得下」
- [ ] ⑧屏与书籍详情对同一个大脑给出同一个结论
- [ ] `context_budget_chars` 对全部既有输入（内置表每个键、认不出的名字、带 override）结果逐一不变；CLI 行为不变
- [ ] `context_window` 四种来源各有一条测试
- [ ] 全量测试绿；`vue-tsc` + `eslint` 干净

**Implementation hints**:
- `context_budget_chars` 改为建在「认不认得」之上，不另立第二张表 —— `ask.py` 那张表是
  「选章判据的唯一来源」
- 大脑列表项增加 `contextSource`；本片不存来源（没有选择表），`vendor` 这个值要到切片 8 才会出现
- 预期可见变化：内置预设里模型名不在表里的（很可能是 lmstudio）会从一个看似确定的数字变成「估计」。
  这是如实，不是回归 —— 完成记录里写明是哪几个
- 既有的大脑接口测试若对响应做整体相等比较，会因为多了字段而红，改成按字段断言

---

### Slice 3: 会话快照正文额度，提问按快照算预算（修 C3，迁移 0003）

- **Type**: AFK
- **Blocked by**: Slice 2
- **User stories**: #20, #25, #31
- **Complexity**: M

**What to build**:

自定义端点填的窗口，⑧屏用了、提问时却被无视（提问只按模型名查内置表，认不出就按 10 万字）。
让**开会话时把 `context_window` 算出的正文额度快照进会话**，提问时按 Spec N9 的三级优先级取预算：
用户手调的载入预算 > 会话快照的正文额度 > 老路径。快照为 0 表示历史会话，走老路径。

**Acceptance criteria**:
- [ ] 开会话时快照下来的正文额度，与⑧屏对这个大脑显示的额度是同一个数
- [ ] 自定义端点填**小**窗口、模型名不在内置表里、书比那个窗口大 → 提问时挑章节（今天会整本送，然后被供应商拒）
- [ ] 自定义端点填**大**窗口、模型名不在内置表里 → 提问时按大窗口判断（今天按 10 万字）
- [ ] 用户手调的载入预算仍然优先于快照
- [ ] 历史会话（快照为 0）的选章与预算逐字不变
- [ ] 迁移 `0003` 可升可降；漂移检测绿
- [ ] 全量测试绿

**Implementation hints**:
- 迁移：`conversations` 加 `context_chars INTEGER NOT NULL DEFAULT 0` 并写 `COMMENT ON COLUMN`；
  PostgreSQL 11+ 带常量默认值加列是元数据操作；不回填
- 测试用小数字最省事：窗口给到只装得下一两节，就能观察「挑没挑章节」
- ⑥屏显示的预算来自选章留档，而留档里的预算来自这一轮真正用的那个值 —— 改完之后两者自然一致，不用另动

---

### Slice 4: 列出模型 · 走通（OpenAI 兼容，成功路径）

- **Type**: AFK
- **Blocked by**: None
- **User stories**: #1, #2, #3, #4, #11, #27
- **Complexity**: M

**What to build**:

曳光弹：填了 key 的 OpenAI 兼容预设（qwen、deepseek、kimi…），在⑧屏那一行点「换模型」，
展开 `ModelPicker`，点「列出模型」，看到可搜索的下拉，对话类模型排前，上方写着
「共 N 个，来自 {地址}」。本片**只读** —— 能看不能存，存在切片 8。

路径：引擎的大脑模块新增「列出模型」（OpenAI 兼容这一支）→ 服务按名字找到大脑、带上 key 调引擎、
排序 → `GET /api/brains/{name}/models` → 前端。见 Spec「模块」「API 契约」、Grill D5。

**Acceptance criteria**:
- [ ] 填了 key 的 OpenAI 兼容预设，点「列出模型」看到下拉；可搜索；名字含 chat / instruct / turbo 的排前，其余保持供应商原序
- [ ] 显示「共 N 个，来自 {地址}」
- [ ] 响应只含模型 id（与供应商报了的窗口），不含对方响应里的其他任何字段
- [ ] 供应商条目里带窗口字段时取到窗口
- [ ] key 经 `X-Brain-Key` 送达并转给供应商，不出现在任何响应与日志里
- [ ] 同一时刻只展开一行
- [ ] 全量测试绿；`vue-tsc` + `eslint` 干净

**Implementation hints**:
- 引擎侧与 `chat` 共用同一套 HTTP 基础（地址拼接、鉴权头），不另起一套
- 引擎调用是阻塞的，服务层放进工作线程
- 替身换在引擎的 HTTP 出口，不在服务层另开分支
- 窗口字段名各家不同，本片先按常见的几个名字取，切片 7 实测后补齐
- `client.ts` 注入 `X-Brain-Key` 的白名单增加这一个端点

---

### Slice 5: 列出模型 · 失败与边界

- **Type**: AFK
- **Blocked by**: Slice 4
- **User stories**: #5, #6, #7, #8, #9, #27, #28, #29
- **Complexity**: M

**What to build**:

把 D1 那句「说清是哪一类失败」做实。四类失败各自一句人话、各自一个写死的 `code`：
key 被拒（`brain_auth`）、这个服务不提供列表（`brain_unsupported`）、超时（`brain_timeout`）、
连不上（`brain_network`）。外加三条边界：需要 key 而没带 → 400「先填 key」且**不去打供应商**；
本机端点（ollama / lmstudio）不需要 key，照常列；别人的自定义端点 → 404。

**失败时绝不把对方响应原文带回来** —— 那是「用一个指向内网的自定义地址窥探内网」的口子（Grill D5②）。

**Acceptance criteria**:
- [ ] 401 / 403 → `brain_auth`；404 / 405 / 返回格式不认识 → `brain_unsupported`；超时 → `brain_timeout`；连接失败 → `brain_network`；均为 502
- [ ] 供应商错误体里的特征串不出现在响应、异常消息、日志里
- [ ] 需要 key 而没带 → 400「先填 key」，且供应商没有被调用
- [ ] 本机端点不带 key 也能列出，请求里没有鉴权头
- [ ] 别人的自定义端点名 → 404，与不存在同一个 404
- [ ] 前端按 `code` 分支给出对应提示；`brain_unsupported` 提示「这个服务没提供模型列表，直接填模型名」（输入框在切片 9）
- [ ] 全量测试绿；`vue-tsc` + `eslint` 干净

**Implementation hints**:
- 复用既有的 HTTP 错误分类与 `BrainError.kind`，不另写一套；按 `backend.md`「消费一次性资源的函数，
  要把取值和判断拆开」，错误体只读一次
- 前端按 `code` 分支，不匹配中文文案（`backend.md`「接口与响应」）

---

### Slice 6: 列出模型 · Anthropic

- **Type**: AFK
- **Blocked by**: Slice 4
- **User stories**: #1, #11
- **Complexity**: S

**What to build**:

claude 那一行也能「列出模型」。引擎走官方 SDK 的模型列表接口（与现有 `chat` 走 SDK 一致），
SDK 的异常映射到与切片 5 相同的四类 `kind`。前端无需改动 —— 同一个端点、同一个组件。

**Acceptance criteria**:
- [ ] claude 填了 key 后能列出模型
- [ ] SDK 的鉴权失败 / 不存在 / 超时 / 连接失败分别映射到四类 `kind`
- [ ] SDK 未安装时给出 `pip install` 提示而不是崩（沿用「可选依赖延迟导入」）
- [ ] 全量测试绿

**Implementation hints**:
- 替身换在引擎构造 SDK 客户端的那一处
- SDK 的模型对象若带窗口字段就取；切片 7 实测确认

---

### Slice 7: 真 key 逐家实测

- **Type**: **HITL**
- **Blocked by**: Slice 5, Slice 6
- **User stories**: #1, #11（验证）
- **Complexity**: S

**What to build**:

Grill R1：各家 `/v1/models` 的真实行为未经实测 —— DashScope 兼容模式、智谱是否提供列表；返回的是否是
这个 key 真有权限的那批；窗口放在哪个字段。**只有你手里有真 key**，所以这一片需要你：

1. 在⑧屏逐家（至少 qwen，以及你有 key 的其他几家）点「列出模型」
2. 告诉我每家看到了什么：列没列出来、大约几个、有没有一堆 embedding / 图像模型、是否显示了窗口、
   列不出来时提示的是哪一类

**key 不贴进对话**，只报告你看到的结果。我据此调整引擎的解析，并按实测到的**形状**（不含任何 key）
各补一条测试。

**Acceptance criteria**:
- [ ] 至少对 qwen 实测一次，结果记入本计划完成记录
- [ ] 每家实测到的窗口字段都被解析覆盖，并各有一条按实测形状写的测试
- [ ] 列不出来的那几家确认走到了正确的失败类别，而不是笼统的「出错了」
- [ ] 若 qwen 不给列表：确认它走到 `brain_unsupported`，并记下「qwen 只能手填」这一结论，切片 9 的手填是它的主路径

**Implementation hints**:
- 本片的代码改动只在引擎的解析那一小块；如实测无需改动，完成记录写明「实测与假设一致」

---

### Slice 8: 为内置预设存选择 + 恢复默认（迁移 0004）

- **Type**: AFK
- **Blocked by**: Slice 1, Slice 3, Slice 4
- **User stories**: #11, #15, #16, #17, #18, #21, #22, #23, #29, #30, #33
- **Complexity**: L

**What to build**:

让选择生效。建 `brain_choices` 表（Spec「Schema 变更」），`PUT` / `DELETE /api/brains/{name}/model`
（Spec「API 契约」）。解析 = **预设（provider / base_url / key 名永远从代码来）+ 选择（只覆盖模型与窗口）**
（Grill D3）。保存之后：⑧屏与书籍详情立刻按新模型判断；**新开的会话**用新模型并快照模型与额度
（切片 1、3 的机制）；**已开的会话**不受影响。前端 `ModelPicker` 从下拉选一个 → 保存；
改过的行出现「恢复默认」。

本片只处理内置预设；自定义端点在切片 10。

**Acceptance criteria**:
- [ ] 从下拉选一个模型保存后，⑧屏与书籍详情立刻按新模型与新窗口判断；刷新页面、换一台机器登录后仍在
- [ ] 保存后新开的会话用选中的模型（替身收到的配置为证）；已开的会话下一轮仍用它原来的模型
- [ ] 下拉条目带供应商窗口时，保存为「来自供应商」，下次打开⑧屏仍这样标；不带时按「内置 / 估计」解析
- [ ] 「恢复默认」后模型、窗口、来源全部回到预设，`modelChosen` 为假
- [ ] 预设的地址与 provider 不随选择变化（选择表里没有这两列）
- [ ] 非法输入（空名、超 64 字、控制字符、窗口越界、窗口为 0 却带来源）→ 422，说清限制，**不回显被校验的值**
- [ ] 看不到、改不了别人的选择
- [ ] 选择表里指向不存在预设的行被忽略，不报错
- [ ] 迁移 `0004` 可升可降；新实体被 `models` 包导出（漂移检测会盯着）
- [ ] **（用户 2026-09-22 追加）保存 key 之后自动展开这一行并拉取模型列表**，让用户当场选默认模型；
      首次填 key 与「换一个」key 都触发（换了 key 能用的模型可能就变了）。这是 Grill D7「点了才拉」的
      修订：保存 key 本身就是用户按下去的那一下
- [ ] 全量测试绿；`vue-tsc` + `eslint` 干净

**Implementation hints**:
- 表：`id` IDENTITY、`user_id` → `users` CASCADE、`brain_name`、`model`、`context_tokens`、`context_source`
  （VARCHAR + CHECK，Spec N2）、`created_at` / `updated_at`（`touch_updated_at` 触发器唯一负责），
  `UNIQUE (user_id, brain_name)`；非平凡列写 COMMENT；与预设按名字弱关联、不做外键
- 仓储每个查询带 `user_id`
- 大脑列表项增加 `defaultModel` / `modelChosen`
- 开会话的快照直接复用切片 1、3 的路径 —— 只要 `config_for` 与 `context_window` 读到了选择，快照自然正确

---

### Slice 9: 手填模型与窗口

- **Type**: AFK
- **Blocked by**: Slice 8
- **User stories**: #7, #10, #14
- **Complexity**: M

**What to build**:

D1 的「永不关掉手填」。`ModelPicker` 里始终有输入框；列不出来（切片 5 的 `brain_unsupported` 等）时
直接落到输入框。手填的名字不在刚拉到的列表里 → 提醒但不拦。窗口输入框预填解析出的值并标来源；
用户改了就变成「你填的」；来源是「估计」时提示「建议填上真实窗口」。后端切片 8 的 `PUT` 已经接受
这些输入，本片以前端为主，加上相应的接口测试。

**Acceptance criteria**:
- [ ] 列不出来时直接出现输入框，手填保存可用，效果与下拉选择相同
- [ ] 手填名字不在已拉到的列表里时提醒但不拦；没拉过列表时不提醒
- [ ] 窗口是「估计」时有「建议填上真实窗口」的提示；改成真实窗口保存后标「你填的」，「装得下吗」按它判断
- [ ] 把窗口清成 0 保存 → 回到「内置 / 估计」
- [ ] 全量测试绿；`vue-tsc` + `eslint` 干净

**Implementation hints**:
- 提醒只在前端做，不为校验再打一次供应商（Grill D7）

---

### Slice 10: 自定义端点换模型

- **Type**: AFK
- **Blocked by**: Slice 8
- **User stories**: #24, #26, #29
- **Complexity**: M

**What to build**:

已保存的自定义端点那一行也有「换模型」。列模型沿用切片 4–5 的端点（名字能解析到自定义端点）；
保存时直接改它自己那一行的模型与窗口，不进选择表（Spec N5）；它没有「默认模型」，
`DELETE /api/brains/{name}/model` 返回 400 并说明。「添加自定义端点」的新建表单保持原样（Grill D6）。

**Acceptance criteria**:
- [ ] 已保存的自定义端点能列出模型、选一个或手填保存；⑧屏与书籍详情随之更新
- [ ] 保存后新开的会话用新模型；已开的会话不变
- [ ] 自定义端点「恢复默认」→ 400，提示「自定义端点没有默认模型，要换就直接选一个」；界面上不出现这个按钮
- [ ] 别人的自定义端点 → 404
- [ ] 新建表单行为不变（既有的大脑接口测试保持绿色）
- [ ] 全量测试绿；`vue-tsc` + `eslint` 干净

**Implementation hints**:
- 自定义端点窗口 > 0 时来源视为「你填的」（Spec「模块」`context_window` 一条）

### Slice 11: 选完大脑回到书籍详情，并标出选的是哪个（用户 2026-09-22 追加）

- **Type**: AFK
- **Blocked by**: None
- **User stories**: 追加（实测⑧屏时提出）
- **Complexity**: S

**What to build**:

从书籍详情「选大脑 →」进⑧屏（`?bookId=`）时，点选一个大脑：**能用就回，缺 key 就留**——
这个大脑已有 key、或是本机的不需要 key → 立刻回书籍详情；还缺 key → 留在⑧屏，填好 key 后再自动回。
书籍详情「装得进哪个大脑」那张表标出当前选中的那一个（朱批 = 你选的），否则回来了也看不出选了谁。
不带 `bookId` 直接进⑧屏时行为不变（不跳走）。

**Acceptance criteria**:
- [ ] 带 `bookId` 进⑧屏，点一个有 key 的（或本机的）大脑 → 回到 `/books/{bookId}`
- [ ] 点一个缺 key 的大脑 → 留在⑧屏；在这一行填好 key 保存后 → 回到书籍详情
- [ ] 书籍详情那张表标出当前选中的大脑
- [ ] 不带 `bookId` 时点选大脑不跳走
- [ ] `vue-tsc` + `eslint` 干净；人工走一遍

**Implementation hints**:
- 纯前端；「选中的大脑」本来就在 `lib/brainKeys` 的 localStorage 里（`readChosenBrain`）
- 与切片 8 的「保存 key 后自动拉模型」同时发生时：缺 key 的大脑填了 key → 先回书籍详情
  （用户是从那里来、要去开会话的）；想换模型再从⑧屏进

---

## Dependency Graph

```
Slice 1 ─────────────────────────────────────┐
                                              │
Slice 2 ──→ Slice 3 ──────────────────────────┼──→ Slice 8 ──┬──→ Slice 9
                                              │              └──→ Slice 10
Slice 4 ──┬──→ Slice 5 ──┬──→ Slice 7 (HITL)  │
          │              │                    │
          └──→ Slice 6 ──┘                    │
          └───────────────────────────────────┘
```

推荐执行顺序：**1 → 2 → 3 → 4 → 5 → 6 → 7(HITL) → 8 → 9 → 10 → 11**（11 无依赖，追加于 2026-09-22）

切片 7 对切片 8 不是硬依赖，但建议先做：它决定 qwen 这个主场景走下拉还是走手填。

## Risk Notes

- **各家 `/v1/models` 的真实行为未知**（Grill R1）→ 切片 7 用真 key 实测；手填（切片 9）保证列不出来的
  那几家不会比今天更难用。
- **SSRF 面**（Grill R2）：列模型会按已保存的自定义端点地址发请求。提问那条路今天已然如此；
  这一条靠「只回 id 不回原文」（切片 5 有测试钉着）限住回显，且不开任意地址探测（Grill D6）。
  部署仍须绑 127.0.0.1。
- **切片 1 改的是每一次提问的调用路径。** 替换模型时若不小心把大脑配置重建一遍，key 可能重新进入 `repr`；
  验收里专门有一条，`test_brain_key` 旁边补测试。
- **切片 2 动的是「选章判据的唯一来源」。** `context_budget_chars` 对既有输入必须逐一不变，否则 CLI 与既有
  会话的选章会悄悄改变。验收要求对内置表每个键逐一回归。
- **两个迁移**（`0003`、`0004`）都要可降级；`conversations` 加列带常量默认值是元数据操作，不锁表。
- **既有大脑接口测试的整体相等断言**会因为列表项多了字段而红 —— 那是测试写得太紧，改成按字段断言，
  不是回归。
- **前端没有自动化测试。** 每个涉及前端的切片完成时在⑧屏人工走一遍并记入完成记录；`vue-tsc` 的类型检查
  是唯一的自动闸门。
- **本机开发时别让本机 uvicorn 与容器里的 api 同时连着库**（`backend.md` 待确认 #2）—— 与本功能无关，
  但实施期间会反复起服务，容易踩。

## Status Tracking

| Slice | Status | Assignee | Notes |
|-------|--------|----------|-------|
| 1 | 🟢 Done | Claude | 修 C2；见完成记录 |
| 2 | 🟢 Done | Claude | lmstudio 变「估计」（唯一一个）；书籍详情改读⑧屏列表；见完成记录 |
| 3 | 🟢 Done | Claude | 修 C3；迁移 0003；新增 `BrainService.describe()`；见完成记录 |
| 4 | 🟢 Done | Claude | 曳光弹；只读，保存在切片 8；见完成记录 |
| 5 | 🟢 Done | Claude | 四类失败 + 不回显原文；429/5xx 归 `brain_network`；见完成记录 |
| 6 | 🟢 Done | Claude | SDK 1.6.0 的 `ModelInfo.max_input_tokens` 即窗口；见完成记录 |
| 7 | 🟢 Done | 用户实测 + Claude | qwen 列得出（261 个、无窗口）；排序改三档；修两处布局回归；见完成记录 |
| 8 | 🟢 Done | Claude | 迁移 0004；全局 422 不回显；保存 key 后自动拉模型；见完成记录 |
| 9 | 🟢 Done | Claude | 手填永远在；`BrainOut` 增 `contextTokens`；见完成记录 |
| 10 | 🟢 Done | Claude | 改端点自己那一行；恢复默认 400；见完成记录 |
| 11 | 🟢 Done | Claude | 用户追加；纯前端；见完成记录 |

Status: 🔴 Not started | 🟡 In progress | 🟢 Done | ⚠️ Blocked

## 完成记录

### Slice 1（🟢 Done）

- 改动：`chat_service.ask()` 在 `config_for` 之后用 `dataclasses.replace(brain, model=conversation.model)`
  把会话快照的模型换进配置；替身 `FakeBrain` 多记一项 `cfg`（造它的那份配置）。
- 测试：`test_chat.py` 两条（旧会话下一轮仍调快照模型 / 改后新开的会话用新模型）、
  `test_brain_key.py` 一条（换过模型的配置 `repr` 仍不含 key）。后两条首跑即绿——它们钉的是
  已然正确的行为，是验收要求的护栏，不是驱动实现的那条。
- **踩到的坑**：第一版测试改 `config.PRESETS` 来模拟「改了预设」，结果**首跑就绿**——项目根的
  `booksoul.toml` 把九个预设逐一重写了一遍，文件覆盖了 `PRESETS`，改动根本没生效。改为包一层
  `available_brains()`（两条路都读它），才红在正确的原因上。后续切片凡要模拟「预设变了」都用
  `test_chat._edit_preset_model()`。
- 全量 323 passed；ruff 干净。无设计偏差。


### Slice 2（🟢 Done）

- 引擎：`ask.known_context_chars(model) -> int | None`；`context_budget_chars` 改为建在它之上，
  表仍只有一张。回归表 `_BEFORE`（16 项：内置表每个键、九个预设的模型名、子串命中、认不出、空串）
  **先对旧实现跑过一遍确认 16/16 一致**，再改实现。
- 服务：新模块 `services/context_window.py`（`Window(chars, source)`，四种来源）。`brain_service`
  的预设、自定义端点、新建端点三处都改走它；`fit_report()` 与 `BrainPreset.fits()` 删除。
- 接口：`BrainOut` / `BrainFit` 增加 `contextSource`。**估计的那一行 `fits` 为 `null`**，
  `fitNote` 为「窗口未知（按估计）」—— 不说装得下，也不说装不下。「没给书」与「估计」两种
  `null` 由 `contextSource` 分开。
- 前端：`types` 增加 `ContextSource`；新 `lib/contextSource.ts`（短名 + 悬停说明，切片 9 的
  窗口输入框复用）；⑧屏「正文额度」一格变为「110k 内置」形式（列宽 48→76px），估计的行
  来源字与「这本书」一格着黄檗；书籍详情「装得进哪个大脑」对 `null` 显示「窗口未知（按估计）」。
- **可见变化 1**：九个内置预设里只有 **lmstudio**（模型名是占位的 `local-model`）从「100k」
  变为「100k 估计」。其余八个都被内置表认得。
- **可见变化 2（偏离计划字面，符合其意图）**：书籍详情「装得进哪个大脑」原先只列内置预设
  （`fit_report` 自己算一遍）；现在直接读⑧屏那份列表（`BrainService.list_for_user`），
  **所以也列出用户自己加的端点**。这是「两页读同一个结果」最直接的做法，也是切片 8 的前提
  （选择按用户存，书籍详情必须知道是谁在看）。
- 旧测试 `test_book_detail::test_装得进哪个大脑用的是引擎那张表` 断言「每一行都有装不装得下的
  结论」—— 这正是本片要改的行为，已改为估计行断言 `fits is None`。
- **顺手修的事故**：发现前端 7 个文件被我之前用 Python `write_text` 改写时转成了 CRLF，
  Prettier `--check` 因此报错（`eslint` / `vue-tsc` 都不管这个）。已转回 LF，前端三项检查全干净。
  后端 `.py` / `.md` 的换行本来就是混的（很多我从没碰过的文件也是 CRLF），ruff 不管，未动。
- 人工走查⑧屏：**待你在浏览器里看一眼**（本会话没有浏览器工具）。计划在第 7 片你打开⑧屏实测时
  一并看。
- 全量 349 passed；ruff、vue-tsc、eslint、prettier 干净。

### Slice 3（🟢 Done）

- 迁移 `0003_conversation_context_chars`：`conversations.context_chars INTEGER NOT NULL DEFAULT 0`
  + `COMMENT ON COLUMN`（写明三级优先级）。不回填。ORM 同步加列。
- **新增 `BrainService.describe(user_id, name) -> BrainOut`**：按名字取⑧屏那一行（同形同值，
  找不到 → 404）。开会话的快照、`config_for`、⑧屏列表从此都经这一处 —— 快照「与⑧屏显示的是同一个数」
  靠构造保证，不靠两边碰巧算得一样。这也是切片 8 合并「选择」的唯一落点。
  `config_for` 的 `require_key` 参数随之删除（它唯一的用途就是开会话那一处，现在改调 `describe`）。
- `ChatService.start` 把 `brain.context_chars` 快照进会话；`ask` 的预算为
  `context_budget_chars(model, budget_chars or context_chars)` —— 即 N9 的三级。
- 测试：`test_retrieval.py` 五条（快照 = ⑧屏的数 / 小窗口挑章节 / 大窗口按 85 万字而非 10 万 /
  手调预算仍优先 / 快照为 0 走老路径），证据一律取⑥屏留档里的 `budget_chars`；后两条首跑即绿，是护栏。
  `test_schema_drift.py` 新增**首条降级测试**：降到 `0002` 再升回 `head`，`finally` 里保证升回去。
  切片 8 加 `0004` 后这条自动覆盖它。
- **待办（人工动作）**：本机开发库需要迁移一次 —— 重建容器 `docker compose up -d --build api`
  （entrypoint 会 `alembic upgrade head`），或本机 `alembic upgrade head`。收尾的 Delta 清单会再列一次。
- 全量 355 passed；ruff 干净（含 `migrations/`）。无设计偏差。

### Slice 4（🟢 Done）

- 引擎 `brain.py`：`ModelEntry(id, context_tokens)`（frozen）+ 模块函数 `list_models(cfg)` =
  `create(cfg).models()`；`OpenAICompatBrain.models()` 走 `GET {base_url}/models`，超时 10 秒。
  `chat` 与 `models` **共用** `_url(path)` 与新抽出的 `_headers()`（原先 chat 里就地拼头）。
  窗口字段先认 `context_length` / `context_window` / `max_context_length` / `max_model_len`，
  取第一个正整数（`True` 不算），切片 7 实测后补。
- 服务 `BrainService.list_models()`：经 `config_for`（同一处取配置与 key）→ `asyncio.to_thread`
  调引擎 → 名字含 chat / instruct / turbo（不分大小写）的排前、其余稳定保持原序 → 只回 id 与窗口。
  `source` = 预设 / 端点的 `base_url` 去掉末尾 `/`。
- 接口 `GET /api/brains/{name}/models`，key 走既有的 `BrainKey` 依赖（`X-Brain-Key`）。
  新 schema `ModelOut` / `ModelList`。
- 测试 `tests/test_list_models.py` 六条：引擎三条（原序 + 地址拼法 + 鉴权头 + 超时上限 / 四种窗口字段 +
  非数字 / 本机端点无鉴权头）、接口三条（排序 + 窗口 + source / 响应里只有 id 与窗口、对方的
  `owned_by` 与顶层杂项一个不带 / key 转给了供应商且不在响应与任何日志里）。替身换在 `urlopen`。
- 前端：`types` 加 `ModelEntry` / `ModelList`；`api/brains.listModels(name)` 带 `brain: name`——
  `client.ts` 的「白名单」就是这个显式参数，注释改写为列明两处调用方；新组件
  `components/brain/ModelPicker.vue`（展示组件，不发请求：view 拉列表、它 emit `fetch` / `close`）；
  `BrainRow` 的「模型」格加「换模型 / 收起」（列宽 142→190px，表头同步）；`SettingsView` 记
  `picking`（同一时刻只一行）、丢弃迟到的列表（用户已换到别的行）。
- 选中某个模型目前只高亮，**没有保存按钮**——按计划，存在切片 8。
- 人工走查⑧屏：待第 7 片一并看。
- 全量 361 passed；ruff、vue-tsc、eslint、prettier 干净。无设计偏差。

### Slice 5（🟢 Done）

- 引擎 `OpenAICompatBrain.models()` 的失败分类：401/403 → `auth`；404/405/400 等其余 4xx 与
  「200 但不是 `{"data": [...]}`」（非 JSON、没有 `data`、`data` 不是数组）→ `unsupported`；
  读超时与连接阶段超时（`URLError(reason=TimeoutError)`）→ `timeout`；其余连接失败 → `network`；
  429 / 5xx 沿用 `_kind_of_http` → `rate_limit` / `server`。新函数 `_list_kind(code)`（建在
  `_kind_of_http` 之上，只多一条「列模型接口的 404 是没有这个接口」）与 `_list_failure(kind, where, code)`。
- **对方说的话一个字都不带出来**：错误响应体根本不读；状态行原因短语不进消息；异常链一律
  `from None` 截断——测试把特征串同时塞进响应体、原因短语与连接错误里，断言它不出现在
  `traceback.format_exception` 的全文（即日志会记下的东西）、接口响应、以及任何日志里。
- 服务：`list_models` 先 `describe`，**需要 key 而没带 → 400「先填 key：…填在这一行右边的框里」**，
  且不调引擎（测试断言供应商零请求）。这句与提问路径的「到设置页把 key 填进去」刻意不同——
  人此刻就在⑧屏（`backend.md`「出路要按谁会读到写」）。为此抽出模块函数 `_config_of(brain, key)`，
  `config_for` 与 `list_models` 共用。`BrainError.kind` → `_LIST_FAILURES` 表（写死的 code + 网页用语）
  → 新异常 `core/errors.BrainUnavailable`（502，`code` 构造时给）。
- **契约细化（未越出 Spec 的四个 code）**：429 与 5xx 的 code 取 `brain_network`，文案各自准确
  （「这家暂时限流了」「这家的服务暂时出错了」）——code 表达的是「用户该做什么」，这两类与连不上
  一样是「稍后再试」。Spec 只列了四类的来路，没说 429/5xx 归哪；若要单列，再加 code 即可。
- 边界：本机端点（ollama）不带 key → 200 且无鉴权头；别人的自定义端点与不存在的名字 → 同一个 404
  （文案除名字外逐字相同），且不发请求。两条首跑即绿（`describe` 已按 `user_id` 查），是护栏。
- 前端：失败**措辞只用服务端的 `detail`**（只在 `_LIST_FAILURES` 一处），**行为按 `code` 分支**：
  `brain_timeout` / `brain_network` 时按钮变「再试一次」，`brain_auth` / `brain_unsupported` 不怂恿重试。
  `ModelPicker` 导出 `ListFailure` 类型（组件内部类型就近定义）。`brain_unsupported` 直接落到手填输入框
  归切片 9。
- 测试 `test_list_models.py` 增至 29 条。全量 384 passed；ruff、vue-tsc、eslint、prettier 干净。

### Slice 6（🟢 Done）

- 先查了**已安装的 SDK 本身**（`anthropic` 1.6.0）而不是凭印象写：`client.models.list(limit=, timeout=)`
  返回自动翻页的 `SyncPage[ModelInfo]`；`ModelInfo` 带 `max_input_tokens`（可空）——**Claude 会报窗口**，
  这一家不必等切片 7 实测字段名。`APITimeoutError` 是 `APIConnectionError` 的子类，必须先接。
- 引擎 `AnthropicBrain.models()`：`limit=1000`（默认一页 20 个，漏掉后面的等于说「没有」）、超时 10 秒；
  401/403 → `auth`、404 → `unsupported`、5xx → `server`（复用 `_list_kind`）、超时 → `timeout`、
  连接失败 → `network`；一律 `from None`，特征串塞进 SDK 异常的 message / 响应体 / body 与
  `ModelInfo.display_name`，断言都不出现在回溯全文里。
- **新增 `BrainError.kind = "dependency"`**：`AnthropicBrain.__init__` 缺 SDK 时那条 `pip install anthropic`
  的错误原先 `kind` 为空，现标为 `dependency`（CLI 文案不变；网页侧唯一按 kind 分支的是 `moderation`，
  已确认无影响）。服务遇到它：ERROR 日志写管理员要做的事（含 `pip install anthropic`），给用户
  「这台服务器暂时接不了这一家（缺它的依赖），先换一个大脑」，502 且**无 code**——重试、换 key、
  手填都救不了，前端不给出路按钮（沿用 `format_service` 的「用户看得懂的 / 管理员要做的」分流先例）。
- `source`：claude 预设不填 `base_url`，服务回 `https://api.anthropic.com`（SDK 默认地址）。
- 测试 `test_list_models.py` 增至 39 条（引擎 8 条 + 接口 2 条）。前端无改动（同一端点、同一组件）。
- 全量 394 passed；ruff 干净。
- **工具踩坑**（不影响产物）：经 Bash heredoc 传给 Python 脚本的双反斜杠会先被折成单个，
  于是脚本里写的「反斜杠 + n」变成了真换行——`test_list_models.py` 一处断言、一次 `brain.py`
  替换、以及本条记录的第一版都因此出错，均已当场发现并改用 Edit 工具。

### Slice 7（🟢 Done，HITL）

- **实测（2026-09-22，用户在⑧屏用真 key，key 未进对话）**：只有 qwen 的 key。
  - qwen（DashScope 兼容模式）**列得出**：共 261 个，来自 `https://dashscope.aliyuncs.com/compatible-mode/v1`。
    **不报窗口**（条目右侧没有数字）→ `contextTokens` 全为 0，保存时回落到内置 / 估计。
  - 列表里混着别家的模型（`kimi/kimi-k2.7-code-highspeed`、`xiaomi/mimo-v2.5-pro`、`MiniMax/MiniMax-M3`、
    `glm-5.2-fast-preview`）；**对话模型大多不带 chat / instruct / turbo**，于是 `qwen3.7-text-embedding`、
    `qwen-audio-3.0-realtime-*` 等夹在中间。
  - 能点选模型（只读，保存在切片 8）。其余各家没有 key，未测；失败类别由切片 5 / 6 的测试覆盖。
  - 结论：**qwen 走下拉，不必只能手填**；切片 9 的手填是兜底而非 qwen 的主路径。
- **据此改动（用户裁定：三档排序）**：`brain_service._tier()` —— 带对话关键词 → 看不出来的 → 明显不是对话的
  （`_NOT_CHAT`：embed / rerank / audio / tts / asr / speech / realtime / whisper / image / video / moderation，
  以及 DashScope 的 wanx / paraformer / cosyvoice / sambert），档内保持原序；「不是对话」先判
  （`wanx…-turbo` 是画图的）。这是对 Spec N6「对话类排前、其余保持原序」的细化。
  测试：新增一条按实测形状写的（DashScope：只有 id、无窗口、混着别家、音频与 embedding 在中间）；
  切片 4 那条排序测试的期望随之改（`qwen-max` 不再排在 embedding 后面）。
- **走查发现的两处布局回归（切片 2 / 4 引入，已修）**：
  1. ⑧屏的表与人格编辑器分一屏宽（约 800px），切片 4 把「模型」列 142→190、切片 2 把「正文额度」列
     48→76，「这本书」一格被挤成「整本装…」。改为**模型名本身就是下拉开关**（`qwen-plus ▾`，虚线底，
     悬停 / 展开时转朱批），列宽回到 142；额度列 64。
  2. 书籍详情那一栏窄，「窗口未知（按估计）」折成两行。改为「窗口未知」一行，「按保守值估」挪进悬停说明。
     与⑧屏仍是同一个结论（不说装不装得下），只是措辞随栏宽收短。
- **用户追加的两件事**已写进计划：切片 8 多一条验收（保存 key 后自动展开并拉模型列表，首次与换 key 都触发）；
  新增切片 11（从书籍详情进⑧屏，选了能用的大脑就回去，缺 key 就留；书籍详情标出选中的那个）。
- 全量 395 passed；ruff、vue-tsc、eslint、prettier 干净。

### Slice 8（🟢 Done）

- 迁移 `0004_brain_choices`：`id` IDENTITY、`user_id` → `users` CASCADE、`brain_name`、`model`、`context_tokens`、
  `context_source`、`created_at` / `updated_at`（`trg_brain_choices_touch`）；约束 `UNIQUE (user_id, brain_name)`
  （首列即外键列，兼作索引）+ 四条 CHECK（模型名非空白、窗口 0–1000 万、来源 ∈ '' / vendor / manual、
  窗口为 0 时来源必须为空）；表与三个非平凡列写了 COMMENT。与预设按名字弱关联、不做外键。
  降级测试扩为同时断言 `brain_choices` 表的消失与回来。ORM `models/brain_choice.BrainChoice`（已导出）。
- 仓储 `repositories/brain_choice_repo.py`：`for_user`（按名索引）/ `put`（一条 `INSERT … ON CONFLICT DO UPDATE`：
  先查再写，两个标签页同时保存会撞唯一约束）/ `remove`。每个查询带 `user_id`。
- 服务：`_from_preset(preset, choice)` —— **选择只覆盖模型与窗口**，provider / 地址 / key 名永远取预设；
  `list_for_user` 与 `describe` 都经它，于是⑧屏、书籍详情、开会话快照、`config_for` 自然全部读到选择。
  新增 `set_model` / `reset_model`（没选过也照常 204）、`_preset_named`。对不上预设的选择行自然被忽略。
  `BrainOut` 增加 `default_model`（自定义端点为空串）、`model_chosen`。
- 接口：`PUT /api/brains/{name}/model`（`ModelChoiceIn`）、`DELETE /api/brains/{name}/model`。
  **本片只处理内置预设**：自定义端点名在两条接口上暂时都是 404，切片 10 接上。
- **校验与不回显**：`ModelChoiceIn` 用 `PydanticCustomError` 给中文限制说明（空 / 超 64 / 控制字符 / 窗口越界 /
  来源非法 / 窗口为 0 却带来源）。**新增全局 `RequestValidationError` 处理器**：422 只回 `loc / msg / type`，
  去掉 FastAPI 默认的 `input` 与 `ctx`——`backend.md` 早有「校验失败的报错不回显被校验的值」，但此前没有
  落实：往「环境变量名」栏粘一串超长 key，默认 422 会把整串 key 抄回响应。形状不变，前端照旧读
  `detail[0].msg`。
- 前端：`types` 增 `defaultModel` / `modelChosen` / `ModelChoice`；`api/brains` 增 `saveModel` / `resetModel`；
  `ModelPicker` 增「用这个」（朱批实心主按钮；双击条目同效）、「恢复默认」（仅改过的内置预设）、
  「现在用 X（预设是 Y）」、按钮旁「只影响以后开的会话」；供应商报了窗口就连同 `vendor` 来源一起存。
  `SettingsView`：保存后收起并重拉整张表；**保存 key 后自动展开这一行并拉模型列表**（用户追加，首次与换 key 都触发）。
- 测试 `tests/test_brain_choices.py` 14 条。全量 409 passed；ruff、vue-tsc、eslint、prettier 干净。

### Slice 9（🟢 Done）

- 后端：`BrainOut` 增 `context_tokens`（存下来的窗口，即供应商报的或你填的那个数；内置表 / 估计时为 0——
  那两种手里只有字数，不从字倒推 token）。这是 Spec「API 契约」之外多出的一个兼容字段，理由：窗口输入框要预填
  「你上次填的那个数」，而 `contextChars` 是折算后的字数、倒推回 token 有损。`PUT` 在切片 8 已接受手填与清零。
- 测试 `test_brain_choices.py` 增 3 条：手填名字 + 100 万窗口 → 标「你填的」、`contextTokens` 原样带回、
  60 万字的书从装不下变成装得下；窗口清成 0 → 回到内置；内置 / 估计的 `contextTokens` 为 0。
- 前端 `ModelPicker` 重写下半部：**模型名与窗口两个输入框永远在**（列不出来、还没列都能用），预填现在在用的；
  点列表一项 = 填进去（带上供应商窗口并标「供应商」）；手改名字时丢掉那个供应商窗口（它属于刚才点的那一个）；
  改窗口 → 标「你填的」；窗口不是 0–1000 万整数时就地提示且不能保存。提示三档：窗口填错（黄檗）/
  现在在用的窗口是估计的 →「建议照着供应商文档填上真实窗口」（黄檗）/ 窗口空着的说明（灰）。
  **手填名字不在刚拉到的列表里 → 提醒不拦**；没拉过列表不提醒。回车即保存；没改动时「用这个」不可点。
- 全量 412 passed；ruff、vue-tsc、eslint、prettier 干净。

### Slice 10（🟢 Done）

- `BrainService.set_model`：名字是内置预设 → 选择表；否则按 `user_id` 找自己的端点 → **直接改那一行的
  `model` / `context_tokens`**（Spec N5），不进选择表；找不到（含别人的）→ 与不存在同一个 404。
  `reset_model`：自定义端点 → 400「自定义端点没有默认模型——它的模型本来就是你填的。要换就直接选一个。」
- **已知局限（Spec 已定 `brains` 表不变）**：`brains` 没有窗口来路一列，所以自定义端点的窗口一律显示「你填的」，
  哪怕是从列表里点来的供应商窗口。数字本身是对的，只是来路标签不如内置预设精确。要精确需给 `brains`
  加一列，留作以后。
- 前端无改动：「模型」下拉开关每一行都有；`ModelPicker` 的「恢复默认」对 `custom` 本来就不出现。
  新建端点表单保持原样（既有接口测试全绿）。
- 测试 `test_brain_choices.py` 增 4 条（改的是自己那一行 + 地址不动 + 不进选择表 / 新会话用新模型、旧会话不变 /
  恢复默认 400 / 别人的端点 PUT 与 DELETE 都 404——后者首跑即绿，是护栏）。
- 全量 416 passed；ruff 干净。

### Slice 11（🟢 Done，用户 2026-09-22 追加）

- `SettingsView`：带 `?bookId=` 进来时点选大脑——**能用（本机的，或这台浏览器已有它的 key）就回
  `/books/{bookId}`**；缺 key 就留下，顶部一条黄檗提示「「qwen」还缺 key —— 填在它那一行右边，保存后就回到书籍详情」，
  填好 key 即回（此时不再自动展开拉模型：人是要回去开会话的，想换模型再从⑧屏进）。不带 `bookId` 时行为不变。
- 书籍详情「装得进哪个大脑」标出「开始对话」会用的那一个（朱批底 + 左侧朱批线 + 名字朱批，与⑧屏同一种标法）。
- 抽出 `lib/brainKeys.brainForNewChat()`（选过的，或 claude）：开会话（`ChatView`）与书籍详情的标记读同一个——
  原先兜底 `|| 'claude'` 就地写在 `ChatView` 里，再抄一份到书籍详情，两处迟早不一致。
- 全量 416 passed（本片无后端改动）；vue-tsc、eslint、prettier 干净。人工走查待你在浏览器里看。
