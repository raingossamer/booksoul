"""注册 / 登录 / 刷新。

这一层回答的是「谁能进来」，不回答「密码长度够不够」——后者在 Pydantic schema
就拦下了，落到这里意味着每个入口都要各写一遍。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from booksoul_api.core.errors import AuthError, ConflictError
from booksoul_api.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from booksoul_api.models.user import User
from booksoul_api.repositories.user_repo import UserRepository
from booksoul_api.schemas.auth import TokenPair

# 一个真实形状的 bcrypt 哈希，专供「查无此人」时空跑一遍校验用。
# 内容是常量 "-" 的哈希，不对应任何账号。
_DUMMY_HASH = hash_password("-")


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._users = UserRepository(session)

    async def register(
        self, *, email: str, password: str, display_name: str = ""
    ) -> tuple[User, TokenPair]:
        if await self._users.get_by_email(email) is not None:
            raise ConflictError("这个邮箱已经注册过了。直接去登录，或者换一个邮箱。")

        user = User(
            email=email,
            password_hash=hash_password(password),
            display_name=display_name,
        )
        await self._users.add(user)
        await self._session.commit()
        return user, _issue(user)

    async def login(self, *, email: str, password: str) -> tuple[User, TokenPair]:
        user = await self._users.get_by_email(email)

        # 用户不存在与密码错误必须给出**同一个**结果。分开报会把「这个邮箱
        # 在不在本站注册过」变成一个可批量查询的接口。
        #
        # 消息一样还不够：查无此人若直接返回，就跳过了 bcrypt 那 ~250ms，
        # 响应快一个数量级，计时本身就是答案。所以这里对着一个固定的假哈希
        # 照样算一遍。这条没有测试——计时断言天生不稳，写了只会时红时绿。
        if user is None:
            verify_password(password, _DUMMY_HASH)
            raise AuthError("邮箱或密码不对。确认一下，或者去注册一个新账号。")
        if not verify_password(password, user.password_hash):
            raise AuthError("邮箱或密码不对。确认一下，或者去注册一个新账号。")

        return user, _issue(user)

    async def refresh(self, *, refresh_token: str) -> TokenPair:
        claims = decode_token(refresh_token, expected="refresh")
        user = await self._users.get_by_id(claims.user_id)
        if user is None:
            raise AuthError("登录状态已失效，请重新登录。")
        return _issue(user)

    async def current_user(self, *, access_token: str) -> User:
        claims = decode_token(access_token, expected="access")
        user = await self._users.get_by_id(claims.user_id)
        if user is None:
            raise AuthError("登录状态已失效，请重新登录。")
        return user


def _issue(user: User) -> TokenPair:
    return TokenPair(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
    )
