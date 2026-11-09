"""纯文本解析。

难点有两个，都跟中文有关：

* **编码**。中文 TXT 大量是 GBK/GB18030，chardet 经常把 GBK 判成 GB2312 或
  Big5。这里的策略是：按候选编码逐个试解，用"解出来有多少个是常用汉字"给
  结果打分，谁解得像中文就用谁。比单纯信 chardet 稳得多。
* **硬换行**。很多 TXT 每 30-40 字就断一行，一个自然段被拆成十几行。不合并的话
  模型看到的是十几个残句。
"""

from __future__ import annotations

import os
import re

from .. import clean, structure
from ..model import Book, BookMeta, Chapter

# 试解顺序：GB18030 是 GBK 的超集，放在 GBK 前面；Big5 覆盖繁体
_CANDIDATES = ("utf-8-sig", "utf-8", "gb18030", "big5", "utf-16", "cp1252", "latin-1")

_COMMON_CJK = re.compile(r"[一-鿿]")
_MOJIBAKE = re.compile(r"[�à-ÿ]{2,}")


def _score(text: str) -> float:
    """给解码结果打分：汉字占比越高、乱码串越少，分越高。"""
    if not text:
        return -1.0
    sample = text[:20000]
    cjk = len(_COMMON_CJK.findall(sample))
    bad = len(_MOJIBAKE.findall(sample)) + sample.count("�")
    ascii_printable = sum(1 for ch in sample if 32 <= ord(ch) < 127)
    return (cjk * 2 + ascii_printable) / max(len(sample), 1) - bad * 0.05


def decode_bytes(data: bytes) -> tuple[str, str]:
    """返回 (文本, 用的编码)。"""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16"), "utf-16"

    best: tuple[float, str, str] = (-2.0, "", "")
    for enc in _CANDIDATES:
        try:
            text = data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        s = _score(text)
        if s > best[0]:
            best = (s, text, enc)
        if enc in ("utf-8-sig", "utf-8") and s > 0.5 and "�" not in text[:20000]:
            break  # 干净的 UTF-8，不用再试了

    if best[1]:
        return best[1], best[2]

    try:  # 全都失败：让 chardet 兜底
        import chardet

        guess = chardet.detect(data[:200000]).get("encoding") or "utf-8"
        return data.decode(guess, errors="replace"), guess
    except ImportError:
        return data.decode("utf-8", errors="replace"), "utf-8(replace)"


_META_PATTERNS = {
    "title": re.compile(r"^\s*(?:书名|标题|title)\s*[:：]\s*(?P<v>.+)$", re.IGNORECASE),
    "author": re.compile(
        r"^\s*(?:作者|著者|author)\s*[:：]\s*(?P<v>.+)$", re.IGNORECASE
    ),
    "translator": re.compile(
        r"^\s*(?:译者|翻译|translator)\s*[:：]\s*(?P<v>.+)$", re.IGNORECASE
    ),
}


def _sniff_meta(text: str, meta: BookMeta) -> str:
    """开头几行常有"书名：xxx / 作者：yyy"，抓出来并从正文里删掉。"""
    lines = text.split("\n")
    head, rest = lines[:12], lines[12:]
    kept = []
    for line in head:
        matched = False
        for field, rx in _META_PATTERNS.items():
            m = rx.match(line)
            if m and not getattr(meta, field):
                setattr(meta, field, m.group("v").strip())
                matched = True
                break
        if not matched:
            kept.append(line)
    return "\n".join(kept + rest)


def parse(path: str, **_) -> Book:
    with open(path, "rb") as f:
        data = f.read()
    text, encoding = decode_bytes(data)

    book = Book()
    book.meta = BookMeta(
        source_file=os.path.basename(path),
        source_format="txt",
    )
    if encoding not in ("utf-8", "utf-8-sig"):
        book.warn(f"源文件编码识别为 {encoding}，已转成 UTF-8")

    # 先从正文头部抓元信息，再用文件名兜底——否则文件名会占住 title，
    # 文件里写着的"书名：xxx"既取不到、也删不掉，最后留在正文里。
    text = _sniff_meta(text, book.meta)
    if not book.meta.title:
        book.meta.title = os.path.splitext(os.path.basename(path))[0]
    text = clean.tidy(text, soft_wrap=True)

    chapters = structure.detect_chapters(text)
    if chapters is None:
        book.warn("未能识别章节标题，已按长度切块")
        chapters = structure.split_by_size(text, book.meta.title)
    book.chapters = [c for c in chapters if c.content.strip() or c.title]
    if not book.chapters:
        book.chapters = [Chapter(title=book.meta.title, content=text, level=1, order=0)]
    return book
