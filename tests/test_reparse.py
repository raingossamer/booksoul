"""重解析：换掉一本书的正文，而不毁掉用户已有的对话（S12）。

这一片处理的是 Grill C10 那条连锁。它的可怕之处在于**全程不报错**：

    重解析重建 chapters
      → 会话里锁定的那几个 chapter id 全都不存在了
      → 下一轮按这份名单选章，选中 0 节
      → 模型收到一份只有目录、没有正文的书
      → 它不会说「我没看到正文」，它会照着章节名把内容编出来

从用户那一侧看，是一次流畅、自信、完全虚构的回答。所以这里钉三道闸：
**锁定要清掉**、**引文要跟着搬家**、**真到了 0 节就地停住不往模型送**。

走的是真路径：书经上传解析进来（引擎换成替身，照 V1 / V2 / V3 造书），重新解析是
`BookLifecycle.reparse()` → 后台解析 → 写盘 → 换章节与善后。段号数的是 writer 真写出来
的文件：块 0 是标题，块 1 是 writer 写的那行元信息（书名 · 第几节 · 字数），正文从块 2 起。
"""

from __future__ import annotations

from fakes import engine_book

from booksoul_api.db.session import get_session_factory
from booksoul_api.models.book import Book
from booksoul_api.models.citation import Citation
from booksoul_api.models.conversation import Conversation
from booksoul_api.models.message import Message
from booksoul_api.models.parse import ParseJob
from booksoul_api.models.user import User
from booksoul_api.repositories.book_repo import BookRepository
from booksoul_api.schemas.parse import UploadAccepted
from booksoul_api.services.book_lifecycle import BookLifecycle, drain
from booksoul_api.services.citation_service import CitationService
from booksoul_api.services.ledger import Ledger
from booksoul_api.services.library_service import LibraryService

# 两节。第二节里那句「他学会了静默」在第一节里也有——重解析之后要认回它自己
# 那一节，不能被前一节抢走。
V1 = {
    "婆罗门之子": "悉达多，俊美的婆罗门之子，与他的好友乔文达一道长大。\n\n"
    "他学会了静默，学会了斋戒。",
    "乔达摩": "两位朝拜乔达摩的青年沙门，一路探询到达此地。\n\n"
    "他学会了静默，学会了斋戒。",
}

# 重解析之后：第一节前面多了一段（段号整体下移），第二节标题没变。
V2 = {
    "婆罗门之子": "【导读】这一节讲的是出走。\n\n"
    "悉达多，俊美的婆罗门之子，与他的好友乔文达一道长大。\n\n"
    "他学会了静默，学会了斋戒。",
    "乔达摩": "两位朝拜乔达摩的青年沙门，一路探询到达此地。\n\n"
    "他学会了静默，学会了斋戒。",
}

# 换了译本：那句引文在书里再也找不到了
V3 = {
    "婆罗门之子": "悉达多，英俊的婆罗门家的儿子，同好友乔文达一起长大。",
    "乔达摩": "两名前去朝拜乔达摩的年轻沙门，一路打听着来到这里。",
}


async def _book_with_talk(
    db_session, fake_parser
) -> tuple[User, Book, Conversation, list[Citation]]:
    """一本经上传解析进来的书（V1）、一个聊过一轮的会话、两条引文（一条会搬家，一条原地不动）。"""
    user = User(email="reader@example.com", password_hash="不参与本测试")
    db_session.add(user)
    await db_session.commit()  # 上传走它自己的 session，看不见没提交的行

    fake_parser.book = engine_book("悉达多", V1)
    async with get_session_factory()() as request:
        with open(__file__, "rb") as original:  # 原件内容无所谓：解析换成了替身
            accepted = await BookLifecycle(request).upload(
                user_id=user.id, filename="悉达多.epub", source=original
            )
    await drain()

    book = await db_session.get(Book, accepted.book_id)
    rows = await BookRepository(db_session).list_chapters(book.id)
    by_title = {row.title: row for row in rows}

    conversation = Conversation(
        user_id=user.id,
        book_id=book.id,
        persona="scholar",
        brain_name="claude",
        model="claude-opus-5",
        title="他为什么拒绝？",
        # 锁定：重解析之后这两个 id 就不存在了
        selected_chapter_ids=[row.id for row in rows],
    )
    db_session.add(conversation)
    await db_session.flush()

    answer = Message(
        conversation_id=conversation.id,
        seq=2,
        turn=1,
        role="assistant",
        persona="scholar",
        content="见原文。",
        complete=True,
    )
    db_session.add_all(
        [
            Message(
                conversation_id=conversation.id,
                seq=1,
                turn=1,
                role="user",
                content="他为什么拒绝？",
                complete=True,
            ),
            answer,
        ]
    )
    await db_session.flush()

    citations = [
        Citation(
            message_id=answer.id,
            chapter_id=by_title["婆罗门之子"].id,
            chapter_title="婆罗门之子",
            quote="悉达多，俊美的婆罗门之子",
            para_index=2,
        ),
        Citation(
            message_id=answer.id,
            chapter_id=by_title["乔达摩"].id,
            chapter_title="乔达摩",
            quote="他学会了静默，学会了斋戒。",
            para_index=3,
        ),
    ]
    db_session.add_all(citations)
    await db_session.commit()
    return user, book, conversation, citations


