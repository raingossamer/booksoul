"""业务编排层。

这是**唯一允许 import booksoul 引擎**的层：routers 只做参数绑定与响应包装，
repositories 只做数据存取，两者都不得直接碰引擎。
跨表事务也在这一层收口——一个用例一个事务。
"""

from __future__ import annotations
