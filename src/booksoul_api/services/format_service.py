"""这个部署到底支持哪些格式 —— **运行时探测，不是一张写死的表**。

解析器的依赖是可选的（pymupdf / python-docx / mobi）。一份没装 pymupdf 的部署
如果照样在界面上挂着 PDF，用户传上去只会换来一句看不懂的报错——那是把安装
这件事推给了不负责安装的人。

两类信息要分开给（Spec N1 / Grill D6）：

- **给管理员的**：缺哪个包、装什么命令。写进 ERROR 日志，不进响应体。
- **给用户的**：「这个部署暂不支持 PDF」。用户做不了 `pip install`，
  给他看安装命令是一种敷衍。
"""

from __future__ import annotations

import importlib.util
import logging
from dataclasses import dataclass, field
from functools import lru_cache

from booksoul_api.core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FormatSpec:
    key: str
    label: str
    extensions: tuple[str, ...]
    # 任意一个装上就算可用。空元组表示只靠标准库与必需依赖，永远可用。
    backends: tuple[str, ...] = ()
    experimental: bool = False
    note: str = ""
    admin_hint: str = ""


# 顺序即界面上格式标签的顺序（界面稿⓪屏／①屏）。
_CATALOG: tuple[FormatSpec, ...] = (
    FormatSpec("epub", "EPUB", (".epub",)),
    FormatSpec(
        "mobi",
        "MOBI",
        (".mobi", ".prc", ".azw"),
        backends=("mobi",),
        admin_hint="pip install mobi",
    ),
    FormatSpec(
        "azw3",
        "AZW3",
        (".azw3", ".kfx"),
        backends=("mobi",),
        experimental=True,
        # Spec N3：mobi 包解析 azw3 会落临时文件，且不支持新版 KFX
        note="实验性：新版 Kindle（KFX）多半解不开，失败了换 EPUB 试试",
        admin_hint="pip install mobi",
    ),
    FormatSpec(
        "pdf",
        "PDF",
        (".pdf",),
        backends=("pymupdf", "fitz", "pypdf"),
        admin_hint="pip install pymupdf（推荐）或 pip install pypdf",
    ),
    FormatSpec(
        "docx",
        "DOCX",
        (".docx", ".doc"),
        backends=("docx",),
        admin_hint="pip install python-docx",
    ),
    FormatSpec("txt", "TXT", (".txt", ".text")),
    FormatSpec("html", "HTML", (".html", ".htm", ".xhtml")),
    FormatSpec("markdown", "MD", (".md", ".markdown")),
)


@dataclass
class FormatStatus:
    key: str
    label: str
    extensions: list[str]
    available: bool
    experimental: bool
    note: str


@dataclass
class Capability:
    max_upload_mb: int
    formats: list[FormatStatus] = field(default_factory=list)

    @property
    def accepted_extensions(self) -> list[str]:
        out: list[str] = []
        for fmt in self.formats:
            if fmt.available:
                out.extend(fmt.extensions)
        return out


def _installed(module: str) -> bool:
    """只看装没装，不 import —— import pymupdf 要几百毫秒，探测不值这个钱。"""
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


@lru_cache
def capability() -> Capability:
    """探测一次，进程内缓存。依赖不会在运行期被装上来。"""
    formats: list[FormatStatus] = []
    missing: list[FormatSpec] = []

    for spec in _CATALOG:
        available = not spec.backends or any(_installed(m) for m in spec.backends)
        if not available:
            missing.append(spec)
        formats.append(
            FormatStatus(
                key=spec.key,
                label=spec.label,
                extensions=list(spec.extensions),
                available=available,
                experimental=spec.experimental,
                note=spec.note,
            )
        )

    for spec in missing:
        # 给管理员看的，所以是 ERROR 且带安装命令。这条不会出现在任何响应体里。
        logger.error(
            "解析后端缺失：%s 不可用，界面上已摘掉。装上它：%s",
            spec.label,
            spec.admin_hint,
        )

    return Capability(max_upload_mb=get_settings().max_upload_mb, formats=formats)


def unsupported(filename: str) -> FormatStatus | None:
    """扩展名认得、这个部署却没装后端的，交回那个格式；收得下或者认不出，都是 None。

    认不出的扩展名不在这里拦：真正的判定按魔数在服务端做，扩展名错了的书照样能解析，
    后端不必比前端的粗筛更严。
    """
    lowered = filename.lower()
    for fmt in capability().formats:
        if any(lowered.endswith(ext) for ext in fmt.extensions):
            return None if fmt.available else fmt
    return None


def available(key: str) -> bool:
    """按魔数判出来的这个格式，这个部署解得了吗。"""
    return any(fmt.key == key and fmt.available for fmt in capability().formats)


def admin_hint(key: str) -> str:
    """缺了这个格式的后端，管理员该装什么。只进日志，不进任何响应。"""
    return next((spec.admin_hint for spec in _CATALOG if spec.key == key), "")


def rejection_reason(filename: str) -> str:
    """这个文件名在本部署能不能收。收得下返回空串。

    只按扩展名做**上传前**的粗筛——真正的判定仍然按魔数在服务端做，
    扩展名错了的书照样能解析。这一层只是别让注定失败的文件白传一趟。
    """
    lowered = filename.lower()
    caps = capability()

    for fmt in caps.formats:
        if any(lowered.endswith(ext) for ext in fmt.extensions):
            if fmt.available:
                return ""
            return f"这个部署暂不支持 {fmt.label}。换一个格式，或者找管理员装上对应的解析后端。"

    known = "、".join(f.label for f in caps.formats if f.available)
    return f"认不出 {filename} 是什么格式。目前收这些：{known}。"
