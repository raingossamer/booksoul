"""人格：把一本书变成一个能对话的对象。

三种人格放在 prompts/ 下的 Markdown 里，而不是写死在代码里——它们是这个项目
真正需要反复打磨的东西，改 prompt 不该动代码。

占位符用 {} 包起来，由 render() 用书的元信息填上。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

PERSONAS = {
    "scholar": ("研读者", "把书读透的人。引原文、分清事实与推演、直说书的边界。"),
    "author": ("作者", "作者本人。用他的语气和思想框架，和读者谈他自己的困惑。"),
    "book": ("书", "书本身。只有自己这些字，知道内部的呼应，诚实说出局限。"),
}

DEFAULT_PERSONA = "scholar"

_PROMPT_DIR_ENV = "BOOKSOUL_PROMPTS"


def prompt_dir() -> str:
    """prompts/ 的位置：环境变量优先，其次项目根目录。"""
    override = os.environ.get(_PROMPT_DIR_ENV)
    if override:
        return override
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))  # src/booksoul -> 项目根
    return os.path.join(root, "prompts")


@dataclass
class Persona:
    key: str
    name: str
    description: str
    template: str

    def render(self, meta, *, words: int, chapters: int) -> str:
        byline_parts = []
        if getattr(meta, "translator", ""):
            byline_parts.append(f"{meta.translator} 译")
        elif getattr(meta, "contributor", ""):
            byline_parts.append(meta.contributor)
        if getattr(meta, "publisher", ""):
            byline_parts.append(meta.publisher)
        if getattr(meta, "published", ""):
            byline_parts.append(str(meta.published))

        return self.template.format(
            title=meta.title or "这本书",
            author=meta.author or "佚名",
            byline="；".join(byline_parts) or "（无）",
            words=f"{words:,}",
            chapters=chapters,
        )


def load(key: str = DEFAULT_PERSONA) -> Persona:
    if key not in PERSONAS:
        raise ValueError(f"未知人格 {key!r}。可选：{'、'.join(PERSONAS)}")
    path = os.path.join(prompt_dir(), f"{key}.md")
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"找不到人格模板：{path}\n"
            f"（可用环境变量 {_PROMPT_DIR_ENV} 指定 prompts 目录）"
        )
    with open(path, encoding="utf-8") as f:
        template = f.read()
    name, description = PERSONAS[key]
    return Persona(key=key, name=name, description=description, template=template)


def list_personas() -> list[tuple[str, str, str]]:
    return [(k, n, d) for k, (n, d) in PERSONAS.items()]


# 读者带着自己的处境来提问时，附加的一段指令。
# 单独拎出来是因为它跨人格通用，而且是这个项目区别于"问答机器人"的地方。
REFLECTION_SUFFIX = """
---

读者这次带来的不是一个关于书的知识问题，而是他自己的处境。

注意：
- 先弄清他实际卡在哪里，不要把他的话套进书里现成的框子。
- 回应要能落回他的具体处境，而不是一段可以贴给任何人的道理。
- 书能照亮的就照亮，照不到的就说照不到。不要为了给一个完整的答案而越界。
"""
