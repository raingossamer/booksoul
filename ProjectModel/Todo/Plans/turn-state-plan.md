---
status: done
---

# 会话的轮次状态（turn_state）— Implementation Plan

## Parent Spec / RFC

- File: `ProjectModel/Todo/Rfcs/turn-state-rfc.md`（实施时 `accepted`，收尾后 `implemented`）
- 模式：**RFC 快车道**。切片按绞杀式迁移路径组织，不按 schema → API → UI。
- 前置条件核对（2026-09-23）：该 RFC 是唯一未实施的提案（`turn-runner-rfc.md` 已 `implemented`，
  本 RFC 建在它之上，不置 superseded）；Design 与代码同步——brain-model-picker 的 `design-doc-sync`
  已收尾，其后的 `improve-codebase-architecture` 不改代码。
  已知 `frontend.md` 的 store / composable 清单失准（`library` / `chat` store、`useParseProgress` 等并不存在），
  本计划只在切片 3 碰 `useAsk` 与 `ChatView`，「API 调用层」那张 SSE 事件表是准的，不影响 tdd 的 Pre-read 基线。

## Plan Overview

八片，基本是一条直链。

**切片 1–2 先把「写」收到一处**：seq 只有一个持行锁的发放者；一轮收尾的那一个事务从
`StreamRecorder` 搬进 `turn_state`，recorder 只剩内存里的活（它的测试因此不再需要数据库）。
先搬收尾、后做准入，是为了过渡期 `turn_state` ↔ `stream_recorder` 之间不出现 import 环。

**切片 3 先把告示送达**，同时把 `test_retrieval.py` 里三条直接调私有方法的测试换成看流里的
`notice` 事件——换完之后，切片 4 才能放心把那两个私有方法搬走。这一片是全程唯一贯穿到前端的。

**切片 4 是主体**：`turn_state.admit` 接管准入。占位必须先于读事实（锁定、历史、轮号），否则读到的
是上一轮还没提交的旧值——所以「占位」与「占位之后读」不能拆成两片。

**切片 5–6 收拢锁定并处理轮中重解析**；其中切片 6 顺带消除一个本 RFC 自己引入的锁序死锁
（见 Risk Notes #1）。**切片 7** 删旧实现与被取代的旧测试。**切片 8** 是真实环境的人工走查。

每片的验收标准统一为：**可观察行为不变（该片明确修掉的缺陷除外）+ 新接口边界测试通过 + 全量测试
保持绿色 + ruff 干净**。基线：全量 416 passed。

## Slices

### Slice 1: seq 由一个持行锁的发放者发放

- **Type**: AFK
- **Blocked by**: None
- **接口契约 / 调用方分组**: RFC Problem 二（seq 违反既有规范）；边界测试 5
- **Complexity**: S

**What to build**:

新建 `services/turn_state.py`，先只放一个私有的发号函数：在给定 session 上对 `conversations` 那一行
`SELECT … FOR UPDATE`，再取 `max(seq)`，一次发 n 个连续号。今天的两个发号点都改用它：

- 写提问：`ChatService.ask` 今天拿 Python 里已经读到的历史行算 `rows[-1].seq + 1`；
- 写回答：`StreamRecorder._persist` 今天 `max(seq)+1`，不持锁。

这一片**不改准入、不改锁序**：回答那一侧的行锁取在写选章留档**之后**，与今天、与解析事务的锁序
一致（统一锁序在切片 6）。

**Acceptance criteria**:
- [ ] 测试另开一条 session 锁住会话行并插入下一个号（不提交）：`ask` 写提问要等它；它提交之后，
      提问拿到再下一个号，没有 IntegrityError（今天：撞 `uq_messages_conv_seq`）
- [ ] 同一招作用在回答一侧（FakeBrain 用 `threading.Event` 卡在轮中，外来事务插入一条后放开）：
      回答照样落库，号接在后面
- [ ] 对读一轮 seq 为 1..4 连续
- [ ] 全量绿，ruff 干净

**Implementation hints**:
- 这两条测试是**确定性**的：外来事务持锁 → 被测方在锁上等 → 外来事务提交。不靠 sleep 赌时序。
- 过渡期 `stream_recorder` 从 `turn_state` 导入发号函数；本片 `turn_state` **不得** import
  `stream_recorder` / `turn`（切片 2 起方向反过来）。
- 规范原文「持 `conversations` 行锁发放，禁 `max(seq)+1`」：行锁之下取 max 再 +1 是 RFC 选定的实现
  （无迁移、不加 `last_seq` 列）。措辞需要澄清，列为收尾时的规范候选。

**完成记录（2026-09-23）**:
- 新建 `services/turn_state.py`，目前只有 `allocate_seq(session, conversation_id)`：`SELECT conversations.id … FOR UPDATE`
  之后取 `max(seq)+1`，锁持有到调用方那次提交。过渡期对外（`chat_service` 与 `stream_recorder` 各用一处），
  切片 7 收成私有。
- 两处发号改用它。回答那一侧**锁序保持旧样**：先 `_write_choice`（写留档、动会话行）、后 `allocate_seq`，
  与解析事务「先章节、后会话」同序，注释写明了理由。
- 测试 `tests/test_turn_state.py` 两条，均先 RED 后 GREEN，RED 的原因都是撞 `uq_messages_conv_seq`：
  - 提问一侧：外来事务锁住会话行并写下 seq=1（不提交）→ 等到 `pg_locks` 里出现排队者再提交。
  - 回答一侧：**必须用复用锁定的第二轮**。第一轮有选章决策，`_write_choice` 那句 `UPDATE conversations`
    恰好顺手锁住了会话行，所以第一轮碰不到这个缺陷；复用轮什么都不写，今天就是裸的 `max+1`。RED 时整轮以
    `error` 结束——一条已付费的回答丢了。
  - 探测排队用 `pg_locks WHERE NOT granted`，每次新开 session：`pg_stat_activity` 在一个事务里只取一次快照。
  - 外来那条消息标成 `complete=false`：残缺行不进 `uq_messages_conv_turn_persona`（部分唯一索引 `WHERE complete`），
    否则先撞上的是「同一轮两条提问」而不是 seq。那是轮号在锁外算的问题，归切片 4。
