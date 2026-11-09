"""对读：同一个问题三种人格并排（界面稿⑦屏三列）。

对读是这个产品的立意——标签页每次只给三分之一，讲不清差别。

盯的几件事：

* **一轮三条回答，同 turn 不同 persona。** 唯一索引就建立在这个形状上。
* **三路共享同一份整本书的缓存。** 书那一段提示词必须**逐字相同**，
  否则「对读不是三倍价」这句话就是假的——而它是人工卡点那条裁决的依据。
* **一路出错不拖垮另外两路。** 那两列的钱已经花了。
* **拼 history 每轮取一条。** 这是 S10 的裁决：原本的「只取主干人格那一支」
  会让切换人格之前的轮次集体落选，历史从 4 条塌到 2 条，而界面上还完整显示着。
"""

from __future__ import annotations

import json
from pathlib import Path

from fakes import FAKE_KEY, FakeBrain
from sqlalchemy import select

from booksoul.brain import BrainError, Reply, Usage
from booksoul.model import count_words
from booksoul_api.core.config import get_settings
from booksoul_api.models.book import Book
from booksoul_api.models.chapter import Chapter
from booksoul_api.models.message import Message
from booksoul_api.models.user import User
from booksoul_api.services.chat_service import ChatService
from booksoul_api.services.turn_state import _history_for

# 人格提示词的第一行。用它认出这次是哪一路在说话。
LABELS = {
    "# 人格：研读者": "scholar",
    "# 人格：作者": "author",
    "# 人格：书本身": "book",
}

CHAPTERS = {
    "婆罗门之子": "悉达多，俊美的婆罗门之子，与他的好友乔文达一道长大。",
    "乔达摩": "他们见到了佛陀乔达摩，佛陀讲说法义，世尊的法义是圆满的。",
}


async def _reader_with_book(db_session) -> tuple[User, Book]:
    user = User(email="reader@example.com", password_hash="不参与本测试")
    db_session.add(user)
    await db_session.flush()

    storage = f"{user.id}/compare-fixture-悉达多"
    directory = Path(get_settings().library_root) / storage
    (directory / "chapters").mkdir(parents=True, exist_ok=True)

    records = []
    for n, (title, body) in enumerate(CHAPTERS.items(), 1):
        record = {
            "序号": n,
            "标题": title,
            "文件": f"chapters/{n:03d}-{title}.md",
            "层级": 1,
            "字数": count_words(body),
        }
        (directory / record["文件"]).write_text(
            f"# {title}\n\n{body}", encoding="utf-8"
        )
        records.append(record)

    (directory / "meta.json").write_text(
        json.dumps({"书名": "悉达多", "章节": records}, ensure_ascii=False),
        encoding="utf-8",
    )
    book = Book(
        user_id=user.id,
        title="悉达多",
        source_file="悉达多.epub",
        source_format="epub",
        status="ready",
        storage_path=storage,
        chapter_count=len(records),
        word_count=sum(r["字数"] for r in records),
    )
    db_session.add(book)
    await db_session.flush()
    db_session.add_all(
        Chapter(
            book_id=book.id,
            idx=r["序号"],
            title=r["标题"],
            level=1,
            word_count=r["字数"],
            file_path=r["文件"],
        )
        for r in records
    )
    await db_session.commit()
    return user, book


def _speaks_its_name(monkeypatch, *, broken: str = "") -> None:
    """让替身按人格说不同的话，这样三列才分得开。

    `broken` 指定哪一路抛错——用来验「一路出错不拖垮另外两路」。
    """

    def chat(self, system, messages, *, on_text=None):
        FakeBrain.calls.append({"system": system, "messages": messages})
        head = system[1][0].split("\n", 1)[0].strip()
        who = LABELS.get(head, "?")
        if who == broken:
            raise BrainError("这一路的模型报错了")
        for chunk in (f"我是{who}，", "我的回答是这样。"):
            if on_text:
                on_text(chunk)
        return Reply(
            text="",
            model=self.cfg.model,
            stop_reason="end_turn",
            usage=Usage(input_tokens=13_204, output_tokens=286, cache_read=12_904),
        )

    monkeypatch.setattr(FakeBrain, "chat", chat)


async def _open(db_session):
    user, book = await _reader_with_book(db_session)
    service = ChatService(db_session)
    conversation = await service.start(
        user_id=user.id, book_id=book.id, persona="scholar", brain_name="claude"
    )
    return service, user, book, conversation


async def _ask(service, conversation, user, question, **kw):
    stream = await service.ask(
        conversation_id=conversation.id,
        user_id=user.id,
        question=question,
        brain_key=FAKE_KEY,
        **kw,
    )
    return [event async for event in stream]


async def _messages(db_session, conversation_id: int) -> list[Message]:
    await db_session.commit()
    rows = await db_session.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.seq)
    )
    return list(rows.scalars())


# ------------------------------------------------------------------ 三路