async def _reparse_to(
    fake_parser, user: User, book: Book, bodies: dict[str, str]
) -> tuple[UploadAccepted, Ledger]:
    """把这本书重新解析成 `bodies` 那一版：比较交换、后台解析、换章节与善后，全走真路径。

    返回回执与这次任务的账——引文重定位那一笔记在账本上。
    """
    fake_parser.book = engine_book("悉达多", bodies)
    async with get_session_factory()() as request:
        receipt = await BookLifecycle(request).reparse(book_id=book.id, user_id=user.id)
    await drain()
    async with get_session_factory()() as fresh:
        job = await fresh.get(ParseJob, receipt.job_id)
    assert job.error_code == "", job.error_message
    return receipt, Ledger.from_json(job.cleanup_stats)


async def _chapters_by_title(db_session, book: Book) -> dict:
    return {
        row.title: row
        for row in await BookRepository(db_session).list_chapters(book.id)
    }


# ------------------------------------------------------------------ 锁定


async def test_重解析清掉会话的选章锁定(db_session, fake_parser):
    """不清掉，下一轮会按一份全是死 id 的名单去选章，选中 0 节。"""
    user, book, conversation, _cites = await _book_with_talk(db_session, fake_parser)
    assert conversation.selected_chapter_ids

    await _reparse_to(fake_parser, user, book, V2)

    await db_session.refresh(conversation)
    assert conversation.selected_chapter_ids == []


# ------------------------------------------------------------------ 引文搬家


async def test_正文没动的那条原地不动动了的那条跟着搬家(db_session, fake_parser):
    user, book, _conv, cites = await _book_with_talk(db_session, fake_parser)
    old_ids = [c.id for c in cites]

    _receipt, ledger = await _reparse_to(fake_parser, user, book, V2)
    by_title = await _chapters_by_title(db_session, book)

    moved, stayed = [
        await db_session.get(Citation, cid, populate_existing=True) for cid in old_ids
    ]
    # 第一节前面多插了一段，这条引文的块号从 2 挪到 3
    assert moved.para_index == 3
    assert moved.chapter_id == by_title["婆罗门之子"].id
    # 第二节一个字没改，块号不变——但 chapter_id 换了新行（旧行已经被删掉）
    assert stayed.para_index == 3
    assert stayed.chapter_id == by_title["乔达摩"].id

    # 两条都找回；位置变了的只有一条，另一条原地不动
    moves = ledger.relocation
    assert (moves.total, moves.located, moves.lost) == (2, 2, 0)
    assert moves.shifted == 1, "第一条真的动了位置"
    assert moves.unchanged == 1


async def test_同名章节优先不让前一节抢走后一节的引文(db_session, fake_parser):
    """「他学会了静默」两节里都有。认错了，用户点过去看到的是一段他没引的话。"""
    user, book, _conv, cites = await _book_with_talk(db_session, fake_parser)
    stayed_id = cites[1].id

    await _reparse_to(fake_parser, user, book, V2)
    by_title = await _chapters_by_title(db_session, book)

    stayed = await db_session.get(Citation, stayed_id, populate_existing=True)
    assert stayed.chapter_id == by_title["乔达摩"].id


