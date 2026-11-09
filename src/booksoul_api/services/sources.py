"""把章节的**原始文件**读出来切块，供引文比对用。

只有一件事，但它值得有自己的文件：**定位和重定位必须数同一份东西。**

S9 落库时按这一套切块定出 `para_index`；S12 重解析之后按同一套切块把旧引文
找回来。两处各写一份的话，「重定位命中率」这个数字测的就成了两套切法的差，
而不是重解析的影响——而那个数字是一条人工裁决的依据。切法一旦分叉也不会报错，
只会让高亮落到隔壁那一段上，而那一段看起来同样像是出处。

读**原始文件**而不是引擎载入后的 `Book`：引擎载入时剥掉了开头的标题行和那行
元信息，而阅读页渲的是原始文件。段号两边必须数同一份东西。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from booksoul_api.core.paths import under
from booksoul_api.services import citations as cite

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Ref:
    """一节正文在磁盘上的位置。

    两个调用方手里的东西不是同一个类型——提问那条路拿的是后台任务开跑前拍下的
    快照（`selector.ChapterIn` + 一张序号→路径的表），重解析那条路拿的是刚
    建出来的 ORM `Chapter`。让它们各自拼出 `Ref`，比在这里认两种类型清楚。
    """

    chapter_id: int
    idx: int
    title: str
    file_path: str


def read_sources(
    root: Path | str, refs: Iterable[Ref], *, only_idx: set[int] | None = None
) -> list[cite.Source]:
    """读这些章节的文件，切成可比对的块。

    `only_idx` 给了就只读那几节（提问时只认这一轮真载入的章节，见 S9 第二条
    纪律）；不给就是整本书——重定位不受「这一轮看了哪几节」限制，它找的是
    这句话现在在书里的哪个位置。

    读不出来的那一节**跳过，不抛**——和解析器那条「失败要局部化」同一个道理：
    一个坏掉的章节文件不该让整条回答失去溯源，也不该让整次重定位停摆。
    """
    base = Path(root)
    out: list[cite.Source] = []
    for ref in refs:
        if only_idx is not None and ref.idx not in only_idx:
            continue
        path = under(base, ref.file_path) if ref.file_path else None
        if path is None:
            logger.warning("章节 %s 的路径不可用，这一节不参与引文比对", ref.idx)
            continue
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            logger.warning("读不出章节 %s，这一节不参与引文比对", ref.idx)
            continue
        out.append(
            cite.Source(
                chapter_id=ref.chapter_id,
                chapter_idx=ref.idx,
                title=ref.title,
                paragraphs=cite.paragraphs(raw),
            )
        )
    return out
