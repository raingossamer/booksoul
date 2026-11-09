---
status: implemented
---

# TurnRunner 与 Detached —— Architecture RFC

> 产出于 2026-09-21 的 `improve-codebase-architecture`。审视基线：S1–S13 全部完成、
> `design-doc-sync` 已收尾之后的代码库（后端 7583 行 / 前端 8221 行 / 测试 292 条）。
> 本 RFC 只描述提案，不改代码。实施须走 RFC 快车道（显式 `rfc to plan`）。

## Problem

### 一、「一轮提问」没有模块，只有一个 933 行的文件和一份手工打包的快照

`services/chat_service.py` 实际是两半，而这个分界在 Design 里一个字都没有：

- **请求期**：`ChatService` 类，手里有 session，能抛规规矩矩的 4xx。
- **后台期**：17 个模块级函数，手里没有 session，靠一个 `_Turn` dataclass 搬运一切。

`_Turn` 有 **18 个字段**，字段来源横跨四个子系统：`cfg` 来自 `brain_service.config_for`，
`chapters`/`files` 来自 `book_repo.list_chapters`，`budget_chars` 来自引擎
`context_budget_chars`，`history` 来自 `_history_for`，`book_id`/`brain_name` 只为
审核台账而存在。17 个模块级函数里 **15 个只有一个调用点** —— 这不是一组可复用模块，
是一条被切碎的单体流程。

`_Turn` 与 `selector.Choice` 在七个函数签名里**成对出现、从不分开**
（`_one` / `_must_have_content` / `_record_moderation` / `_record_passed` / `_record` /
`_read_sources` / `_build_engine_sessions`），事实上它们是同一个参数。

一处具体的化石：`_Turn.files: dict[int, str]` 存在的唯一理由是 `selector.ChapterIn`
少一列 `file_path`，于是 `_read_sources` 必须把两个结构当场缝成 `sources.Ref`。

### 二、一次提问的写入劈在五条连接、五次 commit 上 —— 与 Design 明文冲突

`backend.md`「核心业务流程·提问」写着：**「一轮结束在一个事务里落库 `messages` /
`retrievals` / `retrieval_items` / `citations`，然后才发 `done`」**。代码不是：

| 写什么 | session 来源 | commit |
|---|---|---|
| user message + 会话标题 | 请求 session | `chat_service.py:408` |
| retrievals + retrieval_items + 会话锁定 | 自建 A | `chat_service.py:812` |
| 审核台账（blocked） | 自建 B | `chat_service.py:714` |
| 审核台账（passed） | 自建 C | `chat_service.py:743` |
| assistant message + citations | 自建 D | `stream_recorder.py:377` |

五次提交之间没有任何原子性，而它们共同维护着一组互相依赖的不变量：
「有回答就该有留档」「有留档就该有锁定」。

**已经在代码里显形的后果**：进程在 `_record` 之前死掉，库里留下「有提问、有回答、
没有锁定」。下一轮 `ask` 走到 `chat_service.py:392` 时 `reparsed = turn > 1 and not locked`
为真 —— 给用户播一句「这本书重新解析过了」，**一件从未发生过的事**，同时全价重跑一次
BM25。`_locked_chapter_ids` 那 18 行注释正是在描述这个分裂的另一面：它花整段篇幅解释
为什么不能相信手上那个 ORM 对象。

**连接数**：一次对读（三路）峰值占用 1(请求) + 1(`_record`) + 3(`_record_passed`) +
1(`_persist`) = 最多 6 条连接，而 `db/session.py:28` 的 `create_async_engine` 没有配
`pool_size`。

### 三、「后台要自己开一条 session」这个动作抄了七遍，每遍各自决定错误策略

`async with get_session_factory()() as session: ... await session.commit()` 出现在
`chat_service.py:707/733/774`、`parse_service.py:148/331/361`、`stream_recorder.py:335`。

其中 `_record_moderation`（`:706-716`）与 `_record_passed`（`:732-745`）除 repo 调用外
**逐字一致**，连 except 块的日志字符串都一模一样。

七处各自决定了四件事：session 从哪来、什么时候提交、异常吞还是抛、日志怎么写。
两处吞（审核台账，有明确理由）、一处不吞（`_record`）、一处根本没有 except
（`_pump_progress`）。这个策略差异是**刻意的**，但它今天表达为「各写各的」，
而不是一个可以被读到的参数。

另有两种写法并存且无注释解释：`parse_service` 的三处用函数内延迟 import，
`chat_service` 与 `stream_recorder` 用顶部 import。

### 四、这使得测试只能在错误的高度进行

