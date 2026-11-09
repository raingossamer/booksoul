"""认证的底层原语：密码哈希与 JWT。

这些是纯函数，不碰数据库也不碰 HTTP，所以脱开 FastAPI 就能测。
每条测试对应一个具体的、一旦发生就很难补救的失败。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from booksoul_api.core.config import get_settings
from booksoul_api.core.errors import AuthError
from booksoul_api.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)


def test_密码存的是哈希不是明文():
    """明文密码进库属于那种发生一次就无法补救的事故，必须有测试钉住。"""
    pw = "正确的密码8位"
    h = hash_password(pw)

    assert pw not in h
    assert verify_password(pw, h)
    assert not verify_password("错误的密码", h)


def test_长中文密码不会被bcrypt的七十二字节上限炸掉():
    """一个汉字 UTF-8 占 3 字节，25 个汉字就越过 bcrypt 的 72 字节上限。

    bcrypt 4.x 起超长直接抛 ValueError，更早的版本则静默截断——静默截断更糟：
    前 72 字节相同的两个不同密码会被认成同一个。中文用户随手写一句话就会撞上。
    """
    pw = "我想用一整句中文当作登录密码这样比记一串符号容易得多真的"
    assert len(pw.encode("utf-8")) > 72

    h = hash_password(pw)
    assert verify_password(pw, h)
    # 前 72 字节相同、尾巴不同的密码必须被拒——这正是截断会放过的那一类
    assert not verify_password(pw[:24] + "完全不同的尾巴", h)


def test_access_token_解出来还是同一个用户():
    """token 携带的是身份。解成别人比解不出来危险得多，所以这条要钉死。"""
    claims = decode_token(create_access_token(user_id=7), expected="access")

    assert claims.user_id == 7
    assert claims.type == "access"


def test_access_token_不能当refresh用():
    """两种 token 的寿命差 672 倍（30 分钟 vs 14 天）。

    若解码时不校验类型，一枚 access token 就能当 refresh 用去换新 token，
    「access 只活 30 分钟」这条设计等于不存在。
    """
    token = create_access_token(user_id=7)

    with pytest.raises(AuthError):
        decode_token(token, expected="refresh")


def test_refresh_token_解得回来但同样不能当access用():
    """类型校验必须两个方向都成立，只堵一边等于没堵。"""
    token = create_refresh_token(user_id=7)

    assert decode_token(token, expected="refresh").user_id == 7
    with pytest.raises(AuthError):
        decode_token(token, expected="access")


def _sign(payload: dict, *, key: str | None = None) -> str:
    """绕开本模块的签发函数直接造一枚 token，用来构造那些正常路径造不出的输入。"""
    return jwt.encode(payload, key or get_settings().secret_key, algorithm="HS256")


def test_过期的token会被拒():
    """「access 只活 30 分钟」这条设计，全靠解码时真的去看 exp。"""
    expired = _sign(
        {
            "sub": "7",
            "type": "access",
            "exp": datetime.now(UTC) - timedelta(seconds=1),
        }
    )

    with pytest.raises(AuthError):
        decode_token(expired, expected="access")


def test_用别的密钥签的token不被接受():
    """签名校验是这整套认证唯一的地基。它一旦失效，任何人都能自称任何人。"""
    forged = _sign(
        {
            "sub": "7",
            "type": "access",
            "exp": datetime.now(UTC) + timedelta(minutes=30),
        },
        key="攻击者自己的密钥-必须足够长以免触发告警",
    )

    with pytest.raises(AuthError):
        decode_token(forged, expected="access")