- 对读 seq 1..4 连续：`test_compare.py::test_对读一轮产生一问三答` 早已断言，未新增。
- 全量 **418 passed**（416 + 2），ruff 干净。
- **设计读取**：`backend.md` 全文（含「并发与后台任务」「注释与事务」等规范章节）、`database/sql.md`「约定」。
  应用：seq 持行锁发放（本片就是兑现它）、身份映射只取一列、事务边界在 services、中文 docstring 讲为什么。
- **偏差 / 发现**：`sql.md` 的 `messages` 建表语句已过期——缺 `turn` / `persona` / `complete` / `updated_at` 四列、
  `ck_messages_*` 三条 CHECK 与 `uq_messages_conv_turn_persona`（以 `migrations/versions/0001_init.py` 为准）。
  架构章节，留给收尾的 `design-doc-sync`。
- **规范候选（累积）**：#1 seq 那条的措辞——「禁 `max(seq)+1`」实指「不持锁的 `max(seq)+1`」，持行锁之下取 max
  再 +1 正是发放方式。

---

### Slice 2: 一轮收尾的事务迁进 turn_state，StreamRecorder 只管内存

- **Type**: AFK
- **Blocked by**: Slice 1
- **接口契约 / 调用方分组**: RFC「随之改变的两个模块 · StreamRecorder」+ admit 第 7 步的写入部分；调用方：`TurnRunner.launch`
- **Complexity**: M

**What to build**:

`StreamRecorder.open(..., persist=)` 收一个 `Callable[[Outcome], Awaitable[None]]`。退出时照旧先收尾
（`_drain`、未结算的路标失败），再把 `Outcome` 交出去——这一轮的选章决策、说出口的各路
（persona / 文本 / 错误 / 用量与 stop_reason）、模型手里那几节 `sources`（保留 `None` 与 `[]` 的区别）。
`persist` 抛了 → 整轮结局报 `error`，这条规矩**留在 recorder**。

`_write_choice` 与 `_persist` 的库那一半迁进 `turn_state` 的收尾函数：一个 `Detached` 事务写选章留档、
锁定、回答（用切片 1 的发号）、引文；引文比对 `find()` 放工作线程，原样搬过来。`TurnRunner.launch`
把它作为 `persist` 传给 recorder。`_LIVE` / `live()` 本片不动。

**Acceptance criteria**:
- [ ] `tests/test_stream_recorder.py` 不再用 `db_session`：以一个捕获 `Outcome` 的假 `persist` 验证——
      残文保住、一个字都没有时 `Outcome` 里没有说出口的一路、后来的订阅者先补再跟、工作线程的字不漏、
      `done` 在 `persist` 返回之后才发、`persist` 抛了 → `error`
- [ ] 库形状类测试迁到新文件 `tests/test_turn_state.py`：完整一条、残缺一条（`complete=false`）、
      一个字都没有什么都不落、正常路径只有一次 INSERT 零 UPDATE、收到 `done` 那一刻消息已在库里
- [ ] `test_retrieval.py` 的「留档写不进去时整轮什么都不落、宣布的是 error」与「一轮跑完那五样东西
      一起在库里」原样绿
- [ ] 全量绿

**Implementation hints**:
- `Outcome` 定义在 `stream_recorder`（它是生产者，按「谁算出来的」分组），`turn_state` 导入它；
  本片起 `stream_recorder` 不再 import `turn_state`。`turn.py` 过渡性地 import `turn_state`（切片 4 反向）。
- 收尾事务里行锁**仍取在写留档之后**，与今天一致；RFC 第 7 步「先取行锁」的顺序在切片 6 连同解析事务
  一起改，单独先改会打开 Risk Notes #1 那个死锁窗口。
- 空 `Outcome` 也照样交给 `persist`：切片 4 要在它的 `finally` 里释放席位，不能靠「有字才调」。

**完成记录（2026-09-23）**:
- `stream_recorder` 新增 `Spoken`（persona / text / error / reply）、`Outcome`（conversation_id / turn / choice /
  spoken / sources）与 `Persist` 类型；`open(..., persist=)` 必填。退出时 `await self._persist(self._outcome())`，
  抛了 → `_persist_error` → 整轮 `error`（这条规矩留在 recorder）。`_write_choice` / `_persist` 删除，
  它不再 import 任何 model、repository、`Detached`、`turn_state`。
- `turn_state.settle(outcome)`：原 `_persist` + `_write_choice` 原样迁入（`_write_choice(session, outcome)` 私有）。
  「一个字都没有就什么都不落、留档也不落」的判断在 `settle` 里；recorder 只负责把「说出口的」挑出来
  （`Outcome.spoken` 只含有字的路）。`TurnRunner.launch` 以 `persist=settle` 打开 recorder。
- import 方向：`stream_recorder` ← `turn_state` ← `turn`；无环。
- 测试：
  - `test_stream_recorder.py` 重写为 10 条、**不连库**：假 `persist`（`_Kept`）记下交出来的 `Outcome`。
    新增三条：选章决策与 sources 随结局交出、没拿到书时 sources 是 None、`persist` 炸了宣布 `error`。
    `live()` 那条过渡保留，切片 4 删。
  - 库形状类迁到 `test_turn_state.py`，改经 `ChatService.ask` 驱动（4 条：账单那几个数、一个字都没说出来就什么都
    不落连留档也不落、消息只 INSERT 不 UPDATE、收到 done 时回答已在库里）；「完整一条」「残缺一条」
    `test_chat.py` 已有（`问一轮逐字流出来并落库` / `断在半路时保住已经说出来的那一段`），未重复。
    这 4 条先在旧代码上跑绿作为安全网，再改实现。
  - `test_retrieval.py` 的「留档写不进去整轮 error」与「五样一起在」原样绿。
