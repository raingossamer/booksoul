---
status: done
---

# TurnRunner 与 Detached — Implementation Plan

## Parent Spec / RFC

- File: `ProjectModel/Todo/Rfcs/turn-runner-rfc.md`（`status: accepted`）
- 模式：**RFC 快车道**。无新增用户可见行为，切片按绞杀式迁移路径组织，不按 schema → API → UI。
- 前置条件核对（2026-09-21）：RFC 为唯一未实施提案，无重叠旧 RFC 需置 `superseded`；
  `backend.md` / `database/*` 三份与代码同步（上一轮 `design-doc-sync` 已核）。
  已知 `frontend.md` 的「目录结构」「核心页面与组件」「状态管理」三节失准，
  **本计划不触及前端**，不影响 tdd 的 Pre-read 基线。

## Plan Overview

分两期，九片。

**第一期（切片 2–4）** 给「脱离请求的工作单元」一个名字：`UnitOfWork` 端口 +
`Detached` 实现 + 测试适配器。**不改任何时序、不改任何可见行为**，纯收敛，
跑一遍全量测试即可验证「什么都没变」。

**第二期（切片 5–8）** 让「一轮提问」成为一个模块：`_Turn`（18 个扁平字段）拆成
`Turn` + 三个值对象，17 个模块级函数里那 15 个单调用点收进 `TurnRunner.launch()`，
最后合并落库事务。

切片 1 与 5 完全独立，可插在任意位置；切片 9 是绞杀式迁移的收尾（删旧实现 + 删被
边界测试取代的旧测试），必须最后做。

**唯一改变可观察行为的是切片 8**（⑥屏选章明细与审核台账延后到轮末可见），已在 RFC
中裁定接受；但它另外撞上一个 RFC 未涵盖的决策，故标为 HITL，见该片正文。

其余每片的验收标准统一为：**可观察行为不变 + 新接口边界测试通过 + 全量测试保持绿色**。

## Slices

### Slice 1: 漂移检测补上第 12 张表

- **Type**: AFK
- **Blocked by**: None
- **接口契约 / 调用方分组**: RFC 实施建议 #8
- **Complexity**: S

**What to build**:

`models/__init__.py` 的职责（见其模块 docstring）是「import 一下就把所有实体挂上
`Base.metadata`」，供 `test_schema_drift` 对账。它漏了 `ModerationEvent` ——
实测 `Base.metadata` 只有 11 张表，而迁移建出 12 张，`moderation_events` 不在漂移
检测的覆盖范围内，而它正是上一片（S13）刚加的那张。

补上之后漂移检测**可能当场变红**。红了说明 S13 的 ORM 声明与 `0002` 迁移建出的
真实 schema 确实漂了 —— 那正是这一片要抓的东西，按红的结果修 ORM 侧或补迁移。

**Acceptance criteria**:
- [ ] `import booksoul_api.models` 之后 `Base.metadata.tables` 为 **12** 张
- [ ] `test_schema_drift` 通过；若补上后变红，按「ORM ⊆ DB」方向修正并记录漂了哪一列
- [ ] 全量测试保持绿色

**Implementation hints**:
- 只动模型注册处的 import 与 `__all__`，不动 `ModerationEvent` 定义本身
- 漂移测试只查「ORM 有、DB 没有」与「两边不一致」两类，索引与 COMMENT 的差异是噪音，不要去对

---

### Slice 2: `UnitOfWork` 端口 + `Detached` + 测试适配器

- **Type**: AFK
- **Blocked by**: None
- **接口契约 / 调用方分组**: RFC「Proposed Interface · 第一期」
- **Complexity**: M

**What to build**:

建立 RFC 定义的新接口，**旧代码一行不动**，在新接口边界写测试。

端口是 `UnitOfWork` Protocol（`__aenter__` 交出一个 session，`__aexit__` 收口）。
生产适配器 `Detached` 承担今天散在各处的四件事：session 从哪来、什么时候提交、
异常吞还是抛、日志怎么写。两种错误策略成为显式参数而不是各写一遍的 try：

- `on_error="raise"` —— 失败必须让调用方知道（选章留档属于这一类）
- `on_error="log"` —— 失败只记一条带 `label` 的日志（审核台账属于这一类：
  「台账记不上是一件遗憾，把一条已经说完的回答变成错误不是」）

