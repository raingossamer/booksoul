"""解析层的回归测试。

这里每一条基本都对应一个真实踩过的坑，不是为了凑覆盖率。改解析逻辑后跑一遍：

    pytest tests/ -q
"""

from __future__ import annotations

import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from booksoul import clean, footnotes, structure  # noqa: E402
from booksoul.html2md import html_to_markdown  # noqa: E402
from booksoul.parsers import detect_format  # noqa: E402
from booksoul.parsers.txt import decode_bytes  # noqa: E402
from booksoul.retrieve import BM25, tokenize  # noqa: E402

# ---------------------------------------------------------------- 中文排版


def test_全角空格缩进被剥掉():
    """中文书用 U+3000 缩进。只剥半角空格等于没剥，正文会带着一串全角空格。"""
    text = "　　第一段。\n　　第二段。"
    assert clean.strip_indent(text) == "第一段。\n第二段。"


def test_行首四空格不会变成代码块():
    assert not clean.strip_indent("    正文").startswith("    ")


def test_代码块内部缩进要保住():
    text = "```\n    keep_me()\n```"
    assert "    keep_me()" in clean.strip_indent(text)


def test_硬换行合并回自然段():
    text = "这是一个被拆\n开的句子。"
    assert clean.merge_soft_wraps(text) == "这是一个被拆开的句子。"


def test_合并时中文不补空格英文补():
    assert clean.merge_soft_wraps("中文\n接续") == "中文接续"
    assert clean.merge_soft_wraps("hello\nworld") == "hello world"


def test_句末标点处不合并():
    text = "上一句结束了。\n下一句另起。"
    assert "\n" in clean.merge_soft_wraps(text)


def test_对话起始行不被合并进上一行():
    text = "他停下来\n“你是谁？”"
    assert "\n" in clean.merge_soft_wraps(text)


def test_水印行被删正文不动():
    text = "正文一段。\n本书由XX论坛整理，仅供学习交流\nwww.example.com\n正文二段。"
    out, n = clean.drop_junk_lines(text)
    assert n == 2
    assert "正文一段。" in out and "正文二段。" in out
    assert "example.com" not in out


def test_pdf页眉页脚按频率剥离():
    # 页眉每页相同、页码只有数字在变；正文每页都不一样——这才是真实 PDF 的样子。
    bodies = [
        "他走进院子",
        "河水在流",
        "夜里下起雨",
        "老人没有回头",
        "钟声响了",
        "船靠了岸",
        "风停了",
        "她合上书",
        "路的尽头",
        "天亮之前",
    ]
    pages = [
        f"某某传\n{b}，然后事情继续发生。\n- {i} -" for i, b in enumerate(bodies, 1)
    ]
    out = clean.strip_running_headers(pages)
    assert all("某某传" not in p for p in out)
    assert all(b in out[i] for i, b in enumerate(bodies)), "正文被误删"


def test_页眉剥离不会吃掉重复的正文():
    """诗歌叠句、每章同样的题记——重复出现但在正文中间，不该被当页眉。"""
    pages = [f"页眉\n首行{i}\n同样的叠句\n尾行{i}\n- {i} -" for i in range(1, 9)]
    out = clean.strip_running_headers(pages)
    assert all("同样的叠句" in p for p in out)


def test_正文开头与章节标题重复的标题被去掉():
    """章节标题来自目录，正文 xhtml 里往往还有一个同名 h2，两个都留就重复了。"""
    assert (
        clean.drop_duplicate_heading("### 婆罗门之子\n\n正文。", "婆罗门之子")
        == "正文。"
    )
    assert (
        clean.drop_duplicate_heading("## 第一章　开端\n\n正文。", "第一章 开端")
        == "正文。"
    )


def test_没有井号的纯文字标题也算重复():
    """PDF 没有 h2：标题从书签来，版面上又印了一行纯文字，长得不像 Markdown 标题。

    必须赶在 merge_soft_wraps 之前去掉，否则会被粘成「第二章　岸上岸上的人喊他」。
    """
    assert (
        clean.drop_duplicate_heading(
            "第二章　岸上\n岸上的人喊他，他听见了。", "第二章 岸上"
        )
        == "岸上的人喊他，他听见了。"
    )


def test_不同名的标题不动():
    content = "### 另一个小节\n\n正文。"
    assert clean.drop_duplicate_heading(content, "婆罗门之子") == content


def test_正文不是标题开头时不动():
    content = "正文直接开始。\n\n第二段。"
    assert clean.drop_duplicate_heading(content, "某章") == content


# ---------------------------------------------------------------- 章节推断


