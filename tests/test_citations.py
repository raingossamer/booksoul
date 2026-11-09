"""溯源：把回答里的引文定位回原文的哪一节哪一段。

这一整个文件是纯逻辑，不连库、不打模型。

盯的是同一条线：**对不上就说对不上，不猜。**

一句定位错了的引文比没有引文糟得多——它把用户送到一段他没引的话前面，而那段话
看起来完全像是出处。所以这里只做「归一化之后的精确包含」，不做模糊匹配、不做
相似度阈值。归一化本身是对称的：两边都去掉脚注标记、强调符号和空白。
"""

from __future__ import annotations

from booksoul_api.services import citations as cite

CH3 = [
    "# 婆罗门之子",
    "> 悉达多　·　第 3/16 节　·　3,710 字",
    "悉达多，俊美的婆罗门[^1]之子，年轻的鹰隼，在屋舍阴凉处，"
    "与他的好友，同为婆罗门之子的乔文达一道长大。",
    "欢喜涌上他父亲的心头。这个善悟而渴慕知识的儿子将成长为伟大的贤士和僧侣。",
    "所有人都热爱悉达多。悉达多令所有人喜悦。",
]

CH5 = [
    "# 乔达摩",
    "> 悉达多　·　第 5/16 节　·　3,948 字",
    "“没人能通过法义得到解脱！”悉达多说。"
    "“您从未以言辞或法义宣讲您在证觉成道之际所发生的事。”",
]


def _sources(*, loaded=(3, 5)) -> list[cite.Source]:
    out = []
    if 3 in loaded:
        out.append(
            cite.Source(
                chapter_id=103, chapter_idx=3, title="婆罗门之子", paragraphs=CH3
            )
        )
    if 5 in loaded:
        out.append(
            cite.Source(chapter_id=105, chapter_idx=5, title="乔达摩", paragraphs=CH5)
        )
    return out


# ------------------------------------------------------------------ 切块


def test_切块要和阅读页看到的块一一对应():
    """`para_index` 指的是阅读页渲出来的第几个顶层块。

    切法一旦和 `web/src/lib/markdown.ts` 分叉，高亮就会落在隔壁那一段上——
    而那一段看起来同样像是出处。标题和那行元信息各算一块（阅读页确实渲了它们），
    脚注定义不算（阅读页把页尾那一段整个拆走了）。
    """
    blocks = cite.paragraphs(
        "# 婆罗门之子\n\n> 悉达多　·　第 3/16 节\n\n正文一。\n\n正文二。\n\n"
        "[^1]: 婆罗门，印度社会阶级之一。\n[^2]: 唵。\n"
    )

    assert blocks == ["# 婆罗门之子", "> 悉达多　·　第 3/16 节", "正文一。", "正文二。"]


def test_段内的换行不拆块():
    """一段里的软换行是排版，不是分段。拆了它，段号就全错位。"""
    assert cite.paragraphs("第一句，\n接着还是这一段。\n\n下一段。") == [
        "第一句，\n接着还是这一段。",
        "下一段。",
    ]


# ------------------------------------------------------------------ 定位


def test_块引用定位到具体的章与段():
    answer = (
        "他一开始就肯定了法义的完备：\n\n> 没人能通过法义得到解脱！\n\n往下属于推演。"
    )

    found = cite.find(answer, _sources())

    assert len(found) == 1
    one = found[0]
    assert one.chapter_id == 105
    assert one.chapter_idx == 5
    assert one.chapter_title == "乔达摩"
    assert one.para_index == 2
    assert "没人能通过法义得到解脱" in one.quote


def test_行内引号定位得到也算引文():
    answer = "书里写「所有人都热爱悉达多。悉达多令所有人喜悦。」，这是第三节的调子。"

    found = cite.find(answer, _sources())

    assert [(f.chapter_idx, f.para_index) for f in found] == [(3, 4)]


