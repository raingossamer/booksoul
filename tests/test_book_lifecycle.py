"""一本书的生命周期：从收件到可用，中间每一步的状态迁移。

这里的几件事走错了多半不报错：

* 收件之后谁来排解析——生命周期自己排，调用方没有第二步；漏排就是一本永远「解析中」的书。
* 每条结局都落在 ready 或 failed：引擎失败、写库失败、进度泵出错（它不决定结局）、进程重启。
* 给人看的话只从码表来：引擎的原话（安装命令、服务器路径）进日志，不进界面。
* 能不能读、能不能问、能不能重解析——一张门表说了算。原先三处各判：对话非 ready 即拒、
  重解析只拦正在解析、阅读器根本不看状态，同一种状态回的码与话各不相同。
* 两个几乎同时的「重新解析」只能有一个开跑（比较交换）。
* 在用的目录从不被原地改写：暂存、提交之后整个换上去；崩在中间由启动清扫照库里的话补完。
* 换章节的善后次序——先会话行、后章节行。反过来的话，重解析撞上一轮提问的收尾，
  两边各拿一把锁、互等另一把，库只能杀掉一边；杀到收尾那边，就是丢一条已付费的回答。
  这个次序在全仓库只写一份（`resettle.swap()`），这里在库里看它，而不是读代码看它。

引擎换成替身（conftest 的 `fake_parser`，挂在 `booksoul.parsers`），写盘、落库、换章节全走
真代码；时序靠替身的闸门与**外来事务持锁**来造，不靠 sleep 赌。
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from itertools import pairwise
from pathlib import Path

import pytest
from fakes import engine_book
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from test_turn_state import _until_someone_waits_on_a_lock

from booksoul_api.core.config import get_settings
from booksoul_api.core.errors import (
    ApiError,
    BookNotReady,
    NotFoundError,
    UnsupportedFormat,
)
from booksoul_api.db.session import get_session_factory
from booksoul_api.models.book import Book
from booksoul_api.models.conversation import Conversation
from booksoul_api.models.parse import ParseJob
from booksoul_api.models.user import User
from booksoul_api.repositories.book_repo import BookRepository
from booksoul_api.repositories.parse_repo import ParseRepository
from booksoul_api.schemas.parse import UploadAccepted
from booksoul_api.services import format_service, resettle
from booksoul_api.services.book_lifecycle import (
    BookLifecycle,
    Use,
    drain,
    source_path,
    sweep_orphans,
)
from booksoul_api.services.ledger import Ledger
from booksoul_api.services.library_service import LibraryService
from booksoul_api.services.reader_service import ReaderService

_SAMPLE = Path(__file__).resolve().parent.parent / "悉达多.epub"

needs_sample = pytest.mark.skipif(
    not _SAMPLE.is_file(), reason="样书不在，跳过需要真书的用例"
)


async def _reader(db_session, email: str = "reader@example.com") -> User:
    """一个读者，**提交**了的：上传走它自己的 session，看不见这边没提交的行。"""
    user = User(email=email, password_hash="不参与本测试")
    db_session.add(user)
    await db_session.commit()
    return user


async def _upload(user: User, path: Path, filename: str) -> UploadAccepted:
    """像一次上传请求那样：自己的 session，交完回执就关掉。解析在后台接着跑。"""
    async with get_session_factory()() as request:
        with open(path, "rb") as handle:
            return await BookLifecycle(request).upload(
                user_id=user.id, filename=filename, source=handle
            )


async def _reparse(user: User, book_id: int) -> UploadAccepted:
    """像一次「重新解析」请求那样：自己的 session。"""
    async with get_session_factory()() as request:
        return await BookLifecycle(request).reparse(book_id=book_id, user_id=user.id)


async def _book(db_session, book_id: int) -> Book:
    """从库里重新读这本书。后台解析在别的 session 里写，手上那个对象不会自己变。"""
    return await db_session.get(Book, book_id, populate_existing=True)


async def _state_when(user: User, book_id: int, ready, timeout: float = 5.0):
    """另开连接反复读这本书的 `state()`，直到 `ready(state)` 成立——像界面轮询那样。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        async with get_session_factory()() as probe:
            state = await BookLifecycle(probe).state(book_id=book_id, user_id=user.id)
        if ready(state):
            return state
        if loop.time() > deadline:
            raise AssertionError(f"等了 {timeout} 秒，状态还是这样：{state!r}")
        await asyncio.sleep(0.02)


def _chapter(idx: int, title: str) -> dict:
    return {
        "idx": idx,
        "title": title,
        "level": 1,
        "word_count": 3000,
        "file_path": f"chapters/{idx:03d}-{title}.md",
        "source_id": "",
    }


async def _book_someone_is_talking_about(db_session) -> tuple[Book, int, int]:
    """一本就绪的书、一节章节、一个锁定着它的会话。返回 (书, 章节 id, 会话 id)。"""
    user = User(email="reader@example.com", password_hash="不参与本测试")
    db_session.add(user)
    await db_session.flush()
    book = Book(
        user_id=user.id,
        title="悉达多",
        status="ready",
        storage_path=f"{user.id}/1-悉达多",
        chapter_count=1,
        word_count=3000,
    )
    db_session.add(book)
    await db_session.flush()
    [chapter] = await BookRepository(db_session).replace_chapters(
        book.id, [_chapter(1, "婆罗门之子")]
    )
    conversation = Conversation(
        user_id=user.id,
        book_id=book.id,
        persona="scholar",
        brain_name="claude",
        model="claude-opus-5",
        title="他为什么离开？",
        selected_chapter_ids=[chapter.id],
    )
    db_session.add(conversation)
    await db_session.commit()
    return book, chapter.id, conversation.id


def _lock_refused(exc: BaseException | None) -> bool:
    """这个异常是不是「行锁拿不到」（SQLSTATE 55P03）。驱动把它包了几层，一层层找。"""
    seen = exc
    while seen is not None:
        if getattr(seen, "sqlstate", None) == "55P03":
            return True
        seen = seen.__cause__ or getattr(seen, "orig", None)
    return False


async def _lock_conversation_now(conversation_id: int) -> DBAPIError | None:
    """另开一条连接对会话行 `FOR UPDATE NOWAIT`。拿到了返回 None，拿不到返回那个异常。"""
    async with get_session_factory()() as probe:
        try:
            await probe.execute(
                text("SELECT id FROM conversations WHERE id = :id FOR UPDATE NOWAIT"),
                {"id": conversation_id},
            )
        except DBAPIError as refused:
            return refused
    return None


