"""会话的那几笔跨切片的写。

`chat_service` 自己直接 select 会话（它手里本来就有整行，再绕一层没有收益），
所以这个仓储只收**别的子系统要动会话时**那几笔——现在只有一笔：重解析之后
清掉选章锁定。放在这里而不是 `book_lifecycle` 里，是因为「哪张表、哪个条件」
这种知识应该收在仓储层，解析那边只需要知道「这本书的锁定要清掉」。
"""

from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from booksoul_api.models.conversation import Conversation


class ConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def count_for_book(self, book_id: int) -> int:
        """这本书有几个会话。重解析前那句「有 N 个会话会受影响」要的就是它。"""
        return (
            await self._session.scalar(
                select(func.count())
                .select_from(Conversation)
                .where(Conversation.book_id == book_id)
            )
        ) or 0

    async def clear_locks_for_book(self, book_id: int) -> int:
        """清掉这本书所有会话的选章锁定，返回动了几行。

        **不带 user_id。** 这一笔跑在解析任务里，那时没有「当前用户」；而书是按
        `user_id` 隔离的，`book_id` 已经把范围钉死了。别的地方要照抄这个写法前
        先确认同样成立——仓储层默认把 `user_id` 拼进 WHERE，这里是有理由的例外。

        锁定存的是**旧的** chapter id。重解析把 chapters 整张重建，那几个 id 已经
        不存在了：不清掉，下一轮会按一份全是死 id 的名单去选章，选中 0 节，
        然后把一本没有正文的书送给模型——那正是 Grill C10 那条连锁的终点。
        """
        result = await self._session.execute(
            update(Conversation)
            .where(Conversation.book_id == book_id)
            .values(selected_chapter_ids=[])
            # 批量 UPDATE 绕过 ORM 的属性赋值，身份映射里那些早先读出来的
            # Conversation 会继续拿着旧值——而 session 工厂配的是
            # `expire_on_commit=False`，连 commit 都不刷。同一个 session 后面
            # 再读一次会读到写之前那一份，而且不报错。
            # "fetch" 让 SQLAlchemy 就地把命中的那几个对象置为过期。
            .execution_options(synchronize_session="fetch")
        )
        return result.rowcount or 0