- 全量 **424 passed**，ruff 干净。
- **设计读取**：同会话已读 `backend.md` 全文（含规范章节）。应用：「session 不跨边界…活得比调用方长的活儿自己开
  一条 session」（`settle` 里的 `Detached`）、「结束信号先落库后宣布」、「同一条规则只许有一份实现」（落库只剩
  `settle` 一份）。无偏差。
- **规范候选**：无新增。

---

### Slice 3: 「这本书重新解析过了」送到用户眼前

- **Type**: AFK
- **Blocked by**: Slice 2
- **接口契约 / 调用方分组**: RFC Problem 四；SSE 契约新增 `notice {code, text}`；边界测试 6（送达部分）；`backend.md` 待确认 #6
- **Complexity**: M

**What to build**:

- **后端**：recorder 新增 `notice(code, text)`——存成**列表**、当场广播、`listen()` 在 stage 与 token
  **之前全部回放**；stage 再也盖不掉它。`TurnRunner._run` 在 `turn.reparsed` 时发
  `notice("reparsed", …)`，取代今天那条马上被「正在挑章节…」覆盖的 stage。SSE 路由原样透传事件名。
- **前端**：`useAsk` 增加 `notices`——每次提问清空，收到 `token` / `done` **不清**；`ChatView` 在提问区
  之外画一条横幅。类型补齐。

**Acceptance criteria**:
- [ ] 锁定真被清掉之后的下一问：流里有 `("notice", {"code": "reparsed", …})`，且在第一个 `token` 之前
- [ ] 晚到的订阅者（同一个问题重连）经回放也拿到这条告示
- [ ] 整本装得下的书第二轮：没有这条告示（`test_retrieval.py:658` 改成看 `notice` 而不是 `stage`）
- [ ] 第一轮全失败、什么都没落，再问：没有这条告示
- [ ] 删除 `test_retrieval.py:676-720` 三条直接调 `_lock_was_wiped` / `_locked_chapter_ids` 的测试
      （被上面四条取代）
- [ ] `vue-tsc` / `eslint` / `prettier` 干净；横幅的肉眼验证推到切片 8
- [ ] 全量绿

**Implementation hints**:
- `code` 写死字面量 `"reparsed"`（规范「要让代码分支的东西必须机读，不能是文案」）。
- `useSSE.dispatch` 跳过不认识的事件，所以后端先上不会弄坏旧前端。
- 告示不落库，刷新即消失——它说的是「这一轮」。
- 测试里「清掉锁定」本片仍用 `ConversationRepository.clear_locks_for_book`（切片 5 换成 `forget_locks`）。
- 规范候选：`backend.md`「接口与响应」的 SSE 事件名清单缺 `notice`（收尾统一裁定）。

**完成记录（2026-09-23）**:
- 后端：`StreamRecorder.notice(code, text)` —— 告示存成列表、当场广播；`listen()` 在同一个同步块里取快照，
  回放时**告示排在一切之前**（先于 stage、token、结局），已收工的流也回放。`TurnRunner._run` 在
  `turn.reparsed` 时改发 `notice("reparsed", …)`，措辞不变。SSE 路由原样透传，未改。
- 前端：`types` 新增 `TurnNotice {code, text}`；`useAsk` 新增 `notices`（每次提问清空，token / done 不清）；
  `ChatView` 在错误条下面、对话流之外逐条画出，复用页内已有的 `.banner`（黄檗底 + 左边线，
  即「偏离默认才着色」的存疑色）。`NoteBanner` 绑的是 parse_notes 的 `Note` 类型并带「核对过了」按钮，不适用。
- 测试（`test_turn_state.py`「告示」一节，经 `ChatService.ask`）：
  - 重解析之后的下一问：告示在第一个 token 之前（先 RED 后 GREEN）
  - 晚到的订阅者（另一条 session、同一个问题重连）经回放拿到告示
  - 整本装得下的书第二轮：没有告示（由 `test_retrieval.py:658` 迁来，改看 `notice`）
  - 上一轮全失败、什么都没落下：没有告示（取代原「一行留档都没有时不算被清掉」）
  - `test_stream_recorder.py` 加一条：告示不会被后来的 stage 盖掉，晚到的订阅者第一条就拿到它
- 删除 `test_retrieval.py` 的「『重新解析过了』那句话」一节共 4 条：直接调 `_lock_was_wiped` /
  `_locked_chapter_ids` 的 3 条被上面的流测试取代；整本那条迁走。`test_retrieval.py` 里已无对私有方法的调用。
- 全量 **425 passed**（424 − 4 + 5），ruff 干净；`vue-tsc` / `eslint` / `prettier` 干净，前端三个文件保持 LF。
  横幅的肉眼验证推到切片 8。
- **设计读取**：`frontend.md` 规范章节全文（架构级模式 / 代码格式化 / 样式约定 / 文案 / 命名）。应用：
  「失败的措辞只在服务端写一处，前端按 code 决定行为」（前端照样显示 `text`，分支只认 `code`）、
  色彩语义（偏离默认才着色 → 存疑色）、源码 LF、`types/` 只放后端契约类型、注释写明与后端契约相关的取舍。
- **规范候选（累积）**：#2 SSE 事件名清单加 `notice`，并写明它与 `stage` 的分工——「现在在做什么」只留最后一句，
  「这一轮有什么不一样」一句都不能丢、晚到的订阅者也要全部拿到（`frontend.md`「API 调用层」的事件表属架构章节，
  由收尾 sync 同步）。

---

### Slice 4: turn_state.admit 接管准入

- **Type**: AFK
- **Blocked by**: Slice 3
- **接口契约 / 调用方分组**: 接口 `admit(session, draft)`；值对象 `Draft` / `Standing` / `Notice`；调用方：`ChatService.ask`、`TurnRunner`；嫁接 1（孤儿守卫）、嫁接 3（席位为空的夹具）；边界测试 1–4、9
- **Complexity**: L

**What to build**:

