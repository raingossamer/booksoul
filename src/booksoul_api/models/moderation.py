"""内容审核的证据台账（S13）。

供应商拒绝时只回一个 400，**不说是哪一段触发的**。但「这一次送了哪几节过去、
结果是拦还是通」是我们自己的账：

    嫌疑 = 每次被拦时都在场的那几节（交集）− 曾经平安送过去的那几节（并集）

两边都需要。只有交集不够——《悉达多》的「扉页」「第一部」各只有几个字，
任何预算都装得下，于是每次选章都在场，交集永远排除不掉它们。

append-only：不更新也不删除，所以没有 `updated_at`，也没有 touch 触发器。
每一行都是一个发生过的事实。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from booksoul_api.db.base import Base


class ModerationEvent(Base):
    __tablename__ = "moderation_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    book_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    brain_name: Mapped[str] = mapped_column(String(32), nullable=False)
    # blocked=这几节送过去被拦了；passed=这几节平安送过去了。
    # passed **只在这本书被这个大脑拦过之后才记**，否则这张表会变成一份对话流水。
    outcome: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default="blocked"
    )
    # 当时送出去的章节 id **快照**。不做关联表：重解析之后这些 id 会不存在，
    # 而这条证据说的正是「当时送的是那几节」。界面拿它去对当下的章节，
    # 对不上的自然不显示——降级是对的。
    chapter_ids: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    # 供应商回的那句话，截断保存。**不存请求体、不存 key。**
    detail: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
