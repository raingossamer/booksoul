"""这个部署支持哪些格式。"""

from __future__ import annotations

from booksoul_api.schemas import ApiModel


class FormatOut(ApiModel):
    key: str
    label: str
    extensions: list[str]
    #  False 时前端不把它放进 accept，但仍然显示出来并说明「这个部署暂不支持」——
    #  整个藏起来的话，用户拖进去一个 PDF 只会觉得系统坏了。
    available: bool
    experimental: bool
    note: str


class CapabilityOut(ApiModel):
    max_upload_mb: int
    formats: list[FormatOut]
