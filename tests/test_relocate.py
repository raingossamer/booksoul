"""重解析之后把旧引文重新定位（S12 深模块 CitationRelocator）。

重解析会把 `chapters` 整张重建。旧引文行上的 `chapter_id` 走 `ON DELETE SET NULL`
被置空，`para_index` 指向的是上一套切分里的第几块——两样都作废了。用户点
「去原文」时要么落到一段他没引的话前面，要么按钮是死的。

这个模块的活就是：拿引文原文，去新的一套章节里重新找一遍。

四条纪律：

一、**用和当初定位时同一套匹配器。** `citations.locate()` 那一个，不另写一份。
另写一份的话，「命中率」这个数字测的就成了两套算法的差，而不是重解析的影响——
而这一片的人工卡点正是要人看着那个数字做裁决。

二、**找不到就是找不到。** 和 S9 同一条：一条定位错了的引文比没有引文糟得多。

三、**要分得清「命中」和「真的动了」。** 一本书重解析前后正文一个字没改，
命中率必然 100%，那个数字什么都证明不了。报告要同时给出**位置变了几条**——
只有这一列非零，命中率才是一个有意义的数。

四、**先在同名章节里找。** 重解析通常不改章节名。一句话在两节里都出现过时
（书里前后呼应是常态），按顺序扫会把它认到第一节去。同名优先不是提速，是提准。
"""

from __future__ import annotations

from booksoul_api.services import citations as cite
from booksoul_api.services import relocate as reloc

# 一本小书的两节。段与段之间空行分开，和阅读页切块的规则一致。
BRAHMIN = """# 婆罗门之子

悉达多，俊美的婆罗门之子，与他的好友乔文达一道长大。

他学会了静默，学会了斋戒，学会了思考。

父亲的屋里没有他要的东西。"""

GOTAMA = """# 乔达摩

两位朝拜乔达摩的青年沙门，一路探询到达此地。

世尊的法义是圆满的，可它没有讲那件事：一个人如何亲自走过河。

他学会了静默，学会了斋戒，学会了思考。"""


def _sources(*chapters: tuple[int, int, str, str]) -> list[cite.Source]:
    return [
        cite.Source(
            chapter_id=cid,
            chapter_idx=idx,
            title=title,
            paragraphs=cite.paragraphs(body),
        )
        for cid, idx, title, body in chapters
    ]


def _new_book() -> list[cite.Source]:
    return _sources((11, 1, "婆罗门之子", BRAHMIN), (12, 2, "乔达摩", GOTAMA))


def _stale(**kw) -> reloc.Stale:
    base = {"citation_id": 1, "quote": "", "chapter_title": ""}
    return reloc.Stale(**{**base, **kw})


# ------------------------------------------------------------------ 命中


def test_正文未变时全部命中():
    stale = [
        _stale(
            citation_id=1, quote="悉达多，俊美的婆罗门之子", chapter_title="婆罗门之子"
        ),
        _stale(citation_id=2, quote="一路探询到达此地", chapter_title="乔达摩"),
    ]
    moved, report = reloc.relocate(stale, _new_book())

    assert [m.located for m in moved] == [True, True]
    assert report.total == 2
    assert report.located == 2
    assert report.lost == 0
    assert report.rate == 1.0


def test_正文没改时报告要说清楚位置一条都没动():
    """命中率 100% 而位置零变动，说明这次重解析什么都没改。

    不把这一列摆出来，「命中率 100%」会被当成重定位很管用的证据，
    而它其实连一次真正的重定位都没做过。
    """
    stale = [
        _stale(
            citation_id=1, quote="悉达多，俊美的婆罗门之子", chapter_title="婆罗门之子"
        ),
    ]
    # 旧位置：第 1 节第 0 块
    stale[0] = reloc.Stale(
        citation_id=1,
        quote="悉达多，俊美的婆罗门之子",
        chapter_title="婆罗门之子",
        chapter_idx=1,
        # 标题行本身就是阅读页上的第 0 块，正文从 1 起数
        para_index=1,
    )
    _moved, report = reloc.relocate(stale, _new_book())

    assert report.located == 1
    assert report.shifted == 0
    assert report.unchanged == 1


def test_章节切分变了也能跨章命中():
    """重解析把两节并成一节：段号全变、章节 id 全变，引文还得找得到。"""
    merged = _sources((21, 1, "第一部", BRAHMIN + "\n\n" + GOTAMA.split("\n", 1)[1]))
    stale = [
        reloc.Stale(
            citation_id=1,
            quote="一路探询到达此地",
            chapter_title="乔达摩",  # 这个标题在新书里已经不存在了
            chapter_idx=2,
            para_index=0,
        )
    ]
    moved, report = reloc.relocate(stale, merged)

    assert moved[0].located
    assert moved[0].chapter_id == 21
    assert moved[0].chapter_title == "第一部"
    assert report.shifted == 1  # 换了章，算动过


