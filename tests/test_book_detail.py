"""书籍详情与解析报告（界面稿④屏、②屏）。

这两屏回答的是同一句话：**解析成功不等于解析正确**。
所以测试关心的不是「字段齐不齐」，而是「它有没有在该说实话的地方说实话」。
"""

from __future__ import annotations

import pytest

from booksoul_api.core.errors import NotFoundError
from booksoul_api.models.book import Book
from booksoul_api.models.user import User
from booksoul_api.services.library_service import LibraryService


async def _reader(session) -> User:
    user = User(email="reader@example.com", password_hash="不参与本测试")
    session.add(user)
    await session.flush()
    return user


async def test_详情给出目录树与字数并标出该核对的章节(db_session, imported_book):
    reader, book = imported_book

    detail = await LibraryService(db_session).book_detail(
        book_id=book.id, user_id=reader.id
    )

    assert detail.title == "悉达多"
    assert len(detail.chapters) == 16
    assert [c.idx for c in detail.chapters] == list(range(1, 17))
    assert all(c.file_path.startswith("chapters/") for c in detail.chapters)
    assert sum(c.word_count for c in detail.chapters) > 40000

    # 字数条是质检工具：短到不像正文的那几节要被标出来，而不是混在里面
    flagged = [c for c in detail.chapters if c.short]
    assert all(c.word_count < 100 for c in flagged)


async def test_元信息空就是空_不替来源猜译者(db_session, imported_book):
    """④屏那条「未标注」不是客套话。

    OPF 里那个人没被标角色，我们就记成贡献者，不升格成译者 ——
    因为人格提示词里会出现「译者」，猜错了它会一路错下去。
    """
    reader, book = imported_book

    detail = await LibraryService(db_session).book_detail(
        book_id=book.id, user_id=reader.id
    )

    assert detail.author
    # 译者要么来源明确标了，要么留空 —— 不允许从 contributor 顺手推过来
    if detail.translator:
        assert detail.translator != detail.contributor or not detail.contributor


async def test_核对过的存疑不再计入书库卡片(db_session, imported_book):
    """核对完还被同一条打扰，用户下次就不看了。"""
    reader, book = imported_book
    library = LibraryService(db_session)

    detail = await library.book_detail(book_id=book.id, user_id=reader.id)
    unresolved = [n for n in detail.notes if not n.resolved]
    if not unresolved:
        pytest.skip("这本书解析得很干净，没有存疑条目可核对")

    before = (await library.list_books(user_id=reader.id)).items[0].note_count
    await library.resolve_note(
        book_id=book.id, note_id=unresolved[0].id, user_id=reader.id, resolved=True
    )
    after = (await library.list_books(user_id=reader.id)).items[0].note_count

    assert after == before - 1


async def test_解析报告只列真数出来的项且产出清单来自磁盘(db_session, imported_book):
    """报告的全部价值在于它没有假话。

    引擎没报的项整行不出现，而不是填 0 —— 填 0 会让人以为「一处都没动」。
    产出清单直接读磁盘，因为它的意思正是「这些文件你可以拿走」。
    """
    reader, book = imported_book

    report = await LibraryService(db_session).report(book_id=book.id, user_id=reader.id)

    labels = [row.label for row in report.ledger]
    assert "格式" in labels
    assert "目录来源" in labels
    assert "脚注" in labels
    # 没数出来的那些不许出现
    assert "硬换行" not in labels
    assert "命名空间" not in labels

    toc_row = next(r for r in report.ledger if r.label == "目录来源")
    assert "toc.ncx" in toc_row.detail or "nav" in toc_row.detail
    assert "切出 16 节" in toc_row.detail

    names = {f.path for f in report.outputs}
    assert {"book.md", "INDEX.md", "meta.json"} <= names
    assert "chapters/" in names
    chapters_dir = next(f for f in report.outputs if f.path == "chapters/")
    assert chapters_dir.is_dir and chapters_dir.child_count == 16
    assert all(f.size_bytes > 0 for f in report.outputs if not f.is_dir)

    assert report.source_size_bytes > 0, "原始文件没留住，重新解析就无从谈起"
    assert report.elapsed_seconds is not None and report.elapsed_seconds >= 0


async def test_装得进哪个大脑用的是引擎那张表(db_session, imported_book):
    """界面说「装得下」而选章说「装不下」是最糟的一种不一致。

    所以额度直接问引擎的 context_budget_chars，不在 Web 层复制一份。
    窗口是估计的那一行不下结论——猜出来的「装得下」和查得到的长得一模一样。
    """
    from booksoul.ask import context_budget_chars

    reader, book = imported_book

    detail = await LibraryService(db_session).book_detail(
        book_id=book.id, user_id=reader.id
    )

    assert detail.brains, "一个大脑都没列出来"
    for fit in detail.brains:
        assert fit.context_chars == context_budget_chars(fit.model)
        if fit.context_source == "estimate":
            assert fit.fits is None
        else:
            assert fit.fits == (0 < detail.word_count <= fit.context_chars)
    # 上下文从大到小排，用户一眼看到最宽裕的那个
    assert [b.context_chars for b in detail.brains] == sorted(
        (b.context_chars for b in detail.brains), reverse=True
    )


async def test_别人的书详情与报告都取不到(db_session):
    owner = await _reader(db_session)
    stranger = User(email="stranger@example.com", password_hash="x")
    db_session.add(stranger)
    await db_session.flush()

    # storage_path 不能省：ck_books_ready_has_path 不许 ready 态的书没有目录，
    # 而那条约束正是用来挡住「看起来解析成功了其实什么都没写」的。
    book = Book(
        user_id=owner.id, title="别人的书", status="ready", storage_path="9/9-别人的书"
    )
    db_session.add(book)
    await db_session.commit()

    library = LibraryService(db_session)
    with pytest.raises(NotFoundError):
        await library.book_detail(book_id=book.id, user_id=stranger.id)
    with pytest.raises(NotFoundError):
        await library.report(book_id=book.id, user_id=stranger.id)


async def test_书库卡片带上未核对的存疑条数(db_session):
    """字数和存疑条数都是质检信号，不是装饰。

    ③屏那句「2 处存疑」是「解析成功不等于解析正确」唯一的界面出口；
    已核对过的不再计数，否则用户核对完了还被同一条打扰。
    """
    from booksoul_api.models.parse import ParseNote

    reader = await _reader(db_session)
    book = Book(user_id=reader.id, title="悉达多", status="ready", storage_path="1/1-x")
    db_session.add(book)
    await db_session.flush()
    db_session.add_all(
        [
            ParseNote(book_id=book.id, message="第 3 节只有 5 个字", resolved=False),
            ParseNote(book_id=book.id, message="编码是猜的", resolved=False),
            ParseNote(book_id=book.id, message="这条我核对过了", resolved=True),
        ]
    )
    await db_session.commit()

    page = await LibraryService(db_session).list_books(user_id=reader.id)

    assert page.items[0].note_count == 2
