"""问答编排：把书、人格、大脑接到一起。

一次提问要决定三件事：
  1. 这次送多少书进去（整本还是挑章节）；
  2. 用哪个人格；
  3. 用哪个大脑。

会话状态（多轮）只存消息历史，书和人格每轮重发——这听起来浪费，但对 Anthropic
有 prompt 缓存，对 OpenAI 兼容端多数也有自动前缀缓存，实际成本远低于直觉。
换来的是每一轮模型都完整地"手边有书"，不会聊到第五轮就开始瞎编。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from . import brain as brain_mod
from . import library, retrieve
from . import persona as persona_mod
from .config import BrainConfig
from .model import Book

# 各模型的上下文窗口（token）与由它折算出的正文额度（字）。
#
# 一个汉字大约 0.6-1 个 token，按 1 字 = 1 token 的上限估；再乘 _RESERVE，
# 给框架语、人格、多轮历史和回答本身留出地方。估得保守只会多挑一次章节，
# 估得宽松会在真实请求上炸——所以宁可保守。
#
# 这张表是**选章判据的唯一来源**。设置页说「装得下」而提问时却在挑章节，
# 那一定是有人在别处又抄了一份。
_RESERVE = 0.85

# 键是模型名的子串。窗口数字取各家公布值里偏保守的那一档——它们会随供应商
# 更新而变，写在这里只是开箱可用的默认值，用 --budget 随时可以盖掉。
_CONTEXT_BUDGETS = {
    "claude-opus-5": 900_000,  # 1M 窗口
    "claude-sonnet-5": 900_000,  # 1M 窗口
    "qwen-plus": 110_000,  # 131K
    "moonshot-v1-128k": 108_000,  # 128K（窗口就写在模型名里）
    "glm-4": 108_000,  # 128K
    "gpt-4o": 108_000,  # 128K
    "deepseek-chat": 54_000,  # 64K
    "DeepSeek-V3": 54_000,  # 64K
    "qwen2.5": 27_000,  # 32K，本地跑的那一档
    # 认不出的模型：按 128K 窗口估，但比 _RESERVE 更保守——
    # 不知道对面是什么，就别顶格用。
    "default": 100_000,
}


def budget_from_context_tokens(context_tokens: int) -> int:
    """厂商公布的上下文窗口（token）→ 这次最多能塞多少字正文。

    自己加的 OpenAI 兼容端点走这条路：用户填的是他在对方文档上看到的窗口，
    折算成正文额度的规则必须和上面那张表用的是同一条，否则两种大脑的
    「装不装得下」就不是一把尺子量出来的了。
    """
    return max(0, int(context_tokens * _RESERVE))


def context_budget_chars(model: str, override: int = 0) -> int:
    """这个模型的上下文，按字数算，大约能装多少正文。

    公开的（不带下划线）：Web 层要回答「这本书装得进哪个大脑吗」，
    而那个答案必须和选章时用的判据来自同一张表 —— 复制一份必然会漂。

    刻意不叫 `budget_chars`：`Session` 有个同名字段、`open_session` 有个同名参数，
    重名会在那两处把这个函数遮掉，谁将来在里面调它就是一个 TypeError。
    """
    if override:
        return override
    known = known_context_chars(model)
    return known if known is not None else _CONTEXT_BUDGETS["default"]


def known_context_chars(model: str) -> int | None:
    """这个模型名内置表认不认得：认得给额度，认不得给 `None`。

    `context_budget_chars` 只回一个数，分不清「表里查到的」和「落到 default 的」；
    Web 层要把这两者在界面上分开说——一个是有依据的数，一个是猜的。
    它建在这个函数之上，表仍然只有一张。
    """
    for key, value in _CONTEXT_BUDGETS.items():
        if key != "default" and key in model:
            return value
    return None


@dataclass
class Session:
    """一次读书对话。"""

    book: Book
    persona: persona_mod.Persona
    cfg: BrainConfig
    budget_chars: int = 0
    reflection: bool = False
    history: list[dict] = field(default_factory=list)
    # 选章已经在外面做完了：收 1 起的章节序号（`Chapter.order + 1`，与
    # `load_from_path(only_idx=...)` 同源）。给了它就不在这里再挑一次。
    #
    # 这个口子是给 Web 层开的，那边要先把选章结果落成一份可查的账再调模型。
    # 它同时堵住一个很隐蔽的坑：按 only_idx 载入的书，没选中的章节留着空壳，
    # 本地再挑一次会把空壳也算进「装得下」，于是 `as_context` 拿到一份
    # 「全都载入了」的名单——未载入的那些既丢了「本次未载入全文」的标注，
    # 又渲成有标题没正文的空段。模型会以为正文丢了，或者干脆照着目录名编。
    preselected_idx: set[int] | None = None
    _brain: object = None
    _last_selection: tuple[int, int] = (0, 0)

    def __post_init__(self) -> None:
        self._brain = brain_mod.create(self.cfg)
        if not self.budget_chars:
            self.budget_chars = context_budget_chars(self.cfg.model)

    @property
    def brain_label(self) -> str:
        return self.cfg.describe()

    def _build_system(self, question: str) -> list[tuple[str, bool]]:
        if self.preselected_idx is None:
            chapters, _whole = retrieve.select_chapters(
                self.book.chapters,
                question,
                budget_chars=self.budget_chars,
                always_include=1,
            )
        else:
            # 只认「选中了**并且**读到了正文」的那些。按 only_idx 载入时没选中的
            # 章节是空壳，混进来就是给模型一段有标题没正文的书。
            chapters = [
                c
                for c in self.book.chapters
                if c.order + 1 in self.preselected_idx and c.content
            ]
        self._last_selection = (len(chapters), len(self.book.chapters))
        context = library.as_context(self.book, chapters)
        prompt = self.persona.render(
            self.book.meta, words=self.book.words, chapters=len(self.book.chapters)
        )
        if self.reflection:
            prompt += persona_mod.REFLECTION_SUFFIX
        return brain_mod.build_system(context, prompt)

    def ask(
        self, question: str, *, on_text: Callable[[str], None] | None = None
    ) -> brain_mod.Reply:
        """问一轮。on_text 给了就流式回调。"""
        question = question.strip()
        if not question:
            raise ValueError("问题是空的")

        # 多轮时用第一轮的选章结果，避免每轮换书导致缓存全废
        system = self._build_system(
            question if not self.history else self._first_question()
        )
        messages = [*self.history, {"role": "user", "content": question}]

        reply = self._brain.chat(system, messages, on_text=on_text)

        self.history.append({"role": "user", "content": question})
        self.history.append({"role": "assistant", "content": reply.text})
        return reply

    def _first_question(self) -> str:
        for m in self.history:
            if m["role"] == "user":
                return m["content"]
        return ""

    def selection_note(self) -> str:
        picked, total = self._last_selection
        if picked >= total:
            return f"已载入全书 {total} 节"
        return f"已载入 {picked}/{total} 节（按问题相关度挑选）"

    def reset(self) -> None:
        self.history.clear()


def open_session(
    book_query: str = "",
    *,
    persona: str = persona_mod.DEFAULT_PERSONA,
    cfg: BrainConfig,
    root: str = "",
    reflection: bool = False,
    budget_chars: int = 0,
) -> Session:
    return Session(
        book=library.load(book_query, root),
        persona=persona_mod.load(persona),
        cfg=cfg,
        reflection=reflection,
        budget_chars=budget_chars,
    )