# ------------------------------------------------------------------ 收件与后台解析


@needs_sample
async def test_上传之后不用谁再排一次解析就会跑完(db_session):
    """只调 `upload()`：解析由生命周期自己排，调用方没有第二步。

    原先上传与重解析两个端点各写一遍 `background.add_task(...)`，哪一处漏了，
    就是一本永远「解析中」的书。
    """
    reader = await _reader(db_session)
    accepted = await _upload(reader, _SAMPLE, "悉达多.epub")
    await drain()

    book = await BookLifecycle(db_session).require(
        book_id=accepted.book_id, user_id=reader.id, use=Use.ASK
    )
    assert book.title == "悉达多"
    assert book.chapter_count == 16
    assert book.word_count > 40000
    # 目录名含 user_id 与 book_id：两个人各传一本《悉达多》各占各的目录
    assert book.root == (
        Path(get_settings().library_root) / f"{reader.id}/{book.id}-悉达多"
    )
    assert (book.root / "book.md").is_file()
    assert (book.root / "meta.json").is_file()
    assert len(list((book.root / "chapters").iterdir())) == 16

    chapters = await BookRepository(db_session).list_chapters(book.id)
    assert [c.idx for c in chapters] == list(range(1, 17))
    assert all(c.file_path.startswith("chapters/") for c in chapters)

    job = await db_session.get(ParseJob, accepted.job_id)
    assert job.finished_at is not None
    assert job.error_code == ""
    assert job.detected_format == "epub"
    assert Ledger.from_json(job.cleanup_stats).chapters == 16


@needs_sample
async def test_扩展名是错的也按内容解析(db_session, tmp_path):
    """下载来的书扩展名经常是错的：格式按魔数判，不看扩展名。"""
    renamed = tmp_path / "悉达多.txt"
    renamed.write_bytes(_SAMPLE.read_bytes())

    reader = await _reader(db_session)
    accepted = await _upload(reader, renamed, "悉达多.txt")
    await drain()

    book = await _book(db_session, accepted.book_id)
    assert book.status == "ready"
    assert book.source_format == "epub"


@pytest.fixture
def uninstall(monkeypatch):
    """让这个部署「没装」某几个解析后端，重新探测。测试结束把探测结果清掉。"""

    def missing(*modules: str) -> None:
        monkeypatch.setattr(
            format_service, "_installed", lambda module: module not in modules
        )
        format_service.capability.cache_clear()

    yield missing
    format_service.capability.cache_clear()


_NO_PDF = ("pymupdf", "fitz", "pypdf")


async def test_超限空文件与部署不支持的格式在建行之前就被拒(
    db_session, monkeypatch, tmp_path, uninstall
):
    """三种都在建任何一行之前拒：不留没有文件的书，也不留没有书的文件。

    「认得扩展名、这个部署却没装后端」原先照样收下、建行、存原件，解析失败之后再给一句
    「点重新解析再试一次」——那条出路永远走不通。
    """
    reader = await _reader(db_session)

    monkeypatch.setattr(get_settings(), "max_upload_mb", 1)
    big = tmp_path / "大部头.epub"
    big.write_bytes(b"x" * (1024 * 1024 + 1))
    with pytest.raises(ApiError) as too_big:
        await _upload(reader, big, "大部头.epub")
    assert too_big.value.status_code == 413

    empty = tmp_path / "空的.epub"
    empty.write_bytes(b"")
    with pytest.raises(ApiError) as nothing:
        await _upload(reader, empty, "空的.epub")
    assert nothing.value.status_code == 400

    uninstall(*_NO_PDF)
    pdf = tmp_path / "一本书.pdf"
    pdf.write_bytes(b"%PDF-1.4 not really")
    with pytest.raises(UnsupportedFormat) as unsupported:
        await _upload(reader, pdf, "一本书.pdf")
    assert unsupported.value.status_code == 415
    assert unsupported.value.code == "unsupported_format"
    assert "PDF" in str(unsupported.value)
    assert "pip" not in str(unsupported.value), "装包是管理员的事，不是传书的人的"

    assert await db_session.scalar(select(func.count()).select_from(Book)) == 0
    files = [p for p in Path(get_settings().upload_root).rglob("*") if p.is_file()]
    assert files == []


async def test_失败说明只来自码表_原文进日志(db_session, fake_parser, caplog):
    """引擎的原话是写给装它、修它的人看的：安装命令、服务器上的路径、异常链。

    给读书的人看的那一句只从码表来；原话连同书与任务的 id 进 ERROR 日志，排障照着它查。
    """
    engine_says = "解析 PDF 需要 pymupdf：pip install pymupdf。原件在 /srv/booksoul/uploads/1/1/x.pdf"
    fake_parser.fail = RuntimeError(engine_says)
    reader = await _reader(db_session)
    accepted = await _upload(reader, Path(__file__), "一本书.epub")
    await drain()

    state = await BookLifecycle(db_session).state(
        book_id=accepted.book_id, user_id=reader.id
    )
    job = await db_session.get(ParseJob, accepted.job_id)
    assert state.status == "failed"
    for said in (state.message, state.remedy, job.error_message, job.remedy):
        assert said, "停下了就要说清楚停在哪、能做什么"
        assert "pip" not in said and "/srv/" not in said, said

    logged = [
        r.getMessage()
        for r in caplog.records
        if r.levelno >= logging.ERROR and engine_says in r.getMessage()
    ]
    assert logged, "原话要进日志，排障才有据可查"
    assert f"任务 {accepted.job_id}" in logged[0]


async def test_重新解析时后端已经没了_说换格式而不是再点一次重新解析(
    db_session, fake_parser, uninstall
):
    """上传时装着、重新解析时没了（部署换过镜像）。这时候叫人「再点一次重新解析」是一条
    永远走不通的路——说清楚这个部署现在不支持，换一个格式再传。"""
    uninstall()  # 先当作什么都装了：别让这台机器的实际安装情况左右前半段
    fake_parser.fmt = "pdf"
    reader = await _reader(db_session)
    first = await _upload(reader, Path(__file__), "一本书.pdf")
    await drain()

    uninstall(*_NO_PDF)
    await _reparse(reader, first.book_id)
    await drain()

    state = await BookLifecycle(db_session).state(
        book_id=first.book_id, user_id=reader.id
    )
    assert state.status == "failed"
    assert state.error_code == "unsupported_format"
    assert "重新解析" not in state.remedy
    assert "格式" in state.remedy
    assert "pip" not in state.message and "pip" not in state.remedy