同时建测试适配器：复用测试 session、退出时 flush 不 commit。**后台写入第一次可以
被同步断言**，测试里那些绕到库里重查或等后台任务的写法可以去掉。

**这个端口只覆盖「进出各一次」的一次性写入。** 实测 8 个自建 session 的调用点里
只有 5 处是这个形状，另外 3 处不是（见下），本片不碰它们。

**Acceptance criteria**:
- [ ] `Detached` 正常退出提交；异常退出回滚，库里不留半截
- [ ] `on_error="raise"` 把异常抛给调用方
- [ ] `on_error="log"` 吞掉异常并留一条带 `label` 的日志，调用方感知不到失败
- [ ] 同一个 `Detached` 实例不可重入（重入抛错，不静默共用）
- [ ] 测试适配器可让后台写入在同一个测试 session 内被同步断言
- [ ] 旧代码零改动，全量测试保持绿色

**Implementation hints**:
- 放在数据库会话那一层（与 engine / session factory 同一处），不放 services
- `label` 只进日志，不进任何响应体
- 不要给它加「长活模式」—— 长活的那三处形状不同，硬塞会让这个抽象同时服务两种生命周期

---

### Slice 3: `chat_service` 三处后台写入迁到 `Detached`

- **Type**: AFK
- **Blocked by**: Slice 2
- **接口契约 / 调用方分组**: 调用方组 A（本 RFC 核心区）
- **Complexity**: S

**What to build**:

把 `_record`（选章留档 + 会话锁定）、`_record_moderation`、`_record_passed` 三处
迁到 `Detached`。**行为完全不变**：前者仍然失败即抛，后两者仍然失败只记日志。

这一片让那两个**逐字相同**的 except 块消失 —— `_record_moderation` 与
`_record_passed` 今天除 repo 调用外一模一样，连日志字符串都相同。

**Acceptance criteria**:
- [ ] 三处不再出现 `get_session_factory()()` 与手写 try/except/commit
- [ ] 审核台账写失败时，那一路的回答与错误消息**照常**送达（现有纪律不变）
- [ ] 选章留档写失败时仍然抛给调用方
- [ ] `test_retrieval` / `test_moderation` / `test_chat` 现有断言全绿，一条不改

**Implementation hints**:
- 迁移只换 session 的取得与错误策略，**不动写入内容、不动时序**
- `label` 里带上会话 id，保持今天日志的可追性

---

### Slice 4: 其余一次性后台写入迁移 + 三处长活点的处置记录

- **Type**: AFK
- **Blocked by**: Slice 2
- **接口契约 / 调用方分组**: 调用方组 B
- **Complexity**: S

**What to build**:

迁移剩下两处一次性写入：`parse_service.sweep_orphans`（启动清扫）与
`stream_recorder._persist`（回答与引文落库）。

同时把**不迁的那三处**写明理由，留在代码注释里，避免后来者反复重新判断：

| 调用点 | 形状 | 为什么不迁 |
|---|---|---|
| `parse_service.run_in_background` | 开一条 session 交给 `execute()`，里面自己 commit 五次 | 它是 session **提供者**，不是工作单元；退出时再 commit 一次没有意义 |
| `parse_service._pump_progress` | `while True` 长活，每拍 commit | 生命周期由外部 cancel 决定，不是进出各一次 |
| `routers/jobs._stream` | 长活轮询，靠 `rollback()` 刷 identity map | 同上，且它依赖 `expire_on_commit=False` 的具体行为 |

**Acceptance criteria**:
- [ ] `sweep_orphans` 与 `_persist` 走 `Detached`，行为不变
- [ ] 三处长活点保持原样，各有一句注释说明为什么不走 `Detached`
- [ ] 服务启动时的孤儿清扫仍然「失败不拦着服务起来」
- [ ] 全量测试绿

**Implementation hints**:
- `_persist` 这一片只换 session 机制，**不改它写什么** —— 内容的合并在切片 8
- 不要顺手给长活点加抽象；等真有第四个长活点时再说

---

### Slice 5: `ChapterIn` 补 `file_path`，`_Turn.files` 消失

- **Type**: AFK
- **Blocked by**: None
- **接口契约 / 调用方分组**: RFC 实施建议 #2
- **Complexity**: S

