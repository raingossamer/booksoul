"""溯源：把回答里的引文定位回原文的哪一节哪一段（界面稿⑤屏上半）。

纯逻辑。不碰数据库、不碰模型 API，进来的是一段回答加几节正文，出去的是若干条
「这句话在第几节第几段」。

**这里的全部纪律是：对不上就说对不上，不猜。**

一句定位错了的引文比没有引文糟得多。用户点「去原文」是为了亲眼核对，而错误的
链接会把他送到一段他没引的话前面——那段话看起来完全像是出处，他核对完就更信了。
所以这里只做「归一化之后的精确包含」：不做模糊匹配，不做相似度阈值，不做
「最接近的那一段」。归一化本身是对称的，两边都做同一套。

三处需要解释的判断：

**一、行内「」和块引用不是一回事。** 中文的「」既用来引用也用来强调
（「把佛陀当作外在权威来依附」是研读者自己的措辞）。所以：块引用（`>`）永远
记一条，对不上就记成「原文里没找到」——那是明确的「我在引原文」；行内引号
**只在定位得到时才记**，定位不到就当它是强调。对着用户喊一次狼来了，下一次
真有引文对不上时他已经不看那句提示了。

**二、只在这一轮真载入的章节里找。** 没载入的章节模型根本没看见过正文；
即便书里确实有这句话，也不能替它认下来——它不是引用，是背出来的或编出来的。
认下来就等于用一条「去原文」的链接替一段幻觉背书。

**三、`para_index` 指的是阅读页渲出来的第几个顶层块**，不是「第几个自然段」。
切法必须和 `web/src/lib/markdown.ts` 一致，否则高亮会落到隔壁那一段上——
而那一段看起来同样像是出处。这是一处真实的跨语言耦合，`paragraphs()` 上有说明。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

# 脚注定义块：`[^1]: 正文`。阅读页把页尾那一整段拆走了，所以它不算块。
_FOOTNOTE_DEF = re.compile(r"^\[\^[^\]]+\]:")
# 脚注引用标记：正文里的 `[^1]`。比对时两边都去掉——模型未必会把它抄进引文。
_FOOTNOTE_REF = re.compile(r"\[\^[^\]]+\]")
# 强调符号。`**这样**` 在模型的引文里常常掉一层。
_EMPHASIS = re.compile(r"[*_`]+")
_SPACE = re.compile(r"\s+")

# 块引用：连续的 `> ` 行算一块
_BLOCKQUOTE = re.compile(r"(?:^[ \t]*>[^\n]*\n?)+", re.MULTILINE)
# 行内引号。不处理嵌套——嵌套的引号在中文里本来就少见，猜它只会猜错
_INLINE = re.compile(r"[「『“]([^「」『』“”]{2,400})[」』”]")
# 省略号。模型用它表示「这中间我略去了」
_ELLIPSIS = re.compile(r"(?:…+|\.{3,}|。{3,})")

# 行内引号至少要这么长才当引文。「唵」「我」这种在书里到处都是，
# 认下来只会把用户送到随便一段。
_MIN_INLINE = 6


@dataclass(frozen=True)
class Source:
    """一节可供比对的正文。`paragraphs` 由 `paragraphs()` 切出来。"""

    chapter_id: int
    chapter_idx: int
    title: str
    paragraphs: list[str]


@dataclass(frozen=True)
class Found:
    """一条引文。`para_index == -1` 就是「对不上」，那时章节三项全是空。"""

    quote: str
    chapter_id: int | None
    chapter_idx: int | None
    chapter_title: str
    para_index: int

    @property
    def located(self) -> bool:
        return self.para_index >= 0


def paragraphs(markdown: str) -> list[str]:
    """按阅读页渲出来的顶层块切开。

    **这个函数和 `web/src/lib/markdown.ts` 是一对。** 那边把 Markdown 渲成 HTML，
    取 `body` 的直接子元素当块，并且先把页尾的脚注区整段拆走。这边要切出同一个
    序列，`para_index` 才指得准。

    两边分叉不会报错，只会让高亮落在隔壁那一段上——而隔壁那一段看起来同样像是
    出处。所以阅读页拿到 `para_index` 之后还会**核对引文是不是真在那一块里**，
    对不上就自己去找：索引是快路，原文是准绳。
    """
    out = []
    for block in re.split(r"\n[ \t]*\n", markdown.strip()):
        block = block.strip()
        # 空块、脚注定义、分隔线都不是阅读页上的块
        if not block or _FOOTNOTE_DEF.match(block) or set(block) <= set("-*_ "):
            continue
        out.append(block)
    return out


def quotes(answer: str) -> list[tuple[str, bool]]:
    """把回答里的引文抠出来，标明它是不是块引用。

    返回 `(引文, 是不是块引用)`，按出现顺序，重复的只留第一条。
    """
    out: list[tuple[str, bool]] = []
    seen: set[str] = set()

    def keep(text: str, block: bool) -> None:
        text = text.strip()
        key = _norm(text)
        if not key or key in seen:
            return
        seen.add(key)
        out.append((text, block))

    stripped = answer
    for match in _BLOCKQUOTE.finditer(answer):
        lines = [
            re.sub(r"^[ \t]*>[ \t]?", "", line) for line in match.group(0).splitlines()
        ]
        keep("\n".join(lines), True)
        # 块引用里的「」不再单独算一条：它是这段引文的一部分
        stripped = stripped.replace(match.group(0), "\n")

    for match in _INLINE.finditer(stripped):
        text = match.group(1)
        if len(_norm(text)) >= _MIN_INLINE:
            keep(text, False)
    return out


def find(answer: str, sources: Sequence[Source]) -> list[Found]:
    """把回答里的引文找出来并定位。"""
    out = []
    for text, block in quotes(answer):
        hit = locate(text, sources)
        if hit is None:
            # 行内引号定位不到就当它是强调，不记。块引用记下来，
            # 并且照实说「原文里没找到」。
            if block:
                out.append(
                    Found(
                        quote=text,
                        chapter_id=None,
                        chapter_idx=None,
                        chapter_title="",
                        para_index=-1,
                    )
                )
            continue
        source, index = hit
        out.append(
            Found(
                quote=text,
                chapter_id=source.chapter_id,
                chapter_idx=source.chapter_idx,
                chapter_title=source.title,
                para_index=index,
            )
        )
    return out


def locate(quote: str, sources: Sequence[Source]) -> tuple[Source, int] | None:
    """这句话出自哪一节的第几块。找不到返回 None——不返回「最接近的那个」。

    公开的，因为 S12 的 `relocate` 要用**同一个**匹配器把旧引文放回重解析后的
    章节里。它另写一份的话，「重定位命中率」这个数字测的就成了两套算法的差，
    而不是重解析的影响——而那个数字是一条人工裁决的依据。
    """
    # 省略号是模型在说「这中间我略去了」。按省略处理不是猜：它明确标了有删节。
    # 但每一截都必须落在**同一块**里，而且顺序不能反——否则就成了把两段
    # 不相干的话拼在一起，再给它一条指向其中一段的链接。
    parts = [_norm(p) for p in _ELLIPSIS.split(quote)]
    parts = [p for p in parts if p]
    if not parts:
        return None

    for source in sources:
        for index, block in enumerate(source.paragraphs):
            at = 0
            hay = _norm(block)
            for part in parts:
                at = hay.find(part, at)
                if at < 0:
                    break
                at += len(part)
            else:
                return source, index
    return None


def _norm(text: str) -> str:
    """比对用的形态。**两边都做同一套**，所以它不是模糊匹配。

    空白整个删掉而不是合起来：中文里空白不承载意义，而模型会把一段话重新折行、
    在标点后补空格。删掉之后两边仍然是精确比较，只是比的是「去掉排版之后」的字。
    """
    text = _FOOTNOTE_REF.sub("", text)
    text = _EMPHASIS.sub("", text)
    text = re.sub(r"^#{1,6}[ \t]+", "", text, flags=re.MULTILINE)
    return _SPACE.sub("", text)
