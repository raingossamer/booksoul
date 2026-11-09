"""(X)HTML → Markdown。

不用现成的 html2text/markdownify，因为中文电子书有几个它们处理不好的地方：

1. 中文段落普遍用全角空格 `　　` 做首行缩进。Markdown 里行首 4 个空格 = 代码块，
   全角空格虽不触发代码块，但会污染正文，必须剥掉。
2. 中文书的章节标题经常是 `<p class="title">` 而不是 `<h1>`，标题得由目录（NCX/nav）
   给出，转换器本身不能乱认标题。
3. 需要精确控制哪些标签被丢弃（style/script/epub 注释锚点等）。
"""

from __future__ import annotations

import re
from collections.abc import Callable

from lxml import etree
from lxml import html as lxml_html

# 直接丢弃、连同子树一起不要的标签
_DROP = {"script", "style", "head", "title", "meta", "link", "svg", "iframe", "object"}

# 行内标签：不产生块级分隔
_INLINE = {
    "a",
    "abbr",
    "b",
    "big",
    "cite",
    "code",
    "em",
    "i",
    "kbd",
    "q",
    "s",
    "samp",
    "small",
    "span",
    "strike",
    "strong",
    "sub",
    "sup",
    "tt",
    "u",
    "var",
    "font",
    "ruby",
    "rt",
    "rp",
    "rb",
    "bdi",
    "bdo",
    "mark",
    "time",
    "label",
    "nobr",
    "ins",
    "del",
}

_HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}

# 空白与零宽字符归一。全角空格 U+3000 是中文书首行缩进的元凶，先归一成
# 普通空格再由段落逻辑 strip 掉；零宽字符直接删除，否则会污染关键词检索。
_WS_TRANSLATE = str.maketrans(
    {
        "　": " ",  # 表意空格（全角空格）
        " ": " ",  # 不换行空格
        " ": " ",  # 数字空格
        " ": " ",  # 窄不换行空格
        "﻿": "",  # BOM / 零宽不换行空格
        "​": "",  # 零宽空格
        "‌": "",  # 零宽不连字
        "‍": "",  # 零宽连字
        "­": "",  # 软连字符
    }
)

_XLINK_HREF = "{http://www.w3.org/1999/xlink}href"


def _local(tag: object) -> str:
    """取标签的本地名（去掉命名空间）。非元素节点返回空串。"""
    if not isinstance(tag, str):
        return ""
    return tag.split("}")[-1].lower()


def _clean_text(s: str | None) -> str:
    if not s:
        return ""
    return re.sub(r"[ \t\r\n]+", " ", s.translate(_WS_TRANSLATE))


def _escape(s: str) -> str:
    """只做必要的转义，避免把中文正文弄得满是反斜杠。"""
    s = s.replace("\\", "\\\\")
    # 行内的 markdown 强调符号在中文书里极少见，出现时多半是真的星号
    s = re.sub(r"([*_`])", r"\\\1", s)
    # 裸的 < 会被当成 HTML 标签
    s = re.sub(r"<(?=[A-Za-z/!])", r"\\<", s)
    return s


def _escape_line_start(line: str) -> str:
    """行首可能被误读成 Markdown 结构的字符。"""
    return re.sub(r"^(\s*)([#>+\-]|\d+[.)])(\s)", r"\1\\\2\3", line)


# 脚注角标的常见写法：[1]、(1)、1、①
_FOOTNOTE_MARK = re.compile(r"[\[(【]?\d{1,3}[\])】]?|[①-⑳]")


def _is_external(href: str) -> bool:
    """只有带协议的链接才算外链；书内相对路径和锚点都不算。"""
    if not href or href.startswith("#"):
        return False
    return bool(re.match(r"^(?:https?|ftp|mailto|tel):", href, re.IGNORECASE))


