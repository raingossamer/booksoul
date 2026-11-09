"""测试替身。不是测试模块，是给测试用的零件。

**模型调用一律用替身，不打真实 API** —— 这条写在 S7 的验收里。替身不只是省钱：
「断流时已生成的部分要保住」「同一个问题不许问第二遍」这类行为，真模型根本
没法按需触发，只能靠一个听话的假模型把那一刻造出来。
"""

from __future__ import annotations

import copy
import threading

from booksoul.brain import BrainError, Reply, Usage
from booksoul.model import Book, BookMeta, Chapter

# 纯 ASCII。真 key 也是 ASCII —— 带中文的假 key 会在 httpx 组请求头时
# 撞上 UnicodeEncodeError，那个错和被测的逻辑毫无关系，只会浪费一次排查。
FAKE_KEY = "sk-test-NOT-A-REAL-KEY-AAAABBBBCCCC"


class FakeBrain:
    """替身大脑。按 `script` 吐字，可以在中途抛错。

    它同时是一份**记录仪**：记下被调了几次、每次收到的 system 与 messages，
    以及造它的那份配置。「同一个问题不许问两遍」「模型手里到底有哪几节」
    「真正调的是哪个模型」这几条只能靠它证明。
    """

    calls: list[dict] = []

    def __init__(self, cfg) -> None:
        self.cfg = cfg

    script: list[str] = ["他拒绝的不是佛陀，", "而是把佛陀当作外在权威这条路径。"]
    raise_after: int | None = None
    delay: float = 0.0

    def chat(self, system, messages, *, on_text=None):
        type(self).calls.append(
            {"system": system, "messages": messages, "cfg": self.cfg}
        )
        for i, chunk in enumerate(self.script):
            if self.raise_after is not None and i == self.raise_after:
                raise BrainError("对面把连接掐了")
            if on_text:
                on_text(chunk)
        return Reply(
            text="".join(self.script),
            model=self.cfg.model,
            stop_reason="end_turn",
            usage=Usage(input_tokens=13_204, output_tokens=286, cache_read=12_904),
        )


def engine_book(title: str, chapters: dict[str, str], **meta: str) -> Book:
    """照字典造一本引擎的书：章节标题 → 正文（Markdown，不含它自己的标题行）。"""
    return Book(
        meta=BookMeta(title=title, **meta),
        chapters=[Chapter(title=name, content=body) for name, body in chapters.items()],
    )


class FakeParser:
    """引擎解析的替身（换在 `booksoul.parsers`，见 conftest 的 `fake_parser`）。

    `book` 是下一次要交出去的书，每次交一份深拷贝：清洗会原地改它，重新解析时不该拿到
    上一次清洗过的那本。`fail` 给了就抛它——替身在引擎这一层，抛什么由测试定。
    `hold()` 让下一次解析停在「转换正文」中途，直到测试放行；停下之前先按 `progress`
    报一次进度。解析跑在工作线程里，所以闸门是 `threading.Event`。
    """

    def __init__(self) -> None:
        self.book = engine_book(
            "悉达多", {"婆罗门之子": "悉达多是婆罗门之子，他在父亲的屋影里长大。"}
        )
        self.fmt = "epub"
        self.fail: BaseException | None = None
        self.progress = (1, 1)
        self.calls = 0
        self.reached = threading.Event()  # 已经停在闸门前了
        self._gate: threading.Event | None = None

    def hold(self) -> threading.Event:
        """下一次解析停在转换正文中途；返回的 Event 一 set 就放行。"""
        self.reached.clear()
        self._gate = threading.Event()
        return self._gate

    def detect_format(self, path: str) -> str:
        return self.fmt

    def parse(self, path: str, *, keep_images: bool = True, on_progress=None):
        self.calls += 1
        if on_progress is not None:
            on_progress(*self.progress)
        gate, self._gate = self._gate, None
        if gate is not None:
            self.reached.set()
            if not gate.wait(timeout=10):
                raise AssertionError("测试一直没有放行这次解析")
        if self.fail is not None:
            raise self.fail
        return copy.deepcopy(self.book), None
