"""选章留档的存取。

留档是**一轮一行**，不是一条回答一行：对读一轮有三条回答却只选一次章。
同一轮「重问」要复用那一行（`ON CONFLICT (conversation_id, turn) DO UPDATE`），
而不是另起一个 turn——另起 turn 会让同一个问题在 `messages` 里留下两条提问，
而会话锁定的本意正是复用不重跑。

这一层照例把 `user_id` 拼进 WHERE。留档挂在会话下面，所以是 JOIN 一次
`conversations` 去对人：查不到与不属于你返回同一个 None，上层转 404。

这个文件只认列名，不认 `Choice`——选章的结论长什么样是 service 的事。
反过来 import 一次 service 就是一条自下而上的边，而 service 正要 import 它。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from booksoul_api.models.conversation import Conversation
from booksoul_api.models.retrieval import Retrieval, RetrievalItem


class RetrievalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(
        self,
        *,
        conversation_id: int,
        turn: int,
        summary: dict[str, Any],
        items: list[dict[str, Any]],
    ) -> Retrieval:
        """写下这一轮的选章决策。只 flush 不 commit——提交是 service 的事。"""
        values = {"conversation_id": conversation_id, "turn": turn, **summary}
        stmt = (
            pg_insert(Retrieval)
            .values(**values)
            .on_conflict_do_update(
                constraint="uq_retrievals_conv_turn", set_=dict(summary)
            )
            .returning(Retrieval.id)
        )
        retrieval_id = await self._session.scalar(stmt)

        # 重问会重算一套名次。旧的整批删掉再写，不做差异更新：
        # (retrieval_id, rank) 上有唯一约束，留着半套旧的必然撞。
        await self._session.execute(
            delete(RetrievalItem).where(RetrievalItem.retrieval_id == retrieval_id)
        )
        self._session.add_all(
            RetrievalItem(retrieval_id=retrieval_id, **item) for item in items
        )
        await self._session.flush()
        return await self._session.get(Retrieval, retrieval_id)

    async def latest_for_user(
        self, conversation_id: int, user_id: int
    ) -> tuple[Retrieval, list[RetrievalItem]] | None:
        """这个会话最近一次**真正做过的**选章决策。

        取最近一次而不是「这一轮」是刻意的：复用锁定的那些轮次根本没有留档，
        它们用的就是这一行的结论。⑥屏问的是「模型手里现在是哪几节」，
        答案正是最后那次决策。
        """
        retrieval = await self._session.scalar(
            select(Retrieval)
            .join(Conversation, Conversation.id == Retrieval.conversation_id)
            .where(
                Retrieval.conversation_id == conversation_id,
                Conversation.user_id == user_id,
            )
            .order_by(Retrieval.turn.desc())
            .limit(1)
        )
        if retrieval is None:
            return None
        rows = await self._session.execute(
            select(RetrievalItem)
            .where(RetrievalItem.retrieval_id == retrieval.id)
            .order_by(RetrievalItem.rank)
        )
        return retrieval, list(rows.scalars())
