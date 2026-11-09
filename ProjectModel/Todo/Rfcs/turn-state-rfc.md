---
status: implemented
---

# 会话的轮次状态（turn_state）—— Architecture RFC

> 产出于 2026-09-22 的 `improve-codebase-architecture`。审视基线：brain-model-picker 全部完成、
> `design-doc-sync` 已收尾之后的代码库（全量测试 416 passed）。本 RFC 只描述提案，不改代码。
> 实施须走 RFC 快车道（显式 `rfc to plan`）。
>
> **与 `turn-runner-rfc.md`（implemented）的关系**：本 RFC 建在它之上。`TurnRunner` 的职责
> 不变（选章的触发、读盘、引擎 `Session` 装配、三路并发与错误边界、错误翻译）；它当时留在
> `launch` / `_RUNNING` / `StreamRecorder` 里的「登记与落库」迁进本 RFC 的 `turn_state`。
> 另外**撤回**它引入的 `UnitOfWork` 端口与测试适配器 `Attached`（用户 2026-09-22 裁定，理由见
> 「Dependency Strategy」）。那份 RFC 已实施，不置 superseded。
>
> 方案选定过程：四个方向（最小接口 / 最大灵活 / 为最常见调用方优化 / 端口与适配器）各出一版，
> 用户选定「最小接口 + 三处嫁接」。四版在核心机制上完全收敛（见「Proposed Interface」末尾），
> 分歧只在「谁来造 `Turn`、终局事务放哪、要不要注入工作单元」。
>
> **实施记录（2026-09-23，`implemented`）**：按 `Todo/Plans/turn-state-plan.md` 八片实施，全量 432 passed，
> 用户真实环境走查通过。与本文的三处出入，以代码为准：
> 1. 「一条 `SELECT … FOR UPDATE` 取齐事实」实为**两条语句**：先锁会话行，再另起一条读。读已提交下一条语句的
>    快照在开始时就定了，`FOR UPDATE` 等到锁之后只重读被锁的那一行，同一条语句里的 `max(seq)` 子查询仍是
>    等锁之前的——照本文写法会算出重复的号（切片 5 的测试当场撞上）。
> 2. 收尾「先取行锁」必须连同重解析事务一起改顺序，否则引入锁序死锁（切片 6 确定性复现）。解析那边由
>    `resettle.prepare()`（新）先清锁定、再拍引文快照，然后才换章节；`apply()` 只剩重定位。
> 3. `ask` 的书状态检查按状态分两句（解析中 / 停下了），不是一句「正在重新解析」。

## Problem

### 一、「这个会话此刻能不能开一轮、开了拿什么开」没有主人

开一轮之前要回答四件事：有没有一轮正在跑（是重连还是新问题）？这一轮第几轮、消息几号？
锁着哪几节（锁定为空时，是本来就空还是被重解析清掉了）？收尾时回答、引文、选章留档、锁定
怎么一起落？今天这四件事分在五处：

| 事实 | 在哪里写 / 判 |
|---|---|
| 有没有一轮在跑 | `chat_service.py:296` 查 `StreamRecorder.live()`；`stream_recorder.py:129` 在**后台任务进了 recorder 之后**才登记；`:138` 无条件 pop；`turn._RUNNING` 另是一份登记 |
| 轮号 / seq | 提问那条：`chat_service.py:353-358` 从 Python 里读到的行算；回答那几条：`stream_recorder.py:419` 再 `max(seq)+1` 一次 |
| 锁定 | 写：`stream_recorder._write_choice`（一个在 Design「模块边界」表里没有行的模块）；读：`chat_service._locked_chapter_ids`（带一段绕开身份映射的变通）；解释：`chat_service._lock_was_wiped`（越过 repository 直接查 `Retrieval`）；清：`conversation_repo.clear_locks_for_book`；死 id 过滤：`chapter_selector.choose:208` |
| 本轮告示 | `recorder.stage()` 只有一个位置 |

`backend.md`「模块边界」写的是 `turn` 拥有落库、`conversation_repo` 只管别的子系统动会话那几笔；
实际上落库在 `stream_recorder`，锁定的读、判、清横跨三层。

### 二、单飞有三道缝，每道都能让用户为同一个问题付两次钱（Spec #39）

1. **检查与登记之间隔着约 7 次 await 和一次 commit。** `chat_service.py:296` 查完「没有在跑」之后，
   还要走 `config_for`、读书、读章节、读锁定、读历史、判清锁、写提问并提交，才轮到 `TurnRunner.launch`
   里的后台任务把自己登记进 `_LIVE`。两个标签页同时提交，两个请求都能穿过检查：要么第二个撞
   `uq_messages_conv_seq` 变成 500，要么两个都活下来、**模型被调两次**。