async def test_对读一轮产生一问三答(db_session, fake_brain, monkeypatch):
    _speaks_its_name(monkeypatch)
    service, user, book, conversation = await _open(db_session)

    await _ask(service, conversation, user, "悉达多为什么拒绝追随佛陀？", compare=True)

    rows = await _messages(db_session, conversation.id)
    assert [(m.role, m.turn) for m in rows] == [
        ("user", 1),
        ("assistant", 1),
        ("assistant", 1),
        ("assistant", 1),
    ]
    # 同 turn 不同 persona —— 唯一索引就建立在这个形状上
    assert {m.persona for m in rows if m.role == "assistant"} == {
        "scholar",
        "author",
        "book",
    }
    # seq 在同一个事务里顺序分配。各算各的 max(seq)+1 会算出同一个号，
    # 然后撞 uq_messages_conv_seq
    assert [m.seq for m in rows] == [1, 2, 3, 4]


async def test_token事件带persona才分得开三列(db_session, fake_brain, monkeypatch):
    _speaks_its_name(monkeypatch)
    service, user, book, conversation = await _open(db_session)

    events = await _ask(service, conversation, user, "问题", compare=True)

    said: dict[str, str] = {}
    for name, payload in events:
        if name == "token":
            said[payload["persona"]] = (
                said.get(payload["persona"], "") + payload["text"]
            )
    assert said == {
        "scholar": "我是scholar，我的回答是这样。",
        "author": "我是author，我的回答是这样。",
        "book": "我是book，我的回答是这样。",
    }
    # 每一路说完各发一个 answered；整轮落库之后才发 done
    assert {p["persona"] for n, p in events if n == "answered"} == set(said)
    assert [n for n, _ in events][-1] == "done"


async def test_三路共享同一份整本书的缓存(db_session, fake_brain, monkeypatch):
    """人工卡点那条裁决的依据：对读不是三倍价。

    依据成立的前提是**书那一段提示词逐字相同**，缓存断点在它之后。差一个字，
    三路就各付各的全价，而界面上那句「三路共享整本书的缓存」立刻变成假话。
    """
    _speaks_its_name(monkeypatch)
    service, user, book, conversation = await _open(db_session)

    await _ask(service, conversation, user, "问题", compare=True)

    systems = [call["system"] for call in fake_brain.calls]
    assert len(systems) == 3
    books = {s[0][0] for s in systems}
    assert len(books) == 1, "三路的书那一段必须逐字相同"
    # 书在缓存断点之前、人格在之后
    assert all(s[0][1] is True and s[1][1] is False for s in systems)
    # 人格那一段必须各不相同，否则三列会说同样的话
    assert len({s[1][0] for s in systems}) == 3


async def test_一路出错不拖垮另外两路(db_session, fake_brain, monkeypatch):
    """那两列的钱已经花了。三列一起变白，等于把已经买到的东西也扔了。"""
    _speaks_its_name(monkeypatch, broken="author")
    service, user, book, conversation = await _open(db_session)

    events = await _ask(service, conversation, user, "问题", compare=True)

    failed = [p for n, p in events if n == "failed"]
    assert [p["persona"] for p in failed] == ["author"]
    assert {p["persona"] for n, p in events if n == "answered"} == {"scholar", "book"}

    rows = await _messages(db_session, conversation.id)
    answers = {m.persona: m for m in rows if m.role == "assistant"}
    # 出错那一路一个字都没吐出来，所以不落库（空消息比没有消息更糟）
    assert set(answers) == {"scholar", "book"}
    assert all(m.complete for m in answers.values())
    # 整轮的结局是 error：有一路没说完，这一轮就不算说完了
    assert [n for n, _ in events][-1] == "error"


async def test_重连回放三路_绝不再问模型一次(db_session, fake_brain, monkeypatch):
    """Spec #39 在对读上同样成立，而且代价是三倍。"""
    _speaks_its_name(monkeypatch)
    service, user, book, conversation = await _open(db_session)
    await _ask(service, conversation, user, "问题", compare=True)
    before = len(fake_brain.calls)

    rows = await _messages(db_session, conversation.id)
    assert len([m for m in rows if m.role == "assistant"]) == 3
    assert before == 3, "一轮对读就是三次调用，不该多也不该少"


# ------------------------------------------------------------------ 主干


async def test_跟这个继续聊换主干人格_旁支一个字不删(
    db_session, fake_brain, monkeypatch
):
    _speaks_its_name(monkeypatch)
    service, user, book, conversation = await _open(db_session)
    await _ask(service, conversation, user, "问题", compare=True)

    out = await service.update(
        conversation_id=conversation.id, user_id=user.id, persona="author"
    )

    assert out.persona == "author"
    await db_session.refresh(conversation)
    assert conversation.persona == "author"
    # 旁支还在。用户要的正是「三种说法我都看过」
    rows = await _messages(db_session, conversation.id)
    assert len([m for m in rows if m.role == "assistant"]) == 3


