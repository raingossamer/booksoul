"""列出模型：填了 key 之后，这家到底有哪些模型可选（界面稿⑧屏「换模型」）。

替身一律换在**引擎的 HTTP 出口**（`urllib.request.urlopen`），不在服务层另开分支——
与 `conftest.fake_brain` 同一个思路：服务层要的正是「和真请求走同一条路」。

这里盯的几件事：

* 地址与鉴权头与 `chat` 用的是同一套拼法 —— 两套各拼各的，迟早一个带 `/v1`、一个不带
* 供应商报了窗口就取到 —— 各家字段名不一样
* 回给前端的只有 id 与窗口 —— 对方响应里的其他字段一个都不带出去
* key 经 `X-Brain-Key` 送来、转给供应商，然后就没了
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fakes import FAKE_KEY

from booksoul.brain import list_models
from booksoul.config import BrainConfig


class _Reply:
    """`urlopen` 的替身返回值：一个带 JSON 响应体的上下文管理器。"""

    def __init__(self, body: dict) -> None:
        self._raw = json.dumps(body).encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None


class _Vendor:
    """一家假的 OpenAI 兼容供应商。记下收到的每一个请求。"""

    def __init__(self, body: dict) -> None:
        self.body = body
        self.requests: list = []

    def __call__(self, req, timeout=None):
        self.requests.append((req, timeout))
        return _Reply(self.body)


_QWEN = BrainConfig(
    name="qwen",
    provider="openai",
    model="qwen-plus",
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1/",
    api_key_env="DASHSCOPE_API_KEY",
    api_key=FAKE_KEY,
)


# ---------------------------------------------------------------- 引擎


def test_openai兼容端点列出模型_照供应商原序给出id():
    vendor = _Vendor(
        {
            "object": "list",
            "data": [
                {"id": "text-embedding-v3", "object": "model", "owned_by": "system"},
                {"id": "qwen-max", "object": "model", "owned_by": "system"},
                {"id": "qwen-plus", "object": "model", "owned_by": "system"},
            ],
        }
    )

    with patch("urllib.request.urlopen", vendor):
        found = list_models(_QWEN)

    assert [m.id for m in found] == ["text-embedding-v3", "qwen-max", "qwen-plus"]
    assert all(m.context_tokens == 0 for m in found)  # 没报窗口就是 0，不猜

    req, timeout = vendor.requests[0]
    # 与 chat 同一套拼法：末尾那个 / 被吃掉，不会拼出 //models
    assert req.full_url == "https://dashscope.aliyuncs.com/compatible-mode/v1/models"
    assert req.get_method() == "GET"
    assert req.get_header("Authorization") == f"Bearer {FAKE_KEY}"
    # 列个模型不该等十分钟：chat 的 600 秒是给长回答的
    assert timeout is not None and timeout <= 15


def test_供应商在条目里报了窗口就取到():
    """字段名各家不同。这几个是公开文档里见过的；实测之后再补（切片 7）。"""
    vendor = _Vendor(
        {
            "data": [
                {"id": "a", "context_length": 131_072},
                {"id": "b", "context_window": 200_000},
                {"id": "c", "max_context_length": 32_768},
                {"id": "d", "max_model_len": 8_192},
                {"id": "e", "context_length": "不是数字"},
            ]
        }
    )

    with patch("urllib.request.urlopen", vendor):
        found = {m.id: m.context_tokens for m in list_models(_QWEN)}

    assert found == {"a": 131_072, "b": 200_000, "c": 32_768, "d": 8_192, "e": 0}


def test_本机端点不需要key_请求里就没有鉴权头():
    vendor = _Vendor({"data": [{"id": "qwen2.5:14b"}]})
    local = BrainConfig(
        name="ollama",
        provider="openai",
        model="qwen2.5:14b",
        base_url="http://localhost:11434/v1",
        api_key_env="",
    )

    with patch("urllib.request.urlopen", vendor):
        assert [m.id for m in list_models(local)] == ["qwen2.5:14b"]

    req, _ = vendor.requests[0]
    assert req.get_header("Authorization") is None


# ---------------------------------------------------------------- 接口


async def _token(client, email: str = "reader@example.com") -> str:
    r = await client.post(
        "/api/auth/register", json={"email": email, "password": "一句足够长的中文密码"}
    )
    assert r.status_code == 201, r.text
    return r.json()["accessToken"]


def _headers(token: str, key: str = FAKE_KEY) -> dict:
    return {"Authorization": f"Bearer {token}", "X-Brain-Key": key}


async def test_列出模型_对话类排前_明显不是对话的沉底_各档保持原序(client):
    token = await _token(client)
    vendor = _Vendor(
        {
            "data": [
                {"id": "text-embedding-v3"},
                {"id": "qwen-max"},
                {"id": "qwen-turbo"},
                {"id": "gte-rerank"},
                {"id": "Qwen2.5-7B-Instruct"},
                {"id": "deepseek-chat", "context_length": 65_536},
            ]
        }
    )

    with patch("urllib.request.urlopen", vendor):
        r = await client.get("/api/brains/qwen/models", headers=_headers(token))

    assert r.status_code == 200, r.text
    body = r.json()
    assert [m["id"] for m in body["items"]] == [
        "qwen-turbo",
        "Qwen2.5-7B-Instruct",
        "deepseek-chat",
        "qwen-max",
        "text-embedding-v3",
        "gte-rerank",
    ]
    assert body["items"][2]["contextTokens"] == 65_536
    assert body["items"][0]["contextTokens"] == 0
    # 「共 N 个，来自 {地址}」：被问的就是预设的那个地址
    assert body["source"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"


async def test_回给前端的只有id与窗口_对方的其他字段一个都不带(client):
    token = await _token(client)
    vendor = _Vendor(
        {
            "object": "list",
            "internal_hint": "10.0.0.7 的管理后台",
            "data": [
                {
                    "id": "qwen-plus",
                    "owned_by": "SECRET-OWNER-MARK",
                    "permission": [{"allow_view": True}],
                }
            ],
        }
    )

    with patch("urllib.request.urlopen", vendor):
        r = await client.get("/api/brains/qwen/models", headers=_headers(token))

    assert r.status_code == 200, r.text
    assert set(r.json()) == {"items", "source"}
    assert r.json()["items"] == [{"id": "qwen-plus", "contextTokens": 0}]
    assert "SECRET-OWNER-MARK" not in r.text
    assert "10.0.0.7" not in r.text


async def test_key从请求头转给供应商_不出现在响应与日志里(client, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    token = await _token(client)
    vendor = _Vendor({"data": [{"id": "qwen-plus"}]})

    with patch("urllib.request.urlopen", vendor):
        r = await client.get("/api/brains/qwen/models", headers=_headers(token))

    assert r.status_code == 200, r.text
    req, _ = vendor.requests[0]
    assert req.get_header("Authorization") == f"Bearer {FAKE_KEY}"
    assert FAKE_KEY not in r.text
    assert FAKE_KEY not in "\n".join(rec.getMessage() for rec in caplog.records)


# ---------------------------------------------------------------- 失败与边界
#
# 四类失败各自一句人话、各自一个写死的 code。**失败时绝不把对方响应原文带回来**：
# 列模型会按用户存下的自定义地址发请求，回显原文就是「借服务器之手窥探内网」。
# 所以特征串同时塞进响应体与状态行的原因短语——两处都是对方说了算的。

_MARK = "INTRANET-SECRET-7f3a"


class _Failing:
    """一家会出错的供应商。`how` 决定怎么错。"""

    def __init__(self, how) -> None:
        self.how = how
        self.requests: list = []

    def __call__(self, req, timeout=None):
        import io
        import urllib.error

        self.requests.append((req, timeout))
        if isinstance(self.how, int):
            raise urllib.error.HTTPError(
                req.full_url,
                self.how,
                f"reason {_MARK}",
                {},
                io.BytesIO(f'{{"error": {{"message": "{_MARK}"}}}}'.encode()),
            )
        if self.how == "timeout":
            raise TimeoutError("timed out")
        if self.how == "connect-timeout":
            # 连接阶段超时：urllib 把它包成 URLError，reason 是 TimeoutError
            raise urllib.error.URLError(TimeoutError("timed out"))
        if self.how == "refused":
            raise urllib.error.URLError(ConnectionRefusedError(f"refused {_MARK}"))
        # 返回格式不认识：200，但不是 {"data": [...]}
        raw = self.how.encode("utf-8")

        class _R:
            def read(self):
                return raw

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return None

        return _R()


@pytest.mark.parametrize(
    ("how", "kind"),
    [
        (401, "auth"),
        (403, "auth"),
        (404, "unsupported"),
        (405, "unsupported"),
        (400, "unsupported"),
        (f"<html>{_MARK}</html>", "unsupported"),
        ('{"models": ["a"]}', "unsupported"),
        ('["a", "b"]', "unsupported"),
        ("timeout", "timeout"),
        ("connect-timeout", "timeout"),
        ("refused", "network"),
        (503, "server"),
        (429, "rate_limit"),
    ],
)
def test_列模型的失败各归其类_且对方说的话一个字都不带出来(how, kind):
    import traceback

    from booksoul.brain import BrainError

    with (
        patch("urllib.request.urlopen", _Failing(how)),
        pytest.raises(BrainError) as caught,
    ):
        list_models(_QWEN)

    assert caught.value.kind == kind
    # 异常消息、以及整条异常链的回溯（日志里记的就是它）都不许有对方的原话
    whole = "".join(traceback.format_exception(caught.value))
    assert _MARK not in whole


@pytest.mark.parametrize(
    ("how", "code"),
    [
        (401, "brain_auth"),
        (404, "brain_unsupported"),
        ('{"models": []}', "brain_unsupported"),
        ("timeout", "brain_timeout"),
        ("refused", "brain_network"),
        # 对方服务暂时出错、限流：用户能做的事与连不上一样——稍后再试
        (503, "brain_network"),
        (429, "brain_network"),
    ],
)
async def test_失败给502与写死的code_不回显原文(client, caplog, how, code):
    import logging

    caplog.set_level(logging.DEBUG)
    token = await _token(client)

    with patch("urllib.request.urlopen", _Failing(how)):
        r = await client.get("/api/brains/qwen/models", headers=_headers(token))

    assert r.status_code == 502, r.text
    assert r.json()["code"] == code
    assert r.json()["detail"]  # 一句人话
    assert _MARK not in r.text
    assert _MARK not in "\n".join(rec.getMessage() for rec in caplog.records)
    assert FAKE_KEY not in r.text


async def test_需要key而没带_先说填key_并且不去敲供应商的门(client):
    token = await _token(client)
    vendor = _Vendor({"data": [{"id": "qwen-plus"}]})

    with patch("urllib.request.urlopen", vendor):
        r = await client.get(
            "/api/brains/qwen/models", headers={"Authorization": f"Bearer {token}"}
        )

    assert r.status_code == 400, r.text
    assert "先填 key" in r.json()["detail"]
    assert vendor.requests == []


async def test_本机端点不带key也能列(client):
    token = await _token(client)
    vendor = _Vendor({"data": [{"id": "qwen2.5:14b"}, {"id": "llama3.1:8b"}]})

    with patch("urllib.request.urlopen", vendor):
        r = await client.get(
            "/api/brains/ollama/models", headers={"Authorization": f"Bearer {token}"}
        )

    assert r.status_code == 200, r.text
    assert [m["id"] for m in r.json()["items"]] == ["qwen2.5:14b", "llama3.1:8b"]
    req, _ = vendor.requests[0]
    assert req.get_header("Authorization") is None


async def test_别人的自定义端点与不存在的同一个404(client):
    owner = await _token(client, "owner@example.com")
    r = await client.post(
        "/api/brains",
        headers={"Authorization": f"Bearer {owner}"},
        json={
            "name": "书房那台",
            "model": "my-model",
            "baseUrl": "https://llm.example.com/v1",
        },
    )
    assert r.status_code == 201, r.text
    stranger = await _token(client, "stranger@example.com")
    vendor = _Vendor({"data": [{"id": "my-model"}]})

    with patch("urllib.request.urlopen", vendor):
        theirs = await client.get(
            "/api/brains/书房那台/models", headers=_headers(stranger)
        )
        nowhere = await client.get(
            "/api/brains/没有这个/models", headers=_headers(stranger)
        )

    assert theirs.status_code == nowhere.status_code == 404
    assert theirs.json()["detail"].replace("书房那台", "X") == nowhere.json()[
        "detail"
    ].replace("没有这个", "X")
    assert vendor.requests == []


# ---------------------------------------------------------------- Anthropic
#
# claude 走官方 SDK 的模型列表接口，与它的 chat 走 SDK 一致。替身换在引擎
# 构造 SDK 客户端的那一处（`anthropic.Anthropic`）。


_CLAUDE = BrainConfig(
    name="claude",
    provider="anthropic",
    model="claude-opus-5",
    api_key_env="ANTHROPIC_API_KEY",
    api_key=FAKE_KEY,
)


def _info(model_id: str, window: int | None = None):
    """SDK 的 `ModelInfo` 替身。`display_name` 塞特征串：它不该被带出去。"""
    from types import SimpleNamespace

    return SimpleNamespace(id=model_id, max_input_tokens=window, display_name=_MARK)


def _sdk_client(result):
    """一个假的 `anthropic.Anthropic`：`models.list()` 返回 `result`，或抛它。"""
    calls: list = []

    class _Models:
        def list(self, **kw):
            calls.append(kw)
            if isinstance(result, Exception):
                raise result
            return iter(result)

    class _Client:
        def __init__(self, **kw) -> None:
            calls.append({"init": kw})
            self.models = _Models()

    _Client.calls = calls
    return _Client


def _sdk_error(kind: str):
    import anthropic
    import httpx2

    request = httpx2.Request("GET", "https://api.anthropic.com/v1/models")
    if kind == "timeout":
        return anthropic.APITimeoutError(request=request)
    if kind == "network":
        return anthropic.APIConnectionError(message=_MARK, request=request)
    status, cls = {
        "401": (401, anthropic.AuthenticationError),
        "403": (403, anthropic.PermissionDeniedError),
        "404": (404, anthropic.NotFoundError),
        "500": (500, anthropic.InternalServerError),
    }[kind]
    response = httpx2.Response(status, request=request, text=_MARK)
    return cls(_MARK, response=response, body={"error": {"message": _MARK}})


def test_claude列出模型_窗口取自sdk的max_input_tokens():
    client = _sdk_client([_info("claude-opus-5", 1_000_000), _info("claude-x")])

    with patch("anthropic.Anthropic", client):
        found = list_models(_CLAUDE)

    assert [(m.id, m.context_tokens) for m in found] == [
        ("claude-opus-5", 1_000_000),
        ("claude-x", 0),
    ]
    init, listed = client.calls
    assert init["init"]["api_key"] == FAKE_KEY
    assert 0 < listed["timeout"] <= 15
    # 一页拿够：聚合 SDK 默认一页 20 个，漏掉后面的就等于说「没有」
    assert listed["limit"] >= 100


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        ("401", "auth"),
        ("403", "auth"),
        ("404", "unsupported"),
        ("timeout", "timeout"),
        ("network", "network"),
        ("500", "server"),
    ],
)
def test_sdk的失败映射到同样几类_对方的话不带出来(error, kind):
    import traceback

    from booksoul.brain import BrainError

    with (
        patch("anthropic.Anthropic", _sdk_client(_sdk_error(error))),
        pytest.raises(BrainError) as caught,
    ):
        list_models(_CLAUDE)

    assert caught.value.kind == kind
    assert _MARK not in "".join(traceback.format_exception(caught.value))


def test_没装sdk时说清怎么装_而不是崩(monkeypatch):
    import sys

    from booksoul.brain import BrainError

    # None 占位 = 这个模块 import 不进来
    monkeypatch.setitem(sys.modules, "anthropic", None)

    with pytest.raises(BrainError) as caught:
        list_models(_CLAUDE)

    assert "pip install anthropic" in str(caught.value)
    assert caught.value.kind == "dependency"


async def test_claude那一行列模型_来源写的是官方地址(client):
    token = await _token(client)
    sdk = _sdk_client([_info("claude-opus-5", 1_000_000)])

    with patch("anthropic.Anthropic", sdk):
        r = await client.get("/api/brains/claude/models", headers=_headers(token))

    assert r.status_code == 200, r.text
    assert r.json() == {
        "items": [{"id": "claude-opus-5", "contextTokens": 1_000_000}],
        "source": "https://api.anthropic.com",
    }


async def test_服务器没装sdk_告诉用户换一个大脑_怎么装只写给管理员(
    client, monkeypatch, caplog
):
    import logging
    import sys

    caplog.set_level(logging.ERROR)
    token = await _token(client)
    monkeypatch.setitem(sys.modules, "anthropic", None)

    r = await client.get("/api/brains/claude/models", headers=_headers(token))

    assert r.status_code == 502, r.text
    assert "code" not in r.json()  # 不是四类里的任何一类：重试、换 key、手填都救不了
    assert "pip" not in r.json()["detail"]  # 用户做不了 pip install
    assert "换一个大脑" in r.json()["detail"]
    assert any("pip install anthropic" in rec.getMessage() for rec in caplog.records)


# ---------------------------------------------------------------- 实测形状（切片 7）
#
# 2026-09-22 用真 key 在⑧屏实测（key 不进对话，只报告看到了什么）。


async def test_实测DashScope兼容模式_对话模型大多没有关键词_非对话的要沉底(client):
    """qwen（DashScope 兼容模式）：列得出，261 个，**不报窗口**。

    列表里混着别家的模型（`kimi/…`、`MiniMax/…`），而对话模型大多不带
    chat / instruct / turbo——只把带关键词的提前，embedding 与音频就会夹在
    qwen-max、glm 这些真正要找的模型中间。所以明显不是对话的沉到最后。
    """
    token = await _token(client)
    vendor = _Vendor(
        {
            "data": [
                {"id": "qwen3.7-text-embedding"},
                {"id": "qwen-audio-3.0-realtime-flash"},
                {"id": "qwen-audio-3.0-realtime-plus"},
                {"id": "glm-5.2-fast-preview"},
                {"id": "kimi/kimi-k2.7-code-highspeed"},
                {"id": "xiaomi/mimo-v2.5-pro"},
                {"id": "MiniMax/MiniMax-M3"},
                {"id": "qwen-plus"},
                {"id": "wanx2.1-t2i-turbo"},
                {"id": "paraformer-realtime-v2"},
                {"id": "cosyvoice-v2"},
                {"id": "qwen-turbo"},
                {"id": "qwen-max"},
            ]
        }
    )

    with patch("urllib.request.urlopen", vendor):
        r = await client.get("/api/brains/qwen/models", headers=_headers(token))

    assert r.status_code == 200, r.text
    assert [m["id"] for m in r.json()["items"]] == [
        # 带对话关键词的
        "qwen-turbo",
        # 看不出来的：照供应商原序
        "glm-5.2-fast-preview",
        "kimi/kimi-k2.7-code-highspeed",
        "xiaomi/mimo-v2.5-pro",
        "MiniMax/MiniMax-M3",
        "qwen-plus",
        "qwen-max",
        # 明显不是对话的：embedding、音频 / realtime、文生图、语音识别、语音合成。
        # 「不是对话」先于「带关键词」判：wanx…-turbo 是画图的，不因为 turbo 排到前面
        "qwen3.7-text-embedding",
        "qwen-audio-3.0-realtime-flash",
        "qwen-audio-3.0-realtime-plus",
        "wanx2.1-t2i-turbo",
        "paraformer-realtime-v2",
        "cosyvoice-v2",
    ]
    # 实测没有窗口字段：一律 0，保存时回落到内置表 / 估计（切片 8）
    assert {m["contextTokens"] for m in r.json()["items"]} == {0}
