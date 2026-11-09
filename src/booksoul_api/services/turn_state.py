"""会话的轮次状态：这个会话此刻能不能开一轮、开了拿什么开、收尾怎么一起落。

开一轮之前要回答四件事：有没有一轮正在跑（是重连还是新问题）？这一轮第几轮、
消息几号？锁着哪几节（锁定为空时，是本来就空还是被重解析清掉了）？收尾时回答、
引文、选章留档、锁定怎么一起落？这四件事原先分在五处，谁都不拥有它们
（见 `Todo/Rfcs/turn-state-rfc.md`）。这个模块把它们收到一处。

**一个会话同一时刻只有一轮，问题相同就挂上去回放。** 用户为同一个问题付两次钱
是不能接受的（Spec #39）。原先「有没有一轮在跑」的检查与登记之间隔着七八次查库
和一次提交，两个几乎同时到的请求都能穿过检查；而那一轮在落库**之前**就把自己
摘掉了，落库那几秒里同一个问题再来，又是一轮新的、又一次付费。现在：

* 占位是第一件事，查与占之间没有 await —— 在事件循环上是原子的
* 让位在收尾那个事务**提交之后**、宣布 `done` 之前，而且认人：只让自己占的那个位
* 事实（锁定、历史、轮号）在占位**之后**读 —— 上一轮在让位之前就提交了
* 删会话也占席位（`forget_conversation`）—— 收尾要往会话里写回答，删在它前面，
  付过钱的回答就无处可落

**seq 持会话行锁发放。** `messages` 上有 `(conversation_id, seq)` 唯一约束。
不持锁地 `max(seq)+1`，另一处还没提交的写就读不到，两边算出同一个号——
一边在唯一约束上等另一边提交，然后以一个 500 收场；落在回答那一侧，就是
一条已经付过钱的回答没能存下来。持锁之后两边只会排队，谁后到谁接着往下发。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from booksoul_api.core.errors import ConflictError, NotFoundError, ReselectRequired
from booksoul_api.db.unit_of_work import Detached
from booksoul_api.models.chapter import Chapter
from booksoul_api.models.citation import Citation
from booksoul_api.models.conversation import Conversation
from booksoul_api.models.message import Message
from booksoul_api.models.retrieval import Retrieval
from booksoul_api.repositories.conversation_repo import ConversationRepository
from booksoul_api.repositories.retrieval_repo import RetrievalRepository
from booksoul_api.services import chapter_selector as selector
from booksoul_api.services.citations import find
from booksoul_api.services.stream_recorder import Outcome, StreamRecorder
from booksoul_api.services.turn import Draft, Notice, Standing, Turn, TurnRunner

_REPARSED = Notice(
    code="reparsed",
    text="这本书重新解析过了 —— 这一轮要按新章节重挑一次，"
    "整本书的缓存作废，这次输入按全价算。",
)


_BUSY_ANSWERING = "上一个问题还在回答，等它说完再问下一个。"


@dataclass(eq=False)
class _Seat:
    """一个会话此刻被谁占着：一个问题，或者一次删除（`question` 为 None）。"""

    question: str | None
    # 首领把 recorder 放进来；它在准备阶段就失败了，放 None
    ready: asyncio.Future
    # 别人撞上这个席位时听到的那句话：说实情，不说「上一个问题」——那会儿可能在删
    busy: str = _BUSY_ANSWERING


# 正在跑的那一轮，按会话索引。单实例（`backend.md` 待确认 #2）下进程内的这张表就是
# 全部真相；多实例时要换成外部的（advisory lock + 回放），调用方不必跟着改。
_SEATS: dict[int, _Seat] = {}

# 正在跑的后台任务。必须留一个强引用，否则事件循环可能把任务回收掉，
# 生成就在没人察觉的情况下停了——而钱已经花了。
_RUNNING: set[asyncio.Task] = set()


async def admit(session: AsyncSession, draft: Draft) -> AsyncIterator[tuple[str, dict]]:
    """开一轮，或者挂上正在跑的同一个问题。返回这一轮的事件流。

    **普通协程，不是异步生成器。** 它会抛的——正在答另一个问题（409）、问到了
    没载入的章节（`ReselectRequired`）——都发生在 `await` 的那一刻，于是变成
    规规矩矩的 4xx。写出一个 `yield`，这些检查就要等到第一次迭代才跑，那时
    `200 text/event-stream` 的响应头已经发出去了。

    `session` 是请求那一条：提问在它上面写并提交（连同 `ask` 里那次人格切换）。
    """
    cid = draft.conversation_id
    while True:
        seat = _SEATS.get(cid)
        if seat is None:
            break
        if seat.question != draft.question:
            raise ConflictError(seat.busy)
        # 同一个问题：多半是断线重连。挂上去回放，**不再调模型**。
        # shield：这个请求的客户端走掉会取消这里，不能连带取消首领那边的 future
        recorder = await asyncio.shield(seat.ready)
        if recorder is not None:
            return recorder.listen()
        # 首领在准备阶段就失败了，它已经让了位：回头自己来一遍，
        # 拿到的是自己的那个 4xx，而不是一条空流

    seat = _Seat(
        question=draft.question, ready=asyncio.get_running_loop().create_future()
    )
    _SEATS[cid] = seat  # 与上面那次查之间没有 await
    try:
        standing = await _stand(session, draft)

        async def persist(outcome: Outcome) -> None:
            try:
                await _settle(outcome)
            finally:
                # 提交之后、recorder 宣布 done 之前让位——两者之间没有 await
                _leave(cid, seat)

        recorder = StreamRecorder.open(
            conversation_id=cid,
            turn=standing.turn,
            personas=draft.personas,
            persist=persist,
        )
        # 在任务启动之前发：早到的、晚到的订阅者都经回放拿到
        for notice in standing.notices:
            recorder.notice(notice.code, notice.text)
        task = asyncio.create_task(_drive(recorder, Turn.of(draft, standing)))
    except BaseException:
        _leave(cid, seat)
        # 等在席位前面的人得知道首领没开成，否则他们会一直等下去
        seat.ready.set_result(None)
        raise

    _RUNNING.add(task)
    task.add_done_callback(_RUNNING.discard)
    # 孤儿守卫：让位写在收尾那条路上，而一个还没跑起来就被取消的任务（服务关停时
    # 事件循环就这么做）根本走不到那里。认人，所以任务正常收尾之后它什么都不做。
    task.add_done_callback(lambda _: _leave(cid, seat))
    seat.ready.set_result(recorder)
    return recorder.listen()


async def forget_locks(session: AsyncSession, *, book_id: int) -> int:
    """重解析之后：清掉这本书所有会话的选章锁定，返回动了几行。不提交。

    锁定存的是**旧的** chapter id。重解析把 chapters 整张重建，那几个 id 已经不存在了：
    不清掉，下一轮照一份死名单去挑。清掉之后，下一轮由 `admit` 认出「这是被清掉的、
    不是本来就空」，重挑并告诉用户这一轮按全价算。

    会话的锁定只有这个模块写：一轮收尾时（`_settle`）与这里。
    """
    return await ConversationRepository(session).clear_locks_for_book(book_id)


async def forget_conversation(session: AsyncSession, *, conversation_id: int) -> None:
    """删掉一个会话，提交。消息、引文、选章留档随外键级联一起走。

    **正在答的那一轮收尾之前不许删**（409）。收尾要往这个会话里写回答：删在它
    前面，一条已经付过钱的回答就无处可落。所以删也占席位——和提问同一张表：
    有人占着就拒绝；占住之后再删，删的这一小会儿里新来的提问同样被挡在门外，
    并且被告知的是「在删」。

    调用方先确认这个会话是这个用户的（越权与不存在同一个 404）。
    """
    if conversation_id in _SEATS:
        raise ConflictError("这个会话还在回答，等它说完再删。")
    seat = _Seat(
        question=None,
        ready=asyncio.get_running_loop().create_future(),
        busy="这个会话正在删除。",
    )
    _SEATS[conversation_id] = seat  # 与上面那次查之间没有 await
    try:
        await session.execute(
            delete(Conversation).where(Conversation.id == conversation_id)
        )
        await session.commit()
    finally:
        _leave(conversation_id, seat)
        # 没人会等一次删除（问题永远对不上 None），但 future 不留悬着的
        seat.ready.set_result(None)


async def _drive(recorder: StreamRecorder, turn: Turn) -> None:
    """后台任务的全部：跑这一轮，退出 recorder 时收尾落库。"""
    async with recorder:
        await TurnRunner.run(recorder, turn)


def _leave(conversation_id: int, seat: _Seat) -> None:
    """让出席位。**认人**：只让自己占的那一个，别把后来者的摘掉。"""
    if _SEATS.get(conversation_id) is seat:
        del _SEATS[conversation_id]


async def _stand(session: AsyncSession, draft: Draft) -> Standing:
    """占位之后：读事实、拦逃生门、写提问。

    **写提问在调用模型之前**：断在半路时，用户至少还找得回自己问过什么。
    逃生门在写任何东西**之前**拦：问出去才拦，库里会留下一条永远等不到回答的提问。
    """
    cid = draft.conversation_id
    facts = await _facts(session, cid)
    rows = await _history_rows(session, cid)

    # 锁定里的 id 可能已经不存在了（锁定落下之后章节被换掉）。只认活着的那几节——
    # 照一份死名单去挑，会选中 0 节，把一本没有正文的书送给模型
    known = {chapter.id for chapter in draft.book.chapters}
    locked = [i for i in facts.locked if i in known]

    reuse: selector.Choice | None = None
    if locked and not draft.budget.reselect:
        reuse = selector.choose(
            chapters=draft.book.chapters,
            question=draft.question,
            budget_chars=draft.budget.chars,
            locked_ids=locked,
        )
        if reuse.reason == selector.RELOCK_NEEDED:
            raise ReselectRequired(_relock_hint(reuse, draft.book.title))

    wiped = _lock_was_wiped(
        raw=facts.locked, alive=locked, last_whole_book=facts.last_whole_book
    )

    turn = facts.last_turn + 1
    session.add(
        Message(
            conversation_id=cid,
            # 行锁在 `_facts` 里就拿到了，一直持有到下面这次提交
            seq=facts.last_seq + 1,
            turn=turn,
            role="user",
            persona=None,
            content=draft.question,
        )
    )
    conversation = await session.get(Conversation, cid)
    if conversation is not None and not conversation.title:
        conversation.title = draft.question[:255]
    await session.commit()

    return Standing(
        turn=turn,
        history=_history_for(rows, draft.answering),
        locked=locked,
        reuse=reuse,
        notices=(_REPARSED,) if wiped else (),
    )


@dataclass(frozen=True)
class _Facts:
    # 原样的锁定，可能含已经不存在的 chapter id
    locked: list[int]
    # 最近一次真做过决策的那一轮是不是整本装下；一行留档都没有是 None
    last_whole_book: bool | None
    last_seq: int
    last_turn: int


async def _facts(session: AsyncSession, conversation_id: int) -> _Facts:
    """锁住会话行（持有到写完提问提交），再一条查询取齐这一轮要的事实。

    **先锁、后读，分成两条语句。** 读已提交隔离级别下，一条语句的快照在它**开始**那一刻
    就定了；`FOR UPDATE` 等到锁之后只会重读被锁的那一行，同一条语句里的子查询看到的
    仍是等锁之前的世界——`max(seq)` 读不到刚刚提交的那一条，算出同一个号，撞唯一约束。
    实测撞过：`test_写提问要等会话行上的锁_放开之后接着发号`。

    读全是列级的：锁定是后台任务在**另一条 session** 里写的（`_settle`），而 session
    工厂配的是 `expire_on_commit=False`——这个 session 若早就持有过这行会话，
    `conversation.selected_chapter_ids` 会一直停在读进来那一刻的值，而一次普通
    `select(Conversation)` 也**不覆盖**已经载入的属性。停在旧值的后果不是报错：
    第二轮会当成没锁过，重跑一次 BM25、换一套章节，缓存整个作废。取列而不是整行，
    SQLAlchemy 不会拿身份映射里的对象来替代它。
    """
    last_decision = (
        select(Retrieval.whole_book)
        .where(Retrieval.conversation_id == Conversation.id)
        .order_by(Retrieval.turn.desc())
        .limit(1)
        .scalar_subquery()
    )
    last_seq = (
        select(func.coalesce(func.max(Message.seq), 0))
        .where(Message.conversation_id == Conversation.id)
        .scalar_subquery()
    )
    last_turn = (
        select(func.coalesce(func.max(Message.turn), 0))
        .where(Message.conversation_id == Conversation.id)
        .scalar_subquery()
    )
    await _hold(session, conversation_id)
    row = (
        await session.execute(
            select(
                Conversation.selected_chapter_ids, last_decision, last_seq, last_turn
            ).where(Conversation.id == conversation_id)
        )
    ).one()
    return _Facts(
        locked=[int(i) for i in (row[0] or [])],
        last_whole_book=row[1],
        last_seq=int(row[2]),
        last_turn=int(row[3]),
    )


async def _history_rows(session: AsyncSession, conversation_id: int) -> list[Message]:
    rows = await session.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.seq)
    )
    return list(rows.scalars())


def _lock_was_wiped(
    *, raw: list[int], alive: list[int], last_whole_book: bool | None
) -> bool:
    """这一轮要重挑，是因为锁定被外力弄没了——不是本来就空。

    两种「被弄没了」：

    * **被清空**：`selected_chapter_ids` 为空有两种来路，而它们在库里长得**一模一样**：
      整本装得下的书本来就锁空的（`_settle` 在 `whole_book` 时写的就是 `[]`），和重解析的
      善后把它清掉了。原先的判据是「问过好几轮却没有锁定」，把两件事压成了同一个值——
      整本装得下的每一本书，从第二轮起都会收到一句「这本书重新解析过了，按全价算」，
      书没动过，那笔钱不存在。分辨它们的事实早就在库里：最近一次**真做过决策**的那一轮
      留了档。挑过章、而现在锁定是空的，只能是被清掉的。一行留档都没有，说明这个会话从没
      做成过一次决策——全失败的那一轮什么都不落——也就无所谓「被清掉」。
    * **名单全死了**：锁定非空，可里面一个活的章节都没有（锁定落下之后章节被换掉）。
      这一轮同样要按全价重挑，同样该说一声。只死了一部分不算：照活着的那几节走，不重挑。
    """
    if raw:
        return not alive
    # 没有留档时是 None，`None is False` 为假 —— 正是要的答案
    return last_whole_book is False


def _relock_hint(choice: selector.Choice, title: str) -> str:
    """问到了没载入的章节。说破，并且把代价一起说清楚。

    不自动重选：重选会换掉整套章节，前缀缓存全部作废，这一轮要按全价重算一次
    输入。那笔钱该由用户决定花不花，不该由一个「贴心」的自动重试替他决定。
    """
    named = "、".join(f"《{t}》" for t in choice.mentioned)
    return (
        f"你问到了{named}，这几节这次没在模型手里 —— 《{title}》装不下，"
        f"当时只挑了 {len(choice.selected_ids)} 节。"
        "可以重新挑一次把它们带上，但重选会换掉整套章节、缓存作废，"
        "这一轮的输入要按全价重算一次。要重选就勾上「重新挑章节」再问一遍。"
    )


def _history_for(rows: list[Message], persona: str) -> list[dict]:
    """把历史拼成 user / assistant 严格交替的形状。

    **每轮取一条回答：有当前主干人格那条就用它，没有就用这一轮最早的那条。**

    这是 S10 的裁决。原本的验收是「只取主干人格那一支」，它和「『跟这个继续聊』
    更新主干人格」同时成立时会互相拆台：单人格聊 4 轮后切到作者，切换前那些轮次
    集体落选主干，历史从 4 条塌到 2 条——**而界面上历史还完整显示着，没有任何
    报错**。按「每轮取一条」拼，单人格轮次只有一条回答，永远不会落选。

    一轮只在**既有提问、又有一条说完了的回答**时才进历史。少了哪一半都不进：
    断在半路的那一轮若只进提问，历史里就会出现两个连着的 user 消息，
    而 Anthropic 那边要求角色交替——那是一个只在「上一轮断过流」时才现形的
    500。残文本身不会丢，用户点「接着往下」时它会被原样带回去。
    """
    turns: dict[int, dict] = {}
    for row in rows:
        slot = turns.setdefault(row.turn, {"question": None, "answers": []})
        if row.role == "user":
            slot["question"] = row
        elif row.complete:
            slot["answers"].append(row)

    out: list[dict] = []
    for turn in sorted(turns):
        question, answers = turns[turn]["question"], turns[turn]["answers"]
        if question is None or not answers:
            continue
        # rows 按 seq 排过，所以 answers[0] 就是这一轮最早说完的那条
        pick = next((a for a in answers if a.persona == persona), answers[0])
        out.append({"role": "user", "content": question.content})
        out.append({"role": "assistant", "content": pick.content})
    return out


async def _hold(session: AsyncSession, conversation_id: int) -> None:
    """锁住会话行，持有到这个事务结束。

    锁的是整行，但只取回一列：不取整行，身份映射里那个 `Conversation` 对象就不会被
    这次查询牵动。**锁完再另起一条语句去读**（见 `_facts`）。

    锁不到就是会话没了：提问查会话（`_require`）与这里之间隔着好几次 await，
    这当中它可以被删掉。那是 404，不是后面某条 `.one()` 抛出来的 500。
    """
    held = await session.scalar(
        select(Conversation.id)
        .where(Conversation.id == conversation_id)
        .with_for_update()
    )
    if held is None:
        raise NotFoundError("没有这个会话。它可能已经被删掉了。")


async def _allocate_seq(session: AsyncSession, conversation_id: int) -> int:
    """锁住会话行，返回下一个可用的 seq。要几个连续号就从它往后数。

    **锁持有到这个事务结束**：调用方必须在同一个事务里把号用掉并提交，
    提交之前别处的发号都在这一行上排队。
    """
    await _hold(session, conversation_id)
    last = await session.scalar(
        select(func.coalesce(func.max(Message.seq), 0)).where(
            Message.conversation_id == conversation_id
        )
    )
    return int(last or 0) + 1


async def _settle(outcome: Outcome) -> None:
    """一轮的**唯一一个事务**：回答、引文、选章留档、会话锁定，一起落。

    交给 `StreamRecorder` 作它的 `persist`：它在退出时调这里，这里返回之后它才
    宣布 `done`；这里抛了，它宣布 `error`。

    对读一轮三条回答，分三个事务写就可能留下「两列在库里、第三列不在」的
    中间态；而界面下次打开时看到的是两列，没有任何地方说得清第三列去了哪。

    选章留档与锁定原先在调模型**之前**另开一个事务写。那样崩在中间会留下
    一行孤立的 `retrievals` —— 一份「凭什么是这几节」的解释，配不上任何一条
    回答；而锁定已经落下，下一轮会照着它走。合进来之后，这五样东西
    **同时存在或同时不存在**。

    审核台账刻意**不**并进来：它故意吞异常（台账记不上是一件遗憾，把一条
    已经说完的回答变成错误不是），塞进主事务会让一次台账写失败回滚掉整轮。
    """
    if not outcome.spoken:
        # 一个字都没有就什么都不落，留档也不落：一份配不上任何回答的
        # 「凭什么是这几节」，下一轮会照着它的锁定走。
        return

    # 比对引文要扫几节正文，是纯 CPU 活，放事件循环上做会卡住别人的请求。
    # 残缺的回答照样比对：已经吐出来的那几句里可能就有一条完整的引文。
    traced = {}
    if outcome.sources is not None:
        for said in outcome.spoken:
            traced[said.persona] = await asyncio.to_thread(
                find, said.text, outcome.sources
            )

    # 自己开一条工作单元：请求那条会随响应结束而关掉，而这一步可能发生在
    # 客户端早就断开之后（生成还在跑，钱已经花了，内容还是要保住）。
    async with Detached(label=f"回答落库 会话{outcome.conversation_id}") as session:
        # **先锁会话行、后碰章节行**。重解析那个事务也是这个顺序（先清会话的锁定、
        # 后换章节，见 `resettle.swap`）；两边顺序相反的话，轮中重解析时会互相
        # 等对方手里的锁，库只能杀掉一边——杀到这边，就是丢一条已付费的回答。
        # 号也在这把锁下发。
        seq = await _allocate_seq(session, outcome.conversation_id)
        # 这一轮挑章、读正文可能在一次重解析**之前**，落库在它之后：手里的 chapter id
        # 可能已经不存在了。照写会撞外键、整轮落不下来。锁拿到之后再查（新的一条
        # 语句，看得见刚提交的重解析），不在的一律置空——和 `ON DELETE SET NULL` 同效
        alive = await _alive_chapters(session, _chapter_ids_in(outcome, traced))
        await _write_choice(session, outcome, alive)
        for offset, said in enumerate(outcome.spoken):
            usage = said.reply.usage
            message = Message(
                conversation_id=outcome.conversation_id,
                # 一个事务里顺序发，不是每条各算一次 —— 那样三条会算出同一个号
                seq=seq + offset,
                turn=outcome.turn,
                role="assistant",
                persona=said.persona,
                content=said.text,
                complete=not said.error,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read=usage.cache_read,
                cache_write=usage.cache_write,
                stop_reason=said.reply.stop_reason,
            )
            session.add(message)
            # flush 一次拿自增 id —— 引文要挂到这条回答上。
            # 和回答同一个事务：引文是回答的一部分，不该出现「回答在库里、
            # 引文没进去」这种半截状态。
            await session.flush()
            session.add_all(
                Citation(
                    message_id=message.id,
                    chapter_id=one.chapter_id if one.chapter_id in alive else None,
                    chapter_title=one.chapter_title,
                    quote=one.quote,
                    para_index=one.para_index,
                )
                for one in traced.get(said.persona, [])
            )


async def _write_choice(
    session: AsyncSession, outcome: Outcome, alive: set[int]
) -> None:
    """选章留档 + 会话锁定。跑在 `_settle` 那个事务里。

    复用锁定的轮次什么都不写：它们没有做决定，替它们编一行出来，那张表就不再
    是「决策留档」，而是一份每轮复制粘贴的流水。
    """
    choice = outcome.choice
    if choice is None or not choice.records:
        return
    await RetrievalRepository(session).save(
        conversation_id=outcome.conversation_id,
        turn=outcome.turn,
        summary={
            "budget_chars": choice.budget_chars,
            "used_chars": choice.used_chars,
            "selected_count": len(choice.selected_ids),
            "total_count": choice.total_count,
            "whole_book": choice.whole_book,
            "query_terms": list(choice.query_terms),
        },
        items=[
            {
                "chapter_id": item.chapter_id if item.chapter_id in alive else None,
                "chapter_idx": item.chapter_idx,
                "chapter_title": item.chapter_title,
                "rank": item.rank,
                "score": item.score,
                "word_count": item.word_count,
                "selected": item.selected,
                "matched_terms": [
                    {"term": term, "count": count} for term, count in item.matched_terms
                ],
            }
            for item in choice.items
        ],
    )
    # 真挑过章才锁。整本装得下的书下一轮照样装得下，锁它只会在重解析之后
    # 变成一份过期的名单；重选之后若变成装得下，也要把旧锁清掉。
    # 只锁活着的那几节：一个都不剩时锁定为空，下一轮由 `admit` 认出「被清掉了」
    await session.execute(
        update(Conversation)
        .where(Conversation.id == outcome.conversation_id)
        .values(
            selected_chapter_ids=[]
            if choice.whole_book
            else [i for i in choice.selected_ids if i in alive]
        )
    )


def _chapter_ids_in(outcome: Outcome, traced: dict) -> set[int]:
    """这一轮落库要写到的全部 chapter id：选章明细、锁定、引文。"""
    ids: set[int] = set()
    if outcome.choice is not None:
        ids.update(item.chapter_id for item in outcome.choice.items)
        ids.update(outcome.choice.selected_ids)
    for found in traced.values():
        ids.update(one.chapter_id for one in found if one.chapter_id is not None)
    return ids


async def _alive_chapters(session: AsyncSession, ids: set[int]) -> set[int]:
    if not ids:
        return set()
    rows = await session.scalars(select(Chapter.id).where(Chapter.id.in_(ids)))
    return set(rows)
