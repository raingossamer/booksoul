"""一轮提问的选章决策，以及它下面每一节书的账面。

这两张表是产品的信任基础：**回答基于哪几节，必须可查**。少了它们，
「读了 5/16 节」就只是一句界面上的话，没有任何东西能证实或推翻它。

两个刻意的形状：

**一、挂 `turn` 不挂 `message`。** 对读一轮有三条回答却只选一次章，挂 message
会存三份一模一样的。同一轮「重问」必须复用这一行，不要为重问另起 turn。

**二、没选中的章节也留一行。** 用户要能看到「差 504 字装不下」。只列选中的，
那张表就只能证明「选了什么」，证明不了「凭什么没选别的」——而后者才是他真正
在怀疑的事。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    Numeric,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from booksoul_api.db.base import Base


class Retrieval(Base):
    __tablename__ = "retrievals"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    turn: Mapped[int] = mapped_column(Integer, nullable=False)
    budget_chars: Mapped[int] = mapped_column(Integer, nullable=False)
    used_chars: Mapped[int] = mapped_column(Integer, nullable=False)
    selected_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # 整本都放下了，没走 BM25。界面据此说明模型拿到的是全书，
    # 并且必须说破「这一列 0.00 不是排名」——那一支连分词都没做。
    whole_book: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    query_terms: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RetrievalItem(Base):
    __tablename__ = "retrieval_items"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    retrieval_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("retrievals.id", ondelete="CASCADE"), nullable=False
    )
    # SET NULL 而非 CASCADE：重解析会重建 chapters，CASCADE 会把用户过去所有
    # 对话的检索留档一次清空。
    chapter_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True
    )
    # chapter_id 被置空之后，这两列让排名表仍然读得出「当时读了哪几节」——
    # 那正是这张表存在的理由。快照要在写留档时一并写，不能等到用的时候去 JOIN。
    chapter_idx: Mapped[int] = mapped_column(Integer, nullable=False)
    chapter_title: Mapped[str] = mapped_column(
        String(255), nullable=False, server_default=""
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False, server_default="0"
    )
    word_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    selected: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    matched_terms: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
