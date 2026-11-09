"""上下文窗口从哪来：内置 / 估计 / 你填的 / 来自供应商。

⑧屏的「正文额度」和「这本书装得下吗」过去对所有模型说得一样有底气——哪怕那个数
是认不出模型名、按默认值猜的。一句底气十足的「整本装得下」若是猜出来的，比不说更糟：
用户会照着它开会话，然后在供应商那里撞上一个「上下文超长」。

所以数字要带着它的来路一起走。引擎那一侧只多回答一个问题——「这个模型名我认不认得」；
服务那一侧的 `context_window` 把「用户或供应商给的窗口」「内置表」「保守估计」三级
收成一个结论。⑧屏、书籍详情、开会话快照，三处只调它。
"""

from __future__ import annotations

import pytest

from booksoul.ask import context_budget_chars, known_context_chars

# ---------------------------------------------------------------- 引擎


def test_认得的模型给出内置额度_认不得的给None():
    assert known_context_chars("qwen-plus") == 110_000
    # 表里的键是子串：带版本后缀的也认得
    assert known_context_chars("claude-opus-5-20260101") == 900_000
    assert known_context_chars("local-model") is None
    assert known_context_chars("") is None


# 这张表是改动**之前**的输出，逐字抄下来的。`context_budget_chars` 是选章判据的
# 唯一来源：CLI 和每一个既有会话都按它挑章节，结果变一个数，就有一批书悄悄换了读法。
_BEFORE = {
    # 内置表的每一个键
    "claude-opus-5": 900_000,
    "claude-sonnet-5": 900_000,
    "qwen-plus": 110_000,
    "moonshot-v1-128k": 108_000,
    "glm-4": 108_000,
    "gpt-4o": 108_000,
    "deepseek-chat": 54_000,
    "DeepSeek-V3": 54_000,
    "qwen2.5": 27_000,
    # 九个预设实际用的模型名
    "glm-4-plus": 108_000,
    "deepseek-ai/DeepSeek-V3": 54_000,
    "qwen2.5:14b": 27_000,
    "local-model": 100_000,
    # 认不出的名字、子串命中、空串
    "gpt-4o-mini": 108_000,
    "some-unknown-model": 100_000,
    "": 100_000,
}


@pytest.mark.parametrize(("model", "chars"), sorted(_BEFORE.items()))
def test_正文额度对既有输入逐一不变(model, chars):
    assert context_budget_chars(model) == chars


def test_手调的额度仍然盖过一切():
    assert context_budget_chars("qwen-plus", 5_000) == 5_000
    assert context_budget_chars("local-model", 5_000) == 5_000


# ---------------------------------------------------------------- 服务


def test_供应商报了窗口_按它折算并标来自供应商():
    from booksoul.ask import budget_from_context_tokens
    from booksoul_api.services.context_window import context_window

    window = context_window("qwen-max", tokens=32_768, source="vendor")

    # 与自定义端点用的是同一条折算，否则两种大脑的「装不装得下」不是一把尺子
    assert window.chars == budget_from_context_tokens(32_768)
    assert window.source == "vendor"


def test_用户填了窗口_按它折算并标你填的():
    from booksoul.ask import budget_from_context_tokens
    from booksoul_api.services.context_window import context_window

    # 模型名内置表认得也不管用：用户填的数优先，他多半是从对方文档上查来的
    window = context_window("qwen-plus", tokens=1_000_000, source="manual")

    assert window.chars == budget_from_context_tokens(1_000_000)
    assert window.source == "manual"


def test_填了窗口却没说来路_当作你填的():
    from booksoul_api.services.context_window import context_window

    # 一个没有来路的数只可能是人给的：供应商报的数永远是随列表一起来的
    assert context_window("x", tokens=8_000).source == "manual"


def test_没有窗口但内置表认得_用内置值():
    from booksoul_api.services.context_window import context_window

    window = context_window("qwen-plus")

    assert window.chars == known_context_chars("qwen-plus")
    assert window.source == "builtin"


def test_没有窗口也认不出_保守估计并明说是估计():
    from booksoul_api.services.context_window import context_window

    window = context_window("local-model")

    # 估计值就是提问时真正会用的那个数——不在这里另编一个
    assert window.chars == context_budget_chars("local-model")
    assert window.source == "estimate"