2. **先 pop 再落库。** `StreamRecorder.__aexit__` 第一行（`stream_recorder.py:138`）就把自己从 `_LIVE`
   摘掉，然后才 `_persist`（比对引文 + 写库，秒级）。这几秒里同一个问题再来一次，`live()` 说没有在跑，
   于是开一轮新的、再付一次钱。
3. **pop 不认人。** 第 1 道缝里若两轮都活下来，先结束的那一轮会把后来者的登记一并摘掉。

**seq 的发号违反一条已裁定的规范。** `backend.md`「并发与后台任务」：「`messages.seq` 持 `conversations`
行锁发放，禁 `max(seq)+1`」。全仓库没有一处 `FOR UPDATE`，`stream_recorder.py:419` 用的正是 `max+1`。
那条规范给的理由是「一个会话同一时刻只有一轮在跑，`StreamRecorder.live()` 就是钉这个的」——而
`live()` 恰好建在上面第 1 道缝上。

### 三、锁定一个概念，五个主人；测试只能去碰私有方法

锁定由 `conversations.selected_chapter_ids` 与**最近一行** `retrievals.whole_book` 两个事实**合起来**才说得清
（`[]` 有两种来路：整本装得下本来就锁空的，和重解析清掉的）。写在后台任务那条 session 上，读在请求那条
session 上——于是读的那一侧必须取单列绕开身份映射（`chat_service.py:408-425` 那 18 行注释在解释这件事）。

测试因此只能直接调用 `ChatService._lock_was_wiped` / `_locked_chapter_ids`（`tests/test_retrieval.py:693-720`
共三条）——测试钉在实现上，而不是行为上。

### 四、「这本书重新解析过了」那句告示递送不到（`backend.md` 待确认 #6）

`turn.py:175` 把它交给 `recorder.stage()`，紧接着 `_gather` 在任何 await 之前（`turn.py:305`）用
「正在挑章节…」覆盖掉；`listen()` 只回放最后一条 stage。于是它恰好在**真该说**的时候（真重解析 →
真重挑 → 真按全价重算）看不见。`test_retrieval.py:676` 的 docstring 自己承认了这一点。

### 五、轮次进行中重解析，一条已付费的回答会丢（代码推断，未实跑）

`ChatService.ask` 不看 `book.status`（只有 `start()` 看）。重解析的 `replace_chapters` 删掉旧章节行之后，
正在跑的那一轮落库时仍按旧 `chapter_id` 写 `retrieval_items` / `citations`——两列都外键指向 `chapters`，
整轮事务失败，用户看到「这一轮没能存下来」，钱已经花了。

### 六、工作单元的端口只是名义上的

`db/unit_of_work.py` 定义了 `UnitOfWork` 端口，但三处生产代码（`stream_recorder.py:53`、`turn.py:35`、
`parse_service.py:334`）都按名字 import `Detached`，没有任何注入点；测试适配器 `Attached` 只被它自己的
测试（`test_unit_of_work.py` 四条）用到，而后台写入的测试仍在做它本来要消除的 commit 仪式。

## Proposed Interface

### 对外只有两个入口

```python
# services/turn_state.py —— 会话的轮次状态：准入、事实、发号、告示、收尾落库
async def admit(session: AsyncSession, draft: Draft) -> AsyncIterator[tuple[str, dict]]
async def forget_locks(session: AsyncSession, *, book_id: int) -> int
```

- `admit`：**普通协程，不是异步生成器**——它会抛的 4xx（不同问题撞上正在跑的一轮 → 409、问到没载入的
  章节 → `ReselectRequired`）都发生在 `await` 那一刻。返回这一轮的事件流（首领是新流，重连者是回放）。
- `forget_locks`：重解析善后清锁定。包住 `ConversationRepository.clear_locks_for_book`（保留它「不带
  `user_id`」那条有理由的例外与 `synchronize_session="fetch"`），使 `turn_state` 成为
  `selected_chapter_ids` **唯一的写者**。

### 值对象（放在 `services/turn.py`，沿用「按谁算出来的分组」那条规范）