async def test_提交没成_搬过去的原件不留下(db_session, monkeypatch):
    """原件先搬进上传目录，那一行才提交。提交没成，那份原件没有任何一行指着它。"""
    reader = await _reader(db_session)

    async def cut_off():
        raise ConnectionError("提交的那一刻连接断了")

    async with get_session_factory()() as request:
        monkeypatch.setattr(request, "commit", cut_off)
        with open(__file__, "rb") as handle, pytest.raises(ConnectionError):
            await BookLifecycle(request).upload(
                user_id=reader.id, filename="一本书.epub", source=handle
            )

    left = [p for p in Path(get_settings().upload_root).rglob("*") if p.is_file()]
    assert left == [], "那一行没落下，原件却留在了上传目录里"
    assert await db_session.scalar(select(func.count()).select_from(Book)) == 0


@needs_sample
async def test_两个人各传一本同名书互不覆盖且互相看不见(db_session):
    """多用户书库的核心保证，也是 Story #3。

    目录名含 user_id 与 book_id，两本《悉达多》各占各的目录；
    书库查询在 repository 层就带上了 user_id，谁也看不到对方的书。
    """
    alice = await _reader(db_session, "alice@example.com")
    bob = await _reader(db_session, "bob@example.com")
    uploads = [await _upload(who, _SAMPLE, "悉达多.epub") for who in (alice, bob)]
    await drain()

    a, b = [await _book(db_session, u.book_id) for u in uploads]
    assert a.storage_path != b.storage_path
    assert a.storage_path.startswith(f"{alice.id}/")
    assert b.storage_path.startswith(f"{bob.id}/")

    root = Path(get_settings().library_root)
    assert (root / a.storage_path / "book.md").is_file()
    assert (root / b.storage_path / "book.md").is_file()

    library = LibraryService(db_session)
    for who, own in ((alice, a), (bob, b)):
        page = await library.list_books(user_id=who.id)
        assert [item.id for item in page.items] == [own.id]


async def test_扫描件停下来并给出带真文件名的OCR命令(db_session, tmp_path):
    """扫描版 PDF 宁可停下，也不要写出一个空 md 假装成功。

    停下来的同时给出用户自己能执行的下一步：一条**这个文件**的 OCR 命令，直接复制就能跑，
    而不是一个带占位符的模板。停下来之后这本书要能被看见（书库里是重墨的失败卡片），
    而不是永远卡在「解析中」——那比报错更让人没辙。
    """
    pymupdf = pytest.importorskip("pymupdf")

    doc = pymupdf.open()
    doc.new_page().draw_rect(pymupdf.Rect(40, 40, 300, 400), fill=(0.85, 0.85, 0.85))
    scanned = tmp_path / "扫描件.pdf"
    doc.save(str(scanned))
    doc.close()

    reader = await _reader(db_session)
    accepted = await _upload(reader, scanned, "扫描件.pdf")
    await drain()

    book = await _book(db_session, accepted.book_id)
    job = await db_session.get(ParseJob, accepted.job_id)
    assert book.status == "failed"
    assert book.storage_path is None, "失败了就不该在书库里留下半成品目录"
    assert not list(Path(get_settings().library_root).glob(f"*/{book.id}-*"))
    assert job.error_code == "no_text_layer"
    assert "ocrmypdf" in job.remedy
    assert "扫描件.pdf" in job.remedy, "命令里是这个文件的真名，不是占位符"
    assert "原文件" not in job.remedy
    assert "文字层" in job.error_message
    assert job.finished_at is not None

    assert await BookRepository(db_session).list_chapters(book.id) == []
    assert await ParseRepository(db_session).list_notes(book.id) == []

    # 失败的书仍然出现在书库里，用户才有机会重新解析它
    page = await LibraryService(db_session).list_books(user_id=reader.id)
    assert [item.status for item in page.items] == ["failed"]
    assert page.items[0].error_code == "no_text_layer", "卡片要说清停在哪"


async def test_写库那一半出错_书落在failed_章节撤回_还能再解析(db_session, fake_parser):
    """解析本身成了，存进书库那一步撞了约束：书要落在「停下了」。

    原先写库这一半不在失败保护里，后台任务就这么死了——书停在「解析中」，提问永远
    409、也不许重新解析，进度推送一直轮询下去，直到进程重启才被当成孤儿收掉。
    这里让一节的目录层级超出 SMALLINT，在数据库里真的失败。（超长的书名、章节标题
    现在按列宽截断，不再能拿来造这个失败。）
    """
    fake_parser.book = engine_book("一本书", {"第一章": "正文。" * 200})
    fake_parser.book.chapters[0].level = 40_000

    reader = await _reader(db_session)
    accepted = await _upload(reader, Path(__file__), "一本书.epub")
    await drain()

    book = await _book(db_session, accepted.book_id)
    job = await db_session.get(ParseJob, accepted.job_id)
    assert book.status == "failed"
    assert job.finished_at is not None
    assert job.error_code == "internal"
    assert "服务器这边" in job.error_message, "码表里 internal 那一句"
    assert "INSERT" not in job.error_message and "smallint" not in job.error_message, (
        "数据库的报错原文（带着 SQL 与参数）不给人看"
    )
    assert "重新解析" in job.remedy
    assert await BookRepository(db_session).list_chapters(book.id) == [], (
        "写到一半的章节要跟着撤回"
    )

    # 停下了就能再解析一次。卡在「解析中」的书，这一步是被拒绝的
    again = await _reparse(reader, book.id)
    assert again.job_id != accepted.job_id
    await drain()


async def test_书目字段超过列宽就截断_不让整本书进不了书库(db_session, fake_parser):
    """书里写的书名、作者、章节标题多长都有可能，而那几列有宽度。

    超了就在写库那一刻整本失败——为了一个显示不全也无妨的书名，丢掉整本书。
    截断时结尾换成「…」：看得出是被截的，不会被当成原文。
    """
    fake_parser.book = engine_book(
        "书" * 300, {"长" * 300: "正文。" * 200}, author="作" * 300, language="z" * 40
    )
    reader = await _reader(db_session)
    accepted = await _upload(reader, Path(__file__), "一本书.epub")
    await drain()

    state = await BookLifecycle(db_session).state(
        book_id=accepted.book_id, user_id=reader.id
    )
    assert state.status == "ready", state
    book = await _book(db_session, accepted.book_id)
    assert book.title == "书" * 254 + "…"
    assert book.author == "作" * 254 + "…"
    assert book.language == "z" * 31 + "…"
    [chapter] = await BookRepository(db_session).list_chapters(book.id)
    assert chapter.title == "长" * 254 + "…"