**What to build**:

`_Turn` 今天多带一个 `files: dict[int, str]`（序号 → 正文文件路径），唯一原因是
`selector.ChapterIn` 少一列 `file_path`。于是 `_read_sources` 必须把两个结构当场
缝成 `sources.Ref`。

给 `ChapterIn` 补上这一列，删掉 `_Turn.files` 那张旁表。`_Turn` 从 18 字段降到 17。

**`sources.Ref` 保留** —— 它有第二个构造点（重解析那条路从刚建出来的 ORM `Chapter`
拼 `Ref`），两个调用方手里的类型不同，让它们各自拼出 `Ref` 比在那边认两种类型清楚
（`sources.py` 的 docstring 已经论证过这一点）。

**Acceptance criteria**:
- [ ] `ChapterIn` 带 `file_path`，生产侧唯一构造点与四个测试构造点同步更新
- [ ] `_Turn` 不再有 `files` 字段
- [ ] `_read_sources` 直接从 `ChapterIn` 拼 `Ref`，不再查旁表
- [ ] 溯源行为不变：`test_citations_api` / `test_retrieval` 全绿
- [ ] 重解析那条路的 `Ref` 构造不受影响，`test_reparse` / `test_relocate` 全绿

**Implementation hints**:
- `chapter_selector` 是纯逻辑深模块，加一列不得引入任何 I/O 或数据库依赖
- 选章算法不读 `file_path`，它只是被携带的；不要让它进入打分

---

### Slice 6: `Turn` + 三个值对象取代 `_Turn`

- **Type**: AFK
- **Blocked by**: Slice 5
- **接口契约 / 调用方分组**: RFC「Proposed Interface · 第二期（数据）」
- **Complexity**: M

**What to build**:

把 17 个扁平字段收成 9 个，按**「谁算出来的」**分组成三个 `frozen` 值对象：

- `BookRef` —— 这本书在磁盘与库里的位置（来自书与章节的读取）
- `Budget` —— 能塞多少、锁了哪几节、要不要重挑、复用时已算好的决策（来自预算与锁定）
- `BrainRef` —— 用哪个大脑（来自大脑配置组装）

`Turn` 本体保持 `frozen=True`，仍然全是普通值：后台任务活得比响应长，带一个随响应
关掉的 session 过去是只在慢请求上才现形的 bug。

后台那些函数的签名改吃 `Turn`，**此时仍是模块级函数**，收口在下一片。

**Acceptance criteria**:
- [ ] `Turn` 与三个值对象全部 `frozen=True`
- [ ] `ask()` 构造 `Turn`，不再构造 `_Turn`
- [ ] 后台函数签名统一为 `(turn: Turn, choice: Choice)`
- [ ] 可观察行为零变化，全量测试绿（含 `test_compare` 的对读三路）
- [ ] 值对象的分组依据写进 docstring：新增需求时能一眼看出该往哪个对象加

**Implementation hints**:
- 分组依据是**来源**，不是「字段看起来像一类」
- `cfg.api_key` 已经是 `repr=False`，`BrainRef` 不得把它重新暴露进 `repr`
- `reparsed` 留在 `Turn` 顶层：它是这一轮的性质，不属于任何一个值对象

---

### Slice 7: `TurnRunner.launch()` 收口

- **Type**: AFK
- **Blocked by**: Slice 3, Slice 6
- **接口契约 / 调用方分组**: RFC「Proposed Interface · 第二期（入口）」
- **Complexity**: M

**What to build**:

把 17 个模块级函数里那 **15 个只有一个调用点**的收进 `TurnRunner`，`launch()` 成为
唯一入口。`ChatService.ask()` 的后半段塌成一句 `return await TurnRunner.launch(turn)`。

`TurnRunner` 应当拥有：选章的触发（不是算法）、读盘、引擎 Session 的装配、
并发与错误边界、错误翻译、落库。应当隐藏：session 从哪来、几个事务、哪些活在工作
线程里、重连怎么挂回。应当暴露：一个 `launch`，以及那个只读的 `Turn`。

**这一片必须逐条守住 RFC 的十条约束**，尤其是：