```python
@dataclass(frozen=True)
class Draft:                    # ChatService.ask 算的：请求期的全部输入，**不碰锁定、不碰轮号**
    conversation_id: int
    question: str
    personas: list[str]         # 这一轮跑哪几路
    answering: str              # 主干人格：拼历史时优先取谁
    book: BookRef               # 增加 title（逃生门那句要点名书）
    budget: Budget              # 只剩 chars / reselect；locked 与 reuse 移走
    brain: BrainRef
    reflection: bool

@dataclass(frozen=True)
class Notice:
    code: str                   # 写死的字面量，如 "reparsed"（「要让代码分支的东西必须机读」）
    text: str

@dataclass(frozen=True)
class Standing:                 # 只由 admit 在行锁下算
    turn: int
    history: list[dict]
    locked: list[int]           # 仍然活着的那几节
    reuse: selector.Choice | None
    notices: tuple[Notice, ...]

# Turn = Draft 的字段 + standing。删除：Turn.turn / Turn.history / Turn.reparsed /
# Budget.locked / Budget.reuse —— 它们都是「只有在行锁下才算得对」的东西
```

### 调用方迁移后

```python
# ChatService.ask —— 鉴权、人格、大脑、章节、预算照旧；新增一道书状态检查（嫁接 2）
conversation = await self._require(conversation_id, user_id)
...
if book.status != "ready":
    raise ConflictError("《…》正在重新解析，等它解析完再问。")
return await turn_state.admit(self._session, Draft(...))

# resettle.apply
cleared = await turn_state.forget_locks(session, book_id=book.id)
```

`ChatService` 删除 `_locked_chapter_ids`、`_lock_was_wiped`，以及对 `Retrieval` / `StreamRecorder` /
`TurnRunner` 的 import；`_relock_hint`、`_history_for` 随它们的调用点迁进 `turn_state`。
`detail()` / `retrieval()`（⑥屏）**不动**：它们是只读视图，没有准入逻辑、没有缺陷，给它们加一个
`peek()` 就是第三个入口而无事可修。

随之改变的两个模块（不增加入口）：

- **`StreamRecorder`** 只剩内存里的活：累积、广播、回放。删除 `_LIVE` / `live()` / `_persist` /
  `_write_choice`；`open(..., persist=)` 收一个 `Callable[[Outcome], Awaitable[None]]`，退出时交出
  `Outcome`（这一轮的选章决策 + 说出口的各路 + 模型手里那几节）；新增 `notice(code, text)`——告示存成
  **列表**，广播，`listen()` **全部回放**，排在 stage 与 token 之前，stage 再也盖不掉它。
- **`TurnRunner`** 删除 `launch` 与 `_RUNNING`；`_run` 改为 `run(recorder, turn)`，不再自己
  `async with recorder`，不再发 reparsed 那条 stage。**它的职责清单不变**。

### `admit` 在里面藏了什么（按发生顺序）

1. **单飞。** 第一句是对进程内 `_SEATS[conversation_id]` 的同步查-占，查与占之间没有 await，在事件循环上
   是原子的。已被占：问题不同 → `ConflictError`；问题相同 → `await asyncio.shield(seat.ready)` 后返回
   `recorder.listen()`（`shield`：第二个标签页断开不得连带取消首领）。首领在准备阶段抛了 4xx，`ready`
   以 `None` 结束，等待者回头自己当首领，于是拿到**它自己的**那个 4xx，而不是一条空流。
2. **一次往返读全事实。** 一条 `SELECT … FOR UPDATE OF conversations` 取原始锁定、最近一行
   `retrievals.whole_book`、`max(seq)`、`max(turn)`，随后读历史行。全是列级查询——绕开身份映射这件事
   **只在这一处**发生。
3. **推导。** `live = 原始锁定 ∩ 本书现存章节 id`；
   `wiped = (原始锁定非空 且 live 为空) 或 (原始锁定为空 且 最近留档.whole_book is False)`；
   `reuse` 由 `selector.choose` 给出，`RELOCK_NEEDED` 当场抛 `ReselectRequired`——**在写任何东西之前**。
4. **写提问。** 行锁之下发 `turn = max+1`、`seq = max+1`，写提问、定标题，在请求 session 上提交（它同时
   带走 `ask` 里那次人格切换）。第 2–4 步任何失败都释放席位。
5. **告示。** `wiped` 时在任务启动**之前** `recorder.notice("reparsed", …)`，早到晚到的订阅者都经回放拿到。
6. **启动。** `create_task`，强引用放进 `_RUNNING`；**孤儿守卫**（嫁接 1）：任务的 done-callback 释放一个
   从未进入过的席位——被取消的任务不能留下一个永久的 409；然后 `ready ← recorder`。
