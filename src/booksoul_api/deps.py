"""依赖注入。

用 FastAPI 的 `Depends` 而不是全局单例，好处是测试里能整条替换掉。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from booksoul_api.core.errors import AuthError
from booksoul_api.db.session import get_session
from booksoul_api.models.user import User
from booksoul_api.services.auth_service import AuthService

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_current_user(
    session: SessionDep,
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    """从 Authorization 头解出当前用户。

    没带头和带了坏头给同一个 401：前者是「还没登录」，后者是「登录过期了或者
    有人在试」，对客户端来说下一步动作是同一个——去登录页。
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AuthError("需要先登录。请带上 Authorization: Bearer <access_token>。")

    token = authorization.split(" ", 1)[1].strip()
    return await AuthService(session).current_user(access_token=token)


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_brain_key(
    x_brain_key: Annotated[str | None, Header()] = None,
) -> str:
    """这次提问要用的模型 API key。

    **这是 key 在服务端唯一的入口，而且它就停在这里。** 取出来交给 service，
    用完即丢：不写库、不写文件、不进日志（`core/logging.py` 是白名单制）、
    不进异常上报（`main.py` 的处理器不带 locals 也不回显请求头）。

    走请求头而不是查询串或请求体，因为那两处都会被顺手记下来——访问日志记 URL，
    调试代码记 body。见 `backend.md`「认证授权」。
    """
    return (x_brain_key or "").strip()


BrainKey = Annotated[str, Depends(get_brain_key)]
