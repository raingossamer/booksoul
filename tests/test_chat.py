"""对话：开会话、流式提问、历史、断流与重连（界面稿⑦屏）。

模型调用一律用替身，不打真实 API —— 这条是验收里写死的。替身不只是省钱：
「断流时已生成的部分要保住」这种行为，真模型根本没法按需触发。

这里盯的几件事，每一件都对应一笔真金白银：

* user 消息在**调用模型之前**落库 —— 否则断在半路，用户连自己问过什么都找不回来
* 同一个问题不会被问第二遍 —— 重连要回放，不要重新计费（Spec #39）
* 历史从 `messages` 灌回 —— 服务无状态，重启之后接着聊
"""

from __future__ import annotations

import asyncio
import threading

import pytest
from fakes import FAKE_KEY, FakeBrain
from sqlalchemy import select

from booksoul.brain import BrainError, Reply
from booksoul_api.core.errors import ApiError
from booksoul_api.models.book import Book
from booksoul_api.models.chapter import Chapter
from booksoul_api.models.message import Message
from booksoul_api.models.user import User
from booksoul_api.services.chat_service import ChatService


async def _reader_with_book(db_session) -> tuple[User, Book]:
    """一本真的写在磁盘上的书 —— 服务层要 `load_from_path` 把它读回来。"""
    user = User(email="reader@example.com", password_hash="不参与本测试")
    db_session.add(user)
    await db_session.flush()
    return user, await _book_on_disk(db_session, user)


async def _registered(db_session) -> User:
    """走 HTTP 的测试里，用户是 `_token` 注册出来的，不是这里造的。"""
    return await db_session.scalar(
        select(User).where(User.email == "reader@example.com")
    )


