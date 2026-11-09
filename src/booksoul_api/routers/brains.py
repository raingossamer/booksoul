"""可选的大脑（界面稿⑧屏左半）。

这一组端点**不收也不回 API key**。key 只存在用户浏览器的 localStorage，
提问时经 `X-Brain-Key` 头带来（S7），用完即丢。
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Response, status

from booksoul_api.deps import BrainKey, CurrentUser, SessionDep
from booksoul_api.schemas.brain import BrainCreate, BrainOut, ModelChoiceIn, ModelList
from booksoul_api.services.brain_service import BrainService

router = APIRouter(prefix="/api/brains", tags=["brains"])


@router.get("", response_model=list[BrainOut])
async def list_brains(
    user: CurrentUser,
    session: SessionDep,
    book_id: int | None = Query(default=None, alias="bookId"),
) -> list[BrainOut]:
    """内置预设 + 自己加的端点。

    带上 `bookId` 就多回答一句「这本书装得进它吗」——那个判断用的是选章时
    同一套额度，不在界面里另算一遍。
    """
    return await BrainService(session).list_for_user(user_id=user.id, book_id=book_id)


@router.get("/{name}/models", response_model=ModelList)
async def list_models(
    name: str, user: CurrentUser, session: SessionDep, brain_key: BrainKey
) -> ModelList:
    """这个大脑在这个 key 下有哪些模型（⑧屏「换模型」的下拉）。

    key 经 `X-Brain-Key` 带来、转给供应商、用完即丢——与提问同一个入口。
    按名字定位而不是 id：内置预设不在库里，没有 id。
    """
    return await BrainService(session).list_models(
        user_id=user.id, name=name, api_key=brain_key
    )


@router.put("/{name}/model", response_model=BrainOut)
async def choose_model(
    name: str, payload: ModelChoiceIn, user: CurrentUser, session: SessionDep
) -> BrainOut:
    """给这个大脑换模型并存下来。只影响以后开的会话，已开的会话不变。"""
    return await BrainService(session).set_model(
        user_id=user.id,
        name=name,
        model=payload.model,
        context_tokens=payload.context_tokens,
        context_source=payload.context_source,
    )


@router.delete("/{name}/model", status_code=status.HTTP_204_NO_CONTENT)
async def reset_model(name: str, user: CurrentUser, session: SessionDep) -> Response:
    """恢复默认：模型、窗口、来路全部回到预设。"""
    await BrainService(session).reset_model(user_id=user.id, name=name)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("", response_model=BrainOut, status_code=status.HTTP_201_CREATED)
async def add_brain(
    payload: BrainCreate, user: CurrentUser, session: SessionDep
) -> BrainOut:
    """加一个 OpenAI 兼容端点。"""
    return await BrainService(session).add_custom(user_id=user.id, payload=payload)


@router.delete("/{brain_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_brain(
    brain_id: int, user: CurrentUser, session: SessionDep
) -> Response:
    await BrainService(session).remove(user_id=user.id, brain_id=brain_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
