"""数据存取层。

只做 CRUD 与查询，不含业务判断——「邮箱重复算不算错」这种问题在 services 层回答。
也不自行 commit：事务边界在 services，一个用例一个事务（`backend.md`「注释与事务」）。

一条硬规则：凡是按用户隔离的资源（books / conversations / …），查询必须在这一层
就把 `user_id` 拼进 WHERE，不指望上层记得过滤。
"""

from __future__ import annotations