async def _book_on_disk(db_session, user: User) -> Book:
    """给一个已有的用户放一本真的写在磁盘上的书。"""
    import json
    from pathlib import Path

    from booksoul_api.core.config import get_settings

    # 目录名刻意和解析那条路产出的 `{user_id}/{book_id}-{slug}` 不同形：
    # TRUNCATE ... RESTART IDENTITY 之后每个测试的 user.id 都是 1，
    # 同名目录会让两个测试往同一个 chapters/ 里写文件，然后互相数错文件数。
    storage = f"{user.id}/chat-fixture-悉达多"
    directory = Path(get_settings().library_root) / storage
    (directory / "chapters").mkdir(parents=True, exist_ok=True)
    chapters = [
        {
            "序号": 1,
            "标题": "婆罗门之子",
            "文件": "chapters/001-婆罗门之子.md",
            "层级": 1,
        },
        {"序号": 2, "标题": "沙门", "文件": "chapters/002-沙门.md", "层级": 1},
    ]
    for record in chapters:
        (directory / record["文件"]).write_text(
            f"# {record['标题']}\n\n这一节讲{record['标题']}的事。", encoding="utf-8"
        )
    (directory / "meta.json").write_text(
        json.dumps(
            {"书名": "悉达多", "作者": "赫尔曼·黑塞", "章节": chapters},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    book = Book(
        user_id=user.id,
        title="悉达多",
        author="赫尔曼·黑塞",
        source_file="悉达多.epub",
        source_format="epub",
        status="ready",
        storage_path=storage,
        chapter_count=2,
        word_count=40,
    )
    db_session.add(book)
    await db_session.flush()
    db_session.add_all(
        Chapter(
            book_id=book.id,
            idx=r["序号"],
            title=r["标题"],
            level=1,
            word_count=20,
            file_path=r["文件"],
        )
        for r in chapters
    )
    await db_session.commit()
    return book


async def _drain(stream) -> list[tuple[str, dict]]:
    return [event async for event in stream]


async def test_开一个会话就把书人格与大脑一起锁下来(db_session):
    user, book = await _reader_with_book(db_session)

    conversation = await ChatService(db_session).start(
        user_id=user.id, book_id=book.id, persona="scholar", brain_name="claude"
    )

    assert conversation.book_id == book.id
    assert conversation.persona == "scholar"
    # 冗余存名字而不是外键：预设被改名或删掉都不该篡改历史记录的真实来源
    assert conversation.brain_name == "claude"
    assert conversation.model.startswith("claude")


async def test_别人的书开不出会话(db_session):
    from booksoul_api.core.errors import NotFoundError

    _, book = await _reader_with_book(db_session)
    stranger = User(email="stranger@example.com", password_hash="x")
    db_session.add(stranger)
    await db_session.flush()

    with pytest.raises(NotFoundError):
        await ChatService(db_session).start(
            user_id=stranger.id, book_id=book.id, persona="scholar", brain_name="claude"
        )


async def test_开会话连同第一问_会话与第一问一起落下(db_session, fake_brain):
    """网页上的会话从第一问开始存在（`begin`）。

    先建后问的话，点进「开始对话」又走掉就留下一个空会话——书库「最近问过的」
    里那一串「（还没问出第一个问题）」就是这么来的。
    """
    user, book = await _reader_with_book(db_session)
    service = ChatService(db_session)

    conversation_id, stream = await service.begin(
        user_id=user.id,
        book_id=book.id,
        persona="author",
        brain_name="claude",
        question="悉达多为什么拒绝追随佛陀？",
        brain_key=FAKE_KEY,
    )
    events = await _drain(stream)

    assert events[-1][0] == "done"
    detail = await service.detail(conversation_id=conversation_id, user_id=user.id)
    assert detail.persona == "author"
    assert detail.title == "悉达多为什么拒绝追随佛陀？"
    assert [(m.turn, m.role, m.persona) for m in detail.messages] == [
        (1, "user", None),
        (1, "assistant", "author"),
    ]
    assert len(fake_brain.calls) == 1


async def _committed_conversations() -> int:
    """另开一条 session 去数：只数得到**已经提交**的。

    请求那条 session 里 flush 了、没提交的，请求一结束 session 关掉就回滚了——
    用同一条 session 数，会把它也数进去。
    """
    from sqlalchemy import func

    from booksoul_api.db.session import get_session_factory
    from booksoul_api.models.conversation import Conversation

    async with get_session_factory()() as other:
        return await other.scalar(select(func.count()).select_from(Conversation))


async def test_第一问问不出去_会话一行也不留(db_session, fake_brain):
    """没填 key：服务端那句「到设置页填 key」，库里什么都不留。"""
    user, book = await _reader_with_book(db_session)

    with pytest.raises(ApiError):
        await ChatService(db_session).begin(
            user_id=user.id,
            book_id=book.id,
            persona="scholar",
            brain_name="claude",  # 要 key
            question="问题",
            brain_key="",
        )

    assert await _committed_conversations() == 0
    assert fake_brain.calls == []


async def test_写第一问之前出了错_会话也不在库里(db_session, fake_brain, monkeypatch):
    """会话与第一问是**同一次**提交，不是先把会话提交了、再去写问题。

    炸在占住会话行之后、写提问之前：先提交会话的写法在这里会留下一行空会话。
    """
    from booksoul_api.services import turn_state

    async def broken(*_args, **_kwargs):
        raise RuntimeError("写提问之前炸了")

    monkeypatch.setattr(turn_state, "_history_rows", broken)
    user, book = await _reader_with_book(db_session)

    with pytest.raises(RuntimeError):
        await ChatService(db_session).begin(
            user_id=user.id,
            book_id=book.id,
            persona="scholar",
            brain_name="claude",
            question="问题",
            brain_key=FAKE_KEY,
        )

    assert await _committed_conversations() == 0
    assert fake_brain.calls == []


async def test_问一轮逐字流出来并落库(db_session, fake_brain):
    user, book = await _reader_with_book(db_session)
    service = ChatService(db_session)
    conversation = await service.start(
        user_id=user.id, book_id=book.id, persona="scholar", brain_name="claude"
    )

    events = await _drain(
        await service.ask(
            conversation_id=conversation.id,
            user_id=user.id,
            question="悉达多为什么拒绝追随佛陀？",
            brain_key=FAKE_KEY,
        )
    )

    names = [name for name, _ in events]
    assert names[-1] == "done"
    assert names.count("token") >= 2  # 逐字流，不是一次性甩出来
    said = "".join(p["text"] for n, p in events if n == "token")
    assert said == "他拒绝的不是佛陀，而是把佛陀当作外在权威这条路径。"

    rows = await _messages(db_session, conversation.id)
    assert [(m.role, m.turn, m.complete) for m in rows] == [
        ("user", 1, True),
        ("assistant", 1, True),
    ]
    assert rows[0].persona is None  # user 消息的 persona 必须是 NULL，库里有 CHECK 钉着
    assert rows[1].persona == "scholar"
    assert rows[1].content == said
    assert rows[1].cache_read == 12_904


async def _messages(db_session, conversation_id: int) -> list[Message]:
    await db_session.commit()
    rows = await db_session.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.seq)
    )
    return list(rows.scalars())