按 RFC「`admit` 在里面藏了什么」第 1–4、6 步与第 7 步的释放部分：

- **值对象**（`services/turn.py`）：`Draft`（`ChatService.ask` 算的）、`Standing`（只由 `admit` 算）、
  `Notice`；`Turn` = `Draft` 的字段 + `standing`；删除 `Turn.turn` / `Turn.history` / `Turn.reparsed` /
  `Budget.locked` / `Budget.reuse`；`BookRef` 加 `title`。
- **`ChatService.ask`**：鉴权、人格、大脑、章节、预算照旧，算出 `Draft`，最后一句
  `return await turn_state.admit(self._session, draft)`。`_locked_chapter_ids`、`_lock_was_wiped`
  的逻辑**原样**迁进 `turn_state`（私有，切片 5 再收拢），`_relock_hint`、`_history_for` 随迁。
- **`admit`**：对 `_SEATS` 同步查-占；问题不同 → `ConflictError`（问题存在席位上，不再查库）；问题相同
  → `await asyncio.shield(seat.ready)` 后回放；首领在准备阶段抛 4xx → `ready` 以 `None` 结束，等待者
  自己当首领、拿到它自己的 4xx。**占位之后**读事实（锁定、留档、历史、轮号），`RELOCK_NEEDED` 在写任何
  东西之前抛 `ReselectRequired`；写提问并提交；告示在任务启动**之前**经 `recorder.notice` 发；
  `create_task`，强引用进 `_RUNNING`；孤儿守卫；`ready ← recorder`。
- **释放**：收尾函数的 `finally` 里认人释放（`_SEATS.get(cid) is seat`）——在提交之后、recorder 发
  `done` 之前，两者之间没有 await。
- **`TurnRunner`**：删除 `launch` 与 `_RUNNING`；`_run` 改为 `run(recorder, turn)`，不再自己
  `async with recorder`，不再发告示。**职责清单不变**。
- **`StreamRecorder`**：删除 `_LIVE` / `live()`。
- **`conftest`**：autouse 夹具在每个测试结束时断言席位登记为空。

**Acceptance criteria**（新测试在 `tests/test_turn_state.py`，经 `ChatService.ask` 驱动，两条 session）：
- [ ] 1 · 两条 session 并发提交**同一个问题** → 模型只调一次，两条流内容一致，没有 IntegrityError
- [ ] 2 · 并发提交**不同问题** → 一个 409，库里只有一条提问
- [ ] 3 · 首领正在落库（引文比对卡在 `threading.Event` 上）时同一个问题再来 → 不再调模型，拿到回放
- [ ] 4 · 首领在准备阶段抛 `ReselectRequired` → 等待者拿到同一个 `ReselectRequired` 而不是空流；模型零次
- [ ] 9 · 任务在进入前被取消 → 席位被释放，下一问不是 409
- [ ] 对读三路 + 上述并发之后 seq 连续、无洞无重
- [ ] 原样绿：`test_chat.py` 的重连测试与「检查发生在 await 那一刻」、`test_compare.py` 的三路回放、
      切片 3 的告示测试
- [ ] 删除 `test_stream_recorder.py` 的「正在跑的那条流找得到_跑完就不在了」（`live()` 不复存在，
      被 1、3 取代）；`test_turn.py` 与 `test_brain_key.py:183` 按新的 `Turn` 形状机械改写
- [ ] `ChatService` 不再 import `Retrieval` / `StreamRecorder` / `TurnRunner`
- [ ] 全量绿

**Implementation hints**:
- `admit` 是**普通协程，不是异步生成器**（`backend.md`「核心业务流程·提问」那段说明照旧成立）。
- asyncpg 不允许一条 session 并发使用：并发测试从 `get_session_factory()` 另开 session。
- `FOR UPDATE` 在请求 session 上从读事实一直持有到提交；`ReselectRequired` 与任何异常路径都要回滚放锁、
  并**立刻**释放席位（不等请求结束）。
- 客户端断开会取消请求处理协程：等待者被取消不得连带首领——`shield` 就是为这个。
- 重连方的 `Draft` 是急切算的（要 key 头，前端总会带；约 4 次查询），RFC 已接受这个代价。
- 本片起 `turn_state` import `turn` 与 `stream_recorder`；`turn.py` 必须不再 import `turn_state`，否则成环。
- `detail()` / `retrieval()` 不动。

**完成记录（2026-09-23）**:
- **RED 的样子**：两条 session 同时问同一个问题 → 模型被调了**两次**；服务端日志里两个并存的轮次还在会话行上
  **死锁**了一回（`DeadlockDetectedError`）——两轮同时活着本身就是祸源，消掉竞态就一并消掉它。
- `turn.py`：新增 `Notice`、`Draft`（请求期输入 + `fits_whole`）、`Standing`（turn / history / locked / reuse /
  notices）；`Turn(Draft)` 加 `standing`，由 `Turn.of(draft, standing)` 逐字段合成；`BookRef` 加 `title`；
  `Budget` 只剩 `chars` / `reselect`。`TurnRunner` 删 `launch` / `_RUNNING` / `started`，`_run` → `run(recorder, turn)`，
  不再 `async with recorder`、不再发告示。`turn.py` 不再 import `turn_state`。
- `turn_state.admit(session, draft)`：`_SEATS` 同步查-占；问题不同 → `ConflictError`（问题存在席位上，不再查库）；
  问题相同 → `await asyncio.shield(seat.ready)` 回放；首领准备阶段失败 → 让位并 `ready ← None`，等待者回头
  自己来一遍。`_stand()` 在占位之后读历史、锁定，过逃生门，判清锁，`allocate_seq` 写提问并提交（连同 `ask` 里的
  人格切换）；告示在任务启动前 `recorder.notice`；`create_task(_drive)` 强引用进 `_RUNNING`；孤儿守卫
  `task.add_done_callback(lambda _: _leave(cid, seat))`；`persist` 闭包 `try: settle(...) finally: _leave(...)`。
  `_leave` 认人。`_locked_chapter_ids` / `_lock_was_wiped` / `_relock_hint` / `_history_for` 原样迁入（私有）。