最有说服力的一条证据是 `tests/test_retrieval.py:294`：为了测 `_record`
（「重问同一轮复用那一行留档，不另起一个 turn」），测试必须**手工构造一个 18 字段的
私有 `_Turn`**，显式填了 15 个字段 —— 而 `_record` 只用到其中 **2 个**
（`conversation_id` 和 `turn`）。`storage_path=""`、`chapters=[]`、`cfg=None`、
`history=[]`、`files={}` 全是为了让构造函数闭合而填的空壳。

**接口复杂度已经超过实现复杂度**，这是 Ousterhout 浅模块的定义本身。

另外两处也在够进私有：`test_reparse.py:325` import `_must_have_content`，
`test_compare.py:30` import `_history_for`。

后台写入至今没有一条测试能**同步**断言 —— 它们发生在另一条 session 上，
测试只能绕到库里重新查一遍，或者等。

### 五、导航成本

理解「这一轮选了哪几节」的当下状态要跳七个文件：`models/conversation.py`（列）→
`chat_service._locked_chapter_ids`（必须绕开 identity map）→ `chapter_selector.choose`
（四条分叉）→ `Choice.records`（决定留不留档）→ `chat_service._record`（**另一条
session** 写回）→ `retrieval_repo.save`（upsert）→ `conversation_repo.clear_locks_for_book`
（重解析时清）。三个组件各持有这个状态的一部分真相，分别在三条 session 上读写。

## Proposed Interface

分两期。**第一期不改任何可见行为**，可独立验证后再进第二期。

### 第一期：`Detached` —— 给「脱离请求的工作单元」一个名字和唯一一份实现

```python
# db/unit_of_work.py

class UnitOfWork(Protocol):
    async def __aenter__(self) -> AsyncSession: ...
    async def __aexit__(self, *exc) -> bool: ...


class Detached:
    """一条不跟着请求走的工作单元。

    进出各一次；正常退出提交，异常退出回滚。异常之后怎么办由 `on_error` 说了算，
    不再由每个调用点各写一遍 try。
    """

    def __init__(
        self,
        *,
        on_error: Literal["raise", "log"] = "raise",
        label: str = "",
    ) -> None: ...
```

调用方：

```python
# 失败必须让调用方知道
async with Detached(label=f"选章留档 会话{turn.conversation_id}") as s:
    await RetrievalRepository(s).save(...)
    await s.execute(update(Conversation)...)

# 失败只记日志 —— 台账记不上是遗憾，把一条已经说完的回答变成错误不是
async with Detached(on_error="log", label="审核台账") as s:
    await ModerationRepository(s).record(...)
```

**内部隐藏**：session 的取得、提交与回滚时机、两种错误策略、日志格式，
以及将来要加的连接数上限与优雅停机时的等待。

### 第二期：`Turn` 与 `TurnRunner` —— 让「一轮提问」成为一个模块

```python
# services/turn.py

@dataclass(frozen=True)
class BookRef:
    """这一轮要读的那本书在磁盘与库里的位置。"""
    id: int
    storage_path: str
    word_count: int
    chapters: list[ChapterIn]      # 含 file_path —— 见「实施建议」第 2 条
    

@dataclass(frozen=True)
class Budget:
    """这一轮能塞多少、锁了哪几节、要不要重挑。"""
    chars: int
    locked: list[int]
    reselect: bool
    reuse: Choice | None           # 锁定复用时请求期已经算完了

    @property
    def fits_whole(self) -> bool: ...


@dataclass(frozen=True)
class BrainRef:
    """用哪个大脑。`cfg.api_key` 已经是 repr=False 的。"""
    name: str
    cfg: BrainConfig


@dataclass(frozen=True)
class Turn:
    """一轮提问的全部输入。请求期算好，此后只读。

    刻意全是普通值：后台任务活得比响应长，带着一个随响应关掉的 session 过去，
    是一个只在慢请求上才现形的 bug。
    """
    conversation_id: int
    turn: int
    personas: list[str]
    question: str
    book: BookRef
    budget: Budget
    brain: BrainRef
    reflection: bool
    history: list[dict]
    reparsed: bool = False


class TurnRunner:
    @staticmethod
    async def launch(turn: Turn) -> AsyncIterator[tuple[str, dict]]:
        """唯一入口。挂起一个不跟着响应走的后台任务，返回可订阅的事件流。

        重连（同一会话的同一轮已经在跑）时返回既有流，**绝不二次调模型**。
        """
```

`ChatService.ask()` 的后半段塌成：

```python
        turn = Turn(...)                         # 请求期算好，user message 已落库
        return await TurnRunner.launch(turn)
```

**内部隐藏**：选章与读盘、`_must_have_content` 那道闸、引擎 Session 的装配、
一到三路的并发与各自的错误边界、错误翻译（`BrainError.kind` → 网页版出路）、
审核台账的两条规则，**以及全部落库**。

