"""选章：整本 / BM25 / 复用锁定 / 逃生门（深模块 ChapterSelector）。

这个模块不碰数据库也不碰模型 API，所以这一整个文件都是纯逻辑测试，没有夹具、
不连库、跑得飞快——深模块的本意就是把最难想清楚的那部分放进一个能这样测的地方。

盯的是四条分叉，每一条走错都不会报错、只会悄悄改变模型手里有什么：

* 整本装得下时**不许跑 BM25** —— 一本几百万字的书光切词就是好几秒，
  而这一支的结论和切不切词无关
* 锁定之后原样复用 —— 每轮重挑一次会让缓存全废，那是直接写在账单上的
* 预算线之下的章节也要留档 —— 用户要能看到「差 504 字装不下」
* 问到没载入的章节要说破 —— 不说破，模型就对着一份残缺的书凭标题编
"""

from __future__ import annotations

import pytest

from booksoul_api.services import chapter_selector as sel

# 每节正文的字数。测试里的预算都按它的倍数写。
CHARS = 600


def _chapters() -> list[sel.ChapterIn]:
    """一本假书。**每节正文长度一律裁成 600 字**，这样测试里的预算线是可算的。

    长度不齐会让"预算刚好放得下两节"这种断言变成猜谜：贪心装箱跳过装不下的那节
    继续往下装，结果对、但和测试当初设想的那两节不是同一对。
    """
    seeds = {
        "婆罗门之子": "悉达多是婆罗门之子，他在父亲的屋影里长大。",
        "沙门": "悉达多与乔文达一同出家，随沙门修苦行。",
        "乔达摩": "他们见到了佛陀乔达摩，佛陀讲说法义，世尊的法义是圆满的。",
        "觉醒": "悉达多离开佛陀，他要自己去学，自己去经历。",
        "迦摩罗": "他进了城，遇见名妓迦摩罗，学会了爱与买卖。",
    }
    return [
        sel.ChapterIn(
            id=100 + n,
            idx=n,
            title=title,
            word_count=CHARS,
            content=(seed * 40)[:CHARS],
        )
        for n, (title, seed) in enumerate(seeds.items(), 1)
    ]


def _no_bm25(monkeypatch) -> None:
    """把 BM25 换成炸弹：只要有人跑它，测试就红。"""
    from booksoul import retrieve

    class Boom:
        def __init__(self, *a, **kw):
            raise AssertionError("这条路不该跑 BM25")

    monkeypatch.setattr(retrieve, "BM25", Boom)


def test_整本装得下就不跑BM25(monkeypatch):
    chapters = _chapters()
    _no_bm25(monkeypatch)

    choice = sel.choose(
        chapters=chapters, question="悉达多为什么拒绝追随佛陀？", budget_chars=10**6
    )

    assert choice.reason == sel.WHOLE_BOOK
    assert choice.whole_book is True
    assert choice.selected_ids == [c.id for c in chapters]
    # 没跑 BM25 就没有分数可言。界面据此说明「这一列不是排名」。
    assert choice.query_terms == []
    assert all(item.score == 0 for item in choice.items)


def test_装不下就按预算截断且选中的按原书顺序排(monkeypatch):
    chapters = _chapters()
    one = len(chapters[0].content)

    choice = sel.choose(
        chapters=chapters,
        question="佛陀的法义是什么？乔达摩说了什么？",
        budget_chars=one * 2 + 10,
    )

    assert choice.reason == sel.BM25
    assert choice.whole_book is False
    assert choice.used_chars <= choice.budget_chars
    assert 0 < len(choice.selected_ids) < len(chapters)
    # 选中的必须按原书顺序交出去：模型读到的是一本书，不是一张排行榜
    assert choice.selected_ids == sorted(choice.selected_ids)
    # 问的是佛陀与乔达摩，《乔达摩》那一节必须在里面
    assert 103 in choice.selected_ids


def test_全书每一节都留一行_包括没选中的():
    chapters = _chapters()
    one = len(chapters[0].content)

    choice = sel.choose(
        chapters=chapters, question="佛陀的法义", budget_chars=one * 2 + 10
    )

    assert len(choice.items) == len(chapters) == choice.total_count
    assert [item.rank for item in choice.items] == list(range(1, len(chapters) + 1))
    assert {item.selected for item in choice.items} == {True, False}
    # 没选中的那些同样带着分数和字数 —— 「凭什么没选它」要答得出来
    dropped = [item for item in choice.items if not item.selected]
    assert all(item.word_count > 0 for item in dropped)
    assert all(item.cumulative_chars == 0 for item in dropped)


def test_预算连一章都装不下时至少给最相关的一章():
    chapters = _chapters()

    choice = sel.choose(chapters=chapters, question="佛陀的法义", budget_chars=10)

    assert len(choice.selected_ids) == 1
    # 超了预算。这不是 bug：给模型半节书比给一节完整的更糟，所以宁可超。
    assert choice.used_chars > choice.budget_chars
    picked = next(item for item in choice.items if item.selected)
    assert picked.rank == 1


