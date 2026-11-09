"""人格提示词的读写（界面稿⑧屏右栏）。

**这是整个 Web 层唯一一处往 `prompts/` 写字的地方。**

单独成模块，不是为了整齐，是因为「把一段文字存进一个文件」在这里有四种各自
独立的坏法，而它们都不显眼：

一、**占位符写坏了，报应在几小时之后。** 模板要过一遍 `str.format()` 填书名、
作者、字数。一份 md 里出现 `{原文}` 或者 `{"答案": ...}` 是再自然不过的事，
而它会在**下一次提问时**抛 KeyError / ValueError——炸在后台任务里，用户看到的
是「这条回答没说完」，跟他半小时前改的那个字看不出任何关系；而且只有那一种
人格会坏，另外两种照常，更像是模型抽风。所以体检要发生在**保存的那一刻**，
并且要点名是哪个占位符。

二、**写到一半断电。** 直接 `open(path, "w")` 会先把文件截断。进程在那一刻
挂掉，剩下的就是一个半截的人格——它不会报错，只会让此后每一次对话都差一点。
所以先写临时文件，再 `os.replace` 整体换过去。

三、**改坏了回不去。** `prompts/` 那三个 md 是这个项目最值钱的东西
（CLAUDE.md 第一条：「要调对话质量就改那三个 md」），而这个目录不在版本控制里。
一次覆写不留退路，等于把它交给了手气。第一次覆写前先把原样存一份基线。

四、**全局共享。** 按 Spec N5，本轮不建 `user_personas` 表：改动对**所有用户**
生效。这条不在代码里体现——代码里根本没有 user_id——所以它只能靠界面说清楚，
以及靠这段注释提醒下一个人：给这里加 user_id 之前，先把 N5 那条裁决翻出来看。

文件仍然是 `prompts/` 下那三个 md，位置由引擎的 `persona.prompt_dir()` 说了算。
这一层不自己拼路径去猜——猜出第二个真相，就会有「CLI 读到 A、网页读到 B」。
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from string import Formatter

from booksoul import persona as persona_mod

# 基线放在 prompts/ 下的一个点开头的子目录：跟着提示词一起走，
# 换 BOOKSOUL_PROMPTS 时不会指着一份别人的基线。
BASELINE_DIR = ".baseline"

# 模板里认得的占位符。和 `persona.Persona.render()` 传的那几个关键字一一对应——
# 那边加一个，这里也得加一个，否则新占位符会被这里判成写错了。
FIELDS = ("title", "author", "byline", "words", "chapters")

# 体检用的假书。值本身无所谓，能让 format() 跑完就行。
_TRIAL = {
    "title": "书",
    "author": "作者",
    "byline": "（无）",
    "words": "0",
    "chapters": 0,
}


class PromptError(ValueError):
    """提示词过不了体检。消息是给用户看的，要点名问题出在哪。"""


@dataclass(frozen=True)
class PromptFile:
    key: str
    name: str
    description: str
    # ⑧屏那行 mono 写的就是它。用户据此知道自己改的是磁盘上哪个文件——
    # 这三个 md 同时也能用编辑器直接改，两条路要对得上号。
    filename: str
    text: str
    # 有没有被从这里改过。true 时「还原」才有意义可言。
    edited: bool


def persona_keys() -> tuple[str, ...]:
    """有哪几种人格。给错误消息列可选值用——Spec N1：面向用户的边界要给出路。"""
    return tuple(persona_mod.PERSONAS)


def personas() -> list[PromptFile]:
    """三种人格的名录，**不带正文**。

    三段提示词加起来六千多字。⑧屏进来先画三个标签页，正文是点开哪个才要哪个。
    """
    return [
        PromptFile(
            key=key,
            name=name,
            description=description,
            filename=_shown_path(key),
            text="",
            edited=_baseline(key).is_file(),
        )
        for key, name, description in persona_mod.list_personas()
    ]


def read(key: str) -> PromptFile:
    """读一种人格的提示词原文。未知 key 抛 KeyError。"""
    name, description = _known(key)
    return PromptFile(
        key=key,
        name=name,
        description=description,
        filename=_shown_path(key),
        text=_path(key).read_text(encoding="utf-8"),
        edited=_baseline(key).is_file(),
    )


def check(text: str) -> None:
    """体检。过不了就抛 `PromptError`，**在任何文件被动之前**。

    先截断再校验的写法，会把一次拼错的占位符变成一次内容丢失。
    """
    if not text.strip():
        raise PromptError("提示词不能是空的。要恢复原样请用「还原」。")

    try:
        fields = [f for _, f, _, _ in Formatter().parse(text) if f is not None]
    except ValueError as exc:
        # "Single '{' encountered in format string" 之类。原话是英文的内部细节，
        # 用户要的是「那我该怎么写」。
        raise PromptError(
            f"花括号没配对（{exc}）。提示词会过一遍占位符替换，"
            "想写一个字面的花括号请连写两个：{{ 和 }}。"
        ) from exc

    known = set(FIELDS)
    for field in fields:
        # "{words:,}" 的 field_name 是 "words"；"{}"→""、"{0}"→"0" 都不合法，
        # 它们在渲染时抛的是 IndexError，一样要在这里拦下。
        base = field.split(".", 1)[0].split("[", 1)[0]
        if base not in known:
            shown = f"{{{field}}}" if field else "{}"
            raise PromptError(
                f"认不出占位符 {shown}。能用的只有："
                + "、".join(f"{{{f}}}" for f in FIELDS)
                + "。想写一个字面的花括号请连写两个：{{ 和 }}。"
            )

    # 最后照着真实那一步跑一遍。上面两条挑的是已知的坏法，这一条挡的是
    # 没想到的那些——它调的就是 `Persona.render()` 里那个 format()。
    try:
        text.format(**_TRIAL)
    except Exception as exc:  # noqa: BLE001 —— 任何跑不通的写法都不该存进去
        raise PromptError(
            f"这段提示词渲染不出来（{exc.__class__.__name__}：{exc}）。"
        ) from exc


def write(key: str, text: str) -> PromptFile:
    """覆写一种人格的提示词。

    顺序是刻意的：**先体检、再留基线、最后换文件**。任何一步失败，磁盘上
    都还是上一刻那个样子。
    """
    _known(key)
    check(text)

    path = _path(key)
    baseline = _baseline(key)
    if not baseline.exists():
        # 第一次从这里改：把当前这一版留下来当退路。
        # 之后再改不刷新它——用户点「还原」时多半已经改了三四轮，
        # 最想要的是最初那一版，不是上一版。
        baseline.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, baseline)

    _atomic_write(path, text)
    return read(key)


def restore(key: str) -> PromptFile:
    """还原成第一次从这里改动之前的那一版。没改过就是原样返回。"""
    _known(key)
    baseline = _baseline(key)
    if baseline.is_file():
        _atomic_write(_path(key), baseline.read_text(encoding="utf-8"))
        # 基线跟着退场：再读一次 `edited` 就该是 false 了
        baseline.unlink()
    return read(key)


# ------------------------------------------------------------------ 内部


def _known(key: str) -> tuple[str, str]:
    if key not in persona_mod.PERSONAS:
        raise KeyError(key)
    return persona_mod.PERSONAS[key]


def _path(key: str) -> Path:
    # 目录由引擎说了算（BOOKSOUL_PROMPTS 优先，其次项目根）。
    # key 已经过白名单，拼不出 ../ 来。
    return Path(persona_mod.prompt_dir()) / f"{key}.md"


def _baseline(key: str) -> Path:
    return Path(persona_mod.prompt_dir()) / BASELINE_DIR / f"{key}.md"


def _shown_path(key: str) -> str:
    """界面上写的那个路径。固定是 `prompts/x.md`，不暴露服务器上的绝对路径。"""
    return f"prompts/{key}.md"


def _atomic_write(path: Path, text: str) -> None:
    """先写临时文件再整体换过去。

    `open(path, "w")` 会先把文件截断。进程在那一刻挂掉，留下的是一个半截的
    人格——它不报错，只让此后每一次对话都差一点。临时文件放同一个目录里，
    `os.replace` 才是同一文件系统内的原子改名。
    """
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