7. **收尾（`persist` 回调，一个 `Detached` 事务）。** 取行锁，为说出口的各路一次发一段 seq；
   `choice.records` 时写 `retrievals` / `retrieval_items`；锁定写 `[]`（整本）或选中 id 中**仍然活着的**
   那部分（复用锁定的轮次不写）；写回答与引文（`find()` 放工作线程，原样搬过来）；**已被删掉的
   `chapter_id` 置空**，与 `ON DELETE SET NULL` 同效，而不是撞外键丢掉一条已付费的回答。`finally` 里
   **认人地**释放席位（`_SEATS.get(cid) is seat`）——在提交之后、recorder 发 `done` 之前，两者之间没有 await。

### 四版收敛的核心（选型时的佐证）

四个方向独立设计，以下六点全部一致：同步原子占位且在第一个慢 await 之前；认人释放且在提交之后；
seq 只有一个发放者、持行锁；锁定事实一条列级查询、`ChatService` 不再碰锁定；新增可加的 SSE 事件
`notice`、全量回放；落库时死章节 id 置空。本 RFC 的取舍只在收敛之外的部分。

## Dependency Strategy

- **In-process**：席位登记 `_SEATS` 与任务强引用 `_RUNNING` 是 `turn_state` 的私有状态。单实例是现行
  硬约束（`backend.md` 待确认 #2）；将来多实例（advisory lock + LISTEN/NOTIFY 回放，或外部存储）只改
  `admit` 的内部，调用方零改动。**不为它预先抽端口**——一个端口配一个适配器，在单进程里只是间接层。
- **Local-substitutable**：Postgres。测试照旧用 `booksoul_test`，`FOR UPDATE` 与发号在真库上跑；
  每个测试前 `TRUNCATE`、真提交，所以并发测试可以另开一条 session 读到对方的写。
- **Mock（真外部）**：模型厂商，照旧在引擎 `brain.create` 处换成 `FakeBrain`。
- **工作单元：撤回端口，保留 `Detached`。** 删除 `UnitOfWork` Protocol 与 `tests/fakes.py:Attached`。
  理由：没有任何生产代码注入它；本 RFC 把后台写入的测试缝上移到 `persist` 回调（recorder 的测试不再
  需要数据库）+ 真库（`turn_state` 的测试），`Attached` 再无用处。「替换，而非堆叠」。`Detached` 的
  两种错误策略（`raise` / `log`）与「套住提交那一刻」的行为不变，它自己的测试保留。

## Testing Strategy

### 需编写的边界测试（新文件 `tests/test_turn_state.py`，经 `ChatService.ask` 驱动）

1. 两条 session 并发提交**同一个问题** → 模型只被调一次，两条流内容一致，没有 IntegrityError。
2. 并发提交**不同问题** → 一个 409，库里只有一条提问。
3. 首领正在落库（把引文比对卡在一个 `threading.Event` 上）时同一个问题再来 → 不再调模型。
4. 首领在准备阶段抛 4xx → 等待者拿到同一个 4xx，而不是空流。
5. seq 连续：对读三路 + 并发两次提交 → 1..n，无洞无重。
6. 重解析后的下一问 → 流里有 `("notice", {"code": "reparsed", …})`，一个晚到的订阅者经回放也拿得到；
   整本装得下的第二轮、一行留档都没有的会话 → 都没有这条告示。（取代今天那三条私有方法测试）
7. 轮次进行中重解析 → 回答照样落库，死 `chapter_id` 置空，锁定是活着的子集或空。
8. 书在解析中 → `ask` 409（嫁接 2）。
9. 任务在进入前被取消 → 席位被释放（嫁接 1）。

**测试夹具不变量（嫁接 3）**：`conftest` 的 autouse 夹具在每个测试结束时断言席位登记为空。它是测试
环境的不变量（和「没有泄漏的连接」同一类），不是行为断言——`_SEATS` 是模块级状态，一条挂住的测试
不能把席位漏给下一条。

### 需删除 / 改写的旧测试

- `tests/test_retrieval.py:676-720` 三条直接调私有方法的测试 → 由边界测试 6 取代，删除。
- `tests/test_stream_recorder.py`：`test_正在跑的那条流找得到_跑完就不在了`（`live()` 消失）删除，由
  边界测试 1、3 取代；库形状类（一条完整消息、只有一次 INSERT、收到 done 时已在库里）迁到
  `test_turn_state.py`；其余（保住残文、一个字都没有、后来的订阅者、工作线程的字不漏）改用一个捕获
  `Outcome` 的 `persist` 假件，**不再需要数据库**。
- `tests/test_unit_of_work.py`：`Attached` 相关四条删除（含 parity 测试）；`Detached` 的五条保留。
- 保留不动：`test_chat.py:324` 重连测试、`test_compare.py:244` 三路回放、`test_retrieval.py:616`
  「留档写不进去整轮 error」——它们测的是行为，接口变了行为不变。

### 测试环境需求

