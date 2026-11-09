"""溯源（界面稿⑤屏上半、⑦屏答栏）。

两条路由挂在不同的名下，因为它们回答的是两个不同的问题：

* `/api/messages/{id}/citations` —— 这条回答引了什么（⑦屏渲引文块用）
* `/api/citations/{id}` —— 这条引文是从哪儿来的（⑤屏那条来路条用）

后者把「哪个会话、哪个问题」一起带出来，是为了让阅读页的 URL 干净：
`/books/3/read/5?cite=42`，而不是把问题原文塞进查询串——那句话会被浏览器历史、
Referer 和各级访问日志一路抄下去。
"""

from __future__ import annotations

from fastapi import APIRouter

from booksoul_api.deps import CurrentUser, SessionDep
from booksoul_api.schemas.citation import CitationOut
from booksoul_api.services.citation_service import CitationService

router = APIRouter(tags=["citations"])


@router.get("/api/messages/{message_id}/citations", response_model=list[CitationOut])
async def list_citations(
    message_id: int, user: CurrentUser, session: SessionDep
) -> list[CitationOut]:
    """这条回答引了哪些原文。没引原文就是空数组，不是 404。"""
    return await CitationService(session).for_message(
        message_id=message_id, user_id=user.id
    )


@router.get("/api/citations/{citation_id}", response_model=CitationOut)
async def get_citation(
    citation_id: int, user: CurrentUser, session: SessionDep
) -> CitationOut:
    """一条引文的来路：哪本书、哪一节、第几段，以及是哪个问题带过来的。"""
    return await CitationService(session).one(citation_id=citation_id, user_id=user.id)