- `ask` 保持**普通协程**而非异步生成器 —— 写成生成器，4xx 会变成一条莫名断掉的 200 流
- 引擎 `Session` 的拼装顺序（框架语 → 全书 →（缓存断点）→ 人格）一行都不在 Web 层重写
- user message 留在**请求 session** 里、在调模型之前落库
- 重连挂回同一条流，模型调用次数仍为 1

**Acceptance criteria**:
- [ ] `TurnRunner.launch()` 是这条路径的唯一公开入口
- [ ] 模块级私有函数数量显著下降，剩下的都有第二个调用点或有明确理由
- [ ] 重连测试通过且模型调用计数为 1（Spec #39）
- [ ] 一路失败、另两路读完（对读的错误边界仍在轨上）
- [ ] 零章节载入时就地停住不往模型送
- [ ] 系统提示拼装顺序测试保持绿色
- [ ] 全量测试绿

**Implementation hints**:
- 收口是搬运，不是重写：先让 `launch` 原样调用现有逻辑，再逐步内联
- `_check_persona`（3 个调用点）与 `_conversation_out`（2 个）不属于这一片，留在原处
- 事件仍然只产 `(事件名, 载荷)` 元组，SSE 字节的拼装留在路由层 —— 这个切分今天是干净的，不要动

---

### Slice 8: 落库合并 —— 主事务 + 台账分离

- **Type**: **HITL**
- **Blocked by**: Slice 4, Slice 7
- **接口契约 / 调用方分组**: RFC「落库的终局形状」
- **Complexity**: L

**What to build**:

把今天散在五条连接、五次 commit 上的写入，收成**两个事务**：

| 事务 | 写什么 | 工作单元 |
|---|---|---|
| 主事务 | `messages` + `citations` + `retrievals` + `retrieval_items` + 会话锁定 | `Detached()`（失败要抛） |
| 台账 | `moderation_events` | `Detached(on_error="log")` |

审核台账**刻意不并进主事务**：它今天故意吞异常，塞进主事务会让一次台账写失败回滚
掉整轮的回答。

主事务合并之后，`backend.md`「核心业务流程·提问」已经写下的那句「一轮结束**在一个
事务里**落库 `messages` / `retrievals` / `retrieval_items` / `citations`」第一次成为事实。

**已在 RFC 中裁定接受的代价**：⑥屏选章明细与审核台账在这一轮结束前查不到。理由是
`GET /api/conversations/{id}/retrieval` 的端点语义本来就是「这个会话**最近一次真正
做过的**选章决策」。

#### ⚠️ 这一片标 HITL 的原因：一个 RFC 未涵盖的决策

今天 `_record` 跑在调模型**之前**，所以一轮里三路全失败（例如被内容审核拦掉），
`retrievals` 那行照样写、会话锁定照样落，下一轮复用锁定不重挑。

合并之后这些写入挪进主事务，而落库开头有一句「一个字都没说出来就什么都不落」。
于是**全失败的那一轮，留档与锁定都不会写** —— 下一轮 `turn > 1 and not locked`
为真，用户会收到一句「这本书重新解析过了」（一件没发生过的事），并且全价重跑一次 BM25。

三条出路，实施前须由用户裁定：

- **A** 全失败也写留档与锁定：主事务分两段条件写入（说出话了才写 message，
  选过章就写留档）。语义最接近今天，代价是主事务多一处分支。
- **B**（计划推荐）把「没锁定」与「重解析过」在**判据上分开**：加一个显式标记，
  不再靠 `not locked` 反推。更干净，且顺手修掉一个**今天已经在误报**的缺陷
  （见 RFC Problem 第二节）。代价是要动 `reparsed` 的判定与相关测试。
- **C** 维持今天：留档仍然早写、不进主事务。那主事务只合并回答与引文，
  `backend.md` 那句承诺仍只兑现一半。

**Acceptance criteria**:
- [ ] **用户已在 A / B / C 之间裁定**，裁定结果与理由记入本计划的完成记录
- [ ] 一轮跑完，`messages` / `citations` / `retrievals` / `retrieval_items` / 会话锁定
      **同时存在或同时不存在**
