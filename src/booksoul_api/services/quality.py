"""质检信号：字数条不是装饰，是让人一眼看出哪里可能出错的工具。

这一层是纯函数，不碰数据库也不碰 HTTP —— 判据本身要能脱开一切单独验证，
因为它是会被反复调参数的那种东西。
"""

from __future__ import annotations

from statistics import median

# 短到不可能是一章正文的绝对下限。「第一部」这种分部标题页只有 3-5 字。
_ABSOLUTE_FLOOR = 100
# 相对下限：不到全书中位数这个比例才算异常。
# 只看绝对值会误伤：一本俳句集每章都只有二十来字，那是它本来的样子。
_RELATIVE_FLOOR = 0.2
# 章节太少时不做相对判断——三章的书算不出有意义的中位数。
_MIN_CHAPTERS = 4


def short_chapters(chapters: list[dict]) -> list[dict]:
    """挑出「短到该让人看一眼」的章节。

    两个条件同时成立才算：绝对字数低于下限，**且**远低于全书中位数。
    只用绝对值会把每章都短的书整本标黄，那样这个信号就没人看了。

    返回原字典，附上 `median` 供提示语说明为什么它显得异常。
    """
    if len(chapters) < _MIN_CHAPTERS:
        return []

    counts = [int(c.get("word_count", 0)) for c in chapters]
    mid = median(counts)
    if mid <= 0:
        return []

    threshold = min(_ABSOLUTE_FLOOR, mid * _RELATIVE_FLOOR)
    return [
        {**chapter, "median": mid}
        for chapter, count in zip(chapters, counts, strict=True)
        if count < threshold
    ]


def short_chapter_note(chapter: dict) -> str:
    """给用户看的那句话：说清它为什么被标出来，以及要他判断什么。"""
    title = chapter.get("title") or f"第 {chapter.get('idx')} 节"
    words = int(chapter.get("word_count", 0))
    return (
        f"「{title}」只有 {words} 字，全书中位数是 {int(chapter['median']):,} 字。"
        "多半是分部标题页，也可能是这一节的正文没抽出来——打开看一眼就知道。"
    )
