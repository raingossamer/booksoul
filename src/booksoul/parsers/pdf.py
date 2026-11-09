"""PDF 解析。

PDF 是所有格式里最不可靠的一种：它记录的是"字放在页面哪个位置"，不是"这是一
个段落"。所以这里做三件额外的事：

1. **按页抽取**，再用出现频率剥掉页眉页脚（见 clean.strip_running_headers）；
2. **优先用 PDF 自带的书签（outline）**当目录，它给出的章节边界是准的；
3. 扫描版 PDF 抽不出文字时明确报错，而不是产出一个空文件让人以为成功了。

后端优先 PyMuPDF（版面质量最好），没有就退到 pypdf。
"""

from __future__ import annotations

import os
import re

from .. import clean, structure
from ..model import Book, BookMeta, Chapter, ProgressFn

_INSTALL_HINT = (
    "解析 PDF 需要安装后端之一：\n"
    "  pip install pymupdf     （推荐，版面还原最好）\n"
    "  pip install pypdf       （轻量备选）"
)


def _load_backend():
    try:
        # 1.24 起模块叫 pymupdf；旧名 fitz 仍能 import，但会打弃用警告
        import pymupdf

        return "pymupdf", pymupdf
    except ImportError:
        pass
    try:
        import fitz  # PyMuPDF < 1.24

        return "pymupdf", fitz
    except ImportError:
        pass
    try:
        import pypdf

        return "pypdf", pypdf
    except ImportError:
        pass
    raise RuntimeError(_INSTALL_HINT)


def _extract_pymupdf(fitz, path: str, on_progress: ProgressFn | None = None):
    doc = fitz.open(path)
    meta = doc.metadata or {}
    total = doc.page_count
    pages = []
    for i, page in enumerate(doc, 1):
        pages.append(page.get_text("text") or "")
        if on_progress is not None:
            on_progress(i, total)
    toc = []
    for level, title, page_no in doc.get_toc() or []:
        title = re.sub(r"\s+", " ", title).strip()
        if title:
            toc.append((max(1, int(level)), title, max(1, int(page_no))))
    doc.close()
    return meta, pages, toc


def _extract_pypdf(pypdf, path: str, on_progress: ProgressFn | None = None):
    reader = pypdf.PdfReader(path)
    info = reader.metadata or {}
    meta = {
        "title": str(info.get("/Title", "") or ""),
        "author": str(info.get("/Author", "") or ""),
        "producer": str(info.get("/Producer", "") or ""),
    }
    pages = []
    total = len(reader.pages)
    for i, page in enumerate(reader.pages, 1):
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
        if on_progress is not None:
            on_progress(i, total)

    toc = []
    try:

        def walk(items, level: int) -> None:
            for item in items:
                if isinstance(item, list):
                    walk(item, level + 1)
                    continue
                title = re.sub(r"\s+", " ", str(item.title)).strip()
                page_no = reader.get_destination_page_number(item) + 1
                if title:
                    toc.append((level, title, page_no))

        walk(reader.outline or [], 1)
    except Exception:
        toc = []
    return meta, pages, toc


def parse(path: str, *, on_progress: ProgressFn | None = None, **_) -> Book:
    """解析 PDF。`on_progress(done, total)` 按页回报。

    页是这个格式里唯一数得准的刻度。大部头 PDF 是等得最久的那一类，
    没有刻度就只剩一个不动的转圈。

    注意签名里保留了 `**_`：分发层对所有格式统一传 keep_images，PDF 用不上。
    但 on_progress 必须是具名参数——落进 `**_` 就会被静默吞掉，传了也不生效。
    """
    name, backend = _load_backend()
    raw_meta, pages, toc = (
        _extract_pymupdf(backend, path, on_progress)
        if name == "pymupdf"
        else _extract_pypdf(backend, path, on_progress)
    )

    book = Book()
    book.meta = BookMeta(
        title=(raw_meta.get("title") or "").strip()
        or os.path.splitext(os.path.basename(path))[0],
        author=(raw_meta.get("author") or "").strip(),
        source_file=os.path.basename(path),
        source_format="pdf",
    )

    if not any(p.strip() for p in pages):
        raise ValueError(
            "这个 PDF 里抽不出文字，多半是扫描件（图片）。\n"
            "需要先做 OCR，例如： ocrmypdf 输入.pdf 输出.pdf"
        )

    pages = clean.strip_running_headers(pages)
    # 章节标题通常既在书签里，又印在那一页正文第一行。必须赶在 tidy 合并硬换行
    # 之前去重，否则标题会被粘成「第二章　岸上岸上的人喊他……」。
    for _level, title, start in toc:
        i = start - 1
        if 0 <= i < len(pages):
            pages[i] = clean.drop_duplicate_heading(pages[i], title)
    pages = [clean.tidy(p, soft_wrap=True) for p in pages]

    chapters: list[Chapter] = []
    if toc:
        # 用书签切：每个书签管到下一个书签所在页
        marks = sorted(toc, key=lambda t: t[2])
        for i, (level, title, start) in enumerate(marks):
            end = marks[i + 1][2] if i + 1 < len(marks) else len(pages) + 1
            body = "\n\n".join(pages[start - 1 : end - 1]).strip()
            chapters.append(
                Chapter(
                    title=title,
                    content=body,
                    level=level,
                    order=len(chapters),
                    source_id=f"p.{start}-{max(start, end - 1)}",
                )
            )
        head = "\n\n".join(pages[: marks[0][2] - 1]).strip()
        if head:
            chapters.insert(0, Chapter(title="卷首", content=head, level=1, order=0))
    else:
        book.warn("PDF 没有书签目录，章节按正文内容推断")
        whole = "\n\n".join(pages)
        chapters = structure.detect_chapters(whole) or structure.split_by_size(
            whole, book.meta.title
        )

    levels = sorted({c.level for c in chapters}) or [1]
    remap = {lv: i + 1 for i, lv in enumerate(levels)}
    for i, c in enumerate(chapters):
        c.level, c.order = remap[c.level], i

    book.chapters = [c for c in chapters if c.content.strip()]
    if not book.chapters:
        raise ValueError("PDF 里没有解析出任何正文")
    book.warn(f"PDF 后端：{name}；PDF 的段落边界不总是可靠，建议抽查几章")
    return book