- [ ] 落库之前崩掉：不留孤立的 `retrievals`（今天会留，并导致下一轮误报「重新解析过了」）
- [ ] 审核台账写失败**不**影响回答落库
- [ ] 复用锁定的轮次仍然不写 `retrievals`（`Choice.records` 的语义不变）
- [ ] 对读三路仍在同一事务里落，不出现「两列在库里、第三列不在」
- [ ] 断流留下的残文仍落 `complete=false`
- [ ] 全量测试绿

**Implementation hints**:
- 回答与引文的顺序不能动：message flush 拿 id 再挂 citation，同一事务
- `seq` 仍在同一事务里顺序分配，不是每条各算一次 `max(seq)+1`
- 连接数：合并后一次对读的峰值占用应下降（今天最多 6 条，而引擎未配 `pool_size`）

---

### Slice 9: 清理 —— 删旧实现、删被取代的旧测试、删死代码

- **Type**: AFK
- **Blocked by**: Slice 7, Slice 8
- **接口契约 / 调用方分组**: RFC「Testing Strategy · 需删除的旧测试」+ 实施建议 #7
- **Complexity**: S

**What to build**:

绞杀式迁移的收尾。**替换，而非堆叠** —— 浅模块上的旧单元测试在边界测试存在后即为浪费。

**删除的旧测试**：

| 位置 | 为什么变冗余 |
|---|---|
| `test_retrieval.py` 手搭 18 字段 `_Turn` 测 `_record` 那条 | `_record` 不再是可单独调用的私有函数；同一条断言改在 `TurnRunner` 边界做，不用再填 13 个空壳字段 |
| `test_reparse.py` import `_must_have_content` 那条 | 改为在边界断言「零载入的那一轮不调模型」 |
| `test_compare.py` import `_history_for` 那条 | 历史拼装是 `Turn` 构造的一部分，在 `ChatService.ask` 边界断言 |

**保留**：`test_chat` / `test_moderation` / `test_citations_api` / `test_personas` 现有的
`ChatService` 级断言全部不动 —— 它们本来就在正确的高度，是这次重构的回归网。

**删除的死代码**（全部已实测零调用）：

| 目标 | 处置 |
|---|---|
| `brain_service._config` + `_NEED_KEY` | 删。13 行，与活代码存着同一句面向用户文案的两份，已可独立漂移 |
| `StreamRecorder.text_of` | 删 |
| `models/parse.py` 与 `importer.py` 两个逐字相同的 `STAGES` | 两个都无人使用；保留一份作为单一真相源，删另一份 |
| `Choice.shortfall` 与 `chat_service._shortfall` | 两份等价算法，生产只走后者、测试只测前者。**保留 `Choice.shortfall`**（在纯模块上，已被选章测试覆盖），让读取侧调它，删掉重复实现 |

**Acceptance criteria**:
- [ ] `_Turn` 已删除，无任何残留引用
- [ ] 三条够进私有的旧测试已删，其断言在新边界有等价覆盖（逐条对照，不得净减少覆盖）
- [ ] 四项死代码按上表处置完毕
- [ ] 「差多少字」只剩一份实现，且⑥屏显示的数字与测试断言的是同一次计算
- [ ] `ruff check` / `ruff format` 干净
- [ ] 全量测试绿，且测试总数的变化可以逐条解释

**Implementation hints**:
- 删测试前先确认新边界测试确实覆盖了同一条行为，不要「删了再说」
- `format_service.rejection_reason` **不在本片范围** —— 它未接线是一个功能缺口
  （上传路径不校验格式），属于 RFC「不在本 RFC 范围内」那一节，不要顺手删

## Dependency Graph

```
Slice 1 ──（独立，可插在任意位置）

Slice 2 ──┬─→ Slice 3 ──┐
          │             ├─→ Slice 7 ─┐
          └─→ Slice 4 ──┼────────────┼─→ Slice 8 ─→ Slice 9
                        │            │
Slice 5 ─→ Slice 6 ─────┘────────────┘
```

推荐执行顺序：**1 → 2 → 3 → 4 → 5 → 6 → 7 → 8(HITL) → 9**

## Risk Notes

- **切片 1 可能当场变红。** 补上第 12 张表之后，S13 的 ORM 声明若与 `0002` 迁移建出的
  真实 schema 有出入，漂移测试会立刻失败。这是预期内的收获，不是意外；按「ORM ⊆ DB」
  方向修正，并把漂了哪一列记进完成记录。

