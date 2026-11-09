"""解析过程中的分节进度回报。

大部头解析要几十秒。没有分节刻度，用户看到的就是一个不动的转圈——分不清
「在干活」和「卡死了」，而这两件事的下一步动作完全不同。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from booksoul import parsers

_SAMPLE = Path(__file__).resolve().parent.parent / "悉达多.epub"

needs_sample = pytest.mark.skipif(
    not _SAMPLE.is_file(), reason="样书不在，跳过需要真书的用例"
)


@needs_sample
def test_解析epub时逐节回报进度():
    """回报的是刻度不是估计：done 单调不减、total 全程不变、结束时两者相等。"""
    seen: list[tuple[int, int]] = []

    book, _ = parsers.parse(
        str(_SAMPLE), on_progress=lambda done, total: seen.append((done, total))
    )

    assert seen, "一次都没回报进度"
    assert len({total for _, total in seen}) == 1, "total 中途变了，进度条会往回跳"
    assert seen[-1][0] == seen[-1][1], "结束时 done 应该追平 total"
    assert [done for done, _ in seen] == sorted(done for done, _ in seen)
    assert seen[-1][1] > 1, "整本书只算一节的话，进度条没有意义"
    assert len(book.chapters) == 16


@needs_sample
def test_不给回调时解析行为完全不变():
    """回调是加出来的，CLI 那条路一个字都不该受影响。"""
    with_cb, _ = parsers.parse(str(_SAMPLE), on_progress=lambda done, total: None)
    without, _ = parsers.parse(str(_SAMPLE))

    assert [c.title for c in with_cb.chapters] == [c.title for c in without.chapters]
    assert with_cb.words == without.words
    assert with_cb.warnings == without.warnings
