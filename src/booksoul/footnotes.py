"""把书里的脚注整理成标准 Markdown 脚注。

电子书的脚注转成 Markdown 之后通常长这样：正文里散着一个裸的 `[1]`，章节末尾
跟着一串 `[1]Brahmane，婆罗门……`。模型能读，但认不清哪个角标对应哪条注释。
转成 `[^1]` / `[^1]: ……` 之后语义就明确了，而且渲染器也认。

关键约束：**只有在同一章里找得到对应注释定义时，才改写正文里的角标**。
否则《1984》里的"101 房间"、引文里的"[原文如此]"都会被误伤。
"""

from __future__ import annotations

import re

# 章末的注释定义行：以 [1] / （1） / ① 开头，后面跟注释正文
_DEF_LINE = re.compile(
    r"^\s*(?:\\?[\[(【]\s*(?P<n1>\d{1,3})\s*[\])】]|(?P<n2>[①-⑳]))\s*[:：]?\s*(?P<body>\S.*)$"
)

_CIRCLED = {chr(0x2460 + i): str(i + 1) for i in range(20)}


def _num(m: re.Match[str]) -> str:
    if m.group("n1"):
        return m.group("n1")
    return _CIRCLED.get(m.group("n2") or "", "")


def normalize(markdown: str) -> tuple[str, int]:
    """返回 (处理后的 markdown, 识别出的脚注条数)。认不出来就原样返回。"""
    blocks = markdown.split("\n\n")

    # 从末尾往回扫，收集连续的注释定义块。中间夹一个非定义块就停——
    # 避免把正文里恰好以 [1] 开头的段落卷进来。
    defs: list[tuple[str, str]] = []
    cut = len(blocks)
    for i in range(len(blocks) - 1, -1, -1):
        block = blocks[i].strip()
        if not block:
            continue
        m = _DEF_LINE.match(block)
        if not m or not _num(m):
            break
        defs.append((_num(m), m.group("body").strip()))
        cut = i

    if len(defs) < 2:  # 一条孤零零的匹配多半是误判
        return markdown, 0

    defs.reverse()
    numbers = {n for n, _ in defs}

    body = "\n\n".join(blocks[:cut]).rstrip()

    # 正文里的角标：[1] / \[1\] / ① —— 只改写有定义的
    def mark(m: re.Match[str]) -> str:
        raw = m.group(0)
        n = m.group("d") or _CIRCLED.get(m.group("c") or "", "")
        return f"[^{n}]" if n in numbers else raw

    body = re.sub(r"\\?\[\s*(?P<d>\d{1,3})\s*\\?\]|(?P<c>[①-⑳])", mark, body)

    lines = [f"[^{n}]: {text}" for n, text in defs]
    return body + "\n\n" + "\n".join(lines) + "\n", len(defs)


def normalize_book(book) -> int:
    """就地处理整本书，返回处理的脚注总数。"""
    total = 0
    for chapter in book.chapters:
        chapter.content, n = normalize(chapter.content)
        total += n
    return total
