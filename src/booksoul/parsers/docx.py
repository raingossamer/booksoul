"""DOCX 解析。

Word 文档的结构信息在段落样式里：`Heading 1`（英文版）或 `标题 1`（中文版）。
比正则猜标题可靠得多，所以优先用样式；样式缺失时才退回文本推断。
"""

from __future__ import annotations

import os
import re

from .. import clean, structure
from ..model import Book, BookMeta, Chapter

_HEADING_RE = re.compile(r"^(?:heading|标题|題)\s*(\d)", re.IGNORECASE)


def _heading_level(style_name: str) -> int:
    """样式名 -> 标题层级；不是标题返回 0。"""
    if not style_name:
        return 0
    m = _HEADING_RE.match(style_name.strip())
    if m:
        return min(int(m.group(1)), 6)
    if style_name.strip().lower() in ("title", "标题", "书名"):
        return 1
    return 0


def _para_markdown(para) -> str:
    """段落转 Markdown，保留加粗/斜体。"""
    out = []
    for run in para.runs:
        text = run.text
        if not text:
            continue
        stripped = text.strip()
        if not stripped:
            out.append(text)
            continue
        lead = text[: len(text) - len(text.lstrip())]
        trail = text[len(text.rstrip()) :]
        body = stripped.replace("*", r"\*").replace("_", r"\_")
        if run.bold:
            body = f"**{body}**"
        if run.italic:
            body = f"*{body}*"
        out.append(lead + body + trail)
    return "".join(out).strip()


def _table_markdown(table) -> str:
    rows = []
    for row in table.rows:
        cells = [
            re.sub(r"\s+", " ", cell.text).strip().replace("|", r"\|")
            for cell in row.cells
        ]
        if any(cells):
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    head, *body = rows
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(lines)


def _iter_blocks(doc):
    """按文档里的真实顺序交替产出段落和表格（python-docx 默认做不到）。"""
    from docx.document import Document as _Doc
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    parent = doc.element.body if isinstance(doc, _Doc) else doc._element
    for child in parent.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, doc)
        elif isinstance(child, CT_Tbl):
            yield Table(child, doc)


def parse(path: str, **_) -> Book:
    try:
        import docx  # python-docx
    except ImportError as exc:
        raise RuntimeError(
            "解析 DOCX 需要 python-docx：pip install python-docx"
        ) from exc

    doc = docx.Document(path)
    book = Book()
    book.meta = BookMeta(
        source_file=os.path.basename(path),
        source_format="docx",
    )

    core = doc.core_properties
    book.meta.title = (core.title or "").strip() or os.path.splitext(
        os.path.basename(path)
    )[0]
    book.meta.author = (core.author or "").strip()
    book.meta.description = (core.comments or "").strip()
    if core.created:
        book.meta.published = core.created.strftime("%Y-%m-%d")

    chapters: list[Chapter] = []
    buf: list[str] = []
    saw_heading = False

    def flush_into(title: str, level: int) -> None:
        body = clean.tidy("\n\n".join(b for b in buf if b.strip()))
        buf.clear()
        if chapters:
            chapters[-1].content = body
        elif body:
            chapters.append(Chapter(title="卷首", content=body, level=1, order=0))
        if title:
            chapters.append(
                Chapter(title=title, content="", level=level, order=len(chapters))
            )

    from docx.table import Table

    for block in _iter_blocks(doc):
        if isinstance(block, Table):
            md = _table_markdown(block)
            if md:
                buf.append(md)
            continue

        level = _heading_level(block.style.name if block.style is not None else "")
        text = _para_markdown(block)
        if level and text:
            saw_heading = True
            flush_into(text, level)
        elif text:
            buf.append(text)

    if buf:
        body = clean.tidy("\n\n".join(b for b in buf if b.strip()))
        if chapters:
            chapters[-1].content = body
        elif body:
            chapters.append(
                Chapter(title=book.meta.title, content=body, level=1, order=0)
            )

    if not saw_heading:
        # 没用标题样式写的文档：退回按正文推断
        book.warn("DOCX 未使用标题样式，章节按正文内容推断")
        whole = "\n\n".join(c.content for c in chapters).strip()
        detected = structure.detect_chapters(whole)
        chapters = detected or structure.split_by_size(whole, book.meta.title)

    levels = sorted({c.level for c in chapters}) or [1]
    remap = {lv: i + 1 for i, lv in enumerate(levels)}
    for i, c in enumerate(chapters):
        c.level, c.order = remap[c.level], i

    book.chapters = [c for c in chapters if c.title or c.content.strip()]
    if not book.chapters:
        raise ValueError("DOCX 里没有解析出任何正文")
    return book
