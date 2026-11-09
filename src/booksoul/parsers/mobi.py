"""MOBI / AZW / AZW3 解析。

这几种是 Kindle 的私有格式（AZW3 内部是 KF8，本质上是压进 PDB 容器的 EPUB）。
自己写解包器不划算，这里借 `mobi` 包解包成 EPUB 或 HTML，再交给对应的解析器。

装不上 `mobi` 也有出路：Calibre 的命令行工具能转，错误信息里直接给命令。
"""

from __future__ import annotations

import os
import shutil

from ..model import Book

_HINT = """解析 MOBI/AZW3 需要先解包，二选一：

  1) pip install mobi          然后重新运行本命令
  2) 用 Calibre 转成 EPUB 再解析：
       ebook-convert "{path}" "{stem}.epub"
       booksoul parse "{stem}.epub"
"""


def parse(path: str, *, keep_images: bool = True, **_) -> Book:
    try:
        import mobi as mobi_lib
    except ImportError as exc:
        stem = os.path.splitext(path)[0]
        raise RuntimeError(_HINT.format(path=path, stem=stem)) from exc

    tmpdir, unpacked = mobi_lib.extract(path)
    try:
        ext = os.path.splitext(unpacked)[1].lower()
        if ext == ".epub":
            from .epub import EpubParser

            parser = EpubParser(unpacked, keep_images=keep_images)
            book = parser.parse()
            # 图片得趁临时目录还在的时候取出来，缓存进内存
            book._pending_images = {  # type: ignore[attr-defined]
                rel: parser.zf.read(zip_path)
                for zip_path, rel in parser.images.items()
                if zip_path in parser._names
            }
        else:
            from .plain import parse_html

            book = parse_html(unpacked, keep_images=False)

        book.meta.source_file = os.path.basename(path)
        book.meta.source_format = (
            os.path.splitext(path)[1].lstrip(".").lower() or "mobi"
        )
        if not book.meta.title:
            book.meta.title = os.path.splitext(os.path.basename(path))[0]
        book.warn("经 mobi 解包后解析，Kindle 私有格式的版面还原可能不完整")
        return book
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