### 落库的终局形状：两个事务，不是一个

| 事务 | 写什么 | 工作单元 |
|---|---|---|
| 主事务 | `messages` + `citations` + `retrievals` + `retrieval_items` + 会话锁定 | `Detached()`（失败要抛） |
| 台账 | `moderation_events` | `Detached(on_error="log")` |

**审核台账刻意不并进主事务。** 它今天故意吞异常，理由写在代码里：「台账记不上是一件
遗憾，把一条已经说完的回答变成错误不是」。塞进主事务会让这条纪律失效 —— 一次台账写
失败就会回滚掉整轮的回答。

主事务合并之后，`backend.md` 已经写下的那句「在一个事务里落库 `messages` /
`retrievals` / `retrieval_items` / `citations`」第一次成为事实。

### 已接受的代价

**⑥屏的选章明细与审核台账，在这一轮结束前查不到。** 今天它们在流一开始就落库；
合并之后要等轨全部结算。对读跑 40 秒，这 40 秒里点进⑥屏会看到上一轮的数据。

接受的理由：`GET /api/conversations/{id}/retrieval` 的端点语义本来就是「这个会话
**最近一次真正做过的**选章决策」，延后到轮末与这个语义一致，不制造新的说法。

## Dependency Strategy

| 依赖 | 类别 | 策略 |
|---|---|---|
| `chapter_selector` / `citations` / `sources` / `relocate` | **In-process** | 保持纯函数、零依赖。`TurnRunner` 直接调，不包一层 |
| PostgreSQL | **Local-substitutable** | 已有真测试库 + alembic 迁移（`conftest.migrated_db`）。不引入 SQLite 之类的第二种方言 —— 迁移里有 JSONB、窗口函数、`ON CONFLICT ON CONSTRAINT`，换方言等于测另一套东西 |
| 后台 session 的取得 | **Ports & adapters** | 端口 = `UnitOfWork` Protocol；生产适配器 = `Detached`（真 session factory）；**测试适配器 = 复用测试 session、退出时 flush 不 commit** —— 后台写入第一次可以被同步断言 |
| 模型后端 | **True external** | 维持现状：挡在引擎 `brain.create` 之后，测试在**引擎那一层**换替身（`conftest.fake_brain`）。不在服务层另开测试分支 —— 服务层要的正是「和 CLI 走同一条路」 |
| `library/` 下的章节文件 | Local-substitutable | `tmp_path` + `BOOKSOUL_LIBRARY_ROOT` |

## Testing Strategy

### 需编写的边界测试

**在 `Detached` 边界**（新，不需要 HTTP、不需要后台任务）：

- 正常退出提交；异常退出回滚，库里不留半截
- `on_error="raise"` 把异常抛给调用方；`on_error="log"` 吞掉并留一条带 `label` 的日志
- 同一个 `Detached` 不可重入

**在 `TurnRunner.launch` 边界**（替换今天够进私有的那三条）：

- 一轮跑完，主事务里 `messages` / `citations` / `retrievals` / `retrieval_items` /
  会话锁定**同时存在或同时不存在** —— 这是今天完全没有覆盖的那个状态
- 落库之前崩掉：不留孤立的 `retrievals`（今天会留，并导致下一轮误报「重新解析过了」）
- 复用锁定的轮次不写 `retrievals`（`Choice.records` 的语义）
- 重连挂回同一条流，模型调用次数仍为 1（Spec #39）
- 一路失败、另两路读完（对读的错误边界在轨上）
- 零章节载入时就地停住，不往模型送
- 审核台账写失败**不**影响回答落库（`on_error="log"` 的纪律）

### 需删除的旧测试

| 位置 | 为什么变冗余 |
|---|---|
| `tests/test_retrieval.py:289-340` 手搭 18 字段 `_Turn` 测 `_record` | `_record` 不再是可单独调用的私有函数；同一条断言改在 `TurnRunner` 边界做，且不用填 13 个空壳字段 |
| `tests/test_reparse.py:325` import `_must_have_content` | 同上，改为在边界断言「零载入的那一轮不调模型」 |
| `tests/test_compare.py:30` import `_history_for` | 历史拼装是 `Turn` 构造的一部分，在 `ChatService.ask` 边界断言 |

`tests/test_chat.py` / `test_moderation.py` / `test_citations_api.py` / `test_personas.py`
现有的 `ChatService` 级断言**全部保留** —— 它们本来就在正确的高度，是这次重构的回归网。

### 测试环境需求

- 真 PostgreSQL（现状，Docker 容器 + `conftest` 已备）
- 新增：`UnitOfWork` 的测试适配器，让后台写入可同步断言，去掉测试里对「等后台任务」的依赖
- 引擎替身维持现状（`conftest.fake_brain` 打在 `booksoul.brain.create` 上）

