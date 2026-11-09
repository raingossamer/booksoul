"""字数条作为质检信号。

「第一部」只有 5 个字：多半是分部标题页，也可能是这一节的正文压根没抽出来。
这两件事后果差很远，而系统分不清——所以它的职责是**把它指出来让人看一眼**，
不是替人下结论。
"""

from __future__ import annotations

from booksoul_api.services.quality import short_chapter_note, short_chapters


def _chapters(*counts: int) -> list[dict]:
    return [
        {"idx": i, "title": f"第{i}节", "word_count": n}
        for i, n in enumerate(counts, 1)
    ]


def test_混在长章节里的极短章节会被指出来():
    """典型形状：一本正常的书里夹着两个分部标题页。"""
    found = short_chapters(_chapters(3000, 5, 2800, 3200, 5, 2600))

    assert [c["idx"] for c in found] == [2, 5]
    assert found[0]["median"] > 0


def test_每章都短的书一章都不标():
    """一本俳句集每章都只有二十来字，那是它本来的样子。

    只看绝对字数会把整本标黄——而一个全都在响的警报等于没有警报。
    """
    assert short_chapters(_chapters(22, 19, 25, 18, 21, 24)) == []


def test_章节太少时不做判断():
    """三章的书算不出有意义的中位数，硬算只会瞎标。"""
    assert short_chapters(_chapters(3000, 4, 2800)) == []


def test_提示语说清为什么它显得异常以及要人判断什么():
    found = short_chapters(_chapters(3000, 5, 2800, 3200))
    message = short_chapter_note(found[0])

    assert "第2节" in message
    assert "5 字" in message
    # 给出参照，否则「只有 5 字」没有意义
    assert "中位数" in message
    # 说清两种可能，而不是替人下结论
    assert "标题页" in message and "没抽出来" in message


def test_全是空章节时不报_没有可比的基准():
    assert short_chapters(_chapters(0, 0, 0, 0)) == []
