"""溯源：一条回答引了哪一章哪一段（界面稿⑤屏上半、⑦屏答栏）。

这组 schema 的形状由一句话决定：**对不上就说对不上。**

所以 `located` 是一个显式的布尔，而不是让前端去判断 `paraIndex >= 0`——
那种判断迟早有人写成 `paraIndex > 0`，于是第 0 段的引文全部悄悄变成「找不到」。

`chapterIdx` 为 null 有两种情形，界面要说的是**同一句话的两种程度**：

* `chapterId` 也为 null —— 重解析把章节重建了，这条引文的原位置已变更。
  `chapterTitle` 是当时的快照，界面还能说出「原《婆罗门之子》里这段」。
* `located` 为 false —— 这句话当初就没在模型手里那几节里找到。
  这不是系统出错，是**模型引了一句原文里没有的话**，界面要照直说。
"""

from __future__ import annotations

from datetime import datetime

from booksoul_api.schemas import ApiModel


class CitationOut(ApiModel):
    id: int
    message_id: int
    quote: str
    # -1 = 没定位到。前端一律看 `located`，不要自己拿它跟 0 比。
    para_index: int
    located: bool
    # true = 被 S12 的 CitationRelocator 重定位过（重解析之后重新找回来的）。
    # 界面据此区分「当时就定位到的」和「后来找回来的」——后者值得提醒一句。
    relocated: bool

    chapter_id: int | None
    # 阅读页的路由要它（`/books/:id/read/:idx`）。重解析之后为 null，
    # 那时只剩 chapterTitle 这个快照，链接给不出来。
    chapter_idx: int | None
    chapter_title: str

    # 阅读页那条来路条要的：是哪本书、哪个会话、哪个问题把人带过来的
    book_id: int
    conversation_id: int
    question: str
    created_at: datetime
