"""对话编排：把书、人格、大脑接到一起，再把回答一路流给用户（界面稿⑦屏）。

这一层做的是 CLI 那条路上 `ask.open_session()` 做的事，但有四处必须不一样：

**一、服务是无状态的。** CLI 的 `Session` 活在进程内存里，多轮之间自然接得上。
Web 这边每次请求重建一个：载入书，再把历史从 `messages` 灌回去。代价是每轮多读
一次库，换来的是进程重启、换一台机器、换一个标签页都能接着聊。

**二、提问在调用模型之前落库。** 引擎里 `Session.ask()` 的 `history.append` 在
`chat()` 返回之后——异常即丢。那个形状对 CLI 无所谓（终端上那句话还在屏幕上），
对 Web 是致命的：断在半路，用户连自己刚才问了什么都找不回来。

**三、同一个问题绝不问两遍。** 前端断线重连会把同一个问题再 POST 一次。
服务端认出「这一轮正在跑」就挂上去回放，不再调模型——这件事连同锁定、第几轮、
写提问，都归 `services/turn_state`。用户为一个问题付两次钱是不能接受的（Spec #39）。

**四、选章只做一次，并且留档。** CLI 每轮都现挑（`Session._build_system` 里那句
`select_chapters`），进程内存里的 history 让它看起来没有代价。Web 这边不行：
每轮换一套章节，前缀缓存整个作废，那是直接写在用户账单上的。所以第一轮挑完就
锁进 `conversations.selected_chapter_ids`，后续轮次原样复用，并且把「凭什么是
这几节」写进 `retrievals` —— 回答基于哪几节必须可查，这是产品的信任基础。

系统提示的拼装完全交给引擎的 `Session`，一行都不在这里重写：那个顺序
（框架语 → 全书 →（缓存断点）→ 人格）直接决定用户的账单，在 Web 层复制一份，
迟早会和 CLI 分叉，而分叉的那天没有任何报错。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import asdict, replace

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from booksoul import persona as persona_mod
from booksoul.ask import context_budget_chars
from booksoul_api.core.errors import ApiError, NotFoundError
from booksoul_api.models.book import Book
from booksoul_api.models.conversation import Conversation
from booksoul_api.models.message import Message
from booksoul_api.repositories.book_repo import BookRepository
from booksoul_api.repositories.citation_repo import CitationRepository
from booksoul_api.repositories.retrieval_repo import RetrievalRepository
from booksoul_api.schemas.chat import ConversationOut, MessageOut
from booksoul_api.schemas.retrieval import (
    MatchedTerm,
    RetrievalBrief,
    RetrievalItemOut,
    RetrievalOut,
    Shortfall,
)
from booksoul_api.services import chapter_selector as selector
from booksoul_api.services import turn_state
from booksoul_api.services.book_lifecycle import BookLifecycle, Use
from booksoul_api.services.brain_service import BrainService
from booksoul_api.services.citation_service import citation_out
from booksoul_api.services.turn import BookRef, BrainRef, Budget, Draft

logger = logging.getLogger(__name__)


class ChatService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._books = BookRepository(session)
        self._retrievals = RetrievalRepository(session)
        self._citations = CitationRepository(session)

    # ------------------------------------------------------------ 开会话

    async def begin(
        self,
        *,
        user_id: int,
        book_id: int,
        persona: str,
        brain_name: str,
        question: str,
        brain_key: str,
        compare: bool = False,
        budget_chars: int = 0,
        reflection: bool = False,
    ) -> tuple[int, AsyncIterator[tuple[str, dict]]]:
        """开会话，连同第一问。返回新会话的 id 与第一问的事件流。

        **会话从第一问开始存在。** 先建后问的话，点进「开始对话」又走掉就留下一个
        空会话——书库「最近问过的」里那一串「（还没问出第一个问题）」就是这么来的。

        会话这一行只 flush 不提交，第一问写进去时（`turn_state.admit`）一起提交。
        在那之前任何一处拒绝——没填 key、书在解析、人格不对——都是抛出去，请求那条
        session 一关，这一行就跟着回滚了。

        与 `ask` 一样是普通协程：会抛的都在 `await` 那一刻抛。
        """
        conversation = await self._open(
            user_id=user_id,
            book_id=book_id,
            persona=persona,
            brain_name=brain_name,
            budget_chars=budget_chars,
            reflection=reflection,
        )
        stream = await self.ask(
            conversation_id=conversation.id,
            user_id=user_id,
            question=question,
            brain_key=brain_key,
            compare=compare,
        )
        return conversation.id, stream

    async def start(
        self,
        *,
        user_id: int,
        book_id: int,
        persona: str,
        brain_name: str,
        budget_chars: int = 0,
        reflection: bool = False,
    ) -> Conversation:
        """只建会话这一行，并提交。

        **网页那条路不走这里**，走 `begin`：会话与第一问一起落，不留空会话。
        它只剩一个用处——手里要一个现成的会话、接着问第 N 轮（测试就从这里起）。
        """
        conversation = await self._open(
            user_id=user_id,
            book_id=book_id,
            persona=persona,
            brain_name=brain_name,
            budget_chars=budget_chars,
            reflection=reflection,
        )
        await self._session.commit()
        return conversation

    async def _open(
        self,
        *,
        user_id: int,
        book_id: int,
        persona: str,
        brain_name: str,
        budget_chars: int,
        reflection: bool,
    ) -> Conversation:
        """校验、快照大脑、把会话这一行 flush 出 id。**不提交**——提交归调用方。"""
        book = await BookLifecycle(self._session).require(
            book_id=book_id, user_id=user_id, use=Use.ASK
        )
        _check_persona(persona)

        # 开会话这一刻还不需要 key：只要确认这个大脑存在，拿到⑧屏那一行
        brain = await BrainService(self._session).describe(
            user_id=user_id, name=brain_name
        )
        conversation = Conversation(
            user_id=user_id,
            book_id=book.id,
            persona=persona,
            # 名字与模型都冗余存下来，不做外键：预设被改名或删掉，
            # 都不该篡改历史记录里「这段话当时是谁说的」
            brain_name=brain.name,
            model=brain.model,
            # 正文额度跟着模型一起锁：之后预设换了窗口、用户改了自定义端点，
            # 这个会话都照开的时候那样挑章节。与⑧屏此刻显示的是同一个数。
            context_chars=brain.context_chars,
            budget_chars=budget_chars,
            reflection=reflection,
        )
        self._session.add(conversation)
        # session 工厂关了 autoflush：不手动 flush，拿不到 id，后面按 id 也查不到它
        await self._session.flush()
        return conversation

    async def list_recent(
        self, *, user_id: int, limit: int = 20, book_id: int | None = None
    ) -> list[ConversationOut]:
        """③屏那一栏「最近问过的」；给了 `book_id` 就是书籍详情那一栏「这本书的会话」。

        没有它，历史等于存了也找不到——每点一次「开始对话」都是一个新的。
        """
        # 按**最后一次提问**的时间排，不按 conversations.updated_at：
        # 加一条消息并不会 UPDATE 会话那一行，那个时间戳会一直停在开会话那一刻，
        # 于是「最近问过的」排出来的顺序和「最近问过」毫无关系。
        turns = (
            select(
                Message.conversation_id.label("cid"),
                func.max(Message.turn).label("turn_count"),
                func.max(Message.created_at).label("last_at"),
            )
            .group_by(Message.conversation_id)
            .subquery()
        )
        last_at = func.coalesce(turns.c.last_at, Conversation.created_at)
        query = (
            select(Conversation, Book.title, func.coalesce(turns.c.turn_count, 0))
            .join(Book, Book.id == Conversation.book_id)
            .outerjoin(turns, turns.c.cid == Conversation.id)
            .where(Conversation.user_id == user_id)
        )
        if book_id is not None:
            query = query.where(Conversation.book_id == book_id)
        rows = await self._session.execute(
            query.order_by(last_at.desc(), Conversation.id.desc()).limit(
                max(1, min(limit, 100))
            )
        )
        return [
            _conversation_out(conversation, book_title, int(turn_count))
            for conversation, book_title, turn_count in rows
        ]

    async def delete(self, *, conversation_id: int, user_id: int) -> None:
        """删掉一个会话。正在答的那一轮还没收尾时 409（见 `turn_state`）。"""
        conversation = await self._require(conversation_id, user_id)
        await turn_state.forget_conversation(
            self._session, conversation_id=conversation.id
        )

    async def detail(self, *, conversation_id: int, user_id: int) -> ConversationOut:
        conversation = await self._require(conversation_id, user_id)
        book = await self._books.get_for_user(conversation.book_id, user_id)
        messages = await self._history_rows(conversation.id)
        out = _conversation_out(
            conversation,
            book.title if book else "",
            messages[-1].turn if messages else 0,
        )
        traced: dict[int, list] = {}
        for row in await self._citations.for_conversation(conversation.id, user_id):
            traced.setdefault(row[0].message_id, []).append(citation_out(row))
        out.messages = [_message_out(m, traced.get(m.id, [])) for m in messages]
        out.book_word_count = book.word_count if book else 0
        out.book_chapter_count = book.chapter_count if book else 0

        found = await self._retrievals.latest_for_user(conversation.id, user_id)
        if found is not None:
            row, _ = found
            out.retrieval = RetrievalBrief(
                turn=row.turn,
                selected_count=row.selected_count,
                total_count=row.total_count,
                whole_book=row.whole_book,
                budget_chars=row.budget_chars,
                used_chars=row.used_chars,
            )
        return out

    async def update(
        self,
        *,
        conversation_id: int,
        user_id: int,
        persona: str | None = None,
        reflection: bool | None = None,
    ) -> ConversationOut:
        """改这个会话的两个旋钮。**没给的那个不动。**

        `persona` —— 「跟这个继续聊」：把主干人格换成对读里的这一路（S10）。
        **旧旁支一个字都不删。** 它们仍然在 `messages` 里；换主干改变的只是
        「下一轮用谁的口吻、拼 history 时优先取谁」。删掉旁支会让用户失去对读
        的全部意义——他要的正是「三种说法我都看过」。

        `reflection` —— 处境模式（S11）：把「读者带来的是他自己的处境」那一段
        附到人格提示词后面。**随时可拨，不必先问用户一句**：缓存断点在全书之后、
        人格之前，附加这一段只动断点之后那一块，整本书的缓存照常命中。
        这一点和「重新挑章节」正相反，那个会换掉整套章节、输入按全价重算，
        所以那个要用户点头，这个不要。

        两个旋钮都用 `None` 表示「没给」，不用默认值：`reflection=False` 是一个
        有意义的取值（把它关掉），和「这次请求没提这件事」必须分得开。
        """
        conversation = await self._require(conversation_id, user_id)
        if persona is not None:
            _check_persona(persona)
            conversation.persona = persona
        if reflection is not None:
            conversation.reflection = reflection
        await self._session.commit()
        return await self.detail(conversation_id=conversation_id, user_id=user_id)

    # ------------------------------------------------------------ 选章明细

    async def retrieval(self, *, conversation_id: int, user_id: int) -> RetrievalOut:
        """⑥屏：这次回答读了什么。"""
        conversation = await self._require(conversation_id, user_id)
        found = await self._retrievals.latest_for_user(conversation_id, user_id)
        if found is None:
            raise NotFoundError("这个会话还没有选过章 —— 先问一个问题，明细就有了。")
        row, items = found
        book = await self._books.get_for_user(conversation.book_id, user_id)

        # 累计那一列按名次现算。放到前端去加，预算线就有了第二个算法。
        running = 0
        out_items = []
        for item in items:
            if item.selected:
                running += item.word_count
            out_items.append(
                RetrievalItemOut(
                    chapter_id=item.chapter_id,
                    chapter_idx=item.chapter_idx,
                    chapter_title=item.chapter_title,
                    rank=item.rank,
                    score=float(item.score),
                    word_count=item.word_count,
                    selected=item.selected,
                    cumulative_chars=running if item.selected else 0,
                    matched_terms=[MatchedTerm(**t) for t in item.matched_terms],
                )
            )

        return RetrievalOut(
            conversation_id=conversation_id,
            turn=row.turn,
            question=await self._question_of(conversation_id, row.turn),
            book_title=book.title if book else "",
            model=conversation.model,
            budget_chars=row.budget_chars,
            used_chars=row.used_chars,
            selected_count=row.selected_count,
            total_count=row.total_count,
            whole_book=row.whole_book,
            query_terms=list(row.query_terms),
            items=out_items,
            # 「差多少字」只有一份实现，在 `chapter_selector` 里（挑章节当场算
            # 用的也是它）。这里只做一次领域值 → 响应模型的转换：两边字段同名，
            # `asdict` 展开，多一个少一个都会当场炸而不是静默少一列。
            shortfall=_shortfall_out(row, items),
            created_at=row.created_at,
        )

    # ------------------------------------------------------------ 问一轮

    async def ask(
        self,
        *,
        conversation_id: int,
        user_id: int,
        question: str,
        brain_key: str,
        persona: str | None = None,
        reselect: bool = False,
        compare: bool = False,
    ) -> AsyncIterator[tuple[str, dict]]:
        """问一轮，逐字流出来。

        返回的是 `(事件名, 载荷)` 的异步迭代器，路由那一层再包成 SSE ——
        服务层不认识 SSE 格式，那是传输的事。

        **它是一个普通协程，不是异步生成器。** 区别很要紧：所有会抛错的检查
        （鉴权、没填 key、上一轮还没说完、问到了没载入的章节）都发生在 `await`
        的那一刻，于是它们变成规规矩矩的 4xx。若写成异步生成器，这些检查要等到
        第一次迭代才跑，那时 `200 text/event-stream` 的响应头已经发出去了，
        用户看到的是一条莫名其妙断掉的流，而不是一句「到设置页填 key」。
        """
        question = question.strip()
        if not question:
            raise ApiError("问题是空的。写点什么再问。")

        conversation = await self._require(conversation_id, user_id)
        # 这本书现在能不能拿来问（解析中 / 停下了 → 409）。**还没花钱就拦**：提问不落库，
        # 模型不调。过了门的书路径已经拼好
        book = await BookLifecycle(self._session).require(
            book_id=conversation.book_id, user_id=user_id, use=Use.ASK
        )

        if persona and persona != conversation.persona:
            _check_persona(persona)
            # 不在这里提交：它跟着这一轮的提问一起落（`turn_state.admit`），
            # 这一问没问出去，人格也就没换
            conversation.persona = persona
        answering = persona or conversation.persona
        # 对读三路一起跑。**主干人格不动** —— 换主干是「跟这个继续聊」那个
        # 明确动作的事，不该由「我想并排看看」顺手改掉。
        personas = list(persona_mod.PERSONAS) if compare else [answering]

        brain = await BrainService(self._session).config_for(
            user_id=user_id, name=conversation.brain_name, api_key=brain_key
        )
        # 会话的模型只有一份真相：开会话时存下的快照。显示读它、算预算读它，
        # 真正调模型也读它——`config_for` 给的是**此刻**的配置，预设的模型在
        # 开会话之后可能已经换过。`replace` 造一份新的，不改那份被多处读的配置。
        brain = replace(brain, model=conversation.model)

        rows_of_book = await self._books.list_chapters(book.id)
        chapters = [
            selector.ChapterIn(
                id=row.id,
                idx=row.idx,
                title=row.title,
                word_count=row.word_count,
                file_path=row.file_path,
            )
            for row in rows_of_book
        ]
        # 预算三级，前一级有就不看后一级（Spec N9）：用户手调的载入预算 >
        # 开会话时快照的正文额度 > 按模型名查引擎内置表。快照为 0 的是这一列
        # 加上之前开的会话，走的仍是最后那条老路径，逐字不变。
        budget = context_budget_chars(
            conversation.model,
            conversation.budget_chars or conversation.context_chars,
        )

        # 到这里为止全是请求期的输入。锁定、历史、第几轮、写提问——那些要占住这个
        # 会话之后才读得对，归 `turn_state`；同一个问题正在答就挂上去，也在那里判。
        return await turn_state.admit(
            self._session,
            Draft(
                conversation_id=conversation_id,
                personas=personas,
                question=question,
                answering=answering,
                book=BookRef(
                    id=book.id,
                    title=book.title,
                    storage_path=str(book.root),
                    word_count=book.word_count,
                    chapters=chapters,
                ),
                budget=Budget(chars=budget, reselect=reselect),
                brain=BrainRef(name=conversation.brain_name, cfg=brain),
                reflection=conversation.reflection,
            ),
        )

    # ------------------------------------------------------------ 内部

    async def _require(self, conversation_id: int, user_id: int) -> Conversation:
        conversation = await self._session.scalar(
            select(Conversation).where(
                Conversation.id == conversation_id, Conversation.user_id == user_id
            )
        )
        if conversation is None:
            # 越权与不存在同一个 404：403 等于承认这个 id 确实存在
            raise NotFoundError("没有这个会话。它可能已经被删掉了。")
        return conversation

    async def _history_rows(self, conversation_id: int) -> list[Message]:
        rows = await self._session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.seq)
        )
        return list(rows.scalars())

    async def _question_of(self, conversation_id: int, turn: int) -> str:
        return (
            await self._session.scalar(
                select(Message.content).where(
                    Message.conversation_id == conversation_id,
                    Message.turn == turn,
                    Message.role == "user",
                )
            )
            or ""
        )


# --------------------------------------------------------------- 留在这一层的
#
# 跑一轮的执行在 `services/turn.py` 的 `TurnRunner`；这个会话此刻能不能开一轮、
# 开了拿什么开（锁定、历史、第几轮、逃生门、写提问）在 `services/turn_state`；
# 这本书此刻能不能拿来问（门表）在 `services/book_lifecycle`。
# 留在这里的四个都不属于那几件事：
#
# * `_check_persona`（3 个调用点）、`_conversation_out`（2 个）—— 本来就有第二个调用方
# * `_shortfall_out`、`_message_out` —— ⑥屏与会话详情那两条**读**路径上的转换
#
# 换句话说：`ChatService` 算这一轮请求期的输入，以及会话的开合与读取。


def _check_persona(key: str) -> None:
    if key not in persona_mod.PERSONAS:
        options = "、".join(persona_mod.PERSONAS)
        raise ApiError(f"没有「{key}」这种人格。可选：{options}。")


def _shortfall_out(row, items) -> Shortfall | None:
    """⑥屏那行「差多少字」。算法在 `chapter_selector.shortfall_of`，这里只转类型。"""
    short = selector.shortfall_of(
        whole_book=row.whole_book,
        budget_chars=row.budget_chars,
        used_chars=row.used_chars,
        items=items,
    )
    return None if short is None else Shortfall(**asdict(short))


def _conversation_out(
    conversation: Conversation, book_title: str, turn_count: int
) -> ConversationOut:
    return ConversationOut(
        id=conversation.id,
        book_id=conversation.book_id,
        book_title=book_title,
        persona=conversation.persona,
        brain_name=conversation.brain_name,
        model=conversation.model,
        title=conversation.title,
        reflection=conversation.reflection,
        turn_count=turn_count,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def _message_out(message: Message, traced: list) -> MessageOut:
    return MessageOut(
        id=message.id,
        seq=message.seq,
        turn=message.turn,
        role=message.role,
        persona=message.persona,
        content=message.content,
        complete=message.complete,
        input_tokens=message.input_tokens,
        output_tokens=message.output_tokens,
        cache_read=message.cache_read,
        cache_write=message.cache_write,
        created_at=message.created_at,
        citations=traced,
    )
