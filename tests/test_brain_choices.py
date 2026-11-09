"""为内置预设换模型、存下来、恢复默认（界面稿⑧屏「换模型」→ 保存）。

解析 = **预设 + 选择**：provider / 地址 / key 名永远从代码来，选择只覆盖模型与窗口。
选择跟着账号走（存在库里），key 仍然只在浏览器。

这里盯的几件事：

* 保存之后⑧屏、书籍详情、新开的会话三处立刻用新模型——它们都经 `describe` 那一处
* **已开的会话不受影响**：显示的、算预算的、真正调用的，仍是开会话时那一个
* 窗口的来路存得下来：供应商报的，下次打开还标「供应商」
* 校验失败说清限制，**不把填进来的东西抄回去**
"""

from __future__ import annotations

from fakes import FAKE_KEY
from sqlalchemy import select

from booksoul_api.models.user import User

_PASSWORD = "一句足够长的中文密码"


async def _token(client, email: str = "reader@example.com") -> str:
    r = await client.post(
        "/api/auth/register", json={"email": email, "password": _PASSWORD}
    )
    assert r.status_code == 201, r.text
    return r.json()["accessToken"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _brains(client, token: str, query: str = "") -> dict[str, dict]:
    r = await client.get(f"/api/brains{query}", headers=_auth(token))
    assert r.status_code == 200, r.text
    return {b["name"]: b for b in r.json()}


async def _choose(client, token: str, name: str, **body):
    return await client.put(
        f"/api/brains/{name}/model",
        headers=_auth(token),
        json={"contextTokens": 0, "contextSource": "", **body},
    )


# ---------------------------------------------------------------- 存与读


async def test_没选过时_模型就是预设的_并说得出默认是哪个(client):
    token = await _token(client)

    qwen = (await _brains(client, token))["qwen"]

    assert qwen["modelChosen"] is False
    assert qwen["defaultModel"] == qwen["model"] == "qwen-plus"


async def test_选了一个模型保存_设置页立刻按它说话(client):
    token = await _token(client)

    r = await _choose(client, token, "qwen", model="qwen-plus-latest")

    assert r.status_code == 200, r.text
    saved = r.json()
    assert saved["name"] == "qwen"
    assert saved["model"] == "qwen-plus-latest"
    assert saved["modelChosen"] is True
    assert saved["defaultModel"] == "qwen-plus"
    # 名字里带 qwen-plus，内置表认得
    assert saved["contextSource"] == "builtin"

    listed = (await _brains(client, token))["qwen"]
    assert listed["model"] == "qwen-plus-latest"
    assert listed["modelChosen"] is True


async def test_选择跟着账号走_换一次登录照样在(client):
    """换一台机器登录 = 一张新 token。key 要重填，选择不用。"""
    token = await _token(client)
    await _choose(client, token, "qwen", model="qwen-max")

    r = await client.post(
        "/api/auth/login",
        json={"email": "reader@example.com", "password": _PASSWORD},
    )
    assert r.status_code == 200, r.text
    again = r.json()["accessToken"]

    assert (await _brains(client, again))["qwen"]["model"] == "qwen-max"


async def test_供应商报的窗口存下来_下次打开还标来自供应商(client):
    from booksoul.ask import budget_from_context_tokens

    token = await _token(client)
    r = await _choose(
        client,
        token,
        "qwen",
        model="qwen-max",
        contextTokens=32_768,
        contextSource="vendor",
    )
    assert r.status_code == 200, r.text

    qwen = (await _brains(client, token))["qwen"]
    assert qwen["contextSource"] == "vendor"
    assert qwen["contextChars"] == budget_from_context_tokens(32_768)


async def test_没报窗口也认不出的模型_如实标估计(client):
    token = await _token(client)

    r = await _choose(client, token, "qwen", model="MiniMax/MiniMax-M3")

    assert r.status_code == 200, r.text
    assert r.json()["contextSource"] == "estimate"


async def test_预设的地址与provider不随选择变(client):
    token = await _token(client)
    before = (await _brains(client, token))["qwen"]

    await _choose(client, token, "qwen", model="kimi/kimi-k2.7-code-highspeed")
    after = (await _brains(client, token))["qwen"]

    for field in ("provider", "baseUrl", "apiKeyEnv", "needsKey", "custom"):
        assert after[field] == before[field], field


async def test_恢复默认_模型窗口来源全部回到预设(client):
    token = await _token(client)
    await _choose(
        client,
        token,
        "qwen",
        model="qwen-max",
        contextTokens=32_768,
        contextSource="vendor",
    )

    r = await client.delete("/api/brains/qwen/model", headers=_auth(token))
    assert r.status_code == 204, r.text

    qwen = (await _brains(client, token))["qwen"]
    assert qwen["model"] == "qwen-plus"
    assert qwen["modelChosen"] is False
    assert qwen["contextSource"] == "builtin"
    # 没选过也可以恢复默认：本来就是默认，不算错
    again = await client.delete("/api/brains/qwen/model", headers=_auth(token))
    assert again.status_code == 204


async def test_书籍详情装得下吗也按选的模型判断(client, db_session):
    """选一个窗口小的模型，同一本书就从「整本装得下」变成「要挑章节」。"""
    from booksoul_api.models.book import Book

    token = await _token(client)
    user = await db_session.scalar(
        select(User).where(User.email == "reader@example.com")
    )
    book = Book(
        user_id=user.id,
        title="悉达多",
        source_file="悉达多.epub",
        source_format="epub",
        status="ready",
        storage_path=f"{user.id}/1-悉达多",
        chapter_count=16,
        word_count=48_155,
    )
    db_session.add(book)
    await db_session.commit()

    detail = {b["name"]: b for b in (await _detail(client, token, book.id))}
    assert detail["qwen"]["fits"] is True

    await _choose(client, token, "qwen", model="qwen2.5-7b-instruct")

    detail = {b["name"]: b for b in (await _detail(client, token, book.id))}
    assert detail["qwen"]["model"] == "qwen2.5-7b-instruct"
    assert detail["qwen"]["fits"] is False  # qwen2.5 那一档 27k 字
    settings = (await _brains(client, token, f"?bookId={book.id}"))["qwen"]
    assert settings["fits"] is False


async def _detail(client, token: str, book_id: int) -> list[dict]:
    r = await client.get(f"/api/books/{book_id}", headers=_auth(token))
    assert r.status_code == 200, r.text
    return r.json()["brains"]


# ---------------------------------------------------------------- 会话


async def test_保存之后新开的会话用新模型_已开的仍用原来的(db_session, fake_brain):
    """Grill C2 的回归：选择改的是「以后」，不改写已经开着的那些。"""
    from test_chat import _drain, _reader_with_book

    from booksoul_api.services.brain_service import BrainService
    from booksoul_api.services.chat_service import ChatService

    user, book = await _reader_with_book(db_session)
    chat = ChatService(db_session)
    old = await chat.start(
        user_id=user.id, book_id=book.id, persona="scholar", brain_name="qwen"
    )

    await BrainService(db_session).set_model(
        user_id=user.id, name="qwen", model="qwen-max"
    )
    new = await chat.start(
        user_id=user.id, book_id=book.id, persona="scholar", brain_name="qwen"
    )

    async def asked(conversation) -> str:
        await _drain(
            await chat.ask(
                conversation_id=conversation.id,
                user_id=user.id,
                question="悉达多为什么离开？",
                brain_key=FAKE_KEY,
            )
        )
        return fake_brain.calls[-1]["cfg"].model

    assert await asked(new) == "qwen-max"
    assert await asked(old) == "qwen-plus"
    assert (old.model, new.model) == ("qwen-plus", "qwen-max")


# ---------------------------------------------------------------- 校验


_MARK = "ECHO-MARK-9c1"


async def test_不合法的输入是422_说清限制_不回显填进来的东西(client):
    token = await _token(client)
    bad = [
        {"model": "   "},
        {"model": ""},
        {"model": "m" * 65 + _MARK},
        {"model": f"qwen{_MARK}\n-max"},
        {"model": "qwen-max", "contextTokens": 10_000_001},
        {"model": "qwen-max", "contextTokens": -1},
        {"model": "qwen-max", "contextTokens": 0, "contextSource": "vendor"},
        {"model": "qwen-max", "contextTokens": 8_000, "contextSource": _MARK},
    ]

    for body in bad:
        r = await _choose(client, token, "qwen", **body)
        assert r.status_code == 422, (body, r.text)
        assert _MARK not in r.text, body
        assert r.json()["detail"][0]["msg"], body  # 有一句人话

    # 什么都没存进去
    assert (await _brains(client, token))["qwen"]["modelChosen"] is False


async def test_模型名前后的空白去掉再存(client):
    token = await _token(client)

    r = await _choose(client, token, "qwen", model="  qwen-max  ")

    assert r.status_code == 200, r.text
    assert r.json()["model"] == "qwen-max"


# ---------------------------------------------------------------- 隔离与边界


async def test_看不到也改不了别人的选择(client):
    alice = await _token(client, "alice@example.com")
    bob = await _token(client, "bob@example.com")

    await _choose(client, alice, "qwen", model="qwen-max")
    await _choose(client, bob, "qwen", model="qwen-turbo")
    await client.delete("/api/brains/qwen/model", headers=_auth(bob))

    assert (await _brains(client, alice))["qwen"]["model"] == "qwen-max"
    assert (await _brains(client, bob))["qwen"]["model"] == "qwen-plus"


async def test_没有这个预设_存选择是404(client):
    token = await _token(client)

    r = await _choose(client, token, "没有这个", model="qwen-max")

    assert r.status_code == 404, r.text


async def test_指向已不存在预设的选择被忽略_不报错(client, db_session):
    """预设被改名或从 booksoul.toml 里删掉之后，库里那一行就对不上任何人了（Grill R4）。"""
    from booksoul_api.models.brain_choice import BrainChoice

    token = await _token(client)
    user = await db_session.scalar(
        select(User).where(User.email == "reader@example.com")
    )
    db_session.add(BrainChoice(user_id=user.id, brain_name="改了名的预设", model="x"))
    await db_session.commit()

    brains = await _brains(client, token)

    assert "改了名的预设" not in brains
    assert brains["qwen"]["modelChosen"] is False


# ---------------------------------------------------------------- 手填（切片 9）


async def test_手填模型与真实窗口_标你填的_装得下吗按它判断(client, db_session):
    """列不出来的服务（或列表里没有的模型）照样能用：手填名字、照着文档填窗口。"""
    from booksoul.ask import budget_from_context_tokens
    from booksoul_api.models.book import Book

    token = await _token(client)
    user = await db_session.scalar(
        select(User).where(User.email == "reader@example.com")
    )
    book = Book(
        user_id=user.id,
        title="一本很长的书",
        source_file="长.epub",
        source_format="epub",
        status="ready",
        storage_path=f"{user.id}/1-长",
        chapter_count=80,
        word_count=600_000,
    )
    db_session.add(book)
    await db_session.commit()
    before = (await _brains(client, token, f"?bookId={book.id}"))["qwen"]
    assert before["fits"] is False  # qwen-plus 那一档装不下 60 万字

    r = await _choose(
        client,
        token,
        "qwen",
        model="qwen-long-手填",
        contextTokens=1_000_000,
        contextSource="manual",
    )
    assert r.status_code == 200, r.text

    after = (await _brains(client, token, f"?bookId={book.id}"))["qwen"]
    assert after["model"] == "qwen-long-手填"
    assert after["contextSource"] == "manual"
    # 窗口原样带回来，输入框才预填得出「你上次填的那个数」
    assert after["contextTokens"] == 1_000_000
    assert after["contextChars"] == budget_from_context_tokens(1_000_000)
    assert after["fits"] is True


async def test_把窗口清成0_回到内置表或估计(client):
    token = await _token(client)
    await _choose(
        client,
        token,
        "qwen",
        model="qwen-plus-latest",
        contextTokens=8_000,
        contextSource="manual",
    )

    r = await _choose(client, token, "qwen", model="qwen-plus-latest")

    assert r.status_code == 200, r.text
    assert r.json()["contextTokens"] == 0
    assert r.json()["contextSource"] == "builtin"  # 名字里带 qwen-plus，内置表认得


async def test_内置表与估计的窗口没有token数可给(client):
    """那两种来路手里只有字数（内置表存的就是字数），不从字倒推 token。"""
    token = await _token(client)

    brains = await _brains(client, token)

    assert brains["qwen"]["contextSource"] == "builtin"
    assert brains["qwen"]["contextTokens"] == 0
    assert brains["lmstudio"]["contextTokens"] == 0


# ---------------------------------------------------------------- 自定义端点（切片 10）
#
# 已保存的自定义端点也能换模型：直接改它自己那一行（Spec N5），不进选择表。
# 它没有「默认模型」——模型本来就是用户自己填的那一个。


async def _endpoint(client, token: str, name: str = "书房那台") -> dict:
    r = await client.post(
        "/api/brains",
        headers=_auth(token),
        json={
            "name": name,
            "model": "my-model",
            "baseUrl": "https://llm.example.com/v1",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_自定义端点换模型_改的是它自己那一行(client, db_session):
    from booksoul.ask import budget_from_context_tokens
    from booksoul_api.models.brain import Brain
    from booksoul_api.models.brain_choice import BrainChoice

    token = await _token(client)
    created = await _endpoint(client, token)

    r = await _choose(
        client,
        token,
        "书房那台",
        model="qwen3-32b",
        contextTokens=32_768,
        contextSource="vendor",
    )

    assert r.status_code == 200, r.text
    saved = r.json()
    assert saved["model"] == "qwen3-32b"
    assert saved["contextTokens"] == 32_768
    assert saved["contextChars"] == budget_from_context_tokens(32_768)
    assert saved["custom"] is True
    assert saved["id"] == created["id"]  # 同一行，不是新建了一个
    assert saved["modelChosen"] is False  # 没有默认模型，谈不上「换过」

    listed = (await _brains(client, token))["书房那台"]
    assert listed["model"] == "qwen3-32b"
    row = await db_session.scalar(select(Brain).where(Brain.name == "书房那台"))
    assert (row.model, row.context_tokens) == ("qwen3-32b", 32_768)
    assert row.base_url == "https://llm.example.com/v1"  # 地址不动
    # 不进选择表
    assert await db_session.scalar(select(BrainChoice)) is None


async def test_自定义端点换了模型之后新开的会话用新的_已开的不变(
    db_session, fake_brain
):
    from test_chat import _drain, _reader_with_book

    from booksoul_api.schemas.brain import BrainCreate
    from booksoul_api.services.brain_service import BrainService
    from booksoul_api.services.chat_service import ChatService

    user, book = await _reader_with_book(db_session)
    brains = BrainService(db_session)
    await brains.add_custom(
        user_id=user.id,
        payload=BrainCreate(
            name="书房那台", model="my-model", base_url="https://llm.example.com/v1"
        ),
    )
    chat = ChatService(db_session)
    old = await chat.start(
        user_id=user.id, book_id=book.id, persona="scholar", brain_name="书房那台"
    )

    await brains.set_model(user_id=user.id, name="书房那台", model="my-model-v2")
    new = await chat.start(
        user_id=user.id, book_id=book.id, persona="scholar", brain_name="书房那台"
    )

    for conversation, expected in ((new, "my-model-v2"), (old, "my-model")):
        await _drain(
            await chat.ask(
                conversation_id=conversation.id,
                user_id=user.id,
                question="悉达多为什么离开？",
                brain_key=FAKE_KEY,
            )
        )
        assert fake_brain.calls[-1]["cfg"].model == expected


async def test_自定义端点没有默认模型_恢复默认是400并说清楚(client):
    token = await _token(client)
    await _endpoint(client, token)

    r = await client.delete("/api/brains/书房那台/model", headers=_auth(token))

    assert r.status_code == 400, r.text
    assert "没有默认模型" in r.json()["detail"]
    assert (await _brains(client, token))["书房那台"]["model"] == "my-model"


async def test_别人的自定义端点换不了也恢复不了_与不存在同一个404(client):
    owner = await _token(client, "owner@example.com")
    await _endpoint(client, owner)
    stranger = await _token(client, "stranger@example.com")

    put = await _choose(client, stranger, "书房那台", model="偷换的")
    delete = await client.delete("/api/brains/书房那台/model", headers=_auth(stranger))

    assert put.status_code == 404, put.text
    assert delete.status_code == 404, delete.text
    assert (await _brains(client, owner))["书房那台"]["model"] == "my-model"
