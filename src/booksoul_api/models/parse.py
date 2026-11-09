"""解析任务与解析产生的存疑 / 阻断条目。

两张表回答两个不同的问题：`parse_jobs` 是「这次解析走到哪一步了、停没停」，
`parse_notes` 是「解析成功了，但有几处需要你自己核对」。

后一张表存在的意义就是那句话：**解析成功不等于解析正确**。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ENUM as PGEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from booksoul_api.db.base import Base

# 枚举类型由迁移创建，ORM 只引用不创建
_PARSE_STAGE = PGEnum(
    "read", "toc", "convert", "clean", "write", name="parse_stage", create_type=False
)
_NOTE_SEVERITY = PGEnum("doubt", "block", name="note_severity", create_type=False)

# 五个阶段的先后，进度条按这个顺序画（界面稿①屏）
STAGES = ("read", "toc", "convert", "clean", "write")


class ParseJob(Base):
    __tablename__ = "parse_jobs"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    book_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    stage: Mapped[str] = mapped_column(
        _PARSE_STAGE, nullable=False, server_default="read"
    )
    progress_done: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    progress_total: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    detected_format: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=""
    )
    error_code: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=""
    )
    error_message: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    remedy: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    # 清洗账本：合并了多少硬换行、剥了多少全角空格、脚注接上几条。
    # 这些数字在引擎里是瞬时的，不落库解析报告（②屏）就无从谈起。
    cleanup_stats: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # 可空：排队中的任务还没开跑。与 created_at 分开，孤儿清扫才能区分
    # 「排上队了但后台任务没起来」和「跑起来又停滞」。
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ParseNote(Base):
    __tablename__ = "parse_notes"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    book_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    # SET NULL 而非 CASCADE：重解析会重建 chapters，存疑条目本身不该跟着消失
    chapter_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True
    )
    severity: Mapped[str] = mapped_column(
        _NOTE_SEVERITY, nullable=False, server_default="doubt"
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False, server_default="")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    resolved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
