"""选章：这次把哪几节书交给模型，以及凭什么（界面稿⑥屏）。

这是本片的深模块。它的接口只有一个 `choose()`，藏着四条分叉；
**它不碰数据库，也不碰模型 API**——进来的是章节、问题、预算、已锁定集合，
出去的是一份决策加一本账。能这样测，是把最难想清楚的那部分放进这里的全部理由。

四条分叉，走错哪一条都不会报错，只会悄悄改变模型手里有什么：

**一、整本装得下 → 不挑。** 现在的窗口动辄百万 token，一本普通的书整本塞进去
就是最优解，前后呼应全在。这一支连分词都不做：几百万字的书光切词就是好几秒，
而结论和切不切词无关。

**二、装不下 → 跑 BM25 挑。** 算法在引擎的 `retrieve.plan_selection()` 里，
这里一行都不重写。界面上那张排名表和模型真正拿到的那几节必须是同一次计算的
两个面：一旦在这里照着重算一遍，「界面说读了 5 节、模型实际拿到 4 节」的那天
就一定会来，而且没有任何报错。

**三、已经挑过 → 原样复用。** 每轮重挑一次会让前缀缓存全废，那是直接写在用户
账单上的。锁定存的是 `chapters.id`，而引擎的 `load_from_path(only_idx=...)` 收的
是 `chapters.idx`——这两个数在这条路上极易混淆，所以 `Choice` 两个都给，
调用方不必自己翻译。

**四、问到了没载入的那几节 → 说破，别硬答。** 这是逃生门。锁定意味着模型手里
是一本残缺的书；用户问到了缺的那部分，而系统一声不吭照答，模型就会对着目录名
编——那正是这个产品最不该发生的事。重选要重置缓存（这一轮更贵），所以不自动
重选，交给用户点头。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from booksoul import retrieve
from booksoul.model import Chapter as EngineChapter

# 四种结论。前两种产生了新的选章决策（要留档），后两种没有。
WHOLE_BOOK = "whole_book"
BM25 = "bm25"
LOCKED = "locked"
RELOCK_NEEDED = "relock_needed"

# 逃生门只认两个字以上的标题。单字标题（《唵》《序》）作为词散落全书各处，
# 拿它去匹配问题几乎每次都中，用户会被一串莫名其妙的「要不要重选」拦住。
# 宁可漏判也不误判：漏判只是少一次提示，误判是逼用户多花一次钱。
_MIN_TITLE = 2

# 一节最多留几个命中词。留档不是全量转录：一个问题切出几十个字组，
# 全写进去只会把真正能看的词挤到看不见的地方。
_MAX_TERMS = 8

# 一个词要被写成「为什么是这一节」的理由，至少得在这一节里出现这么多次。
#
# 这是**展示规则，不是打分规则**——分数那一列完全不受它影响。它挡的是滑窗
# 切出来的跨词碎片：问「悉达多为什么拒绝追随佛陀」会切出「随佛」，它来自
# 「追随佛陀」的中间，不是一个词；全书只出现两次，idf 高得离谱，按贡献排会
# 冲到第一位。于是那一列的头条写着「随佛×2」，而用户完全看不懂那是什么。
#
# 另一头也踩过：按出现次数排，十六行全是「悉达×46 达多×46」——书名里的字
# 在每一节都有，idf 接近 0，对排名毫无影响，却次次排第一。
# 两头都试过，所以这里是「按贡献排 + 出现太少的不算理由」。
_MIN_HITS = 3


@dataclass(frozen=True)
class ChapterIn:
    """选章眼里的一节书。

    `id` 给数据库（锁定集合存的是它），`idx` 给引擎（`only_idx` 收的是它）。
    两个都要，是因为混用它们不会报错——只会读到错位的章节，或者一节都读不到。

    `content` 为空表示这次没把正文读进来。锁定复用那条路本来就不需要读盘，
    所以它允许为空；要挑章节则必须有正文，没有正文无从打分。

    `file_path` 只是**被携带**的：选章一个字都不读它。它在这里是因为溯源要用，
    而不带的话调用方就得另外拎一张 `{idx: file_path}` 的旁表跟着走，
    两个结构在读正文那一刻再缝回去——缝错了不报错，只是读到错位的那一节。
    它不得进入打分：让它进去，「模型读哪几节」就悄悄取决于磁盘上的文件名。

    指的是**原始文件**（`chapters/NNN-标题.md`），不是引擎载入后的那份：
    引擎会把开头的标题行与那行元信息剥掉，而阅读页渲的是原始文件，
    段号两边必须数同一份东西，否则溯源的高亮会落在隔壁那一段上。
    """

    id: int
    idx: int
    title: str
    word_count: int
    content: str = ""
    file_path: str = ""

    @property
    def chars(self) -> int:
        """算预算用的字数。

        有正文时以正文长度为准：预算算的是**真正送出去的那些字**，
        而 `word_count` 是解析时统计的，两者可能因为清洗规则变化而不一致。
        """
        return len(self.content) if self.content else self.word_count


@dataclass(frozen=True)
class Item:
    """排名表里的一行。全书每一节都有一行，包括没选中的。"""

    chapter_id: int
    chapter_idx: int
    chapter_title: str
    rank: int
    score: float
    word_count: int
    selected: bool
    # 按名次累加到这一节为止用掉的字数；没选中的为 0（界面上显示破折号）
    cumulative_chars: int
    matched_terms: list[tuple[str, int]] = field(default_factory=list)


@dataclass(frozen=True)
class Shortfall:
    """预算线上那句话：下一节要 X 字，只剩 Y，差 Z 字装不下。"""

    title: str
    need: int
    left: int
    short: int


@dataclass(frozen=True)
class Choice:
    reason: str
    # 锁定集合用的 chapters.id，按原书顺序
    selected_ids: list[int]
    # 引擎 load_from_path(only_idx=) 用的 chapters.idx
    selected_idx: set[int]
    whole_book: bool
    budget_chars: int
    used_chars: int
    total_count: int
    query_terms: list[str] = field(default_factory=list)
    items: list[Item] = field(default_factory=list)
    # 只在 RELOCK_NEEDED 时非空：问题里点到名、这次却没载入的章节标题
    mentioned: list[str] = field(default_factory=list)

    @property
    def records(self) -> bool:
        """这一轮是不是产生了一个新的选章决策。

        只有产生了才往 `retrievals` 写一行。复用锁定的那些轮次什么都不写——
        它们没有做决定，替它们编一行出来，那张表就不再是「决策留档」，
        而是一份每轮复制粘贴的流水。
        """
        return self.reason in (WHOLE_BOOK, BM25)

    @property
    def shortfall(self) -> Shortfall | None:
        """名次最高的那个落选者差多少字。整本装得下时没有这回事。"""
        return shortfall_of(
            whole_book=self.whole_book,
            budget_chars=self.budget_chars,
            used_chars=self.used_chars,
            items=self.items,
        )


def shortfall_of(
    *, whole_book: bool, budget_chars: int, used_chars: int, items: Iterable[Any]
) -> Shortfall | None:
    """预算线上那句话：名次最高的那个落选者差多少字。整本装得下就没有这回事。

    两个调用方手里的「一节」是两种类型：挑章节当场算时是 `Item`，⑥屏从库里
    读回来时是 `retrieval_items` 那一行。要用的三个字段（`selected` /
    `chapter_title` / `word_count`）两边同名，所以这里只认字段不认类型。

    为什么非要共用一份：两边各留一份的话，**⑥屏显示的那个数字和测试断言的
    那个数字可以是两次不同的计算** —— 而它们长得一模一样，改错一边不会报错。
    """
    if whole_book:
        return None
    nxt = next((item for item in items if not item.selected), None)
    if nxt is None:
        return None
    left = max(0, budget_chars - used_chars)
    return Shortfall(
        title=nxt.chapter_title,
        need=nxt.word_count,
        left=left,
        short=max(0, nxt.word_count - left),
    )


def choose(
    *,
    chapters: Sequence[ChapterIn],
    question: str,
    budget_chars: int,
    locked_ids: Iterable[int] = (),
    reselect: bool = False,
    always_include: int = 1,
) -> Choice:
    """决定这次把哪几节交给模型。

    `locked_ids` 是会话第一轮锁下来的集合；`reselect=True` 表示用户已经点头
    重挑一次（他知道这一轮更贵）。
    """
    known = {c.id for c in chapters}
    # 重解析会重建 chapters，老的 id 全都作废。这时候要重挑，不是读零节——
    # 读零节的表现是「模型手里没书，然后开始瞎编」，而且一路上没有任何报错。
    locked = [i for i in locked_ids if i in known]

    if locked and not reselect:
        return _reuse(chapters, question, budget_chars, locked)
    return _fresh(chapters, question, budget_chars, always_include)


# --------------------------------------------------------------------- 复用


def _reuse(
    chapters: Sequence[ChapterIn],
    question: str,
    budget_chars: int,
    locked: list[int],
) -> Choice:
    picked = [c for c in chapters if c.id in set(locked)]
    mentioned = unloaded_titles_in(question, chapters=chapters, selected_ids=locked)
    return Choice(
        reason=RELOCK_NEEDED if mentioned else LOCKED,
        selected_ids=[c.id for c in picked],
        selected_idx={c.idx for c in picked},
        whole_book=False,
        budget_chars=budget_chars,
        used_chars=sum(c.chars for c in picked),
        total_count=len(chapters),
        mentioned=mentioned,
    )


def unloaded_titles_in(
    question: str, *, chapters: Sequence[ChapterIn], selected_ids: Iterable[int]
) -> list[str]:
    """问题里点了名、这次却没载入的章节标题。逃生门就靠它。"""
    loaded = set(selected_ids)
    hits = []
    for c in chapters:
        title = c.title.strip()
        if c.id in loaded or len(title) < _MIN_TITLE:
            continue
        if title in question and title not in hits:
            hits.append(title)
    return hits


# --------------------------------------------------------------------- 新挑


def _fresh(
    chapters: Sequence[ChapterIn],
    question: str,
    budget_chars: int,
    always_include: int,
) -> Choice:
    # 引擎收的是它自己的 Chapter。`order` 在引擎里是 0 起的，回来时
    # `order + 1` 就是 idx —— 这一层翻译只在这里发生一次。
    engine_chapters = [
        EngineChapter(title=c.title, content=c.content, order=c.idx - 1)
        for c in chapters
    ]
    plan = retrieve.plan_selection(
        engine_chapters,
        question,
        budget_chars=budget_chars,
        always_include=always_include,
    )

    by_idx = {c.idx: c for c in chapters}
    picked = [by_idx[c.order + 1] for c in plan.chapters]
    return Choice(
        reason=WHOLE_BOOK if plan.whole_book else BM25,
        selected_ids=[c.id for c in picked],
        selected_idx={c.idx for c in picked},
        whole_book=plan.whole_book,
        budget_chars=budget_chars,
        used_chars=plan.used_chars,
        total_count=len(chapters),
        query_terms=plan.terms,
        items=[_item(by_idx[s.chapter.order + 1], s) for s in plan.items],
    )


def _item(source: ChapterIn, scored: retrieve.Scored) -> Item:
    return Item(
        chapter_id=source.id,
        chapter_idx=source.idx,
        chapter_title=source.title,
        rank=scored.rank,
        score=round(scored.score, 4),
        word_count=scored.chars or source.word_count,
        selected=scored.selected,
        cumulative_chars=scored.cumulative_chars,
        matched_terms=_explain(scored.matched),
    )


def _explain(matched: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """挑出能当「为什么是这一节」讲的那几个词。

    进来的已经按对分数的贡献排好了（`BM25.weight`）。这里只做一件事：
    把出现太少的踢掉——它们多半是跨词碎片，而碎片恰恰排在最前面。

    全踢光了就留贡献最高的那一个：一列空白说不了任何话，而「这一节是靠一个
    只出现两次的词进来的」本身就是用户该看到的事。
    """
    kept = [t for t in matched if t[1] >= _MIN_HITS]
    if not kept and matched:
        kept = matched[:1]
    return kept[:_MAX_TERMS]
