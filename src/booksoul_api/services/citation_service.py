"""溯源的读取侧：把引文交给⑦屏的答栏和⑤屏的来路条。

写入侧不在这里——引文是**跟着回答一起落库的**（`StreamRecorder._persist`），
同一个事务，不该出现「回答在库里、引文没进去」这种半截状态。这个文件只负责读。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from booksoul_api.core.errors import NotFoundError
from booksoul_api.repositories.citation_repo import CitationRepository, Row
from booksoul_api.schemas.citation import CitationOut


class CitationService:
    def __init__(self, session: AsyncSession) -> None:
        self._citations = CitationRepository(session)

    async def for_message(self, *, message_id: int, user_id: int) -> list[CitationOut]:
        """这条回答引了哪些原文。没引就是空数组，不是 404。"""
        if not await self._citations.message_exists(message_id, user_id):
            # 越权与不存在同一个 404。而「这条回答没引原文」是空数组——
            # 两者混成一个，一个打错的 id 会安静地看起来像「它没引原文」。
            raise NotFoundError("没有这条回答。它可能随会话一起被删掉了。")
        return [
            citation_out(row)
            for row in await self._citations.for_message(message_id, user_id)
        ]

    async def one(self, *, citation_id: int, user_id: int) -> CitationOut:
        """⑤屏那条来路条要的：跳过来之前是哪个问题、引的是哪一段。"""
        row = await self._citations.one(citation_id, user_id)
        if row is None:
            raise NotFoundError("没有这条引文。它可能随会话一起被删掉了。")
        return citation_out(row)


def citation_out(row: Row) -> CitationOut:
    citation, chapter_idx, conversation_id, book_id, question = row
    return CitationOut(
        id=citation.id,
        message_id=citation.message_id,
        quote=citation.quote,
        para_index=citation.para_index,
        located=citation.para_index >= 0,
        # 两个时间戳不等即说明这条引文被 S12 重定位过。用毫秒级的相等去判断
        # 是稳的：触发器只在 UPDATE 时动 updated_at，INSERT 时两者同源。
        relocated=citation.updated_at != citation.created_at,
        chapter_id=citation.chapter_id,
        chapter_idx=chapter_idx,
        chapter_title=citation.chapter_title,
        book_id=book_id,
        conversation_id=conversation_id,
        question=question or "",
        created_at=citation.created_at,
    )
