"""这个部署支持哪些格式，是探测出来的不是写死的。

一份没装 pymupdf 的部署如果照样在界面上挂着 PDF，用户传上去只会换来一句看不懂的
报错——那是把安装这件事推给了不负责安装的人。
"""

from __future__ import annotations

import logging

from booksoul_api.services import format_service


def _reprobe(monkeypatch, missing: set[str]):
    """把指定模块伪装成没装，重新探测一次。"""
    monkeypatch.setattr(
        format_service,
        "_installed",
        lambda module: module not in missing,
    )
    format_service.capability.cache_clear()
    caps = format_service.capability()
    format_service.capability.cache_clear()  # 别把伪装结果留给后面的测试
    return caps


def test_装了后端的格式才出现在可用列表里(monkeypatch):
    caps = _reprobe(monkeypatch, {"pymupdf", "fitz", "pypdf", "docx", "mobi"})
    by_key = {f.key: f for f in caps.formats}

    assert by_key["pdf"].available is False
    assert by_key["docx"].available is False
    assert by_key["mobi"].available is False
    # 只靠标准库与必需依赖的那几个永远在
    assert by_key["epub"].available is True
    assert by_key["txt"].available is True

    assert ".pdf" not in caps.accepted_extensions
    assert ".epub" in caps.accepted_extensions


def test_不可用的格式仍然列出来而不是整个藏起来(monkeypatch):
    """藏起来的话，用户拖一个 PDF 进去只会觉得系统坏了。

    列出来但标成不可用，他才知道是这份部署的事，不是他的文件有问题。
    """
    caps = _reprobe(monkeypatch, {"pymupdf", "fitz", "pypdf"})

    assert any(f.key == "pdf" and not f.available for f in caps.formats)


def test_给用户的话里不许出现安装命令(monkeypatch):
    """Spec N1 的分层：补救建议要放在**能执行它的人**面前。

    用户做不了 pip install，给他看安装命令是一种敷衍；管理员需要这条命令，
    所以它去 ERROR 日志。
    """
    monkeypatch.setattr(
        format_service,
        "_installed",
        lambda module: module not in {"pymupdf", "fitz", "pypdf"},
    )
    format_service.capability.cache_clear()
    try:
        reason = format_service.rejection_reason("扫描版.pdf")
    finally:
        format_service.capability.cache_clear()

    assert "这个部署暂不支持 PDF" in reason
    assert "pip install" not in reason
    assert "pymupdf" not in reason


def test_缺依赖要给管理员留一条ERROR日志(monkeypatch, caplog):
    """用户看不到的那一半信息不能凭空消失——管理员得知道少装了什么。"""
    with caplog.at_level(logging.ERROR, logger=format_service.__name__):
        _reprobe(monkeypatch, {"pymupdf", "fitz", "pypdf"})

    messages = [record.getMessage() for record in caplog.records]
    assert any("PDF" in m and "pip install pymupdf" in m for m in messages)


def test_azw3标为实验性(monkeypatch):
    """Spec N3：mobi 包解析 azw3 会落临时文件，且不支持新版 KFX。

    它能用，但和别的格式不是一个可靠程度，界面上要说清楚。
    """
    caps = _reprobe(monkeypatch, set())
    azw3 = next(f for f in caps.formats if f.key == "azw3")

    assert azw3.experimental is True
    assert azw3.note
    assert all(f.experimental is False for f in caps.formats if f.key != "azw3")


def test_认不出的扩展名给出目前收哪些(monkeypatch):
    caps = _reprobe(monkeypatch, set())
    reason = format_service.rejection_reason("笔记.rtf")

    assert "认不出" in reason
    for label in ("EPUB", "TXT"):
        assert label in reason
    assert caps.max_upload_mb == 100
