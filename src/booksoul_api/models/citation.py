"""溯源：一条回答引了哪一章哪一段。

这张表要撑住的是一句承诺：**回答里的引文，用户能点回去亲眼核对。**

三个字段的取值规则比字段本身重要：

- `para_index = -1` 是「对不上」，不是「第 -1 段」。落库时比对不上就填它，
  **不猜**——一条定位错了的引文比没有引文糟得多，它把用户送到一段他没引的话
  前面，而那段话看起来完全像是出处。
- `chapter_id` 走 `ON DELETE SET NULL`：重解析会重建 chapters，引文降级成
  「原位置已变更」是可接受的，凭空消失不是。
- `chapter_title` 是当时的快照。`chapter_id` 被置空之后，界面还能说出
  「原《婆罗门之子》里这段，位置已变更」，而不是只剩一个灰按钮。

`updated_at` 与 `created_at` 不等，即说明这条引文被 S12 的 CitationRelocator
重定位过。界面据此区分「当时就定位到的」和「重解析之后重新找回来的」。
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


class Citation(Base):
    __tablename__ = "citations"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    message_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
    )
    chapter_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True
    )
    chapter_title: Mapped[str] = mapped_column(
        String(255), nullable=False, server_default=""
    )
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    # -1 = 未能定位。落库时比对得出，对不上就填 -1，不猜。
    para_index: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="-1"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # 不配 onupdate：updated_at 由数据库的 touch_updated_at 触发器唯一负责
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
