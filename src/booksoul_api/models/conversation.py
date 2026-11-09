"""一次读书对话。

`brain_name` 与 `model` 是**冗余存下来的名字，不是外键**：预设被改名或删掉，
都不该篡改历史记录里「这段话当时是谁说的」。

`persona` 是主干人格，可以被切换改写。**不要拿它反推历史轮次属于哪一支**——
哪一路人格答的这一条，记在 `messages.persona` 上（S10 的对读会让这件事变得要紧）。
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
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from booksoul_api.db.base import Base

# create_type=False：这几个枚举由迁移建，ORM 只是引用它们。
# 让 ORM 也去 CREATE TYPE，第二次建表就会撞名。
PERSONA_KEY = ENUM("scholar", "author", "book", name="persona_key", create_type=False)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    book_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    persona: Mapped[str] = mapped_column(
        PERSONA_KEY, nullable=False, server_default="scholar"
    )
    brain_name: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    # 0 是魔法值 = 按所选模型的上下文窗口自动估；非 0 为用户手动指定
    budget_chars: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    # 开会话时快照的正文额度，与⑧屏当时显示的是同一个数。0 = 这一列加上之前
    # 开的会话，提问时走老路径。优先级见 `ChatService.ask` 里取预算那一句。
    context_chars: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    reflection: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    # 第一轮选定后锁定的 chapters.id 数组（S8）。注意引擎的 load_from_path(only_idx=)
    # 收的是 chapters.idx 不是 id，中间必须翻译一次。
    selected_chapter_ids: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # 不配 onupdate：updated_at 由数据库的 touch_updated_at 触发器唯一负责
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
