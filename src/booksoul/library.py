"""书库：读回已经解析好的书。

解析和问答是两件事，中间隔着磁盘。这一层负责把 library/ 下的产物读回成 Book，
这样问答不必每次重新解析，也方便你手工修订 chapters/ 里的文字后直接生效。
"""

from __future__ import annotations

import json
import os
import re

from .model import Book, BookMeta, Chapter

DEFAULT_ROOT = "library"

_FIELD_MAP = {
    "书名": "title",
    "作者": "author",
    "译者": "translator",
    "贡献者": "contributor",
    "出版社": "publisher",
    "出版日期": "published",
    "ISBN": "isbn",
    "语言": "language",
    "source_file": "source_file",
    "source_format": "source_format",
}


def root_dir(root: str = "") -> str:
    return root or os.environ.get("BOOKSOUL_LIBRARY", "") or DEFAULT_ROOT


def list_books(root: str = "") -> list[dict]:
    """列出书库里所有书，按书名排序。"""
    base = root_dir(root)
    if not os.path.isdir(base):
        return []
    out = []
    for name in sorted(os.listdir(base)):
        meta_path = os.path.join(base, name, "meta.json")
        if not os.path.isfile(meta_path):
            continue
        try:
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        info = meta.get("书籍", {})
        stats = meta.get("统计", {})
        out.append(
            {
                "目录名": name,
                "书名": info.get("title", name),
                "作者": info.get("author", ""),
                "章节数": stats.get("章节数", len(meta.get("章节", []))),
                "总字数": stats.get("总字数", 0),
                "路径": os.path.join(base, name),
            }
        )
    return out


def find(query: str, root: str = "") -> dict:
    """按书名模糊找一本书。找不到或有歧义时抛出带建议的错误。"""
    books = list_books(root)
    if not books:
        raise FileNotFoundError(
            f"书库是空的（{root_dir(root)}/）。先解析一本：booksoul parse 你的书.epub"
        )
    if not query:
        if len(books) == 1:
            return books[0]
        names = "、".join(b["书名"] for b in books)
        raise ValueError(f"书库里有多本书，请指定书名。现有：{names}")

    q = query.strip().lower()
    exact = [b for b in books if b["书名"].lower() == q or b["目录名"].lower() == q]
    if exact:
        return exact[0]
    partial = [b for b in books if q in b["书名"].lower() or q in b["目录名"].lower()]
    if len(partial) == 1:
        return partial[0]
    if len(partial) > 1:
        names = "、".join(b["书名"] for b in partial)
        raise ValueError(f"{query!r} 匹配到多本：{names}。请写得更具体。")

    names = "、".join(b["书名"] for b in books)
    raise FileNotFoundError(f"书库里没有 {query!r}。现有：{names}")


def load(query: str = "", root: str = "") -> Book:
    """按书名从书库里找一本并读回内存。"""
    info = find(query, root)
    book = load_from_path(info["路径"])
    if not book.meta.title:
        book.meta.title = info["书名"]
    return book


def load_from_path(path: str, *, only_idx: set[int] | None = None) -> Book:
    """从一个具体目录读回 Book，不经书名检索。

    多用户书库里书名不再唯一（两个人各传一本《悉达多》是常态），`find()` 那条
    模糊匹配的路走不通；而调用方手里本来就有确切路径，直接给它。

    `only_idx` 是 1 起的章节序号集合，与 meta.json 里的「序号」同源。给了它就只读
    这几章的文件，**其余章节仍然留在 `book.chapters` 里，只是 content 为空**。

    保留空壳是刻意的：`as_context()` 靠 `book.chapters` 渲染全书目录并给未载入的
    章节打上「本次未载入全文」。若把没选中的整个丢掉，模型会以为手里这几节就是
    全书，然后对着一份残缺目录凭章节名臆测内容——那正是这个产品最不该发生的事。
    """
    with open(os.path.join(path, "meta.json"), encoding="utf-8") as f:
        meta_json = json.load(f)

    meta = BookMeta()
    for cn, attr in _FIELD_MAP.items():
        value = meta_json.get("书籍", {}).get(attr) or meta_json.get("书籍", {}).get(cn)
        if value:
            setattr(meta, attr, value)
    if not meta.title:
        meta.title = os.path.basename(os.path.normpath(path))

    book = Book(meta=meta, warnings=list(meta_json.get("警告", [])))
    read = 0
    for position, record in enumerate(meta_json.get("章节", []), 1):
        idx = int(record.get("序号", position))
        content = ""

        # 「这一章没被选中」和「这一章的文件坏了」是两回事，处理方式也不同：
        # 前者留一个空壳让目录仍然完整，后者整章丢掉（记一笔警告），
        # 否则 as_context 会渲出一个有标题没正文的空段，看着像正文丢了。
        if only_idx is None or idx in only_idx:
            rel = record.get("文件", "")
            full = os.path.join(path, *rel.split("/"))
            if not os.path.isfile(full):
                book.warn(f"章节文件缺失：{rel}")
                continue
            with open(full, encoding="utf-8") as f:
                content = _strip_chapter_header(f.read())
            read += 1

        book.chapters.append(
            Chapter(
                title=record.get("标题", ""),
                content=content,
                level=int(record.get("层级", 1)),
                order=idx - 1,
                source_id=record.get("来源", ""),
                # 索引里记着的字数。没载入正文的那些靠它报数，
                # 否则「全书多少字」会随着这次载入了哪几节而变——见 Chapter.words
                stated_words=int(record.get("字数", 0) or 0),
            )
        )

    if not read:
        raise ValueError(
            f"{book.display_title()} 没有读到任何章节正文。"
            "确认目录里的 chapters/ 还在，或者重新解析一次。"
        )
    book._dir = path  # type: ignore[attr-defined]
    return book


def _strip_chapter_header(text: str) -> str:
    """去掉写盘时加的标题行和那行元信息引用。"""
    text = re.sub(r"\A#\s+.*\n+", "", text)
    text = re.sub(r"\A>\s+.*\n+", "", text)
    return text.strip()


def as_context(book: Book, chapters=None) -> str:
    """拼成送进模型的正文。带目录，让模型知道自己拿到的是全书还是片段。"""
    chosen = list(chapters) if chapters is not None else book.chapters
    partial = len(chosen) < len(book.chapters)
    # 按对象身份建索引。Chapter 是 dataclass，`c in chosen` 走的是逐字段值比较，
    # 章节一多就是 O(n²) 次全文字符串比较——几千章的全集会卡住。
    chosen_ids = {id(c) for c in chosen}

    head = [f"《{book.display_title()}》"]
    if book.meta.author:
        line = f"作者：{book.meta.author}"
        if book.meta.translator:
            line += f"　译者：{book.meta.translator}"
        elif book.meta.contributor:
            line += f"　{book.meta.contributor}"
        head.append(line)
    head.append("")
    head.append("全书目录：")
    for c in book.chapters:
        mark = "" if id(c) in chosen_ids else "　（本次未载入全文）"
        head.append(f"{'  ' * (c.level - 1)}- {c.title}{mark}")
    if partial:
        head += [
            "",
            "注意：受上下文限制，本次只载入了上面标注之外的章节全文。"
            "若回答需要未载入的章节，请明说需要哪一章，不要凭目录名臆测内容。",
        ]
    head.append("")

    body = [c.to_markdown(base_level=1) for c in chosen]
    return "\n".join(head) + "\n" + "\n\n".join(body)
