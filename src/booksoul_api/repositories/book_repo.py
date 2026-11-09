"""books / chapters 两张表的存取。

这个文件里每一句面向单本书的查询都带 `user_id` 条件，而且是在**这一层**拼进去的，
不靠调用方记得传。理由很朴素：漏掉一次就是别人的私人藏书出现在你的列表里，
而「记得过滤」这种约定，写代码的人迟早会忘一次。

查不到与不属于你返回同一个结果（None，上层转 404），不区分——403 等于告诉对方
「这个 id 确实存在，只是不归你」，那本身就是一次信息泄露。
"""

from __future__ import annotations

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from booksoul_api.models.book import Book
from booksoul_api.models.chapter import Chapter


class BookRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, book: Book) -> Book:
        """入库并拿到自增 id。只 flush 不 commit——提交是 service 的事。"""
        self._session.add(book)
        await self._session.flush()
        return book

    async def get_for_user(self, book_id: int, user_id: int) -> Book | None:
        return await self._session.scalar(
            select(Book).where(Book.id == book_id, Book.user_id == user_id)
        )

    async def claim_for_parsing(self, book_id: int, user_id: int) -> bool:
        """把这本书改成「解析中」——只在它此刻不在解析中时。改到了返回 True。

        判断与修改是**同一句** UPDATE（比较交换）：两个并发的请求只有一个拿得到这一行，
        第二个在行锁上等第一个提交，然后按提交后的值重新看条件，那时它已经是 parsing 了。
        先读再改做不到这一点——两个都读到「没在解析」，两个都改。
        """
        claimed = await self._session.execute(
            update(Book)
            .where(
                Book.id == book_id,
                Book.user_id == user_id,
                Book.status != "parsing",
            )
            .values(status="parsing")
            .returning(Book.id)
        )
        return claimed.first() is not None

    async def list_for_user(
        self, user_id: int, *, offset: int, limit: int
    ) -> tuple[list[Book], int]:
        """按 (user_id, created_at DESC) 取一页，连同总数一起返回。"""
        where = Book.user_id == user_id

        total = await self._session.scalar(
            select(func.count()).select_from(Book).where(where)
        )
        rows = await self._session.execute(
            select(Book)
            .where(where)
            .order_by(Book.created_at.desc(), Book.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(rows.scalars()), int(total or 0)

    async def replace_chapters(
        self, book_id: int, records: list[dict]
    ) -> list[Chapter]:
        """整批换掉一本书的章节索引。

        重解析走的也是这条路：章节是解析产物的投影，增量更新没有意义，
        而且 `(book_id, idx)` 上有唯一约束，先删后建最省心。
        """
        await self._session.execute(delete(Chapter).where(Chapter.book_id == book_id))
        rows = [
            Chapter(
                book_id=book_id,
                idx=record["idx"],
                title=record["title"],
                level=record["level"],
                word_count=record["word_count"],
                file_path=record["file_path"],
                source_id=record["source_id"],
            )
            for record in records
        ]
        self._session.add_all(rows)
        # flush 一次拿到自增 id —— 存疑条目要挂到具体章节上，没 id 就挂不上
        await self._session.flush()
        return rows

    async def list_chapters(self, book_id: int) -> list[Chapter]:
        rows = await self._session.execute(
            select(Chapter).where(Chapter.book_id == book_id).order_by(Chapter.idx)
        )
        return list(rows.scalars())
