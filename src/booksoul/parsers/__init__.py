"""格式探测与解析分发。

**先看文件内容，再看扩展名**。下载来的书扩展名经常是错的：标着 .txt 的其实是
epub，标着 .epub 的其实是 azw3。按魔数判断能避开这类坑。
"""

from __future__ import annotations

import os
import zipfile

from ..model import Book, ProgressFn

SUPPORTED = {
    "epub": "EPUB 电子书",
    "mobi": "Kindle MOBI",
    "azw3": "Kindle AZW3/KF8",
    "pdf": "PDF",
    "docx": "Word 文档",
    "txt": "纯文本",
    "html": "HTML 网页",
    "markdown": "Markdown",
}


def detect_format(path: str) -> str:
    """返回 SUPPORTED 里的格式键。认不出来时按扩展名兜底，最后当纯文本。"""
    if not os.path.isfile(path):
        raise FileNotFoundError(f"文件不存在：{path}")

    with open(path, "rb") as f:
        head = f.read(4096)

    if head.startswith(b"%PDF"):
        return "pdf"
    if head[60:68] in (b"BOOKMOBI", b"TPZ3TPZ0"):
        return "azw3" if b"RSC%" in head[:1024] else "mobi"
    if head.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(path) as zf:
                names = zf.namelist()
                if "mimetype" in names:
                    mime = zf.read("mimetype").strip().decode("ascii", "ignore")
                    if mime == "application/epub+zip":
                        return "epub"
                if any(n.endswith(".opf") for n in names):
                    return "epub"
                if "word/document.xml" in names:
                    return "docx"
        except zipfile.BadZipFile:
            pass

    ext = os.path.splitext(path)[1].lower().lstrip(".")
    by_ext = {
        "epub": "epub",
        "mobi": "mobi",
        "prc": "mobi",
        "azw": "mobi",
        "azw3": "azw3",
        "kfx": "azw3",
        "pdf": "pdf",
        "docx": "docx",
        "doc": "docx",
        "htm": "html",
        "html": "html",
        "xhtml": "html",
        "md": "markdown",
        "markdown": "markdown",
        "txt": "txt",
        "text": "txt",
    }
    if ext in by_ext:
        return by_ext[ext]

    # 内容里有大量标签就当 HTML，否则当纯文本
    sample = head.lower()
    if b"<html" in sample or b"<!doctype html" in sample or b"<body" in sample:
        return "html"
    return "txt"


def parse(
    path: str,
    *,
    keep_images: bool = True,
    on_progress: ProgressFn | None = None,
) -> tuple[Book, object | None]:
    """解析一本书。返回 (Book, 解析器实例或 None)。

    解析器实例只有 EPUB 会给——它还握着 zip 句柄，写盘那步要用它导图片。

    `on_progress(done, total)` 逐节回报进度，不给就完全不影响原有行为。
    只有能数得准的格式才会回报（epub 按 spine 文件、pdf 按页）；数不准的格式
    宁可不报，也不要编一个假的百分比——那比没有进度更误导人。
    """
    fmt = detect_format(path)

    if fmt == "epub":
        from .epub import parse as _parse

        return _parse(path, keep_images=keep_images, on_progress=on_progress)

    if fmt in ("mobi", "azw3"):
        from .mobi import parse as _parse

        return _parse(path, keep_images=keep_images), None

    if fmt == "pdf":
        from .pdf import parse as _parse

        return _parse(path, on_progress=on_progress), None

    if fmt == "docx":
        from .docx import parse as _parse

        return _parse(path), None

    if fmt == "html":
        from .plain import parse_html as _parse

        return _parse(path, keep_images=keep_images), None

    if fmt == "markdown":
        from .plain import parse_markdown as _parse

        return _parse(path), None

    from .txt import parse as _parse

    return _parse(path), None
