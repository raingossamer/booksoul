"""解析账本（②屏）：一本可以逐条核对的账。

两条规矩，走错了都不报错：

* 没数出来的项整行不出现，而不是填 0——填 0 会让人以为「一处都没动」，那是假话。
* 只有真有引文搬家时才有「引文重定位」那一行。一本正文一字未改的书会给出 100% 找回，
  而那个数字什么都没证明——所以「位置变了几条」必须摆在那句话里。

库里 `parse_jobs.cleanup_stats` 存的是中文键的字典（前端导入页也读其中几个）。这里的
期望是类型化之前那份渲染的原样输出：旧行照读，逐行相同。
"""

from __future__ import annotations

from booksoul_api.models.book import Book
from booksoul_api.services import relocate as reloc
from booksoul_api.services.ledger import Ledger


def _book(**fields) -> Book:
    return Book(user_id=1, title="悉达多", **fields)


def _render(book: Book, stats: dict) -> list[tuple[str, str]]:
    return [(row.label, row.detail) for row in Ledger.from_json(stats).rows(book=book)]


# 一本 EPUB 的全套：引擎自记的几笔（目录来源、spine 文件数、剔除的目录页）也在里面
_EPUB = {
    "格式": "epub",
    "章节": 16,
    "字数": 48155,
    "图片": 3,
    "脚注": 12,
    "元信息命中": 5,
    "元信息字段数": 6,
    "去重的重复标题": 2,
    "目录来源": "nav",
    "spine 文件数": 18,
    "剔除的目录页": 1,
}


def test_一本EPUB的账_行序与说法照旧():
    assert _render(_book(source_format="epub", chapter_count=16), _EPUB) == [
        ("格式", "EPUB —— 按文件魔数判定，不看扩展名"),
        ("目录来源", "nav · spine 18 个文件 · 切出 16 节"),
        ("元信息", "书名 · 作者 · 出版社 · 出版日 · ISBN · 语言 命中 5/6"),
        (
            "目录页",
            "从正文剔除 1 个文件（它混在 spine 里，且与我们自己写的 INDEX.md 重复）",
        ),
        ("重复标题", "2 节的正文开头与标题重复，已去掉那一行"),
        ("脚注", "12 条接回标准 Markdown 脚注"),
        ("图片", "导出 3 张到 images/"),
    ]


def test_早先的旧行照读_没数出来的项不出行():
    """早先的解析只记了格式、章节、字数。没记的那几项不出行，不填 0。"""
    book = _book(source_format="pdf", chapter_count=5, contributor="张三")
    old = {"格式": "pdf", "章节": 5, "字数": 1000}

    assert _render(book, old) == [
        ("格式", "PDF —— 按文件魔数判定，不看扩展名"),
        ("贡献者", "张三 —— 来源没标角色，记为「贡献者」而不是替你猜成译者"),
    ]


def test_空账一行都没有():
    assert _render(_book(source_format="", chapter_count=0), {}) == []


def test_解析报告里有引文重定位那一行():
    """②屏那本账要说得出这次重解析对旧引文做了什么。

    **「位置变了几条」必须出现在那句话里。** 一本正文一字未改的书会给出
    100% 找回，而那个数字什么都没证明——它连一次真正的重定位都没做过。
    """
    book = _book(source_format="epub", chapter_count=16)

    all_moved = {"引文总数": 39, "引文找回": 39, "引文位置变了": 39, "引文丢失": 0}
    line = dict(_render(book, all_moved))["引文重定位"]
    assert "39 条旧引文" in line
    assert "39 条位置变了" in line
    assert "没有猜" not in line, "一条都没丢就不必提降级"

    mostly_lost = {"引文总数": 39, "引文找回": 9, "引文位置变了": 0, "引文丢失": 30}
    line = dict(_render(book, mostly_lost))["引文重定位"]
    assert line == (
        "39 条旧引文，找回 9 条（其中 0 条位置变了）；"
        "30 条在新正文里找不到，已降级为「原位置已变更」，没有猜"
    )


def test_没有引文要搬家时账本里不出现那一行():
    """首次解析一条引文都没有。记一行「0 条」会让账本多出一笔没发生过的事。"""
    book = _book(source_format="epub", chapter_count=16)
    rows = _render(book, {"引文总数": 0, "引文找回": 0})
    assert all(label != "引文重定位" for label, _ in rows)


def test_库里的存储格式不变():
    """读进来再写回去，还是那个字典：前端导入页读的键、库里的旧行都不受影响。"""
    assert Ledger.from_json(_EPUB).to_json() == _EPUB


def test_写进库里再读回来是同一本账():
    ledger = Ledger(
        source_format="epub",
        chapters=16,
        words=48155,
        images=3,
        footnotes=12,
        meta_hit=5,
        meta_fields=6,
        deduped_headings=2,
        engine={"目录来源": "nav", "spine 文件数": 18},
        relocation=reloc.Report(total=39, shifted=0, unchanged=9, lost=30),
    )
    assert Ledger.from_json(ledger.to_json()) == ledger


def test_没有引文要搬家就不记那几笔():
    """这条规矩只写在账本里一处：首次解析一条引文都没有，账上就没有这一笔。"""
    first = Ledger(source_format="epub", chapters=2).moved(
        reloc.Report(total=0, shifted=0, unchanged=0, lost=0)
    )
    assert first.relocation is None
    assert "引文总数" not in first.to_json()

    again = first.moved(reloc.Report(total=2, shifted=1, unchanged=1, lost=0))
    assert again.to_json()["引文总数"] == 2
    assert again.to_json()["引文找回"] == 2