class HtmlToMarkdown:
    def __init__(
        self,
        *,
        keep_images: bool = True,
        image_resolver: Callable[[str], str] | None = None,
        heading_base: int = 2,
    ) -> None:
        self.keep_images = keep_images
        self.image_resolver = image_resolver
        self.heading_base = heading_base

    # ---------- 对外入口 ----------

    def convert(self, source: str | bytes) -> str:
        root = self._parse(source)
        if root is None:
            return ""
        body = root.find(".//body")
        if body is None:
            body = root
        return self._join(self._blocks(body))

    def _parse(self, source: str | bytes):
        if isinstance(source, str):
            source = source.encode("utf-8")
        # XHTML 优先按 XML 解，失败再退回宽松的 HTML 解析器
        try:
            parser = etree.XMLParser(
                recover=True, resolve_entities=False, huge_tree=True
            )
            root = etree.fromstring(source, parser=parser)
            if root is not None:
                root = self._strip_ns(root)
                if root.tag == "body" or root.find(".//body") is not None:
                    return root
        except etree.XMLSyntaxError:
            pass
        try:
            return lxml_html.fromstring(source)
        except (etree.ParserError, ValueError):
            return None

    @staticmethod
    def _strip_ns(root):
        """XHTML 带命名空间，去掉后统一用裸标签名匹配。"""
        for el in root.iter():
            if isinstance(el.tag, str) and "}" in el.tag:
                el.tag = el.tag.split("}", 1)[1]
        etree.cleanup_namespaces(root)
        return root

    # ---------- 块级 ----------

    def _blocks(self, el) -> list[str]:
        out: list[str] = []
        buf: list[str] = []

        def flush() -> None:
            text = "".join(buf).strip()
            buf.clear()
            if text:
                out.append(self._paragraph(text))

        if el.text:
            buf.append(_escape(_clean_text(el.text)))

        for child in el:
            tag = _local(child.tag)
            if not tag or tag in _DROP:  # 注释/处理指令节点，或被丢弃的标签
                if child.tail:
                    buf.append(_escape(_clean_text(child.tail)))
                continue

            if tag == "br":
                buf.append("  \n")
            elif tag == "img":
                buf.append(self._image(child))
            elif tag in _INLINE:
                buf.append(self._inline(child))
            else:
                flush()
                out.extend(self._block(child, tag))

            if child.tail:
                buf.append(_escape(_clean_text(child.tail)))

        flush()
        return [b for b in out if b.strip()]

    def _block(self, el, tag: str) -> list[str]:
        if tag in _HEADINGS:
            text = re.sub(r"\s+", " ", self._inline(el).strip())
            if not text:
                return []
            depth = min(self.heading_base + _HEADINGS[tag] - 1, 6)
            return ["#" * depth + " " + text]

        if tag == "hr":
            return ["---"]

        if tag == "blockquote":
            inner = self._join(self._blocks(el))
            if not inner.strip():
                return []
            return ["\n".join("> " + ln if ln else ">" for ln in inner.split("\n"))]

        if tag in ("ul", "ol"):
            return self._list(el, ordered=(tag == "ol"))

        if tag == "pre":
            text = "".join(el.itertext()).strip("\n")
            return ["```\n" + text + "\n```"] if text.strip() else []

        if tag == "table":
            return self._table(el)

        # p / div / section / li / td ... 一律按容器递归，结构残缺也不会丢内容
        return self._blocks(el)

    def _paragraph(self, text: str) -> str:
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r" *\n *", "\n", text)
        lines = [_escape_line_start(ln.strip()) for ln in text.split("\n")]
        return "\n".join(lines).strip()

    def _list(self, el, *, ordered: bool) -> list[str]:
        items: list[str] = []
        idx = 1
        for li in el:
            if _local(li.tag) != "li":
                continue
            inner = self._join(self._blocks(li)).strip()
            if not inner:
                continue
            marker = f"{idx}. " if ordered else "- "
            pad = " " * len(marker)
            lines = inner.split("\n")
            block = marker + lines[0]
            for ln in lines[1:]:
                block += "\n" + (pad + ln if ln else "")
            items.append(block)
            idx += 1
        return ["\n".join(items)] if items else []

    def _table(self, el) -> list[str]:
        rows: list[list[str]] = []
        for tr in el.iter():
            if _local(tr.tag) != "tr":
                continue
            cells = [
                re.sub(r"\s+", " ", self._inline(td).strip()).replace("|", "\\|")
                for td in tr
                if _local(td.tag) in ("td", "th")
            ]
            if cells:
                rows.append(cells)
        if not rows:
            return []
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        head, *body = rows
        md = ["| " + " | ".join(head) + " |", "|" + "---|" * width]
        md += ["| " + " | ".join(r) + " |" for r in body]
        return ["\n".join(md)]

    # ---------- 行内 ----------

    def _inline(self, el) -> str:
        tag = _local(el.tag)
        if tag in _DROP:
            return _escape(_clean_text(el.tail))
        if tag == "br":
            return "  \n" + _escape(_clean_text(el.tail))
        if tag == "img":
            return self._image(el) + _escape(_clean_text(el.tail))
        if tag == "rp":  # 日文注音的括号占位符，丢掉
            return _escape(_clean_text(el.tail))

        parts: list[str] = [_escape(_clean_text(el.text))]
        for child in el:
            ctag = _local(child.tag)
            if not ctag:
                if child.tail:
                    parts.append(_escape(_clean_text(child.tail)))
                continue
            if ctag in _INLINE or ctag in ("br", "img"):
                parts.append(self._inline(child))
            else:
                # 行内标签里嵌了块级标签，降级成纯文本拼接
                parts.append(self._join(self._blocks(child)).replace("\n\n", " "))
                parts.append(_escape(_clean_text(child.tail)))
        inner = "".join(parts)

        stripped = inner.strip()
        if not stripped:
            return ""

        lead = inner[: len(inner) - len(inner.lstrip())]
        trail = inner[len(inner.rstrip()) :]

        if tag in ("strong", "b"):
            wrapped = f"**{stripped}**"
        elif tag in ("em", "i", "cite", "var"):
            wrapped = f"*{stripped}*"
        elif tag in ("code", "kbd", "samp", "tt"):
            wrapped = "`" + stripped.replace("`", "").replace("\\", "") + "`"
        elif tag in ("s", "strike", "del"):
            wrapped = f"~~{stripped}~~"
        elif tag == "a":
            href = (el.get("href") or "").strip()
            # 只保留真正的外链。书内跳转（脚注、目录、章节互引）在 Markdown 里指向
            # 不存在的文件，留着只会变成噪声，剥成纯文字即可。
            wrapped = f"[{stripped}]({href})" if _is_external(href) else stripped
        elif tag == "sup":
            # 脚注角标（[1] / 1）保持原样，交给 footnotes 那一步统一处理
            wrapped = (
                stripped
                if _FOOTNOTE_MARK.fullmatch(stripped)
                else (f"^{stripped}^" if len(stripped) <= 8 else stripped)
            )
        elif tag == "sub":
            wrapped = f"~{stripped}~" if len(stripped) <= 8 else stripped
        elif tag == "rt":
            wrapped = f"（{stripped}）"  # 日文注音：括号里保留读音
        else:
            wrapped = stripped

        return lead + wrapped + trail

    def _image(self, el) -> str:
        if not self.keep_images:
            return ""
        src = (el.get("src") or el.get(_XLINK_HREF) or "").strip()
        if not src:
            return ""
        if self.image_resolver:
            src = self.image_resolver(src)
            if not src:
                return ""
        alt = re.sub(r"\s+", " ", (el.get("alt") or "").strip())
        return f"![{alt}]({src})"

    # ---------- 组装 ----------

    @staticmethod
    def _join(blocks: list[str]) -> str:
        text = "\n\n".join(b.strip("\n") for b in blocks if b.strip())
        return re.sub(r"\n{3,}", "\n\n", text).strip()


def html_to_markdown(source: str | bytes, **kwargs) -> str:
    return HtmlToMarkdown(**kwargs).convert(source)
