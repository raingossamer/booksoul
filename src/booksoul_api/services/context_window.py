"""一个模型能装多少正文，以及这个数是从哪来的（纯逻辑，不碰库、不打网络）。

三级取值，前一级有就不看后一级：

1. **有人给了窗口**（token）：供应商在模型列表里报的，或用户照着对方文档填的。
   按引擎的 `budget_from_context_tokens` 折算——与内置表用的是同一个预留比例。
2. **内置表认得这个模型名**：用引擎那张表的值。
3. **都没有**：保守估计，并且**明说是估计**。

第三级的数和第二级一样会被真正拿去挑章节，区别只在底气。过去两者在界面上
长得一模一样，于是一句「整本装得下」可能是猜的，用户照着它开会话，然后在
供应商那里撞上「上下文超长」。

⑧屏列表、书籍详情的「装得下吗」、开会话时的快照，三处只调这一个函数。
各算各的，「设置页说装得下、提问时却在挑章节」的那天就到了。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from booksoul.ask import (
    budget_from_context_tokens,
    context_budget_chars,
    known_context_chars,
)

Source = Literal["vendor", "manual", "builtin", "estimate"]


@dataclass(frozen=True)
class Window:
    # 正文额度（字），不是窗口（token）：它才是「这本书能送进去多少」的答案
    chars: int
    source: Source


def context_window(model: str, *, tokens: int = 0, source: str = "") -> Window:
    """`tokens` / `source` 是存下来的那一对：窗口为 0 表示没人给过。

    窗口大于 0 却没有来路时当作「你填的」——供应商报的数只会随列表一起来，
    一个没有来路的数只可能是人给的。
    """
    if tokens > 0:
        given: Source = "vendor" if source == "vendor" else "manual"
        return Window(chars=budget_from_context_tokens(tokens), source=given)
    known = known_context_chars(model)
    if known is not None:
        return Window(chars=known, source="builtin")
    return Window(chars=context_budget_chars(model), source="estimate")
