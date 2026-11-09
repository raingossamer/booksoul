"""大脑：可插拔的模型后端。

两个后端就覆盖了绝大多数场景：

* **anthropic** —— 用官方 SDK。它值得单独一条路径，因为 prompt 缓存能把整本书
  缓住：第一次问付全价，之后每一轮书的部分只按十分之一计费。一本 50 万字的书
  反复问，这个差别是数量级的。
* **openai** —— 通用的 OpenAI 兼容端点。DeepSeek、Kimi、智谱、通义、硅基流动、
  OpenAI 本身，以及本地的 Ollama / LM Studio / vLLM，全都是这一套协议，
  换供应商只是换 base_url 和 model。这条路径用标准库发 HTTP，不引入依赖。

两个后端对外是同一个 chat() 签名，上层不需要知道自己在跟谁说话。
「这个 key 下有哪些模型」也一样：`list_models(cfg)`，两家各走各的协议。
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from .config import BrainConfig

# 书正文前面这段框架语是固定的，和人格无关。放在最前面既给了模型角色框架，
# 又保证了"框架 + 全书"这个前缀在切换人格时不变，缓存才能命中。
_FRAME = """你将扮演一个可以与读者对话的角色，依据是下面这本书的全文。

对话过程中始终记住：你的依据只有这本书。不要把从别处知道的关于这本书、
这位作者的评论当成书里的内容；需要引用时引下面的原文。

