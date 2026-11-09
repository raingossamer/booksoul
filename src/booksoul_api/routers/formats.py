"""这个部署的解析能力。

前端的拖放提示文案、文件选择器 accept、上传前的体积与格式校验全部由它驱动，
不在前端硬编码后缀 —— 否则一份没装 pymupdf 的部署会继续对外宣称支持 PDF。
"""

from __future__ import annotations

from fastapi import APIRouter

from booksoul_api.schemas.formats import CapabilityOut, FormatOut
from booksoul_api.services.format_service import capability

router = APIRouter(prefix="/api/formats", tags=["formats"])


@router.get("", response_model=CapabilityOut)
async def get_formats() -> CapabilityOut:
    caps = capability()
    return CapabilityOut(
        max_upload_mb=caps.max_upload_mb,
        formats=[
            FormatOut(
                key=f.key,
                label=f.label,
                extensions=f.extensions,
                available=f.available,
                experimental=f.experimental,
                note=f.note,
            )
            for f in caps.formats
        ],
    )
