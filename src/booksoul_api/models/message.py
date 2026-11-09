"""对话里的一条消息。

三个字段值得在这里说清楚，它们互相咬合，改一个会连带另外两个：

* **`turn`**：一个问题连同它引出的一到三条回答同属一个 turn。对读（S10）一轮出
  三条回答，`turn` 相同而 `seq` 各不相同。
* **`persona`**：user 消息为 NULL，assistant 消息记它是哪一路人格。库里有一条
  CHECK 钉死这个对应——没有它，一条 persona 为 NULL 的 assistant 行会和同一轮的
  提问撞唯一索引，第一条回答就落不了库。
* **`complete`**：false = 断流留下的残缺回答。**残缺行不进那条唯一索引**
  （它带 `WHERE complete`），所以用户点「重问」时同一轮同人格还能再生成一条。
  若把残文也算进唯一约束，服务层就只剩「删掉残文」这一条路——而那恰好毁掉
  「已生成的部分被保住」这条承诺。
"""

from __future__ import annotations

from datetime import datetime

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
    text,
)
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import Mapped, mapped_column

from booksoul_api.db.base import Base
from booksoul_api.models.conversation import PERSONA_KEY

MESSAGE_ROLE = ENUM("user", "assistant", name="message_role", create_type=False)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    turn: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(MESSAGE_ROLE, nullable=False)
    persona: Mapped[str | None] = mapped_column(PERSONA_KEY, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    complete: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    input_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    output_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    # 书放在缓存断点之前，切人格不失效——这一列能看出省了多少
    cache_read: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    cache_write: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    stop_reason: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=""
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # 不配 onupdate：updated_at 由数据库的 touch_updated_at 触发器唯一负责
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
