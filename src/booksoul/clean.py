"""正文清洗。

解析器吐出来的 Markdown 还不能直接喂模型，典型毛病：

* TXT 里一个自然段被硬换行拆成十几行，模型会误判成十几个段落；
* PDF 每页顶部/底部有重复的书名、页码；
* 电子书里残留的"扫描自""本书由XX整理"之类的水印行；
* 大量连续空行、行尾空格。

这里的每个函数都只做一件事，由各解析器按需组合，避免"万能清洗"误伤正文。
"""

from __future__ import annotations

import re
from collections import Counter

# 句子结束符：中英文都算
_SENT_END = "。！？…、；：」』》”’)】》!?;:.\"')]"
_CJK = r"一-鿿㐀-䶿぀-ヿ가-힯"

# 常见的电子书水印/广告行，整行匹配才删，避免误伤正文
_JUNK_LINE_PATTERNS = [
    r"^[-=*_~\s]{3,}$",
    r"^\s*(?:本书|本电子书|此书).{0,20}(?:仅供|交流|学习|试读|下载).{0,20}$",
    r"^\s*(?:更多(?:好书|电子书|资源)|免费下载|请访问|欢迎访问|扫码关注).{0,40}$",
    r"^\s*(?:www\.|https?://)\S{4,60}$",
    r"^\s*(?:第\s*\d+\s*页|Page\s*\d+|-\s*\d+\s*-)\s*$",
]
_JUNK_RE = [re.compile(p, re.IGNORECASE) for p in _JUNK_LINE_PATTERNS]


