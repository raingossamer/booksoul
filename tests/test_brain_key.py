"""模型 API key 的几条硬规则（`backend.md`「认证授权」）。

规则本身一句话：**key 只存在用户浏览器，经 `X-Brain-Key` 送来，用完即丢**——
不写数据库、不写文件、不写日志、不进异常上报、不出现在任何响应体里。

这个文件测的是「不」的那几条。它们的共同点是：**平时全都是对的，出事那天才知道错**。
一次「顺手把请求头一起打出来看看」的调试代码能把所有人的 key 抄进日志，
而那段代码在功能测试里长得和正确代码一模一样。所以这几条要有测试钉着。
"""

from __future__ import annotations

import logging

import pytest

from booksoul.config import BrainConfig

# 长得像真 key 的假 key。纯 ASCII —— HTTP 头本来就只放得下 ASCII，
# 真 key 也一样，用中文当占位符会在发请求那一步就炸掉，测不到真正想测的东西。
FAKE_KEY = "sk-ant-api03-NEVER-IN-LOGS-AAAABBBBCCCCDDDDEEEEFFFF"


def test_配置对象打印出来不含key():
    """`repr()` 是 key 最常见的泄漏口。

    它会在四个地方自动被调用：日志里的 `%r`、异常回溯里的局部变量、
    pytest 断言失败时打印的对象、调试器的变量面板。
    每一处都是「没人打算打印 key」却把 key 打印了出来。
    """
    cfg = BrainConfig(name="claude", api_key=FAKE_KEY)

    assert FAKE_KEY not in repr(cfg)
    assert "sk-ant" not in repr(cfg)
    # 但字段本身照常能取——这是隐藏，不是加密
    assert cfg.api_key == FAKE_KEY
    # 别的字段该出现还是要出现，否则这个 repr 就没用了
    assert "claude" in repr(cfg)


def test_描述这个大脑时不会把key带出来():
    cfg = BrainConfig(name="qwen", model="qwen-plus", api_key=FAKE_KEY)
    assert FAKE_KEY not in cfg.describe()


async def test_日志只记方法路径状态与耗时_不记任何请求头(client, caplog):
    """日志中间件是**白名单制**：允许什么就只记什么。

    黑名单（「把 X-Brain-Key 过滤掉」）挡不住下一个带密钥的头——
    而那个头的名字今天还没人想到。
    """
    caplog.set_level(logging.INFO, logger="booksoul_api.access")

    r = await client.get(
        "/api/health?api-key=" + FAKE_KEY,
        headers={"X-Brain-Key": FAKE_KEY, "Cookie": "session=NEVER-IN-LOGS-COOKIE"},
    )
    assert r.status_code == 200

    records = [r for r in caplog.records if r.name == "booksoul_api.access"]
    assert records, "请求日志一条都没有——中间件没装上"

    whole = "\n".join(r.getMessage() for r in records)
    assert FAKE_KEY not in whole
    assert "NEVER-IN-LOGS-COOKIE" not in whole
    # 该记的还是要记，否则「不记」就成了「什么都不记」
    assert "GET" in whole and "/api/health" in whole and "200" in whole
    # query string 整段不记：key 不该出现在 URL 里，但那是前端的纪律，
    # 服务端这一侧也不替它兜着——只记 path
    assert "api-key" not in whole


async def test_未预料的异常不回显请求头也不带回溯(client, monkeypatch, caplog):
    """500 是异常处理器最容易出错的一档：前面几档都有人仔细写过。

    Starlette 默认那个 500 是纯文本 "Internal Server Error"，本来就干净；
    我们换成中文提示之后，得自己保证没把上下文顺手带出去。
    """
    from httpx import ASGITransport, AsyncClient

    from booksoul_api.main import app
    from booksoul_api.services import brain_service

    token = (
        await client.post(
            "/api/auth/register",
            json={"email": "reader@example.com", "password": "一句足够长的中文密码"},
        )
    ).json()["accessToken"]

    def boom(*_args, **_kwargs):
        raise RuntimeError(f"内部错误，上下文里带着 {FAKE_KEY}")

    monkeypatch.setattr(brain_service, "presets", boom)
    caplog.set_level(logging.ERROR)

    # raise_app_exceptions=False：Starlette 处理完 500 后仍会把异常重新抛出
    # 给服务器去记，这里要的是客户端看到的那个响应
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://booksoul.test") as c:
        r = await c.get(
            "/api/brains",
            headers={"X-Brain-Key": FAKE_KEY, "Authorization": f"Bearer {token}"},
        )

    assert r.status_code == 500
    assert FAKE_KEY not in r.text
    assert "X-Brain-Key" not in r.text
    assert "Traceback" not in r.text and "RuntimeError" not in r.text
    # 给用户的是一句人话，不是一个状态码
    assert "出" in r.text or "错" in r.text

    # 服务端这边该留下回溯（否则出事没法查），但那条日志里不带请求上下文
    logged = "\n".join(rec.getMessage() for rec in caplog.records)
    assert FAKE_KEY not in logged


