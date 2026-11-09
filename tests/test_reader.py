"""阅读器：把磁盘上那个 md 文件原样端到屏幕上（界面稿⑤屏的阅读部分）。

这一屏的全部承诺是一句话：**你看到的就是磁盘上那个文件**。
所以测试盯的是两件事——给出去的是不是原文，以及文件不在时会不会假装它在。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import select, update

from booksoul_api.core.config import get_settings
from booksoul_api.core.errors import ApiError, NotFoundError
from booksoul_api.models.book import Book
from booksoul_api.models.chapter import Chapter
from booksoul_api.models.user import User
from booksoul_api.services.reader_service import ReaderService


async def _user(session, email: str = "reader@example.com") -> User:
    user = User(email=email, password_hash="不参与本测试")
    session.add(user)
    await session.flush()
    return user


def _on_disk(book: Book, file_path: str) -> Path:
    return Path(get_settings().library_root) / book.storage_path / file_path


async def test_读一章拿到的就是磁盘上那个文件的原文(db_session, imported_book):
    """不做「整理」。整理过一遍，屏幕上的和用户拿走的就不是同一个东西了。"""
    reader, book = imported_book

    chapter = await ReaderService(db_session).chapter(
        book_id=book.id, idx=3, user_id=reader.id
    )

    raw = _on_disk(book, chapter.file_path).read_text(encoding="utf-8")
    assert chapter.markdown == raw
    assert chapter.markdown.startswith("# ")
    # 脚注是接成标准 Markdown 脚注的，前端才渲得出来
    assert "[^1]:" in chapter.markdown


async def test_位置说清这一章在全书的哪里(db_session, imported_book):
    reader, book = imported_book

    service = ReaderService(db_session)
    third = await service.chapter(book_id=book.id, idx=3, user_id=reader.id)

    assert third.idx == 3
    assert third.chapter_count == 16
    assert third.prev_idx == 2
    assert third.next_idx == 4
    # 「前面多少字」是前两节的字数和，不是猜的
    first = await service.chapter(book_id=book.id, idx=1, user_id=reader.id)
    second = await service.chapter(book_id=book.id, idx=2, user_id=reader.id)
    assert third.preceding_words == first.word_count + second.word_count

    assert first.preceding_words == 0
    assert first.prev_idx is None
    last = await service.chapter(book_id=book.id, idx=16, user_id=reader.id)
    assert last.next_idx is None


async def test_源文件路径给的是能照着找到的那一条(db_session, imported_book):
    """⑤屏底下那行路径不是装饰：用户要照着它把文件拿走或手工改。

    从书库根的**目录名**起算，不给服务器上的绝对路径：compose 下那是容器里的
    `/data/library/…`，在宿主机上照着它什么也找不到。
    """
    reader, book = imported_book

    chapter = await ReaderService(db_session).chapter(
        book_id=book.id, idx=3, user_id=reader.id
    )

    library = Path(get_settings().library_root).resolve()
    assert chapter.source_path == (
        f"{library.name}/{book.storage_path}/{chapter.file_path}"
    )
    assert (library.parent / chapter.source_path).is_file()
    # 一律正斜杠：这行字是给人复制的，不是拿去 open() 的
    assert "\\" not in chapter.source_path


async def test_文件不在磁盘上时明确报错_不给一段空正文(db_session, imported_book):
    """索引说有、磁盘说没有——这时候唯一不能做的事就是返回空字符串。

    空正文会被读成「这一章本来就没内容」，那是假话，而且它会一路假下去：
    用户以为解析漏了这一章，回头去重新解析，而问题其实出在文件被谁删了。
    """
    reader, book = imported_book

    service = ReaderService(db_session)
    chapter = await service.chapter(book_id=book.id, idx=3, user_id=reader.id)
    _on_disk(book, chapter.file_path).unlink()

    with pytest.raises(ApiError) as caught:
        await service.chapter(book_id=book.id, idx=3, user_id=reader.id)

    message = str(caught.value)
    assert chapter.file_path in message  # 说清是哪个文件
    assert "重新解析" in message  # 以及下一步能做什么


async def test_章节文件路径逃出书目录就拒绝(db_session, imported_book):
    """`file_path` 来自引擎，正常情况下老实。但它是一条**存在数据库里的路径**，

    而数据库里的东西终究是可以被改的。拿它去拼文件路径而不设防，等于把
    「读任意文件」这件事的开关交给了数据库的写入方。
    """
    reader, book = imported_book

    await db_session.execute(
        update(Chapter)
        .where(Chapter.book_id == book.id, Chapter.idx == 3)
        .values(file_path="../../../../../etc/passwd")
    )
    await db_session.flush()
    assert (
        await db_session.scalar(
            select(Chapter.file_path).where(
                Chapter.book_id == book.id, Chapter.idx == 3
            )
        )
        == "../../../../../etc/passwd"
    )

    with pytest.raises(ApiError) as caught:
        await ReaderService(db_session).chapter(
            book_id=book.id, idx=3, user_id=reader.id
        )
    # 是「越界了」而不是「文件恰好不存在」——后者说明防线根本没生效
    assert "目录外面" in str(caught.value)
    # 也不要把那条被改过的路径原样抄回去
    assert "passwd" not in str(caught.value)


async def test_别人的书读不到_而且不告诉你它存在(db_session, imported_book):
    owner, book = imported_book
    stranger = await _user(db_session, "stranger@example.com")

    with pytest.raises(NotFoundError):
        await ReaderService(db_session).chapter(
            book_id=book.id, idx=3, user_id=stranger.id
        )


async def test_没有这一节时说清这本书一共有几节(db_session, imported_book):
    reader, book = imported_book

    with pytest.raises(NotFoundError) as caught:
        await ReaderService(db_session).chapter(
            book_id=book.id, idx=99, user_id=reader.id
        )
    assert "16" in str(caught.value)


async def test_正文对中文排版是安全的(db_session, imported_book):
    """CLAUDE.md 记着两个反复踩的坑，它们都要到渲染这一步才现形：

    行首 4 个空格会被 Markdown 当成代码块；全角空格缩进会污染正文。
    引擎那边已经处理过，这里守的是「送到渲染器手上时它还是干净的」。
    """
    reader, book = imported_book

    service = ReaderService(db_session)
    fence = False
    for idx in range(1, 17):
        chapter = await service.chapter(book_id=book.id, idx=idx, user_id=reader.id)
        for line in chapter.markdown.splitlines():
            if line.lstrip().startswith("```"):
                fence = not fence
            if fence:
                continue
            assert not re.match(r"^ {4,}\S", line), f"{chapter.file_path}: {line!r}"
            assert not line.startswith("\u3000"), f"{chapter.file_path}: {line!r}"


# ------------------------------------------------------------------ 书里的图片
#
# 章节里写的是 `![](images/xxx.jpeg)`，文件就在书目录的 images/ 下。浏览器照着
# 相对路径去要，拿回来的是前端的 index.html —— 一个破图标。这一条路把它端出来。

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


async def _book_with_image(session, user: User, name: str = "cover.png") -> Book:
    storage = f"{user.id}/image-fixture"
    images = Path(get_settings().library_root) / storage / "images"
    images.mkdir(parents=True, exist_ok=True)
    (images / name).write_bytes(_PNG)
    (images.parent / "meta.json").write_text("{}", encoding="utf-8")
    book = Book(
        user_id=user.id,
        title="悉达多",
        source_file="悉达多.epub",
        source_format="epub",
        status="ready",
        storage_path=storage,
        chapter_count=1,
        word_count=10,
    )
    session.add(book)
    await session.commit()
    return book


async def test_章节里引的图按名字取得到_类型按扩展名(db_session):
    reader = await _user(db_session)
    book = await _book_with_image(db_session, reader, "Cover.JPG")

    path, media_type = await ReaderService(db_session).image(
        book_id=book.id, name="Cover.JPG", user_id=reader.id
    )

    assert path.read_bytes() == _PNG
    assert media_type == "image/jpeg"


async def test_别人的书里的图取不到(db_session):
    owner = await _user(db_session, "owner@example.com")
    book = await _book_with_image(db_session, owner)
    stranger = await _user(db_session, "stranger@example.com")

    with pytest.raises(NotFoundError):
        await ReaderService(db_session).image(
            book_id=book.id, name="cover.png", user_id=stranger.id
        )


@pytest.mark.parametrize(
    "name",
    [
        "../meta.json",  # 逃出 images/
        "..",
        "sub/cover.png",  # 只认 images/ 下面那一层
        "meta.json",  # 不是图
        # SVG 里能写脚本。前端把图取成 blob: 地址，而 blob: 继承本站的源 ——
        # 右键「在新标签页打开」，脚本就在本站跑起来，localStorage 里的 key 就在隔壁
        "cover.svg",
        "missing.png",  # 名字合法，文件不在
        # 空字符：扩展名过得了白名单，resolve() 却会抛 ValueError —— 是 404 不是 500
        "cover\x00.png",
    ],
)
async def test_只端出images目录里的位图_别的一律当作没有(db_session, name):
    reader = await _user(db_session)
    book = await _book_with_image(db_session, reader)
    images = Path(get_settings().library_root) / book.storage_path / "images"
    (images / "cover.svg").write_text("<svg><script>alert(1)</script></svg>")

    with pytest.raises(NotFoundError):
        await ReaderService(db_session).image(
            book_id=book.id, name=name, user_id=reader.id
        )


def test_路径里有空字符_当作不在书目录里而不是抛错():
    """图片名来自请求路径，`a%00.png` 就能送进来。

    Linux 上 `resolve()` 遇到空字符抛 `ValueError`（Windows 不抛），于是生产环境
    是一个 500。这条路径在操作系统上根本表示不出来，谈不上「在书目录里」。
    """
    from booksoul_api.core.paths import under

    assert under(get_settings().library_root, "images", "a\x00.png") is None


async def test_图片接口带上类型与nosniff_没登录拿不到(client, db_session):
    from test_brains_api import _auth, _token

    token = await _token(client)
    reader = await db_session.scalar(
        select(User).where(User.email == "reader@example.com")
    )
    book = await _book_with_image(db_session, reader)

    r = await client.get(f"/api/books/{book.id}/images/cover.png", headers=_auth(token))
    assert r.status_code == 200, r.text
    assert r.content == _PNG
    assert r.headers["content-type"] == "image/png"
    # 不许浏览器按内容猜类型：一张「图」里要是其实是 HTML，猜出来就是一段能跑的页面
    assert r.headers["x-content-type-options"] == "nosniff"

    anonymous = await client.get(f"/api/books/{book.id}/images/cover.png")
    assert anonymous.status_code == 401
