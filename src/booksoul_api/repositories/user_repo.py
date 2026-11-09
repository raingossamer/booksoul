"""users 表的存取。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from booksoul_api.models.user import User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_email(self, email: str) -> User | None:
        """按邮箱找人。大小写不敏感由列类型 CITEXT 保证，这里不必 lower()。"""
        result = await self._session.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def get_by_id(self, user_id: int) -> User | None:
        return await self._session.get(User, user_id)

    async def add(self, user: User) -> User:
        """入库并拿到自增 id。

        只 flush 不 commit：提交是 service 的事，这里 commit 会让一个用例里的
        多张表写入没法回滚到同一个点。
        """
        self._session.add(user)
        await self._session.flush()
        return user
