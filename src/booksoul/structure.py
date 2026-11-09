"""在没有目录的纯文本里推断章节结构。

EPUB/DOCX 自带目录，直接用就行；TXT 和一部分 PDF 没有，只能从正文里认标题。
这里的策略是"先找规律，再套规律"：先统计全书里哪种标题写法成规模地反复出现，
再只按那一种切分。这样比逐行套一堆正则稳得多——单条正则很容易把正文里的
"第二天早上他醒来"这种句子误判成章节标题。
"""

from __future__ import annotations

import re

from .model import Chapter

_CN_NUM = "零〇一二三四五六七八九十百千两"

# 序号和标题之间**必须**有分隔（空格、冒号、点号、破折号），或者干脆没有标题。
# 少了这条约束，正文里"第二章的内容在这里……"这种句子会被当成章节标题——
# "第二章"后面直接跟"的内容"，正则照样能匹配上。这是章节推断最常见的误判来源。
_SEP = r"(?:\s*$|[　\s:：·.、\-—－]+(?P<t>.{0,40})\s*$)"
_SEP_EN = r"(?:\s*$|[\s:：.\-—]+(?P<t>.{0,60})\s*$)"

# 每个模式给一个 (名字, 正则, 层级, 最少命中次数)。层级用来还原"卷 > 章 > 节"的嵌套。
#
# 最少命中次数按层级区分：一本书分两卷是常态，但只有两处"第X章"就很可疑
# （多半是正文里提到了章节号）。所以顶层分卷 2 次即可采信，章节一级要 3 次。
_PATTERNS: list[tuple[str, re.Pattern[str], int, int]] = [
    ("卷", re.compile(rf"^\s*第\s*[{_CN_NUM}\d]{{1,8}}\s*[卷部篇]{_SEP}"), 1, 2),
    ("章", re.compile(rf"^\s*第\s*[{_CN_NUM}\d]{{1,8}}\s*[章回折]{_SEP}"), 2, 3),
    ("节", re.compile(rf"^\s*第\s*[{_CN_NUM}\d]{{1,8}}\s*[节節]{_SEP}"), 3, 3),
    ("chapter", re.compile(rf"^\s*[Cc]hapter\s+[\dIVXLCivxlc]{{1,6}}{_SEP_EN}"), 2, 3),
    (
        "part",
        re.compile(rf"^\s*(?:[Pp]art|[Bb]ook)\s+[\dIVXLCivxlc]{{1,6}}{_SEP_EN}"),
        1,
        2,
    ),
    ("数字点", re.compile(r"^\s*(?P<n>\d{1,3})\s*[.、]\s+(?P<t>\S.{0,40})\s*$"), 2, 3),
    ("裸数字", re.compile(r"^\s*(?P<n>\d{1,3})\s*$"), 2, 3),
]

# 这些独立成行时一律算章节，不参与"成规模"统计
_STANDALONE = re.compile(
    r"^\s*(序章|楔子|自序|代序|序言|序|前言|引子|引言|导言|导读|题记|"
    r"尾声|终章|后记|跋|附录|附:|注释|译后记|译序|译者序|出版说明|作者简介|"
    r"内容简介|编者按|致谢|参考文献|番外)\s*[:：]?\s*(?P<t>.{0,40})$"
)

_MAX_TITLE_LEN = 45


def _is_titleish(line: str) -> bool:
    """标题行的共性：短、独立、不以句末标点收尾。"""
    s = line.strip()
    if not s or len(s) > _MAX_TITLE_LEN:
        return False
    return s[-1] not in "。！？，、；：,;"


def detect_chapters(text: str) -> list[Chapter] | None:
    """从整篇文本里切出章节。认不出来就返回 None，由调用方决定怎么退化。"""
    lines = text.split("\n")

    # 第一遍：统计每种模式命中多少次，找出这本书实际在用的写法
    hits: dict[str, list[tuple[int, str, int]]] = {}
    thresholds: dict[str, int] = {}
    for idx, line in enumerate(lines):
        if not _is_titleish(line):
            continue
        for name, rx, level, min_hits in _PATTERNS:
            if rx.match(line.strip()):
                hits.setdefault(name, []).append((idx, line.strip(), level))
                thresholds[name] = min_hits
                break

    used = {
        name: found for name, found in hits.items() if len(found) >= thresholds[name]
    }
    # "裸数字"太容易误伤（正文里孤立的年份、页码），只有在没有别的模式时才用
    if len(used) > 1:
        used.pop("裸数字", None)
    if "数字点" in used and len(used) > 1:
        used.pop("数字点", None)

    marks: list[tuple[int, str, int]] = []
    for found in used.values():
        marks.extend(found)

    # 独立成行的序/跋类标题无条件加入
    for idx, line in enumerate(lines):
        if _is_titleish(line) and _STANDALONE.match(line.strip()):
            marks.append((idx, line.strip(), 1))

    if not marks:
        return None

    marks.sort(key=lambda x: x[0])
    deduped: list[tuple[int, str, int]] = []
    for mark in marks:
        if not deduped or mark[0] != deduped[-1][0]:
            deduped.append(mark)

    # 规范化层级：把实际用到的层级压缩成连续的 1..n
    levels = sorted({lv for _, _, lv in deduped})
    remap = {lv: i + 1 for i, lv in enumerate(levels)}

    chapters: list[Chapter] = []
    first = deduped[0][0]
    if first > 0:
        head = "\n".join(lines[:first]).strip()
        if head:
            chapters.append(Chapter(title="卷首", content=head, level=1, order=0))

    for i, (idx, title, level) in enumerate(deduped):
        end = deduped[i + 1][0] if i + 1 < len(deduped) else len(lines)
        body = "\n".join(lines[idx + 1 : end]).strip()
        chapters.append(
            Chapter(
                title=title,
                content=body,
                level=remap[level],
                order=len(chapters),
                source_id=f"line:{idx + 1}",
            )
        )
    return chapters


def split_by_size(text: str, title: str, *, target: int = 6000) -> list[Chapter]:
    """认不出章节时的退路：按段落边界切成大小接近 target 字的块。

    宁可切得糙一点，也不要把整本书塞进一个 Chapter——下游的检索和引用都按章节走。
    """
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paras:
        return []

    chunks: list[list[str]] = [[]]
    size = 0
    for para in paras:
        if size and size + len(para) > target:
            chunks.append([])
            size = 0
        chunks[-1].append(para)
        size += len(para)

    if len(chunks) == 1:
        return [Chapter(title=title, content="\n\n".join(chunks[0]), level=1, order=0)]

    return [
        Chapter(
            title=f"{title}（{i + 1}/{len(chunks)}）",
            content="\n\n".join(c),
            level=1,
            order=i,
        )
        for i, c in enumerate(chunks)
    ]
