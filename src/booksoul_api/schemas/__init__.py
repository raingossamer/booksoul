"""请求与响应的 Pydantic 模型。

跨层命名对照（`backend.md`）：Python 侧一律 snake_case，与引擎保持一致；
转成 camelCase 只发生在序列化这一处，由 `ApiModel` 的 alias_generator 完成。
前端拿到的就是 camelCase，不再转换。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class ApiModel(BaseModel):
    """所有对外 schema 的基类。

    `populate_by_name=True` 让 Python 侧仍能用 snake_case 构造对象——
    否则 service 层每次组装响应都得写一遍 camelCase，白白多一处翻译。
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
