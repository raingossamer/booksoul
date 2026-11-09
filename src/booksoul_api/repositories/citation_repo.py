"""引文的读取。

照例把 `user_id` 拼进 WHERE，而且是在**这一层**拼。引文挂在消息下、消息挂在
会话下，所以要 JOIN 两次才对得上人——正因为要绕两跳，这个条件才更容易被漏掉，
也更该锁在这里，不靠调用方记得传。

查不到与不属于你返回同一个空结果，上层转 404：403 等于承认这个 id 确实存在。
"""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from booksoul_api.models.chapter import Chapter
from booksoul_api.models.citation import Citation
from booksoul_api.models.conversation import Conversation
from booksoul_api.models.message import Message

# 一条引文在界面上要用到的全部东西：引文本身、它属于哪条回答、
# 那条回答属于哪个会话与哪本书、当时问的是什么、以及章节现在的阅读序号。
Row = tuple[Citation, int | None, int, int, str]


def _query(user_id: int):
    """引文 + 章节序号 + 会话 + 书 + 那一轮的问题。

    `question` 走一个相关子查询而不是再 JOIN 一次 messages：同一个 turn 下
    user 那条只有一行，JOIN 回去要再写一遍 `role = 'user'` 的条件，
    而那个条件一旦漏写就会把三条回答各乘一遍。
    """
    asked = aliased(Message)
    question = (
        select(asked.content)
        .where(
            asked.conversation_id == Message.conversation_id,
            asked.turn == Message.turn,
            asked.role == "user",
        )
        .limit(1)
        .scalar_subquery()
    )
    return (
        select(
            Citation,
            Chapter.idx,
            Conversation.id,
            Conversation.book_id,
            question,
        )
        .join(Message, Message.id == Citation.message_id)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .outerjoin(Chapter, Chapter.id == Citation.chapter_id)
        .where(Conversation.user_id == user_id)
    )


class CitationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def for_message(self, message_id: int, user_id: int) -> list[Row]:
        rows = await self._session.execute(
            _query(user_id)
            .where(Citation.message_id == message_id)
            .order_by(Citation.id)
        )
        return [tuple(r) for r in rows.all()]

    async def for_conversation(self, conversation_id: int, user_id: int) -> list[Row]:
        """一个会话里所有回答的引文，一次取完。

        ⑦屏一屏里有几十条回答，逐条去问就是几十个请求。引文是回答的一部分，
        跟着会话详情一起给出去比让界面自己凑要诚实——也省掉一次
        「有的答栏已经有出处、有的还在转圈」的参差。
        """
        rows = await self._session.execute(
            _query(user_id)
            .where(Message.conversation_id == conversation_id)
            .order_by(Citation.message_id, Citation.id)
        )
        return [tuple(r) for r in rows.all()]

    async def one(self, citation_id: int, user_id: int) -> Row | None:
        row = (
            await self._session.execute(
                _query(user_id).where(Citation.id == citation_id)
            )
        ).first()
        return tuple(row) if row is not None else None

    async def message_exists(self, message_id: int, user_id: int) -> bool:
        """这条回答在不在、归不归你。

        「没有引文」和「没有这条回答」是两件事：前者返回空数组，后者是 404。
        不分开的话，一个打错的 id 会安静地返回 `[]`，看起来像「这条回答没引原文」。
        """
        found = await self._session.scalar(
            select(Message.id)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(Message.id == message_id, Conversation.user_id == user_id)
        )
        return found is not None

    # -------------------------------------------------------- 重解析（S12）

    async def stale_for_book(self, book_id: int) -> list[tuple[Citation, int | None]]:
        """这本书的全部引文，连同它们**当下**落在第几节。

        **必须在 `replace_chapters` 之前调。** `citations.chapter_id` 走
        `ON DELETE SET NULL`，旧章节一删，`Chapter.idx` 这一列就再也读不出来了——
        而重定位报告要靠它分辨「找回来了但位置变了」和「压根没动过」。
        少了这个区分，一本正文一字未改的书会给出 100% 命中率，而那个数字什么
        都没证明：它连一次真正的重定位都没做过。

        返回原始行，不返回 `relocate.Stale`：仓储不认识 service 层的类型
        （那是一条朝上的依赖），组装交给调用方。
        """
        rows = await self._session.execute(
            select(Citation, Chapter.idx)
            .join(Message, Message.id == Citation.message_id)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .outerjoin(Chapter, Chapter.id == Citation.chapter_id)
            .where(Conversation.book_id == book_id)
            .order_by(Citation.id)
        )
        return [(row[0], row[1]) for row in rows.all()]

    async def apply_moves(self, moves: list[tuple[int, int | None, str, int]]) -> int:
        """把重定位的结果写回去，返回动了几行。

        `moves` 是 `(citation_id, chapter_id, chapter_title, para_index)`。
        逐行 UPDATE 而不是一条 CASE 大语句：一本书的引文是几十条量级，
        而 `updated_at` 触发器要逐行触发——界面靠 `updated_at != created_at`
        分辨「当时就定位到的」和「重解析之后找回来的」。
        """
        touched = 0
        for citation_id, chapter_id, chapter_title, para_index in moves:
            result = await self._session.execute(
                update(Citation)
                .where(Citation.id == citation_id)
                .values(
                    chapter_id=chapter_id,
                    chapter_title=chapter_title,
                    para_index=para_index,
                )
                # 见 `conversation_repo.clear_locks_for_book` 上的说明：
                # 批量 UPDATE 不会刷新身份映射里那份旧值。
                .execution_options(synchronize_session="fetch")
            )
            touched += result.rowcount or 0
        return touched