===== 全书正文开始 =====
"""

_FRAME_END = "\n===== 全书正文结束 =====\n"


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0

    def summary(self) -> str:
        parts = [f"输入 {self.input_tokens:,}", f"输出 {self.output_tokens:,}"]
        if self.cache_read:
            parts.append(f"缓存命中 {self.cache_read:,}")
        if self.cache_write:
            parts.append(f"写入缓存 {self.cache_write:,}")
        return "　".join(parts)


@dataclass
class Reply:
    text: str = ""
    model: str = ""
    usage: Usage = field(default_factory=Usage)
    stop_reason: str = ""


@dataclass(frozen=True)
class ModelEntry:
    """供应商那边的一个模型。

    只有 id 与窗口：对方响应里的其他字段一概不带出来。列模型会按用户存下的
    自定义地址发请求，把响应原样转回去，就是开了一个「借服务器之手窥探内网」的口子。
    """

    id: str
    # 供应商在列表里报了才有；0 = 没报，不猜
    context_tokens: int = 0


# 各家在模型列表里报上下文窗口用的字段名，取第一个是正整数的。
# 以实测为准逐家补（brain-model-picker 切片 7）。
_WINDOW_FIELDS = (
    "context_length",
    "context_window",
    "max_context_length",
    "max_model_len",
)

# 列个模型不该等十分钟：chat 的 600 秒是给长回答的
_LIST_TIMEOUT = 10


class BrainError(RuntimeError):
    """所有后端的错误都归一成这一个。

    `message` 是给人看的。`kind` 是给代码看的——它是同一个判断的另一半。

    为什么要多这一个字段：这些失败在 HTTP 上长得几乎一样（都是 4xx），
    但用户要做的事完全不同。认证失败去填 key，限流等一会儿，模型不存在改名字，
    而**内容审核只能换个大脑或者调小载入量**。调用方要么拿到机读的结论，
    要么就只能去匹配一句中文——而那句话改个措辞就会把分支改坏，并且不报错。

    另一个更实际的理由：出路是**分场合**的。这里写的出路是命令行的
    （`--brain` / `--budget`），在浏览器里照抄出去就是一句无法执行的建议。
    有了 `kind`，网页那一侧能自己写一套。

    取值：`moderation` / `auth` / `rate_limit` / `not_found` / `server`
    / `network`，认不出来就是空串。空串是默认值，所以老的 `BrainError("…")`
    原样可用。列模型那条路另有 `unsupported`（这个端点不给列表）与 `timeout`；
    `dependency` 是本机缺了这个后端的 SDK——那是管理员的事，不是用户的。
    """

    def __init__(self, *args, kind: str = "") -> None:
        super().__init__(*args)
        self.kind = kind


def build_system(book_text: str, persona_prompt: str) -> list[tuple[str, bool]]:
    """拼系统提示，返回 [(文本, 是否在此处设缓存断点)]。

    顺序是刻意的：框架语 → 全书 →（缓存断点）→ 人格。
    人格放在断点之后，切换人格时整本书的缓存仍然有效。
    """
    return [
        (_FRAME + book_text.strip() + _FRAME_END, True),
        (persona_prompt.strip(), False),
    ]


# --------------------------------------------------------------------------
# Anthropic
# --------------------------------------------------------------------------


class AnthropicBrain:
    def __init__(self, cfg: BrainConfig) -> None:
        self.cfg = cfg
        try:
            import anthropic
        except ImportError as exc:
            raise BrainError(
                "用 Anthropic 需要官方 SDK：pip install anthropic\n"
                "（或改用别的大脑，例如 --brain deepseek）",
                kind="dependency",
            ) from exc
        self._anthropic = anthropic
        kwargs = {}
        if cfg.api_key:
            kwargs["api_key"] = cfg.api_key
        if cfg.base_url:
            kwargs["base_url"] = cfg.base_url
        # 不传 api_key 时 SDK 会自己找环境变量或 `ant auth login` 的配置
        self.client = anthropic.Anthropic(**kwargs)

    def models(self) -> list[ModelEntry]:
        """SDK 的模型列表接口，一页拿够（默认一页只有 20 个）。

        窗口取 `max_input_tokens`。失败按与 OpenAI 兼容那一支相同的几类归类，
        同样 `from None`：SDK 的异常里挂着对方的响应体与原话。
        """
        a = self._anthropic
        where = self.cfg.base_url or "api.anthropic.com"
        try:
            found = list(self.client.models.list(limit=1000, timeout=_LIST_TIMEOUT))
        # 超时是连接错误的子类，得先接
        except a.APITimeoutError:
            raise BrainError(_list_failure("timeout", where), kind="timeout") from None
        except a.APIConnectionError:
            raise BrainError(_list_failure("network", where), kind="network") from None
        except a.APIStatusError as exc:
            kind = _list_kind(exc.status_code)
            raise BrainError(
                _list_failure(kind, where, exc.status_code), kind=kind
            ) from None
        return [
            ModelEntry(id=m.id, context_tokens=m.max_input_tokens or 0) for m in found
        ]

    def chat(
        self,
        system: list[tuple[str, bool]],
        messages: list[dict],
        *,
        on_text: Callable[[str], None] | None = None,
    ) -> Reply:
        blocks = []
        for text, cached in system:
            block: dict = {"type": "text", "text": text}
            if cached:
                # 1 小时 TTL：一次读书会话通常在这个尺度上
                block["cache_control"] = {"type": "ephemeral", "ttl": "1h"}
            blocks.append(block)

        try:
            with self.client.messages.stream(
                model=self.cfg.model,
                max_tokens=self.cfg.max_tokens,
                system=blocks,
                messages=messages,
            ) as stream:
                if on_text:
                    for chunk in stream.text_stream:
                        on_text(chunk)
                msg = stream.get_final_message()
        except self._anthropic.AuthenticationError as exc:
            raise BrainError(
                f"Anthropic 认证失败：设置 {self.cfg.api_key_env}，或运行 `ant auth login`",
                kind="auth",
            ) from exc
        except self._anthropic.RateLimitError as exc:
            retry = exc.response.headers.get("retry-after", "稍后")
            raise BrainError(
                f"触发限流，请 {retry} 秒后重试", kind="rate_limit"
            ) from exc
        except self._anthropic.NotFoundError as exc:
            raise BrainError(f"模型不存在：{self.cfg.model}", kind="not_found") from exc
        except self._anthropic.APIStatusError as exc:
            raise BrainError(
                f"Anthropic 接口错误 {exc.status_code}：{exc.message}",
                kind=_kind_of_http(exc.status_code, exc.message or ""),
            ) from exc
        except self._anthropic.APIConnectionError as exc:
            raise BrainError(f"连接 Anthropic 失败：{exc}", kind="network") from exc

        text = "".join(b.text for b in msg.content if b.type == "text")
        u = msg.usage
        return Reply(
            text=text,
            model=msg.model,
            stop_reason=msg.stop_reason or "",
            usage=Usage(
                input_tokens=u.input_tokens,
                output_tokens=u.output_tokens,
                cache_read=getattr(u, "cache_read_input_tokens", 0) or 0,
                cache_write=getattr(u, "cache_creation_input_tokens", 0) or 0,
            ),
        )


# --------------------------------------------------------------------------
# OpenAI 兼容端点
# --------------------------------------------------------------------------


class OpenAICompatBrain:
    """任何讲 /chat/completions 的端点。用标准库发请求，不引额外依赖。"""

    def __init__(self, cfg: BrainConfig) -> None:
        self.cfg = cfg
        if not cfg.base_url:
            raise BrainError(f"大脑 {cfg.name!r} 没有 base_url")
        if cfg.needs_key and not cfg.api_key:
            raise BrainError(
                f"缺少 API key：请设置环境变量 {cfg.api_key_env}\n"
                f'  PowerShell:  $env:{cfg.api_key_env} = "你的key"\n'
                f"  bash:        export {cfg.api_key_env}=你的key"
            )

    def _url(self, path: str = "/chat/completions") -> str:
        return self.cfg.base_url.rstrip("/") + path

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.cfg.api_key:
            headers["Authorization"] = f"Bearer {self.cfg.api_key}"
        return headers

    def models(self) -> list[ModelEntry]:
        """`GET {base_url}/models`。与 chat 同一套地址拼法与鉴权头。

        失败一律抛 `BrainError`，`kind` ∈ auth / unsupported / timeout / network /
        rate_limit / server。**对方说的话一个字都不带出来**：不读错误响应体，状态行
        的原因短语也不进消息；异常链用 `from None` 截断——`HTTPError` 挂在链上，
        一条 `logger.exception` 就会把对方的原因短语连同回溯一起写进日志。
        这个请求可能是按用户存下的自定义地址发出去的，回显就是窥探内网的口子。
        """
        import urllib.error
        import urllib.request

        where = self.cfg.base_url
        req = urllib.request.Request(
            self._url("/models"), headers=self._headers(), method="GET"
        )
        try:
            with urllib.request.urlopen(req, timeout=_LIST_TIMEOUT) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            kind = _list_kind(exc.code)
            raise BrainError(_list_failure(kind, where, exc.code), kind=kind) from None
        except urllib.error.URLError as exc:
            kind = "timeout" if isinstance(exc.reason, TimeoutError) else "network"
            raise BrainError(_list_failure(kind, where), kind=kind) from None
        except TimeoutError:
            raise BrainError(_list_failure("timeout", where), kind="timeout") from None

        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            body = None
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list):
            # 200 了，但不是 {"data": [...]}：这个端点不按 OpenAI 的样子给列表
            raise BrainError(
                _list_failure("unsupported", where), kind="unsupported"
            ) from None
        return [
            _entry_of(item)
            for item in data
            if isinstance(item, dict) and item.get("id")
        ]

    def chat(
        self,
        system: list[tuple[str, bool]],
        messages: list[dict],
        *,
        on_text: Callable[[str], None] | None = None,
    ) -> Reply:
        import urllib.error
        import urllib.request

        payload: dict = {
            "model": self.cfg.model,
            "max_tokens": self.cfg.max_tokens,
            "messages": [
                {"role": "system", "content": "\n\n".join(t for t, _ in system)},
                *messages,
            ],
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if self.cfg.temperature is not None:
            payload["temperature"] = self.cfg.temperature

        req = urllib.request.Request(
            self._url(),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )

        chunks: list[str] = []
        usage = Usage()
        model = self.cfg.model
        stop_reason = ""
        ended = False

        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                for line in _iter_sse(resp):
                    if line == "[DONE]":
                        ended = True
                        break
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event.get("model"):
                        model = event["model"]
                    if event.get("usage"):
                        u = event["usage"]
                        usage.input_tokens = u.get("prompt_tokens", 0) or 0
                        usage.output_tokens = u.get("completion_tokens", 0) or 0
                        details = u.get("prompt_tokens_details") or {}
                        usage.cache_read = details.get("cached_tokens", 0) or 0
                    for choice in event.get("choices") or []:
                        if choice.get("finish_reason"):
                            stop_reason = choice["finish_reason"]
                        delta = choice.get("delta") or {}
                        piece = delta.get("content")
                        if piece:
                            chunks.append(piece)
                            if on_text:
                                on_text(piece)
        except urllib.error.HTTPError as exc:
            # 只取一次 detail：`exc.read()` 是个流，读第二次是空的
            detail = _detail_of(exc)
            raise BrainError(
                _http_error_message(self.cfg, exc.code, detail),
                kind=_kind_of_http(exc.code, detail),
            ) from exc
        except urllib.error.URLError as exc:
            raise BrainError(
                f"连不上 {self.cfg.base_url}：{exc.reason}\n"
                "（本地模型请确认服务已启动；在线服务请检查网络或代理）",
                kind="network",
            ) from exc
        except TimeoutError as exc:
            raise BrainError(f"请求超时：{self.cfg.base_url}", kind="network") from exc

        # 流没了，但既没收到 [DONE] 也没收到 finish_reason —— 对面是在说完之前
        # 把连接断了。这里必须报错：**一段被截断的回答读起来和完整回答一模一样**
        # （模型停下时最后一句常常正好是个句号），不说破，用户就会把半截话当成
        # 完整答案。这是「不要假装成功」在对话这一侧的形态。
        if not ended and not stop_reason:
            raise BrainError(
                f"{self.cfg.name} 在说完之前断开了连接。已经说出来的部分保留着，"
                "可以让它接着往下说，或者重问一次。"
            )

        return Reply(
            text="".join(chunks), model=model, usage=usage, stop_reason=stop_reason
        )


def _entry_of(item: dict) -> ModelEntry:
    tokens = 0
    for name in _WINDOW_FIELDS:
        value = item.get(name)
        # bool 是 int 的子类：True 不是一个窗口
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            tokens = value
            break
    return ModelEntry(id=str(item["id"]), context_tokens=tokens)


def _list_kind(code: int) -> str:
    """列模型那一下的 HTTP 失败属于哪一类。

    认证、限流、服务端错误沿用 `_kind_of_http` 的判法；其余 4xx 一律当作
    「这个端点不提供（或不按这个样子提供）模型列表」——对话接口的 404 是「模型名
    写错了」，列模型接口的 404 是「没有这个接口」，同一个码在这里是另一件事。
    """
    if code in (401, 403):
        return "auth"
    kind = _kind_of_http(code, "")
    return kind if kind in ("rate_limit", "server") else "unsupported"


def _list_failure(kind: str, where: str, code: int = 0) -> str:
    """列模型失败的那句话。只有类别、地址与状态码，没有对方说的任何一个字。"""
    status = f"（HTTP {code}）" if code else ""
    return {
        "auth": f"{where} 没接受这个 key{status}",
        "unsupported": f"{where} 没按 OpenAI 的格式提供模型列表{status}",
        "timeout": f"{where} 在 {_LIST_TIMEOUT} 秒内没回音",
        "network": f"连不上 {where}",
        "rate_limit": f"{where} 限流了{status}",
        "server": f"{where} 服务端出错{status}",
    }[kind]


def _iter_sse(resp) -> Iterable[str]:
    """从 SSE 响应里逐条产出 data: 后面的内容。"""
    for raw in resp:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line or line.startswith(":"):
            continue
        if line.startswith("data:"):
            yield line[5:].strip()


def _detail_of(exc) -> str:
    """从 HTTPError 的响应体里抠出那句人话。

    **只能调一次**：`exc.read()` 是个流。调用方取一次，然后同时喂给
    `_kind_of_http` 和 `_http_error_message`——这两个本来就是同一个判断的两半。
    """
    body = ""
    try:
        body = exc.read().decode("utf-8", errors="replace")[:600]
    except Exception:
        return ""
    try:
        parsed = json.loads(body)
        err = parsed.get("error")
        if isinstance(err, dict):
            detail = err.get("message", "")
        elif isinstance(err, str):
            detail = err
        else:
            detail = ""
        return detail or parsed.get("message", "") or ""
    except Exception:
        return re.sub(r"\s+", " ", body).strip()


def _http_error_message(cfg: BrainConfig, code: int, detail: str) -> str:
    """组一句给**命令行用户**看的话。

    这里的出路是 CLI 的（`--brain` / `--budget`）。网页那一侧不能照抄——
    它按 `BrainError.kind` 自己写一套。同一个结论，两种说法，这是刻意的：
    出路必须是**收到它的人能执行的那一种**（Spec N1）。
    """
    kind = _kind_of_http(code, detail)
    if kind == "moderation":
        # 这是读文学作品时的常见障碍：严肃文学里的暴力、性、宗教、自毁描写，
        # 在部分供应商的内容审核里会被整段拒绝。不是 key 或代码的问题，
        # 所以要明确说清楚，别让人去反复查配置。
        hint = (
            "\n\n这是供应商的内容审核拦下的，不是 key 或配置的问题。"
            "\n文学作品里的暴力、性、宗教、沉沦等描写常会触发国内平台的过滤。"
            "\n可以试："
            "\n  1) 换一个大脑：booksoul ask --brain deepseek/kimi/claude ……"
            "\n  2) 用 --budget 调小载入量，让检索避开触发审核的那一章"
            "\n  3) 用本地模型：booksoul ask --brain ollama --model <你的模型>"
        )
    elif kind == "auth":
        hint = f"\n检查环境变量 {cfg.api_key_env} 里的 key 是否正确、是否有权限。"
    elif kind == "not_found":
        hint = f"\n检查 base_url（{cfg.base_url}）和模型名（{cfg.model}）是否正确。"
    elif kind == "rate_limit":
        hint = "\n触发限流或余额不足，稍后重试。"
    elif kind == "server":
        hint = "\n服务端错误，稍后重试。"
    else:
        hint = ""
    return f"{cfg.name} 请求失败 HTTP {code}：{detail or '(无详情)'}{hint}"


def _kind_of_http(code: int, detail: str) -> str:
    """这次失败属于哪一类。认不出来返回空串。

    **它们在 HTTP 上长得几乎一样，用户要做的事却完全不同**——认证失败去填 key，
    限流等一会儿，模型不存在改名字，而内容审核只能换个大脑或者调小载入量。
    混成一句「请求失败」，用户会先把 key 翻出来检查三遍。

    审核要先判：DashScope 把它回成 400，而 400 同时也是「参数写错了」。
    普通的 400 **不冒充审核**——认错了会把用户支去换大脑，而那救不了他。
    """
    if _is_moderation(code, detail):
        return "moderation"
    if code in (401, 403):
        return "auth"
    if code == 404:
        return "not_found"
    if code == 429:
        return "rate_limit"
    if code >= 500:
        return "server"
    return ""


# 各家审核拒绝里出现过的字样。宁可漏判也不要错判：漏判只是少给一条建议，
# 错判会把一个 key 填错的人支去换大脑，而那救不了他。
_MODERATION_MARKS = (
    "inappropriate content",
    "content_filter",
    "content filter",
    "data_inspection",
    "内容审核",
    "敏感",
    "违规",
    "不当内容",
    "risk control",
)


def _is_moderation(code: int, detail: str) -> bool:
    """这个 4xx 是不是内容审核拦的。

    只认 400 / 403 / 451 这三个码：审核在各家那里落在这几个上，
    而把范围放宽会让普通的参数错误开始冒充审核。
    """
    if code not in (400, 403, 451):
        return False
    low = detail.lower()
    return any(mark.lower() in low for mark in _MODERATION_MARKS)


# --------------------------------------------------------------------------


def list_models(cfg: BrainConfig) -> list[ModelEntry]:
    """这个 key 在这家能用哪些模型，照供应商给的顺序。"""
    return create(cfg).models()


def create(cfg: BrainConfig):
    """按配置造一个大脑。"""
    if cfg.provider == "anthropic":
        return AnthropicBrain(cfg)
    if cfg.provider in ("openai", "openai-compat", "compat"):
        return OpenAICompatBrain(cfg)
    raise BrainError(f"不认识的 provider：{cfg.provider}（支持 anthropic / openai）")