- `ChatService.ask` 只算 `Draft`，末句 `return await turn_state.admit(...)`；不再 import `Retrieval` /
  `StreamRecorder` / `TurnRunner`，也不再出现 `selected_chapter_ids`。`StreamRecorder` 删 `_LIVE` / `live()`。
- 测试（`test_turn_state.py`「准入」一节，均经 `ChatService.ask`、两条 session）：
  1 同一问题并发 → 模型一次、两条流一致、seq [1, 2]；2 不同问题并发 → 一个 409、库里一条提问；
  3 首领正在落库（`turn_state.find` 卡在 `threading.Event` 上撑开那几秒）时同一问题再来 → 回放、不再调模型——
  **变异验证过**：把让位挪到落库之前，这条当场红（`assert 2 == 1`）；
  4 首领被逃生门拦下 → 等着的那个拿到同一个 `ReselectRequired`（先 RED：它永远等在 `seat.ready` 上）——
  首领靠外来事务 `LOCK TABLE messages` 停在占位之后的第一次读上，等待者走到席位前一张 `messages` 都不碰；
  9 后台任务还没跑起来就被取消（模拟服务关停：取消当前任务之外的全部任务）→ 下一问不是 409（先 RED）。
- `conftest.py` 新增 autouse 夹具：每个测试结束时断言没有会话还占着席位（嫁接 3）。全量跑下来**没有一条旧测试
  漏席位**。
- 旧测试的机械改写：`test_turn.py`（新形状，另加一条「`Turn.of` 逐字段搬」、冻结检查加上 `standing`）；
  `test_brain_key.py` 那条 repr 测试改成 `Draft` 与 `Turn` 两层都验（规范「包一层不得把下一层的 repr 隐藏拆掉」：
  `Draft` 是新的一层）；`test_compare.py` 三条 `_history_for` 纯函数测试改从 `turn_state` 导入；删除
  `test_stream_recorder.py` 的 `live()` 那条。
- 重构：四处各写一份的「让模型停住」合成 `_GatedModel`；「晚到的订阅者」测试改用 `_stream_on_own_session`，
  删掉 `_ask_elsewhere`。
- 全量 **431 passed**，ruff 干净。
- **设计读取**：同会话已读 `backend.md` 全文（含规范章节）。应用：`admit` 是普通协程；「session 不跨边界」；
  「写发生在别处时手上的 ORM 对象就是旧的」（锁定仍取单列）；「包一层不得拆掉 repr 隐藏」；「`code` 写死字面量」。
- **偏差**：
  - `_stand` 先读历史、后读锁定（原顺序相反）：结果不变，但让「首领停在占位之后的第一次读上」可以用锁
    `messages` 表造出来。
  - 事实在占位之后读，但**尚未**在会话行锁之下读——那是切片 5 的「一条 `FOR UPDATE` 查询」。单进程里占位已经
    保证上一轮提交在先，这一步只差重解析那条写路径（切片 5 / 6）。
  - `settle` / `allocate_seq` 仍是公开名，切片 7 收成私有。
- **规范候选**：无新增。

---

### Slice 5: 锁定的读、推导、清收拢进 turn_state

- **Type**: AFK
- **Blocked by**: Slice 4
- **接口契约 / 调用方分组**: 接口 `forget_locks(session, *, book_id)`；调用方：`resettle.apply`；RFC admit 第 2–3 步
- **Complexity**: M

**What to build**:

- `admit` 读事实改成**一条** `SELECT … FOR UPDATE OF conversations`：原始锁定、最近一行留档的
  `whole_book`、`max(seq)`、`max(turn)`，全是列级查询。
- 推导：`live = 原始锁定 ∩ 本书现存章节 id`；
  `wiped = (原始非空 且 live 为空) 或 (原始为空 且 最近留档.whole_book is False)`。
- `forget_locks` 包住 `ConversationRepository.clear_locks_for_book`（保留「不带 `user_id`」的例外与
  `synchronize_session="fetch"`），`resettle.apply` 改调它；`turn_state` 成为 `selected_chapter_ids`
  **唯一的写者**。

**行为变化**（修掉的缺陷）：锁定里的 id **全都已经不存在**时，今天是悄悄全价重挑、一句不说
（`choose` 过滤掉死 id，而 `_lock_was_wiped` 看见原始锁定非空就说「没被清」）；之后发 `reparsed` 告示。
锁定只有一部分死掉：照旧复用活着的那几节、不告示。

**Acceptance criteria**:
- [ ] 锁定全是不存在的 id → 流里有 `reparsed` 告示，并且重挑
- [ ] 锁定一部分不存在 → 只用活着的那几节，没有告示，不重挑
- [ ] `test_reparse.py` 的「重解析清掉会话的选章锁定」经 `forget_locks` 保持绿
- [ ] 切片 3 的告示测试改用 `forget_locks` 造「清掉」，保持绿
- [ ] `ChatService` / `stream_recorder` / `resettle` 里不再出现 `selected_chapter_ids`
- [ ] 全量绿

**Implementation hints**:
- 身份映射的绕行（规范「写发生在别处时，手上那个 ORM 对象就是旧的」）从此只在这一条查询里发生。
- `chapter_selector.choose` 里过滤死 id 的那道防线保留：它是纯模块对自己入参的前提检查，不依赖调用方。

**完成记录（2026-09-23）**:
- `turn_state._facts()`：`_hold()`（`SELECT conversations.id … FOR UPDATE`）之后**另起一条语句**取原始锁定、
  最近一行留档的 `whole_book`、`max(seq)`、`max(turn)`（三个标量子查询，全列级）。`_stand` 用它们发轮号与 seq
  （不再调 `allocate_seq`），行锁持有到写完提问提交。`_lock_was_wiped` 变成纯函数
  `(raw, alive, last_whole_book) -> bool`：`raw` 非空时「一个活的都没有」即被清；`raw` 为空时看最近留档。
  锁定只认活着的那几节（`∩ draft.book.chapters` 的 id）。
