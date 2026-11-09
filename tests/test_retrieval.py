"""选章锁定、预算与检索明细（界面稿⑥屏）。

这一片要证明的是一句界面文案不是空话：「读了 5/16 节」背后有一张查得到的账。

盯的几件事，每一件走错都不报错、只是悄悄改变模型手里有什么：

* 第一轮挑完就锁住，后续轮次原样复用 —— 每轮重挑一次，前缀缓存全废，
  那是直接写在用户账单上的
* 后续轮次只读选中的那几节文件 —— 一本几百万字的书，每轮全读一次是每轮几百毫秒
* 没选中的章节也要留档 —— 用户要能看到「差 504 字装不下」
* 没载入的章节被明确标给模型 —— 不标，它就对着一份残缺的书凭目录名编
* 问到了没载入的那几节要拦下来 —— 并且把重选的代价说清楚，那要花钱
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fakes import FAKE_KEY, FakeBrain
from sqlalchemy import func, select

from booksoul.model import count_words
from booksoul_api.core.config import get_settings
from booksoul_api.core.errors import ConflictError, NotFoundError, ReselectRequired
from booksoul_api.models.book import Book
from booksoul_api.models.chapter import Chapter
from booksoul_api.models.conversation import Conversation
from booksoul_api.models.message import Message
from booksoul_api.models.retrieval import Retrieval, RetrievalItem
from booksoul_api.models.user import User
from booksoul_api.services.chat_service import ChatService

# 每节正文一律 600 字。长度不齐会让「预算刚好放得下两节」变成猜谜：
# 贪心装箱会跳过装不下的那节继续往下装，结果对、但不是测试设想的那两节。
CHARS = 600

# 预算：够装两节（600 × 2 = 1200），第三节就超了。
BUDGET = 1300

SEEDS = {
    "婆罗门之子": "悉达多是婆罗门之子，他在父亲的屋影里长大。",
    "沙门": "悉达多与乔文达一同出家，随沙门修苦行。",
    "乔达摩": "他们见到了佛陀乔达摩，佛陀讲说法义，世尊的法义是圆满的。",
    "觉醒": "悉达多离开佛陀，他要自己去学，自己去经历。",
    "迦摩罗": "他进了城，遇见名妓迦摩罗，学会了爱与买卖。",
    "船夫": "他回到河边，跟着船夫瓦稣迪瓦听河水说话。",
}

# 问它，《乔达摩》必是第一名（标题被问题点了名，有压倒性加成）
ABOUT_GOTAMA = "佛陀的法义是什么？乔达摩说了什么？"
# 问它，问到的是一节没载入的书
ABOUT_KAMALA = "迦摩罗教了他什么？"


async def _reader_with_big_book(db_session) -> tuple[User, Book]:
    """一本装不进预算的书，真写在磁盘上 —— 选章要把它读回来切词打分。"""
    user = User(email="reader@example.com", password_hash="不参与本测试")
    db_session.add(user)
    await db_session.flush()

    # 目录名与别的测试错开：TRUNCATE ... RESTART IDENTITY 之后每个测试的
    # user.id 都是 1，同名目录会让两个测试往同一个 chapters/ 里写文件。
    storage = f"{user.id}/retrieval-fixture-悉达多"
    directory = Path(get_settings().library_root) / storage
    (directory / "chapters").mkdir(parents=True, exist_ok=True)

    records = []
    for n, (title, seed) in enumerate(SEEDS.items(), 1):
        body = (seed * 40)[:CHARS]
        record = {
            "序号": n,
            "标题": title,
            "文件": f"chapters/{n:03d}-{title}.md",
            "层级": 1,
            # 字数要写：真书库里 writer 就是这么写的，而按 only_idx 载入时
            # 没读到正文的章节全靠它报数（见 Chapter.stated_words）。
            # 少了它，这份夹具测的就是一种现实中不存在的索引。
            "字数": count_words(body),
        }
        (directory / record["文件"]).write_text(
            f"# {title}\n\n{body}", encoding="utf-8"
        )
        records.append(record)

    (directory / "meta.json").write_text(
        json.dumps(
            {"书名": "悉达多", "作者": "赫尔曼·黑塞", "章节": records},
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
        chapter_count=len(records),
        word_count=CHARS * len(records),
    )
    db_session.add(book)
    await db_session.flush()
    db_session.add_all(
        Chapter(
            book_id=book.id,
            idx=r["序号"],
            title=r["标题"],
            level=1,
            word_count=CHARS,
            file_path=r["文件"],
        )
        for r in records
    )
    await db_session.commit()
    return user, book


async def _ask(service, conversation, user, question, **kw) -> list[tuple[str, dict]]:
    stream = await service.ask(
        conversation_id=conversation.id,
        user_id=user.id,
        question=question,
        brain_key=FAKE_KEY,
        **kw,
    )
    return [event async for event in stream]


async def _open(db_session, budget: int = BUDGET):
    user, book = await _reader_with_big_book(db_session)
    service = ChatService(db_session)
    conversation = await service.start(
        user_id=user.id,
        book_id=book.id,
        persona="scholar",
        brain_name="claude",
        budget_chars=budget,
    )
    return service, user, book, conversation


async def _chapter_ids(db_session, book_id: int) -> dict[str, int]:
    rows = await db_session.execute(
        select(Chapter).where(Chapter.book_id == book_id).order_by(Chapter.idx)
    )
    return {c.title: c.id for c in rows.scalars()}


def _no_bm25(monkeypatch) -> None:
    """把 BM25 换成炸弹：只要有人跑它，测试就红。"""
    from booksoul import retrieve

    class Boom:
        def __init__(self, *a, **kw):
            raise AssertionError("这条路不该跑 BM25")

    monkeypatch.setattr(retrieve, "BM25", Boom)


# ------------------------------------------------------------------ 锁定


async def test_第一轮挑完就把选中的锁进会话(db_session, fake_brain):
    service, user, book, conversation = await _open(db_session)
    ids = await _chapter_ids(db_session, book.id)

    await _ask(service, conversation, user, ABOUT_GOTAMA)

    await db_session.refresh(conversation)
    # 预算够两节：开头无条件保留的那节 + 问题点了名的《乔达摩》
    assert conversation.selected_chapter_ids == [
        ids["婆罗门之子"],
        ids["乔达摩"],
    ]


async def test_第二轮原样复用锁定_不再挑一次(db_session, fake_brain, monkeypatch):
    service, user, book, conversation = await _open(db_session)
    await _ask(service, conversation, user, ABOUT_GOTAMA)
    await db_session.refresh(conversation)
    locked = list(conversation.selected_chapter_ids)

    # 第二轮起，跑 BM25 就是错的
    _no_bm25(monkeypatch)
    await _ask(service, conversation, user, "那他后来又去了哪里？")

    await db_session.refresh(conversation)
    assert conversation.selected_chapter_ids == locked
    # 复用的那一轮没有做决定，不该替它编一行留档出来
    rows = await db_session.execute(select(Retrieval.turn))
    assert list(rows.scalars()) == [1]


async def test_复用那一轮的系统提示与第一轮一模一样(db_session, fake_brain):
    """会话锁定的全部意义就是这一条：提示词不变，前缀缓存才不作废。

    差一个字都不行。最容易差的那个字是「全书多少字」——第二轮按 only_idx 载入，
    没选中的章节只剩空壳，字数一数就少了一大截（见 `Chapter.stated_words`）。
    长度一样并不等于一样，所以这里比的是整段文本。
    """
    service, user, book, conversation = await _open(db_session)
    await _ask(service, conversation, user, ABOUT_GOTAMA)
    await _ask(service, conversation, user, "那他后来又去了哪里？")

    first, second = fake_brain.calls[0]["system"], fake_brain.calls[1]["system"]
    assert first == second
    # 顺带钉住「书在缓存断点之前」这个拼装顺序——它同样直接写在账单上
    assert first[0][1] is True and first[1][1] is False


async def test_第二轮只读选中的那几节文件(db_session, fake_brain, monkeypatch):
    """一本几百万字的书，每轮全读一次就是每轮几百毫秒——而结论早就定下来了。"""
    from booksoul_api.services import turn as turn_mod

    seen: list[set[int] | None] = []
    real = turn_mod.load_from_path

    def spy(path, *, only_idx=None):
        seen.append(only_idx)
        return real(path, only_idx=only_idx)

    monkeypatch.setattr(turn_mod, "load_from_path", spy)

    service, user, book, conversation = await _open(db_session)
    await _ask(service, conversation, user, ABOUT_GOTAMA)
    await _ask(service, conversation, user, "那他后来又去了哪里？")

    # 第一轮要全书读盘（不读就没法打分），第二轮只读锁定的那两节
    assert seen[0] is None
    assert seen[1] == {1, 3}


async def test_整本装得下时不挑也不锁(db_session, fake_brain, monkeypatch):
    _no_bm25(monkeypatch)
    service, user, book, conversation = await _open(db_session, budget=10**6)

    await _ask(service, conversation, user, ABOUT_GOTAMA)

    await db_session.refresh(conversation)
    # 整本装得下的书下一轮照样装得下。锁它只会在重解析之后变成一份过期的名单。
    assert conversation.selected_chapter_ids == []
    row = await db_session.scalar(select(Retrieval))
    assert row.whole_book is True
    assert row.selected_count == row.total_count == len(SEEDS)
    assert row.query_terms == []  # 没跑 BM25 就没有查询词


# ------------------------------------------------------------------ 留档


async def test_全书每一节都留一行_包括没选中的(db_session, fake_brain):
    service, user, book, conversation = await _open(db_session)

    await _ask(service, conversation, user, ABOUT_GOTAMA)

    retrieval = await db_session.scalar(select(Retrieval))
    assert (retrieval.conversation_id, retrieval.turn) == (conversation.id, 1)
    assert retrieval.whole_book is False
    assert retrieval.budget_chars == BUDGET
    assert retrieval.used_chars == CHARS * 2 <= BUDGET
    assert (retrieval.selected_count, retrieval.total_count) == (2, len(SEEDS))

    rows = await db_session.execute(
        select(RetrievalItem)
        .where(RetrievalItem.retrieval_id == retrieval.id)
        .order_by(RetrievalItem.rank)
    )
    items = list(rows.scalars())
    assert [i.rank for i in items] == list(range(1, len(SEEDS) + 1))
    assert sum(1 for i in items if i.selected) == 2
    # 落选的也带着分数、字数和名次 —— 「凭什么没选它」要答得出来
    assert all(i.word_count == CHARS for i in items)
    # 快照：重解析会把 chapter_id 置空，到那时这两列就是唯一读得出来的身份
    assert all(i.chapter_title for i in items)
    assert sorted(i.chapter_idx for i in items) == list(range(1, len(SEEDS) + 1))

    first = items[0]
    assert first.chapter_title == "乔达摩"
    assert first.selected is True
    assert float(first.score) > 0
    assert {t["term"] for t in first.matched_terms} & {"佛陀", "法义"}


async def test_重问同一轮复用那一行留档_不另起一个turn(db_session, fake_brain):
    """`(conversation_id, turn)` 上有唯一约束，重问必须走 ON CONFLICT。

    打在仓储的 `save()` 上，而不是绕道去搭一个假的一轮：重用那一行是
    **仓储的契约**，跟这一轮是怎么跑起来的无关。绕道搭一轮的代价是要填一堆
    与本条断言毫不相干的空壳字段，而那些字段每改一次签名就要跟着改一次。
    """
    from booksoul_api.repositories.retrieval_repo import RetrievalRepository

    service, user, book, conversation = await _open(db_session)
    await _ask(service, conversation, user, ABOUT_GOTAMA)
    await db_session.commit()

    retrieval = await db_session.scalar(select(Retrieval))
    assert retrieval is not None

    await RetrievalRepository(db_session).save(
        conversation_id=conversation.id,
        turn=1,
        summary={
            "budget_chars": BUDGET,
            "used_chars": CHARS,
            "selected_count": 1,
            "total_count": len(SEEDS),
            "whole_book": False,
            "query_terms": ["佛陀"],
        },
        items=[
            {
                "chapter_id": None,
                "chapter_idx": 1,
                "chapter_title": "婆罗门之子",
                "rank": 1,
                "score": 1.5,
                "word_count": CHARS,
                "selected": True,
                "matched_terms": [],
            }
        ],
    )
    await db_session.commit()

    kept = list((await db_session.execute(select(Retrieval))).scalars())
    assert len(kept) == 1 and kept[0].id == retrieval.id
    # 旧的那套名次要整批换掉，不能留半套 —— (retrieval_id, rank) 上有唯一约束
    count = await db_session.scalar(select(func.count()).select_from(RetrievalItem))
    assert count == 1


# ------------------------------------------------------- 模型手里到底有什么


async def test_没载入的章节被明确标给模型(db_session, fake_brain):
    """Grill C10 那条连锁的终点：模型对着残缺目录凭章节名臆测内容。"""
    service, user, book, conversation = await _open(db_session)

    await _ask(service, conversation, user, ABOUT_GOTAMA)

    book_block, cached = fake_brain.calls[0]["system"][0]
    assert cached is True
    # 目录是完整的六节 —— 模型要知道这本书还有别的部分
    for title in SEEDS:
        assert title in book_block
    # 没载入的那几节被标出来，并且明说不要凭目录名臆测
    assert book_block.count("本次未载入全文") == len(SEEDS) - 2
    assert "不要凭目录名臆测内容" in book_block
    # 正文只有选中的那两节。《迦摩罗》的正文一个字都不该在里面
    assert SEEDS["乔达摩"][:12] in book_block
    assert SEEDS["迦摩罗"][:12] not in book_block


# ------------------------------------------------------------------ 逃生门


async def test_问到没载入的章节会被拦下来(db_session, fake_brain):
    service, user, book, conversation = await _open(db_session)
    await _ask(service, conversation, user, ABOUT_GOTAMA)
    before = await db_session.scalar(select(func.count()).select_from(Message))

    with pytest.raises(ReselectRequired) as caught:
        await _ask(service, conversation, user, ABOUT_KAMALA)

    detail = str(caught.value)
    assert "迦摩罗" in detail
    # 重选要花钱，这句话必须说破，不能只说「要不要重新挑一下」
    assert "缓存" in detail and "全价" in detail
    # **拦在写下这条提问之前**：否则库里留下一条永远等不到回答的提问
    after = await db_session.scalar(select(func.count()).select_from(Message))
    assert after == before
    assert len(fake_brain.calls) == 1, "被拦下来就不该碰模型"


async def test_勾了重新挑章节才会重挑_并换掉锁定(db_session, fake_brain):
    service, user, book, conversation = await _open(db_session)
    ids = await _chapter_ids(db_session, book.id)
    await _ask(service, conversation, user, ABOUT_GOTAMA)

    await _ask(service, conversation, user, ABOUT_KAMALA, reselect=True)

    await db_session.refresh(conversation)
    assert ids["迦摩罗"] in conversation.selected_chapter_ids
    assert ids["乔达摩"] not in conversation.selected_chapter_ids
    # 重选是一个新的决定，要留下自己那一行
    rows = await db_session.execute(select(Retrieval.turn).order_by(Retrieval.turn))
    assert list(rows.scalars()) == [1, 2]


async def test_问到的是已经载入的章节就不打扰用户(db_session, fake_brain):
    service, user, book, conversation = await _open(db_session)
    await _ask(service, conversation, user, ABOUT_GOTAMA)

    events = await _ask(service, conversation, user, "乔达摩讲的法义是什么？")

    assert [n for n, _ in events][-1] == "done"


# ------------------------------------------------------------------ 明细


async def test_明细给出排名表与预算线(db_session, fake_brain):
    service, user, book, conversation = await _open(db_session)
    await _ask(service, conversation, user, ABOUT_GOTAMA)

    out = await service.retrieval(conversation_id=conversation.id, user_id=user.id)

    assert out.turn == 1
    assert out.question == ABOUT_GOTAMA  # 是这句话挑的章，不是最近那个问题
    assert out.book_title == "悉达多"
    assert out.whole_book is False
    assert (out.selected_count, out.total_count) == (2, len(SEEDS))
    assert out.query_terms  # 真正参与打分的那些词
    assert len(out.items) == len(SEEDS)

    # 累计只在选中的行上走，落选的是破折号（0）
    running = [i.cumulative_chars for i in out.items if i.selected]
    assert running == [CHARS, CHARS * 2]
    assert all(i.cumulative_chars == 0 for i in out.items if not i.selected)

    # 预算线那句话：下一节要 X 字，只剩 Y，差 Z 字装不下
    short = out.shortfall
    assert short.need == CHARS
    assert short.left == BUDGET - CHARS * 2
    assert short.short == CHARS - (BUDGET - CHARS * 2) > 0
    assert short.title == next(i.chapter_title for i in out.items if not i.selected)


async def test_整本装得下时明细说的是整本(db_session, fake_brain):
    service, user, book, conversation = await _open(db_session, budget=10**6)
    await _ask(service, conversation, user, ABOUT_GOTAMA)

    out = await service.retrieval(conversation_id=conversation.id, user_id=user.id)

    assert out.whole_book is True
    assert out.shortfall is None  # 整本装得下就没有「差多少字」这回事
    assert all(i.selected for i in out.items)


async def test_会话详情带上够写仪表的那几个数(db_session, fake_brain):
    service, user, book, conversation = await _open(db_session)
    await _ask(service, conversation, user, ABOUT_GOTAMA)

    out = await service.detail(conversation_id=conversation.id, user_id=user.id)

    assert out.retrieval is not None
    assert (out.retrieval.selected_count, out.retrieval.total_count) == (2, len(SEEDS))
    assert out.retrieval.whole_book is False


async def test_还没问过就没有明细可看(db_session, fake_brain):
    service, user, book, conversation = await _open(db_session)

    with pytest.raises(NotFoundError) as caught:
        await service.retrieval(conversation_id=conversation.id, user_id=user.id)

    assert "先问一个问题" in str(caught.value)


async def test_别人的选章明细看不到(db_session, fake_brain):
    service, user, book, conversation = await _open(db_session)
    await _ask(service, conversation, user, ABOUT_GOTAMA)
    stranger = User(email="stranger@example.com", password_hash="x")
    db_session.add(stranger)
    await db_session.flush()

    # 越权与不存在同一个 404：403 等于承认这个 id 确实存在
    with pytest.raises(NotFoundError):
        await service.retrieval(conversation_id=conversation.id, user_id=stranger.id)


# ------------------------------------------------------------------ 进度


async def test_挑章节要先说一声(db_session, fake_brain):
    """切几百万字的词要好几秒。不说一声，卡住和还在跑长得一模一样——
    而用户对卡住的反应是再问一遍，那要再花一次钱。"""
    service, user, book, conversation = await _open(db_session)

    events = await _ask(service, conversation, user, ABOUT_GOTAMA)

    stages = [p["text"] for n, p in events if n == "stage"]
    assert stages and stages[0] == "正在挑章节…"
    assert f"{len(SEEDS)} 节里读 2 节" in stages[-1]


async def test_复用锁定的那一轮不报挑好了(db_session, fake_brain, monkeypatch):
    """复用的那一轮什么都没挑。报一句「挑好了」是替一个没发生的动作邀功，
    而且会让用户以为每轮都在重挑——正好和这一片给他的保证相反。"""
    service, user, book, conversation = await _open(db_session)
    await _ask(service, conversation, user, ABOUT_GOTAMA)

    _no_bm25(monkeypatch)
    events = await _ask(service, conversation, user, "那他后来又去了哪里？")

    assert [n for n, _ in events if n == "stage"] == []


async def test_整本装得下就不说挑章节(db_session, fake_brain, monkeypatch):
    _no_bm25(monkeypatch)
    service, user, book, conversation = await _open(db_session, budget=10**6)

    events = await _ask(service, conversation, user, ABOUT_GOTAMA)

    assert [n for n, _ in events if n == "stage"] == []


async def test_越权的会话拿不到别人的锁定(db_session, fake_brain):
    _, book = await _reader_with_big_book(db_session)
    stranger = User(email="stranger@example.com", password_hash="x")
    db_session.add(stranger)
    await db_session.flush()
    hidden = await db_session.scalar(select(Conversation))

    assert hidden is None  # 还没开会话，下面那条就是纯粹的越权探测
    with pytest.raises(NotFoundError):
        await ChatService(db_session).ask(
            conversation_id=999,
            user_id=stranger.id,
            question=ABOUT_GOTAMA,
            brain_key=FAKE_KEY,
        )
    assert FakeBrain.calls == []


async def test_两种409分得开_前端不必去匹配中文文案():
    """「问到了没载入的章节」和「上一个问题还在回答」同为 409，前端要区别对待：
    一个弹确认，一个只要让人等。靠中文文案分辨它们，等于把界面逻辑挂在一句
    随时会被润色的话上——改坏了还不报错。"""
    from booksoul_api.main import _api_error_handler

    hit = await _api_error_handler(None, ReselectRequired("问到了《迦摩罗》"))
    assert hit.status_code == 409
    assert json.loads(hit.body)["code"] == "reselect_required"

    busy = await _api_error_handler(None, ConflictError("上一个问题还在回答"))
    assert busy.status_code == 409
    # 没有 code 的错误不该凭空多一个字段出来
    assert "code" not in json.loads(busy.body)


def test_命中的词按贡献排_不按次数排():
    """次数最多的词常常正是最没用的那个。

    《悉达多》里「悉达」出现在每一节，idf 接近 0，对分数几乎没有影响；
    按次数排，十六行会显示同样三个词，那一列就废了——而它要回答的是
    「为什么是这一节」。
    """
    from booksoul_api.services import chapter_selector as selector

    chapters = [
        selector.ChapterIn(
            id=n,
            idx=n,
            title=title,
            word_count=CHARS,
            content=((seed) * 40)[:CHARS],
        )
        for n, (title, seed) in enumerate(SEEDS.items(), 1)
    ]
    choice = selector.choose(
        chapters=chapters, question=ABOUT_GOTAMA, budget_chars=CHARS * 2 + 10
    )

    gotama = next(i for i in choice.items if i.chapter_title == "乔达摩")
    terms = gotama.matched_terms
    assert terms, "总得有词，否则那一列永远是空的"

    # 「法义」只出现在这一节，idf 最高，所以排在最前；
    # 「佛」次数更多（44 > 42），但另外几节也有，贡献反而小。
    assert {t for t, _ in terms[:3]} == {"义", "法", "法义"}
    counts = [c for _, c in terms]
    assert counts != sorted(counts, reverse=True), (
        "按次数排和按贡献排在这份夹具上没区别，这个测试就没验到东西"
    )


def test_跨词碎片不许当成理由写进留档():
    """问「拒绝追随佛陀」会切出「随佛」——它来自「追随佛陀」的中间，不是词。

    这种碎片全书只出现一两次，idf 高得离谱，按贡献排会冲到第一位；
    那一列的头条写着「随佛×2」，用户完全看不懂那是什么。
    """
    from booksoul_api.services import chapter_selector as selector

    chapters = [
        selector.ChapterIn(
            id=n, idx=n, title=title, word_count=CHARS, content=(seed * 40)[:CHARS]
        )
        for n, (title, seed) in enumerate(SEEDS.items(), 1)
    ]
    choice = selector.choose(
        chapters=chapters,
        question="悉达多为什么拒绝追随佛陀？",
        budget_chars=CHARS * 2 + 10,
    )

    shown = [(t, c) for item in choice.items for t, c in item.matched_terms]
    assert shown, "总得有词，否则那一列永远是空的"
    # 出现一两次的不算理由。唯一的例外是「这一节只有这么一个词」，
    # 那时留一个比留一片空白诚实。
    thin = [
        item
        for item in choice.items
        if any(c < 3 for _, c in item.matched_terms) and len(item.matched_terms) > 1
    ]
    assert thin == []


async def test_留档写不进去时整轮什么都不落_而且宣布的是error(
    db_session, fake_brain, monkeypatch
):
    """合并之后，一轮的五样东西**同时存在或同时不存在**。

    合并之前它们分在两个事务里：留档与锁定在调模型**之前**落，回答与引文在
    之后落。崩在中间会留下一行孤立的 `retrievals` —— 一份「凭什么是这几节」的
    解释，配不上任何一条回答；而锁定已经落下，下一轮照着它走。

    另一半同样要紧：落库失败时宣布的必须是 `error`。宣布 `done` 而库里什么都
    没有，客户端会立刻回头拉一次，看到的是一个不存在的空洞——一条它刚刚亲眼
    看着流完的回答。`backend.md`「接口与响应」写的就是这条：先落库、后宣布。
    """
    from booksoul_api.repositories.retrieval_repo import RetrievalRepository

    service, user, book, conversation = await _open(db_session)

    async def 写不进去(self, **kw):
        raise RuntimeError("留档写不进去")

    monkeypatch.setattr(RetrievalRepository, "save", 写不进去)

    events = await _ask(service, conversation, user, ABOUT_GOTAMA)
    assert events[-1][0] == "error", events[-1]

    await db_session.commit()
    assert not await db_session.scalar(
        select(func.count()).select_from(Message).where(Message.role == "assistant")
    )
    assert not await db_session.scalar(select(func.count()).select_from(Retrieval))
    assert not await db_session.scalar(select(func.count()).select_from(RetrievalItem))
    await db_session.refresh(conversation)
    assert not conversation.selected_chapter_ids


async def test_一轮跑完那五样东西一起在库里(db_session, fake_brain):
    """合并之后的另一半：成功那一轮，五样东西**一起**在。

    它和「整轮什么都不落」那条是一对。只测失败那半边，一个把写入整段删掉的
    改动也能全绿；只测成功那半边，回到两个事务照样全绿。
    """
    service, user, book, conversation = await _open(db_session)
    await _ask(service, conversation, user, ABOUT_GOTAMA)
    await db_session.commit()

    assert await db_session.scalar(
        select(func.count()).select_from(Message).where(Message.role == "assistant")
    )
    assert await db_session.scalar(select(func.count()).select_from(Retrieval))
    assert await db_session.scalar(select(func.count()).select_from(RetrievalItem))
    await db_session.refresh(conversation)
    assert conversation.selected_chapter_ids


async def test_一节正文都没载入时就地停住_不往模型送(db_session, fake_brain):
    """Grill C10 那条连锁的最后一道闸，打在边界上。

    一节都没载入时，模型收到的是一份**只有目录、没有正文**的书。它不会说
    「我没拿到正文」——它会对着章节名把内容编出来，语气和读过一样。整条路上
    没有任何一处会报错，用户看到的是一个流畅、自信、完全虚构的回答。

    这里把磁盘上的正文文件掏空来造那一刻：库里索引还在（章节名、字数都在），
    盘上一个字都没有。断言两件事——**一次模型都没调**，以及报错给了出路。
    """
    service, user, book, conversation = await _open(db_session)
    root = Path(get_settings().library_root) / book.storage_path
    for one in (root / "chapters").glob("*.md"):
        one.write_text("", encoding="utf-8")

    events = await _ask(service, conversation, user, ABOUT_GOTAMA)

    assert fake_brain.calls == [], "一节正文都没有就不该往模型送"
    failed = [p for n, p in events if n == "failed"]
    assert failed, events
    assert "重新解析" in failed[0]["message"], "报错要给出路（Spec N1）"


# ------------------------------------------------------------------ 预算从哪来
#
# 三级，前一级有就不看后一级（Spec N9）：用户手调的载入预算 > 开会话时快照的
# 正文额度 > 老路径（按模型名查内置表）。快照为 0 的是升级之前开的会话。
#
# 用 ⑥屏那一份留档的 `budget_chars` 作证：它记的就是这一轮真正拿去挑章节的那个数。


async def _open_on_endpoint(db_session, *, tokens: int, budget: int = 0):
    """在一个自己加的端点上开会话。模型名刻意取内置表认不出的那种。"""
    from booksoul_api.schemas.brain import BrainCreate
    from booksoul_api.services.brain_service import BrainService

    user, book = await _reader_with_big_book(db_session)
    await BrainService(db_session).add_custom(
        user_id=user.id,
        payload=BrainCreate(
            name="书房那台",
            model="my-model",
            base_url="https://llm.example.com/v1",
            context_tokens=tokens,
        ),
    )
    service = ChatService(db_session)
    conversation = await service.start(
        user_id=user.id,
        book_id=book.id,
        persona="scholar",
        brain_name="书房那台",
        budget_chars=budget,
    )
    return service, user, book, conversation


async def test_开会话快照的额度就是设置页显示的那个数(db_session, fake_brain):
    from booksoul_api.services.brain_service import BrainService

    service, user, book, conversation = await _open_on_endpoint(
        db_session, tokens=32_768
    )
    qwen = await service.start(
        user_id=user.id, book_id=book.id, persona="scholar", brain_name="qwen"
    )

    shown = {
        b.name: b.context_chars
        for b in await BrainService(db_session).list_for_user(user_id=user.id)
    }
    assert conversation.context_chars == shown["书房那台"]
    assert qwen.context_chars == shown["qwen"]


async def test_自定义端点填了小窗口_提问时按它挑章节(db_session, fake_brain):
    """今天：模型名认不出 → 按 10 万字算 → 整本送 → 供应商报「上下文超长」。"""
    from booksoul.ask import budget_from_context_tokens

    small = 1_530  # 折算下来 1300 字：够两节
    service, user, _, conversation = await _open_on_endpoint(db_session, tokens=small)

    await _ask(service, conversation, user, ABOUT_GOTAMA)
    out = await service.retrieval(conversation_id=conversation.id, user_id=user.id)

    assert out.budget_chars == budget_from_context_tokens(small)
    assert out.whole_book is False
    assert out.selected_count == 2


async def test_自定义端点填了大窗口_提问时按它判断而不是按10万字(
    db_session, fake_brain
):
    from booksoul.ask import budget_from_context_tokens

    service, user, _, conversation = await _open_on_endpoint(
        db_session, tokens=1_000_000
    )

    await _ask(service, conversation, user, ABOUT_GOTAMA)
    out = await service.retrieval(conversation_id=conversation.id, user_id=user.id)

    assert out.budget_chars == budget_from_context_tokens(1_000_000)
    assert out.whole_book is True


async def test_手调的载入预算仍然盖过快照(db_session, fake_brain):
    service, user, _, conversation = await _open_on_endpoint(
        db_session, tokens=1_000_000, budget=BUDGET
    )

    await _ask(service, conversation, user, ABOUT_GOTAMA)
    out = await service.retrieval(conversation_id=conversation.id, user_id=user.id)

    assert out.budget_chars == BUDGET
    assert out.whole_book is False


async def test_升级之前开的会话_预算照老路径逐字不变(db_session, fake_brain):
    """快照为 0 = 这一列加上之前就开着的会话。它们按模型名查内置表，和今天一样。"""
    from booksoul.ask import context_budget_chars

    service, user, _, conversation = await _open_on_endpoint(
        db_session, tokens=1_000_000
    )
    conversation.context_chars = 0  # 迁移加列时的默认值，不回填
    await db_session.commit()

    await _ask(service, conversation, user, ABOUT_GOTAMA)
    out = await service.retrieval(conversation_id=conversation.id, user_id=user.id)

    assert out.budget_chars == context_budget_chars("my-model")