def test_章节标题与正文句子不混淆():
    """'第二章的内容在这里' 是正文，不是标题——序号后面必须有分隔符。"""
    text = "\n".join(
        [
            "第一章　开端",
            "内容甲。",
            "第二章　发展",
            "第二章的内容在这里，不该被当成标题。",
            "第三章　结局",
            "内容丙。",
        ]
    )
    chapters = structure.detect_chapters(text)
    titles = [c.title for c in chapters]
    assert titles == ["第一章　开端", "第二章　发展", "第三章　结局"]


def test_冒号分隔的标题也认():
    text = "\n".join(["第一章：起", "甲", "第二章：承", "乙", "第三章：转", "丙"])
    assert [c.title for c in structure.detect_chapters(text)] == [
        "第一章：起",
        "第二章：承",
        "第三章：转",
    ]


def test_光杆章节号也认():
    text = "\n".join(["第一章", "甲", "第二章", "乙", "第三章", "丙"])
    assert len(structure.detect_chapters(text)) == 3


def test_卷章嵌套还原成层级():
    text = "\n".join(
        [
            "第一卷　上",
            "第一章　甲",
            "a",
            "第二章　乙",
            "b",
            "第二卷　下",
            "第三章　丙",
            "c",
            "第四章　丁",
            "d",
        ]
    )
    chapters = structure.detect_chapters(text)
    levels = {c.title: c.level for c in chapters}
    assert levels["第一卷　上"] < levels["第一章　甲"]


def test_序跋类标题无条件识别():
    text = "\n".join(
        ["楔子", "甲", "第一章　起", "乙", "第二章　承", "丙", "后记", "丁"]
    )
    titles = [c.title for c in structure.detect_chapters(text)]
    assert "楔子" in titles and "后记" in titles


def test_认不出章节时返回None():
    assert structure.detect_chapters("就是一段普通的话，没有任何章节标记。") is None


def test_切块兜底不会产出空章():
    text = "\n\n".join(["段落内容" * 200 for _ in range(10)])
    chunks = structure.split_by_size(text, "某书", target=2000)
    assert len(chunks) > 1
    assert all(c.content.strip() for c in chunks)


# ---------------------------------------------------------------- HTML→MD


def test_标题层级与行内样式():
    md = html_to_markdown(
        "<body><h1>标题</h1><p>正文<strong>粗</strong><em>斜</em></p></body>"
    )
    assert "## 标题" in md  # heading_base 默认 2
    assert "**粗**" in md and "*斜*" in md


def test_书内链接剥成纯文字外链保留():
    md = html_to_markdown(
        '<body><p><a href="part0005.xhtml#f1">注1</a>'
        '<a href="#anchor">锚</a>'
        '<a href="https://example.com">外链</a></p></body>'
    )
    assert "part0005" not in md and "(#anchor)" not in md
    assert "注1" in md and "锚" in md
    assert "[外链](https://example.com)" in md


def test_style与script被丢弃():
    md = html_to_markdown(
        "<body><style>p{color:red}</style><p>正文</p><script>x()</script></body>"
    )
    assert "color:red" not in md and "x()" not in md
    assert "正文" in md


def test_表格与列表():
    md = html_to_markdown(
        "<body><ul><li>甲</li><li>乙</li></ul>"
        "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table></body>"
    )
    assert "- 甲" in md and "- 乙" in md
    assert "| A | B |" in md and "| 1 | 2 |" in md


def test_全角空格在转换时就被归一():
    md = html_to_markdown("<body><p>　　缩进的段落</p></body>")
    assert not md.startswith("　")


# ---------------------------------------------------------------- 脚注


def test_脚注转成标准markdown脚注():
    md = "正文里有个角标[1]，还有一个[2]。\n\n[1]第一条注释。\n\n[2]第二条注释。"
    out, n = footnotes.normalize(md)
    assert n == 2
    assert "[^1]" in out and "[^2]" in out
    assert "[^1]: 第一条注释。" in out


def test_没有定义的方括号数字不动():
    """《1984》的'101 房间'、引文里的'[原文如此]'不该被改写。"""
    md = "他被带进[101]房间。\n\n后面没有任何脚注定义。"
    out, n = footnotes.normalize(md)
    assert n == 0
    assert out == md


def test_单条疑似定义不触发():
    md = "正文。\n\n[1]只有一条，多半是误判。"
    _, n = footnotes.normalize(md)
    assert n == 0


# ---------------------------------------------------------------- 编码


def test_gbk文本被正确解码():
    text, enc = decode_bytes("中文内容测试，包含标点。".encode("gb18030"))
    assert "中文内容测试" in text
    assert enc in ("gb18030", "gbk")