- **偏差（相对 RFC「一条 `SELECT … FOR UPDATE`」）**：锁与读必须是两条语句。最初照 RFC 写成一条，切片 1 的
  「写提问要等会话行上的锁」当场红——**读已提交下一条语句的快照在它开始时就定了，`FOR UPDATE` 等到锁之后只重读
  被锁的那一行，同一条语句里的 `max(seq)` 子查询仍是等锁之前的世界**，算出同一个号撞唯一约束。拆成「先锁、后读」
  之后绿。`allocate_seq` 本来就是这个形状，改为复用 `_hold()`。
- `forget_locks(session, *, book_id)` 包 `ConversationRepository.clear_locks_for_book`（保留不带 `user_id` 的例外与
  `synchronize_session="fetch"`），不提交；`resettle.apply` 改调它。`turn_state` 成为 `selected_chapter_ids`
  唯一的写者（`settle` 与 `forget_locks`）；`chat_service` / `stream_recorder` / `resettle` / `turn` 的代码里
  不再出现这个列名（只剩两处模块说明提到它）。
- **行为变化（修掉的缺陷）**：锁定里的 id 全都不存在了 → 发 `reparsed` 告示并重挑（原先悄悄全价重挑、一句不说）。
- 测试（`test_turn_state.py`「锁定」一节）：锁定全是死 id → 告示 + 真重挑（流里有「挑好了」）——先 RED；
  锁定只死了一部分 → 照活着的走、不告示、不重挑（原本就对，钉住）。告示一节的 `_wipe_locks` 改用
  `forget_locks`。`test_reparse` 经 `resettle.apply → forget_locks` 保持绿。
- 全量 **433 passed**，ruff 干净。
- **设计读取**：同会话已读。应用：「写发生在别处时手上的 ORM 对象就是旧的」（列级读）；「同一个『空』有两种来路时
  判据不能靠反推」（判据仍是最近留档这一既有事实，新增的「名单全死」分支也是既有事实——章节表里查得到）；
  「repositories 只做存取」（清锁的 SQL 仍在 `conversation_repo`，谁在什么时候清归 `turn_state`）。
- **规范候选（累积）**：#3「**先锁、后读**：要在行锁之下读的东西，与取锁的 `FOR UPDATE` 不能放在同一条语句里」
  （理由与实测见上）。跨模块、以后所有「持锁发号 / 持锁读」都适用，建议进 `backend.md`「并发与后台任务」或
  `sql.md`「约定」。

---

### Slice 6: 轮次进行中重解析

- **Type**: AFK
- **Blocked by**: Slice 2, Slice 5
- **接口契约 / 调用方分组**: RFC Problem 五；admit 第 7 步的死 id 处理与锁序；嫁接 2；边界测试 7、8；调用方：解析事务（`parse_service._record_success` / `resettle`）
- **Complexity**: M

**What to build**:

- **收尾**：先取会话行锁（RFC 第 7 步原文的顺序）→ 查这一轮涉及的 `chapter_id` 里哪些还在 → 不在的，
  在 `retrieval_items` / `citations` 里写 NULL（与 `ON DELETE SET NULL` 同效）→ 锁定只写活着的子集
  （整本 → `[]`；复用锁定的轮次 → 不写）。
- **锁序统一为「先会话行、后章节行」**：解析事务在 `replace_chapters` **之前**先 `forget_locks`（同一个
  事务里，语义不变）。`resettle` 的调用顺序因此变成 `snapshot` → `forget_locks` → `replace_chapters` →
  重定位引文，它模块注释里「调用顺序不是可选的」那段同步更新。
- **`ask`**：书的状态不是 `ready` 时 409，措辞按状态分——解析中：等它解析完再问；停下了：去书籍详情看。

**Acceptance criteria**:
- [ ] 7 · 轮中重解析（FakeBrain 卡住；另一条 session 跑完 snapshot / `forget_locks` / 换章节并提交；
      再放开）→ 回答照样落库，死 `chapter_id` 为 NULL，锁定是活着的子集或 `[]`，流以 `done` 结束
- [ ] 8 · 书在解析中 → `ask` 409，库里没有新的提问，模型零次
- [ ] 若能确定性复现旧锁序下的交错，加一条「轮中重解析不死锁」；复现不了就在完成记录里写明未测
- [ ] `test_reparse.py` 全绿（`_reparse_with` 里抄的那份顺序同步成四步）
- [ ] 全量绿

**Implementation hints**:
- 按新锁序，两边只会串行：收尾先提交 → 解析随后删章节，`ON DELETE SET NULL` 处理刚写进去的行；
  解析先提交 → 收尾拿到行锁后看见死 id，自己置空。
- 状态检查放在算 `Draft` 之前（`start()` 同款）。
- 「重解析时拒绝正在跑轮次的书」那一侧不在本 RFC 范围。

**完成记录（2026-09-23）**:
- **收尾（`settle`）**：`allocate_seq` 提到事务最前（先锁会话行，RFC 第 7 步原文的顺序）；随后另起一条语句查这一轮
  涉及的 chapter id（选章明细、锁定、引文）哪些还在（`_chapter_ids_in` / `_alive_chapters`）；不在的在
  `retrieval_items` 与 `citations` 里写 NULL（其余字段照写，与 `ON DELETE SET NULL` 同效）；锁定只写活着的子集。
- **解析事务的锁序**：`resettle.snapshot()` → **`resettle.prepare()`** = 先 `turn_state.forget_locks`（锁会话行）、
  再拍引文快照；`apply()` 只剩重定位。`parse_service._record_success` 与 `test_reparse._reparse_with` 改调
  `prepare`，`resettle` 模块注释「调用顺序不是可选的」补上锁序那一条理由。全项目碰这两类行的事务一律
  **先会话行、后章节行**。