def test_已经锁定过就原样复用_不再挑一次(monkeypatch):
    chapters = _chapters()
    _no_bm25(monkeypatch)

    choice = sel.choose(
        chapters=chapters,
        question="那他后来为什么又回到河边？",
        budget_chars=10,  # 小得离谱也无所谓：锁定了就不重算
        locked_ids=[101, 103],
    )

    assert choice.reason == sel.LOCKED
    assert choice.selected_ids == [101, 103]
    assert choice.selected_idx == {1, 3}
    # 锁定这一轮没有产生新的选章决策，不该再往 retrievals 里写一行
    assert choice.records is False
    assert choice.items == []


def test_问到没载入的章节时给出需重选信号(monkeypatch):
    chapters = _chapters()
    _no_bm25(monkeypatch)

    choice = sel.choose(
        chapters=chapters,
        question="迦摩罗教了他什么？",
        budget_chars=10,
        locked_ids=[101, 103],
    )

    assert choice.reason == sel.RELOCK_NEEDED
    assert choice.mentioned == ["迦摩罗"]
    # 还没重选，锁定集合原样不动 —— 重选要花钱，得用户点头
    assert choice.selected_ids == [101, 103]


def test_问到的是已经载入的章节就不打扰用户(monkeypatch):
    chapters = _chapters()
    _no_bm25(monkeypatch)

    choice = sel.choose(
        chapters=chapters,
        question="乔达摩讲的法义是什么？",
        budget_chars=10,
        locked_ids=[101, 103],
    )

    assert choice.reason == sel.LOCKED
    assert choice.mentioned == []


def test_单字标题不算命中_否则几乎每个问题都会被拦(monkeypatch):
    _no_bm25(monkeypatch)
    chapters = [
        sel.ChapterIn(id=1, idx=1, title="序", word_count=10, content="序言。"),
        sel.ChapterIn(id=2, idx=2, title="唵", word_count=10, content="唵。"),
    ]

    choice = sel.choose(
        chapters=chapters,
        question="他在河边听到的那个声音是什么？",
        budget_chars=10,
        locked_ids=[1],
    )

    assert choice.reason == sel.LOCKED


def test_重选会把锁定换成新的一套():
    chapters = _chapters()

    choice = sel.choose(
        chapters=chapters,
        question="迦摩罗教了他什么？",
        budget_chars=len(chapters[0].content) * 2 + 10,
        locked_ids=[101, 103],
        reselect=True,
    )

    assert choice.reason == sel.BM25
    assert choice.records is True
    assert 105 in choice.selected_ids  # 《迦摩罗》这次进来了


def test_选中的序号是idx不是主键id():
    """这两个数在这条路上极易混淆，混了就读到错位的章节或一节都读不到。"""
    chapters = _chapters()

    choice = sel.choose(chapters=chapters, question="佛陀的法义", budget_chars=10**6)

    assert choice.selected_ids == [101, 102, 103, 104, 105]
    assert choice.selected_idx == {1, 2, 3, 4, 5}


def test_下一节差多少字装不下():
    chapters = _chapters()
    one = len(chapters[0].content)

    choice = sel.choose(
        chapters=chapters, question="佛陀的法义", budget_chars=one * 2 + 10
    )
    short = choice.shortfall

    assert short is not None
    assert short.need == next(
        item.word_count for item in choice.items if not item.selected
    )
    assert short.left == choice.budget_chars - choice.used_chars
    assert short.short == short.need - short.left > 0


def test_整本装得下就没有差多少字这回事():
    choice = sel.choose(chapters=_chapters(), question="佛陀的法义", budget_chars=10**6)
    assert choice.shortfall is None


@pytest.mark.parametrize("locked", [[], [999]])
def test_锁定里全是已经不存在的章节就当没锁定(locked):
    """重解析会重建 chapters，老的 id 全都作废。这时候要重挑，不是读零节。"""
    choice = sel.choose(
        chapters=_chapters(),
        question="佛陀的法义",
        budget_chars=10**6,
        locked_ids=locked,
    )
    assert choice.reason == sel.WHOLE_BOOK
    assert len(choice.selected_ids) == 5


def test_file_path只是被携带的_不参与打分():
    """`ChapterIn` 带上 `file_path` 是为了让溯源少查一张旁表，与选章无关。

    让它漏进打分不会报错，只会让「模型读哪几节」悄悄取决于磁盘上的文件名——
    而那个名字是解析时按序号生成的，换一次解析器就可能变。
    """
    from dataclasses import replace

    光秃秃 = _chapters()
    带路径 = [
        replace(one, file_path=f"chapters/{one.idx:03d}-{one.title}.md")
        for one in 光秃秃
    ]
    问题 = "佛陀的法义是什么？乔达摩说了什么？"
    预算 = CHARS * 2 + 10

    甲 = sel.choose(chapters=光秃秃, question=问题, budget_chars=预算, locked_ids=[])
    乙 = sel.choose(chapters=带路径, question=问题, budget_chars=预算, locked_ids=[])

    assert 甲 == 乙
