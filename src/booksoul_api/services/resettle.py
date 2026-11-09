"""重解析之后的善后：把挂在旧章节上的东西重新安顿好（S12）。

重解析把 `chapters` 整张重建。两样东西因此悬空，而**两样都不会报错**：

**一、会话的选章锁定。** `conversations.selected_chapter_ids` 存的是旧的
chapter id。那几个 id 已经不存在了，下一轮按这份名单去选章会选中 0 节，
然后把一本没有正文的书送给模型——模型手里只剩一份目录，于是对着章节名臆测
内容，说得跟真的一样。这是 Grill C10 那条连锁的终点，也是这一片存在的理由。

**二、引文的落点。** `citations.chapter_id` 走 `ON DELETE SET NULL` 被置空，
`para_index` 指的是上一套切分里的第几块。不管的话，用户点「去原文」要么按钮
是死的，要么落到一段他没引的话前面。

## 次序只在 `swap()` 里出现一次

```
swapped = await swap(session, book_id=..., records=..., root=...)
```

里面三步，先后不是可选的：

1. **清掉会话的选章锁定**——为的是**锁的先后**。这个事务要碰会话行（清锁定）与章节行
   （删旧建新）；一轮提问收尾时（`turn_state` 的收尾事务）也要碰这两样，它先锁会话行、再
   引用章节。这里若先删章节、后清锁定，两边会各拿一把、互等另一把，库只能杀掉一边——
   杀到收尾那边，就是丢一条已付费的回答。同序之后只会排队。
2. **拍下引文当下的落点。** 旧章节一删，`ON DELETE SET NULL` 就把 `chapter_id` 清了，
   「这条引文原本在第几节」这个事实再也读不回来——而重定位报告要靠它分辨「找回来了
   但位置变了」和「压根没动过」。少了这个区分，一本正文一字未改的书会给出 100% 命中率，
   而那个数字什么都没证明。
3. **换章节，再按 `root` 下的新正文把引文找回来。**

这个次序原先由调用方记着：解析成功落库那里写一遍，测试里又抄了三份。锁序测试钉住的是
其中一份抄本——把生产代码里的两步对调，它照样是绿的。收进一个函数之后，
`tests/test_book_lifecycle.py::test_换章节之前已经握着会话行` 在库里看它。

## 成功的解析也要走一遍

不只是重解析。首次解析时这本书还没有会话、也没有引文，两条查询各自空手而归；
多加一个 `is_reparse` 分支只会多一条平时走不到、因而也没人验过的路。
不变量本来就是「章节刚被换掉 → 锁定与引文必须重新安顿」，两种情形都成立。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from booksoul_api.models.chapter import Chapter
from booksoul_api.repositories.book_repo import BookRepository
from booksoul_api.repositories.citation_repo import CitationRepository
from booksoul_api.services import relocate as reloc
from booksoul_api.services import sources as sources_mod
from booksoul_api.services import turn_state

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Swapped:
    """换完章节之后调用方还要用的两样。"""

    chapters: list[Chapter]  # 新行：存疑条目要挂 chapter_id
    relocation: reloc.Report  # 引文重定位那份账


async def swap(
    session: AsyncSession, *, book_id: int, records: list[dict], root: Path
) -> Swapped:
    """把一本书的章节换成 `records`，连同善后：清锁定、拍引文落点、换章节、找回引文。

    先会话行、后章节行（理由见模块注释）。`root` 是**新正文**所在的目录——重定位要读
    的是换上来的那一版。不提交：提交归调用方，它还要在同一个事务里写别的。
    """
    stale = await _prepare(session, book_id)
    chapters = await BookRepository(session).replace_chapters(book_id, records)
    relocation = await _relocate(session, book_id, chapters, stale, root)
    return Swapped(chapters=chapters, relocation=relocation)


async def _prepare(session: AsyncSession, book_id: int) -> list[reloc.Stale]:
    """换章节之前：清掉会话的选章锁定，并把引文连同**当下的落点**拍下来。"""
    cleared = await turn_state.forget_locks(session, book_id=book_id)
    if cleared:
        logger.info("重解析：清掉了 %s 个会话的选章锁定（书 %s）", cleared, book_id)

    rows = await CitationRepository(session).stale_for_book(book_id)
    return [
        reloc.Stale(
            citation_id=citation.id,
            quote=citation.quote,
            chapter_title=citation.chapter_title,
            chapter_idx=chapter_idx,
            para_index=citation.para_index,
        )
        for citation, chapter_idx in rows
    ]


async def _relocate(
    session: AsyncSession,
    book_id: int,
    chapters: Sequence[Chapter],
    stale: Sequence[reloc.Stale],
    root: Path,
) -> reloc.Report:
    """章节换完之后：把引文找回来。返回重定位那份账。"""
    if not stale:
        return reloc.Report(total=0, shifted=0, unchanged=0, lost=0)

    refs = [
        sources_mod.Ref(
            chapter_id=row.id, idx=row.idx, title=row.title, file_path=row.file_path
        )
        for row in chapters
    ]
    # 读磁盘是阻塞的，而这段跑在解析任务的事件循环里
    sources = await asyncio.to_thread(sources_mod.read_sources, root, refs)

    moved, report = reloc.relocate(stale, sources)
    await CitationRepository(session).apply_moves(
        [(m.citation_id, m.chapter_id, m.chapter_title, m.para_index) for m in moved]
    )

    logger.info("重解析：%s（书 %s）", report.describe(), book_id)
    return report