@pytest.mark.parametrize(
    "url",
    [
        "https://sk-abc123DEF456ghi789@api.example.com/v1",
        "https://api.example.com/v1?api-key=sk-abc123DEF456ghi789jkl",
        "https://api.example.com/v1?token=abcdefghijklmnopqrstuvwxyz0123",
    ],
)
def test_端点地址里夹带key会被拦下(url):
    """把 key 塞进 URL 是绕开「不存 key」那条规则最省事的方式。

    库里那两条 CHECK 挡住了 userinfo 段；查询串挡不住（Azure 的 `?api-version=`
    是合法的，一刀切会误伤），所以那一侧在这里判。
    """
    from booksoul_api.services.brain_service import key_in_url

    assert key_in_url(url), url


@pytest.mark.parametrize(
    "url",
    [
        "https://api.deepseek.com/v1",
        "http://localhost:11434/v1",
        "https://x.openai.azure.com/openai/deployments/gpt4?api-version=2024-02-01",
        "https://api.example.com/v1?stream=true&mode=compatible",
    ],
)
def test_正常端点地址不会被误伤(url):
    from booksoul_api.services.brain_service import key_in_url

    assert not key_in_url(url), url


def test_uvicorn自带的访问日志被关掉了():
    """它记的是**完整 URL**，含 query string。

    上面那个白名单中间件再克制，只要这一行还在，「key 不进日志」就是一句空话——
    而且它是框架默认打开的，不做任何事就是开着。
    """
    import logging as std_logging

    from booksoul_api.core.logging import configure

    access = std_logging.getLogger("uvicorn.access")
    access.disabled = False
    try:
        configure()
        assert access.disabled is True
        # 但我们自己那条要活着，否则「日志干净」只是因为什么都没记
        assert std_logging.getLogger("booksoul_api.access").isEnabledFor(
            std_logging.INFO
        )
    finally:
        access.disabled = False


def test_一轮提问打印出来不含key():
    """`Turn` 把大脑配置整个装进去了，于是它自己也成了一个 repr 泄漏口。

    `BrainConfig` 那一层已经挡住了，但外面再包一层就可能白挡——而它恰好是
    后台任务出事时最容易被打印的那个对象：异常回溯里的局部变量、
    `logger.exception` 里的 `%r`、pytest 断言失败时打印的入参，都是它。
    """
    from booksoul_api.services import chapter_selector as selector
    from booksoul_api.services.turn import (
        BookRef,
        BrainRef,
        Budget,
        Draft,
        Standing,
        Turn,
    )

    # `Draft` 与 `Turn` 各是一层包装，两层都要验
    draft = Draft(
        conversation_id=1,
        personas=["scholar"],
        question="他为什么沉沦？",
        answering="scholar",
        book=BookRef(
            id=7,
            title="悉达多",
            storage_path="/tmp/library/7/悉达多",
            word_count=48155,
            chapters=[selector.ChapterIn(id=1, idx=1, title="序", word_count=10)],
        ),
        budget=Budget(chars=60000, reselect=False),
        brain=BrainRef(name="claude", cfg=BrainConfig(name="claude", api_key=FAKE_KEY)),
        reflection=False,
    )
    turn = Turn.of(draft, Standing(turn=1, history=[], locked=[], reuse=None))

    for wrapped in (draft, turn):
        assert FAKE_KEY not in repr(wrapped)
        assert "sk-ant" not in repr(wrapped)
    # 照常取得到——这是隐藏，不是加密
    assert turn.brain.cfg.api_key == FAKE_KEY


async def test_换成会话快照的模型之后_真正调模型的配置打印出来仍不含key(
    db_session, fake_brain
):
    """提问路径会用会话快照的模型**另造一份**配置交给引擎。

    另造的那一份是 key 重新进 `repr` 的机会：若有人把它改写成「按字段重建一个
    新的 dataclass」，而新类型忘了 `repr=False`，这一条就红。
    """
    from test_chat import _drain, _reader_with_book

    from booksoul_api.services.chat_service import ChatService

    user, book = await _reader_with_book(db_session)
    service = ChatService(db_session)
    conversation = await service.start(
        user_id=user.id, book_id=book.id, persona="scholar", brain_name="claude"
    )
    await _drain(
        await service.ask(
            conversation_id=conversation.id,
            user_id=user.id,
            question="问题",
            brain_key=FAKE_KEY,
        )
    )

    used = fake_brain.calls[-1]["cfg"]
    assert used.api_key == FAKE_KEY  # key 确实送到了引擎
    assert FAKE_KEY not in repr(used)