async def test_进度泵出错不会把一次成功的解析记成失败(db_session, fake_parser, caplog):
    """进度与心跳只是给人看的：泵出了事，这一拍没落下，结局不归它管。

    原先泵一出错就带着异常死掉，停泵那一步把异常原样抛回来，一次成功的解析被记成
    `internal`。这里让替身报一个超出 INTEGER 的进度，泵写库那一句当场失败。
    """
    from sqlalchemy import event

    from booksoul_api.db.session import get_engine

    too_big = 2**40
    fake_parser.progress = (too_big, 1)
    gate = fake_parser.hold()

    attempted = threading.Event()

    def spot(_conn, _cursor, statement, parameters, *_rest):
        if statement.startswith("UPDATE parse_jobs") and too_big in (parameters or ()):
            attempted.set()

    engine = get_engine().sync_engine
    event.listen(engine, "before_cursor_execute", spot)
    reader = await _reader(db_session)
    try:
        accepted = await _upload(reader, Path(__file__), "悉达多.epub")
        assert await asyncio.to_thread(attempted.wait, 5), "泵一直没写过进度"
    finally:
        event.remove(engine, "before_cursor_execute", spot)
        gate.set()
        await drain()

    state = await BookLifecycle(db_session).state(
        book_id=accepted.book_id, user_id=reader.id
    )
    assert state.status == "ready", state
    assert state.error_code == ""
    assert any("进度没能落库" in r.getMessage() for r in caplog.records)


async def test_书里照抄来的定位与出版信息多长都存得下(db_session, fake_parser):
    """EPUB 的来源定位是「压缩包里的路径#锚点」，路径深一点就过了 64；出版日期、
    ISBN 也是书里写什么就存什么。这几栏长了不该让整本书进不了书库（迁移 0005）。"""
    located = "OEBPS/Text/9787020024759_Siddhartha_chapter_0001.xhtml#toc_anchor_12"
    published = "2019-06-01T00:00:00.000000+08:00（第一版第三次印刷）"
    isbn = "urn:isbn:9787020024759（平装，二〇一九年第三次印刷，附赠书签）"
    assert min(len(located) - 64, len(published) - 32, len(isbn) - 32) > 0
    fake_parser.book = engine_book(
        "一本书", {"第一章": "正文。" * 200}, published=published, isbn=isbn
    )
    fake_parser.book.chapters[0].source_id = located

    reader = await _reader(db_session)
    accepted = await _upload(reader, Path(__file__), "一本书.epub")
    await drain()

    book = await _book(db_session, accepted.book_id)
    assert book.status == "ready"
    assert (book.published, book.isbn) == (published, isbn), "原样保留，不截"
    chapters = await BookRepository(db_session).list_chapters(book.id)
    assert [c.source_id for c in chapters] == [located]


@needs_sample
async def test_一本失败不影响同一批里的其余各本(db_session, tmp_path):
    """一次失败不该毁掉整批导入。

    每个文件各走各的请求、各有各的任务，本来就互不相干——这条测试钉住的是
    「失败那本的收尾不会顺手把别人的状态也改了」。
    """
    broken = tmp_path / "半本.epub"
    broken.write_bytes(_SAMPLE.read_bytes()[: 40 * 1024])

    reader = await _reader(db_session)
    for path, name in ((broken, "半本.epub"), (_SAMPLE, "悉达多.epub")):
        await _upload(reader, path, name)
    await drain()

    page = await LibraryService(db_session).list_books(user_id=reader.id)
    assert page.total == 2
    assert {item.status for item in page.items} == {"failed", "ready"}
    assert next(i for i in page.items if i.status == "ready").chapter_count == 16
    # 下载截断的 zip 是常态：说清是文件坏了，而不是一句笼统的「解析失败」
    assert next(i for i in page.items if i.status == "failed").error_code == (
        "bad_archive"
    )


async def test_解析中途的进度从另一条连接看得见(db_session, fake_parser):
    """进度泵是这一段唯一有并发的地方：解析在工作线程里跑，落库在事件循环里。

    《悉达多》150 毫秒就解析完了，快过落库的节拍，所以走查里只看得到首尾两个
    阶段——那既可能是「书太快」，也可能是「泵根本没工作」。这条测试把两者分开：
    让替身停在转换正文中途，从另一条连接读 `state()`——界面此刻看到的就是它。
    """
    fake_parser.progress = (20, 64)
    gate = fake_parser.hold()
    reader = await _reader(db_session)
    accepted = await _upload(reader, Path(__file__), "悉达多.epub")
    try:
        assert await asyncio.to_thread(fake_parser.reached.wait, 5)
        midway = await _state_when(
            reader, accepted.book_id, lambda s: s.stage == "convert"
        )
        assert midway.status == "parsing"
        assert midway.job_id == accepted.job_id
        assert (midway.done, midway.total) == (20, 64)
    finally:
        gate.set()
        await drain()

    after = await BookLifecycle(db_session).state(
        book_id=accepted.book_id, user_id=reader.id
    )
    assert after.status == "ready"
    assert after.error_code == ""


async def test_一页书的状态一条查询取齐_取的是最近那一次任务(db_session):
    """书库一页二十本书，逐本去查就是二十一次往返。

    每本书只看**最近一次**任务：重解析留下好几条，卡片上说的是现在这一次。
    """
    from sqlalchemy import event

    from booksoul_api.db.session import get_engine

    reader = await _reader(db_session)
    newest: dict[int, int] = {}
    for n in range(3):
        book = Book(user_id=reader.id, title=f"第{n}本", status="parsing")
        db_session.add(book)
        await db_session.flush()
        old = ParseJob(book_id=book.id, stage="write", error_code="bad_archive")
        db_session.add(old)
        await db_session.flush()
        now = ParseJob(book_id=book.id, stage="convert", progress_done=n)
        db_session.add(now)
        await db_session.flush()
        newest[book.id] = now.id
    await db_session.commit()

    statements: list[str] = []

    def count(_conn, _cursor, statement, *_rest):
        statements.append(statement)

    engine = get_engine().sync_engine
    event.listen(engine, "before_cursor_execute", count)
    try:
        states = await BookLifecycle(db_session).states(
            user_id=reader.id, book_ids=list(newest)
        )
    finally:
        event.remove(engine, "before_cursor_execute", count)

    assert len(statements) == 1, statements
    assert {s.book_id: s.job_id for s in states.values()} == newest
    assert all(s.stage == "convert" and s.error_code == "" for s in states.values())


