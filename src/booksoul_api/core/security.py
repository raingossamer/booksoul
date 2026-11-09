"""密码哈希与 JWT 的底层原语。

放在 core/ 而不是 services/，是因为这里没有任何业务判断：给什么密码算什么哈希，
给什么身份签什么 token。「谁能登录」「token 发给谁」那类规则在 services 层。
这样切分的直接好处是这些函数脱开数据库和 HTTP 就能测。
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from booksoul_api.core.config import get_settings
from booksoul_api.core.errors import AuthError

_ALGORITHM = "HS256"

# 工作因子。Spec N4 定的 12。调高会线性拖慢登录，调低会线性削弱抗爆破。
_BCRYPT_ROUNDS = 12


def _to_bcrypt_input(password: str) -> bytes:
    """把任意长度的密码压成 bcrypt 吃得下的 44 字节。

    bcrypt 只认前 72 字节，且遇到 NUL 字节就截断。这对英文密码几乎碰不到，
    对中文是日常：一个汉字 UTF-8 占 3 字节，25 个汉字就越界了。
    越界的后果分两种，都很糟——bcrypt 4.x 抛 ValueError（用户被挡在门外），
    更早的版本静默截断（前 72 字节相同的两个不同密码被认成同一个，等于认证绕过）。

    先做一次 SHA-256 再 base64，长度恒为 44 字节且不含 NUL，两个坑一起绕开。
    这是 Django 与 passlib 的 bcrypt_sha256 用的同一个办法。
    """
    digest = hashlib.sha256(password.encode("utf-8")).digest()
    return base64.b64encode(digest)


def hash_password(password: str) -> str:
    """把明文密码算成 bcrypt 哈希。数据库里只存这个返回值。"""
    salt = bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
    return bcrypt.hashpw(_to_bcrypt_input(password), salt).decode("ascii")


def verify_password(password: str, hashed: str) -> bool:
    """校验明文密码与哈希是否匹配。"""
    return bcrypt.checkpw(_to_bcrypt_input(password), hashed.encode("ascii"))


@dataclass(frozen=True)
class TokenClaims:
    """一枚 token 解出来的东西。只有身份和类型，没有别的——token 不是缓存。"""

    user_id: int
    type: str


def _create_token(user_id: int, *, type_: str, lifetime: timedelta) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "type": type_,
        "iat": now,
        "exp": now + lifetime,
    }
    return jwt.encode(payload, get_settings().secret_key, algorithm=_ALGORITHM)


def create_access_token(user_id: int) -> str:
    """签一枚短期访问令牌。"""
    minutes = get_settings().access_token_minutes
    return _create_token(user_id, type_="access", lifetime=timedelta(minutes=minutes))


def create_refresh_token(user_id: int) -> str:
    """签一枚长期刷新令牌。它唯一的用途是换新的 access token。"""
    days = get_settings().refresh_token_days
    return _create_token(user_id, type_="refresh", lifetime=timedelta(days=days))


def decode_token(token: str, *, expected: str) -> TokenClaims:
    """解一枚 token，并校验它确实是 `expected` 那一类。

    类型校验不是可选的。access 活 30 分钟、refresh 活 14 天，两者差 672 倍；
    不校验类型，拿 access 去换新 token 就能成功，短期令牌等于长期令牌。
    """
    try:
        payload = jwt.decode(token, get_settings().secret_key, algorithms=[_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise AuthError("登录状态已失效，请重新登录。") from exc

    if payload.get("type") != expected:
        raise AuthError("登录状态已失效，请重新登录。")

    return TokenClaims(user_id=int(payload["sub"]), type=payload["type"])