async def test_提问在调用模型之前就落库(db_session, fake_brain, monkeypatch):
    """断在半路时，用户至少还找得回自己问过什么。

    先调模型再落提问，是最省事也最常见的写法：一次异常，那个问题就彻底没了。
    """
    user, book = await _reader_with_book(db_session)
    service = ChatService(db_session)
    conversation = await service.start(
        user_id=user.id, book_id=book.id, persona="scholar", brain_name="claude"
    )

    seen: list[int] = []

    def chat_and_peek(self, system, messages, *, on_text=None):
        """模型被调用的那一刻，库里已经有几条消息了？"""
        import os

        from sqlalchemy import create_engine, text
        from sqlalchemy.engine import make_url

        url = make_url(os.environ["BOOKSOUL_DATABASE_URL"]).set(
            drivername="postgresql+psycopg2"
        )
        engine = create_engine(url)
        with engine.connect() as conn:
            seen.append(
                conn.execute(
                    text("SELECT count(*) FROM messages WHERE conversation_id = :c"),
                    {"c": conversation.id},
                ).scalar_one()
            )
        engine.dispose()
        return Reply(text="", stop_reason="end_turn")

    monkeypatch.setattr(FakeBrain, "chat", chat_and_peek)
    await _drain(
        await service.ask(
            conversation_id=conversation.id,
            user_id=user.id,
            question="悉达多为什么拒绝追随佛陀？",
            brain_key=FAKE_KEY,
        )
    )

    assert seen == [1], "调模型时库里应当已经有那条提问"


async def test_断在半路时保住已经说出来的那一段(db_session, fake_brain):
    user, book = await _reader_with_book(db_session)
    fake_brain.script = ["他拒绝的不是佛陀，", "而是把佛陀", "当作外在权威。"]
    fake_brain.raise_after = 2  # 前两段吐出来了，第三段之前断

    service = ChatService(db_session)
    conversation = await service.start(
        user_id=user.id, book_id=book.id, persona="scholar", brain_name="claude"
    )

    events = await _drain(
        await service.ask(
            conversation_id=conversation.id,
            user_id=user.id,
            question="悉达多为什么拒绝追随佛陀？",
            brain_key=FAKE_KEY,
        )
    )

    assert events[-1][0] == "error"
    message = events[-1][1]["message"]
    assert "没答完" in message  # 网页上的一句人话，不是回溯
    assert "掐" not in message, "引擎那句话（连同对方的原话）不照转"

    rows = await _messages(db_session, conversation.id)
    answer = rows[-1]
    assert answer.complete is False
    assert answer.content == "他拒绝的不是佛陀，而是把佛陀"


# 对方说的话里可能有的东西：内网地址、半截 key。一个特征串把它们都代表了
_THEIRS = "对方原话-10.0.0.7:8080-sk-proj-9f2a"


