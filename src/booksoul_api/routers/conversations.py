"""对话（界面稿⑦屏）。

提问这一条是 SSE：回答逐字流出来，用户不必盯着空白等几十秒。开会话也是——
会话从第一问开始存在，开会话那一下回的就是第一问的流。

用 SSE 不用 WebSocket，理由和解析进度那条一样——单向推送，没有上行消息。
和那条不同的是，这里**不轮询数据库**：token 是内存里的东西，一边流一边写库
就是一次回答几百次写库。真相在 `StreamRecorder` 手上，它在流结束时一次落库。
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Query, Response, status
from fastapi.responses import StreamingResponse

from booksoul_api.deps import BrainKey, CurrentUser, SessionDep
from booksoul_api.schemas.chat import (
    AskRequest,
    ConversationCreate,
    ConversationOut,
    ConversationPatch,
)
from booksoul_api.schemas.retrieval import RetrievalOut
from booksoul_api.services.chat_service import ChatService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


@router.get("", response_model=list[ConversationOut])
async def list_conversations(
    user: CurrentUser,
    session: SessionDep,
    limit: int = Query(default=20, ge=1, le=100),
    book_id: int | None = Query(default=None, alias="bookId"),
) -> list[ConversationOut]:
    """③屏那一栏「最近问过的」；带 `bookId` 是书籍详情那一栏「这本书的会话」。"""
    return await ChatService(session).list_recent(
        user_id=user.id, limit=limit, book_id=book_id
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def start_conversation(
    payload: ConversationCreate,
    user: CurrentUser,
    session: SessionDep,
    brain_key: BrainKey,
) -> StreamingResponse:
    """开会话，连同第一问。回的是第一问的流（201，事件同 `/ask`）。

    把书、人格、大脑一起锁下来，与第一问同一个事务落下：第一问问不出去（没填 key、
    书在解析……），就什么都不留——不会有空会话。

    新会话在哪里看 `Location`。它在流吐出第一个字之前就到了：客户端拿它把地址换成
    这个会话，流还在走时刷新页面，回到的就是这个会话，而不是再开一个、再付一次钱。
    """
    conversation_id, stream = await ChatService(session).begin(
        user_id=user.id,
        book_id=payload.book_id,
        persona=payload.persona,
        brain_name=payload.brain_name,
        question=payload.question,
        brain_key=brain_key,
        compare=payload.compare,
        budget_chars=payload.budget_chars,
        reflection=payload.reflection,
    )
    return _stream(
        stream,
        conversation_id,
        status_code=status.HTTP_201_CREATED,
        location=f"/api/conversations/{conversation_id}",
    )


@router.get("/{conversation_id}", response_model=ConversationOut)
async def get_conversation(
    conversation_id: int, user: CurrentUser, session: SessionDep
) -> ConversationOut:
    """会话详情 + 全部历史消息。

    重连之后前端拉的就是这一条：残缺的那条回答带着 `complete=false` 回来，
    界面据此渲染「这条回答没说完」，而不是把半截话当完整回答显示。
    """
    return await ChatService(session).detail(
        conversation_id=conversation_id, user_id=user.id
    )


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: int, user: CurrentUser, session: SessionDep
) -> Response:
    """删掉一个会话，连同它的问答、引文与选章留档。

    正在回答时 409：收尾要往这个会话里写回答，删在它前面，付过钱的回答就无处可落。
    """
    await ChatService(session).delete(conversation_id=conversation_id, user_id=user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/{conversation_id}", response_model=ConversationOut)
async def patch_conversation(
    conversation_id: int,
    payload: ConversationPatch,
    user: CurrentUser,
    session: SessionDep,
) -> ConversationOut:
    """改会话的两个旋钮，没给的那个不动。

    `persona`：「跟这个继续聊」，对读完挑一路接着往下（S10）。旁支一个字都不删——
    用户要的正是「三种说法我都看过」。

    `reflection`：处境模式（S11）。拨它不废缓存，所以它是一个可以随手拨的开关。
    """
    return await ChatService(session).update(
        conversation_id=conversation_id,
        user_id=user.id,
        persona=payload.persona,
        reflection=payload.reflection,
    )


@router.get("/{conversation_id}/retrieval", response_model=RetrievalOut)
async def get_retrieval(
    conversation_id: int, user: CurrentUser, session: SessionDep
) -> RetrievalOut:
    """⑥屏：这次回答读了什么。

    挂在会话上而不是某条回答上：一轮里三种人格各答一次，却只选一次章。
    返回的是这个会话**最近一次真正做过的**选章决策——后续轮次复用它，
    没有自己的决策可言。
    """
    return await ChatService(session).retrieval(
        conversation_id=conversation_id, user_id=user.id
    )


@router.post("/{conversation_id}/ask")
async def ask(
    conversation_id: int,
    payload: AskRequest,
    user: CurrentUser,
    session: SessionDep,
    brain_key: BrainKey,
) -> StreamingResponse:
    """提问，SSE 流式返回。事件：`stage` / `token` / `done` / `error`。

    `stage` 是「现在在做什么」：书装不下时要先挑章节，那一步几秒内没有任何
    token，界面不能是一片空白——空白和卡死长得一模一样，而用户对卡死的反应
    是再问一遍，那要再花一次钱。

    `await` 在建响应**之前**：鉴权、没填 key、上一轮还没说完、问到了没载入的
    章节，这些都要变成规规矩矩的 4xx。等流开起来再抛，用户看到的就只是一条
    莫名其妙断掉的流。
    """
    stream = await ChatService(session).ask(
        conversation_id=conversation_id,
        user_id=user.id,
        question=payload.question,
        brain_key=brain_key,
        persona=payload.persona,
        reselect=payload.reselect,
        compare=payload.compare,
    )
    return _stream(stream, conversation_id)


def _stream(
    stream,
    conversation_id: int,
    *,
    status_code: int = status.HTTP_200_OK,
    location: str = "",
) -> StreamingResponse:
    headers = {
        "Cache-Control": "no-cache",
        # 有反向代理时它会缓冲整个响应，SSE 就变成了「最后一次性全给」
        "X-Accel-Buffering": "no",
    }
    if location:
        headers["Location"] = location
    return StreamingResponse(
        _events(stream, conversation_id),
        status_code=status_code,
        media_type="text/event-stream",
        headers=headers,
    )


def _event(name: str, payload: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _events(stream, conversation_id: int):
    """把服务层的 (事件名, 载荷) 包成 SSE。

    客户端中途走掉不是错误：生成任务不跟着响应走，它会自己跑完并把内容写下来
    （钱已经花了）。下次打开这个会话就能看到完整回答。
    """
    try:
        async for name, payload in stream:
            yield _event(name, payload)
    except Exception:
        logger.exception("回答流中断：会话 %s", conversation_id)
        yield _event("error", {"message": "这条回答的推送断了。刷新一下页面看看。"})
