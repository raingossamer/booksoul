"""大脑预设。

**这张表不存 API key，一个字节都不存。** 存的只有「需要哪个环境变量」这种名字
（`api_key_env`），以及端点地址与模型名。库里那两条 CHECK 是这句话的执行者——
注释挡不住 INSERT。

`user_id` 为 NULL 表示内置全局预设。本轮内置预设的真相在引擎的
`booksoul.config.PRESETS` 里，这张表只装用户自己加的 OpenAI 兼容端点；
两边同时当真相会打架（见 `services/brain_service.py` 的说明）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from booksoul_api.db.base import Base


class Brain(Base):
    __tablename__ = "brains"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    base_url: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    # 只存环境变量的【名字】（如 DASHSCOPE_API_KEY），绝不存值
    api_key_env: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=""
    )
    # 厂商公布的上下文窗口，单位 token。折算成正文字数额度由
    # `booksoul.ask.budget_from_context_tokens` 唯一负责。
    context_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    max_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="8000"
    )
    note: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # 不配 onupdate：updated_at 由数据库的 touch_updated_at 触发器唯一负责
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
