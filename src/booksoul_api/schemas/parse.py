"""上传与解析进度的响应。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from booksoul_api.schemas import ApiModel


class UploadAccepted(ApiModel):
    """202 的响应体。解析还没开始，先把两个 id 给前端去轮询。"""

    book_id: int
    job_id: int
    # 重解析会波及几个会话。新上传时恒为 0 —— 一本刚传上来的书还没有人跟它聊过。
    # 前端在确认框里先说一次，这里回的是**真正动手那一刻**的数，两者不一定相等
    # （中间可能又开了一个会话），所以两处都给。
    affected_conversations: int = 0


class JobOut(ApiModel):
    """解析进度（界面稿①屏的五阶段条）。

    `stage` 给英文枚举值，中文阶段名由前端映射——枚举值入库与出网都一律英文。
    `remedy` 只放**用户自己能执行**的补救：扫描版 PDF 给 ocrmypdf 命令，
    缺依赖那种该给管理员看的东西不进这里（Spec N1）。
    """

    id: int
    book_id: int
    stage: str
    progress_done: int
    progress_total: int
    detected_format: str
    error_code: str
    error_message: str
    remedy: str
    # 清洗账本：脚注接上几条、图片几张、多少节多少字。②屏的解析报告用它，
    # ①屏完成那一行也顺手显示。
    cleanup_stats: dict[str, Any]
    started_at: datetime | None
    finished_at: datetime | None