- **切片 7 是搬运，最容易在搬运中悄悄改写语义。** 十条约束里有三条（`ask` 必须是普通
  协程、引擎提示拼装顺序、user message 落在请求 session）一旦破坏都**不会报错**：
  前者表现为 4xx 变成断掉的 200 流，中者表现为账单变贵，后者表现为断线时用户找不回
  自己问过什么。必须逐条有测试或注释兜住。

- **切片 8 会改变失败轮次的后续行为**，具体取决于 A/B/C 的裁定。选 B 时要同步检查
  所有依赖 `not locked` 反推的地方，不能只改判定式。

- **`expire_on_commit=False` 的坑在本轮重构中会再次逼近。** 合并事务后，请求 session
  与主事务 session 读到的会话行可能不同步；`_locked_chapter_ids` 今天那 18 行注释
  描述的正是这个问题。迁移时不得把它简化掉。

- **测试环境依赖真 PostgreSQL。** 不引入 SQLite 之类的第二种方言 —— 迁移里有 JSONB、
  窗口函数、`ON CONFLICT ON CONSTRAINT`，换方言等于测另一套东西。

- **模型替身维持现状**：打在引擎的 `brain.create` 上，不在服务层另开测试分支 ——
  服务层要的正是「和 CLI 走同一条路」。

## 实施偏差记录

- **切片 6：`fits_whole` 放在 `Turn` 上，不在 `Budget` 上**（RFC「Proposed Interface ·
  第二期（数据）」把它画在 `Budget` 里）。它同时要问「书有多大」和「预算有多少」，
  横跨两个值对象；放进 `Budget` 就得让预算自己拎一份书的字数，而本计划给三个值对象
  定的分组依据是**「谁算出来的」**——字数来自读书那一步，不是预算那一步。
  依据一破，下一个新字段该往哪放就又没有唯一答案了。接口语义不变，调用方写法
  从 `order.fits` 变成 `turn.fits_whole`。

### 切片 8 的裁定与连带发现

**用户裁定：B —— 把「没锁定」与「重解析过」在判据上分开。**（2026-09-21）

判据落在 `ChatService._lock_was_wiped(conversation_id, locked)`：锁定为空时，
去看最近一次**真做过决策**的那一轮留了什么档。挑过章而现在锁定是空的 → 被清掉的；
留的是「整本装得下」→ 本来就空；**一行留档都没有** → 这个会话从没做成过决策，
也就无所谓被清掉。不需要新列、不需要迁移。

裁定 B 意味着接受这一条代价（计划正文已列出）：**全失败的那一轮什么都不写，
于是下一轮要重挑一次章节。** 换来的是那一轮不会把「为一个没得到回答的问题挑的
几节」锁进会话。

连带修掉两个**已经在生产上**的缺陷（都是本片的判据与事务边界直接牵出来的）：

1. **整本装得下的书，从第二轮起每轮都误报「这本书重新解析过了，缓存作废，
   这次输入按全价算」。** `whole_book` 那条路 `_record` 写的锁定就是 `[]`，
   旧判据 `turn > 1 and not locked` 当场命中。实测三轮跑完 `retrievals` 里躺着
   `turn = [1, 2, 3]` —— 而 `Choice.records` 的 docstring 自己写着那张表不该变成
   「每轮复制粘贴的流水」。
2. **落库失败时宣布的是 `done`。** `_finale()` 只看每一路的 track 有没有错，
   看不见落库炸没炸。合并之后一轮的五样东西都押在这一个事务上，报 `done`
   而库里什么都没有，客户端回头一拉就是一个空洞 —— `backend.md`「接口与响应」
   的「先落库、后宣布」写的正是这条。现在落库炸了整轮报 `error`。

### 本片之外的发现（未修，需另行决定）

**「这本书重新解析过了」这条告示递送不到用户眼前。** `stage` 只有一个「现在在
做什么」的位置：紧随其后的「正在挑章节…」会当场把它盖掉，而 `listen()` 回放的
也只是最后一条 stage。于是它恰好在**真该说**的时候（真重解析 → 真重挑 → 真花钱）
看不见，在**不该说**的时候（整本装得下，上面第 1 条）反而看得见。