- **`ask`**：`_check_readable(book)` 放在读完书之后、算 `Draft` 之前——解析中 → 409「正在重新解析，等它解析完再问
  ——这一问还没发出去，没花钱」；停下了 → 409「上一次解析停下了…到书籍详情页看看」。
- 测试（`test_turn_state.py`「轮中重解析」一节）：
  - 7 · 一轮正跑着时书被重解析（另一条 session 换掉全部章节并提交）→ 流以 `done` 结束、回答落下、引文
    `chapter_id` 为 NULL、下一问发 `reparsed` 告示（锁定已空）——先 RED：整轮 `error`（撞外键）。
  - **锁序**：重解析事务未提交时收尾开始（`prepare` + `replace_chapters` 之后放行模型，看见收尾在锁上排队再
    `apply` + 提交）→ `done`、引文 `chapter_id` 为 NULL。**确定性复现了旧锁序下的死锁**：把 `forget_locks` 挪回
    `apply`（先删章节、后清锁定），这条当场 `DeadlockDetectedError`，被杀的是收尾那边，整轮 `error`。
  - 8 · 书在解析中 → `ask` 409、模型零次、提问不落库——先 RED。
- 全量 **436 passed**，ruff 干净。
- **设计读取**：同会话已读 `backend.md` 与 `sql.md`「约定」。应用：`sql.md`「要读的旧状态，必须在破坏它的操作之前读」
  （`prepare` 保留快照在删章节之前）；「级联：引用关系用 SET NULL」（死 id 置空与之同效）；「先锁、后读」（候选 #3，
  `_alive_chapters` 在拿到行锁之后另起语句）。
- **偏差**：`resettle` 的公开函数 `snapshot` 改名为 `prepare`（多做了清锁定）——计划只写了「解析事务在
  `replace_chapters` 之前先 `forget_locks`」，没有指定落在哪个函数里；放进 `resettle` 让「换章节之前要做的事」只有一个
  入口，调用方仍是「准备 → 换章节 → 善后」三步。
- **规范候选（累积）**：
  - #4「**同时碰多张表的事务，锁的先后全项目一致**：会话行在前、章节行在后」（死锁的复现条件与实测见上）。
  - #5 `sql.md`「约定」里「要读的旧状态…」一条的「实现见 `services/resettle.py` 的 `snapshot()` → `replace_chapters()`
    → `apply()`」已过期，应改为 `prepare()`（规范章节，sync 不能改）。

---

### Slice 7: 删除旧实现与被取代的旧测试

- **Type**: AFK
- **Blocked by**: Slice 4, Slice 5, Slice 6
- **接口契约 / 调用方分组**: RFC「需删除 / 改写的旧测试」+「Dependency Strategy · 工作单元：撤回端口」
- **Complexity**: S

**What to build**:

- 删除 `UnitOfWork` Protocol 与 `tests/fakes.py:Attached`；`test_unit_of_work.py` 里 `Attached` 相关四条
  （含 parity 那条）删除，`Detached` 的五条保留；`db/unit_of_work.py` 模块 docstring 去掉「端口」说法。
- 清过渡残留：`turn_state` 对外只有 `admit` / `forget_locks`（其余一律 `_` 前缀）；
  `ConversationRepository.clear_locks_for_book` 只剩 `forget_locks` 一个调用方。

**Acceptance criteria**:
- [ ] `src/` 与 `tests/` 里 grep 不到：`UnitOfWork`、`Attached`、`_LIVE`、`StreamRecorder.live`、
      `TurnRunner.launch`、`_lock_was_wiped`、`_locked_chapter_ids`
- [ ] 删掉的测试与 RFC「需删除 / 改写的旧测试」逐条对得上；RFC 列为「保留不动」的几条仍在且绿
- [ ] 全量绿，ruff 干净，`vue-tsc` / `eslint` / `prettier` 干净

**Implementation hints**:
- 规范「端口的测试适配器与生产适配器…parity」那条的实测例子引用 `Attached`，删除后例子过期——规范候选。

**完成记录（2026-09-23）**:
- `db/unit_of_work.py`：删 `UnitOfWork` Protocol（连同 `Protocol` / `runtime_checkable` import 与端口说明），`Detached` 不动。
- `tests/fakes.py`：删 `Attached` 与只为它存在的 `_uow_logger`。`tests/test_unit_of_work.py`：删 `Attached` 相关四条
  （写入落在手边那条 session、两个适配器同一份错误约定、两个适配器都满足端口、测试适配器吃得下提交那一刻的失败），
  `Detached` 五条保留。
- `turn_state`：`settle` → `_settle`、`allocate_seq` → `_allocate_seq`；对外只剩 `admit` / `forget_locks`
  （另有模块级常量与私有状态，全 `_` 前缀）。`resettle` 与 `turn.py` 里两处指向旧名（`turn_state.settle`、`_record`）
  的注释改掉。`ConversationRepository.clear_locks_for_book` 只剩 `forget_locks` 一个调用方。
- 验收 grep：`src/` 与 `tests/` 里没有 `UnitOfWork` / `Attached` / `_LIVE` / `StreamRecorder.live` / `TurnRunner.launch` /
  `_locked_chapter_ids`。**`_lock_was_wiped` 这个名字仍在**——切片 5 把它重写成 `turn_state` 里的一个纯函数，同名
  是有意的（说的还是同一件事）；要消失的是「测试直接调 `ChatService._lock_was_wiped`」，那已经没有了。
- 对照 RFC「需删除 / 改写的旧测试」：`test_retrieval` 三条私有方法测试（切片 3 删）、`test_stream_recorder` 的 `live()`
  那条（切片 4 删）与库形状类（切片 2 迁到 `test_turn_state`）、`test_unit_of_work` 的 `Attached` 四条（本片删）——
  逐条对上。RFC 列为保留的三条（`test_chat` 重连、`test_compare` 三路回放、`test_retrieval` 留档写不进去）仍在且绿。