## Implementation Recommendations

不绑定具体文件路径的持久指导：

1. **顺序是 Detached 先、TurnRunner 后。** 第一期纯收敛、不改时序、不改任何可见行为，
   可以独立跑一遍全量测试验证「什么都没变」。第二期才动时序。反过来做，第二期会把
   session 收成一条，第一期就没剩多少可做了。

2. **补齐 `ChapterIn.file_path`，删掉那张 `idx → file_path` 的旁表。** 它是 `_Turn`
   多带一个字段的唯一原因，也是 `_read_sources` 必须当场缝合两个结构的唯一原因。
   补上之后 `sources.Ref` 是否还需要独立存在，实施时再判断。

3. **「一轮提问」这个模块应当拥有**：选章的触发（不是算法）、读盘、引擎 Session 的
   装配、并发与错误边界、错误翻译、落库。**应当隐藏**：session 从哪来、几个事务、
   哪些活在工作线程里、重连怎么挂回。**应当暴露**：一个 `launch`，以及那个只读的
   `Turn`。

4. **不要把 `Turn` 变成可变的上下文袋子。** 审视时比较过「把一轮做成一串有名字的
   阶段」的方案，结论是它会把类型化的字段表换成阶段之间往 ctx 上挂属性 ——
   读起来更干净，改起来更危险。`Turn` 必须保持 `frozen=True`。

5. **三个值对象（`BookRef` / `Budget` / `BrainRef`）的分组依据是「谁算出来的」**，
   不是「字段看起来像一类」。这样新增需求时能一眼看出该往哪个对象加，
   以及要不要改 `ask` 的准备段。

6. **十条约束（C1–C10）在实施期必须逐条有测试或注释兜住**，尤其是：
   `ask` 必须保持普通协程而非异步生成器（否则 4xx 变成断掉的 200 流）；
   引擎 Session 的拼装顺序一行都不许在 Web 层重写（缓存断点直接决定账单）；
   user message 必须留在请求 session 里、在调模型之前落库。

7. **顺手清掉审视中确认的死代码**（都已实测零调用，与本 RFC 同一片区域）：
   `brain_service._config` + `_NEED_KEY`（13 行，且与活代码存着同一句文案的两份）、
   `StreamRecorder.text_of`、`models/parse.py` 与 `importer.py` 两个逐字相同且都无人
   使用的 `STAGES`、`Choice.shortfall`（只有测试用，生产走 `chat_service._shortfall`
   的等价实现 —— 改了前者测试会绿而用户看到的还是后者，二选一留一份）。

8. **一并修掉漂移检测的盲区**：`models/__init__.py` 漏了 `ModerationEvent`，
   `Base.metadata` 只挂了 11 张表，而库里有 12 张 —— `moderation_events` 不在
   `test_schema_drift` 的覆盖范围内，而它正是上一片刚加的那张。

## 不在本 RFC 范围内

以下摩擦在本轮审视中被确认，但与「一轮提问」不是同一块，留给后续轮次：

- **前端 SSE 通道缺 401 续期**（`composables/useSSE.ts` 复制了 `api/client.ts` 的
  headers 与错误转换，唯独没复制 401 → refresh → 重放 → 跳登录）。access token 30 分钟，
  开着对话页想半小时再提问必然撞上。前端目前**零测试**。
- **前端「一个概念多份词典」**：人格显示名 3 份且不一致、`GLYPH.author` 硬编码为
  「黑」（黑塞的姓，换任何别的书都还是它）、errorCode 词典 2 份、`.banner` 样式 9 份、
  错误兜底文案 19 处、3625 行 scoped CSS 对 149 行共享样式。
- **浅 repository 与手写查询并存**：`user_repo` / `brain_repo` 全部方法透传且各自单
  service 专属，同时 `chat_service` 绕过这一层手写了 5 处查询 —— `user_id` 不变式在
  前者是机制、在后者是手打的。
- **`routers/jobs.py:82-127`** 越过 service 直接 new repository、自开 session、
  自己 `rollback()` 刷 identity map。
- **跨层字段手工抄写 6 处约 100 行**，ORM ↔ Pydantic ↔ TS 三段均无对账。
- **`format_service.rejection_reason` 未接线**：上传路径只校验体积不校验格式，
  没装 pymupdf 的部署传 PDF 会建出一本 failed 的书加一个永久孤儿文件。
- **`resettle` 的调用顺序约束**（`snapshot` 必须早于 `replace_chapters`）写在三个文件的
  注释里，代码没有任何强制 —— 调换两行不报错，只让重定位命中率变成假的 100%。
