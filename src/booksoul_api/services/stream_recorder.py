"""一轮回答的流：累积、广播，无论怎么结束都把说出口的交给落库那一方。

这个模块存在的理由是一句很具体的话：**用户花的钱不能白花**。

回答生成到一半断了——网络断、服务重启、模型那头报错——已经吐出来的那些字是
已经计过费的。把它们丢掉，等于让用户为一段他从没看到的文字付钱，而且他连
「刚才说到哪了」都不知道。

一轮可以有**不止一路**：对读同时跑三种人格（S10）。所以这里的单位是「一轮」
而不是「一条回答」，里面装着一到三条**轨**。单人格只是 N=1 的那个情形，
走的是同一段代码——两套代码迟早会在断流那条路上分叉，而那条路本来就少有人走。

对外只有一个 `async with`，里面藏着五件不那么显然的事：

**一、退出路径只有一条。** 正常退出落 `complete=true`，异常退出落
`complete=false`，两条路都走同一段落库代码。引擎里 `Session.ask()` 是
`history.append` 在 `chat()` 返回之后——异常即丢，那个形状在这里不能照搬。

**二、它根本不碰数据库。** 一次回答几十秒，一边流一边 UPDATE 就是几百次写库，
还得整段时间占着一个连接。这里只动内存，退出时把这一轮的结局（`Outcome`）交给
`persist`——落库在 `services/turn_state`，一个事务写完。它炸了，整轮报 `error`。

**三、跨线程交接要收尾。** 模型调用跑在工作线程里（对读是三个线程），
`sink()` 是从那边被调的。最容易丢的是最后几个 chunk：事件循环还没来得及收，
上下文就退出了。所以落库前 `_drain()` 再收一次。

**四、一路出错不拖垮另外两路。** 每条轨自己结算：谁说完了谁就发一个
`answered`，谁炸了谁发一个 `failed`，都带着自己的 persona。对读时一列失败、
另外两列照常读完，比三列一起变白好。

**这四个事件名分成两层，不是啰嗦：** `answered` / `failed` 是**某一路**的结局，
当场就发，界面据此把那一列的转圈收掉；`done` / `error` 是**整轮**的结局，
落库之后才发。合成一个的话，「收到 done 时消息已经在库里」这条保证就没了——
而客户端收到 done 的第一反应正是回头去拉一次会话。

**五、同一轮可以被听第二次。** 断线重连时前端重新订阅，`listen()` 先把每条轨
已经累积的补上、再跟着往下——而不是让服务端把同一个问题再问模型一遍。
同一个问题付两次钱是不能接受的（Spec #39）。「这一轮正在跑、该挂上去」由
`turn_state` 的席位判断，不归这里。
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from types import TracebackType

from booksoul.brain import Reply
from booksoul_api.services.chapter_selector import Choice
from booksoul_api.services.citations import Source

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Spoken:
    """说出口的一路。`error` 非空就是断在半路的残文——字照样要落，只是标成残缺。"""

    persona: str
    text: str
    error: str
    reply: Reply


@dataclass(frozen=True)
class Outcome:
    """一轮的结局，交给 `persist` 落库。由 recorder 算，所以定义在这里。"""

    conversation_id: int
    turn: int
    # 这一轮的选章决策。复用锁定的轮次也有，只是 `records` 为假
    choice: Choice | None
    spoken: list[Spoken]
    # None = 我们根本没拿到书；[] = 书在手上、一节都没载入。见 `trace()`
    sources: list[Source] | None


Persist = Callable[[Outcome], Awaitable[None]]


@dataclass
class _Track:
    """一路人格的回答。对读有三条，单人格有一条。"""

    persona: str
    chunks: list[str] = field(default_factory=list)
    reply: Reply = field(default_factory=Reply)
    error: str = ""
    # 这一路已经结算过了（`done` 或 `error` 已经发出去）。
    # 每路各自结算，一列失败不该让另外两列跟着变白。
    settled: bool = False

    @property
    def text(self) -> str:
        return "".join(self.chunks)

    def settle_event(self) -> tuple[str, dict]:
        if self.error:
            return ("failed", {"persona": self.persona, "message": self.error})
        return ("answered", {"persona": self.persona})


class StreamRecorder:
    """用 `StreamRecorder.open(...)` 拿，不要直接构造。"""

    def __init__(
        self, *, conversation_id: int, turn: int, personas: list[str], persist: Persist
    ) -> None:
        self.conversation_id = conversation_id
        self.turn = turn
        self.personas = list(personas)
        self._persist = persist

        self._tracks = {p: _Track(persona=p) for p in self.personas}
        # 工作线程投进来、还没收的 (人格, chunk)
        self._pending: deque[tuple[str, str]] = deque()
        self._subscribers: list[asyncio.Queue] = []
        self._stage = ""
        # 本轮告示。和 stage 不同，一条都不许被后来的盖掉。见 notice()
        self._notices: list[dict] = []
        # None = 还没拿到书；空列表 = 书在手上但一节都没载入。见 trace()
        self._sources: list[Source] | None = None
        # 这一轮的选章决策。落库时和回答写在**同一个事务**里。见 record()
        self._choice: Choice | None = None
        self._closed = False
        # 落库炸了。整轮的结局要据此报 error 而不是 done —— 见 _finale()
        self._persist_error = ""
        self._loop = asyncio.get_running_loop()

    # ------------------------------------------------------------------ 生命周期

    @classmethod
    def open(
        cls, *, conversation_id: int, turn: int, personas: list[str], persist: Persist
    ) -> StreamRecorder:
        return cls(
            conversation_id=conversation_id,
            turn=turn,
            personas=personas,
            persist=persist,
        )

    async def __aenter__(self) -> StreamRecorder:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        self._drain()  # 工作线程最后投进来那几个 chunk

        if exc is not None:
            # 整个任务炸了：没结算过的那几路都算失败。已经自己结算过的不动——
            # 它们说完了，钱花了，内容是完整的。
            for track in self._tracks.values():
                if not track.settled and not track.error:
                    track.error = str(exc)

        # **先落库，再宣布结束。** 反过来的话，客户端收到 done 的那一刻这条回答
        # 还没进库；它转头去重新拉会话，看到的是一个没有回答的问题——一条它刚刚
        # 亲眼看着流完的回答。
        try:
            await self._persist(self._outcome())
        except Exception:
            # 落库失败不该把原来的异常盖掉——那个才是用户真正撞上的问题。
            # 但**必须告诉客户端**：宣布 done 而库里什么都没有，它会立刻回头拉
            # 一次，看到的是一个不存在的空洞——一条它刚刚亲眼看着流完的回答。
            self._persist_error = (
                "这一轮没能存下来。上面显示的内容不会保留，请再问一次。"
            )
            logger.exception(
                "回答没能落库（会话 %s 第 %s 轮），已生成的 %d 字丢失",
                self.conversation_id,
                self.turn,
                sum(len(t.text) for t in self._tracks.values()),
            )
        finally:
            self.close()
        return False  # 从不吞异常

    # ------------------------------------------------------------------ 写入

    def sink(self, persona: str, chunk: str) -> None:
        """模型吐出来的一段。**这个方法是从工作线程调的**（对读是三个线程）。

        所以它只做两件线程安全的事：往 deque 里丢，以及请事件循环来收。
        真正的累积与广播都发生在 `_drain()` 里，那是在事件循环上跑的。
        """
        if not chunk:
            return
        self._pending.append((persona, chunk))
        try:
            self._loop.call_soon_threadsafe(self._drain)
        except RuntimeError:
            # 事件循环已经关了。chunk 还在 deque 里，落库前那次 _drain 会收走。
            pass

    def record(self, choice: Choice) -> None:
        """这一轮的选章决策。**不当场写**，留到落库时和回答一起写。

        `backend.md`「核心业务流程·提问」承诺的是「一轮结束在一个事务里落库
        `messages` / `retrievals` / `retrieval_items` / `citations`」。当场写就
        做不到：留档在调模型**之前**落，回答在之后落，中间崩掉会留下一行孤立的
        `retrievals` —— 一份「凭什么是这几节」的解释，配不上任何一条回答。

        代价是⑥屏的选章明细要等这一轮结束才查得到（RFC 已裁定接受）：那个端点的
        语义本来就是「这个会话**最近一次真正做过的**选章决策」。
        """
        self._choice = choice

    def trace(self, sources: list[Source]) -> None:
        """这一轮模型手里是这几节。落库时拿它们比对回答里的引文（S9 溯源）。

        **不给它就一条引文都不记**，这是刻意的：`None` 是「我们根本没拿到书」
        （选章那步就出错了），空列表才是「书在手上、但这句话不在里面」。
        两者对用户是完全不同的两句话，混成一个的话，一次载入失败会把整条回答
        里的每个块引用都标成「原文里没找到」——在最该怀疑系统的时候去怀疑模型。
        """
        self._sources = sources

    def stage(self, text: str) -> None:
        """现在在做什么。第一个字还没出来之前，屏幕上不该是空的。

        选章要切几百万字的词，这中间有好几秒没有任何 token。不说一声，用户
        看到的就是一个卡住的界面，而正确的反应（等一下）和错误的反应（刷新、
        再问一遍）在屏幕上长得一模一样——后者还要再花一次钱。

        它是**整轮共享**的：挑章节这件事三路一起用一个结果，不是各挑各的。
        """
        self._stage = text
        for queue in self._subscribers:
            queue.put_nowait(("stage", {"text": text}))

    def notice(self, code: str, text: str) -> None:
        """这一轮要让用户知道的一件事，比如「这本书重新解析过了，这次按全价算」。

        和 `stage` 分开，是因为它们回答的问题不同：`stage` 是「现在在做什么」，
        只有一个位置，下一句就把上一句盖掉；告示是「这一轮有什么不一样」，
        一句都不能丢。它原先走 `stage`，紧随其后的「正在挑章节…」当场就把它
        盖了——恰好在真该说的时候（真重解析、真重挑、真花钱）看不见。

        所以存成列表，每个订阅者（早到的、晚到的）都经回放拿到全部。
        `code` 是写死的字面量，给前端分支用；`text` 给人看。
        """
        payload = {"code": code, "text": text}
        self._notices.append(payload)
        for queue in self._subscribers:
            queue.put_nowait(("notice", payload))

    def finish(self, persona: str, reply: Reply) -> None:
        """这一路正常说完了。记下账单那几个数字，并且当场结算。"""
        track = self._tracks[persona]
        track.reply = reply
        self._settle(track)

    def fail(self, persona: str, message: str) -> None:
        """这一路报错了。已经流出来的字照样保住，只是这条会标成残缺。"""
        track = self._tracks[persona]
        track.error = message
        self._settle(track)

    def _settle(self, track: _Track) -> None:
        """把这一路的结局广播出去。**先收尾再收工。**

        `sink()` 走的是 `call_soon_threadsafe`，排在下一轮循环才跑；不先
        `_drain()` 就宣布结束，最后投进来的那几个 chunk 会被结束信号越过去——
        订阅者看到 done 就把那一列收了，那几个字在屏幕上永远不会出现。
        """
        if track.settled:
            return
        self._drain()
        track.settled = True
        event = track.settle_event()
        for queue in self._subscribers:
            queue.put_nowait(event)

    def close(self) -> None:
        """整轮收工。**调用它之前内容必须已经落库。**

        客户端收到 `done` 的第一反应是回头拉一次会话；这一步排在落库之后，
        它才不会看到一个没有回答的问题——一条它刚刚亲眼看着流完的回答。
        """
        if self._closed:
            return
        self._drain()
        for track in self._tracks.values():
            self._settle(track)
        self._closed = True
        event = self._finale()
        for queue in self._subscribers:
            queue.put_nowait(event)
            queue.put_nowait(None)

    def _finale(self) -> tuple[str, dict]:
        """整轮的结局。有任何一路没说完，这一轮就不算说完了。

        落库炸了排在最前面：那一路说没说完已经不重要了，内容没存下来。
        每一路自己的 `failed` 早就带着各自那句出路发出去过了，这里是整轮的信号。
        """
        if self._persist_error:
            return ("error", {"message": self._persist_error})
        broken = [t for t in self._tracks.values() if t.error]
        if broken:
            return ("error", {"message": broken[0].error})
        return ("done", {})

    def _drain(self) -> None:
        """把工作线程投进来的收进各自的轨并广播出去。只在事件循环上跑。"""
        while self._pending:
            persona, chunk = self._pending.popleft()
            track = self._tracks.get(persona)
            if track is None:  # 不该发生；真发生了也不能把字丢了
                logger.warning("收到未登记人格 %s 的输出，已丢弃", persona)
                continue
            track.chunks.append(chunk)
            for queue in self._subscribers:
                queue.put_nowait(("token", {"persona": persona, "text": chunk}))

    # ------------------------------------------------------------------ 读出

    async def listen(self) -> AsyncIterator[tuple[str, dict]]:
        """订阅这一轮：先补上每条轨已经说过的，再跟着往下。

        挂队列和取回放快照必须在**同一个同步块**里完成，中间不能有 await——
        否则这中间到达的 chunk 会既在快照里、又在队列里，用户看到一段重复的话。
        """
        queue: asyncio.Queue = asyncio.Queue()
        self._drain()
        self._subscribers.append(queue)
        replay = [
            (t.persona, t.text, t.settled, t.settle_event())
            for t in self._tracks.values()
        ]
        notices = list(self._notices)
        stage = self._stage
        finished = self._closed
        finale = self._finale() if finished else None
        said = any(text for _, text, _, _ in replay)

        try:
            # 告示一条不落地先补上，排在一切之前：它说的是「这一轮有什么不一样」，
            # 晚到的订阅者同样需要知道
            for payload in notices:
                yield ("notice", payload)
            # 还没吐出第一个字就来重连（选章要好几秒，这很常见）：先把
            # 「现在在做什么」补上，否则重连回来的人对着一片空白，不知道
            # 是在等还是已经坏了。已经有字了就不必——字本身就说明它在跑。
            if stage and not said and not finished:
                yield ("stage", {"text": stage})
            for persona, text, settled, settle_event in replay:
                if text:
                    yield ("token", {"persona": persona, "text": text})
                if settled:
                    yield settle_event
            if finale is not None:
                # 已经收工了才来订阅：回放完就给结局，不然订阅者会一直等下去
                yield finale
                return
            while True:
                item = await queue.get()
                if item is None:
                    return
                yield item
        finally:
            if queue in self._subscribers:
                self._subscribers.remove(queue)

    # ------------------------------------------------------------------ 交出去

    def _outcome(self) -> Outcome:
        """这一轮的结局：落库需要的全部东西，不多不少。

        只交**说出口的**那几路。一个字都没有的那一路不算——空消息比没有消息更糟：
        界面上它看起来像「模型答了个空」，而真实情况是「还没开口就断了」。
        """
        return Outcome(
            conversation_id=self.conversation_id,
            turn=self.turn,
            choice=self._choice,
            spoken=[
                Spoken(persona=t.persona, text=t.text, error=t.error, reply=t.reply)
                for t in self._tracks.values()
                if t.text
            ],
            sources=self._sources,
        )
