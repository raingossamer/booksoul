"""会话的轮次状态：一轮能不能开、开了拿什么开、收尾怎么一起落。

这里测的几件事，走错了都不报错，只在「两件事恰好同时发生」时才现形：

* seq 持会话行锁发放 —— 不持锁，另一处的写和这一处算出同一个号，
  症状是偶发的 500，或者一条已经付过钱的回答没能存下来
* 一轮收尾的那一个事务 —— 五样东西同时在或同时不在，先落库、后宣布
* 本轮告示 —— 「这本书重新解析过了，这次按全价算」恰好在真该说的时候最容易
  被别的话盖掉；也恰好在不该说的时候（书没动过）最容易被误报
* 准入 —— 一个会话同一时刻只有一轮；同一个问题挂上去回放、不再付一次钱，
  另一个问题拿 409；占位的那一轮不管怎么结束都要让位
* 轮中重解析 —— 这一轮手里的章节 id 在落库时可能已经不在了；两个事务碰会话行与
  章节行的先后要一致，否则互等到库杀掉一边

并发的时序靠**外来事务持锁**来造，不靠 sleep 赌：外来事务先锁住会话行、写进
下一个号，被测方来了就只能排队；看见它排上了，再让外来事务提交。
"""

from __future__ import annotations

import asyncio
import threading

from fakes import FAKE_KEY, FakeBrain
from sqlalchemy import func, select, text
from test_retrieval import ABOUT_GOTAMA, _ask, _open

from booksoul.brain import Reply
from booksoul_api.db.session import get_session_factory
from booksoul_api.models.conversation import Conversation
from booksoul_api.models.message import Message
from booksoul_api.models.retrieval import Retrieval


