"""写盘与读回的往返。

这两条路是解析和问答之间那块磁盘的两端：`writer.write_book` 把 Book 落成目录，
`library.load*` 再读回来。Web 层要在它们上面加两件事——目录名由调用方指定
（多用户书库不能靠书名当目录名），以及按需只读指定章节。
"""

from __future__ import annotations

import os
import shutil

import pytest

from booksoul import library, writer
from booksoul.model import Book, BookMeta, Chapter


def _book(title: str, chapters: int = 3) -> Book:
    return Book(
        meta=BookMeta(title=title, author="某人", source_format="epub"),
        chapters=[
            Chapter(title=f"第{i}章", content=f"这是第{i}章的正文。" * 5, order=i - 1)
            for i in range(1, chapters + 1)
        ],
    )


def test_给了dir_name就用它当目录名(tmp_path):
    """多用户书库里目录名是 <user_id>/<book_id>-<slug>，由调用方算好。

    引擎自己按书名取名在单人本地是对的，多用户下不成立：两个人各传一本《悉达多》
    会写进同一个目录，后一本静默覆盖前一本。
    """
    summary = writer.write_book(_book("悉达多"), str(tmp_path), dir_name="7-悉达多")

    assert os.path.basename(summary["目录"]) == "7-悉达多"
    assert os.path.isfile(os.path.join(summary["目录"], "book.md"))


def test_不给dir_name时仍按书名取目录(tmp_path):
    """CLI 那条路不受影响：单人本地按书名取目录是对的，也是已有行为。"""
    summary = writer.write_book(_book("悉达多"), str(tmp_path))

    assert os.path.basename(summary["目录"]) == "悉达多"


def test_两本同名书在不同dir_name下互不覆盖(tmp_path):
    """多用户书库的核心保证。

    dir_name 带子目录（真实形状是 <user_id>/<book_id>-<slug>），中间目录要能自动建出来。
    """
    a = writer.write_book(
        _book("悉达多", chapters=2), str(tmp_path), dir_name="7/1-悉达多"
    )
    b = writer.write_book(
        _book("悉达多", chapters=5), str(tmp_path), dir_name="9/2-悉达多"
    )

    assert a["目录"] != b["目录"]
    assert a["章节数"] == 2 and b["章节数"] == 5
    assert len(os.listdir(os.path.join(a["目录"], "chapters"))) == 2
    assert len(os.listdir(os.path.join(b["目录"], "chapters"))) == 5


def test_按路径直取与按书名找回来的是同一本书(tmp_path):
    """Web 层的书库按 user_id 分目录，书名不再唯一，find() 那条模糊匹配走不通。

    调用方手里本来就有确切路径（books.storage_path），直接给它就行——
    但直取的结果必须与原来那条路一字不差，否则两条路会慢慢长成两种行为。
    """
    written = writer.write_book(_book("悉达多"), str(tmp_path), dir_name="7-悉达多")

    direct = library.load_from_path(written["目录"])
    via_find = library.load("悉达多", str(tmp_path))

    assert direct.meta.title == via_find.meta.title == "悉达多"
    assert direct.meta.author == via_find.meta.author
    assert [c.title for c in direct.chapters] == [c.title for c in via_find.chapters]
    assert [c.content for c in direct.chapters] == [
        c.content for c in via_find.chapters
    ]
    assert direct.words == via_find.words


def test_only_idx只读指定章节但全书目录仍然完整(tmp_path):
    """按需读章节要省的是读盘，不是省掉「模型知道自己拿到的是片段」这件事。

    没选中的章节留一个空壳，as_context 才能在目录上标出「本次未载入全文」。
    整个丢掉的话，模型会以为手里这两节就是全书，然后对着残缺目录凭章节名臆测内容。
    """
    written = writer.write_book(
        _book("悉达多", chapters=5), str(tmp_path), dir_name="7-悉达多"
    )

    book = library.load_from_path(written["目录"], only_idx={2, 4})

    assert len(book.chapters) == 5
    assert [c.title for c in book.chapters if c.content] == ["第2章", "第4章"]

    context = library.as_context(book, [c for c in book.chapters if c.content])
    assert context.count("（本次未载入全文）") == 3
    assert "不要凭目录名臆测内容" in context


def test_只读几节也要照实说全书多少字(tmp_path):
    """`book.words` 不能随着「这次载入了哪几节」变。

    人格提示词里有一句「全书多少字」。按 only_idx 载入时没选中的章节只剩空壳，
    数出来是 0——那句话就会从「全书 48,155 字」缩水成两万，而且两个后果都不报错：
    一是告诉模型一本它其实没拿全的书只有这么长，二是提示词一变，前缀缓存整个
    作废——而会话锁定的全部意义正是保住那个缓存。
    """
    written = writer.write_book(
        _book("悉达多", chapters=5), str(tmp_path), dir_name="7-悉达多"
    )

    whole = library.load_from_path(written["目录"])
    part = library.load_from_path(written["目录"], only_idx={2, 4})

    assert part.words == whole.words > 0


def test_章节文件缺了一个也能把书读回来(tmp_path):
    """单个章节坏掉不该毁掉整本书——记一笔警告继续走，别让异常冒到顶上。"""
    written = writer.write_book(
        _book("悉达多", chapters=4), str(tmp_path), dir_name="7-悉达多"
    )
    chapters_dir = os.path.join(written["目录"], "chapters")
    os.remove(os.path.join(chapters_dir, sorted(os.listdir(chapters_dir))[1]))

    book = library.load_from_path(written["目录"])

    assert len(book.chapters) == 3
    assert any("章节文件缺失" in w for w in book.warnings)


def test_一节都读不到时直接报错而不是返回一本空书(tmp_path):
    """Grill C10 那条连锁的起点：载入 0 节 → 模型手里没书 → 悄悄凭印象瞎编。

    宁可在这里停下，也不要把一本没有正文的书送进模型。
    """
    written = writer.write_book(_book("悉达多"), str(tmp_path), dir_name="7-悉达多")
    shutil.rmtree(os.path.join(written["目录"], "chapters"))

    with pytest.raises(ValueError, match="没有读到任何章节正文"):
        library.load_from_path(written["目录"])


def test_only_idx指向不存在的章节同样报错(tmp_path):
    """重解析后章节序号变了，旧的选章锁定可能一节都命中不了。同一条出路。"""
    written = writer.write_book(
        _book("悉达多", chapters=3), str(tmp_path), dir_name="7-悉达多"
    )

    with pytest.raises(ValueError, match="没有读到任何章节正文"):
        library.load_from_path(written["目录"], only_idx={99})