def test_utf8带bom():
    text, _ = decode_bytes("﻿中文".encode())
    assert text.strip("﻿").startswith("中文")


def test_utf16():
    text, _ = decode_bytes("中文内容".encode("utf-16"))
    assert "中文内容" in text


# ---------------------------------------------------------------- 格式探测


def test_按内容识别格式而非扩展名(tmp_path):
    """下载来的书扩展名经常是错的。"""
    fake = tmp_path / "其实是epub.txt"
    with zipfile.ZipFile(fake, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", "<container/>")
    assert detect_format(str(fake)) == "epub"


def test_pdf按魔数识别(tmp_path):
    f = tmp_path / "noext"
    f.write_bytes(b"%PDF-1.7\n...")
    assert detect_format(str(f)) == "pdf"


def test_纯文本兜底(tmp_path):
    f = tmp_path / "book.txt"
    f.write_text("就是一段文字", encoding="utf-8")
    assert detect_format(str(f)) == "txt"


# ---------------------------------------------------------------- 检索


def test_跨词边界的垃圾二元组不主导排序():
    """'悉达多为什么' 会切出 '多什'；它若在全书只出现一次，idf 极高会带偏排序。"""
    docs = [
        tokenize("佛陀说法，众人皈依。他离开了佛陀。"),
        tokenize("这里恰好有多什么这个巧合组合。"),
        tokenize("无关内容，讲别的事情。"),
    ]
    bm = BM25(docs)
    scores = bm.score(tokenize("为什么离开佛陀"))
    assert scores[0] > scores[1], "讲佛陀的那篇应该排在巧合命中之前"


def test_低频孤例词被过滤():
    docs = [tokenize("重复的词重复出现多次重复"), tokenize("孤例")]
    bm = BM25(docs)
    assert bm.total_freq["孤例"] == 1


# ---------------------------------------------------------------- 大脑


def test_内容审核错误被单独识别():
    """严肃文学触发国内平台过滤是常态，不能让人以为是 key 配错了。"""
    from booksoul.brain import _is_moderation

    assert _is_moderation(400, "Input data may contain inappropriate content.")
    assert _is_moderation(400, "输入内容涉嫌违规，已被内容审核拦截")
    assert not _is_moderation(400, "max_tokens must be a positive integer")
    assert not _is_moderation(401, "invalid api key")
    assert not _is_moderation(404, "model not found")


def test_系统提示把书放在缓存断点之前():
    """书在前、人格在后，换人格时整本书的缓存才不会失效。"""
    from booksoul.brain import build_system

    blocks = build_system("书的正文" * 100, "人格提示词")
    assert len(blocks) == 2
    book_block, persona_block = blocks
    assert book_block[1] is True, "书那一块应当设缓存断点"
    assert persona_block[1] is False, "人格那一块不设断点，换人格才不会冲掉书"
    assert "书的正文" in book_block[0]
    assert "人格提示词" in persona_block[0]


# ---------------------------------------------------------------- 真书端到端

BOOK_DIR = os.path.join(os.path.dirname(__file__), "..", "library", "悉达多")


@pytest.mark.skipif(not os.path.isdir(BOOK_DIR), reason="需要先解析《悉达多》")
def test_真书解析产物完整():
    from booksoul import library

    book = library.load("悉达多")
    assert book.meta.author == "赫尔曼·黑塞"
    assert len(book.chapters) >= 15
    assert book.words > 40000

    titles = [c.title for c in book.chapters]
    for expected in ("婆罗门之子", "沙门", "乔达摩", "迦摩罗", "船夫", "乔文达"):
        assert expected in titles, f"缺少章节：{expected}"

    whole = "\n".join(c.content for c in book.chapters)
    assert "　" not in whole, "正文里还有全角空格"
    assert "[^1]" in whole, "脚注没有转成 markdown 脚注"
    assert ".xhtml" not in whole, "书内链接没有剥干净"

    # EPUB 自带的目录页是导航构件，不该混进正文
    assert "Landmarks" not in whole, "导航文档混进了正文"
    assert "Table of Contents" not in whole, "导航文档混进了正文"

    # 章节正文不该以"完全相同"的标题开头（和 writer 写出的标题重复）。
    # 注意是相等而非包含：《第一部》的正文以 "## Erster Teil 第一部" 开头，
    # 那是书里原本的德中双语部名，属于正文，应当保留。
    import re as _re

    def _norm(s: str) -> str:
        return _re.sub(r"[\s　·:：.。、#]+", "", s)

    for c in book.chapters:
        first = c.content.lstrip().split("\n", 1)[0]
        if first.startswith("#"):
            assert _norm(first) != _norm(c.title), f"《{c.title}》正文开头重复了标题"