@pytest.mark.parametrize(
    "kind", ["auth", "rate_limit", "not_found", "server", "network", ""]
)
async def test_提问时对方说的话一个字都不转给用户(
    db_session, fake_brain, monkeypatch, caplog, kind
):
    """自定义端点的地址是用户存的，可以指向内网：把对方响应里的字转回来，就是借服务器
    之手去窥探那里。列模型那条路早就不转了，提问这条路原先照转 `str(exc)`——里面有
    对方的错误响应体、连不上时的底层原因，还有写给命令行的出路（`--brain`、环境变量）。
    日志也不记：对方的原话进日志，等于把同一件事挪了个地方。"""

    def chat(self, system, messages, *, on_text=None):
        raise BrainError(
            f"claude 请求失败 HTTP 400：{_THEIRS}\n"
            "检查环境变量 ANTHROPIC_API_KEY；或者 booksoul ask --brain deepseek",
            kind=kind,
        )

    monkeypatch.setattr(FakeBrain, "chat", chat)
    user, book = await _reader_with_book(db_session)
    service = ChatService(db_session)
    conversation = await service.start(
        user_id=user.id, book_id=book.id, persona="scholar", brain_name="claude"
    )

    events = await _drain(
        await service.ask(
            conversation_id=conversation.id,
            user_id=user.id,
            question="悉达多为什么拒绝追随佛陀？",
            brain_key=FAKE_KEY,
        )
    )

    said = [p["message"] for name, p in events if name in ("failed", "error")]
    assert said, "失败要说出来"
    for message in said:
        assert _THEIRS not in message
        assert "--brain" not in message and "环境变量" not in message
        assert "claude" in message, "点名是哪个大脑出的事"
    assert _THEIRS not in caplog.text


async def test_服务器缺后端时让人换一个大脑_装什么包进日志(
    db_session, fake_brain, monkeypatch, caplog
):
    """缺 SDK 是管理员的事：网页上给「换一个大脑」，`pip install` 那句进 ERROR 日志
    （backend.md「出路要按谁会读到写」）。"""

    def chat(self, system, messages, *, on_text=None):
        raise BrainError(
            "用 claude 需要 Anthropic SDK：pip install anthropic", kind="dependency"
        )

    monkeypatch.setattr(FakeBrain, "chat", chat)
    user, book = await _reader_with_book(db_session)
    service = ChatService(db_session)
    conversation = await service.start(
        user_id=user.id, book_id=book.id, persona="scholar", brain_name="claude"
    )

    with caplog.at_level("ERROR"):
        events = await _drain(
            await service.ask(
                conversation_id=conversation.id,
                user_id=user.id,
                question="悉达多为什么拒绝追随佛陀？",
                brain_key=FAKE_KEY,
            )
        )

    failed = [p["message"] for name, p in events if name == "failed"]
    assert failed and "换一个大脑" in failed[0]
    assert "pip" not in failed[0]
    assert any(
        "pip install anthropic" in r.getMessage()
        for r in caplog.records
        if r.levelname == "ERROR"
    ), "管理员要知道装什么"


async def test_第二轮把第一轮的问答灌回历史(db_session, fake_brain):
    """服务层每次请求重建 Session，历史从 `messages` 读回来。

    这是「服务无状态」的实际含义：进程重启、换一台机器，接着聊都不受影响。
    """
    user, book = await _reader_with_book(db_session)
    service = ChatService(db_session)
    conversation = await service.start(
        user_id=user.id, book_id=book.id, persona="scholar", brain_name="claude"
    )

    for question in ["第一个问题", "第二个问题"]:
        await _drain(
            await service.ask(
                conversation_id=conversation.id,
                user_id=user.id,
                question=question,
                brain_key=FAKE_KEY,
            )
        )

    second = fake_brain.calls[1]["messages"]
    assert [m["role"] for m in second] == ["user", "assistant", "user"]
    assert second[0]["content"] == "第一个问题"
    assert second[2]["content"] == "第二个问题"


