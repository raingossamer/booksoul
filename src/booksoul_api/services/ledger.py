"""解析账本：一次解析到底做了什么，逐条可核对（界面稿②屏）。

那十几个中文键只在这里。库里 `parse_jobs.cleanup_stats` 的存储格式不变——前端导入页也读
其中几个（章节、字数、脚注、图片）——但写它、读它、把它说成人话的只有这一个类型。原先它是
一个中文键的字典，横跨引擎、importer、解析落库、书库四处：少一个键那一行就静默消失（这是
设计），于是**改一个键名不会报任何错**。

两条规矩，也只写在这里：

* 只放真的数出来的东西。解析器没报的项（html2md 的站内链接转换、xmlutil 的命名空间剥离等）
  **不出现**，而不是填 0——填 0 会让人以为「一处都没动」，那是假的。
* 只有真有引文搬家时才记那一笔（`moved()`）。首次解析一条引文都没有，记一行「0 条」会让
  账本多出一件没发生过的事。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace

from booksoul_api.models.book import Book
from booksoul_api.schemas.book import LedgerRow
from booksoul_api.services import relocate as reloc

# 存进库里的键。改名要连前端导入页一起改（它读章节、字数、脚注、图片）
_FORMAT = "格式"
_CHAPTERS = "章节"
_WORDS = "字数"
_IMAGES = "图片"
_FOOTNOTES = "脚注"
_META_HIT = "元信息命中"
_META_FIELDS = "元信息字段数"
_DEDUPED = "去重的重复标题"
_MOVED_TOTAL = "引文总数"
_MOVED_LOCATED = "引文找回"
_MOVED_SHIFTED = "引文位置变了"
_MOVED_LOST = "引文丢失"
_OWN = {
    _FORMAT,
    _CHAPTERS,
    _WORDS,
    _IMAGES,
    _FOOTNOTES,
    _META_HIT,
    _META_FIELDS,
    _DEDUPED,
    _MOVED_TOTAL,
    _MOVED_LOCATED,
    _MOVED_SHIFTED,
    _MOVED_LOST,
}

# 解析器自己记的那几笔（原样放在 `engine` 里），说成人话时读其中这三个
_TOC_SOURCE = "目录来源"
_SPINE_FILES = "spine 文件数"
_DROPPED_TOC = "剔除的目录页"


@dataclass(frozen=True)
class Ledger:
    source_format: str = ""
    chapters: int = 0
    words: int = 0
    images: int = 0
    footnotes: int = 0
    meta_hit: int = 0
    meta_fields: int = 0
    deduped_headings: int = 0
    # 解析器自己记下的那几笔（目录来源、spine 文件数、剔除的目录页……），原样透传
    engine: Mapping[str, object] = field(default_factory=dict)
    # 重解析时引文搬家的账；只在真有引文要搬家时才有，见 `moved()`
    relocation: reloc.Report | None = None

    def moved(self, report: reloc.Report) -> Ledger:
        """记上这次换章节对旧引文做了什么——只有真有引文要搬家时才记。"""
        return replace(self, relocation=report) if report.total else self

    def to_json(self) -> dict[str, object]:
        """写进 `parse_jobs.cleanup_stats` 的样子。"""
        out: dict[str, object] = {
            _FORMAT: self.source_format,
            _CHAPTERS: self.chapters,
            _WORDS: self.words,
            _IMAGES: self.images,
            _FOOTNOTES: self.footnotes,
            _META_HIT: self.meta_hit,
            _META_FIELDS: self.meta_fields,
        }
        if self.deduped_headings:
            out[_DEDUPED] = self.deduped_headings
        out.update(self.engine)
        if self.relocation is not None:
            out |= {
                _MOVED_TOTAL: self.relocation.total,
                _MOVED_LOCATED: self.relocation.located,
                _MOVED_SHIFTED: self.relocation.shifted,
                _MOVED_LOST: self.relocation.lost,
            }
        return out

    @classmethod
    def from_json(cls, raw: Mapping[str, object] | None) -> Ledger:
        """从库里读回来。旧行照读：少了的那几项，就当作没数出来。"""
        raw = raw or {}
        relocation = None
        if total := _int(raw.get(_MOVED_TOTAL)):
            located = _int(raw.get(_MOVED_LOCATED))
            shifted = _int(raw.get(_MOVED_SHIFTED))
            relocation = reloc.Report(
                total=total,
                shifted=shifted,
                unchanged=located - shifted,
                lost=_int(raw.get(_MOVED_LOST)),
            )
        return cls(
            source_format=str(raw.get(_FORMAT) or ""),
            chapters=_int(raw.get(_CHAPTERS)),
            words=_int(raw.get(_WORDS)),
            images=_int(raw.get(_IMAGES)),
            footnotes=_int(raw.get(_FOOTNOTES)),
            meta_hit=_int(raw.get(_META_HIT)),
            meta_fields=_int(raw.get(_META_FIELDS)),
            deduped_headings=_int(raw.get(_DEDUPED)),
            engine={key: value for key, value in raw.items() if key not in _OWN},
            relocation=relocation,
        )

    def rows(self, *, book: Book) -> list[LedgerRow]:
        """说成②屏那两段人话。**只有真的数出来的项才会出现。**"""
        rows: list[LedgerRow] = []

        fmt = (self.source_format or book.source_format or "").upper()
        if fmt:
            rows.append(
                LedgerRow(label="格式", detail=f"{fmt} —— 按文件魔数判定，不看扩展名")
            )

        source = self.engine.get(_TOC_SOURCE)
        if source:
            pieces = [str(source)]
            if self.engine.get(_SPINE_FILES):
                pieces.append(f"spine {self.engine[_SPINE_FILES]} 个文件")
            pieces.append(f"切出 {self.chapters or book.chapter_count} 节")
            rows.append(LedgerRow(label="目录来源", detail=" · ".join(pieces)))

        if self.meta_fields:
            rows.append(
                LedgerRow(
                    label="元信息",
                    detail="书名 · 作者 · 出版社 · 出版日 · ISBN · 语言 命中 "
                    f"{self.meta_hit}/{self.meta_fields}",
                )
            )

        if book.contributor and not book.translator:
            rows.append(
                LedgerRow(
                    label="贡献者",
                    detail=f"{book.contributor} —— 来源没标角色，"
                    "记为「贡献者」而不是替你猜成译者",
                )
            )

        if self.engine.get(_DROPPED_TOC):
            rows.append(
                LedgerRow(
                    label="目录页",
                    detail=f"从正文剔除 {self.engine[_DROPPED_TOC]} 个文件"
                    "（它混在 spine 里，且与我们自己写的 INDEX.md 重复）",
                )
            )

        if self.relocation is not None:
            moved = self.relocation
            # 「位置变了几条」要摆出来：一本正文一字未改的书会给出 100% 找回，
            # 而那个数字什么都没证明——它连一次真正的重定位都没做过。
            detail = (
                f"{moved.total} 条旧引文，找回 {moved.located} 条"
                f"（其中 {moved.shifted} 条位置变了）"
            )
            if moved.lost:
                detail += f"；{moved.lost} 条在新正文里找不到，已降级为「原位置已变更」，没有猜"
            rows.append(LedgerRow(label="引文重定位", detail=detail))

        if self.deduped_headings:
            rows.append(
                LedgerRow(
                    label="重复标题",
                    detail=f"{self.deduped_headings} 节的正文开头与标题重复，已去掉那一行",
                )
            )
        if self.footnotes:
            rows.append(
                LedgerRow(
                    label="脚注", detail=f"{self.footnotes} 条接回标准 Markdown 脚注"
                )
            )
        if self.images:
            rows.append(
                LedgerRow(label="图片", detail=f"导出 {self.images} 张到 images/")
            )

        return rows


def _int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
