"""StreamRecorder：一次流式回答，无论怎么结束都把说出口的交出去。

这个模块存在的理由是一句很具体的话：**用户花的钱不能白花**。回答生成到一半
断了——网络断、服务重启、模型那头报错——已经吐出来的那些字是已经计过费的，
把它们丢掉等于让用户为一段他从没看到的文字付钱。

它只管内存里的事：累积、广播、回放，退出时把这一轮的结局（`Outcome`）交给
`persist`。**怎么落库不归它**——那在 `services/turn_state`，库的形状在
`test_turn_state` 里测。所以这里一律用一个把 `Outcome` 记下来的假 `persist`，
不连库。

它的契约：

1. 正常结束 → 交出一路完整的回答，带着账单
2. 异常结束 → 交出已经累积的每一个字，标着错
3. 一个字都没有 → 交出的结局里没有说出口的一路
4. **先交、后宣布**：`done` 在 `persist` 返回之后才发；`persist` 炸了宣布的是 `error`

外加一条为「重连不重复计费」服务的：同一条流可以被多个订阅者听，
后来的订阅者先拿到已经累积的部分，再跟上后续——**不会再调一次模型**。
（「这一轮正在跑、该挂上去」由 `turn_state` 判断，在 `test_turn_state` 里测。）
"""

from __future__ import annotations

import asyncio

import pytest

from booksoul.brain import Reply, Usage
from booksoul_api.services.stream_recorder import Outcome, StreamRecorder


class _Kept:
    """假 `persist`：把交过来的结局记下来。可以让它炸。"""

    def __init__(self, *, fails: bool = False) -> None:
        self.outcomes: list[Outcome] = []
        self._fails = fails

    async def __call__(self, outcome: Outcome) -> None:
        await asyncio.sleep(0)  # 让出一次：真落库要等库，别让「顺序对」只是碰巧
        if self._fails:
            raise RuntimeError("库写不进去")
        self.outcomes.append(outcome)

    @property
    def only(self) -> Outcome:
        [outcome] = self.outcomes
        return outcome


def _open(kept: _Kept, personas: tuple[str, ...] = ("scholar",)) -> StreamRecorder:
    return StreamRecorder.open(
        conversation_id=1, turn=1, personas=list(personas), persist=kept
    )


async def test_正常结束交出一路完整的回答():
    kept = _Kept()

    async with _open(kept) as recorder:
        recorder.sink("scholar", "他拒绝的不是佛陀，")
        recorder.sink("scholar", "而是把佛陀当作外在权威这条路径。")
        recorder.finish(
            "scholar",
            Reply(
                stop_reason="end_turn",
                usage=Usage(input_tokens=13_204, output_tokens=286, cache_read=12_904),
            ),
        )

    [said] = kept.only.spoken
    assert said.persona == "scholar"
    assert said.error == ""
    assert said.text == "他拒绝的不是佛陀，而是把佛陀当作外在权威这条路径。"
    # 账单三件套要交出去，否则⑦屏底下那条仪表就只能编
    assert said.reply.usage.cache_read == 12_904
    assert said.reply.stop_reason == "end_turn"


async def test_中途出事也保住已经吐出来的字():
    """这是这个模块存在的全部理由。

    已经流出来的字是已经计过费的。丢掉它们，用户就为一段自己从没看到的文字
    付了钱——而且他连「刚才生成到哪了」都不知道。
    """
    kept = _Kept()

    with pytest.raises(RuntimeError):
        async with _open(kept) as recorder:
            recorder.sink("scholar", "他拒绝的不是佛陀，")
            recorder.sink("scholar", "而是把佛陀当作")
            raise RuntimeError("连接被对面掐断了")

    [said] = kept.only.spoken
    assert said.text == "他拒绝的不是佛陀，而是把佛陀当作"
    assert "掐断" in said.error


async def test_一个字都没有时交出去的结局里没有说出口的一路():
    """空消息比没有消息更糟：界面上它看起来像「模型答了个空」，

    而真实情况是「还没开口就断了」。结局照样交出去——收尾那边还有别的事要做——
    只是里面没有一路说出过话。
    """
    kept = _Kept()

    with pytest.raises(RuntimeError):
        async with _open(kept):
            raise RuntimeError("还没开口就断了")

    assert kept.only.spoken == []


async def test_选章决策与模型手里那几节跟着结局一起交出去():
    """两样都要和回答写进同一个事务；`sources` 的 None 与 [] 是两句不同的话。"""
    kept = _Kept()
    sentinel_choice = object()

    async with _open(kept) as recorder:
        recorder.record(sentinel_choice)  # type: ignore[arg-type]
        recorder.trace([])
        recorder.sink("scholar", "说完了。")
        recorder.finish("scholar", Reply(stop_reason="end_turn"))

    assert kept.only.choice is sentinel_choice
    assert kept.only.sources == [], "书在手上、一节都没载入，不是「没拿到书」"
    assert (kept.only.conversation_id, kept.only.turn) == (1, 1)