async def test_系统提示的拼装顺序不许动(db_session, fake_brain):
    """框架语 → 全书 →（缓存断点）→ 人格。

    这个顺序直接决定用户的账单：书放在缓存断点之前，换人格时整本书的缓存仍然
    有效。把人格挪到书前面，每换一次人格就要重新写一次整本书的缓存。
    """
    user, book = await _reader_with_book(db_session)
    service = ChatService(db_session)
    conversation = await service.start(
        user_id=user.id, book_id=book.id, persona="scholar", brain_name="claude"
    )
    await _drain(
        await service.ask(
            conversation_id=conversation.id,
            user_id=user.id,
            question="问题",
            brain_key=FAKE_KEY,
        )
    )

    system = fake_brain.calls[0]["system"]
    assert len(system) == 2
    book_block, cached = system[0]
    persona_block, persona_cached = system[1]
    assert cached is True and persona_cached is False
    assert book_block.index("全书正文开始") < book_block.index("婆罗门之子")
    assert "研读者" in persona_block


async def test_没填key时说清去哪儿填_而不是丢一个500(db_session, fake_brain):
    user, book = await _reader_with_book(db_session)
    service = ChatService(db_session)
    conversation = await service.start(
        user_id=user.id, book_id=book.id, persona="scholar", brain_name="claude"
    )

    with pytest.raises(ApiError) as caught:
        await _drain(
            await service.ask(
                conversation_id=conversation.id,
                user_id=user.id,
                question="问题",
                brain_key="",
            )
        )
    detail = str(caught.value)
    assert "设置" in detail and "key" in detail.lower()
    assert fake_brain.calls == [], "没 key 就不该碰模型"


async def test_重连订阅同一条流_绝不再问模型一次(db_session, fake_brain, monkeypatch):
    """Spec #39：同一个问题不能让用户付两次钱。

    前端断线重连后会把同一个问题再 POST 一次。服务端必须认出「这一轮正在跑」，
    挂上去回放，而不是又调一次模型。
    """
    user, book = await _reader_with_book(db_session)
    service = ChatService(db_session)
    conversation = await service.start(
        user_id=user.id, book_id=book.id, persona="scholar", brain_name="claude"
    )

    # threading.Event 而不是 asyncio.Event：模型调用跑在真正的工作线程里，
    # 那里没有事件循环。用 asyncio.Event 会在线程里等一个别的循环上的对象，
    # 永远等不到。
    gate = threading.Event()

    def slow_chat(self, system, messages, *, on_text=None):
        FakeBrain.calls.append({"system": system, "messages": messages})
        on_text("前半段。")
        gate.wait(timeout=5)  # 卡住，模拟一次还在生成中的回答
        on_text("后半段。")
        return Reply(text="前半段。后半段。", stop_reason="end_turn")

    monkeypatch.setattr(FakeBrain, "chat", slow_chat)

    first = await service.ask(
        conversation_id=conversation.id,
        user_id=user.id,
        question="同一个问题",
        brain_key=FAKE_KEY,
    )
    got_first = []

    async def read_first():
        async for name, payload in first:
            if name == "token":
                got_first.append(payload["text"])

    reader_task = asyncio.create_task(read_first())
    await asyncio.sleep(0.2)  # 等第一段流出来

    # 重连：同一个问题又来一次
    second = await service.ask(
        conversation_id=conversation.id,
        user_id=user.id,
        question="同一个问题",
        brain_key=FAKE_KEY,
    )
    got_second = []

    async def read_second():
        async for name, payload in second:
            if name == "token":
                got_second.append(payload["text"])

    second_task = asyncio.create_task(read_second())
    await asyncio.sleep(0.1)
    gate.set()
    await asyncio.wait_for(asyncio.gather(reader_task, second_task), timeout=5)

    assert len(FakeBrain.calls) == 1, "重连不许再调一次模型"
    assert "".join(got_second) == "前半段。后半段。"  # 回放 + 跟进
    rows = await _messages(db_session, conversation.id)
    assert len([m for m in rows if m.role == "assistant"]) == 1