def test_同名章节优先():
    """同一句话在两节里都出现过时，认回它原来那一节。

    「他学会了静默…」在两节里各出现一次——书里前后呼应是常态。按顺序扫会把
    第二节那条认到第一节去，而用户点过去看到的是一段他没引的话。
    """
    stale = [
        reloc.Stale(
            citation_id=1,
            quote="他学会了静默，学会了斋戒",
            chapter_title="乔达摩",
            chapter_idx=2,
            para_index=3,
        )
    ]
    moved, _report = reloc.relocate(stale, _new_book())

    assert moved[0].chapter_id == 12
    assert moved[0].chapter_title == "乔达摩"


# ------------------------------------------------------------------ 未命中


def test_正文被改写之后未命中而且不误报():
    """换了译本 / 改了清洗规则，正文真的变了。

    这时**没有正确答案**。给一个「最接近的那一段」比给一个灰按钮糟得多：
    用户点过去亲眼核对，看到的是一段他没引的话，而那段话看起来完全像是出处。
    """
    stale = [
        _stale(
            citation_id=1, quote="悉达多，英俊的婆罗门之子", chapter_title="婆罗门之子"
        )
    ]
    moved, report = reloc.relocate(stale, _new_book())

    assert moved[0].located is False
    assert moved[0].para_index == -1
    assert moved[0].chapter_id is None
    # 章节标题留着：界面还能说「原《婆罗门之子》里这段，位置已变更」
    assert moved[0].chapter_title == "婆罗门之子"
    assert report.lost == 1
    assert report.rate == 0.0


def test_一本书里一条都找不到时命中率是零不是崩():
    stale = [_stale(citation_id=1, quote="完全不在这本书里的一句话")]
    _moved, report = reloc.relocate(stale, _new_book())
    assert report.rate == 0.0


def test_没有引文要重定位时命中率不是除零():
    _moved, report = reloc.relocate([], _new_book())
    assert report.total == 0
    assert report.rate == 0.0


def test_新书一节都没有时全部未命中而不是抛错():
    """解析出 0 节是另一条路上的错（服务层会拦），这里只负责不把它变成崩溃。"""
    stale = [_stale(citation_id=1, quote="悉达多，俊美的婆罗门之子")]
    moved, report = reloc.relocate(stale, [])

    assert moved[0].located is False
    assert report.lost == 1


# ------------------------------------------------------------------ 归一化


def test_全角空格与脚注标记不妨碍命中():
    """模型抄引文时会掉脚注标记、会重新折行、会补上全角空格。

    两边过同一套归一化，所以这仍然是精确比较，只是比的是「去掉排版之后」的字。
    """
    body = "# 乔达摩\n\n世尊的法义[^3]　是圆满的，\n可它没有讲那件事。"
    sources = _sources((31, 1, "乔达摩", body))
    stale = [_stale(citation_id=1, quote="世尊的法义是圆满的，可它没有讲那件事。")]

    moved, _report = reloc.relocate(stale, sources)
    assert moved[0].located


def test_引文带强调符号也能命中():
    body = "# 乔达摩\n\n**世尊的法义**是圆满的。"
    sources = _sources((32, 1, "乔达摩", body))
    stale = [_stale(citation_id=1, quote="世尊的法义是圆满的。")]

    assert reloc.relocate(stale, sources)[0][0].located


def test_省略号表示的删节照旧按同一块处理():
    stale = [
        _stale(quote="世尊的法义是圆满的……一个人如何亲自走过河", chapter_title="乔达摩")
    ]
    moved, _report = reloc.relocate(stale, _new_book())

    assert moved[0].located
    assert moved[0].chapter_id == 12


# ------------------------------------------------------------------ 报告


def test_报告的几个数加起来等于总数():
    stale = [
        reloc.Stale(
            citation_id=1,
            quote="悉达多，俊美的婆罗门之子",
            chapter_title="婆罗门之子",
            chapter_idx=1,
            para_index=1,
        ),
        reloc.Stale(
            citation_id=2,
            quote="一路探询到达此地",
            chapter_title="乔达摩",
            chapter_idx=2,
            para_index=9,  # 旧段号是错的，命中后会被纠正
        ),
        _stale(citation_id=3, quote="这句话不在书里"),
    ]
    _moved, report = reloc.relocate(stale, _new_book())

    assert report.total == 3
    assert report.located + report.lost == report.total
    assert report.unchanged + report.shifted == report.located
    assert report.located == 2
    assert report.unchanged == 1
    assert report.shifted == 1


def test_报告能写成一句人话():
    """这一片的人工卡点靠的就是这句话。它得说得出「动了几条」。"""
    stale = [
        reloc.Stale(
            citation_id=1,
            quote="悉达多，俊美的婆罗门之子",
            chapter_title="婆罗门之子",
            chapter_idx=1,
            para_index=1,
        ),
        _stale(citation_id=2, quote="这句话不在书里"),
    ]
    _moved, report = reloc.relocate(stale, _new_book())

    line = report.describe()
    assert "2" in line  # 总数
    assert "1" in line  # 找回来的
    assert "%" in line
