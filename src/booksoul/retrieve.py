"""书装不下时，挑哪几章送进上下文。

现在的模型上下文动辄百万 token，一本普通的书整本塞进去是最优解——前后呼应全在，
不会因为检索漏掉关键段落。所以这里的第一原则是：**能整本就整本**。

只有大部头（全集、辞书、几百万字的长篇）才需要挑。挑的办法用 BM25，中文按
一元+二元字组切，英文按词切，不引 jieba 之类的分词依赖——书籍检索的粒度用字组
足够，而且对人名、地名、生造词更鲁棒。

已知局限：纯词面检索对两类问题无能为力——一是问法和原文用词完全不同
（“做生意堕落” vs 原文的具体叙述），二是某个词本就贯穿全书、不具备章节
区分度（《悉达多》里的“唵”）。需要更好的召回时，正确做法是把预算调大让整本
进上下文，而不是在这里堆启发式规则。

**挑的过程要留得下账。** `select_chapters()` 只回答「挑了哪几章」，
`plan_selection()` 连「每一章几分、命中了什么词、为什么没选它、差多少字装不下」
一起给出来。界面上那张排名表读的就是后者——而两者必须是同一段代码算出来的：
一旦在别处照着重算一遍，「界面说读了 5 节、模型实际拿到 4 节」的那天就会到。
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

from .model import Chapter

# 中文里过于常见、区分度接近零的字
_STOP = set("的了是在和与也就都而及或被把让使对于之其此这那有无不为以所它他她们个")

_LATIN = re.compile(r"[A-Za-z][A-Za-z'\-]*")
_CJK_RUN = re.compile(r"[一-鿿㐀-䶿]+")

# 标题在文档表示里重复的次数
_TITLE_BOOST = 8


def tokenize(text: str) -> list[str]:
    """中文切一元 + 二元字组，西文按词切。

    二元组负责精度（"佛陀"比"佛"准），一元组负责召回，两者一起用比只用二元组稳。

    注意二元组是滑窗切的，必然会切出跨词边界的垃圾组合——"悉达多为什么"会切出
    "多什"。这类噪声在 BM25 里很危险：它恰好在全书只出现一次时 idf 极高，一个
    字符相邻的巧合就能主导排序。所以 BM25.score 里会把全书只出现一次的查询词
    丢掉（见那里的说明）。
    """
    tokens = [w.lower() for w in _LATIN.findall(text) if len(w) > 1]
    for run in _CJK_RUN.findall(text):
        run = "".join(ch for ch in run if ch not in _STOP)
        tokens.extend(run)
        tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens


class BM25:
    def __init__(
        self, docs: list[list[str]], *, k1: float = 1.5, b: float = 0.75
    ) -> None:
        self.k1, self.b = k1, b
        self.docs = docs
        self.n = len(docs) or 1
        self.freqs = [Counter(d) for d in docs]
        self.lengths = [len(d) or 1 for d in docs]
        self.avg_len = sum(self.lengths) / self.n

        df: Counter[str] = Counter()
        self.total_freq: Counter[str] = Counter()
        for freq in self.freqs:
            df.update(freq.keys())
            self.total_freq.update(freq)
        self.idf = {
            term: math.log(1 + (self.n - n + 0.5) / (n + 0.5)) for term, n in df.items()
        }

    def terms_for(self, query: list[str]) -> list[str]:
        """真正参与打分的查询词。

        全书只出现一次的查询词一律丢掉。滑窗切出来的跨词边界组合（"多什"、
        "么离"）几乎都是这种孤例，而它们的 idf 恰恰最高，留着会让一次字符
        相邻的巧合压过真正的实词。真实的人名、地名、术语在一本书里必定反复出现。

        单独开出来是因为界面要如实写出「这次是按哪些词排的」。那一列若另算一遍，
        用户看到的词和真正参与打分的词就是两回事，而这张表存在的全部意义正是
        「回答基于什么，可查」。
        """
        terms = [t for t in query if self.total_freq.get(t, 0) > 1]
        # 查询词全被滤掉了（极短或全是生词），退回原始查询
        return terms or list(query)

    def weight(self, term: str, count: int, doc: int) -> float:
        """一个词在某一节里贡献了多少分。

        单开一个方法，是因为界面上「命中的词」那一列要按**贡献**排，不能按次数排。
        次数最多的词恰恰常常是最没用的那个：《悉达多》里「悉达」出现在每一节，
        idf 接近 0，对分数几乎没有影响——按次数排，十六行会显示同样三个词。
        那一列要回答的是「为什么是这一节」。

        排序和打分必须用同一个式子，否则界面会把一个没起作用的词摆在第一位。
        """
        if not count:
            return 0.0
        norm = self.k1 * (1 - self.b + self.b * self.lengths[doc] / self.avg_len)
        return self.idf.get(term, 0.0) * count * (self.k1 + 1) / (count + norm)

    def score(self, query: list[str]) -> list[float]:
        terms = self.terms_for(query)
        return [
            sum(self.weight(term, freq.get(term, 0), i) for term in terms)
            for i, freq in enumerate(self.freqs)
        ]


@dataclass
class Scored:
    """一节书在这次选章里的账面。全书每一节都有一行，包括没选中的。

    没选中的也留着，是因为用户要能看到「差 504 字装不下」这种事——
    只列选中的，那张表就只能证明「选了什么」，证明不了「凭什么没选别的」。
    """

    chapter: Chapter
    rank: int  # 按分数降序的名次，1 起
    score: float
    # 命中的查询词与它在**正文**里出现的次数，按次数降序。
    # 刻意不含标题加权的那 8 份：那是打分用的权重，不是「这个词出现了几次」。
    matched: list[tuple[str, int]] = field(default_factory=list)
    selected: bool = False
    # 按名次累加到这一节为止用掉的字数；没选中的为 0（界面上那一列显示破折号）
    cumulative_chars: int = 0

    @property
    def chars(self) -> int:
        return len(self.chapter.content)


@dataclass
class Plan:
    """一次选章的完整决策。"""

    chapters: list[Chapter]  # 选中的，按原书顺序
    whole_book: bool
    budget_chars: int
    used_chars: int
    terms: list[str]  # 真正参与打分的查询词
    items: list[Scored]  # 全书每一节各一行，按名次升序


def plan_selection(
    chapters: list[Chapter],
    question: str,
    *,
    budget_chars: int,
    always_include: int = 0,
) -> Plan:
    """挑出预算内最相关的章节，并把挑的过程一起交出来。"""
    total = sum(len(c.content) for c in chapters)
    if total <= budget_chars:
        # 整本都放得下，**连分词都不做**。挑是为了塞得下，塞得下就没有挑的必要；
        # 一本几百万字的书光切词就是好几秒，而这一支的结论和切不切词无关。
        # 代价是这时的 rank 只是原书顺序、score 恒为 0 —— 界面必须照实说
        # 「没跑 BM25」，不要把一列 0.00 摆成排名。
        return Plan(
            chapters=list(chapters),
            whole_book=True,
            budget_chars=budget_chars,
            used_chars=total,
            terms=[],
            items=[
                Scored(
                    chapter=c,
                    rank=n,
                    score=0.0,
                    selected=True,
                    cumulative_chars=sum(len(x.content) for x in chapters[:n]),
                )
                for n, c in enumerate(chapters, 1)
            ],
        )

    # 标题重复计入，等于给标题字段加权（信息检索里的 field boost）。
    # 章节标题往往是这一章的题眼——《船夫》《唵》——但它作为词又散落全书各处，
    # 不加权的话正文里的零星出现会把真正讲它的那一章淹掉。
    bodies = [tokenize(c.content) for c in chapters]
    docs = [
        tokenize(c.title) * _TITLE_BOOST + body
        for c, body in zip(chapters, bodies, strict=True)
    ]
    engine = BM25(docs)
    query = tokenize(question)
    terms = engine.terms_for(query)
    scores = engine.score(query)

    # 问题里直接点了章节名，那几乎就是明示，给一个压倒性加成
    q = question.lower()
    for i, c in enumerate(chapters):
        title = c.title.strip()
        if not title or title.lower() not in q:
            continue
        # 单字标题（《唵》）同样是强信号，但得先确认这个字本身不常见，
        # 否则《一》《人》这类标题会被几乎每个问题蹭到
        if len(title) == 1:
            spread = sum(1 for other in chapters if title in other.content)
            if spread > len(chapters) / 3:
                continue
        scores[i] += 10.0

    order = sorted(range(len(chapters)), key=lambda i: scores[i], reverse=True)

    # 开头几章无条件保留——但**只在装得下的前提下**。"无条件"从来不该意味着
    # "保留到把预算撑爆"：预算小到连序言都放不下时，硬塞序言的后果是模型拿到
    # 一页扉页、而用户问的那一章一个字都没进去，并且全程没有任何报错。
    picked: set[int] = set()
    used = 0
    for i in range(min(always_include, len(chapters))):
        size = len(chapters[i].content)
        if used + size > budget_chars:
            break
        picked.add(i)
        used += size

    for i in order:
        if i in picked:
            continue
        size = len(chapters[i].content)
        if used + size > budget_chars:
            continue
        picked.add(i)
        used += size

    if not picked:  # 预算连一章都放不下，至少给最相关的那章
        picked = {order[0]}
        used = len(chapters[order[0]].content)

    wanted = set(terms)
    running = 0
    items = []
    for rank, i in enumerate(order, 1):
        selected = i in picked
        if selected:
            running += len(chapters[i].content)
        counts = Counter(t for t in bodies[i] if t in wanted)
        items.append(
            Scored(
                chapter=chapters[i],
                rank=rank,
                score=scores[i],
                # 按**贡献**排，不按次数排。见 `BM25.weight` 上那段说明：
                # 次数最多的词常常正是对分数毫无影响的那个。
                matched=sorted(
                    counts.items(),
                    key=lambda kv, d=i: (-engine.weight(kv[0], kv[1], d), kv[0]),
                ),
                selected=selected,
                cumulative_chars=running if selected else 0,
            )
        )

    return Plan(
        chapters=[chapters[i] for i in sorted(picked)],
        whole_book=False,
        budget_chars=budget_chars,
        used_chars=used,
        terms=terms,
        items=items,
    )


def select_chapters(
    chapters: list[Chapter],
    question: str,
    *,
    budget_chars: int,
    always_include: int = 0,
) -> tuple[list[Chapter], bool]:
    """挑出预算内最相关的章节，按原书顺序返回。

    返回 (选中的章节, 是否整本都放下了)。
    always_include 指定开头多少章无条件保留（序、前言常含全书线索）。

    这是 `plan_selection()` 的简版出口：只要结论、不要账本的调用方走这条。
    """
    plan = plan_selection(
        chapters, question, budget_chars=budget_chars, always_include=always_include
    )
    return plan.chapters, plan.whole_book
