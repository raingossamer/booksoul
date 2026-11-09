"""brain_choices 表的存取。每个查询都带 `user_id`，不靠调用方记得传。

这张表里没有 API key，也没有地址：它只覆盖内置预设的模型与窗口。
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from booksoul_api.models.brain_choice import BrainChoice


class BrainChoiceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def for_user(self, user_id: int) -> dict[str, BrainChoice]:
        """这个人的全部选择，按预设名索引。对不上预设的行也在里面，由调用方忽略。"""
        rows = await self._session.execute(
            select(BrainChoice).where(BrainChoice.user_id == user_id)
        )
        return {row.brain_name: row for row in rows.scalars()}

    async def put(
        self,
        *,
        user_id: int,
        brain_name: str,
        model: str,
        context_tokens: int,
        context_source: str,
    ) -> None:
        """写或覆盖那一行。一条语句做完：先查再写，两个标签页同时保存会撞唯一约束。

        只执行不提交——提交是 service 的事。
        """
        values = {
            "model": model,
            "context_tokens": context_tokens,
            "context_source": context_source,
        }
        await self._session.execute(
            insert(BrainChoice)
            .values(user_id=user_id, brain_name=brain_name, **values)
            .on_conflict_do_update(
                constraint="uq_brain_choices_user_brain", set_=values
            )
        )

    async def remove(self, *, user_id: int, brain_name: str) -> None:
        await self._session.execute(
            delete(BrainChoice).where(
                BrainChoice.user_id == user_id, BrainChoice.brain_name == brain_name
            )
        )
