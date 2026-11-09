"""brains 表的存取。

这张表里**没有 API key**——`api_key_env` 存的是环境变量的名字。所以这个文件里
不会出现任何形如「加密后存起来」的代码；要加密就说明存错东西了。

面向单个用户的查询一律在这一层拼 `user_id`，不靠调用方记得传。内置预设那几行
（`user_id IS NULL`）本轮不落库，查询里因此不做 NULL 的特殊处理。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from booksoul_api.models.brain import Brain


class BrainRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, brain: Brain) -> Brain:
        """入库并拿到自增 id。只 flush 不 commit——提交是 service 的事。"""
        self._session.add(brain)
        await self._session.flush()
        return brain

    async def list_for_user(self, user_id: int) -> list[Brain]:
        rows = await self._session.execute(
            select(Brain).where(Brain.user_id == user_id).order_by(Brain.created_at)
        )
        return list(rows.scalars())

    async def get_for_user(self, brain_id: int, user_id: int) -> Brain | None:
        return await self._session.scalar(
            select(Brain).where(Brain.id == brain_id, Brain.user_id == user_id)
        )

    async def get_by_name(self, user_id: int, name: str) -> Brain | None:
        return await self._session.scalar(
            select(Brain).where(Brain.user_id == user_id, Brain.name == name)
        )

    async def delete(self, brain: Brain) -> None:
        await self._session.delete(brain)