def test_行内引号定位不到就不当引文():
    """中文的「」既用来引用也用来强调。

    「把佛陀当作外在权威来依附」是研读者自己的措辞，不是书里的话。把它记成
    一条「原文里没找到」的引文，等于对着用户喊一次狼来了——而下一次真的有
    引文对不上时，他已经不看那句提示了。
    """
    answer = "他拒绝的是「把佛陀当作外在权威来依附」这条路径本身。"

    assert cite.find(answer, _sources()) == []


def test_块引用对不上就记成找不到_并且不猜是哪一章():
    """块引用是明确的「我在引原文」。它对不上，用户就该知道。"""
    answer = "> 悉达多在河边见到了瓦稣迪瓦，两人相视而笑。"

    found = cite.find(answer, _sources())

    assert len(found) == 1
    assert found[0].para_index == -1
    assert found[0].chapter_id is None
    assert found[0].chapter_idx is None
    assert found[0].chapter_title == ""


def test_只在这一轮真载入的章节里找():
    """没载入的章节，模型根本没看见过它的正文。

    这时候即便书里确实有这句话，也不能替它认下来——它不是引用，是背出来的，
    或者编出来的。认下来就等于用一条「去原文」的链接替一段幻觉背书。
    """
    answer = "> 没人能通过法义得到解脱！"

    found = cite.find(answer, _sources(loaded=(3,)))

    assert found[0].para_index == -1
    assert found[0].chapter_id is None


# ------------------------------------------------------- 归一化（对称地做）


def test_原文里的脚注标记不影响比对():
    answer = "> 悉达多，俊美的婆罗门之子，年轻的鹰隼"

    found = cite.find(answer, _sources())

    assert (found[0].chapter_idx, found[0].para_index) == (3, 2)


def test_引文里的换行与空格不影响比对():
    answer = "> 欢喜涌上他父亲的心头。\n> 这个善悟而渴慕知识的儿子\n> 将成长为伟大的贤士和僧侣。"

    found = cite.find(answer, _sources())

    assert (found[0].chapter_idx, found[0].para_index) == (3, 3)


def test_省略号当成省略_两截都得在同一段里按顺序出现():
    """「A……B」是模型在说「中间我略去了」。

    按省略处理不是猜：它明确标了这里有删节。但两截必须落在**同一段**、
    而且顺序不能反——否则就成了拼接两段不相干的话。
    """
    answer = "> 悉达多，俊美的婆罗门之子……与他的好友，同为婆罗门之子的乔文达一道长大。"

    found = cite.find(answer, _sources())

    assert (found[0].chapter_idx, found[0].para_index) == (3, 2)


def test_省略号两截跨了段就不算():
    answer = "> 悉达多，俊美的婆罗门之子……所有人都热爱悉达多。"

    found = cite.find(answer, _sources())

    assert found[0].para_index == -1


def test_顺序反了就不算():
    answer = "> 年轻的鹰隼……悉达多，俊美的婆罗门之子"

    found = cite.find(answer, _sources())

    assert found[0].para_index == -1


# ------------------------------------------------------------------ 其它


def test_同一句引两次只记一条():
    answer = (
        "> 所有人都热爱悉达多。\n\n这句话在第三节。后面他又说了一次：\n\n"
        "> 所有人都热爱悉达多。\n"
    )

    assert len(cite.find(answer, _sources())) == 1


def test_太短的行内引号不当引文():
    """「唵」「我」这种在书里到处都是，认下来只会把用户送到随便一段。"""
    answer = "他念的是「唵」，而不是「我」。"

    assert cite.find(answer, _sources()) == []


def test_没有引文的回答就是没有引文():
    answer = "这个问题书里没有正面回答。我只能说它的理路指向哪里。"

    assert cite.find(answer, _sources()) == []


def test_一节都没载入时块引用照样记成找不到():
    """不是「没有正文可比就跳过」——那等于在最该报警的时候最安静。"""
    found = cite.find("> 随便什么", [])

    assert [f.para_index for f in found] == [-1]
