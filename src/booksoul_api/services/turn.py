"""一轮提问 —— 跑完它需要的那些只读的值。

一个 `Turn` 分两半算：`ChatService.ask()` 在请求期算出 `Draft`，
`turn_state.admit()` 占住这个会话之后再算出 `Standing`，合起来交给后台任务照着执行。
**刻意全是普通值**：没有 ORM 对象、没有 session。后台任务活得比这次响应长，
带一个随响应关掉的 session 过去，是一个只在慢请求上才现形的 bug。

分组依据是**「谁算出来的」**，不是「字段看起来像一类」：

| 对象 | 谁算的 |
|---|---|
| `BookRef` | 读书与读章节那一步 |
| `Budget` | 预算换算那一步 |
| `BrainRef` | 大脑配置组装那一步 |
| `Draft` 本体 | 这一轮请求自身（问什么、跑哪几路、主干是谁）—— `ChatService.ask` |
| `Standing` | 占住会话之后才读得对的：第几轮、历史、锁定、告示 —— `turn_state.admit` |

按来源分而不是按「看起来像」分，是为了让**下一个新增的字段有地方可放**：
问「它是谁算出来的」有唯一答案，问「它像哪一类」每个人的答案都不一样，
于是那张表会慢慢退化回一堆扁平字段——它本来就是那样开始的。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, fields, replace

from booksoul import persona as persona_mod
from booksoul.ask import Session as EngineSession
from booksoul.brain import BrainError
from booksoul.config import BrainConfig
from booksoul.library import load_from_path
from booksoul.model import Book as EngineBook
from booksoul_api.core.errors import ApiError
from booksoul_api.db.unit_of_work import Detached
from booksoul_api.repositories.moderation_repo import ModerationRepository
from booksoul_api.services import chapter_selector as selector
from booksoul_api.services import citations as cite
from booksoul_api.services import sources as sources_mod
from booksoul_api.services.stream_recorder import StreamRecorder

logger = logging.getLogger(__name__)

# 提问时大脑出的事，按 `BrainError.kind` 说给网页上的人听：出路是一个能点的位置，
# 不是一条命令。审核不在这里——它另有台账与出路（`TurnRunner._moderation_advice`）。
# 措辞只在这里写一处，前端照样显示（frontend.md「失败的措辞只在服务端写一处」）。
_ASK_FAILURES = {
    "auth": "{name} 没认这个 key。到「大脑与人格」核对这一行的 key："
    "是不是这一家的、有没有过期。",
    "rate_limit": "{name} 暂时限流了，也可能是余额不足。等一会儿再问。",
    "not_found": "{name} 那边没有「{model}」这个模型。到「大脑与人格」点模型名换一个。",
    "server": "{name} 的服务暂时出错了。稍后再问一次。",
    "network": "连不上 {name}。检查网络或代理；本机的模型要确认服务开着。",
    "timeout": "{name} 太久没有回音。稍后再问一次。",
    "dependency": "这台服务器上用不了 {name}。先到「大脑与人格」换一个大脑。",
}
# 认不出类别的（包括流到一半断掉）
_ASK_FAILURE_OTHER = (
    "{name} 这次没答完。可以再问一次；一直这样的话，到「大脑与人格」换一个大脑试试。"
)


@dataclass(frozen=True)
class BookRef:
    """这本书在库里和磁盘上的位置。来自**读书与读章节**那一步。

    `storage_path` 已经拼过书库根（`UsableBook.root`，过门时拼好的）：后台任务跑在别处，
    再让它自己拼一次就是第二份拼法。
    """

    id: int
    # 逃生门那句话要点名这本书
    title: str
    storage_path: str
    # `books.word_count` 那个估值。只用来决定要不要先说一句「正在挑章节…」——
    # 真正的判据是 `choose()`，它拿的是磁盘上的真字数。
    word_count: int
    # 每节自带 `file_path`，溯源直接拿它拼 `Ref`，不再另拎一张旁表。
    chapters: list[selector.ChapterIn]


@dataclass(frozen=True)
class Budget:
    """这一轮能塞多少、用户点没点头重挑。来自**预算换算**那一步。

    会话锁着哪几节不在这里：那要占住会话之后才读得对（见 `Standing`）。
    """

    chars: int
    reselect: bool


@dataclass(frozen=True)
class BrainRef:
    """用哪个大脑。来自**大脑配置组装**那一步。

    `cfg.api_key` 是 `repr=False` 的，这一层不得把它重新暴露进 `repr` ——
    包一层就把下一层的隐藏白白拆掉，是这类泄漏最常见的来路。
    """

    name: str
    cfg: BrainConfig


@dataclass(frozen=True)
class Notice:
    """这一轮有什么不一样，要让用户知道（见 `StreamRecorder.notice`）。"""

    # 写死的字面量，给前端分支用；`text` 给人看
    code: str
    text: str


@dataclass(frozen=True)
class Draft:
    """这一轮请求期的全部输入。`ChatService.ask` 算。

    **不碰锁定、不碰轮号、不碰历史**：那几样只有占住这个会话之后读才对——
    占位之前读，读到的可能是上一轮还没提交的样子（见 `Standing`）。
    """

    conversation_id: int
    # 这一轮要跑哪几路。单人格一路，对读三路，走的是同一段代码——
    # 两套代码迟早会在断流那条路上分叉，而那条路本来就少有人走。
    personas: list[str]
    question: str
    # 主干人格：拼历史时每轮优先取它那一条回答
    answering: str
    book: BookRef
    budget: Budget
    brain: BrainRef
    reflection: bool

    @property
    def fits_whole(self) -> bool:
        """整本大概装得下。

        它同时要问书有多大、预算有多少，横跨两个值对象，所以放在这里而不是
        `Budget` 上——放进 `Budget` 就得让预算自己拎着一份书的字数，
        那张表按来源分组的依据当场就破了。
        """
        return self.book.word_count <= self.budget.chars


@dataclass(frozen=True)
class Standing:
    """这个会话此刻站在哪儿。只由 `turn_state.admit` 在占住会话之后算。

    与 `Draft` 分开，是因为它们**只有占位之后读才对**：上一轮的回答与锁定在
    它让出席位之前才提交，占位之前读，历史里会少一条回答、锁定还是旧的。
    """

    turn: int
    history: list[dict]
    # 会话锁定的章节（`chapters.id`）
    locked: list[int]
    # 锁定复用时 `admit` 已经算完了，后台任务不必再挑一次
    reuse: selector.Choice | None
    notices: tuple[Notice, ...] = ()


@dataclass(frozen=True)
class Turn(Draft):
    """这一轮要办的事：请求期的输入 + 占位之后的站位。

    `frozen=True`：它跨线程、跨任务传，谁在半路改一个字段，症状会出现在
    另一个地方的另一个时刻。
    """

    standing: Standing

    @classmethod
    def of(cls, draft: Draft, standing: Standing) -> Turn:
        return cls(
            **{f.name: getattr(draft, f.name) for f in fields(Draft)},
            standing=standing,
        )


class TurnRunner:
    """跑完一轮提问。

    **它拥有**：选章的触发（不是算法，算法在 `chapter_selector` 那个纯模块里）、
    读盘、引擎 `Session` 的装配、三路并发与错误边界、错误翻译。

    **它不管**：这一轮能不能开、第几轮、跑完怎么落库、断线重连怎么挂回同一条流——
    那些归 `turn_state`，它把 `run` 放进后台任务、包在 `async with recorder` 里。

    **它暴露**：一个 `run`，以及那个只读的 `Turn`。它们之间的调用顺序——先选章、
    再留档、再读正文、最后才装配引擎——只存在于 `run` 那一段的行序里。
    """

    @staticmethod
    async def run(recorder: StreamRecorder, turn: Turn) -> None:
        """挑章、留档、跑模型，把字喂给 recorder。

        **这个任务不跟着响应走。** 用户关掉页面、网络断了，它照样跑完——因为那次
        调用的钱已经花出去了，把结果写下来才对得起它。
        """
        try:
            choice, book = await TurnRunner._gather(recorder, turn)
            TurnRunner._must_have_content(book, choice)
            # 留档不当场写：它和回答在同一个事务里落（见 recorder.record）
            recorder.record(choice)
            # 只有真挑过才报「挑好了」。复用锁定的那一轮什么都没挑，报一句
            # 「挑好了」是替一个没发生的动作邀功——而且它会让用户以为每一轮
            # 都在重挑，正好和这一片要给他的保证相反。
            if choice.records and not choice.whole_book:
                recorder.stage(
                    f"挑好了：{choice.total_count} 节里读 {len(choice.selected_ids)} 节"
                )
            # 溯源要比对的是**这一轮模型真拿到的那几节**。没载入的章节它根本
            # 没看见过正文，那里的「引文」不是引用，是背出来的或编出来的。
            recorder.trace(
                await asyncio.to_thread(TurnRunner._read_sources, turn, choice)
            )
            sessions = await asyncio.to_thread(
                TurnRunner._build_engine_sessions, turn, book, choice
            )
        except BrainError as exc:
            # 准备阶段就炸了（多半是这台服务器缺这个后端的 SDK）：几路一个都没跑起来，
            # 全部标失败。说法按类别换成网页上的那一句，见 `_failure_text`
            text = TurnRunner._failure_text(exc, turn.brain)
            for persona in turn.personas:
                recorder.fail(persona, text)
            return
        except ApiError as exc:
            # 我们自己写给用户的话（Spec N1：带着下一步动作），原样转过去
            for persona in turn.personas:
                recorder.fail(persona, str(exc))
            return
        except Exception:
            logger.exception("准备这一轮时出错：会话 %s", recorder.conversation_id)
            for persona in turn.personas:
                recorder.fail(persona, "这一轮没能开始。请再试一次。")
            return

        # 三路并发。它们共享同一份整本书的缓存（缓存断点在全书之后、人格之前），
        # 所以真实成本 ≈ 1 份书 + 3 份人格与问答，不是三倍。
        await asyncio.gather(
            *(
                TurnRunner._one(recorder, persona, session, turn, choice)
                for persona, session in sessions.items()
            )
        )

    @staticmethod
    async def _one(
        recorder: StreamRecorder,
        persona: str,
        session: EngineSession,
        turn: Turn,
        choice: selector.Choice,
    ) -> None:
        """跑一路。

        **一路出错不拖垮另外两路**，所以异常在这里就地收住，不往 `gather` 外面抛。
        对读时一列失败、另外两列照常读完，比三列一起变白好——而且那两列的钱
        已经花了。
        """
        try:
            reply = await asyncio.to_thread(
                session.ask,
                turn.question,
                on_text=lambda chunk: recorder.sink(persona, chunk),
            )
        except BrainError as exc:
            if exc.kind == "moderation":
                # 内容审核（S13）。两件事要做，而且都不能拖到下一次：
                # 记一笔台账（拦得多了取交集，范围会自己收窄），
                # 以及把引擎那句**命令行口吻**的出路换成网页能执行的那一套。
                await TurnRunner._record_moderation(turn, choice, str(exc))
                recorder.fail(persona, TurnRunner._moderation_advice(turn.brain.name))
            else:
                recorder.fail(persona, TurnRunner._failure_text(exc, turn.brain))
        except Exception:
            # 别的异常不往外抛原文：里面可能有任何东西，包括这次请求的 key
            logger.exception(
                "生成回答时出错：会话 %s 人格 %s", recorder.conversation_id, persona
            )
            recorder.fail(persona, "回答生成时出错了。已经说出来的部分保留在下面。")
        else:
            recorder.finish(persona, reply)
            await TurnRunner._record_passed(turn, choice)

    @staticmethod
    def _must_have_content(book: EngineBook, choice: selector.Choice) -> None:
        """模型手里必须真有正文。一节都没载入就**就地停住，不往模型送**。

        这是 Grill C10 那条连锁的最后一道闸。前面几道已经堵上了（重解析会清掉
        会话的选章锁定，选章器在预算装不下时也至少给一节），但这一道要一直留着：

        一节都没载入时，模型收到的是一份**只有目录、没有正文**的书。它不会说
        「我没拿到正文」——它会对着章节名把内容编出来，语气和读过一样。整条路上
        没有任何一处会报错，用户看到的是一个流畅、自信、完全虚构的回答。

        宁可让这一轮明确失败。花掉的那次调用没了，但没花掉的那次信任还在。
        """
        loaded = sum(
            1
            for chapter in book.chapters
            if chapter.order + 1 in choice.selected_idx and chapter.content
        )
        if loaded:
            return
        raise ApiError(
            "这本书这一轮一节正文都没载入，所以这一问没有发给模型 —— "
            "模型手里只有目录时不会说「我没看到正文」，它会照着章节名编。"
            "多半是书库里的正文文件动过了：去书籍详情页重新解析一次，再回来问。"
        )

    @staticmethod
    async def _gather(
        recorder: StreamRecorder, turn: Turn
    ) -> tuple[selector.Choice, EngineBook]:
        """把书拿到手，并确定这次给模型哪几节。"""
        if turn.standing.reuse is not None:
            # 后续轮次：**只读选中的那几节文件**，不再全书读盘。一本几百万字的书，
            # 每轮全读一次就是每轮几百毫秒——而结论早在第一轮就定下来了。
            book = await asyncio.to_thread(
                load_from_path,
                turn.book.storage_path,
                only_idx=turn.standing.reuse.selected_idx,
            )
            return turn.standing.reuse, book

        if not turn.fits_whole:
            # 装不下就要切词打分，几百万字要好几秒。不说一声，用户对着的是一个
            # 卡住的界面——而「再问一遍」在那种时候看起来是个合理的反应。
            recorder.stage("正在挑章节…")
        return await asyncio.to_thread(TurnRunner._decide, turn)

    @staticmethod
    def _decide(turn: Turn) -> tuple[selector.Choice, EngineBook]:
        """读盘 + 选章。整段跑在工作线程里。

        读一本大部头、切几百万字的词都是纯阻塞活，放在事件循环上做会把整个服务
        连同别人的请求一起卡住。
        """
        book = load_from_path(turn.book.storage_path)
        # 引擎的 `order` 是 0 起的，`order + 1` 才是 `chapters.idx`。
        # 这一层翻译只在这里发生一次。
        content = {c.order + 1: c.content for c in book.chapters}
        filled = [
            replace(c, content=content.get(c.idx, "")) for c in turn.book.chapters
        ]
        choice = selector.choose(
            chapters=filled,
            question=turn.question,
            budget_chars=turn.budget.chars,
            locked_ids=turn.standing.locked,
            reselect=turn.budget.reselect,
        )
        return choice, book

    @staticmethod
    async def _record_moderation(
        turn: Turn, choice: selector.Choice, detail: str
    ) -> None:
        """记一笔内容审核拦截的台账（S13）。

        记的是「**这一次送了哪几节过去**」。供应商不说是哪一段触发的，但这份账是
        我们自己的：拦得多了取交集，范围会自己收窄，一分钱不用多花。

        自己开一条工作单元，理由和一轮收尾落库一样：请求那条随响应结束就关了。
        失败不往外抛 —— 台账记不上是一件遗憾，让这一路的错误消息也丢掉不是。
        """
        async with Detached(
            on_error="log", label=f"审核台账 会话{turn.conversation_id}"
        ) as session:
            await ModerationRepository(session).record(
                book_id=turn.book.id,
                brain_name=turn.brain.name,
                chapter_ids=list(choice.selected_ids),
                detail=detail,
            )

    @staticmethod
    async def _record_passed(turn: Turn, choice: selector.Choice) -> None:
        """这几节平安送过去了 —— 但**只在这本书被这个大脑拦过之后才记**。

        为什么要记：光靠「被拦时都在场」取交集排除不掉那些几个字的小节（《悉达多》
        的「扉页」254 字、「第一部」5 字），它们任何预算都装得下，每次选章都在场。
        一次成功的请求正好证明那几节是干净的，交集减去它们，范围才收得下去。

        为什么要加那个前提：成功的轮次每天几十上百，全记下来这张表就成了一份对话
        流水；而在第一次被拦之前，那些记录一个字都用不上。代价是成功那条路上多一次
        EXISTS 查询，走的是 `(book_id, brain_name, …)` 那个索引。

        失败不往外抛：台账记不上是一件遗憾，把一条**已经说完的回答**变成错误不是。
        """
        async with Detached(
            on_error="log", label=f"审核台账 会话{turn.conversation_id}"
        ) as session:
            repo = ModerationRepository(session)
            if not await repo.blocked_before(turn.book.id, turn.brain.name):
                return
            await repo.record(
                book_id=turn.book.id,
                brain_name=turn.brain.name,
                chapter_ids=list(choice.selected_ids),
                outcome="passed",
            )

    @staticmethod
    def _failure_text(exc: BrainError, brain: BrainRef) -> str:
        """大脑出的事，换成网页上的一句话。**对方说的话一个字都不带回来。**

        引擎那句话（`str(exc)`）不能照转，两个理由：

        * 里面有对方的原话——错误响应体（截 600 字）、连不上时的底层原因。自定义端点的
          地址是用户存的，可以指向内网：把对方的字转回来，就是借服务器之手去窥探那里；
          原话里还可能带着半截 key。列模型那条路早就不转了（`brain_service` 的
          `_LIST_FAILURES`），提问这条路漏了。
        * 它是写给命令行的：「检查环境变量 X」「`--brain deepseek`」「`pip install`」，
          在浏览器里一句都做不到（backend.md「出路要按谁会读到写」）。

        日志也只记类别：对方的原话进日志，等于把上面那两件事挪了个地方。缺 SDK 例外——
        那句话是我们自己写的（装哪个包），正是管理员要看的，进 ERROR。
        """
        if exc.kind == "dependency":
            logger.error("大脑 %s 用不了：%s", brain.name, exc)
        else:
            logger.warning(
                "大脑 %s 这一路失败，类别：%s", brain.name, exc.kind or "未归类"
            )
        template = _ASK_FAILURES.get(exc.kind, _ASK_FAILURE_OTHER)
        return template.format(name=brain.name, model=brain.cfg.model)

    @staticmethod
    def _moderation_advice(brain_name: str) -> str:
        """网页版的出路。

        引擎给的那一套是命令行的（`booksoul ask --brain …` / `--budget`），
        照抄到浏览器里就是一句无法执行的建议。同一个结论，两种说法，这是刻意的：
        出路必须是**收到它的人能执行的那一种**（Spec N1）。
        """
        return (
            f"{brain_name} 的内容审核把这一问拦下了 —— 不是 key 或配置的问题。"
            "文学作品里的暴力、性、宗教、沉沦等描写常会触发国内平台的过滤。"
            "两条出路：到「大脑与人格」换一个大脑（Claude / DeepSeek / Kimi / "
            "本机 Ollama 都不走国内审核），或者调小载入预算让检索避开触发的那一节。"
            "被拦过几次之后，书籍详情页会指出多半是哪一节。"
        )

    @staticmethod
    def _read_sources(turn: Turn, choice: selector.Choice) -> list[cite.Source]:
        """这一轮载入的那几节，切成可比对的块。

        切法交给 `services/sources`，不在这里自己写一份：S12 重解析之后按同一套
        切法把旧引文找回来，两处分叉的话「重定位命中率」测的就成了两套切法的差。
        """
        refs = [
            sources_mod.Ref(
                chapter_id=chapter.id,
                idx=chapter.idx,
                title=chapter.title,
                file_path=chapter.file_path,
            )
            for chapter in turn.book.chapters
        ]
        return sources_mod.read_sources(
            turn.book.storage_path, refs, only_idx=set(choice.selected_idx)
        )

    @staticmethod
    def _build_engine_sessions(
        turn: Turn, book: EngineBook, choice: selector.Choice
    ) -> dict[str, EngineSession]:
        """在工作线程里造引擎的 Session，一路一个。import 模型 SDK 是阻塞的。

        `preselected_idx` 一定要给：不给它，引擎会拿这本书再挑一次，而按 only_idx
        载入的书里没选中的章节只剩空壳——再挑一次会把空壳也算成「装得下」，
        于是模型收到一份「全都载入了」的目录和一堆有标题没正文的空段。

        几路**共用同一个 `book` 对象**（只读），这正是三路共享整本书缓存的来源：
        书那一段提示词逐字相同，缓存断点在它之后。
        """
        sessions = {}
        for persona in turn.personas:
            session = EngineSession(
                book=book,
                persona=persona_mod.load(persona),
                cfg=turn.brain.cfg,
                budget_chars=turn.budget.chars,
                reflection=turn.reflection,
                preselected_idx=set(choice.selected_idx),
            )
            # **每路各自一份 history。** 共用一个 list 的话，`Session.ask()` 结束时
            # 那句 `history.append` 会三路一起往同一个列表里塞，下一轮的上下文就成了
            # 三种声音交替的一团乱麻——而且它不报错。
            session.history = list(turn.standing.history)
            sessions[persona] = session
        return sessions