def normalize_blank_lines(text: str) -> str:
    """行尾空白收拾干净，连续空行压成一个。保留 Markdown 的两空格硬换行。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for line in text.split("\n"):
        hard_break = line.endswith("  ") and line.strip()
        line = line.rstrip()
        lines.append(line + "  " if hard_break else line)
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# 行首缩进用到的各种空白。**必须含全角空格 U+3000**——中文书正是用它缩进的，
# 只剥半角空格等于没剥。U+00A0 等价物一并处理。
_INDENT_CHARS = " \t　     ﻿"


def strip_indent(text: str) -> str:
    """去掉每行开头的缩进空白（含全角空格）。

    中文书用全角空格 `　　` 做首行缩进。这些空白留在 Markdown 里既污染正文，
    残留 4 个以上半角空格时还会被渲染成代码块，模型看到的就是一坨 `<pre>`。
    代码块内部的缩进要保住，所以按围栏跳过。
    """
    out, in_fence = [], False
    for line in text.split("\n"):
        if line.lstrip(_INDENT_CHARS).startswith("```"):
            in_fence = not in_fence
            out.append(line)
            continue
        out.append(line if in_fence else line.lstrip(_INDENT_CHARS))
    return "\n".join(out)


def drop_junk_lines(text: str) -> tuple[str, int]:
    """删掉整行的水印/页码行，返回清洗后的文本和删除行数。"""
    kept, dropped = [], 0
    for line in text.split("\n"):
        if line.strip() and any(rx.match(line) for rx in _JUNK_RE):
            # 分隔线 --- 是合法 Markdown，只在它孤立出现时才删
            if line.strip() in ("---", "***", "___") and kept and not kept[-1].strip():
                kept.append(line)
                continue
            dropped += 1
            continue
        kept.append(line)
    return "\n".join(kept), dropped


def merge_soft_wraps(text: str) -> str:
    """把被硬换行拆散的自然段合回去。主要给 TXT 用。

    判据：上一行不以句末标点结尾，且下一行不像新段落的开头（标题、列表、引用、
    对话起始的破折号/引号）。中文相接不补空格，西文相接补一个空格。
    """
    paragraphs = re.split(r"\n\s*\n", text)
    out = []
    for para in paragraphs:
        lines = [ln for ln in para.split("\n")]
        if len(lines) <= 1:
            out.append(para)
            continue
        merged = lines[0]
        for line in lines[1:]:
            prev, cur = merged.rstrip(), line.strip()
            if not cur:
                merged += "\n"
                continue
            if (
                _starts_new_block(cur)
                or not prev
                or prev[-1] in _SENT_END
                or prev.endswith("  ")
            ):
                merged += "\n" + line
                continue
            # 中文字符相接不加空格，否则按西文规则补空格
            sep = (
                ""
                if (re.search(f"[{_CJK}]$", prev) or re.match(f"^[{_CJK}]", cur))
                else " "
            )
            merged = prev + sep + cur
        out.append(merged)
    return "\n\n".join(out)


def _starts_new_block(line: str) -> bool:
    return bool(
        re.match(r"^(#{1,6}\s|[-*+]\s|\d+[.)]\s|>\s|\|)", line)
        or re.match(r"^(?:```|---$|\*\*\*$)", line)
        or re.match(r"^[“「『\"'—－—]", line)  # 对话/引文起始
    )


def strip_running_headers(pages: list[str], *, threshold: float = 0.5) -> list[str]:
    """按出现频率剥掉 PDF 的页眉页脚。

    只看每页的首尾各两行；某行在超过 threshold 比例的页面上重复出现，就认定是
    页眉页脚。纯数字（页码）单独放宽到 0.3，因为页码内容每页都不同，这里比的是
    "去掉数字后的骨架"。
    """
    if len(pages) < 4:
        return pages

    def skeleton(s: str) -> str:
        return re.sub(r"\d+", "#", s.strip())

    head_c: Counter[str] = Counter()
    tail_c: Counter[str] = Counter()
    for page in pages:
        lines = [ln for ln in page.split("\n") if ln.strip()]
        for ln in lines[:2]:
            head_c[skeleton(ln)] += 1
        for ln in lines[-2:]:
            tail_c[skeleton(ln)] += 1

    n = len(pages)
    bad_head = {k for k, v in head_c.items() if v / n >= threshold and len(k) < 80}
    bad_tail = {k for k, v in tail_c.items() if v / n >= threshold and len(k) < 80}

    cleaned = []
    for page in pages:
        lines = page.split("\n")
        # 最多各剥 2 行——上面只统计了首尾各 2 行，再往里剥就超出了判据的依据范围，
        # 一旦正文恰好重复（诗歌的叠句、每章同样的引语）就会吃掉正文。
        for _ in range(2):
            if lines and (not lines[0].strip() or skeleton(lines[0]) in bad_head):
                lines.pop(0)
            else:
                break
        for _ in range(2):
            if lines and (not lines[-1].strip() or skeleton(lines[-1]) in bad_tail):
                lines.pop()
            else:
                break
        cleaned.append("\n".join(lines))
    return cleaned


def drop_duplicate_heading(content: str, title: str) -> str:
    """去掉正文开头那个和章节标题重复的标题行。

    章节标题来自目录（NCX/nav），而正文 xhtml 里往往还有一个 `<h2>婆罗门之子</h2>`。
    两者都保留的话，章节文件开头会连着出现两次同一个标题。

    `#` 是可选的：EPUB 里这行是 Markdown 标题，PDF 里它只是正文第一行纯文字
    （书签给了标题，版面上又印了一遍）。两种都要认。PDF 必须在合并硬换行之前
    调用，否则标题会先被粘进后面那一段，就找不着了。
    """
    if not title.strip():
        return content
    stripped = content.lstrip("\n")
    m = re.match(r"^(?:#{1,6}\s+)?(\S.*?)\s*$", stripped.split("\n", 1)[0])
    if not m:
        return content

    def norm(s: str) -> str:
        return re.sub(r"[\s　·:：.。、]+", "", s)

    if norm(m.group(1)) != norm(title):
        return content
    rest = stripped.split("\n", 1)
    return rest[1].lstrip("\n") if len(rest) > 1 else ""


def drop_duplicate_headings(book) -> int:
    """整本书做一遍，返回去掉了几个。"""
    n = 0
    for chapter in book.chapters:
        new = drop_duplicate_heading(chapter.content, chapter.title)
        if new != chapter.content:
            chapter.content = new
            n += 1
    return n


def tidy(text: str, *, soft_wrap: bool = False) -> str:
    """标准清洗流水线。soft_wrap=True 时额外合并硬换行（TXT/PDF 用）。"""
    text = strip_indent(text)
    text, _ = drop_junk_lines(text)
    if soft_wrap:
        text = merge_soft_wraps(text)
    return normalize_blank_lines(text)