async def test_最近问过的按时间倒序给出来(db_session, fake_brain):
    """③屏那一栏。没有它，对话历史等于存了也找不到。"""
    user, book = await _reader_with_book(db_session)
    service = ChatService(db_session)

    for question in ["第一个问题", "第二个问题"]:
        conversation = await service.start(
            user_id=user.id, book_id=book.id, persona="scholar", brain_name="claude"
        )
        await _drain(
            await service.ask(
                conversation_id=conversation.id,
                user_id=user.id,
                question=question,
                brain_key=FAKE_KEY,
            )
        )

    recent = await service.list_recent(user_id=user.id, limit=10)
    assert [c.title for c in recent] == ["第二个问题", "第一个问题"]
    assert recent[0].book_title == "悉达多"
    assert recent[0].turn_count == 1


async def test_按书列会话_只列这本书的(db_session, fake_brain):
    """书籍详情那一栏「这本书的会话」：接着上次聊，而不是每点一次开一个新的。"""
    user, book = await _reader_with_book(db_session)
    other = Book(
        user_id=user.id,
        title="另一本",
        source_file="另一本.epub",
        source_format="epub",
        status="ready",
        storage_path=f"{book.storage_path}-另一本",  # 开会话不读文件，目录在不在都行
        chapter_count=2,
        word_count=40,
    )
    db_session.add(other)
    await db_session.commit()
    service = ChatService(db_session)

    elsewhere = await service.start(
        user_id=user.id, book_id=other.id, persona="scholar", brain_name="claude"
    )
    here = [
        await service.start(
            user_id=user.id, book_id=book.id, persona="scholar", brain_name="claude"
        )
        for _ in range(2)
    ]

    listed = await service.list_recent(user_id=user.id, book_id=book.id, limit=10)

    assert {c.id for c in listed} == {c.id for c in here}
    assert elsewhere.id not in {c.id for c in listed}


async def test_删掉会话_问答与选章留档一起走_别人删不了(db_session, fake_brain):
    from sqlalchemy import func

    from booksoul_api.core.errors import NotFoundError
    from booksoul_api.models.retrieval import Retrieval

    user, book = await _reader_with_book(db_session)
    service = ChatService(db_session)
    conversation = await service.start(
        user_id=user.id, book_id=book.id, persona="scholar", brain_name="claude"
    )
    await _drain(
        await service.ask(
            conversation_id=conversation.id,
            user_id=user.id,
            question="悉达多是谁？",
            brain_key=FAKE_KEY,
        )
    )
    stranger = User(email="stranger@example.com", password_hash="不参与本测试")
    db_session.add(stranger)
    await db_session.commit()

    # 别人的：和「没有这个会话」同一个 404，不承认它存在
    with pytest.raises(NotFoundError):
        await service.delete(conversation_id=conversation.id, user_id=stranger.id)
    assert await _messages(db_session, conversation.id), "别人删不动，一条都不少"

    await service.delete(conversation_id=conversation.id, user_id=user.id)

    with pytest.raises(NotFoundError):
        await service.detail(conversation_id=conversation.id, user_id=user.id)
    assert await _messages(db_session, conversation.id) == []
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(Retrieval)
            .where(Retrieval.conversation_id == conversation.id)
        )
        == 0
    )


async def test_开会话接口_回第一问的流_会话在哪里看Location(
    client, db_session, fake_brain
):
    """`POST /api/conversations` 回的是第一问的流（201），新会话的地址在 `Location`。

    客户端拿它在流还在走时就把地址换成这个会话：这时刷新页面，回到的应是这个会话，
    而不是一张新稿纸——再问一遍就是第二个会话、第二笔钱。
    """
    from test_brains_api import _auth, _token

    token = await _token(client)
    book = await _book_on_disk(db_session, await _registered(db_session))

    r = await client.post(
        "/api/conversations",
        json={
            "bookId": book.id,
            "persona": "book",
            "brainName": "claude",
            "question": "你是谁？",
        },
        # 开发时前端直连 8000 就是跨源：不 expose，脚本读到的 Location 是 null
        headers={
            **_auth(token),
            "X-Brain-Key": FAKE_KEY,
            "Origin": "http://localhost:5173",
        },
    )

    assert r.status_code == 201, r.text
    assert r.headers["content-type"].startswith("text/event-stream")
    assert "event: token" in r.text
    assert "event: done" in r.text
    location = r.headers["location"]
    assert location.startswith("/api/conversations/")
    assert "location" in r.headers["access-control-expose-headers"].lower()

    opened = await client.get(location, headers=_auth(token))
    assert opened.status_code == 200, opened.text
    assert opened.json()["persona"] == "book"
    assert [m["role"] for m in opened.json()["messages"]] == ["user", "assistant"]


