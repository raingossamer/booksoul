"""注册 / 登录 / 刷新 / 当前用户 这四个端点的行为。

Spec 的测试决策写明本轮不做 API 端点集成测试，但认证是安全边界：
「登录失败不泄露账号是否存在」「access 不能当 refresh 用」这类回归靠手工走查记不住。
所以这里只覆盖认证这四个端点，其余端点仍按 Spec 走手工走查。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from booksoul_api.schemas.auth import RegisterRequest


def test_密码短于八位在schema就被拦下():
    """Spec N4 定的最短 8 位。

    这条必须在 Pydantic schema 完成而不是 service：落到 service 就意味着
    注册、改密码、重置密码每个入口都要记得再写一遍，迟早漏一个。
    """
    with pytest.raises(ValidationError):
        RegisterRequest(email="reader@example.com", password="1234567")

    ok = RegisterRequest(email="reader@example.com", password="12345678")
    assert ok.password == "12345678"


async def test_注册返回一对token(client):
    """贯穿 HTTP → service → repository → PostgreSQL 的第一条路径。

    顺带钉住跨层命名对照：响应里的字段是 camelCase（accessToken），
    Python 侧仍是 snake_case，转换只发生在 Pydantic 序列化这一处。
    """
    r = await client.post(
        "/api/auth/register",
        json={"email": "reader@example.com", "password": "一句足够长的中文密码"},
    )

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["accessToken"]
    assert body["refreshToken"]
    assert body["tokenType"] == "bearer"


async def test_同一个邮箱不能注册两次(client):
    """邮箱是登录账号，重复等于两个人抢同一个身份。"""
    payload = {"email": "reader@example.com", "password": "一句足够长的中文密码"}
    assert (await client.post("/api/auth/register", json=payload)).status_code == 201

    again = await client.post("/api/auth/register", json=payload)

    assert again.status_code == 409
    # Spec N1：面向用户的边界必须给出下一步动作，不能只说「失败了」
    assert "登录" in again.json()["detail"]


async def test_邮箱大小写不敏感(client):
    """注册时打的 Reader@Example.com，登录时多半会打成小写。

    靠的是列类型 CITEXT，不是每个查询记得 lower()——记得这种事总有一天会忘。
    """
    await client.post(
        "/api/auth/register",
        json={"email": "Reader@Example.com", "password": "一句足够长的中文密码"},
    )

    dup = await client.post(
        "/api/auth/register",
        json={"email": "reader@example.com", "password": "另一句足够长的密码"},
    )
    assert dup.status_code == 409

    login = await client.post(
        "/api/auth/login",
        json={"email": "READER@EXAMPLE.COM", "password": "一句足够长的中文密码"},
    )
    assert login.status_code == 200


async def test_登录失败不泄露这个邮箱注册过没有(client):
    """密码错与查无此人必须给同一个答复。

    分开报等于免费送出一个「这个邮箱在不在本站注册过」的批量查询接口——
    对一个存私人藏书的站点来说，这本身就是泄露。
    """
    await client.post(
        "/api/auth/register",
        json={"email": "reader@example.com", "password": "一句足够长的中文密码"},
    )

    wrong_password = await client.post(
        "/api/auth/login",
        json={"email": "reader@example.com", "password": "完全不对的另一个密码"},
    )
    no_such_user = await client.post(
        "/api/auth/login",
        json={"email": "nobody@example.com", "password": "一句足够长的中文密码"},
    )

    assert wrong_password.status_code == no_such_user.status_code == 401
    assert wrong_password.json()["detail"] == no_such_user.json()["detail"]


async def test_带access_token能拿到当前用户_不带则401(client):
    reg = await client.post(
        "/api/auth/register",
        json={
            "email": "reader@example.com",
            "password": "一句足够长的中文密码",
            "displayName": "读者",
        },
    )
    access = reg.json()["accessToken"]

    me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {access}"})
    assert me.status_code == 200
    assert me.json() == {
        "id": me.json()["id"],
        "email": "reader@example.com",
        "displayName": "读者",
    }

    assert (await client.get("/api/auth/me")).status_code == 401


async def test_refresh_能换新access_但access换不了(client):
    """access 活 30 分钟、refresh 活 14 天。

    若 /refresh 不校验 token 类型，拿 access 就能一直续期，那 30 分钟形同虚设。
    """
    reg = await client.post(
        "/api/auth/register",
        json={"email": "reader@example.com", "password": "一句足够长的中文密码"},
    )
    tokens = reg.json()

    ok = await client.post(
        "/api/auth/refresh", json={"refreshToken": tokens["refreshToken"]}
    )
    assert ok.status_code == 200
    assert ok.json()["accessToken"]

    bad = await client.post(
        "/api/auth/refresh", json={"refreshToken": tokens["accessToken"]}
    )
    assert bad.status_code == 401


async def _register(client, email: str, name: str = "") -> dict[str, str]:
    r = await client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": "一句足够长的中文密码",
            "displayName": name,
        },
    )
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['accessToken']}"}


async def test_书库是空的但那是自己的空(client):
    """Story #3 #4：别人看不到我的书。

    这一片还没有上传，所以两个人的书库都是空的——但「空」必须来自一句带了
    user_id 条件的查询，而不是「反正还没人传过书」。这条测试在 S2 之后会自然
    长出牙齿：那时它护住的是「A 传的书不会出现在 B 的列表里」。
    """
    alice = await _register(client, "alice@example.com", "爱丽丝")
    bob = await _register(client, "bob@example.com", "鲍勃")

    for who in (alice, bob):
        r = await client.get("/api/books", headers=who)
        assert r.status_code == 200, r.text
        assert r.json() == {"items": [], "total": 0, "page": 1, "size": 20}

    assert (await client.get("/api/books")).status_code == 401
