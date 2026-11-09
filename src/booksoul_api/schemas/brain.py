"""大脑的请求与响应（界面稿⑧屏左半）。

这一组 schema 里**没有也不会有 `api_key` 字段**。key 只存在用户浏览器的
localStorage，提问时经 `X-Brain-Key` 头带来、用完即丢；它既不进请求体，
也不进响应体。加一个「方便起见」的 api_key 字段就等于把整条规则作废。
"""

from __future__ import annotations

import re

from pydantic import Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from booksoul_api.schemas import ApiModel

# 换行、制表符这一类。模型名会被存下、显示、再原样发给供应商——混进一个换行，
# 在哪一处出事都说不准，所以在录入时就挡住
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class BrainOut(ApiModel):
    """一个可选的大脑。

    `context_chars` 是**正文额度**（字），不是厂商公布的窗口（token）。
    界面上标的就是这个数，因为它才是「你这本书能送进去多少」的答案；
    折算规则在 `booksoul.ask` 里，选章用的是同一条。
    """

    # 内置预设不在库里，没有 id
    id: int | None
    name: str
    provider: str
    # 实际在用的模型：内置预设 = 用户的选择（没选过就是预设的）
    model: str
    # 预设本来的模型，「恢复默认」回到它。自定义端点没有默认模型，是空串
    default_model: str
    # 用户给这个内置预设换过模型。为真时⑧屏才出现「恢复默认」
    model_chosen: bool
    base_url: str
    api_key_env: str
    note: str
    context_chars: int
    # 存下来的窗口（token），即供应商报的或你填的那个数；换模型时窗口输入框预填它。
    # 来路是内置表或估计时为 0：那两种手里只有字数，不从字倒推 token（有损）
    context_tokens: int
    # 这个额度从哪来：vendor（供应商报的）/ manual（你填的）/ builtin（内置表）/
    # estimate（认不出、按保守值估的）。数字要带着来路走——猜的数和查得到的数
    # 长得一模一样，界面就会带着同样的底气说出两种话。
    context_source: str
    needs_key: bool
    custom: bool

    # 只有请求里带了 bookId 时才有意义。**估计的那一行也是 None**：窗口是猜的，
    # 「装得下」「装不下」都是没有依据的话。两种 None 靠 `context_source` 分开。
    fits: bool | None = None
    fit_note: str = ""


class ModelOut(ApiModel):
    """这个 key 在这家能用的一个模型。**只有 id 与窗口**。

    对方响应里的其他字段（owned_by、permission……）一个都不转回来：列模型会按
    用户存下的自定义地址发请求，原样转回去就是「借服务器之手窥探内网」。
    """

    id: str
    # 供应商在列表里报了窗口（token）才有；0 = 没报
    context_tokens: int = 0


class ModelList(ApiModel):
    items: list[ModelOut]
    # 被问的那个地址。界面上写「共 N 个，来自 {source}」—— 用户要知道这份列表
    # 是不是从他以为的那个服务来的
    source: str


class ModelChoiceIn(ApiModel):
    """给一个大脑换模型（⑧屏「换模型」→ 保存）。

    **在录入的那一刻校验**：一个存进去的坏名字要到下次开会话、真去调模型时才炸，
    那时用户早忘了自己填过什么。报错说清限制，**不回显填进来的值**（全局的 422
    处理器只回 loc / msg / type）——自由文本框里什么都可能被粘进来，包括 key。
    """

    model: str
    # 上下文窗口（token）。0 = 不知道，解析时按内置表或保守估计
    context_tokens: int = 0
    # 窗口的来路：vendor = 列模型时供应商报的；manual = 你填的；窗口为 0 时必须为空
    context_source: str = ""

    @field_validator("model")
    @classmethod
    def _model(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise PydanticCustomError("model_empty", "模型名不能是空的。")
        if len(value) > 64:
            raise PydanticCustomError("model_too_long", "模型名最多 64 个字符。")
        if _CONTROL.search(value):
            raise PydanticCustomError(
                "model_control_char", "模型名里不能有换行、制表符这类控制字符。"
            )
        return value

    @field_validator("context_tokens")
    @classmethod
    def _tokens(cls, value: int) -> int:
        if not 0 <= value <= 10_000_000:
            raise PydanticCustomError(
                "context_tokens_range",
                "上下文窗口填 0 到 1000 万之间的整数（单位 token）；不知道就填 0。",
            )
        return value

    @field_validator("context_source")
    @classmethod
    def _source(cls, value: str) -> str:
        if value not in ("", "vendor", "manual"):
            raise PydanticCustomError(
                "context_source",
                "窗口来源只能是 vendor（供应商报的）、manual（你填的）或空。",
            )
        return value

    @model_validator(mode="after")
    def _source_needs_window(self) -> ModelChoiceIn:
        if self.context_tokens == 0 and self.context_source:
            raise PydanticCustomError(
                "context_source_without_window",
                "窗口是 0 时不能标来源 —— 没有数字，就谈不上是谁给的。",
            )
        return self


class BrainCreate(ApiModel):
    """自己加一个 OpenAI 兼容端点。

    `provider` 不收：能自定义的只有 OpenAI 兼容这一种。anthropic 那条路要官方
    SDK，填一个地址是接不上的，给了这个选项只会让人填出一个连不通的配置。
    """

    name: str = Field(min_length=1, max_length=32)
    model: str = Field(min_length=1, max_length=64)
    base_url: str = Field(min_length=1, max_length=2000)
    # 厂商文档上写的上下文窗口，单位 token。不填就按认不出的模型保守估。
    context_tokens: int = Field(default=0, ge=0, le=10_000_000)
    note: str = Field(default="", max_length=200)
    # 环境变量的**名字**，不是值。留空表示这个端点不需要 key（本地模型常见）。
    api_key_env: str = Field(default="", max_length=64)
