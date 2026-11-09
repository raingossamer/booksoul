"""溯源落库与读取（界面稿⑤屏上半、⑦屏答栏）。

这个文件走的是真路径：模型（替身）吐出一段带引文的回答 → 落库 → 端点读回来。

盯的三件事：

* **引文和回答在同一个事务里落。** 「回答在库里、引文没进去」这种半截状态，
  界面上表现为一条没有任何出处的回答——看起来完全正常，只是不能核对了。
* **段号数的是阅读页看到的块。** 章节文件开头有标题行和一行元信息，引擎载入时
  剥掉了它们、阅读页没有。差两块，高亮就落在隔壁那一段上。
* **对不上就说对不上。** 块引用定位不到记 -1，不猜是哪一章。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fakes import FAKE_KEY
from sqlalchemy import select

from booksoul.model import count_words
from booksoul_api.core.config import get_settings
from booksoul_api.core.errors import NotFoundError
from booksoul_api.models.book import Book
from booksoul_api.models.chapter import Chapter
from booksoul_api.models.citation import Citation
from booksoul_api.models.message import Message
from booksoul_api.models.user import User
from booksoul_api.services.chat_service import ChatService
from booksoul_api.services.citation_service import CitationService

# 两节书，原样写在磁盘上。开头的标题行与那行元信息是 writer 真的会写的，
# 留着它们正是为了让「段号差两块」这个坑暴露出来。
CHAPTERS = {
    "婆罗门之子": (
        "悉达多，俊美的婆罗门[^1]之子，年轻的鹰隼，与他的好友乔文达一道长大。\n\n"
        "所有人都热爱悉达多。悉达多令所有人喜悦。\n\n"
        "[^1]: 婆罗门，印度社会阶级之一。\n"
    ),
    "乔达摩": (
        "“没人能通过法义得到解脱！”悉达多说。\n\n"
        "“您从未以言辞或法义宣讲您在证觉成道之际所发生的事。”\n"
    ),
}

# 第 1 节的块：0 标题、1 元信息、2 第一段、3 第二段（脚注定义不算块）
PARA_OF_LOVE = 3


async def _reader_with_book(db_session) -> tuple[User, Book]:
    user = User(email="reader@example.com", password_hash="不参与本测试")
    db_session.add(user)
    await db_session.flush()

    storage = f"{user.id}/citation-fixture-悉达多"
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
            f"# {title}\n\n> 悉达多　·　第 {n}/2 节\n\n{body}", encoding="utf-8"
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


async def _answer(db_session, fake_brain, script: list[str]):
    """让替身说一段话，回来的是 (service, user, book, conversation, 那条回答)。"""
    user, book = await _reader_with_book(db_session)
    fake_brain.script = script
    service = ChatService(db_session)
    conversation = await service.start(
        user_id=user.id, book_id=book.id, persona="scholar", brain_name="claude"
    )
    stream = await service.ask(
        conversation_id=conversation.id,
        user_id=user.id,
        question="悉达多为什么拒绝追随佛陀？",
        brain_key=FAKE_KEY,
    )
    [event async for event in stream]

    answer = await db_session.scalar(
        select(Message).where(
            Message.conversation_id == conversation.id, Message.role == "assistant"
        )
    )
    return service, user, book, conversation, answer


async def _citations(db_session, message_id: int) -> list[Citation]:
    rows = await db_session.execute(
        select(Citation).where(Citation.message_id == message_id).order_by(Citation.id)
    )
    return list(rows.scalars())


# ------------------------------------------------------------------ 落库


async def test_块引用落库成引文并定位到章与段(db_session, fake_brain):
    _, _, book, _, answer = await _answer(
        db_session,
        fake_brain,
        [
            "这一节的调子是这样的：\n\n",
            "> 所有人都热爱悉达多。悉达多令所有人喜悦。\n\n",
            "往下属于推演。",
        ],
    )

    rows = await _citations(db_session, answer.id)
    assert len(rows) == 1
    one = rows[0]
    assert one.chapter_title == "婆罗门之子"
    # 段号数的是**阅读页看到的块**：标题、元信息各占一块
    assert one.para_index == PARA_OF_LOVE
    assert one.chapter_id is not None


async def test_原文里的脚注标记不妨碍定位(db_session, fake_brain):
    """原文是「婆罗门[^1]之子」，模型引的时候不会把 `[^1]` 抄进去。"""
    _, _, _, _, answer = await _answer(
        db_session,
        fake_brain,
        ["> 悉达多，俊美的婆罗门之子，年轻的鹰隼，与他的好友乔文达一道长大。"],
    )

    rows = await _citations(db_session, answer.id)
    assert [(r.chapter_title, r.para_index) for r in rows] == [("婆罗门之子", 2)]


async def test_对不上的块引用记成找不到_并且不猜是哪一章(db_session, fake_brain):
    _, _, _, _, answer = await _answer(
        db_session, fake_brain, ["> 悉达多在河边见到了瓦稣迪瓦，两人相视而笑。"]
    )

    rows = await _citations(db_session, answer.id)
    assert len(rows) == 1
    assert rows[0].para_index == -1
    assert rows[0].chapter_id is None
    assert rows[0].chapter_title == ""


async def test_没引原文的回答不留下引文(db_session, fake_brain):
    _, _, _, _, answer = await _answer(
        db_session, fake_brain, ["这个问题书里没有正面回答，我只能说它的理路指向哪里。"]
    )

    assert await _citations(db_session, answer.id) == []


async def test_断在半路的回答里的引文照样保住(db_session, fake_brain):
    """已经吐出来的那几句是计过费的，里面那条引文同样是。"""
    user, book = await _reader_with_book(db_session)
    fake_brain.script = [
        "> 所有人都热爱悉达多。悉达多令所有人喜悦。\n\n",
        "接下来我要说的是",
    ]
    fake_brain.raise_after = 1  # 第一段吐出来了，第二段之前断
    service = ChatService(db_session)
    conversation = await service.start(
        user_id=user.id, book_id=book.id, persona="scholar", brain_name="claude"
    )
    stream = await service.ask(
        conversation_id=conversation.id,
        user_id=user.id,
        question="问题",
        brain_key=FAKE_KEY,
    )
    [event async for event in stream]

    answer = await db_session.scalar(
        select(Message).where(
            Message.conversation_id == conversation.id, Message.role == "assistant"
        )
    )
    assert answer.complete is False
    rows = await _citations(db_session, answer.id)
    assert [r.para_index for r in rows] == [PARA_OF_LOVE]


# ------------------------------------------------------------------ 读取


async def test_按回答取它引的全部原文(db_session, fake_brain):
    _, user, book, conversation, answer = await _answer(
        db_session,
        fake_brain,
        ["> 所有人都热爱悉达多。悉达多令所有人喜悦。\n\n> 这句书里没有。"],
    )

    out = await CitationService(db_session).for_message(
        message_id=answer.id, user_id=user.id
    )

    assert len(out) == 2
    located, missing = out
    assert located.located is True
    assert located.chapter_idx == 1  # 阅读页路由要的是 idx，不是主键
    assert located.chapter_title == "婆罗门之子"
    assert located.book_id == book.id
    assert located.conversation_id == conversation.id
    assert located.question == "悉达多为什么拒绝追随佛陀？"
    assert located.relocated is False

    assert missing.located is False
    assert missing.chapter_idx is None
    assert missing.chapter_title == ""


async def test_没引原文返回空数组_不是404(db_session, fake_brain):
    _, user, _, _, answer = await _answer(
        db_session, fake_brain, ["书里没有正面回答。"]
    )

    assert (
        await CitationService(db_session).for_message(
            message_id=answer.id, user_id=user.id
        )
        == []
    )


async def test_不存在的回答是404_而不是空数组(db_session, fake_brain):
    """空数组会被读成「这条回答没引原文」，而真相是这个 id 根本不对。"""
    _, user, _, _, _ = await _answer(db_session, fake_brain, ["随便说点什么。"])

    with pytest.raises(NotFoundError):
        await CitationService(db_session).for_message(
            message_id=999_999, user_id=user.id
        )


async def test_别人的引文看不到(db_session, fake_brain):
    _, _, _, _, answer = await _answer(
        db_session, fake_brain, ["> 所有人都热爱悉达多。悉达多令所有人喜悦。"]
    )
    stranger = User(email="stranger@example.com", password_hash="x")
    db_session.add(stranger)
    await db_session.flush()

    # 越权与不存在同一个 404：403 等于承认这个 id 确实存在
    with pytest.raises(NotFoundError):
        await CitationService(db_session).for_message(
            message_id=answer.id, user_id=stranger.id
        )


async def test_单条引文带得出是哪个问题把人带过来的(db_session, fake_brain):
    """⑤屏那条来路条要的就是它 —— 不然阅读页只能显示一段高亮，说不出为什么。"""
    _, user, _, conversation, answer = await _answer(
        db_session, fake_brain, ["> 所有人都热爱悉达多。悉达多令所有人喜悦。"]
    )
    rows = await _citations(db_session, answer.id)

    out = await CitationService(db_session).one(citation_id=rows[0].id, user_id=user.id)

    assert out.question == "悉达多为什么拒绝追随佛陀？"
    assert out.conversation_id == conversation.id
    assert out.para_index == PARA_OF_LOVE
    assert out.quote.startswith("所有人都热爱悉达多")


async def test_别人的单条引文也看不到(db_session, fake_brain):
    _, _, _, _, answer = await _answer(
        db_session, fake_brain, ["> 所有人都热爱悉达多。悉达多令所有人喜悦。"]
    )
    rows = await _citations(db_session, answer.id)
    stranger = User(email="stranger@example.com", password_hash="x")
    db_session.add(stranger)
    await db_session.flush()

    with pytest.raises(NotFoundError):
        await CitationService(db_session).one(
            citation_id=rows[0].id, user_id=stranger.id
        )
