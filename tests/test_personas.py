"""人格提示词的查看与修改，以及「处境模式」开关（界面稿⑦屏开关 + ⑧屏右栏）。

两件事放在一片里，因为它们改的是同一样东西——**模型最终收到的那段人格提示词**。
一个是临时附加（处境模式），一个是永久覆写（编辑）。

盯的几件事：

* **写坏的占位符必须在保存的那一刻就拦住。** 提示词要过一遍 `str.format()` 填
  书名作者，而在一份 md 里写 `{原文}` 是再自然不过的事。不拦，它会在**下一次
  提问时**炸在后台任务里——用户看到的是「这条回答没说完」，跟他半小时前改的
  那个字看不出任何关系，而且三种人格里只有那一种会坏。
* **写坏了要还得回来。** `prompts/` 那三个 md 是这个项目最值钱的东西，
  这个目录又不在版本控制里。覆写必须留得下退路。
* **改完即时生效。** 下一轮就该用新的，不必重启服务。
* **开关处境模式不废缓存。** 人格那一段在缓存断点之后，附加一段指令不该让
  整本书重新计一次输入费。这条是「这个开关很便宜」的依据，不能只是嘴上说。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fakes import FAKE_KEY, FakeBrain

from booksoul import persona as persona_mod
from booksoul.model import count_words
from booksoul_api.core.config import get_settings
from booksoul_api.models.book import Book
from booksoul_api.models.chapter import Chapter
from booksoul_api.models.user import User
from booksoul_api.services import prompt_store
from booksoul_api.services.chat_service import ChatService

# ------------------------------------------------------------------ 保存前的体检


def test_写坏的占位符在保存时就被拦下并且点名(prompts_sandbox):
    with pytest.raises(prompt_store.PromptError) as exc:
        prompt_store.write("scholar", "你是《{title}》的研读者。请引用 {原文}。")

    # 光说「格式不对」等于让用户在三千字里自己找。必须把那个词说出来。
    assert "原文" in str(exc.value)
    # 顺带告诉他有哪些是能用的
    assert "title" in str(exc.value)


def test_单个花括号被拦下并且告诉用户字面花括号怎么写(prompts_sandbox):
    with pytest.raises(prompt_store.PromptError) as exc:
        prompt_store.write("book", '输出一个 JSON：{ "答案": ... }')

    assert "{{" in str(exc.value)


def test_成对的花括号是字面花括号放行(prompts_sandbox):
    text = '你是《{title}》。输出形如 {{"答案": ...}} 的东西。'
    prompt_store.write("book", text)

    # 存进去的是原样，不是转义后的
    assert prompts_sandbox.joinpath("book.md").read_text(encoding="utf-8") == text
    # 而渲染出来的是一对真花括号
    rendered = persona_mod.load("book").render(_meta(), words=1, chapters=1)
    assert '{"答案": ...}' in rendered


def test_五个已知占位符都放行(prompts_sandbox):
    text = "《{title}》，{author}。{byline}。{words} 字，{chapters} 节。"
    prompt_store.write("scholar", text)
    assert persona_mod.load("scholar").template == text


def test_空提示词被拒(prompts_sandbox):
    before = prompts_sandbox.joinpath("scholar.md").read_text(encoding="utf-8")
    with pytest.raises(prompt_store.PromptError):
        prompt_store.write("scholar", "   \n\n  ")
    assert prompts_sandbox.joinpath("scholar.md").read_text(encoding="utf-8") == before


def test_被拒的保存一个字都不写进去(prompts_sandbox):
    """拒收要发生在**动文件之前**。

    先截断再校验的写法，会把一次拼错的占位符变成一次内容丢失。
    """
    before = prompts_sandbox.joinpath("author.md").read_text(encoding="utf-8")
    with pytest.raises(prompt_store.PromptError):
        prompt_store.write("author", "改了一半 {没这个占位符}")

    assert prompts_sandbox.joinpath("author.md").read_text(encoding="utf-8") == before
    # 也不许留下基线：什么都没发生过
    assert not (prompts_sandbox / prompt_store.BASELINE_DIR).exists()


# ------------------------------------------------------------------ 读、写、还原


def test_读出正文文件名与出厂状态(prompts_sandbox):
    one = prompt_store.read("scholar")

    assert one.key == "scholar"
    assert one.name == "研读者"
    assert one.description
    # ⑧屏那行 mono 写的就是它，用户据此知道「改的是磁盘上哪个文件」
    assert one.filename == "prompts/scholar.md"
    assert one.text.startswith("# 人格：研读者")
    assert one.edited is False


def test_保存之后引擎立刻读到新的(prompts_sandbox):
    prompt_store.write("scholar", "# 人格：研读者\n\n换了个说法。《{title}》。")

    # 不重启、不清缓存：引擎每一轮都重新读文件
    assert "换了个说法" in persona_mod.load("scholar").template
    assert prompt_store.read("scholar").edited is True


def test_首次保存留下出厂基线还原拿得回来(prompts_sandbox):
    factory = prompts_sandbox.joinpath("book.md").read_text(encoding="utf-8")

    prompt_store.write("book", "# 人格：书本身\n\n我被改坏了。")
    assert prompts_sandbox.joinpath("book.md").read_text(encoding="utf-8") != factory

    back = prompt_store.restore("book")
    assert back.text == factory
    assert prompts_sandbox.joinpath("book.md").read_text(encoding="utf-8") == factory


def test_第二次保存不刷新基线(prompts_sandbox):
    """基线是**出厂**那一版，不是上一版。

    每次保存都刷新基线的话，「恢复默认」只能退回一步；而用户点它的时候，
    多半已经改了三四轮，最想要的正是最初那一版。
    """
    factory = prompts_sandbox.joinpath("author.md").read_text(encoding="utf-8")

    prompt_store.write("author", "# 人格：作者\n\n第一次改。")
    prompt_store.write("author", "# 人格：作者\n\n第二次改。")

    assert prompt_store.restore("author").text == factory


def test_还原之后不再算改过(prompts_sandbox):
    prompt_store.write("book", "# 人格：书本身\n\n改了。")
    assert prompt_store.read("book").edited is True

    prompt_store.restore("book")
    assert prompt_store.read("book").edited is False


def test_还原一个没改过的人格不报错(prompts_sandbox):
    factory = prompts_sandbox.joinpath("scholar.md").read_text(encoding="utf-8")
    assert prompt_store.restore("scholar").text == factory


def test_未知人格(prompts_sandbox):
    for call in (
        lambda: prompt_store.read("莎士比亚"),
        lambda: prompt_store.write("莎士比亚", "随便"),
        lambda: prompt_store.restore("莎士比亚"),
    ):
        with pytest.raises(KeyError):
            call()


def test_列表不带正文(prompts_sandbox):
    """三段提示词加起来六千多字。列表那一下没有理由背着它们走。"""
    rows = prompt_store.personas()

    assert [r.key for r in rows] == ["scholar", "author", "book"]
    assert all(r.name and r.description for r in rows)
    assert all(r.text == "" for r in rows)


# ------------------------------------------------------------------ 端点


async def _token(client, email: str = "reader@example.com") -> str:
    r = await client.post(
        "/api/auth/register", json={"email": email, "password": "一句足够长的中文密码"}
    )
    assert r.status_code == 201, r.text
    return r.json()["accessToken"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_列出三种人格(client, prompts_sandbox):
    token = await _token(client)
    r = await client.get("/api/personas", headers=_auth(token))

    assert r.status_code == 200, r.text
    rows = r.json()
    assert [p["key"] for p in rows] == ["scholar", "author", "book"]
    assert rows[0]["name"] == "研读者"
    assert rows[0]["filename"] == "prompts/scholar.md"
    assert rows[0]["edited"] is False
    assert rows[0]["text"] == ""


async def test_取一种人格的正文(client, prompts_sandbox):
    token = await _token(client)
    r = await client.get("/api/personas/author", headers=_auth(token))

    assert r.status_code == 200, r.text
    assert r.json()["text"].startswith("# 人格：作者")


async def test_覆写之后再取是新的(client, prompts_sandbox):
    token = await _token(client)
    body = {"text": "# 人格：作者\n\n我换了个口气。《{title}》。"}

    r = await client.put("/api/personas/author", json=body, headers=_auth(token))
    assert r.status_code == 200, r.text
    assert r.json()["edited"] is True

    again = await client.get("/api/personas/author", headers=_auth(token))
    assert again.json()["text"] == body["text"]


async def test_覆写坏占位符是422不是500(client, prompts_sandbox):
    token = await _token(client)
    r = await client.put(
        "/api/personas/author",
        json={"text": "《{title}》里的 {原文} 怎么说"},
        headers=_auth(token),
    )

    assert r.status_code == 422, r.text
    assert "原文" in r.json()["detail"]


async def test_还原端点(client, prompts_sandbox):
    token = await _token(client)
    factory = prompts_sandbox.joinpath("book.md").read_text(encoding="utf-8")

    await client.put(
        "/api/personas/book", json={"text": "改坏了"}, headers=_auth(token)
    )
    r = await client.post("/api/personas/book/restore", headers=_auth(token))

    assert r.status_code == 200, r.text
    assert r.json()["text"] == factory
    assert r.json()["edited"] is False


async def test_没登录不给看也不给改(client, prompts_sandbox):
    assert (await client.get("/api/personas")).status_code == 401
    assert (await client.get("/api/personas/book")).status_code == 401
    assert (
        await client.put("/api/personas/book", json={"text": "x"})
    ).status_code == 401


async def test_未知人格是404(client, prompts_sandbox):
    token = await _token(client)
    assert (
        await client.get("/api/personas/莎士比亚", headers=_auth(token))
    ).status_code == 404


# ------------------------------------------------------------------ 处境模式

CHAPTERS = {
    "婆罗门之子": "悉达多，俊美的婆罗门之子，与他的好友乔文达一道长大。",
    "乔达摩": "他们见到了佛陀乔达摩，佛陀讲说法义，世尊的法义是圆满的。",
}


class _Meta:
    title = "悉达多"
    author = "黑塞"
    translator = ""
    contributor = ""
    publisher = ""
    published = ""


def _meta() -> _Meta:
    return _Meta()


async def _reader_with_book(db_session) -> tuple[User, Book]:
    user = User(email="reader@example.com", password_hash="不参与本测试")
    db_session.add(user)
    await db_session.flush()

    storage = f"{user.id}/persona-fixture-悉达多"
    directory = Path(get_settings().library_root) / storage
    (directory / "chapters").mkdir(parents=True, exist_ok=True)

    records = []
    for n, (title, body) in enumerate(CHAPTERS.items(), 1):
        record = {
            "序号": n,
            "标题": title,
            "文件": f"chapters/{n:03d}-{title}.md",
            "层级": 1,
            "字数": count_words(body),
        }
        (directory / record["文件"]).write_text(
            f"# {title}\n\n{body}", encoding="utf-8"
        )
        records.append(record)

    (directory / "meta.json").write_text(
        # 书籍元信息在 "书籍" 底下 —— 顶层放 "书名" 会被静静忽略，
        # 书名于是退回目录名，而人格提示词第一句就是「你是《{title}》的…」
        json.dumps(
            {"书籍": {"书名": "悉达多", "作者": "黑塞"}, "章节": records},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    book = Book(
        user_id=user.id,
        title="悉达多",
        source_file="悉达多.epub",
        source_format="epub",
        status="ready",
        storage_path=storage,
        chapter_count=len(records),
        word_count=sum(r["字数"] for r in records),
    )
    db_session.add(book)
    await db_session.flush()
    db_session.add_all(
        Chapter(
            book_id=book.id,
            idx=r["序号"],
            title=r["标题"],
            level=1,
            word_count=r["字数"],
            file_path=r["文件"],
        )
        for r in records
    )
    await db_session.commit()
    return user, book


async def _ask(service, conversation, user, question="他为什么拒绝？"):
    stream = await service.ask(
        conversation_id=conversation.id,
        user_id=user.id,
        question=question,
        brain_key=FAKE_KEY,
    )
    return [event async for event in stream]


def _last_system() -> list[tuple[str, bool]]:
    return FakeBrain.calls[-1]["system"]


async def test_处境模式开着时人格那一段尾部多了那一段指令(
    db_session, fake_brain, prompts_sandbox
):
    user, book = await _reader_with_book(db_session)
    service = ChatService(db_session)
    conversation = await service.start(
        user_id=user.id,
        book_id=book.id,
        persona="scholar",
        brain_name="claude",
        reflection=True,
    )
    await _ask(service, conversation, user)

    persona_block = _last_system()[1][0]
    assert "读者这次带来的不是一个关于书的知识问题" in persona_block


async def test_处境模式关着时没有那一段(db_session, fake_brain, prompts_sandbox):
    user, book = await _reader_with_book(db_session)
    service = ChatService(db_session)
    conversation = await service.start(
        user_id=user.id, book_id=book.id, persona="scholar", brain_name="claude"
    )
    await _ask(service, conversation, user)

    assert "读者这次带来的不是" not in _last_system()[1][0]


async def test_中途打开处境模式下一轮就带上而书那一段逐字不变(
    db_session, fake_brain, prompts_sandbox
):
    """**这个开关很便宜**——依据在这里，不在嘴上。

    缓存断点在全书之后、人格之前。附加一段处境指令只动断点之后那一块，
    整本书那一段逐字相同，缓存照常命中。开关因此可以随手拨，
    不必像「重新挑章节」那样先问用户一句。
    """
    user, book = await _reader_with_book(db_session)
    service = ChatService(db_session)
    conversation = await service.start(
        user_id=user.id, book_id=book.id, persona="scholar", brain_name="claude"
    )
    await _ask(service, conversation, user, "他为什么拒绝？")
    before = _last_system()

    out = await service.update(
        conversation_id=conversation.id, user_id=user.id, reflection=True
    )
    assert out.reflection is True

    await _ask(service, conversation, user, "那我该怎么办？")
    after = _last_system()

    assert after[0][0] == before[0][0]  # 书那一段：逐字相同
    assert after[0][1] is True  # 缓存断点还在原处
    assert "读者这次带来的不是" in after[1][0]
    assert "读者这次带来的不是" not in before[1][0]


async def test_只改处境模式不动主干人格(db_session, prompts_sandbox):
    user, book = await _reader_with_book(db_session)
    service = ChatService(db_session)
    conversation = await service.start(
        user_id=user.id, book_id=book.id, persona="book", brain_name="claude"
    )

    out = await service.update(
        conversation_id=conversation.id, user_id=user.id, reflection=True
    )
    assert out.persona == "book"
    assert out.reflection is True

    out = await service.update(
        conversation_id=conversation.id, user_id=user.id, persona="author"
    )
    assert out.persona == "author"
    assert out.reflection is True  # 没给就不动它


async def test_改过的人格提示词下一轮就生效(db_session, fake_brain, prompts_sandbox):
    """端到端的「改完即时生效」：改文件 → 下一轮模型手上就是新的。"""
    user, book = await _reader_with_book(db_session)
    service = ChatService(db_session)
    conversation = await service.start(
        user_id=user.id, book_id=book.id, persona="scholar", brain_name="claude"
    )
    await _ask(service, conversation, user, "他为什么拒绝？")
    assert "一个刚改出来的口气" not in _last_system()[1][0]

    prompt_store.write("scholar", "# 人格：研读者\n\n一个刚改出来的口气。《{title}》。")

    await _ask(service, conversation, user, "那再往下呢？")
    assert "一个刚改出来的口气" in _last_system()[1][0]
    # 书名确实填进去了：占位符还在正常工作
    assert "《悉达多》" in _last_system()[1][0]


async def test_改人格提示词不动书那一段(db_session, fake_brain, prompts_sandbox):
    """编辑提示词同样只动缓存断点之后那一块。"""
    user, book = await _reader_with_book(db_session)
    service = ChatService(db_session)
    conversation = await service.start(
        user_id=user.id, book_id=book.id, persona="scholar", brain_name="claude"
    )
    await _ask(service, conversation, user, "他为什么拒绝？")
    before = _last_system()[0][0]

    prompt_store.write("scholar", "# 人格：研读者\n\n完全不同的一段话。")
    await _ask(service, conversation, user, "那再往下呢？")

    assert _last_system()[0][0] == before


# ------------------------------------------------------------------ 自由文本的路径参数


async def test_路径里的换行进不了日志(client, prompts_sandbox, caplog):
    """`/api/personas/{key}` 是这个应用里第一个**自由文本**的路径参数。

    别的路径参数都是整数。自由文本一进来，访问日志就多了一条注入路径：一个
    `%0A` 能把一行访问日志劈成两行，后一行看起来就是另一次请求——伪造出一条
    「某人成功登录了」不需要任何权限。

    现在挡住它的是 `request.url.path`：`urlsplit` 会剥掉 ASCII 的换行与制表符。
    这条测试钉的正是这一点——有人把中间件改成看起来更直接的 `request.scope["path"]`
    （那个是解码后的原样），注入就回来了，而且不会有别的测试变红。
    """
    import logging

    token = await _token(client)
    with caplog.at_level(logging.INFO, logger="booksoul_api.access"):
        r = await client.get(
            "/api/personas/%0AINFO:%20%20%20%20%20forged", headers=_auth(token)
        )

    assert r.status_code == 404
    lines = [rec.getMessage() for rec in caplog.records]
    assert lines, "访问日志一行都没有，这条测试就什么都没验到"
    assert not any("\n" in line for line in lines)


async def test_404不原样回显任意长度的key(client, prompts_sandbox):
    """回显要给出路（Spec N1），但回显的是**修剪过**的那一份。

    一个 4KB 的路径不该换来一条 4KB 的错误消息，控制字符也不该原样抄回去。
    """
    token = await _token(client)
    r = await client.get("/api/personas/" + "长" * 500, headers=_auth(token))

    assert r.status_code == 404
    detail = r.json()["detail"]
    assert len(detail) < 120
    assert "scholar" in detail  # 出路还在
