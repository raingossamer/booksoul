"""把一个上传上来的文件解析进书库。

**这一层不碰数据库也不碰 HTTP**：文件进、目录出，外加一份说明这次解析发生了什么
的结果对象。这样切分有两个具体好处——它能用真书跑真解析而不必搭替身；以及解析
失败时的语义（哪一步停的、用户自己能做什么）集中在一个地方，不散在路由里。

DB 编排（建行拿 id、回填 storage_path、落章节索引与存疑条目）在 `BookLifecycle`。

这是 `booksoul_api` 里唯一允许 import 引擎的层之一，符合 backend.md 的分层约定。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from booksoul.model import Book
from booksoul_api.services import format_service
from booksoul_api.services.ledger import Ledger


class ImportFailed(RuntimeError):
    """这本书没能进书库。

    带上 `code` 与 `remedy`：按 Spec N1，面向用户的边界必须给出下一步动作。
    扫描版 PDF 尤其如此——引擎宁可停下也不写出一个空 md 假装成功，那么停下来的
    同时就得告诉人怎么办，而不是抛一句看不懂的话。
    """

    def __init__(self, message: str, *, code: str = "parse_failed", remedy: str = ""):
        super().__init__(message)
        self.code = code
        self.remedy = remedy


@dataclass
class ImportOutcome:
    """一次解析的产出。字段对应 books / chapters / parse_notes 三张表要的东西。"""

    title: str
    author: str = ""
    translator: str = ""
    contributor: str = ""
    publisher: str = ""
    published: str = ""
    isbn: str = ""
    language: str = ""
    description: str = ""
    source_format: str = ""
    storage_path: str = ""
    chapter_count: int = 0
    word_count: int = 0
    image_count: int = 0
    footnote_count: int = 0
    chapters: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # 解析账本：只放**真的数出来的**东西。数不出来的宁可不在报告里出现，
    # 也不编一个数字——这份报告存在的全部意义就是可以逐条核对。
    ledger: Ledger = field(default_factory=Ledger)


# (stage, done, total)。done/total 为 0 表示这个阶段没有分节刻度可报。
StageFn = Callable[[str, int, int], None]


def _remedy_for(code: str, filename: str) -> str:
    """给出**这个文件**的补救命令，不是一条带占位符的模板。

    「可以直接复制执行」是验收标准里的原话。写成「原文件.pdf」等于让用户
    自己回来替换一遍，那就不叫直接执行了。
    """
    if code == "no_text_layer":
        stem = Path(filename).stem or "输入"
        return (
            "这本 PDF 是扫描图片，里面没有文字层。先做一次 OCR 再传上来：\n"
            f"ocrmypdf -l chi_sim --force-ocr {filename} {stem}_可检索.pdf"
        )
    return ""


def import_file(
    src: Path,
    *,
    out_root: Path,
    dir_name: str,
    keep_images: bool = True,
    on_stage: StageFn | None = None,
) -> ImportOutcome:
    """解析 `src`，把产物写进 `out_root/dir_name`，返回这次解析的账。

    `dir_name` 由调用方给，不让引擎按书名取——多用户下两个人各传一本《悉达多》会写进
    同一个目录互相覆盖。Web 层给的是暂存目录（`job-<id>`）：解析完、落了库，再由
    `BookLifecycle` 换到按书名起的正式位置，在用的那一版从不被原地改写。

    `on_stage(stage, done, total)` 按 read / toc / convert / clean / write 五个阶段
    回报。只有 convert 带得出分节刻度（由解析器逐节回报），其余阶段 done/total 是 0
    —— 宁可不报，也不要编一个假的百分比，那比没有进度更误导人。
    """
    report: StageFn = on_stage or (lambda stage, done, total: None)
    from booksoul import clean, footnotes, writer
    from booksoul.parsers import detect_format
    from booksoul.parsers import parse as parse_book

    src = Path(src)
    report("read", 0, 0)
    try:
        # 按魔数判格式，不看扩展名：下载来的书扩展名经常是错的
        source_format = detect_format(str(src))
    except Exception as exc:
        raise ImportFailed(
            f"认不出这是什么格式的书：{exc}",
            code="unknown_format",
            remedy="确认文件没有损坏，或者换一个格式重新导出。",
        ) from exc
    # 按内容判出来的格式，这个部署解得了吗。上传时按扩展名拦过一道，拦不住两种：扩展名
    # 认不出、内容其实是 PDF；以及重新解析时后端已经被卸掉了。这句话只进日志
    if not format_service.available(source_format):
        raise ImportFailed(
            f"这个部署没装 {source_format} 的解析后端："
            f"{format_service.admin_hint(source_format)}",
            code="unsupported_format",
        )

    # 解析器先读目录再逐节转换，第一次分节回报到来就说明已经进了 convert。
    report("toc", 0, 0)
    try:
        book, parser = parse_book(
            str(src),
            keep_images=keep_images,
            on_progress=lambda done, total: report("convert", done, total),
        )
        try:
            report("clean", 0, 0)
            # 这两个函数本来就返回计数，之前被丢掉了。它们正是账本里最该有的两行。
            deduped_headings = clean.drop_duplicate_headings(book)
            footnote_count = footnotes.normalize_book(book)

            report("write", 0, len(book.chapters))
            summary = writer.write_book(
                book, str(out_root), images_from=parser, dir_name=dir_name
            )
            report("write", len(book.chapters), len(book.chapters))
        finally:
            # EPUB 解析器握着原件的 zip 句柄（导图片要用）。写完就放手，别等垃圾回收：
            # Windows 上一个还开着的文件删不掉、也换不掉，重新解析、删书都要动那份原件
            close = getattr(parser, "close", None)
            if close is not None:
                close()
    except ImportFailed:
        raise
    except Exception as exc:
        code = _classify(str(exc))
        raise ImportFailed(
            str(exc), code=code, remedy=_remedy_for(code, src.name)
        ) from exc

    return ImportOutcome(
        title=book.display_title(),
        author=book.meta.author,
        translator=book.meta.translator,
        contributor=book.meta.contributor,
        publisher=book.meta.publisher,
        published=book.meta.published,
        isbn=book.meta.isbn,
        language=book.meta.language,
        description=book.meta.description,
        source_format=source_format,
        storage_path=dir_name,
        chapter_count=summary["章节数"],
        word_count=summary["总字数"],
        image_count=summary["图片数"],
        footnote_count=footnote_count,
        chapters=_chapter_records(book),
        warnings=list(summary["警告"]),
        ledger=_ledger(
            book,
            source_format=source_format,
            summary=summary,
            footnotes=footnote_count,
            deduped_headings=deduped_headings,
        ),
    )


# 元信息里算「原文标注了」的字段。译者与贡献者不在其中：它们为空是常态，
# 记进命中率只会让这个比例天天不满，失去提示价值。
_META_FIELDS = ("title", "author", "publisher", "published", "isbn", "language")


def _ledger(
    book: Book,
    *,
    source_format: str,
    summary: dict,
    footnotes: int,
    deduped_headings: int,
) -> Ledger:
    """这次解析到底做了什么，逐条可核对（`services/ledger`）。

    值只放数出来的东西。解析器自己记下的那几笔（目录来源、spine 文件数、剔除的目录页……）
    原样透传；它没报的项（html2md 的站内链接转换、xmlutil 的命名空间剥离等）**不出现**。
    """
    return Ledger(
        source_format=source_format,
        chapters=summary["章节数"],
        words=summary["总字数"],
        images=summary["图片数"],
        footnotes=footnotes,
        meta_hit=sum(1 for name in _META_FIELDS if getattr(book.meta, name, "")),
        meta_fields=len(_META_FIELDS),
        deduped_headings=deduped_headings,
        engine=dict(book.stats),
    )


def _chapter_records(book: Book) -> list[dict]:
    from booksoul.writer import chapter_filename

    return [
        {
            "idx": i,
            "title": chapter.title,
            "level": chapter.level,
            "word_count": chapter.words,
            "file_path": f"chapters/{chapter_filename(chapter, i)}",
            "source_id": chapter.source_id,
        }
        for i, chapter in enumerate(book.chapters, 1)
    ]


def _classify(message: str) -> str:
    """把引擎的错误消息归到一个稳定的 error_code 上，界面据此决定怎么呈现。"""
    if "抽不出文字" in message or "没有文字层" in message:
        return "no_text_layer"
    if "没有解析出任何正文" in message or "没有可用的章节" in message:
        return "no_content"
    if "不是有效的" in message or "损坏" in message or "zip" in message.lower():
        return "bad_archive"
    return "parse_failed"
