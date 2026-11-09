"""可选的大脑：内置预设 + 用户自己加的 OpenAI 兼容端点，以及「这本书装得下吗」。

**内置预设的真相在引擎的 `booksoul.config`，不在 `brains` 表里。** 表只装用户
自己加的端点。两边同时当真相，改了一处忘了另一处，界面上就会出现一个引擎根本
不认识的大脑名——然后在真正提问那一刻才报错。

上下文额度连同它的来路一律问 `services/context_window`，它建在引擎选章用的那套
判据之上。在这里复制一份，「设置页说装得下、提问时却在挑章节」的那天就到了。

key 一个字节都不经过这里：不进参数、不进返回值、不进库。这一层对 key 唯一做的事
是**拦住它从别的门溜进来**——夹在端点地址里，或者填进了「环境变量名」那一栏。
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlsplit

from sqlalchemy.ext.asyncio import AsyncSession

from booksoul_api.core.errors import (
    ApiError,
    BrainUnavailable,
    ConflictError,
    NotFoundError,
)
from booksoul_api.models.brain import Brain
from booksoul_api.models.brain_choice import BrainChoice
from booksoul_api.repositories.book_repo import BookRepository
from booksoul_api.repositories.brain_choice_repo import BrainChoiceRepository
from booksoul_api.repositories.brain_repo import BrainRepository
from booksoul_api.schemas.brain import BrainCreate, BrainOut, ModelList, ModelOut
from booksoul_api.services.context_window import Window, context_window

# 环境变量名的样子：字母数字下划线，不含连字符。与库里那条 CHECK 同一条正则——
# 真 key（sk-ant-api03-… / sk-…）必然含连字符，会被它挡下。
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

# 查询串里这些参数名一出现就是 key，不看值
_SECRET_PARAMS = frozenset(
    {"key", "apikey", "api_key", "api-key", "token", "access_token", "secret", "auth"}
)

# 跑在自己机器上的端点：Ollama / LM Studio 这一类，不需要 key
_LOOPBACK = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1"})

# 模型列表分三档，档内保持供应商原序：带对话关键词的 → 看不出来的 → 明显不是对话的。
# 聚合平台一列几百个，用户要找的是能对话的那几个。**只调顺序，不删**——判错一个
# 也还在列表里，搜得到。
#
# 光把带关键词的提前不够：实测 DashScope 兼容模式（2026-09-22，261 个）里对话模型
# 大多不带关键词（qwen-max、glm-5.2、kimi-k2.7…），embedding 与音频就夹在它们中间。
_CHATTY = ("chat", "instruct", "turbo")
# 后四个是 DashScope 的产品名：文生图、语音识别、语音合成——用户主要用的就是这一家
_NOT_CHAT = (
    "embed",
    "rerank",
    "audio",
    "tts",
    "asr",
    "speech",
    "realtime",
    "whisper",
    "image",
    "video",
    "moderation",
    "wanx",
    "paraformer",
    "cosyvoice",
    "sambert",
)

logger = logging.getLogger(__name__)

# SDK 自己的默认地址。内置的 claude 预设不填 base_url，但「共 N 个，来自 …」
# 那一句得说得出是哪儿
_ANTHROPIC_OFFICIAL = "https://api.anthropic.com"

# 列模型失败：引擎的 kind → (写死的 code, 给网页用户的那句话)。
# 限流与对方服务出错归进 network：用户能做的事和连不上一样——稍后再试。
# 那句话里没有对方说的任何一个字（引擎那一侧就没带出来）。
_LIST_FAILURES = {
    "auth": (
        "brain_auth",
        "key 没通过验证。检查这一行右边的 key 是不是这一家的、有没有过期。",
    ),
    "unsupported": (
        "brain_unsupported",
        "这个服务没提供模型列表。直接填模型名也一样能用。",
    ),
    "timeout": ("brain_timeout", "等了 10 秒没回音。稍后再试一次。"),
    "network": ("brain_network", "连不上这个服务。检查网络或代理，稍后再试。"),
    "rate_limit": ("brain_network", "这家暂时限流了。稍后再试。"),
    "server": ("brain_network", "这家的服务暂时出错了。稍后再试。"),
}

# 一段长而无意义的 URL-safe 串。Azure 的 ?api-version=2024-02-01 只有 10 个字符，
# 真 key 动辄上百 —— 长度是这里唯一靠得住的分界。
_LONG_OPAQUE = re.compile(r"^[A-Za-z0-9_\-]{20,}$")


def key_in_url(url: str) -> bool:
    """这条端点地址里是不是夹带了一个 key。

    库里那两条 CHECK 挡住了 userinfo 段（`https://sk-xxx@host/v1`）；查询串挡不住，
    因为 `?api-version=` 这类是合法的，一刀切会误伤。所以那一侧在这里判，
    判据两条：**参数名像密钥**，或者**值是一长串没有意义的 URL-safe 字符**。

    宁可漏判也不误判：误判会让一个正常端点加不进来，而用户没有别的出路；
    漏判只是少了一层提醒，key 本来也不会被存下来（库里还有 CHECK 兜着）。
    """
    parts = urlsplit(url)
    if "@" in parts.netloc:
        return True
    for name, value in parse_qsl(parts.query, keep_blank_values=True):
        if name.lower() in _SECRET_PARAMS:
            return True
        if _LONG_OPAQUE.match(value):
            return True
    return False


def _is_local(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host in _LOOPBACK or host.endswith(".local")


@dataclass(frozen=True)
class BrainPreset:
    name: str
    provider: str
    model: str
    base_url: str
    api_key_env: str
    note: str
    window: Window


def presets() -> list[BrainPreset]:
    """内置预设 + 用户配置文件（`booksoul.toml`）里定义的大脑。"""
    from booksoul.config import available_brains

    out: list[BrainPreset] = []
    for name, cfg in available_brains().items():
        model = cfg.get("model", "")
        out.append(
            BrainPreset(
                name=name,
                provider=cfg.get("provider", ""),
                model=model,
                base_url=cfg.get("base_url", ""),
                api_key_env=cfg.get("api_key_env", ""),
                note=cfg.get("note", ""),
                window=context_window(model),
            )
        )
    return out


def fit_note(
    *, fits: bool | None, context_chars: int, word_count: int, provider: str
) -> str:
    """一句话说清这本书和这个大脑的关系。

    只说算得出来的。界面稿上还有「会被内容审核拦」「当前 IP 没权限」两种说法，
    那是要真发一次请求才知道的事（S13 / S7），这里不预言。窗口是估计的
    （`fits is None`）也一样：不说装得下，也不说装不下。
    """
    if not word_count:
        return ""
    if fits is None:
        return "窗口未知（按估计）"
    if fits:
        if provider == "anthropic":
            # 缓存这件事不是修辞：整本书每轮重发，命中缓存与否差的是账单
            return "整本装得下，而且能缓存住不重复计费"
        return "整本装得下"
    share = round(context_chars / word_count * 100)
    return f"装不下，要挑章节 —— 额度约够这本书的 {share}%"


class BrainService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._brains = BrainRepository(session)
        self._choices = BrainChoiceRepository(session)
        self._books = BookRepository(session)

    async def list_for_user(
        self, *, user_id: int, book_id: int | None = None
    ) -> list[BrainOut]:
        word_count = 0
        if book_id is not None:
            book = await self._books.get_for_user(book_id, user_id)
            if book is None:
                raise NotFoundError("没有这本书。它可能已经被删掉了。")
            word_count = book.word_count

        chosen = await self._choices.for_user(user_id)
        rows = [_from_preset(p, chosen.get(p.name)) for p in presets()]
        rows += [_from_row(r) for r in await self._brains.list_for_user(user_id)]
        rows.sort(key=lambda b: (-b.context_chars, b.name))

        if word_count:
            for row in rows:
                # 一个汉字粗略按 1 token 上限估（见引擎里那张表的注释）。估得保守
                # 没关系，它只决定「要不要挑章节」，宁可多挑一次也不要溢出。
                row.fits = (
                    None
                    if row.context_source == "estimate"
                    else 0 < word_count <= row.context_chars
                )
                row.fit_note = fit_note(
                    fits=row.fits,
                    context_chars=row.context_chars,
                    word_count=word_count,
                    provider=row.provider,
                )
        return rows

    async def add_custom(self, *, user_id: int, payload: BrainCreate) -> BrainOut:
        name = payload.name.strip()
        base_url = payload.base_url.strip()
        env = payload.api_key_env.strip()

        if key_in_url(base_url):
            raise ApiError(
                "端点地址里带着 key，这个地址不能存。把地址里的密钥去掉，"
                "key 填在下面那个框里 —— 它只留在这台浏览器，不会存到服务器。"
            )
        if env and not _ENV_NAME.match(env):
            # **不把填进来的那串东西抄回响应里。** 它极可能就是一个真 key——
            # 这条分支存在的理由正是「有人往这一栏粘了 key」。原样回显，
            # 它就会经响应体落进前端的错误提示、控制台，以及任何错误上报管道。
            raise ApiError(
                "「需要哪个 key」那一栏填的是**环境变量的名字**（例如 "
                "DASHSCOPE_API_KEY），不是 key 本身。你填的那串不像名字 —— "
                "如果那就是 key，它该填在这一行右边的 key 框里，"
                "只留在这台浏览器。"
            )
        if any(p.name == name for p in presets()):
            raise ConflictError(f"「{name}」和一个内置预设重名，换一个名字。")
        if await self._brains.get_by_name(user_id, name) is not None:
            raise ConflictError(f"你已经有一个叫「{name}」的端点了，换个名字。")

        row = Brain(
            user_id=user_id,
            name=name,
            provider="openai",
            model=payload.model.strip(),
            base_url=base_url,
            api_key_env=env,
            context_tokens=payload.context_tokens,
            note=payload.note.strip(),
        )
        await self._brains.add(row)
        await self._session.commit()
        return _from_row(row)

    async def describe(self, *, user_id: int, name: str) -> BrainOut:
        """按名字取一个大脑，与⑧屏列表里那一行**同形同值**。找不到 → 404。

        开会话的快照、提问时组的配置、⑧屏那一行，三处都从这里取：各查各的，
        「设置页写着这个模型、这个额度，会话里锁下的却是另一个」的那天就到了。
        名字在同一用户下唯一——预设之间唯一，自定义端点被禁止与预设重名。

        内置预设 = 预设 + 这个人的选择：选择只覆盖模型与窗口。
        """
        preset = _preset_named(name)
        if preset is not None:
            chosen = await self._choices.for_user(user_id)
            return _from_preset(preset, chosen.get(name))
        row = await self._brains.get_by_name(user_id, name)
        if row is None:
            raise NotFoundError(f"没有叫「{name}」的大脑。到设置页看看还有哪些可选。")
        return _from_row(row)

    async def config_for(self, *, user_id: int, name: str, api_key: str):
        """按名字取出一个 `BrainConfig`，把用户这次带来的 key 装进去。

        **绝不从环境变量里兜底取 key。** 引擎的 `config.resolve()` 会那么做，
        对 CLI 是对的（那是你自己的机器、你自己的 key）；在服务端那意味着：
        用户没填 key，这次提问悄悄花的是运维的钱，而且没有任何人会发现。
        """
        brain = await self.describe(user_id=user_id, name=name)
        if brain.needs_key and not api_key:
            raise ApiError(
                f"「{name}」需要你自己的 key 才能用。到设置页把 key 填进去 —— "
                "它只存在这台浏览器，每次提问时经服务器转发给模型厂商，服务器不保存。"
            )
        return _config_of(brain, api_key)

    async def set_model(
        self,
        *,
        user_id: int,
        name: str,
        model: str,
        context_tokens: int = 0,
        context_source: str = "",
    ) -> BrainOut:
        """给一个大脑换模型并存下来。返回换过之后的那一行（与⑧屏同形）。

        入参已经在 schema 那一层校验过（`ModelChoiceIn`）。**只影响以后开的会话**：
        已开的会话把模型与正文额度快照在自己身上，不读这里。

        内置预设 → 写或覆盖选择表那一行；自定义端点 → **直接改它自己那一行**
        （Spec N5）：它的模型本来就是用户填的，没有「预设 + 选择」两层可分。
        `brains` 表没有窗口来路那一列，所以自定义端点的窗口一律显示为「你填的」——
        哪怕是从列表里点来的供应商窗口。
        """
        if _preset_named(name) is not None:
            await self._choices.put(
                user_id=user_id,
                brain_name=name,
                model=model,
                context_tokens=context_tokens,
                context_source=context_source,
            )
        else:
            row = await self._brains.get_by_name(user_id, name)
            if row is None:
                # 别人的端点与不存在同一个 404
                raise NotFoundError(
                    f"没有叫「{name}」的大脑。到设置页看看还有哪些可选。"
                )
            row.model = model
            row.context_tokens = context_tokens
        await self._session.commit()
        return await self.describe(user_id=user_id, name=name)

    async def reset_model(self, *, user_id: int, name: str) -> None:
        """恢复默认：删掉那一行选择，模型、窗口、来路一起回到预设。

        没选过也照常成功——本来就是默认，不算错。自定义端点没有默认模型（Spec N5）。
        """
        if _preset_named(name) is None:
            if await self._brains.get_by_name(user_id, name) is None:
                raise NotFoundError(
                    f"没有叫「{name}」的大脑。到设置页看看还有哪些可选。"
                )
            raise ApiError(
                "自定义端点没有默认模型——它的模型本来就是你填的。要换就直接选一个。"
            )
        await self._choices.remove(user_id=user_id, brain_name=name)
        await self._session.commit()

    async def list_models(self, *, user_id: int, name: str, api_key: str) -> ModelList:
        """用用户这次带来的 key 去问这家：你有哪些模型。

        三档排序（见 `_CHATTY` / `_NOT_CHAT`），在这里做好让测试钉着；搜索在前端。
        不缓存：列表跟着 key 走，换一个 key 可能就是另一批。
        """
        from booksoul import brain as brain_mod

        brain = await self.describe(user_id=user_id, name=name)
        # 在敲供应商的门之前拦：不带 key 去问，得到的只会是一个 401，
        # 而用户看到的会是「key 没通过验证」——可他根本还没填。
        # 这句和提问那条路的说法不同：人此刻就在⑧屏，出路是「这一行右边」。
        if brain.needs_key and not api_key:
            raise ApiError(
                f"先填 key：「{name}」要用你自己的 key 才列得出模型 —— "
                "填在这一行右边的框里，它只留在这台浏览器。"
            )
        cfg = _config_of(brain, api_key)
        try:
            # 引擎走的是阻塞的标准库 HTTP：放进工作线程，别让一次慢请求卡住事件循环
            found = await asyncio.to_thread(brain_mod.list_models, cfg)
        except brain_mod.BrainError as exc:
            if exc.kind == "dependency":
                # 服务器缺这一家的 SDK。怎么装是管理员的事，写进 ERROR 日志；
                # 用户做不了 pip install，给他一条做得到的路。没有 code：
                # 重试、换 key、手填模型名，一样都救不了。
                logger.error("列模型失败，服务器缺依赖：%s", exc)
                raise BrainUnavailable(
                    "这台服务器暂时接不了这一家（缺它的依赖），先换一个大脑。"
                    "这件事要管理员来处理。",
                    code="",
                ) from None
            code, message = _LIST_FAILURES.get(exc.kind, _LIST_FAILURES["network"])
            # from None：引擎的消息里有地址与状态码，对用户够用了；链上的东西不往外带
            raise BrainUnavailable(message, code=code) from None
        ranked = sorted(found, key=lambda m: _tier(m.id))  # sorted 是稳定的
        return ModelList(
            items=[ModelOut(id=m.id, context_tokens=m.context_tokens) for m in ranked],
            source=cfg.base_url.rstrip("/")
            or (_ANTHROPIC_OFFICIAL if cfg.provider == "anthropic" else ""),
        )

    async def remove(self, *, user_id: int, brain_id: int) -> None:
        """删掉自己加的端点。

        内置预设不在库里，所以「删一个内置预设」和「删别人的端点」都落到同一个
        404 —— 这两件事用户都不该被告知「它存在，只是你动不了」。
        """
        row = await self._brains.get_for_user(brain_id, user_id)
        if row is None:
            raise NotFoundError("没有这个自定义端点。内置预设不能删除。")
        await self._brains.delete(row)
        await self._session.commit()


def _config_of(brain: BrainOut, api_key: str):
    """⑧屏那一行 + 这次带来的 key → 引擎要的配置。provider / 地址永远取那一行的。"""
    from booksoul.config import BrainConfig

    return BrainConfig(
        name=brain.name,
        provider=brain.provider,
        model=brain.model,
        base_url=brain.base_url,
        api_key_env=brain.api_key_env,
        api_key=api_key,
    )


def _tier(model_id: str) -> int:
    """0 = 带对话关键词，1 = 看不出来，2 = 明显不是对话。

    「不是对话」先判：wanx2.1-t2i-turbo 是画图的，不该因为一个 turbo 排到最前。
    """
    low = model_id.lower()
    if any(word in low for word in _NOT_CHAT):
        return 2
    if any(word in low for word in _CHATTY):
        return 0
    return 1


def _needs_key(row: Brain) -> bool:
    """自己加的端点要不要 key。

    除非它跑在本机，否则一律当作要。判错的两个方向代价差得很远：多出一个填了
    也没用的输入框只是噪音；少了那个输入框，用户就走进了一条没有出路的死胡同。
    """
    return bool(row.api_key_env) or not _is_local(row.base_url)


def _preset_named(name: str) -> BrainPreset | None:
    return next((p for p in presets() if p.name == name), None)


def _from_preset(preset: BrainPreset, choice: BrainChoice | None = None) -> BrainOut:
    """预设 + 这个人的选择 → ⑧屏那一行。

    **选择只覆盖模型与窗口**：provider、地址、key 名永远取预设的——选择表里
    根本没有放它们的列。没选过就是预设原样。
    """
    if choice is None:
        model, window, tokens = preset.model, preset.window, 0
    else:
        model, tokens = choice.model, choice.context_tokens
        window = context_window(
            choice.model,
            tokens=choice.context_tokens,
            source=choice.context_source,
        )
    return BrainOut(
        id=None,
        name=preset.name,
        provider=preset.provider,
        model=model,
        default_model=preset.model,
        model_chosen=choice is not None,
        base_url=preset.base_url,
        api_key_env=preset.api_key_env,
        note=preset.note,
        context_chars=window.chars,
        context_tokens=tokens,
        context_source=window.source,
        needs_key=bool(preset.api_key_env),
        custom=False,
    )


def _from_row(row: Brain) -> BrainOut:
    # 自己加的端点：窗口只可能是用户填的（`context_window` 把没有来路的窗口
    # 当作「你填的」）；没填就和内置预设走同一条内置表 / 估计的路。
    window = context_window(row.model, tokens=row.context_tokens)
    return BrainOut(
        id=row.id,
        name=row.name,
        provider=row.provider,
        model=row.model,
        # 自定义端点没有「默认模型」：它的模型就是用户自己填的那一个
        default_model="",
        model_chosen=False,
        base_url=row.base_url,
        api_key_env=row.api_key_env,
        note=row.note,
        context_chars=window.chars,
        context_tokens=row.context_tokens,
        context_source=window.source,
        needs_key=_needs_key(row),
        custom=True,
    )