# ------------------------------------------------------------------ 进度推送


async def _watch(db_session, user: User, job_id: int) -> list[tuple[str, dict]]:
    stream = await BookLifecycle(db_session).watch(job_id=job_id, user_id=user.id)
    return [event async for event in stream]


async def test_进度推送阶段变了才推_结局只推一次_到寿命就收(
    db_session, fake_parser, monkeypatch
):
    """推送读的是库，不是进程内的状态；变了才推，结局推一次就收。

    一个卡住的任务不该让这条流一直轮询下去：到了寿命还没有结局就静默收尾，不宣布
    任何结局——前端有轮询兜底，接着问 `GET /api/jobs/{id}`。
    """
    fake_parser.fail = RuntimeError("坏了")
    reader = await _reader(db_session)
    failed = await _upload(reader, Path(__file__), "一本书.epub")
    await drain()

    events = await _watch(db_session, reader, failed.job_id)
    assert [name for name, _ in events] == ["stage", "error"]
    error = events[-1][1]
    assert error["errorCode"] == "parse_failed"
    assert error["remedy"]
    assert error["bookId"] == failed.book_id

    monkeypatch.setattr(get_settings(), "job_stream_max_seconds", 0.3)
    monkeypatch.setattr(get_settings(), "job_poll_seconds", 0.02)
    fake_parser.fail = None
    gate = fake_parser.hold()
    stuck = await _upload(reader, Path(__file__), "另一本.epub")
    try:
        events = await _watch(db_session, reader, stuck.job_id)
    finally:
        gate.set()
        await drain()

    assert events, "至少推过一次眼下的进度"
    assert {name for name, _ in events} == {"stage"}, "到寿命是静默收尾，不宣布结局"
    payloads = [payload for _, payload in events]
    assert all(a != b for a, b in pairwise(payloads)), "没变就不该再推"


# ------------------------------------------------------------------ HTTP


async def _register(client, email: str) -> dict[str, str]:
    r = await client.post(
        "/api/auth/register",
        json={"email": email, "password": "一句足够长的中文密码", "displayName": ""},
    )
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['accessToken']}"}


async def _post_book(client, auth: dict[str, str], name: str = "一本书.epub"):
    return await client.post(
        "/api/books",
        files={"file": (name, b"PK\x03\x04 not really", "application/epub+zip")},
        headers=auth,
    )


async def test_上传202_camelCase回执(client, fake_parser):
    auth = await _register(client, "reader@example.com")

    r = await _post_book(client, auth)
    await drain()

    assert r.status_code == 202, r.text
    assert set(r.json()) == {"bookId", "jobId", "affectedConversations"}
    assert r.json()["affectedConversations"] == 0


async def test_任务查询与事件流都认得它(client, fake_parser):
    auth = await _register(client, "reader@example.com")
    accepted = (await _post_book(client, auth)).json()
    await drain()

    job = await client.get(f"/api/jobs/{accepted['jobId']}", headers=auth)
    assert job.status_code == 200
    assert job.json()["bookId"] == accepted["bookId"]
    assert job.json()["finishedAt"] is not None
    assert job.json()["errorCode"] == ""

    events = await client.get(f"/api/jobs/{accepted['jobId']}/events", headers=auth)
    assert events.status_code == 200
    assert events.headers["content-type"].startswith("text/event-stream")
    assert events.headers["x-accel-buffering"] == "no"
    assert "event: stage" in events.text
    assert events.text.rstrip().split("\n\n")[-1].startswith("event: done")


async def test_别人的任务_查询与事件流都是404_都在开流之前(client, fake_parser):
    """越权与不存在同一个 404。事件流也要在响应头之前就说 404，而不是开一条空流。"""
    alice = await _register(client, "alice@example.com")
    bob = await _register(client, "bob@example.com")
    accepted = (await _post_book(client, alice)).json()
    await drain()

    job = await client.get(f"/api/jobs/{accepted['jobId']}", headers=bob)
    events = await client.get(f"/api/jobs/{accepted['jobId']}/events", headers=bob)

    assert job.status_code == 404
    assert events.status_code == 404
    assert events.headers["content-type"].startswith("application/json")


async def test_书的状态接口_解析中好了停下了都说得清_别人的书404(client, fake_parser):
    """书籍详情在解析中每一拍只问这一句：一条查询，不拼整份详情（大脑列表、审核记录、会话数）。"""
    auth = await _register(client, "reader@example.com")
    gate = fake_parser.hold()
    try:
        accepted = (await _post_book(client, auth)).json()
        during = await client.get(
            f"/api/books/{accepted['bookId']}/state", headers=auth
        )
    finally:
        gate.set()
        await drain()
    after = await client.get(f"/api/books/{accepted['bookId']}/state", headers=auth)

    fake_parser.fail = RuntimeError("坏了")
    broken = (await _post_book(client, auth, "另一本.epub")).json()
    await drain()
    stopped = await client.get(f"/api/books/{broken['bookId']}/state", headers=auth)
    bob = await _register(client, "bob@example.com")
    stranger = await client.get(f"/api/books/{accepted['bookId']}/state", headers=bob)

    assert during.status_code == 200
    assert during.json()["status"] == "parsing"
    assert during.json()["jobId"] == accepted["jobId"]
    assert after.json()["status"] == "ready"
    assert after.json()["errorCode"] == ""
    assert stopped.json()["status"] == "failed"
    assert stopped.json()["errorCode"] == "parse_failed"
    assert stopped.json()["message"] and stopped.json()["remedy"]
    assert stranger.status_code == 404


async def test_重新解析撞上正在解析_409(client, fake_parser):
    auth = await _register(client, "reader@example.com")
    gate = fake_parser.hold()
    try:
        accepted = (await _post_book(client, auth)).json()
        again = await client.post(
            f"/api/books/{accepted['bookId']}/reparse", headers=auth
        )
    finally:
        gate.set()
        await drain()

    assert again.status_code == 409
    assert again.json()["code"] == "book_parsing"