- 全量 **432 passed**（436 − 4），ruff 干净；`vue-tsc` / `eslint` / `prettier` 干净。
- **设计读取**：同会话已读。应用：「私有函数 / 模块级私有常量用 `_` 前缀」。
- **规范候选（累积）**：#6 `backend.md`「设计模式与约定」端口 parity 那条的实测例子引用 `Attached`（已删）；
  条目本身作为通则仍成立，例子需要换或删。

---

### Slice 8: 真实环境走一遍

- **Type**: HITL
- **Blocked by**: Slice 7
- **接口契约 / 调用方分组**: 端到端确认
- **Complexity**: S

**What to build**:

`docker compose up -d --build`（无迁移），用一个你有 key 的大脑在浏览器里走：

1. 同一个会话开两个标签页，同时问同一个问题 → 两边看到同一段回答，会话里只有一条回答；供应商控制台只记一次调用
2. 一个标签页正在答，另一个问别的 → 看到「上一个问题还在回答」
3. 找一本装不下、已经问过一轮的书，重新解析，完了再问 → 对话页出现「重新解析过了」横幅，不被「正在挑章节…」盖掉；刷新之后横幅消失
4. 重新解析进行中去问 → 看到「等它解析完」的提示
5. 常规一问一答、对读三路、答到一半刷新页面的回放照常

**Acceptance criteria**:
- [x] 以上五条逐条由你确认；有偏差就回到对应切片修
- [x] 只报告观察，不把真实 key 贴进对话

**完成记录（2026-09-23）**:
- `docker compose up -d --build api web`：api healthy；未登录调 `/ask` 回 401；容器里 `turn_state` 对外只有
  `admit` / `forget_locks`——确认跑的是新代码。无迁移。
- 用户在浏览器里走完五条，回复「验过了」。未报告偏差。

## Dependency Graph

```
Slice 1 → Slice 2 → Slice 3 → Slice 4 → Slice 5 → Slice 6 → Slice 7 → Slice 8
             └───────────────────────────────────↗
```

## Risk Notes

1. **锁序死锁（本 RFC 自己引入，切片 6 消除）。** 解析事务先删 `chapters`（行锁）、后改 `conversations`；
   RFC 第 7 步要收尾事务先 `FOR UPDATE` 会话行、后写引用章节的行（外键检查对 `chapters` 取 KEY SHARE）。
   两边锁序相反，轮中重解析时会互等，Postgres 杀掉其中一边——杀到收尾就是丢一条已付费的回答。
   今天的代码恰好与解析同序，所以切片 1–5 **保持旧顺序**，切片 6 让解析事务先 `forget_locks`，全部事务
   统一为「先会话行、后章节行」。
2. **过渡期的 import 方向。** 切片 1：`stream_recorder → turn_state`；切片 2：`turn → turn_state`，
   `stream_recorder` 不再依赖它；切片 4：`turn_state → turn, stream_recorder`，`turn` 必须不再依赖
   `turn_state`。每片结束检查无环。
3. **并发测试的确定性。** 优先用「外来事务持锁」与 `threading.Event` 卡点，少用 `sleep` 赌时序；
   并发的两路各用一条 session；每个测试结束席位为空（切片 4 的夹具）。
4. **请求 session 上持行锁。** `admit` 从读事实到提交持有会话行的 `FOR UPDATE`；同一行上的 PATCH
   （换人格、处境开关）会短暂排队，属预期。异常路径必须立刻回滚并释放席位。
5. **单实例假设不变**（`backend.md` 待确认 #2）。席位在进程内；`uvicorn --reload` 与容器里的 api 同时连
   一个库时席位不共享，但行锁仍保证 seq 不撞。
6. **前端无测试框架。** 切片 3 的横幅只有静态检查，肉眼验证在切片 8。
7. **不在范围、行为照旧**：整轮已经结束之后才到的「重连」仍是新的一轮、再付一次钱；
   `Turn.fits_whole` 用的字数口径不动（候选 4）。
8. **规范候选（auto 模式收尾统一裁定，`design-doc-sync` 不改规范章节）**：SSE 事件名清单加 `notice`；
   seq 那条规范的理由（`live()` → 席位）与「禁 `max(seq)+1`」的措辞；端口 parity 那条的过期例子；
   以及实施中可能提出的「先会话行、后章节行」锁序规则。
9. **部署**：无迁移。compose 里的 api 跑的是旧代码，切片 8 之前要重建。

## Status Tracking

| Slice | Status | Assignee | Notes |
|-------|--------|----------|-------|
| 1 | 🟢 Done | - | 418 passed |
| 2 | 🟢 Done | - | 424 passed |
| 3 | 🟢 Done | - | 425 passed |
| 4 | 🟢 Done | - | 431 passed |
| 5 | 🟢 Done | - | 433 passed |
| 6 | 🟢 Done | - | 436 passed |
| 7 | 🟢 Done | - | 432 passed |
| 8 | 🟢 Done | - | HITL：用户走查通过（2026-09-23） |

Status: 🔴 Not started | 🟡 In progress | 🟢 Done | ⚠️ Blocked

## 规范候选判定（2026-09-23，auto 模式收尾一次性裁定）

六条全部同意写入，条目末尾均附来源：

| # | 候选 | 写到哪里 |
|---|------|---------|
| 1 | seq 规范措辞「禁**不持锁的** `max(seq)+1`」，理由改为「由 `turn_state` 的席位保证」 | `backend.md`「并发与后台任务」（修订原条目） |
| 2 | SSE 事件名清单加 `notice`；新增一条「`stage` 与 `notice` 是两个事件」 | `backend.md`「接口与响应」 |
| 3 | 先锁、后读（读已提交的语句快照陷阱） | `backend.md`「并发与后台任务」（新增） |
| 4 | 锁序全项目一致：会话行在前、章节行在后 | `backend.md`「并发与后台任务」（新增） |
| 5 | 「要读的旧状态…」一条的实现引用 `snapshot()` → `prepare()` | `database/sql.md`「约定」（修订引用） |
| 6 | 端口 parity 条目保留为通则，例子注明 `Attached` 与端口已撤 | `backend.md`「架构级模式」（修订例子） |
