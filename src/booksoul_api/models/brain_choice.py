"""用户为内置预设选的模型与窗口。

**这张表只覆盖模型与窗口。** provider、地址、key 名永远从引擎的预设来——所以这里
连放它们的列都没有。与预设按名字弱关联、不做外键：预设不在库里。

**不存 API key。** 这张表里没有一列放得下它。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from booksoul_api.db.base import Base


class BrainChoice(Base):
    __tablename__ = "brain_choices"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    brain_name: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    # 0 = 没人给过窗口，解析时按内置表或保守估计（见 services/context_window）
    context_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    # '' / vendor / manual。窗口为 0 时必须是空串——库里有 CHECK 钉着
    context_source: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default=""
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # 不配 onupdate：updated_at 由数据库的 touch_updated_at 触发器唯一负责
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