async def _until_someone_waits_on_a_lock(timeout: float = 5.0) -> None:
    """等到库里有一条语句在锁上排队。

    查 `pg_locks` 而不是 `pg_stat_activity`：后者在一个事务里只取一次快照，
    轮询它会一直看到第一眼的样子。每次开一条新 session 查，理由相同。
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        async with get_session_factory()() as probe:
            waiting = await probe.scalar(
                text("SELECT count(*) FROM pg_locks WHERE NOT granted")
            )
        if waiting:
            return
        if loop.time() > deadline:
            raise AssertionError(f"等了 {timeout} 秒，没有谁在锁上排队")
        await asyncio.sleep(0.02)


async def _hold_the_conversation(other, conversation_id: int, *, seq: int) -> None:
    """外来事务：锁住会话行，并写进下一个号（不提交）。"""
    await other.execute(
        select(Conversation.id)
        .where(Conversation.id == conversation_id)
        .with_for_update()
    )
    other.add(
        Message(
            conversation_id=conversation_id,
            seq=seq,
            turn=1,
            role="user",
            content="另一处写进来的一条",
            # 标成残缺：残缺行不进 `(会话, 轮, 人格)` 那条部分唯一索引。
            # 这里要让两边撞上的只该是 seq
            complete=False,
        )
    )
    await other.flush()


async def _seqs_elsewhere(conversation_id: int) -> int:
    """另开一条 session 看已经提交了几号。被测那条 session 这时可能正被流占着。"""
    async with get_session_factory()() as probe:
        last = await probe.scalar(
            select(func.max(Message.seq)).where(
                Message.conversation_id == conversation_id
            )
        )
    return int(last or 0)


class _GatedModel:
    """让模型吐完字之后停住，直到测试放行。

    模型调用跑在工作线程里，所以用 `threading.Event` 而不是 `asyncio.Event`：
    后者在线程里等一个别的循环上的对象，永远等不到。
    """

    def __init__(self, monkeypatch) -> None:
        self.talking = threading.Event()  # 已经开口了
        self.go = threading.Event()  # 放行
        gate = self

        def gated_chat(brain, system, messages, *, on_text=None):
            FakeBrain.calls.append({"system": system, "messages": messages})
            for chunk in FakeBrain.script:
                on_text(chunk)
            gate.talking.set()
            gate.go.wait(timeout=5)
            return Reply(text="".join(FakeBrain.script), stop_reason="end_turn")

        monkeypatch.setattr(FakeBrain, "chat", gated_chat)

    async def until_talking(self) -> None:
        await asyncio.to_thread(self.talking.wait, 5)


async def _seqs(db_session, conversation_id: int) -> list[int]:
    await db_session.commit()  # 丢掉本 session 的快照：别处的写要看得见
    rows = await db_session.scalars(
        select(Message.seq)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.seq)
    )
    return list(rows)


# ------------------------------------------------------------------ 发号


async def test_写提问要等会话行上的锁_放开之后接着发号(db_session, fake_brain):
    """别处正锁着会话行、写着下一个号：提问要排队，而不是算出同一个号去撞。

    不持锁的话，提问读不到那条还没提交的写，算出同一个号，然后在唯一约束上
    等它提交、再以一个 500 收场——用户只看到「问不出去」。
    """
    service, user, book, conversation = await _open(db_session)

    async with get_session_factory()() as other:
        await _hold_the_conversation(other, conversation.id, seq=1)
        asking = asyncio.create_task(_ask(service, conversation, user, ABOUT_GOTAMA))
        await _until_someone_waits_on_a_lock()
        await other.commit()

    events = await asyncio.wait_for(asking, timeout=10)

    assert events[-1][0] == "done", events[-1]
    assert await _seqs(db_session, conversation.id) == [1, 2, 3]


async def test_写回答也要等会话行上的锁_钱花了的回答不能因为撞号丢掉(
    db_session, fake_brain, monkeypatch
):
    """回答那一侧更要紧：撞了号，丢的是一条已经付过钱的回答。

    用的是**复用锁定**的第二轮：它不写选章留档、也不动会话那一行，于是落库时
    没有任何东西顺手替它锁住会话行——第一轮不会暴露这个问题，第二轮才会。
    """
    service, user, book, conversation = await _open(db_session)
    await _ask(service, conversation, user, ABOUT_GOTAMA)  # 第一轮：挑章并锁定

    model = _GatedModel(monkeypatch)
    asking = asyncio.create_task(_ask(service, conversation, user, "他后来怎样了？"))
    await model.until_talking()
    head = await _seqs_elsewhere(conversation.id)

    async with get_session_factory()() as other:
        await _hold_the_conversation(other, conversation.id, seq=head + 1)
        model.go.set()
        await _until_someone_waits_on_a_lock()
        await other.commit()

    events = await asyncio.wait_for(asking, timeout=10)

    assert events[-1][0] == "done", events[-1]
    seqs = await _seqs(db_session, conversation.id)
    assert seqs == list(range(1, len(seqs) + 1)), seqs
    assert len(seqs) == 5, "两轮各一问一答，外加外来的那一条"


# ------------------------------------------------------------------ 收尾落库


async def _answers(conversation_id: int) -> list[Message]:
    async with get_session_factory()() as probe:
        rows = await probe.scalars(
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.role == "assistant",
            )
            .order_by(Message.seq)
        )
        return list(rows)


async def test_账单那几个数跟着回答一起落下(db_session, fake_brain):
    """⑦屏底下那条仪表读的就是它们。落不下来，仪表就只能编。"""
    service, user, book, conversation = await _open(db_session)

    await _ask(service, conversation, user, ABOUT_GOTAMA)

    [answer] = await _answers(conversation.id)
    assert (answer.input_tokens, answer.output_tokens, answer.cache_read) == (
        13_204,
        286,
        12_904,
    )
    assert answer.stop_reason == "end_turn"


async def test_一个字都没说出来就什么都不落_连选章留档也不落(db_session, fake_brain):
    """空消息比没有消息更糟：界面上它看起来像「模型答了个空」，

    而真实情况是「还没开口就断了」。留档也一样：一份配不上任何回答的
    「凭什么是这几节」，下一轮会照着它的锁定走。
    """
    fake_brain.raise_after = 0
    service, user, book, conversation = await _open(db_session)

    events = await _ask(service, conversation, user, ABOUT_GOTAMA)

    assert events[-1][0] == "error"
    assert await _answers(conversation.id) == []
    async with get_session_factory()() as probe:
        assert not await probe.scalar(
            select(func.count())
            .select_from(Retrieval)
            .where(Retrieval.conversation_id == conversation.id)
        )


async def test_一轮落库时消息只INSERT不UPDATE(db_session, fake_brain):
    """一边流一边 UPDATE 是最容易想到的写法，代价是一次回答几百次写库。

    这条盯着 SQL 本身，不是盯着结果——结果对而写法错，下次就会被改回去。
    """
    from sqlalchemy import event

    from booksoul_api.db.session import get_engine

    service, user, book, conversation = await _open(db_session)
    statements: list[str] = []

    def record(_conn, _cursor, statement, *_args):
        head = statement.strip().split()[0].upper()
        if "messages" in statement.lower() and head in {"INSERT", "UPDATE"}:
            statements.append(head)

    engine = get_engine().sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        await _ask(service, conversation, user, ABOUT_GOTAMA)
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert statements == ["INSERT", "INSERT"], "一条提问、一条回答"


async def test_收到done的那一刻回答已经在库里(db_session, fake_brain):
    """先宣布结束再落库的话，客户端收到 done 转头去拉会话，

    看到的是一个没有回答的问题——一条它刚刚亲眼看着流完的回答。
    """
    service, user, book, conversation = await _open(db_session)
    stream = await service.ask(
        conversation_id=conversation.id,
        user_id=user.id,
        question=ABOUT_GOTAMA,
        brain_key=FAKE_KEY,
    )

    at_done: list[int] = []
    async for name, _ in stream:
        if name == "done":
            at_done.append(len(await _answers(conversation.id)))

    assert at_done == [1]


# ------------------------------------------------------------------ 告示


async def _wipe_locks(db_session, book_id: int) -> None:
    """重解析的善后就是这一句：旧章节没了，会话的选章锁定清空。"""
    from booksoul_api.services import turn_state

    await turn_state.forget_locks(db_session, book_id=book_id)
    await db_session.commit()


def _notices(events) -> list[dict]:
    return [payload for name, payload in events if name == "notice"]


async def test_重解析之后的下一问_告示在第一个字之前送到(db_session, fake_brain):
    """真重解析 → 真重挑 → 真按全价重算：这正是最该说一声的时候。

    它原先走 `stage`，而 `stage` 只有一个「现在在做什么」的位置——紧随其后的
    「正在挑章节…」当场就把它盖掉了。告示有自己的通道，不会被盖。
    """
    service, user, book, conversation = await _open(db_session)
    await _ask(service, conversation, user, ABOUT_GOTAMA)
    await _wipe_locks(db_session, book.id)

    events = await _ask(service, conversation, user, "那他后来又去了哪里？")

    [notice] = _notices(events)
    assert notice["code"] == "reparsed"
    assert "重新解析" in notice["text"]
    names = [name for name, _ in events]
    assert names.index("notice") < names.index("token")


async def test_晚到的订阅者经回放也拿到告示(db_session, fake_brain, monkeypatch):
    """重连回来的人同样要知道这一轮为什么贵——他错过的恰好就是开头那一句。"""
    service, user, book, conversation = await _open(db_session)
    await _ask(service, conversation, user, ABOUT_GOTAMA)
    await _wipe_locks(db_session, book.id)

    model = _GatedModel(monkeypatch)
    question = "那他后来又去了哪里？"
    first = asyncio.create_task(_ask(service, conversation, user, question))
    await model.until_talking()
    # 重连：另一个请求、另一条 session，同一个问题，挂上正在跑的那一轮
    again = await _stream_on_own_session(conversation.id, user.id, question)
    model.go.set()
    late = [event async for event in again]
    await asyncio.wait_for(first, timeout=10)

    assert [n["code"] for n in _notices(late)] == ["reparsed"]


async def test_整本装得下的书第二轮没有告示(db_session, fake_brain):
    """`selected_chapter_ids` 为空有两种来路，它们在库里长得一模一样。

    整本装得下的书**本来就锁空的**，和重解析的善后把它**清掉了**，都是 `[]`。
    靠「问过好几轮却没有锁定」反推，这两件事被压成同一个值——于是整本装得下的
    每一本书，从第二轮起都会收到一句「这本书重新解析过了，按全价算」。
    书没动过，缓存也没作废，那笔钱不存在。
    """
    service, user, book, conversation = await _open(db_session, budget=10**6)
    await _ask(service, conversation, user, ABOUT_GOTAMA)

    events = await _ask(service, conversation, user, "那他后来又去了哪里？")

    assert _notices(events) == []


async def test_上一轮什么都没落下时没有告示(db_session, fake_brain):
    """全失败的那一轮一个字都没落、留档也没落。下一轮不该因此被当成重解析过——

    否则用户在一次失败之后会被告知「这本书重新解析过了」，一件没发生过的事。
    """
    fake_brain.raise_after = 0
    service, user, book, conversation = await _open(db_session)
    await _ask(service, conversation, user, ABOUT_GOTAMA)
    fake_brain.raise_after = None

    events = await _ask(service, conversation, user, ABOUT_GOTAMA)

    assert events[-1][0] == "done"
    assert _notices(events) == []


# ------------------------------------------------------------------ 准入


async def _stream_on_own_session(conversation_id: int, user_id: int, question: str):
    """另一个请求：自己的一条 session，拿到流就返回（流本身不再用 session）。"""
    from booksoul_api.services.chat_service import ChatService

    async with get_session_factory()() as session:
        return await ChatService(session).ask(
            conversation_id=conversation_id,
            user_id=user_id,
            question=question,
            brain_key=FAKE_KEY,
        )


async def _said(stream) -> tuple[str, str]:
    """(说出来的字, 整轮的结局)"""
    events = [event async for event in stream]
    text = "".join(p["text"] for n, p in events if n == "token")
    return text, events[-1][0]


async def test_两个标签页同时问同一个问题_模型只调一次(
    db_session, fake_brain, monkeypatch
):
    """Spec #39：同一个问题不能让用户付两次钱。

    原先「有没有一轮在跑」的检查与登记之间隔着七八次查库和一次提交，两个几乎
    同时到的请求都能穿过检查：要么第二个撞唯一约束变成 500，要么两个都活下来、
    模型被调两次。
    """
    service, user, book, conversation = await _open(db_session)
    model = _GatedModel(monkeypatch)

    first, second = await asyncio.gather(
        _stream_on_own_session(conversation.id, user.id, ABOUT_GOTAMA),
        _stream_on_own_session(conversation.id, user.id, ABOUT_GOTAMA),
    )
    model.go.set()
    heard = await asyncio.gather(_said(first), _said(second))

    assert len(FakeBrain.calls) == 1, "同一个问题只许问模型一次"
    assert heard[0] == heard[1]
    assert heard[0][1] == "done"
    assert await _seqs(db_session, conversation.id) == [1, 2], "一问一答"


async def test_一个问题正在答时问另一个_拿到409而不是第二轮(
    db_session, fake_brain, monkeypatch
):
    from booksoul_api.core.errors import ConflictError

    service, user, book, conversation = await _open(db_session)
    model = _GatedModel(monkeypatch)

    got = await asyncio.gather(
        _stream_on_own_session(conversation.id, user.id, ABOUT_GOTAMA),
        _stream_on_own_session(conversation.id, user.id, "他后来怎样了？"),
        return_exceptions=True,
    )
    model.go.set()

    refused = [g for g in got if isinstance(g, BaseException)]
    streams = [g for g in got if not isinstance(g, BaseException)]
    assert len(refused) == 1 and isinstance(refused[0], ConflictError), refused
    assert [done for _, done in [await _said(s) for s in streams]] == ["done"]
    assert len(FakeBrain.calls) == 1
    assert await _seqs(db_session, conversation.id) == [1, 2], "库里只有一条提问"


async def test_上一轮正在落库时同一个问题再来_挂上去而不是再付一次(
    db_session, fake_brain, monkeypatch
):
    """原先那一轮在落库**之前**就把自己从「正在跑」里摘掉了。

    落库要比对引文、写库，秒级；这几秒里同一个问题再来（比如前端断线重连），
    服务端以为没有在跑的一轮，于是又开一轮、又调一次模型。这里把引文比对
    卡住来撑开这几秒。
    """
    from booksoul_api.services import turn_state

    matching = threading.Event()
    go_on = threading.Event()
    real_find = turn_state.find

    def slow_find(text, sources):
        matching.set()
        go_on.wait(timeout=5)
        return real_find(text, sources)

    monkeypatch.setattr(turn_state, "find", slow_find)
    service, user, book, conversation = await _open(db_session)

    first = await _stream_on_own_session(conversation.id, user.id, ABOUT_GOTAMA)
    leader = asyncio.create_task(_said(first))
    await asyncio.to_thread(matching.wait, 5)  # 首领正在落库

    again = await _stream_on_own_session(conversation.id, user.id, ABOUT_GOTAMA)
    go_on.set()
    heard = await asyncio.gather(leader, _said(again))

    assert len(FakeBrain.calls) == 1, "落库那几秒里重连，不许再调一次模型"
    assert heard[0] == heard[1]
    assert heard[1][1] == "done"


async def test_首领在准备阶段被拦下_等着的那个拿到自己的4xx而不是干等(
    db_session, fake_brain
):
    """同一个问题的两个请求，前一个占了位，却在准备阶段被逃生门拦下（没花钱）。

    后一个在席位前面等着。首领失败时若只让位、不告诉等着的人，后一个就永远
    等下去；把一条空流交给它也不对——它该拿到和首领一样的那句话。

    首领卡住的办法：外来事务锁住 `messages` 表，首领占位之后第一件事就是读历史，
    于是停在那里；后一个请求走到席位前面之前一张 `messages` 都不碰。
    """
    from test_retrieval import ABOUT_KAMALA

    from booksoul_api.core.errors import ReselectRequired

    service, user, book, conversation = await _open(db_session)
    await _ask(service, conversation, user, ABOUT_GOTAMA)  # 挑两节并锁定
    await db_session.commit()  # 别让手边这条 session 拿着表上的锁

    async with get_session_factory()() as other:
        await other.execute(text("LOCK TABLE messages IN ACCESS EXCLUSIVE MODE"))
        leader = asyncio.create_task(
            _stream_on_own_session(conversation.id, user.id, ABOUT_KAMALA)
        )
        await _until_someone_waits_on_a_lock()
        follower = asyncio.create_task(
            _stream_on_own_session(conversation.id, user.id, ABOUT_KAMALA)
        )
        await asyncio.sleep(0.3)  # 让它走到席位前面等着
        await other.commit()

    got = await asyncio.wait_for(
        asyncio.gather(leader, follower, return_exceptions=True), timeout=10
    )

    assert all(isinstance(g, ReselectRequired) for g in got), got
    assert len(FakeBrain.calls) == 1, "只有第一轮调过模型"


async def test_后台任务还没跑起来就被取消_席位也要让出来(db_session, fake_brain):
    """让位写在收尾那条路上——而一个还没跑起来就被取消的任务根本走不到那里。

    服务关停时事件循环就是这么做的：把还没跑的任务一并取消。不在别处兜住，
    这个会话从此只剩 409（「上一个问题还在回答」），而那个问题永远答不完。
    """
    service, user, book, conversation = await _open(db_session)
    await service.ask(  # 拿到流但不读：后台任务此刻还一步都没跑
        conversation_id=conversation.id,
        user_id=user.id,
        question=ABOUT_GOTAMA,
        brain_key=FAKE_KEY,
    )
    for task in asyncio.all_tasks() - {asyncio.current_task()}:
        task.cancel()
    for _ in range(3):
        await asyncio.sleep(0)  # 让取消与它的回调走完

    stream = await _stream_on_own_session(conversation.id, user.id, "他后来怎样了？")

    assert (await _said(stream))[1] == "done"


# ------------------------------------------------------------------ 锁定


async def _lock_to(db_session, conversation_id: int, ids: list[int]) -> None:
    """把会话的锁定改成这份名单。死 id 就是这么来的：锁定落下之后，章节被换掉了。"""
    from sqlalchemy import update

    await db_session.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(selected_chapter_ids=ids)
    )
    await db_session.commit()


def _picked(events) -> bool:
    """这一轮是不是真的重挑过：真挑过才会报「挑好了」。"""
    return any(
        name == "stage" and str(p.get("text", "")).startswith("挑好了")
        for name, p in events
    )


async def test_锁定里的章节全都不在了_说一声并且重挑(db_session, fake_brain):
    """锁定落下之后章节被换掉了（比如一轮正跑着时重解析），名单里一个活的都没有。

    原先的判据看见「锁定非空」就说「没被清」，于是这一轮悄悄按全价重挑、一句不说
    ——和重解析之后清空锁定的那种情形花的是同一笔钱，却没有告示。
    """
    service, user, book, conversation = await _open(db_session)
    await _ask(service, conversation, user, ABOUT_GOTAMA)
    await _lock_to(db_session, conversation.id, [9991, 9992])

    events = await _ask(service, conversation, user, "那他后来又去了哪里？")

    assert [n["code"] for n in _notices(events)] == ["reparsed"]
    assert _picked(events), "一个活的都没有，只能重挑"


async def test_锁定里只死了一部分_用活着的那几节_不重挑也不告示(db_session, fake_brain):
    service, user, book, conversation = await _open(db_session)
    await _ask(service, conversation, user, ABOUT_GOTAMA)
    await db_session.refresh(conversation)
    alive = list(conversation.selected_chapter_ids)[:1]
    await _lock_to(db_session, conversation.id, [*alive, 9999])

    events = await _ask(service, conversation, user, "那他后来又去了哪里？")

    assert events[-1][0] == "done"
    assert _notices(events) == []
    assert not _picked(events), "还有活的就照锁定走：换一套章节缓存就全废了"


# ------------------------------------------------------------------ 轮中重解析


async def _same_chapters(books, book_id: int) -> list[dict]:
    """这本书现有章节的记录：换上去正文不变，只是 id 全新。"""
    return [
        {
            "idx": c.idx,
            "title": c.title,
            "level": c.level,
            "word_count": c.word_count,
            "file_path": c.file_path,
            "source_id": c.source_id,
        }
        for c in await books.list_chapters(book_id)
    ]


async def _replace_chapters_elsewhere(book) -> None:
    """另一条 session 上把这本书的章节整张换掉并提交：旧 chapter id 全都不在了。

    这就是重解析在库里留下的样子（正文没变，id 全新）。
    """
    from pathlib import Path

    from booksoul_api.core.config import get_settings
    from booksoul_api.repositories.book_repo import BookRepository
    from booksoul_api.services import resettle

    async with get_session_factory()() as other:
        await resettle.swap(
            other,
            book_id=book.id,
            records=await _same_chapters(BookRepository(other), book.id),
            root=Path(get_settings().library_root) / book.storage_path,
        )
        await other.commit()


async def test_一轮正跑着时书被重解析了_钱花了的回答照样落下(
    db_session, fake_brain, monkeypatch
):
    """这一轮挑章、读正文都在重解析之前，落库在之后：它手里的 chapter id 已经不存在了。

    `retrieval_items` 与 `citations` 的 `chapter_id` 都外键指向 `chapters`。照原样写，
    整轮事务撞外键失败——用户看到「这一轮没能存下来」，钱已经花了。死 id 要像
    `ON DELETE SET NULL` 那样置空：引文降级成「原位置已变更」，而不是连回答一起丢掉。
    """
    fake_brain.script = ["> 他们见到了佛陀乔达摩，佛陀讲说法义\n\n", "这是原话。"]
    model = _GatedModel(monkeypatch)
    service, user, book, conversation = await _open(db_session)

    asking = asyncio.create_task(_ask(service, conversation, user, ABOUT_GOTAMA))
    await model.until_talking()  # 已经挑完章、读完正文，正在说
    await _replace_chapters_elsewhere(book)
    model.go.set()
    events = await asyncio.wait_for(asking, timeout=10)

    assert events[-1][0] == "done", events[-1]
    [answer] = await _answers(conversation.id)
    assert answer.content.startswith("> 他们见到了佛陀乔达摩")
    detail = await service.detail(conversation_id=conversation.id, user_id=user.id)
    [cited] = detail.messages[-1].citations
    assert cited.chapter_id is None, "原来那一节已经不在了，不能挂在一个不存在的 id 上"
    # 锁定只写活着的那部分：旧 id 一个都不剩，锁定为空——下一问会说一声并重挑
    later = await _ask(service, conversation, user, "那他后来又去了哪里？")
    assert [n["code"] for n in _notices(later)] == ["reparsed"]


async def test_重解析与收尾同时进行_两边同序拿锁_不死锁也不丢回答(
    db_session, fake_brain, monkeypatch
):
    """重解析那个事务还没提交时，这一轮开始落库。

    两边都要碰会话行（清锁定 / 写锁定与发号）和章节行（删 / 外键引用）。顺序相反
    的话——重解析先删章节、后清锁定，收尾先锁会话、后引用章节——两边会互相等
    对方手里的锁，库只能杀掉一边；杀到收尾那边，就是丢一条已付费的回答。
    同序之后只会排队：收尾等重解析提交，再把已经不在的章节 id 置空。

    重解析那一步是一次 `resettle.swap()`，锁的先后在它里面（`test_book_lifecycle`
    那条「换章节之前已经握着会话行」钉着）；这里看的是两边同序时的结局。
    """
    from pathlib import Path

    from booksoul_api.core.config import get_settings
    from booksoul_api.repositories.book_repo import BookRepository
    from booksoul_api.services import resettle

    fake_brain.script = ["> 他们见到了佛陀乔达摩，佛陀讲说法义\n\n", "这是原话。"]
    model = _GatedModel(monkeypatch)
    service, user, book, conversation = await _open(db_session)
    asking = asyncio.create_task(_ask(service, conversation, user, ABOUT_GOTAMA))
    await model.until_talking()

    async with get_session_factory()() as parse:  # 解析任务那条 session
        await resettle.swap(
            parse,
            book_id=book.id,
            records=await _same_chapters(BookRepository(parse), book.id),
            root=Path(get_settings().library_root) / book.storage_path,
        )
        model.go.set()  # 这一轮说完了，开始落库——撞上还没提交的重解析
        await _until_someone_waits_on_a_lock()
        await parse.commit()

    events = await asyncio.wait_for(asking, timeout=10)

    assert events[-1][0] == "done", events[-1]
    [answer] = await _answers(conversation.id)
    detail = await service.detail(conversation_id=conversation.id, user_id=user.id)
    assert [c.chapter_id for c in detail.messages[-1].citations] == [None]


async def test_书正在解析时不开新的一轮(db_session, fake_brain):
    """解析任务正在重写这本书的目录与章节：这时候开一轮，读到的是一本写到一半的书。

    还没花钱就拦下来——提问不落库、模型不调——并且说清楚等什么。
    """
    from sqlalchemy import update

    from booksoul_api.core.errors import ConflictError
    from booksoul_api.models.book import Book

    service, user, book, conversation = await _open(db_session)
    await db_session.execute(
        update(Book).where(Book.id == book.id).values(status="parsing")
    )
    await db_session.commit()

    try:
        await _ask(service, conversation, user, ABOUT_GOTAMA)
    except ConflictError as refused:
        assert "解析" in str(refused)
        # 前端要按它分支：等一会儿再问，而不是去详情页
        assert refused.code == "book_parsing"
    else:
        raise AssertionError("书在解析中，不该开出一轮")

    assert fake_brain.calls == []
    assert await _seqs(db_session, conversation.id) == [], "提问也不落库"


# ------------------------------------------------------------------ 删会话
#
# 删和一轮收尾抢的是同一个东西：收尾要往这个会话里写回答。删在它前面，一条
# 已经付过钱的回答就无处可落。所以删也要占席位——和提问同一张表、同一条规矩。


async def _delete_on_own_session(conversation_id: int, user_id: int) -> None:
    from booksoul_api.services.chat_service import ChatService

    async with get_session_factory()() as session:
        await ChatService(session).delete(
            conversation_id=conversation_id, user_id=user_id
        )


async def _still_there(conversation_id: int) -> bool:
    async with get_session_factory()() as probe:
        return (
            await probe.scalar(
                select(Conversation.id).where(Conversation.id == conversation_id)
            )
            is not None
        )


async def test_一轮正在答时删会话_拿到409_钱花了的回答照样落下(
    db_session, fake_brain, monkeypatch
):
    from booksoul_api.core.errors import ConflictError

    service, user, book, conversation = await _open(db_session)
    model = _GatedModel(monkeypatch)
    stream = await _stream_on_own_session(conversation.id, user.id, ABOUT_GOTAMA)
    listening = asyncio.create_task(_said(stream))
    await model.until_talking()

    try:
        await _delete_on_own_session(conversation.id, user.id)
    except ConflictError as refused:
        assert "回答" in str(refused)
    else:
        raise AssertionError("正在答的会话不该删得掉")

    model.go.set()
    _, ending = await listening
    assert ending == "done"
    assert await _seqs(db_session, conversation.id) == [1, 2], "一问一答都落下了"

    # 说完了就删得掉
    await _delete_on_own_session(conversation.id, user.id)
    assert not await _still_there(conversation.id)


async def test_删到一半时来的提问_拿到409说的是在删_而不是上一个问题(
    db_session, fake_brain
):
    """删也占着席位：这一小会儿里来的提问被挡在门外，而且被告知的是实情。

    时序靠外来事务锁住会话行来撑开：删除占了席位、卡在那一行的锁上。
    """
    from booksoul_api.core.errors import ConflictError

    service, user, book, conversation = await _open(db_session)

    async with get_session_factory()() as other:
        await other.execute(
            select(Conversation.id)
            .where(Conversation.id == conversation.id)
            .with_for_update()
        )
        deleting = asyncio.create_task(_delete_on_own_session(conversation.id, user.id))
        await _until_someone_waits_on_a_lock()

        try:
            # 限时：删除要是没占席位，提问会排到同一把锁上，一直等到外来事务放手
            # ——而放手要等这里返回。等不到就是失败，不是挂住整个测试
            await asyncio.wait_for(
                _stream_on_own_session(conversation.id, user.id, ABOUT_GOTAMA),
                timeout=5,
            )
        except ConflictError as refused:
            assert "删" in str(refused), refused
        else:
            raise AssertionError("删除占着席位，提问不该开出一轮")
        await other.commit()

    await asyncio.wait_for(deleting, timeout=10)
    assert not await _still_there(conversation.id)
    assert fake_brain.calls == []


async def test_会话在查到之后被删了_提问拿到404而不是500(
    db_session, fake_brain, monkeypatch
):
    """提问先查会话（`_require`），再准备一堆东西，最后才占位。

    这中间会话被删了：锁会话行那一步什么都锁不到，原先接着 `.one()` 抛
    `NoResultFound`——一个 500。它就是没有了，该说没有了。
    """
    from sqlalchemy import delete

    from booksoul_api.core.errors import NotFoundError
    from booksoul_api.services import turn_state

    service, user, book, conversation = await _open(db_session)
    real_facts = turn_state._facts

    async def deleted_just_before(session, conversation_id):
        async with get_session_factory()() as elsewhere:
            await elsewhere.execute(
                delete(Conversation).where(Conversation.id == conversation_id)
            )
            await elsewhere.commit()
        return await real_facts(session, conversation_id)

    monkeypatch.setattr(turn_state, "_facts", deleted_just_before)

    try:
        await _stream_on_own_session(conversation.id, user.id, ABOUT_GOTAMA)
    except NotFoundError:
        pass
    else:
        raise AssertionError("会话已经没了，不该开出一轮")
    assert fake_brain.calls == []
