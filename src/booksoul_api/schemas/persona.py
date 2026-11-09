"""人格提示词的请求与响应（界面稿⑧屏右栏）。

`text` 在列表里是空串，在详情里才是几千字的正文。同一个模型两种填法，
是因为⑧屏进来只画三个标签页，正文是点开哪个才要哪个。
"""

from __future__ import annotations

from pydantic import Field

from booksoul_api.schemas import ApiModel


class PersonaOut(ApiModel):
    key: str
    name: str
    description: str
    # 磁盘上那个文件，固定形如 `prompts/scholar.md`。界面上用 mono 显示它——
    # 这三个 md 同时也能用编辑器直接改，两条路要对得上号。
    filename: str
    # 列表为空串；详情是原文
    text: str
    # 有没有从界面改过。false 时「还原」没有意义，按钮该是灰的。
    edited: bool


class PersonaUpdate(ApiModel):
    # 上限给得比任何一份现有提示词宽裕得多，但不是没有：这段字会进每一次
    # 请求的 system，一份三十万字的"提示词"意味着每问一句都按那个长度计费。
    text: str = Field(min_length=1, max_length=40_000)
