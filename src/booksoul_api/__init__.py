"""booksoul 的 HTTP 层。

引擎（`booksoul`）不知道这一层存在，也不该知道：它是一个能独立当库和 CLI 用的包。
本包依赖引擎，反向不成立。分层是 routers → services → repositories → models，
其中 services 是唯一允许 import booksoul 的层。
"""

from __future__ import annotations
