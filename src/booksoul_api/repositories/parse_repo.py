"""parse_jobs / parse_notes 的存取。

任务的查询一律经 books 连回 user_id：进度条也是资源，别人的解析进度不该被看见。
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import Row, delete, func, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from booksoul_api.models.book import Book
from booksoul_api.models.parse import ParseJob, ParseNote


class ParseRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_job(self, job: ParseJob) -> ParseJob:
        self._session.add(job)
        await self._session.flush()
        return job

    async def get_job(self, job_id: int) -> ParseJob | None:
        return await self._session.get(ParseJob, job_id)

    async def get_job_for_user(self, job_id: int, user_id: int) -> ParseJob | None:
        """连回 books 拿 user_id —— 进度也是资源，不该被别人看见。"""
        return await self._session.scalar(
            select(ParseJob)
            .join(Book, Book.id == ParseJob.book_id)
            .where(ParseJob.id == job_id, Book.user_id == user_id)
        )

    async def latest_job_for_book(self, book_id: int) -> ParseJob | None:
        return await self._session.scalar(
            select(ParseJob)
            .where(ParseJob.book_id == book_id)
            .order_by(ParseJob.created_at.desc(), ParseJob.id.desc())
            .limit(1)
        )

    async def book_states(self, user_id: int, book_ids: Sequence[int]) -> list[Row]:
        """这几本书各自的状态，连同**最近一次**解析任务——一条查询取齐（LATERAL）。

        列级查询，不经身份映射：进度是后台任务在另一条 session 里写的，请求这边手上的
        ORM 对象不会自己变（`backend.md`「写发生在别处时，手上那个 ORM 对象就是旧的」）。
        一页书逐本去查是 N+1。没有任务的书（从没排过解析）那几列是 NULL。
        """
        if not book_ids:
            return []
        latest = (
            select(
                ParseJob.id,
                ParseJob.stage,
                ParseJob.progress_done,
                ParseJob.progress_total,
                ParseJob.error_code,
                ParseJob.error_message,
                ParseJob.remedy,
            )
            .where(ParseJob.book_id == Book.id)
            .order_by(ParseJob.created_at.desc(), ParseJob.id.desc())
            .limit(1)
            .lateral("latest")
        )
        rows = await self._session.execute(
            select(
                Book.id.label("book_id"),
                Book.status,
                Book.storage_path,
                latest.c.id.label("job_id"),
                latest.c.stage,
                latest.c.progress_done,
                latest.c.progress_total,
                latest.c.error_code,
                latest.c.error_message,
                latest.c.remedy,
            )
            .outerjoin(latest, true())
            .where(Book.user_id == user_id, Book.id.in_(book_ids))
        )
        return list(rows.all())

    async def replace_notes(self, book_id: int, notes: list[ParseNote]) -> None:
        """整批换掉一本书的存疑条目。重解析时旧条目不再成立，留着只会误导。"""
        await self._session.execute(
            delete(ParseNote).where(ParseNote.book_id == book_id)
        )
        self._session.add_all(notes)
        await self._session.flush()

    async def unresolved_counts(self, book_ids: list[int]) -> dict[int, int]:
        """一次查出这一批书各有几条待核对。

        分开逐本查会变成 N+1：一页 20 本就是 21 次往返。
        """
        if not book_ids:
            return {}
        rows = await self._session.execute(
            select(ParseNote.book_id, func.count())
            .where(ParseNote.book_id.in_(book_ids), ParseNote.resolved.is_(False))
            .group_by(ParseNote.book_id)
        )
        return {book_id: count for book_id, count in rows.all()}

    async def list_notes(self, book_id: int, *, unresolved_only: bool = False):
        stmt = select(ParseNote).where(ParseNote.book_id == book_id)
        if unresolved_only:
            stmt = stmt.where(ParseNote.resolved.is_(False))
        rows = await self._session.execute(stmt.order_by(ParseNote.id))
        return list(rows.scalars())
