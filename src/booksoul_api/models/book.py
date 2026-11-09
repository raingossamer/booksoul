"""书库里的一本书。

**正文不在这张表里**，只有元信息和磁盘路径——章节 md 落在
`<书库根>/<storage_path>/chapters/` 下。这是引擎定下的形状：解析产物是给人也给
模型读的 Markdown 文件，塞进数据库会同时失去这两个用途。

`storage_path` 上的 UNIQUE 不是洁癖：目录名由 `<user_id>/<book_id>-<slug>` 组成，
一旦两行撞了同一个路径，后解析的那本会静默覆盖前一本的正文。
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
from sqlalchemy.dialects.postgresql import ENUM as PGEnum
from sqlalchemy.orm import Mapped, mapped_column

from booksoul_api.db.base import Base

# 枚举类型由迁移创建，ORM 只引用不创建——create_type=False 是关键，
# 否则 SQLAlchemy 会在建表时试着再 CREATE TYPE 一次然后撞名。
_BOOK_STATUS = PGEnum(
    "parsing", "ready", "failed", name="book_status", create_type=False
)


class Book(Base):
    __tablename__ = "books"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    author: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    # 译者与贡献者分两列：来源明确标了译者角色才填 translator，
    # 角色不明的一律进 contributor。不猜是这个产品的立场之一。
    translator: Mapped[str] = mapped_column(
        String(255), nullable=False, server_default=""
    )
    contributor: Mapped[str] = mapped_column(
        String(255), nullable=False, server_default=""
    )
    publisher: Mapped[str] = mapped_column(
        String(255), nullable=False, server_default=""
    )
    # 这两列是书里写什么就存什么（dc:date 原文、带前缀的 ISBN），长度由书决定。
    # 原先是 VARCHAR(32)：超了就在写库那一刻整本失败（迁移 0005 放宽成 TEXT）
    published: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    isbn: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    language: Mapped[str] = mapped_column(String(32), nullable=False, server_default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    source_file: Mapped[str] = mapped_column(
        String(255), nullable=False, server_default=""
    )
    source_format: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=""
    )
    # VARCHAR 而非 CHAR：CHAR(64) 是 bpchar，DEFAULT '' 实际落库的是 64 个空格。
    # SQL 侧 = '' 仍为真（bpchar 忽略尾随空格），Python 侧却拿到一串空格：
    # `not v` 为假、`v == ""` 为假，「这本书你传过」的判空分支会静默走错边。
    source_sha256: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=""
    )
    # 可空：目录名是 <user_id>/<book_id>-<slug>，含自身主键，而 id 要 INSERT
    # 之后才有。建行时填不出来，解析写盘后再 UPDATE 补上。
    storage_path: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    chapter_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    word_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    status: Mapped[str] = mapped_column(
        _BOOK_STATUS, nullable=False, server_default="parsing"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # 不配 onupdate：updated_at 由数据库的 touch_updated_at 触发器唯一负责。
    # 两边都写就是两份真相，而漏刷这件事恰恰是触发器要防的。
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