async def test_提问带着人格_这一问用它答_主干跟着这一问换过去(
    db_session, fake_brain, monkeypatch
):
    """⑦屏点人格标签，下一问就带着这个人格去问。

    服务端两件事缺一件，界面就对不上：这一问用它答（字流进点中的那一列），
    主干跟着换过去（说完回头拉一次，标签停在点中的那个上，不跳回去）。
    """
    _speaks_its_name(monkeypatch)
    service, user, book, conversation = await _open(db_session)

    events = await _ask(service, conversation, user, "你是谁？", persona="book")

    assert {p["persona"] for n, p in events if n == "token"} == {"book"}
    detail = await service.detail(conversation_id=conversation.id, user_id=user.id)
    assert detail.persona == "book"
    assert [m.persona for m in detail.messages if m.role == "assistant"] == ["book"]


async def test_换了主干之后下一轮用新人格回答(db_session, fake_brain, monkeypatch):
    _speaks_its_name(monkeypatch)
    service, user, book, conversation = await _open(db_session)
    await _ask(service, conversation, user, "问题", compare=True)
    await service.update(
        conversation_id=conversation.id, user_id=user.id, persona="book"
    )

    events = await _ask(service, conversation, user, "那再往下呢？")

    assert {p["persona"] for n, p in events if n == "token"} == {"book"}


# --------------------------------------------------------------- 历史怎么拼


def _row(turn, role, content, persona=None, complete=True, seq=0):
    return Message(
        seq=seq,
        turn=turn,
        role=role,
        content=content,
        persona=persona,
        complete=complete,
    )


def test_切换人格之后历史一条都不丢():
    """S1 记下、S10 裁决的那个失败。

    原本的验收是「只取主干人格那一支」。单人格聊两轮（都是研读者）后切到作者，
    那两轮集体落选主干，历史直接塌掉——**而界面上历史还完整显示着，没有报错**。
    按「每轮取一条」拼，单人格轮次只有一条回答，永远不会落选。
    """
    rows = [
        _row(1, "user", "问题一", seq=1),
        _row(1, "assistant", "研读者答一", persona="scholar", seq=2),
        _row(2, "user", "问题二", seq=3),
        _row(2, "assistant", "研读者答二", persona="scholar", seq=4),
        _row(3, "user", "问题三", seq=5),
        _row(3, "assistant", "研读者答三", persona="scholar", seq=6),
        _row(3, "assistant", "作者答三", persona="author", seq=7),
        _row(3, "assistant", "书答三", persona="book", seq=8),
    ]

    history = _history_for(rows, "author")

    assert [m["content"] for m in history] == [
        "问题一",
        "研读者答一",  # 这一轮只有研读者答过，照样进
        "问题二",
        "研读者答二",
        "问题三",
        "作者答三",  # 对读那一轮取当前主干人格这一路
    ]


def test_历史里不会出现两个连着的user():
    """断在半路的那一轮：只进提问、不进回答的话，历史里就是两个连着的 user。

    Anthropic 那边要求角色交替 —— 那会变成一个只在「上一轮断过流」时才现形的
    500。残文本身不丢：用户点「接着往下」时它会被原样带回去。
    """
    rows = [
        _row(1, "user", "问题一", seq=1),
        _row(1, "assistant", "断在这里", persona="scholar", complete=False, seq=2),
        _row(2, "user", "问题二", seq=3),
        _row(2, "assistant", "这次说完了", persona="scholar", seq=4),
    ]

    history = _history_for(rows, "scholar")

    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[0]["content"] == "问题二"


def test_一轮都没有就是空历史():
    assert _history_for([], "scholar") == []


async def test_切换人格之后模型手里的历史一条都不丢(
    db_session, fake_brain, monkeypatch
):
    """上面那三条纯逻辑测试的边界对照：真正送到模型手里的 `messages` 是什么样。

    它们不重复：那三条逐条钉住拼装规则的几个分叉（单人格轮次不落选、
    断流那一轮不进历史、空历史），跑得飞快、不连库；这一条钉的是**那份历史
    真的到了模型手里**——中间隔着落库、读回、`Turn` 构造三步，任何一步把它
    弄丢，上面三条照样全绿。
    """
    _speaks_its_name(monkeypatch)
    service, user, book, conversation = await _open(db_session)

    await _ask(service, conversation, user, "问题一")
    await _ask(service, conversation, user, "问题二")
    # 切到作者：原本的验收「只取主干人格那一支」会让前两轮集体落选，
    # 历史当场塌掉，而界面上它还完整显示着（S10 裁决的那个失败）
    await _ask(service, conversation, user, "问题三", persona="author")

    messages = fake_brain.calls[-1]["messages"]
    assert [m["role"] for m in messages] == [
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
    ]
    assert [m["content"] for m in messages if m["role"] == "user"] == [
        "问题一",
        "问题二",
        "问题三",
    ]
