"""FastAPI 路由，一个资源一个文件。

只做三件事：绑定参数、调 service、包装响应。不含业务判断，也不直接访问数据库。
"""

from __future__ import annotations
