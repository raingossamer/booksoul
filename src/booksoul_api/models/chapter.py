"""章节索引。

正文在磁盘上，这张表只存定位信息与统计。`idx` 是 1 起的阅读序号，与章节文件名
前缀一致，也是引擎 `load_from_path(only_idx=...)` 收的那个序号——**不是主键 id**，
两者在选章那条路上极易混淆，混了就会读到错位的章节或一节都读不到。

`word_count` 不只是统计，它是质检工具：异常短的章节（如只有 5 字的「第一部」）
要在目录树上标黄提示用户核对，是分部标题页还是丢了正文。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from booksoul_api.db.base import Base


class Chapter(Base):
    __tablename__ = "chapters"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    book_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    idx: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    level: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="1")
    word_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    # 原样照抄的来源定位（epub 是「压缩包里的路径#锚点」），长度由书决定：
    # 原先的 VARCHAR(64) 装不下深一点的路径，整本书因此进不了书库（迁移 0005）
    source_id: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
