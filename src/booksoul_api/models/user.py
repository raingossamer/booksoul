"""账号。

这张表——以及整个库——都没有模型 API key 那种东西。用户的 key 只存在他自己浏览器的
localStorage，提问时经 `X-Brain-Key` 头带来，服务器用完即丢。`password_hash` 是账号
密码的 bcrypt 哈希，跟模型 key 无关，别把两者混为一谈。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Identity, String, Text, func
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column

from booksoul_api.db.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    # CITEXT 而非 VARCHAR：登录邮箱大小写不敏感是用户的预期，
    # 放在类型上比在每个查询里记得 lower() 可靠。
    email: Mapped[str] = mapped_column(CITEXT, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=""
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # 不配 onupdate：updated_at 由数据库的 touch_updated_at 触发器唯一负责。
    # 两边都写就是两份真相，而漏刷这件事恰恰是触发器要防的。
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