# ------------------------------------------------------------------ 重新解析


async def test_重新解析复用同一个book_id与同一个目录(db_session, fake_parser):
    """重解析不是「删掉重传」：book_id 与目录都要留着。

    会话、引文都挂在 book_id 上，换个 id 等于把用户过去的一切扔掉。章节索引是整批
    换掉而不是追加，否则重解析一次就翻一倍。
    """
    reader = await _reader(db_session)
    first = await _upload(reader, Path(__file__), "悉达多.epub")
    await drain()
    storage_path = (await _book(db_session, first.book_id)).storage_path

    gate = fake_parser.hold()
    again = await _reparse(reader, first.book_id)
    try:
        assert again.book_id == first.book_id
        assert again.affected_conversations == 0, "这本书还没人跟它聊过"
        assert await asyncio.to_thread(fake_parser.reached.wait, 5)
        during = await _book(db_session, first.book_id)
        assert during.status == "parsing", "重解析期间书库卡片要变回解析中"
    finally:
        gate.set()
        await drain()

    after = await _book(db_session, first.book_id)
    assert after.status == "ready"
    assert after.storage_path == storage_path
    chapters = await BookRepository(db_session).list_chapters(after.id)
    assert len(chapters) == 1, "章节索引被追加了而不是换掉"


async def _jobs(db_session) -> int:
    return await db_session.scalar(select(func.count()).select_from(ParseJob))


async def test_同时两次重新解析只有一个开跑(db_session, fake_parser):
    """两个几乎同时的「重新解析」只能有一个开跑。

    原先是先读状态、再改：两个请求都读到「没在解析」，都穿过检查，两个工作线程
    同时往同一个目录里写。现在是一句「不在解析中才改成解析中」的 UPDATE——第二个
    等第一个提交之后重新看条件，那时它已经是 parsing 了。
    """
    reader = await _reader(db_session)
    first = await _upload(reader, Path(__file__), "悉达多.epub")
    await drain()
    before = await _jobs(db_session)

    gate = fake_parser.hold()  # 开跑的那一个停在半路，不让它在第二个到来之前就跑完
    try:
        results = await asyncio.gather(
            _reparse(reader, first.book_id),
            _reparse(reader, first.book_id),
            return_exceptions=True,
        )
    finally:
        gate.set()
        await drain()

    receipts = [r for r in results if isinstance(r, UploadAccepted)]
    refused = [r for r in results if isinstance(r, BookNotReady)]
    assert len(receipts) == 1 and len(refused) == 1, results
    assert refused[0].code == "book_parsing"
    assert await _jobs(db_session) == before + 1, "只该多出一个解析任务"


async def test_原件不在时重新解析409且状态不动(db_session, fake_parser):
    """原件没了，重新解析无从谈起。说清楚只能再传一次——而且书的状态一点都不动：
    它还是那本读得了、问得了的书。"""
    reader = await _reader(db_session)
    first = await _upload(reader, Path(__file__), "悉达多.epub")
    await drain()
    book = await _book(db_session, first.book_id)
    source_path(book).unlink()
    before = await _jobs(db_session)

    with pytest.raises(BookNotReady) as refused:
        await _reparse(reader, first.book_id)

    assert refused.value.code == "source_missing"
    assert refused.value.status_code == 409
    assert "再传一次" in str(refused.value)
    assert (await _book(db_session, first.book_id)).status == "ready"
    assert await _jobs(db_session) == before


async def _read(user: User, book_id: int, idx: int = 1) -> str:
    """像阅读页那样另开一个请求读一节。"""
    async with get_session_factory()() as request:
        chapter = await ReaderService(request).chapter(
            book_id=book_id, idx=idx, user_id=user.id
        )
    return chapter.markdown


async def test_重新解析写完了还没换上去_读到的仍是完整的旧版(db_session, fake_parser):
    """在用的目录从不被原地改写。

    原先引擎直接往那个目录里写：新正文写好、库还没提交的这一刻，阅读器读到的是一本新旧
    混杂的书。造法：外来事务锁住这本书的一个会话，解析写完正文、换章节时（先清会话的锁定）
    只能排队——正好停在「新正文已经写好、还没提交」这一刻。
    """
    reader = await _reader(db_session)
    fake_parser.book = engine_book(
        "悉达多", {"婆罗门之子": "旧版的第一节。", "乔达摩": "旧版的第二节。"}
    )
    first = await _upload(reader, Path(__file__), "悉达多.epub")
    await drain()
    talk = Conversation(
        user_id=reader.id,
        book_id=first.book_id,
        persona="scholar",
        brain_name="claude",
        model="claude-opus-5",
        title="他为什么离开？",
    )
    db_session.add(talk)
    await db_session.commit()
    before = await _read(reader, first.book_id)

    fake_parser.book = engine_book(
        "悉达多", {"婆罗门之子": "新版的第一节。", "乔达摩": "新版的第二节。"}
    )
    async with get_session_factory()() as holder:
        await holder.execute(
            text("SELECT id FROM conversations WHERE id = :id FOR UPDATE"),
            {"id": talk.id},
        )
        await _reparse(reader, first.book_id)
        try:
            await _until_someone_waits_on_a_lock()  # 写完了，卡在换章节上
            during = await _read(reader, first.book_id)
        finally:
            await holder.rollback()
            await drain()

    assert during == before, "写好了还没换上去，读到的得是完整的旧版"
    assert "新版的第一节" in await _read(reader, first.book_id)


async def test_重新解析成功后换新_不留旧章节文件_书名变了旧目录收掉(
    db_session, fake_parser
):
    """章节变少，旧的那几节文件不该留着（报告页的产出清单会数错）；书名变了，旧名字的
    目录不再有任何一行指着它，也不该留着。"""
    reader = await _reader(db_session)
    fake_parser.book = engine_book(
        "悉达多", {"婆罗门之子": "第一版的第一节。", "乔达摩": "第一版的第二节。"}
    )
    first = await _upload(reader, Path(__file__), "悉达多.epub")
    await drain()
    old_root = Path(get_settings().library_root) / f"{reader.id}/{first.book_id}-悉达多"

    fake_parser.book = engine_book("悉达多", {"婆罗门之子": "第二版只剩一节。"})
    await _reparse(reader, first.book_id)
    await drain()
    assert [p.name for p in (old_root / "chapters").iterdir()] == [
        "001-婆罗门之子.md"
    ], "章节变少，旧章节文件还留着"

    fake_parser.book = engine_book(
        "悉达多（新译本）", {"婆罗门之子": "新译本的第一节。"}
    )
    await _reparse(reader, first.book_id)
    await drain()

    async with get_session_factory()() as request:
        book = await BookLifecycle(request).require(
            book_id=first.book_id, user_id=reader.id, use=Use.READ
        )
    assert book.root.name == f"{first.book_id}-悉达多（新译本）"
    assert not old_root.exists(), "书名变了，旧名字的目录还留着"
    assert "新译本的第一节" in await _read(reader, first.book_id)
    staging = Path(get_settings().library_root) / ".staging"
    assert not staging.exists() or not any(staging.iterdir()), "暂存目录要收干净"


