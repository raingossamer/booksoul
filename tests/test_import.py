"""把一个上传上来的文件解析进书库这一步。

这一步刻意不碰数据库也不碰 HTTP：它就是「文件进、目录出」，因此能用真书跑真解析，
不必搭一套替身。DB 编排（建行、回填 storage_path、落章节索引）在
`services/book_lifecycle`，那是另一回事。
"""

from __future__ import annotations

import gc
import shutil
from pathlib import Path

import pytest

from booksoul_api.services.importer import import_file

_SAMPLE = Path(__file__).resolve().parent.parent / "悉达多.epub"

needs_sample = pytest.mark.skipif(
    not _SAMPLE.is_file(), reason="样书不在，跳过需要真书的用例"
)


@needs_sample
def test_解析完就放开原件_不等垃圾回收(tmp_path):
    """EPUB 解析器握着原件的 zip 句柄——写盘那步导图片要用它。写完不放手，就要等
    垃圾回收才放；Windows 上那份原件在此之前删不掉、也换不掉。

    关掉垃圾回收，才看得出是自己放的手还是被回收的。
    """
    src = tmp_path / "悉达多.epub"
    shutil.copy(_SAMPLE, src)

    gc.disable()
    try:
        import_file(src, out_root=tmp_path / "lib", dir_name="7/1-悉达多")
        src.unlink()
    finally:
        gc.enable()
    assert not src.exists()


@needs_sample
def test_解析时按五个阶段回报进度(tmp_path):
    """①屏那条五阶段进度条要的是真事件，不是定时器动画。

    转换正文这一段还要带上分节刻度，否则大部头只剩一个不动的转圈——
    而「在干活」和「卡死了」的下一步动作完全不同。
    """
    seen: list[tuple[str, int, int]] = []

    import_file(
        _SAMPLE,
        out_root=tmp_path / "lib",
        dir_name="7/1-悉达多",
        on_stage=lambda stage, done, total: seen.append((stage, done, total)),
    )

    stages = [stage for stage, _, _ in seen]
    # 五个阶段都要出现，且按固定顺序推进，不许回头
    assert set(stages) == {"read", "toc", "convert", "clean", "write"}
    order = ["read", "toc", "convert", "clean", "write"]
    assert [order.index(s) for s in stages] == sorted(order.index(s) for s in stages)

    converting = [(d, t) for s, d, t in seen if s == "convert"]
    assert len(converting) > 1, "转换正文只报了一次，等于没有分节刻度"
    assert converting[-1][0] == converting[-1][1]

    assert seen[-1][0] == "write"
    assert seen[-1][1] == seen[-1][2] == 16