无新依赖。并发测试需要第二条 `AsyncSession`（asyncpg 不允许一条 session 并发使用），从同一个
`get_session_factory()` 开即可。

## Implementation Recommendations

**`turn_state` 应该拥有**：一个会话「此刻能不能开一轮」的全部判断（单飞、重连、409）；一轮的轮号与
全部 seq；锁定的读、推导、写、清；本轮告示；一轮收尾的那一个事务；后台任务的启动与强引用。

**应该隐藏**：席位登记及其原子性；行锁与发号；身份映射的绕行；死章节 id 的处理；告示怎么保证送达；
事务边界；哪些活在工作线程。

**应该暴露**：`admit(session, draft)` 与 `forget_locks(session, book_id=)`，以及 `Draft` / `Notice`
两个值对象。别的一概不公开——⑥屏与会话详情的只读路径继续走 `RetrievalRepository`。

**调用方如何迁移**：

- `ChatService.ask` 只算 `Draft`，最后一句 `return await turn_state.admit(...)`；不再读锁定、不再发号、
  不再写提问、不再碰 `StreamRecorder`。
- `resettle.apply` 改调 `turn_state.forget_locks`；`ConversationRepository.clear_locks_for_book` 只剩这
  一个调用方。
- `TurnRunner` 只由 `turn_state` 启动；`StreamRecorder` 只由 `turn_state` 打开。
- **SSE 契约新增 `notice {code, text}`**（可加的：`useSSE.dispatch` 跳过不认识的事件，后端可以先上）。
  前端 `useAsk` 增加 `notices`：每次提问清空、收到 token / done **不清**；`ChatView` 在提问区之外给一条
  横幅。告示不落库，刷新即消失——它说的是「这一轮」，刷新之后已经不是这一轮了。

**建议的迁移顺序**（供 spec-to-plan 切片参考，不强制）：先把 seq 收成持行锁的一个发放者（独立可验，
直接兑现既有规范）→ 建 `turn_state.admit` 接管单飞与写提问（边界测试 1–4、8、9）→ 把收尾事务迁成
`persist` 回调（5、7）→ 锁定的读推导收进来、`forget_locks`（6 的判据部分）→ `notice` 事件与前端横幅
（6 的送达部分）→ 删除 `UnitOfWork` / `Attached` 与被取代的旧测试。

**规范章节受影响的三处**（`design-doc-sync` 不改规范章节；实施时由 `tdd` 列为规范候选、经用户裁定）：

- `backend.md`「接口与响应」列的 SSE 事件名缺 `notice`。
- `backend.md`「并发与后台任务」seq 那条的理由写着「`StreamRecorder.live()` 就是钉这个的」——实施后
  `live()` 不存在，规范本身仍成立，理由要换成「`turn_state` 的席位」。
- `backend.md`「设计模式与约定」端口 parity 那条的实测例子引用 `Attached`——删除后该条作为通则仍成立，
  例子过期。

**架构章节需同步的**（实施收尾时 `design-doc-sync` 做）：`backend.md`「模块边界」新增 `turn_state` 行、
改 `turn` / `db/unit_of_work` / `conversation_repo` 三行、给 `stream_recorder` 补一行；「核心业务流程·提问」
与 SSE 事件说明；待确认 #6 关闭、#2 里 `_LIVE` 的表述改为席位；`frontend.md`「API 调用层」SSE 事件表
加 `notice`。

## 不在本 RFC 范围内

以下摩擦在同一轮审视中确认存在，但与轮次状态无关，留给后续：

- 大脑解析两套（引擎 `config.resolve()` 与 `brain_service`），`booksoul.toml` 的 `max_tokens` /
  `temperature` 在网页那条路上被静默丢弃；测试读开发者真实的 `~/.booksoul/config.toml`。
- 「装得下吗」两把尺子：⑧屏与书籍详情比 `word_count`，提问时比 `len(content)`（《悉达多》48,354 vs
  59,014，差 22%）。`Turn.fits_whole` 也用前者，本 RFC 不动它。
- 重解析善后的调用顺序靠注释维持（`test_reparse.py:163` 抄了一份顺序）；**重解析时拒绝正在跑轮次的
  书**这一侧（本 RFC 只做了「解析中不准开新一轮」与「落库时死 id 置空」两侧）。
- 前端：人格标签不生效、本机模型在对话页问不出去、流式请求不续期 token、登录失效回不到登录页——
  四个独立缺陷，可以不等 RFC 直接走 `edit code`。
- 解析账本 `cleanup_stats` 以中文键为契约、审核台账写读两半分离、书在磁盘上的位置拼了六处。