async def test_没拿到书时sources是None不是空列表():
    kept = _Kept()

    async with _open(kept) as recorder:
        recorder.sink("scholar", "说完了。")
        recorder.finish("scholar", Reply(stop_reason="end_turn"))

    assert kept.only.sources is None


async def test_后来的订阅者先拿到已经说过的再跟上后面():
    """「重连回放已有内容，绝不重新调用模型」这条验收的机制在这里。

    断线重连时前端重新订阅同一条流。它必须先补上错过的那一段，再跟着往下——
    而不是让服务端把同一个问题再问模型一遍。同一个问题付两次钱是不能接受的。
    """
    kept = _Kept()

    async with _open(kept) as recorder:
        recorder.sink("scholar", "前半段。")

        heard: list[str] = []

        async def late_listener():
            async for name, payload in recorder.listen():
                if name == "token":
                    heard.append(payload["text"])

        listener = asyncio.create_task(late_listener())
        await asyncio.sleep(0)  # 让订阅真正挂上去

        recorder.sink("scholar", "后半段。")
        recorder.finish("scholar", Reply(stop_reason="end_turn"))
        recorder.close()
        await listener

    # 补上的那一段 + 挂上之后的那一段，合起来是完整的一句，且不重不漏
    assert "".join(heard) == "前半段。后半段。"


async def test_工作线程里写进来的字一个都不会漏():
    """模型调用跑在工作线程里，`sink()` 是从那边被调的。

    跨线程交接最容易丢的是**最后几个 chunk**：事件循环还没来得及收，
    上下文就退出了。交出去之前必须再收一次尾。
    """
    kept = _Kept()

    async with _open(kept) as recorder:

        def produce():
            for chunk in "一二三四五六七八九十":
                recorder.sink("scholar", chunk)

        await asyncio.to_thread(produce)
        recorder.finish("scholar", Reply(stop_reason="end_turn"))

    assert kept.only.spoken[0].text == "一二三四五六七八九十"


async def test_done在persist返回之后才发():
    """先宣布结束再落库的话，客户端收到 done 转头去重新拉一次会话，

    看到的是一个没有回答的问题——一条它刚刚亲眼看着流完的回答。
    """
    kept = _Kept()
    kept_at_done: list[int] = []

    async def listener(recorder):
        async for name, _ in recorder.listen():
            if name == "done":
                kept_at_done.append(len(kept.outcomes))

    async with _open(kept) as recorder:
        watcher = asyncio.create_task(listener(recorder))
        await asyncio.sleep(0)
        recorder.sink("scholar", "说完了。")
        recorder.finish("scholar", Reply(stop_reason="end_turn"))

    await asyncio.wait_for(watcher, timeout=5)
    assert kept_at_done == [1]


async def test_persist炸了宣布的是error而不是done():
    """宣布 done 而库里什么都没有，客户端会立刻回头拉一次，看到一个不存在的空洞。"""
    kept = _Kept(fails=True)
    heard: list[str] = []

    async def listener(recorder):
        async for name, payload in recorder.listen():
            heard.append(name)
            if name == "error":
                assert "没能存下来" in payload["message"]

    async with _open(kept) as recorder:
        watcher = asyncio.create_task(listener(recorder))
        await asyncio.sleep(0)
        recorder.sink("scholar", "说完了。")
        recorder.finish("scholar", Reply(stop_reason="end_turn"))

    await asyncio.wait_for(watcher, timeout=5)
    assert heard[-1] == "error"
    assert "done" not in heard


async def test_告示不会被后来的stage盖掉_晚到的订阅者先拿到它():
    """`stage` 是「现在在做什么」，只有一个位置；告示是「这一轮有什么不一样」，

    一句都不能丢。原先告示走 `stage`，紧随其后的「正在挑章节…」当场就把它盖了。
    """
    kept = _Kept()
    heard: list[tuple[str, dict]] = []

    async with _open(kept) as recorder:
        recorder.notice("reparsed", "这本书重新解析过了")
        recorder.stage("正在挑章节…")

        async def late_listener():
            async for event in recorder.listen():
                heard.append(event)

        listener = asyncio.create_task(late_listener())
        await asyncio.sleep(0)
        recorder.sink("scholar", "说完了。")
        recorder.finish("scholar", Reply(stop_reason="end_turn"))
        recorder.close()
        await listener

    assert heard[0] == ("notice", {"code": "reparsed", "text": "这本书重新解析过了"})
    assert ("stage", {"text": "正在挑章节…"}) in heard
