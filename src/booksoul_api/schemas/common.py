"""跨资源共用的 schema。"""

from __future__ import annotations

from typing import Generic, TypeVar

from booksoul_api.schemas import ApiModel

T = TypeVar("T")

# 列表端点的分页默认值。前端不传就是这一页。
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


class Page(ApiModel, Generic[T]):
    """列表端点的统一响应：`{items, total, page, size}`。

    不做 `{code, message, data}` 那种全局包装——错误用 HTTP 状态码加 `{detail}`
    表达就够了，再包一层只是让每个调用点都得先剥壳。
    """

    items: list[T]
    total: int
    page: int
    size: int
