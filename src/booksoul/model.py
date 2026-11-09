"""booksoul 的核心数据模型：一本书被解析成 BookMeta + 若干 Chapter。

所有解析器（epub/pdf/txt/docx/...）无论来源格式如何，最终都产出同一个 Book，
下游的清洗、写盘、人格合成、问答都只认这一个结构。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

# 解析进度回调：(已完成节数, 总节数)。放在这里是因为解析器和调用方都要认识它，
# 而这个模块本来就是「所有层都只认这一个结构」的那一层。
ProgressFn = Callable[[int, int], None]


def count_words(text: str) -> int:
    """中英混排的字数统计：一个汉字算一个字，连续的西文单词算一个词。"""
    cjk = len(re.findall(r"[\u4e00-\u9fff\u3400-\u4dbf]", text))
    latin = len(re.findall(r"[A-Za-z0-9]+(?:['\-][A-Za-z0-9]+)*", text))
    return cjk + latin


@dataclass
class Chapter:
    """一个章节。level 是它在目录树中的深度（1 = 顶层）。"""

    title: str
    content: str  # 已经是 Markdown 正文（不含自己的标题行）
    level: int = 1
    order: int = 0
    source_id: str = ""  # 来源定位，如 epub 的 spine idref、pdf 的页码区间
    # 没载入正文时这一节有多少字，来自书库索引（meta.json）。
    #
    # 按 only_idx 载入的书里，没选中的章节只剩空壳，数出来是 0；拿那个数去说
    # 「全书多少字」，会得到一个**随着「这次载入了哪几节」变来变去的全书字数**。
    # 两个后果都不报错：人格提示词里那句「全书 48,155 字」会缩水成 2 万，
    # 而提示词一变，前缀缓存整个作废——而会话锁定的全部意义正是保住那个缓存。
    stated_words: int = 0

    @property
    def words(self) -> int:
        return count_words(self.content) if self.content else self.stated_words

    def to_markdown(self, base_level: int = 2) -> str:
        """渲染成带标题的 Markdown 片段。base_level 是顶层章节用几号标题。"""
        depth = min(base_level + self.level - 1, 6)
        head = "#" * depth + " " + self.title.strip()
        body = self.content.strip()
        return f"{head}\n\n{body}\n" if body else f"{head}\n"


@dataclass
class BookMeta:
    """书的元信息。字段缺失时一律留空字符串，不编造。"""

    title: str = ""
    author: str = ""
    translator: str = ""  # 仅当来源明确标了译者角色（如 OPF 的 opf:role="trl"）
    contributor: str = ""  # 角色不明的贡献者，原样保留，不猜成译者
    publisher: str = ""
    published: str = ""
    isbn: str = ""
    language: str = ""
    description: str = ""
    source_file: str = ""
    source_format: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v}


@dataclass
class Book:
    meta: BookMeta = field(default_factory=BookMeta)
    chapters: list[Chapter] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # 解析过程中各处记下的账：目录走了哪条路线、剔掉几个目录页、去重了几个标题。
    # 解析报告（②屏）靠它逐条核对——「解析成功不等于解析正确」那句话要能落到数字上。
    # 只记**真的数出来的**东西；数不出来的宁可不出现在报告里，也不编一个。
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def words(self) -> int:
        return sum(c.words for c in self.chapters)

    def display_title(self) -> str:
        return self.meta.title or "未命名书籍"

    def warn(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)

    def note_stat(self, key: str, value: Any = 1) -> None:
        """记一笔。数值型的累加，其余直接覆盖。"""
        if isinstance(value, int) and isinstance(self.stats.get(key), int):
            self.stats[key] += value
        else:
            self.stats[key] = value