async def test_开会话接口_没有第一问或问不出去_一个会话都不建(
    client, db_session, fake_brain
):
    """这个接口上已经没有「只建一个空会话」的说法了。"""
    from test_brains_api import _auth, _token

    token = await _token(client)
    book = await _book_on_disk(db_session, await _registered(db_session))
    opening = {"bookId": book.id, "persona": "scholar", "brainName": "claude"}

    bare = await client.post(
        "/api/conversations",
        json=opening,
        headers={**_auth(token), "X-Brain-Key": FAKE_KEY},
    )
    assert bare.status_code == 422, bare.text

    keyless = await client.post(
        "/api/conversations",
        json={**opening, "question": "悉达多是谁？"},
        headers=_auth(token),
    )
    assert keyless.status_code == 400, keyless.text
    assert "key" in keyless.json()["detail"]

    listed = await client.get("/api/conversations", headers=_auth(token))
    assert listed.json() == []
    assert fake_brain.calls == []


async def test_会话接口_按书过滤与删除(client, db_session, fake_brain):
    """路由这一层只做两件容易写错的事：`bookId` 是驼峰的查询参数，删完回 204。"""
    from test_brains_api import _auth, _token

    token = await _token(client)
    book = await _book_on_disk(db_session, await _registered(db_session))
    r = await client.post(
        "/api/conversations",
        json={"bookId": book.id, "brainName": "claude", "question": "悉达多是谁？"},
        headers={**_auth(token), "X-Brain-Key": FAKE_KEY},
    )
    assert r.status_code == 201, r.text
    cid = int(r.headers["location"].rsplit("/", 1)[-1])

    listed = await client.get(
        f"/api/conversations?bookId={book.id}", headers=_auth(token)
    )
    assert [c["id"] for c in listed.json()] == [cid]
    elsewhere = await client.get(
        f"/api/conversations?bookId={book.id + 1}", headers=_auth(token)
    )
    assert elsewhere.json() == []

    gone = await client.delete(f"/api/conversations/{cid}", headers=_auth(token))
    assert gone.status_code == 204, gone.text
    again = await client.get(f"/api/conversations/{cid}", headers=_auth(token))
    assert again.status_code == 404


async def test_对面在说完之前断开连接会被认出来(db_session, fake_brain, monkeypatch):
    """一段被截断的回答**读起来和完整回答一模一样**。

    模型停下时，最后一句常常正好是个句号。不说破，用户就把半截话当成了完整答案，
    然后奇怪它怎么答得这么浅。OpenAI 兼容协议里，正常结束一定有 `[DONE]` 或
    `finish_reason`；两者都没有就是对面把线掐了。
    """
    import json as json_mod
    from unittest.mock import patch

    from booksoul import brain as brain_mod
    from booksoul.config import BrainConfig

    # 只吐两段正文就断，不给 [DONE] 也不给 finish_reason
    body = [
        b"data: "
        + json_mod.dumps({"choices": [{"delta": {"content": "前半段。"}}]}).encode(),
        b"",
        b"data: "
        + json_mod.dumps({"choices": [{"delta": {"content": "后半"}}]}).encode(),
        b"",
    ]

    class FakeResponse:
        def __enter__(self):
            return iter(body)

        def __exit__(self, *_):
            return False

    cfg = BrainConfig(
        name="断线的",
        provider="openai",
        model="x",
        base_url="http://127.0.0.1:1/v1",
        api_key_env="",  # 本机端点，不要 key
    )
    engine_brain = brain_mod.OpenAICompatBrain(cfg)
    said: list[str] = []

    with (
        patch("urllib.request.urlopen", return_value=FakeResponse()),
        pytest.raises(BrainError) as caught,
    ):
        engine_brain.chat([("system", True)], [], on_text=said.append)

    assert "断开" in str(caught.value)
    assert "接着往下" in str(caught.value)  # N1：给出下一步动作
    # 已经流出来的字照样交给了上层，残文才保得住
    assert said == ["前半段。", "后半"]