async def test_重新解析失败_旧版照读_提问409(db_session, fake_parser):
    """解析出来、写好了盘，存进书库那一步却出了事：书记成「停下了」，旧版原样可读。

    原先新正文已经写进在用的那个目录，库又撤回了——章节行是旧的、文件是新的，对不上。
    """
    reader = await _reader(db_session)
    fake_parser.book = engine_book("悉达多", {"婆罗门之子": "旧版的第一节。"})
    first = await _upload(reader, Path(__file__), "悉达多.epub")
    await drain()
    before = await _read(reader, first.book_id)

    fake_parser.book = engine_book("悉达多", {"婆罗门之子": "新版的第一节。"})
    fake_parser.book.chapters[0].level = 40_000  # 写库那一半才会撞上的坏值
    await _reparse(reader, first.book_id)
    await drain()

    state = await BookLifecycle(db_session).state(
        book_id=first.book_id, user_id=reader.id
    )
    assert state.status == "failed"
    assert await _read(reader, first.book_id) == before, "旧版要原样读得到"
    with pytest.raises(BookNotReady) as refused:
        await BookLifecycle(db_session).require(
            book_id=first.book_id, user_id=reader.id, use=Use.ASK
        )
    assert refused.value.code == "book_failed"


async def test_别人的书重新解析不了(db_session):
    """越权返回 404 而不是 403——403 等于承认这个 id 确实存在。"""
    owner = await _reader(db_session, "owner@example.com")
    stranger = await _reader(db_session, "stranger@example.com")
    book = Book(user_id=owner.id, title="别人的书", status="failed")
    db_session.add(book)
    await db_session.commit()

    with pytest.raises(NotFoundError):
        await BookLifecycle(db_session).reparse(book_id=book.id, user_id=stranger.id)


# ------------------------------------------------------------------ 启动清扫


async def test_服务重启时被中断的解析不会永远卡在解析中(db_session):
    """本地 `uvicorn --reload` 存个文件就重启，这条路每天都会踩到。

    卡在「解析中」比报错更让人没辙——它连个出路都不给，用户只能盯着一个
    永远不动的进度条。所以进程一起来就把上一条命留下的任务收掉，并给出重试的路。
    """
    reader = await _reader(db_session)
    book = Book(user_id=reader.id, title="半途而废", status="parsing")
    db_session.add(book)
    await db_session.flush()
    job = ParseJob(
        book_id=book.id, stage="convert", progress_done=41, progress_total=64
    )
    db_session.add(job)
    await db_session.commit()

    swept = await sweep_orphans()

    await db_session.refresh(book)
    await db_session.refresh(job)
    assert swept == 1
    assert book.status == "failed"
    assert job.error_code == "orphaned"
    assert job.finished_at is not None
    assert "重新解析" in job.remedy, "停下来必须给出用户自己能执行的下一步"


async def test_重启清扫_孤儿收口_暂存清掉_已提交未发布的补完(db_session):
    """进程在半路没了，盘上会留下三种东西：

    * 解析到一半的暂存目录——那次解析已经没人在跑，删掉；
    * 已经提交成功、还没来得及换上去的暂存目录——库里说新版在哪，就把它换到哪（roll-forward）；
    * 上次没删完的退役目录——删掉。
    """
    from datetime import UTC, datetime

    reader = await _reader(db_session)
    library = Path(get_settings().library_root)
    staging = library / ".staging"
    retired = library / ".retired"

    orphan = Book(user_id=reader.id, title="半途而废", status="parsing")
    db_session.add(orphan)
    await db_session.flush()
    orphan_job = ParseJob(book_id=orphan.id, stage="convert")
    db_session.add(orphan_job)

    done = Book(user_id=reader.id, title="悉达多", status="parsing")
    db_session.add(done)
    await db_session.flush()
    # 成功那个事务写下的：新版的位置与 ready（目录名含 id，得先拿到 id）
    done.storage_path = f"{reader.id}/{done.id}-悉达多"
    done.status = "ready"
    done_job = ParseJob(
        book_id=done.id, stage="write", finished_at=datetime.now(UTC), error_code=""
    )
    db_session.add(done_job)
    await db_session.commit()

    (staging / f"job-{orphan_job.id}" / "chapters").mkdir(parents=True)
    (staging / f"job-{orphan_job.id}" / "chapters" / "001-半截.md").write_text("半截")
    (staging / f"job-{done_job.id}").mkdir(parents=True)
    (staging / f"job-{done_job.id}" / "book.md").write_text("新版", encoding="utf-8")
    (library / done.storage_path).mkdir(parents=True)
    (library / done.storage_path / "book.md").write_text("旧版", encoding="utf-8")
    (retired / "job-999").mkdir(parents=True)

    swept = await sweep_orphans()

    assert swept == 1
    await db_session.refresh(orphan)
    assert orphan.status == "failed"
    assert not (staging / f"job-{orphan_job.id}").exists(), "没人在跑的暂存要删掉"
    published = library / done.storage_path / "book.md"
    assert published.read_text(encoding="utf-8") == "新版", "已提交的那一版要补完换上"
    assert not (staging / f"job-{done_job.id}").exists()
    assert not retired.exists() or not any(retired.iterdir()), "退役目录要清掉"


async def test_清扫失败不拦着服务起来(monkeypatch):
    """清扫是启动时顺手做的一件事，不是服务能不能起来的前提。

    让它把整站拦下来，是把一个小问题（几条没收尾的任务）放大成不可用。
    接住它的是 lifespan 那层 try。
    """
    from booksoul_api import main as main_mod

    async def 炸():
        raise RuntimeError("清扫的时候数据库还没起来")

    monkeypatch.setattr(main_mod, "sweep_orphans", 炸)

    async with main_mod.lifespan(main_mod.app):
        pass  # 走到这里就是这条断言：它起来了


