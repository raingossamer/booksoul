"""内容审核拦截台账的读写（S13）。

照例把 `user_id` 拼进 WHERE —— 这张表本身没有 user_id（它挂在书上），
所以要 JOIN 一次 books 才对得上人。正因为要绕一跳，这个条件更该锁在这一层。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from booksoul_api.models.book import Book
from booksoul_api.models.moderation import ModerationEvent

# 供应商那句话截到这么长。留着是为了让用户认得出「哦是这个平台的审核」，
# 不是为了完整复现——那句话里可能被回显任何东西。
_MAX_DETAIL = 500


class ModerationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        book_id: int,
        brain_name: str,
        chapter_ids: list[int],
        detail: str = "",
        outcome: str = "blocked",
    ) -> ModerationEvent:
        """记一次「送了这几节，结果如何」。

        `outcome="blocked"` **只在 `BrainError.kind == "moderation"` 时调**。
        限流、认证失败也是 4xx，混进来的话一次限流就会给这本书挂上一条永久的
        审核警告，而那条警告会把用户支去换大脑——那救不了他。
        """
        event = ModerationEvent(
            book_id=book_id,
            brain_name=brain_name[:32],
            outcome=outcome,
            chapter_ids=sorted({int(i) for i in chapter_ids}),
            detail=detail[:_MAX_DETAIL],
        )
        self._session.add(event)
        await self._session.flush()
        return event

    async def blocked_before(self, book_id: int, brain_name: str) -> bool:
        """这本书被这个大脑拦过没有。

        成功那条路上靠它决定要不要记一笔 `passed`——没被拦过就什么都不记，
        否则这张表会变成一份对话流水。走 `(book_id, brain_name, …)` 那个索引。
        """
        found = await self._session.scalar(
            select(ModerationEvent.id)
            .where(
                ModerationEvent.book_id == book_id,
                ModerationEvent.brain_name == brain_name[:32],
                ModerationEvent.outcome == "blocked",
            )
            .limit(1)
        )
        return found is not None

    async def for_book(self, book_id: int, user_id: int) -> list[ModerationEvent]:
        """这本书被拦过的全部记录，新的在前。不属于这个人就是空的。"""
        rows = await self._session.execute(
            select(ModerationEvent)
            .join(Book, Book.id == ModerationEvent.book_id)
            .where(ModerationEvent.book_id == book_id, Book.user_id == user_id)
            .order_by(ModerationEvent.created_at.desc(), ModerationEvent.id.desc())
        )
        return list(rows.scalars())
