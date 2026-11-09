"""选章明细：这次回答读了什么（界面稿⑥屏）。

这组 schema 的用处是让一句界面文案变得可查。「读了 5/16 节」如果没有下面这张
表撑着，它和「模型说它读了」是同一个可信度——而这个产品卖的正好就是那点可信度。

两个数要特别小心，它们看起来都像「字数」：

* `word_count` 是这一节有多少**字符**——标点、换行、Markdown 标记都算，
  因为它们真的占上下文。它比 `chapters.word_count`（解析时统计的正文字数）
  略大，那是两件事，界面上要写「字符」而不是「字数」；
* `cumulative_chars` 是按名次累加到这一节为止用掉了多少字，只有选中的才有。

累计这一列**不在库里**，是服务端按名次现算的。放到前端去加，那条预算线就有了
第二个算法，而两个算法迟早会在某个边界上分岔。
"""

from __future__ import annotations

from datetime import datetime

from booksoul_api.schemas import ApiModel


class MatchedTerm(ApiModel):
    term: str
    count: int


class RetrievalItemOut(ApiModel):
    """排名表里的一行。没选中的也在，而且带着分数与字数。"""

    # 重解析之后会变成 null（章节被重建了）。下面两列是当时的快照，
    # 就是为了在那之后这一行仍然读得出「当时读的是哪一节」。
    chapter_id: int | None
    chapter_idx: int
    chapter_title: str
    rank: int
    score: float
    word_count: int
    selected: bool
    cumulative_chars: int
    matched_terms: list[MatchedTerm] = []


class Shortfall(ApiModel):
    """预算线上那句话：下一节要 X 字，只剩 Y，差 Z 字装不下。"""

    title: str
    need: int
    left: int
    short: int


class RetrievalBrief(ApiModel):
    """⑦屏底下那条仪表要的几个数。够它写「读了 5/16 节」，不够画整张表。"""

    turn: int
    selected_count: int
    total_count: int
    whole_book: bool
    budget_chars: int
    used_chars: int


class RetrievalOut(ApiModel):
    conversation_id: int
    turn: int
    # 做这个决定时问的那一句。后续轮次复用这份锁定，所以它未必是最近一个问题——
    # 界面要照实说是哪句话挑的章，不然用户会以为排名是刚才那个问题算出来的。
    question: str
    book_title: str
    model: str
    budget_chars: int
    used_chars: int
    selected_count: int
    total_count: int
    # true = 整本都给了模型，**没跑 BM25**。这时 score 那一列恒为 0，
    # 界面必须说破，不能把一列 0.00 摆成排名。
    whole_book: bool
    query_terms: list[str] = []
    items: list[RetrievalItemOut] = []
    shortfall: Shortfall | None = None
    created_at: datetime