async def test_正文被改写之后降级而不是猜一个最近的(db_session, fake_parser):
    """换了译本。这时**没有正确答案**，给一个灰按钮比给一条错链接好得多。"""
    user, book, _conv, cites = await _book_with_talk(db_session, fake_parser)
    lost_id = cites[0].id

    _receipt, ledger = await _reparse_to(fake_parser, user, book, V3)

    lost = await db_session.get(Citation, lost_id, populate_existing=True)
    assert lost.para_index == -1
    assert lost.chapter_id is None
    # 标题快照留着：界面还能说「原《婆罗门之子》里这段，位置已变更」
    assert lost.chapter_title == "婆罗门之子"
    assert ledger.relocation.lost == 2
    assert ledger.relocation.located == 0


async def test_搬过家的引文在接口上带着relocated标记(db_session, fake_parser):
    """界面据此区分「当时就定位到的」和「重解析之后重新找回来的」。

    读那一下换一个 session，和线上一样：重定位跑在解析任务里，读引文是另一个
    请求。同一个 session 里读不到新值也不是 bug——`expire_on_commit=False`，
    而一次普通 SELECT 不会覆盖身份映射里已经载入的属性。这两条合起来是这个
    项目踩过好几次的那个坑（S10 的会话锁定就是同一个），所以这里照着线上写。
    """
    user, book, _conv, cites = await _book_with_talk(db_session, fake_parser)
    message_id = cites[0].message_id

    before = await CitationService(db_session).for_message(
        message_id=message_id, user_id=user.id
    )
    assert [c.relocated for c in before] == [False, False]

    await _reparse_to(fake_parser, user, book, V2)

    async with get_session_factory()() as reader:
        after = await CitationService(reader).for_message(
            message_id=message_id, user_id=user.id
        )
    assert all(c.relocated for c in after), "重定位过的引文要能被认出来"
    assert all(c.located for c in after)


# ------------------------------------------------------------------ 受影响会话数


async def test_重解析回执与书详情都说得出受影响几个会话(db_session, fake_parser):
    """两处都要给，因为它们回答的是两个问题。

    ④屏那个确认框问的是「值不值得」——按下去之前就得知道有几个会话会被清掉
    锁定、下一轮要按全价重算。回执说的是「实际动了多少」，那是真正动手那一刻
    数出来的，中间可能又开了一个会话，两个数不保证相等。
    """
    user, book, _conv, _cites = await _book_with_talk(db_session, fake_parser)

    detail = await LibraryService(db_session).book_detail(
        book_id=book.id, user_id=user.id
    )
    assert detail.conversation_count == 1

    receipt, _ledger = await _reparse_to(fake_parser, user, book, V2)
    assert receipt.affected_conversations == 1


# ------------------------------------------------------------------ 0 节那道闸


# Grill C10 那条连锁的最后一道闸——「一节正文都没载入就地停住」——改在边界上断言，
# 见 `test_retrieval.py::test_一节正文都没载入时就地停住_不往模型送`：那里把磁盘上的
# 正文掏空，走完整条提问路径，断言一次模型都没调。原先那条搭三个假 dataclass 去调
# `TurnRunner._must_have_content`，钉的是一个私有函数的签名，而不是那道闸。


# ------------------------------------------------------------------ ②屏那本账


async def test_解析报告只在真有引文搬家时才有引文重定位那一行(db_session, fake_parser):
    """首次解析一条引文都没有，账上不该有这一行；重解析搬过引文，就得说得出搬了什么。

    账本行怎么说，单元测试在 `test_ledger.py`；这里看的是真路径上那一笔有没有记下。
    """
    user, book, _conv, _cites = await _book_with_talk(db_session, fake_parser)
    library = LibraryService(db_session)

    first = await library.report(book_id=book.id, user_id=user.id)
    assert all(row.label != "引文重定位" for row in first.ledger)

    await _reparse_to(fake_parser, user, book, V2)

    again = await library.report(book_id=book.id, user_id=user.id)
    line = next(row for row in again.ledger if row.label == "引文重定位")
    assert line.detail.startswith("2 条旧引文，找回 2 条（其中 1 条位置变了）")
