"""把解析好的 Book 写成书库目录。

输出结构（library/<书名>/）：

    book.md          全书单文件，带 YAML front matter 和目录。上下文放得下时直接整本喂
    INDEX.md         导览：元信息 + 目录树 + 每章字数。人和模型都读它来决定看哪章
    meta.json        结构化元信息 + 章节清单。程序读它做检索和按需加载
    chapters/*.md    分章文件，章节名带序号，便于按需加载单章
    images/          正文引用到的图片

为什么既要 book.md 又要 chapters/：小书直接整本进上下文质量最好；大书只能按章
取，这时 INDEX.md + 单章文件才是正确的粒度。两种都备着，由调用方按预算选。
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime

from .model import Book, Chapter

_WIN_FORBIDDEN = r'[\\/:*?"<>|]'
_WIN_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def slugify(name: str, *, fallback: str = "untitled", maxlen: int = 60) -> str:
    """做成跨平台安全的目录/文件名，中文原样保留。"""
    name = re.sub(_WIN_FORBIDDEN, "", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    name = name[:maxlen].strip(" .")
    if not name or name.upper() in _WIN_RESERVED:
        return fallback
    return name


def _front_matter(book: Book, extra: dict | None = None) -> str:
    data: dict[str, object] = {}
    for key, label in (
        ("title", "书名"),
        ("author", "作者"),
        ("translator", "译者"),
        ("contributor", "贡献者"),
        ("publisher", "出版社"),
        ("published", "出版日期"),
        ("isbn", "ISBN"),
        ("language", "语言"),
    ):
        value = getattr(book.meta, key, "")
        if value:
            data[label] = value
    data["章节数"] = len(book.chapters)
    data["总字数"] = book.words
    data["来源文件"] = book.meta.source_file
    data["来源格式"] = book.meta.source_format
    data["解析时间"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    if extra:
        data.update(extra)

    lines = ["---"]
    for key, value in data.items():
        text = str(value).replace("\n", " ")
        if re.search(r'[:#\-{}\[\]&*!|>%@`"\']', text):
            text = '"' + text.replace('"', '\\"') + '"'
        lines.append(f"{key}: {text}")
    lines.append("---")
    return "\n".join(lines)


def _toc_lines(book: Book, *, link: bool = False) -> list[str]:
    out = []
    for i, c in enumerate(book.chapters, 1):
        indent = "  " * (c.level - 1)
        target = f"chapters/{chapter_filename(c, i)}"
        label = f"[{c.title}]({target})" if link else c.title
        out.append(f"{indent}- {label}　`{c.words:,} 字`")
    return out


def chapter_filename(chapter: Chapter, index: int) -> str:
    return f"{index:03d}-{slugify(chapter.title, fallback=f'chapter{index:03d}', maxlen=40)}.md"


def write_book(
    book: Book, out_root: str, *, images_from=None, dir_name: str = ""
) -> dict:
    """写出一本书，返回写盘摘要。

    `dir_name` 让调用方自己决定目录名。默认（空串）仍按书名取，单人本地用着正好；
    多用户书库下不成立——两个人各传一本《悉达多》会写进同一个目录，后一本静默
    覆盖前一本，所以 Web 层传的是 `<user_id>/<book_id>-<slug>`。
    """
    book_dir = os.path.join(out_root, dir_name or slugify(book.display_title()))
    chap_dir = os.path.join(book_dir, "chapters")
    os.makedirs(chap_dir, exist_ok=True)

    # ---- 分章文件 ----
    chapter_records = []
    for i, c in enumerate(book.chapters, 1):
        fname = chapter_filename(c, i)
        path = os.path.join(chap_dir, fname)
        header = f"# {c.title}\n\n> {book.display_title()}　·　第 {i}/{len(book.chapters)} 节　·　{c.words:,} 字\n"
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(header + "\n" + c.content.strip() + "\n")
        chapter_records.append(
            {
                "序号": i,
                "标题": c.title,
                "层级": c.level,
                "字数": c.words,
                "文件": f"chapters/{fname}",
                "来源": c.source_id,
            }
        )

    # ---- 全书单文件 ----
    parts = [_front_matter(book), "", f"# {book.display_title()}", ""]
    if book.meta.author:
        byline = book.meta.author
        if book.meta.translator:
            byline += f"　著／{book.meta.translator} 译"
        elif book.meta.contributor:
            byline += f"　著／{book.meta.contributor}"
        parts += [f"> {byline}", ""]
    if book.meta.description:
        parts += ["## 内容简介", "", book.meta.description.strip(), ""]
    parts += ["## 目录", "", *_toc_lines(book), "", "---", ""]
    for c in book.chapters:
        parts.append(c.to_markdown(base_level=2))
        parts.append("")
    book_md = "\n".join(parts).rstrip() + "\n"
    book_md = re.sub(r"\n{4,}", "\n\n\n", book_md)
    with open(
        os.path.join(book_dir, "book.md"), "w", encoding="utf-8", newline="\n"
    ) as f:
        f.write(book_md)

    # ---- 导览 ----
    index_md = _render_index(book, chapter_records)
    with open(
        os.path.join(book_dir, "INDEX.md"), "w", encoding="utf-8", newline="\n"
    ) as f:
        f.write(index_md)

    # ---- 元信息 ----
    meta = {
        "书籍": book.meta.to_dict(),
        "统计": {
            "章节数": len(book.chapters),
            "总字数": book.words,
            "最长章节": max((c.words for c in book.chapters), default=0),
            "解析时间": datetime.now().isoformat(timespec="seconds"),
        },
        "章节": chapter_records,
        "警告": book.warnings,
    }
    with open(
        os.path.join(book_dir, "meta.json"), "w", encoding="utf-8", newline="\n"
    ) as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    # ---- 图片 ----
    n_images = 0
    if images_from is not None and hasattr(images_from, "extract_images"):
        n_images = images_from.extract_images(book_dir)
    pending = getattr(book, "_pending_images", None)
    if pending:
        os.makedirs(os.path.join(book_dir, "images"), exist_ok=True)
        for rel, data in pending.items():
            with open(os.path.join(book_dir, *rel.split("/")), "wb") as f:
                f.write(data)
        n_images = len(pending)

    return {
        "目录": book_dir,
        "章节数": len(book.chapters),
        "总字数": book.words,
        "图片数": n_images,
        "警告": book.warnings,
    }


def _render_index(book: Book, records: list[dict]) -> str:
    m = book.meta
    lines = [
        f"# {book.display_title()}　·　阅读导览",
        "",
        "这份导览是给「按需读书」用的：先看目录和字数，再决定打开哪一章。",
        "",
        "## 书籍信息",
        "",
        "| 项 | 值 |",
        "|---|---|",
    ]
    for label, value in (
        ("书名", m.title),
        ("作者", m.author),
        ("译者", m.translator),
        ("贡献者", m.contributor),
        ("出版社", m.publisher),
        ("出版日期", m.published),
        ("ISBN", m.isbn),
        ("语言", m.language),
        ("来源", f"{m.source_file}（{m.source_format}）"),
    ):
        if value:
            lines.append(f"| {label} | {value} |")
    lines += [
        f"| 章节数 | {len(book.chapters)} |",
        f"| 总字数 | {book.words:,} |",
        "",
    ]

    if m.description:
        lines += ["## 内容简介", "", m.description.strip(), ""]

    lines += [
        "## 目录",
        "",
        *_toc_lines(book, link=True),
        "",
        "## 怎么读这本书",
        "",
        f"- **整本读**：`book.md`（约 {book.words:,} 字），上下文放得下时优先用这个，",
        "  前后文完整，回答时能照顾到全书的呼应。",
        "- **按章读**：`chapters/` 下的单章文件，问题只涉及某几章时用，省上下文。",
        "- **程序读**：`meta.json` 里有每章的字数和文件路径，适合做检索和预算控制。",
        "",
    ]

    if book.warnings:
        lines += ["## 解析提示", ""]
        lines += [f"- {w}" for w in book.warnings]
        lines.append("")

    return "\n".join(lines)
