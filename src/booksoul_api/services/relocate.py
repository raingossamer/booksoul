"""重解析之后把旧引文重新定位（S12 深模块 CitationRelocator）。

纯字符串处理，无副作用：进来的是一批引文与一组新章节，出去的是每条的新落点
（或者「找不到」）加一份账。不碰数据库，不碰模型 API，不写文件。

**为什么需要它。** 重解析把 `chapters` 整张重建。旧引文行上的 `chapter_id` 走
`ON DELETE SET NULL` 被置空，`para_index` 指的是上一套切分里的第几块——两样都
作废了。不管的话，用户点「去原文」要么按钮是死的，要么落到一段他没引的话前面。

四条纪律：

**一、用和当初定位时同一套匹配器。** 直接调 `citations.locate()`，不在这里另写
一份。另写一份的话，「命中率」这个数字测的就成了两套算法的差，而不是重解析的
影响——而这一片的人工卡点，正是要人看着那个数字决定这个降级方案接不接受。

**二、找不到就是找不到。** 和 S9 同一条：不做模糊匹配、不做相似度阈值、不给
「最接近的那一段」。一条定位错了的引文比没有引文糟得多——用户点过去是为了亲眼
核对，而错误的链接会把他送到一段他没引的话前面，那段话看起来完全像是出处，
他核对完反而更信了。

**三、报告要分得清「命中」和「真的动了」。** 一本书重解析前后正文一个字没改，
命中率必然 100%，而那个数字什么都没证明——它连一次真正的重定位都没做过。
所以 `Report` 把命中拆成 `unchanged` 与 `shifted` 两列：只有 `shifted` 非零，
那个百分比才是一个有意义的数。

**四、先在同名章节里找。** 重解析通常不改章节名。而一句话在两节里都出现过是
常态（书里前后呼应），按顺序扫会把后一节那条认到前一节去。同名优先不是提速，
是提准。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from booksoul_api.services import citations as cite


@dataclass(frozen=True)
class Stale:
    """一条待重定位的引文，连同它**当初**落在哪里。

    旧落点（`chapter_idx` / `para_index`）只用来两件事：同名章节优先，
    以及事后判断「这条到底动没动」。它不参与匹配——匹配只认引文原文。
    """

    citation_id: int
    quote: str
    # 当时那一节的标题快照。重解析通常不改章节名，所以它是个很强的线索。
    chapter_title: str = ""
    chapter_idx: int | None = None
    para_index: int = -1


@dataclass(frozen=True)
class Moved:
    """一条引文的新落点。`para_index == -1` 就是没找到。"""

    citation_id: int
    chapter_id: int | None
    # 找到了就是新那一节的标题；没找到就留着旧的那个快照——界面还能说
    # 「原《婆罗门之子》里这段，位置已变更」，而不是只剩一个灰按钮。
    chapter_title: str
    chapter_idx: int | None
    para_index: int

    @property
    def located(self) -> bool:
        return self.para_index >= 0


@dataclass(frozen=True)
class Report:
    """一次重定位的账。这一片的人工卡点看的就是这几个数。"""

    total: int
    # 找回来了，但落点和从前不是同一处（换了章，或者段号变了）
    shifted: int
    # 找回来了，落点和从前一模一样
    unchanged: int
    lost: int

    @property
    def located(self) -> int:
        return self.shifted + self.unchanged

    @property
    def rate(self) -> float:
        """命中率。没有引文要重定位时是 0.0，不是除零，也不是「100% 成功」。"""
        return self.located / self.total if self.total else 0.0

    def describe(self) -> str:
        """写成一句人话。`shifted` 必须出现在里面——见模块注释第三条。"""
        if not self.total:
            return "这本书还没有引文需要重定位。"
        return (
            f"{self.total} 条引文，找回 {self.located} 条（{self.rate:.0%}）："
            f"位置变了 {self.shifted} 条，原地不动 {self.unchanged} 条，"
            f"没找到 {self.lost} 条。"
        )


def relocate(
    stale: Sequence[Stale], sources: Sequence[cite.Source]
) -> tuple[list[Moved], Report]:
    """把一批旧引文放到新的一套章节里重新找一遍。

    `sources` 是**整本书**的新章节，不是某一轮载入的那几节：重定位不受
    「这一轮模型看了哪几节」的限制，它找的是这句话现在在书里的哪个位置。
    """
    out: list[Moved] = []
    shifted = unchanged = lost = 0

    for one in stale:
        hit = _find(one, sources)
        if hit is None:
            lost += 1
            out.append(
                Moved(
                    citation_id=one.citation_id,
                    chapter_id=None,
                    # 旧标题留着当线索，不清空
                    chapter_title=one.chapter_title,
                    chapter_idx=None,
                    para_index=-1,
                )
            )
            continue

        source, index = hit
        same = source.chapter_idx == one.chapter_idx and index == one.para_index
        if same:
            unchanged += 1
        else:
            shifted += 1
        out.append(
            Moved(
                citation_id=one.citation_id,
                chapter_id=source.chapter_id,
                chapter_title=source.title,
                chapter_idx=source.chapter_idx,
                para_index=index,
            )
        )

    return out, Report(
        total=len(stale), shifted=shifted, unchanged=unchanged, lost=lost
    )


def _find(one: Stale, sources: Sequence[cite.Source]) -> tuple[cite.Source, int] | None:
    """先在同名的那一节里找，找不到再翻整本书。

    两趟不是为了快。一句话在两节里都出现过时（书里前后呼应是常态），一趟扫
    会把它认到第一节去——而用户点过去看到的是一段他没引的话。
    """
    named = [s for s in sources if one.chapter_title and s.title == one.chapter_title]
    if named:
        hit = cite.locate(one.quote, named)
        if hit is not None:
            return hit
    return cite.locate(one.quote, sources)