async def test_该报错的检查发生在await那一刻_不是第一次迭代时(db_session, fake_brain):
    """`ask` 是普通协程，不是异步生成器——区别在**检查发生的时刻**。

    写成异步生成器，鉴权、没填 key、上一轮还没说完那几条检查都要等到第一次
    迭代才跑；而路由那一层在拿到迭代器的那一刻就已经把 `200 text/event-stream`
    的响应头发出去了。于是用户收到的不是「到设置页填 key」，是一条莫名断掉的
    空流——没有报错，只是什么都没有。

    所以这里**刻意不 drain**：真正要钉的是「一个字节都还没发出去就抛了」。
    收口进 `TurnRunner` 时最容易顺手写成生成器，`launch` 里一行 yield 就够。
    """
    user, book = await _reader_with_book(db_session)
    service = ChatService(db_session)
    conversation = await service.start(
        user_id=user.id, book_id=book.id, persona="scholar", brain_name="claude"
    )

    with pytest.raises(ApiError):
        await service.ask(
            conversation_id=conversation.id,
            user_id=user.id,
            question="问题",
            brain_key="",
        )
    assert fake_brain.calls == []


async def test_开会话之后预设换了模型_这个会话下一轮仍调开会话时那个(
    db_session, fake_brain, monkeypatch
):
    """会话的模型只有一份真相：开会话时存下的快照。

    显示读它、算预算读它，**真正调模型也必须读它**。否则改一下 `booksoul.toml`
    里某个预设的 `model`，已开会话的下一轮就会调新模型，界面上却还写着旧的，
    预算也还按旧模型的窗口算——三者分叉，而且一个字都不报。
    """
    user, book = await _reader_with_book(db_session)
    service = ChatService(db_session)
    conversation = await service.start(
        user_id=user.id, book_id=book.id, persona="scholar", brain_name="claude"
    )
    opened_with = conversation.model

    _edit_preset_model(monkeypatch, "claude", "claude-改过的模型")

    await _drain(
        await service.ask(
            conversation_id=conversation.id,
            user_id=user.id,
            question="悉达多为什么拒绝追随佛陀？",
            brain_key=FAKE_KEY,
        )
    )

    assert fake_brain.calls[-1]["cfg"].model == opened_with
    assert opened_with != "claude-改过的模型"


async def test_预设换了模型之后新开的会话用新模型(db_session, fake_brain, monkeypatch):
    user, book = await _reader_with_book(db_session)
    service = ChatService(db_session)
    _edit_preset_model(monkeypatch, "claude", "claude-改过的模型")

    conversation = await service.start(
        user_id=user.id, book_id=book.id, persona="scholar", brain_name="claude"
    )
    await _drain(
        await service.ask(
            conversation_id=conversation.id,
            user_id=user.id,
            question="悉达多为什么拒绝追随佛陀？",
            brain_key=FAKE_KEY,
        )
    )

    assert conversation.model == "claude-改过的模型"
    assert fake_brain.calls[-1]["cfg"].model == "claude-改过的模型"


def _edit_preset_model(monkeypatch, name: str, model: str) -> None:
    """相当于有人改了 `booksoul.toml` 里 `[brains.{name}]` 的 `model`。

    换在 `available_brains()` 上，不换 `PRESETS`：项目根的 `booksoul.toml`
    把每个预设都重写了一遍，改 `PRESETS` 会被文件盖掉——测试照样绿，
    却什么都没证明。
    """
    from booksoul import config as config_mod

    real = config_mod.available_brains

    def edited() -> dict[str, dict]:
        brains = real()
        brains[name]["model"] = model
        return brains

    monkeypatch.setattr(config_mod, "available_brains", edited)