# ------------------------------------------------------------------ 门表

# (状态, 有没有已发布的一版) × 用途 → None 放行 / 404 / 409 的 code。
# 「有已发布的一版」而状态是 parsing / failed：重解析中、重解析失败——读的是旧版。
_GATE = [
    ("parsing", False, Use.READ, 404),
    ("parsing", False, Use.ASK, "book_parsing"),
    ("parsing", False, Use.REPARSE, "book_parsing"),
    ("parsing", True, Use.READ, None),
    ("parsing", True, Use.ASK, "book_parsing"),
    ("parsing", True, Use.REPARSE, "book_parsing"),
    ("ready", True, Use.READ, None),
    ("ready", True, Use.ASK, None),
    ("ready", True, Use.REPARSE, None),
    ("failed", False, Use.READ, 404),
    ("failed", False, Use.ASK, "book_failed"),
    ("failed", False, Use.REPARSE, None),
    ("failed", True, Use.READ, None),
    ("failed", True, Use.ASK, "book_failed"),
    ("failed", True, Use.REPARSE, None),
]


async def _book_in(db_session, status: str, *, published: bool) -> tuple[User, Book]:
    user = User(email="reader@example.com", password_hash="不参与本测试")
    db_session.add(user)
    await db_session.flush()
    book = Book(
        user_id=user.id,
        title="悉达多",
        status=status,
        storage_path=f"{user.id}/1-悉达多" if published else None,
    )
    db_session.add(book)
    await db_session.commit()
    return user, book


@pytest.mark.parametrize(
    ("status", "published", "use", "expected"),
    _GATE,
    ids=[f"{s}-{'有旧版' if p else '没有'}-{u}" for s, p, u, _ in _GATE],
)
async def test_能不能读能不能问_一张表说了算(
    db_session, status, published, use, expected
):
    """放行的交回拼好了路径的书；拦下的，读正文是 404（这一面不存在），问与重解析是 409 带 code。

    书库卡片与书籍详情上的按钮读 `state().allows()`：它与 `require()` 查的是同一张表，
    说「能」的时候门一定开，说「不能」的时候门一定关。
    """
    user, book = await _book_in(db_session, status, published=published)
    gate = BookLifecycle(db_session)

    state = await gate.state(book_id=book.id, user_id=user.id)
    assert state.status == status
    assert state.allows(use) is (expected is None)

    if expected is None:
        usable = await gate.require(book_id=book.id, user_id=user.id, use=use)
        assert usable.id == book.id
        if published:
            assert usable.root == Path(get_settings().library_root) / book.storage_path
    elif expected == 404:
        with pytest.raises(NotFoundError) as refused:
            await gate.require(book_id=book.id, user_id=user.id, use=use)
        assert not isinstance(refused.value, BookNotReady)
        assert "悉达多" in str(refused.value)
    else:
        with pytest.raises(BookNotReady) as refused:
            await gate.require(book_id=book.id, user_id=user.id, use=use)
        assert refused.value.code == expected
        assert refused.value.status_code == 409
        assert "悉达多" in str(refused.value)


@pytest.mark.parametrize("use", list(Use))
async def test_别人的书过不了门_一律404(db_session, use):
    """越权与不存在同一个 404：403 等于承认这个 id 确实存在。"""
    _owner, book = await _book_in(db_session, "ready", published=True)
    stranger = User(email="stranger@example.com", password_hash="不参与本测试")
    db_session.add(stranger)
    await db_session.commit()

    with pytest.raises(NotFoundError) as refused:
        await BookLifecycle(db_session).require(
            book_id=book.id, user_id=stranger.id, use=use
        )
    assert "悉达多" not in str(refused.value), "别人的书名也不该说出来"


# ------------------------------------------------------------------ 书在磁盘上的位置


def test_书库根与上传根只在一处拼():
    """书在磁盘上的位置只在 `book_lifecycle` 里拼。

    原先八处各拼一遍，其中一处把容器里的绝对路径端到了阅读页上。再有地方要用，就经
    `UsableBook.root` / `file()` / `display()`、`book_dir()`、`source_path()`。
    """
    src = Path(__file__).resolve().parent.parent / "src" / "booksoul_api"
    allowed = {src / "services" / "book_lifecycle.py", src / "core" / "config.py"}
    offenders = [
        path.relative_to(src).as_posix()
        for path in sorted(src.rglob("*.py"))
        if path not in allowed
        and re.search(r"\b(library_root|upload_root)\b", path.read_text("utf-8"))
    ]
    assert offenders == []


# ------------------------------------------------------------------ 换章节的次序


async def test_换章节之前已经握着会话行(db_session):
    """重解析换章节的那一刻，会话行必须已经在它手里。

    造法：第三条连接对一行旧章节加 `FOR KEY SHARE`，`swap()` 删旧章节时只能排队；
    趁它卡在那里，第四条连接对会话行 `FOR UPDATE NOWAIT`——必须拿不到。
    把 `swap()` 里清锁定挪到换章节之后，它卡住时还没碰会话行，这一条当场变红。
    原先的锁序测试在测试里自己抄了一遍这三步，生产代码里对调，它照样是绿的。
    """
    book, chapter_id, conversation_id = await _book_someone_is_talking_about(db_session)
    root = Path(get_settings().library_root) / book.storage_path
    factory = get_session_factory()

    async with factory() as holder, factory() as parse:
        await holder.execute(
            text("SELECT id FROM chapters WHERE id = :id FOR KEY SHARE"),
            {"id": chapter_id},
        )
        swapping = asyncio.create_task(
            resettle.swap(
                parse,
                book_id=book.id,
                records=[_chapter(1, "婆罗门之子"), _chapter(2, "乔达摩")],
                root=root,
            )
        )
        try:
            await _until_someone_waits_on_a_lock()  # 卡在删旧章节上
            refused = await _lock_conversation_now(conversation_id)
        finally:
            # 不管上面怎样都放它走完：它还压在 `parse` 那条连接上，
            # 连接没空出来就退出，报出来的会是连接错误而不是上面那条断言
            await holder.rollback()
            swapped = await asyncio.wait_for(swapping, timeout=10)
            await parse.rollback()

    assert _lock_refused(refused), (
        f"swap() 卡在删旧章节上时，会话行还不在它手里：{refused!r}"
    )
    assert [c.idx for c in swapped.chapters] == [1, 2]
