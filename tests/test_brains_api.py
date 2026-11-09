"""可选的大脑：内置预设 + 用户自己加的 OpenAI 兼容端点（界面稿⑧屏左半）。

这一屏回答两个问题：**接哪个模型**，以及**这本书装不装得进它**。
第二个问题的答案必须和选章时用的判据同源，否则会出现「设置页说装得下、
提问时却在挑章节」这种自己打自己的情形。
"""

from __future__ import annotations

from sqlalchemy import select

from booksoul_api.models.book import Book
from booksoul_api.models.user import User


async def _token(client, email: str = "reader@example.com") -> str:
    r = await client.post(
        "/api/auth/register", json={"email": email, "password": "一句足够长的中文密码"}
    )
    assert r.status_code == 201, r.text
    return r.json()["accessToken"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_列出可选的大脑_带上下文额度与需要哪个key(client):
    token = await _token(client)
    r = await client.get("/api/brains", headers=_auth(token))

    assert r.status_code == 200, r.text
    brains = {b["name"]: b for b in r.json()}
    assert {"claude", "deepseek", "kimi", "ollama"} <= set(brains)

    claude = brains["claude"]
    assert claude["provider"] == "anthropic"
    assert claude["model"].startswith("claude")
    assert claude["apiKeyEnv"] == "ANTHROPIC_API_KEY"
    assert claude["needsKey"] is True
    assert claude["custom"] is False
    assert claude["id"] is None  # 内置预设不在库里

    # 本地端点不需要 key —— 界面上那一格写「本地 · 无需 key」而不是留空
    assert brains["ollama"]["needsKey"] is False

    # 各家的上下文不该长得一模一样：一样就说明用的是兜底值，那个数字没有信息量
    windows = {b["name"]: b["contextChars"] for b in r.json()}
    assert windows["claude"] > windows["deepseek"]
    assert len(set(windows.values())) > 2

    # 响应里绝不出现 key 本身，连字段都不该有
    assert "apiKey" not in claude


async def _book(db_session, email: str, *, word_count: int = 48_155) -> Book:
    user = await db_session.scalar(select(User).where(User.email == email))
    book = Book(
        user_id=user.id,
        title="悉达多",
        source_file="悉达多.epub",
        source_format="epub",
        status="ready",
        storage_path=f"{user.id}/1-悉达多",
        chapter_count=16,
        word_count=word_count,
    )
    db_session.add(book)
    await db_session.commit()
    return book


async def test_给定一本书就回答装不装得下并说清依据(client, db_session):
    token = await _token(client)
    book = await _book(db_session, "reader@example.com")

    r = await client.get(f"/api/brains?bookId={book.id}", headers=_auth(token))
    assert r.status_code == 200, r.text
    brains = {b["name"]: b for b in r.json()}

    assert brains["claude"]["fits"] is True
    assert "装得下" in brains["claude"]["fitNote"]

    # 装不下的那一档要说清差多少，光说「装不下」没法让人做决定
    tight = [b for b in r.json() if b["fits"] is False]
    assert tight, "48,155 字应该有装不下的大脑"
    assert "%" in tight[0]["fitNote"] or "挑章节" in tight[0]["fitNote"]


async def test_别人的书拿来问装不装得下_一律404(client, db_session):
    """越权与不存在同一个 404：403 等于承认这本书确实存在。"""
    await _token(client, "owner@example.com")
    book = await _book(db_session, "owner@example.com")
    stranger = await _token(client, "stranger@example.com")

    r = await client.get(f"/api/brains?bookId={book.id}", headers=_auth(stranger))
    assert r.status_code == 404
    assert (
        await client.get("/api/brains?bookId=99999", headers=_auth(stranger))
    ).status_code == 404


async def test_加一个自定义的openai兼容端点(client):
    token = await _token(client)
    r = await client.post(
        "/api/brains",
        headers=_auth(token),
        json={
            "name": "我的推理机",
            "model": "qwen3-32b",
            "baseUrl": "http://192.168.1.9:8000/v1",
            "contextTokens": 32768,
            "note": "书房那台机器",
        },
    )
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["custom"] is True
    assert created["id"]
    assert created["provider"] == "openai"
    # 额度按上下文窗口折算，不是原样照抄 token 数
    assert 0 < created["contextChars"] <= 32768

    listed = {
        b["name"]: b
        for b in (await client.get("/api/brains", headers=_auth(token))).json()
    }
    assert "我的推理机" in listed
    assert listed["我的推理机"]["baseUrl"] == "http://192.168.1.9:8000/v1"


async def test_自定义端点只属于加它的人(client):
    owner = await _token(client, "owner@example.com")
    await client.post(
        "/api/brains",
        headers=_auth(owner),
        json={
            "name": "书房那台",
            "model": "qwen3-32b",
            "baseUrl": "http://10.0.0.2:8000/v1",
        },
    )
    stranger = await _token(client, "stranger@example.com")

    names = {
        b["name"]
        for b in (await client.get("/api/brains", headers=_auth(stranger))).json()
    }
    assert "书房那台" not in names


async def test_删掉自己加的端点(client):
    token = await _token(client)
    created = (
        await client.post(
            "/api/brains",
            headers=_auth(token),
            json={"name": "临时的", "model": "x", "baseUrl": "http://127.0.0.1:1/v1"},
        )
    ).json()

    r = await client.delete(f"/api/brains/{created['id']}", headers=_auth(token))
    assert r.status_code == 204

    names = {
        b["name"]
        for b in (await client.get("/api/brains", headers=_auth(token))).json()
    }
    assert "临时的" not in names

    # 内置预设不是用户的东西，删不掉也不该假装删掉了
    r = await client.delete("/api/brains/999999", headers=_auth(token))
    assert r.status_code == 404


async def test_端点地址里夹带key会被拒绝并说清key该填在哪(client):
    token = await _token(client)
    r = await client.post(
        "/api/brains",
        headers=_auth(token),
        json={
            "name": "夹带的",
            "model": "gpt-4o",
            "baseUrl": "https://api.example.com/v1?api-key=sk-abc123DEF456ghi789jkl",
        },
    )
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert "key" in detail.lower()
    # N1：面向用户的边界要给出下一步动作
    assert "浏览器" in detail


async def test_想把真key填进环境变量名那一栏也会被拒(client):
    """`api_key_env` 存的是**名字**（DASHSCOPE_API_KEY），不是值。

    库里有一条 CHECK 兜底，但用户看到的不该是一条 IntegrityError。
    """
    token = await _token(client)
    r = await client.post(
        "/api/brains",
        headers=_auth(token),
        json={
            "name": "填错了",
            "model": "gpt-4o",
            "baseUrl": "https://api.example.com/v1",
            "apiKeyEnv": "sk-ant-api03-AAAABBBBCCCC",
        },
    )
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert "环境变量" in detail
    # 而且**不能把那串东西抄回来**。这条分支存在的理由就是「有人往这栏粘了 key」，
    # 原样回显等于把它送进前端的错误提示、控制台和任何错误上报管道。
    assert "sk-ant-api03-AAAABBBBCCCC" not in detail


async def test_同名端点加两次给的是冲突而不是崩(client):
    token = await _token(client)
    payload = {"name": "重名", "model": "x", "baseUrl": "http://127.0.0.1:1/v1"}
    assert (
        await client.post("/api/brains", headers=_auth(token), json=payload)
    ).status_code == 201
    r = await client.post("/api/brains", headers=_auth(token), json=payload)
    assert r.status_code == 409, r.text
    assert "重名" in r.json()["detail"]


async def test_自定义端点不能盖掉内置预设的名字(client):
    token = await _token(client)
    r = await client.post(
        "/api/brains",
        headers=_auth(token),
        json={"name": "claude", "model": "x", "baseUrl": "http://127.0.0.1:1/v1"},
    )
    assert r.status_code == 409, r.text


async def test_自己加的远程端点默认当作要key_本机的不要(client):
    """判错的两个方向代价差得很远：多一个填了没用的框只是噪音，
    少一个该有的框就是一条没有出路的死胡同。"""
    token = await _token(client)
    for name, url in [
        ("远的", "https://api.example.com/v1"),
        ("近的", "http://localhost:8000/v1"),
    ]:
        r = await client.post(
            "/api/brains",
            headers=_auth(token),
            json={"name": name, "model": "x", "baseUrl": url},
        )
        assert r.status_code == 201, r.text

    listed = {
        b["name"]: b
        for b in (await client.get("/api/brains", headers=_auth(token))).json()
    }
    assert listed["远的"]["needsKey"] is True
    assert listed["近的"]["needsKey"] is False


async def _add_endpoint(client, token: str, name: str, model: str, tokens: int):
    r = await client.post(
        "/api/brains",
        headers=_auth(token),
        json={
            "name": name,
            "model": model,
            "baseUrl": "http://192.168.1.9:8000/v1",
            "contextTokens": tokens,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_每一行都说清额度是从哪来的(client):
    """内置表认得 →「内置」；自己填了窗口 →「你填的」；都没有 →「估计」。

    过去这三种在界面上长得一模一样，于是一个猜出来的数和一个查得到的数
    带着同样的底气。
    """
    token = await _token(client)
    filled = await _add_endpoint(client, token, "填了窗口的", "my-model", 32_768)
    blank = await _add_endpoint(client, token, "没填窗口的", "my-model", 0)
    assert filled["contextSource"] == "manual"
    assert blank["contextSource"] == "estimate"

    brains = {
        b["name"]: b
        for b in (await client.get("/api/brains", headers=_auth(token))).json()
    }
    assert brains["qwen"]["contextSource"] == "builtin"
    assert brains["claude"]["contextSource"] == "builtin"
    # lmstudio 的模型名是占位的 local-model，内置表认不出——如实说是估计
    assert brains["lmstudio"]["contextSource"] == "estimate"
    assert brains["填了窗口的"]["contextSource"] == "manual"
    assert brains["没填窗口的"]["contextSource"] == "estimate"


async def test_估计的那一行不下装不装得下的结论(client, db_session):
    """窗口是猜的，「整本装得下」就是一句没有依据的话。

    这本书按估计值算其实装得下——正因为如此才要钉住：最容易说出那句假话的，
    恰恰是这种情形。
    """
    token = await _token(client)
    book = await _book(db_session, "reader@example.com", word_count=20_000)

    brains = {
        b["name"]: b
        for b in (
            await client.get(f"/api/brains?bookId={book.id}", headers=_auth(token))
        ).json()
    }

    guessed = brains["lmstudio"]
    assert guessed["fitNote"] == "窗口未知（按估计）"
    assert guessed["fits"] is None
    # 有依据的那几行照常下结论
    assert brains["qwen"]["fits"] is True
    assert brains["qwen"]["fitNote"] == "整本装得下"


async def test_书籍详情与设置页对同一个大脑给出同一个结论(client, db_session):
    """两页各算各的，迟早一页说装得下、另一页说要挑章节。

    所以书籍详情那一栏直接读⑧屏那份结果——连用户自己加的端点也在里面。
    """
    token = await _token(client)
    await _add_endpoint(client, token, "书房那台", "my-model", 1_000_000)
    book = await _book(db_session, "reader@example.com", word_count=120_000)

    settings = {
        b["name"]: b
        for b in (
            await client.get(f"/api/brains?bookId={book.id}", headers=_auth(token))
        ).json()
    }
    r = await client.get(f"/api/books/{book.id}", headers=_auth(token))
    assert r.status_code == 200, r.text
    detail = {b["name"]: b for b in r.json()["brains"]}

    assert set(detail) == set(settings)
    assert "书房那台" in detail
    for name, fit in detail.items():
        mine = settings[name]
        assert (
            fit["model"],
            fit["contextChars"],
            fit["fits"],
            fit["contextSource"],
        ) == (
            mine["model"],
            mine["contextChars"],
            mine["fits"],
            mine["contextSource"],
        ), name
