"""注册 / 登录 / 刷新 的请求与响应。

密码规则写在这里而不是 service 层：校验落在 service 就意味着注册、改密码、
重置密码每个入口都要各写一遍，迟早漏一个。
"""

from __future__ import annotations

from pydantic import EmailStr, Field

from booksoul_api.schemas import ApiModel

# Spec N4：密码最短 8 位。上限 128 只为挡住拿超长串打哈希拖慢服务的玩法——
# bcrypt 的 72 字节限制已在 security.py 用 SHA-256 预哈希绕开，不靠这个上限。
_PASSWORD_MIN = 8
_PASSWORD_MAX = 128


class RegisterRequest(ApiModel):
    email: EmailStr
    password: str = Field(min_length=_PASSWORD_MIN, max_length=_PASSWORD_MAX)
    display_name: str = Field(default="", max_length=64)


class LoginRequest(ApiModel):
    email: EmailStr
    password: str


class RefreshRequest(ApiModel):
    refresh_token: str


class TokenPair(ApiModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserOut(ApiModel):
    id: int
    email: str
    display_name: str