修它要给「本轮告示」一条不会被覆盖的通道，涉及 SSE 事件契约（`backend.md`
「接口与响应」列的是 `token` / `stage` / `done` / `error` + `answered` / `failed`）
与前端 —— **本计划不触及前端**，故留在此处待裁。判据本身已经修好并有测试钉着
（`test_锁定是被清掉的还是本来就空_判得出来` 等三条）。

### 切片 9 的偏差：`_history_for` 那三条测试保留，没删

计划要删的三条旧测试，两条按计划处置，第三条**保留并补了边界对照**：

| 旧测试 | 处置 |
|---|---|
| `test_retrieval` 手搭 `_Turn` 测 `_record` | **改打在 `RetrievalRepository.save()` 上**。「重问同一轮复用那一行」是仓储的契约（`(conversation_id, turn)` 上的 ON CONFLICT），跟这一轮怎么跑起来的无关。空壳字段从 13 个降到 0 |
| `test_reparse` import `_must_have_content` | **删**，换成 `test_retrieval::test_一节正文都没载入时就地停住_不往模型送`：把磁盘上的正文掏空，走完整条提问路径，断言一次模型都没调 |
| `test_compare` import `_history_for` 那三条 | **保留**，另加一条边界对照 |

保留的理由：计划的前提是「历史拼装是 `Turn` 构造的一部分」，但 `_history_for`
最后**没有**被收进 `TurnRunner` —— 它是 `ask()` 出发前的活，留在 `ChatService`
（切片 7 已在代码里写明六个函数各自留下的理由）。它也不是浅模块：它编码的是
S10 裁决的那条多分支规则，而那条规则**已经真的失败过一次**（单人格聊几轮后切
人格，历史当场塌掉、界面上还完整显示着、没有任何报错）。

而且实测：**全套测试里没有任何一条断言过送到模型手里的 `messages` 长什么样**。
照计划删掉那三条，是净减少覆盖 —— 计划自己的验收标准写着「不得净减少覆盖」。
所以改为补上那条缺失的边界测试（`test_compare::test_切换人格之后模型手里的历史
一条都不丢`），三条纯逻辑测试留作分叉级的快速规格。

### 测试总数：292 → 320（+28）

| 来源 | 条数 |
|---|---|
| 漂移检测：实体注册（S1） | +1 |
| `Detached` / `Attached` 边界（S2） | +9 |
| 台账与留档写失败时的纪律（S3、S4） | +4 |
| `file_path` 不参与打分（S5） | +1 |
| `Turn` 只读、`fits_whole`、repr 不漏 key（S6） | +6 |
| `ask` 是普通协程（S7） | +1 |
| 「重解析过了」判据四条 + 落库原子性两条（S8） | +6 |
| 零载入边界、历史边界（S9） | +2 |
| 删：`_must_have_content` 那条私有单测（S9） | −1 |

## Status Tracking

| Slice | Status | Assignee | Notes |
|-------|--------|----------|-------|
| 1 | 🟢 Done | - | 12 张表已齐；S13 的 ORM 与 `0002` **无列级漂移** |
| 2 | 🟢 Done | - | `db/unit_of_work.py` + `fakes.Attached`（SAVEPOINT）；9 条边界测试；旧代码零改动 |
| 3 | 🟢 Done | - | 两个逐字相同的 except 块已消失；3 条行为先补上再迁移 |
| 4 | 🟢 Done | - | 5 处一次性写入全走 `Detached`；3 处长活点各留一段说明 |
| 5 | 🟢 Done | - | `_Turn` 18→17 字段；`file_path` 带默认值，测试构造点无需改 |
| 6 | 🟢 Done | - | 17 字段→10（`Turn`）+ 3 值对象；`fits_whole` 挪到 `Turn`（见偏差） |
| 7 | 🟢 Done | - | `chat_service` 891→541 行、模块级函数 17→6；`turn.py` 0 个模块级函数 |
| 8 | 🟢 Done | - | **用户裁定 B**（2026-09-21）；顺带修掉两个已在生产的缺陷，见下 |
| 9 | 🟢 Done | - | 死代码四项清完；旧测试两删一留（见偏差）；292→320，逐条可解释 |

Status: 🔴 Not started | 🟡 In progress | 🟢 Done | ⚠️ Blocked
