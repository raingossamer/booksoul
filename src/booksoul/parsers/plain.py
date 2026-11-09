"""单文件 HTML / Markdown。

HTML 走和 EPUB 同一个转换器；Markdown 本来就是目标格式，只做清洗和按标题切章。
两者都没有可靠的元信息来源，标题从 <title>/一级标题/文件名里取。
"""

from __future__ import annotations

import os
import re

from .. import clean, structure
from ..html2md import HtmlToMarkdown
from ..model import Book, BookMeta, Chapter
from ..parsers.txt import decode_bytes
from ..xmlutil import find


def _split_by_headings(markdown: str) -> list[Chapter] | None:
    """按 Markdown 的 ATX 标题切章。"""
    lines = markdown.split("\n")
    marks: list[tuple[int, str, int]] = []
    in_fence = False
    for i, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = re.match(r"^(#{1,6})\s+(\S.*)$", line)
        if m:
            marks.append((i, m.group(2).strip(), len(m.group(1))))
    if not marks:
        return None

    levels = sorted({lv for _, _, lv in marks})
    remap = {lv: i + 1 for i, lv in enumerate(levels)}

    chapters: list[Chapter] = []
    if marks[0][0] > 0:
        head = "\n".join(lines[: marks[0][0]]).strip()
        if head:
            chapters.append(Chapter(title="卷首", content=head, level=1, order=0))
    for i, (idx, title, level) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(lines)
        body = "\n".join(lines[idx + 1 : end]).strip()
        chapters.append(
            Chapter(
                title=title,
                content=body,
                level=remap[level],
                order=len(chapters),
                source_id=f"line:{idx + 1}",
            )
        )
    return chapters


def parse_html(path: str, *, keep_images: bool = True, **_) -> Book:
    with open(path, "rb") as f:
        data = f.read()

    conv = HtmlToMarkdown(keep_images=keep_images)
    markdown = conv.convert(data)

    book = Book()
    title = ""
    root = conv._parse(data)
    if root is not None:
        node = find(root, ".//title", ".//head/title")
        if node is not None:
            title = re.sub(r"\s+", " ", "".join(node.itertext())).strip()

    book.meta = BookMeta(
        title=title or os.path.splitext(os.path.basename(path))[0],
        source_file=os.path.basename(path),
        source_format="html",
    )
    markdown = clean.tidy(markdown)
    chapters = _split_by_headings(markdown) or structure.detect_chapters(markdown)
    if not chapters:
        chapters = structure.split_by_size(markdown, book.meta.title)
    book.chapters = [c for c in chapters if c.content.strip()]
    if not book.chapters:
        raise ValueError("HTML 里没有解析出任何正文")
    return book


def parse_markdown(path: str, **_) -> Book:
    with open(path, "rb") as f:
        text, _enc = decode_bytes(f.read())

    book = Book()
    book.meta = BookMeta(
        source_file=os.path.basename(path),
        source_format="markdown",
    )

    # YAML front matter 里可能有元信息
    fm = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if fm:
        for line in fm.group(1).split("\n"):
            key, _, value = line.partition(":")
            key, value = key.strip().lower(), value.strip().strip("\"'")
            if not value:
                continue
            if key in ("title", "书名"):
                book.meta.title = value
            elif key in ("author", "作者"):
                book.meta.author = value
            elif key in ("translator", "译者"):
                book.meta.translator = value
        text = text[fm.end() :]

    if not book.meta.title:
        h1 = re.search(r"^#\s+(\S.*)$", text, re.MULTILINE)
        book.meta.title = (
            h1.group(1).strip() if h1 else os.path.splitext(os.path.basename(path))[0]
        )

    text = clean.normalize_blank_lines(text)
    chapters = _split_by_headings(text) or structure.detect_chapters(text)
    if not chapters:
        chapters = structure.split_by_size(text, book.meta.title)
    book.chapters = [c for c in chapters if c.content.strip()]
    if not book.chapters:
        raise ValueError("Markdown 里没有解析出任何正文")
    return book
