"""解析任务的进度：轮询一条、SSE 一条。

两条路**读的是同一份数据库记录**，所以不会给出互相矛盾的答案。怎么读、什么时候推、
推到什么时候为止，都在 `BookLifecycle.job()` / `watch()`；这里只剩 SSE 的字节拼装。

用 SSE 不用 WebSocket：解析进度是单向推送，没有上行消息。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from booksoul_api.deps import CurrentUser, SessionDep
from booksoul_api.schemas.parse import JobOut
from booksoul_api.services.book_lifecycle import BookLifecycle

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

_NO_BUFFER = {
    "Cache-Control": "no-cache",
    # 有反向代理时它会缓冲整个响应，SSE 就变成了「最后一次性全给」
    "X-Accel-Buffering": "no",
}


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: int, user: CurrentUser, session: SessionDep) -> JobOut:
    return await BookLifecycle(session).job(job_id=job_id, user_id=user.id)


@router.get("/{job_id}/events")
async def job_events(
    job_id: int, user: CurrentUser, session: SessionDep
) -> StreamingResponse:
    """SSE 推解析进度。没有 / 不是你的在开流之前就 404。"""
    events = await BookLifecycle(session).watch(job_id=job_id, user_id=user.id)
    return StreamingResponse(
        _sse(events), media_type="text/event-stream", headers=_NO_BUFFER
    )


async def _sse(events: AsyncIterator[tuple[str, dict]]):
    async for name, payload in events:
        yield f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
