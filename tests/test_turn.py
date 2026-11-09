"""`Turn` —— 一轮提问的那几个只读的值。

这个文件测的是**值对象自己的规矩**，不连库、不跑模型。
一轮提问的端到端行为在 `test_chat` / `test_retrieval` / `test_compare` /
`test_turn_state` 那边。
"""

from __future__ import annotations

import dataclasses

import pytest

from booksoul.config import BrainConfig
from booksoul_api.services import chapter_selector as selector
from booksoul_api.services.turn import (
    BookRef,
    BrainRef,
    Budget,
    Draft,
    Standing,
    Turn,
)


def _draft(*, word_count: int = 48155, chars: int = 60000) -> Draft:
    return Draft(
        conversation_id=1,
        personas=["scholar"],
        question="他为什么沉沦？",
        answering="scholar",
        book=BookRef(
            id=7,
            title="悉达多",
            storage_path="/tmp/library/7/悉达多",
            word_count=word_count,
            chapters=[selector.ChapterIn(id=1, idx=1, title="序", word_count=10)],
        ),
        budget=Budget(chars=chars, reselect=False),
        brain=BrainRef(name="claude", cfg=BrainConfig(name="claude")),
        reflection=False,
    )


def _turn() -> Turn:
    return Turn.of(
        _draft(), Standing(turn=1, history=[], locked=[], reuse=None, notices=())
    )


@pytest.mark.parametrize(
    "拿到手, 字段",
    [
        (lambda t: t, "question"),
        (lambda t: t.book, "storage_path"),
        (lambda t: t.budget, "chars"),
        (lambda t: t.brain, "name"),
        (lambda t: t.standing, "locked"),
    ],
)
def test_一轮提问和它的值对象都改不动(拿到手, 字段):
    """它跨线程、跨任务传，而且三路并发共用同一份。

    谁在半路改一个字段，症状会出现在**另一个地方的另一个时刻**：
    对读的第二路读到第一路改过的值，而两边都没有报错。
    """
    turn = _turn()
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(拿到手(turn), 字段, "换一个")


def test_合成一轮时请求期的输入原样带过来():
    """`Turn.of` 逐个字段搬：`Draft` 加一个字段而这里漏了，会当场炸，不会静默少一样。"""
    draft = _draft()
    turn = Turn.of(
        draft, Standing(turn=3, history=[], locked=[1], reuse=None, notices=())
    )

    for field in dataclasses.fields(Draft):
        assert getattr(turn, field.name) == getattr(draft, field.name), field.name
    assert turn.standing.turn == 3


def test_装不装得下同时要问书和预算():
    """所以它在 `Draft` 上，不在 `Budget` 上。

    放进 `Budget` 就得让预算自己拎一份书的字数，「按来源分组」那条依据当场就破。
    """
    assert _draft(word_count=100, chars=60000).fits_whole
    assert not _draft(word_count=900_000, chars=60000).fits_whole
