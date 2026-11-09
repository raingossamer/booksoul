"""书库：列表、详情、解析报告、存疑核对。

按 user_id 隔离这件事在 repository 层做（那里每句查询都带条件），这一层只做
参数归一、实体到 schema 的转换，以及「这些数字该怎么说给人听」。
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from booksoul_api.core.errors import ApiError, NotFoundError
from booksoul_api.models.book import Book
from booksoul_api.models.chapter import Chapter
from booksoul_api.models.parse import ParseNote
from booksoul_api.repositories.book_repo import BookRepository
from booksoul_api.repositories.conversation_repo import ConversationRepository
from booksoul_api.repositories.parse_repo import ParseRepository
from booksoul_api.schemas.book import (
    BookDetail,
    BookOut,
    BrainFit,
    ChapterOut,
    ModerationOut,
    NoteOut,
    OutputFile,
    ReportOut,
)
from booksoul_api.schemas.common import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Page
from booksoul_api.services.book_lifecycle import (
    BookLifecycle,
    book_dir,
    source_path,
)
from booksoul_api.services.brain_service import BrainService
from booksoul_api.services.ledger import Ledger
from booksoul_api.services.moderation import ModerationService
from booksoul_api.services.quality import short_chapters


class LibraryService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._books = BookRepository(session)
        self._parse = ParseRepository(session)

    # ------------------------------------------------------------ 列表（③屏）

    async def list_books(
        self, *, user_id: int, page: int = 1, size: int = DEFAULT_PAGE_SIZE
    ) -> Page[BookOut]:
        page = max(1, page)
        size = min(max(1, size), MAX_PAGE_SIZE)

        rows, total = await self._books.list_for_user(
            user_id, offset=(page - 1) * size, limit=size
        )
        book_ids = [b.id for b in rows]
        notes = await self._parse.unresolved_counts(book_ids)
        # 状态与停在哪一步读同一处：卡片、详情、轮询说的是同一件事
        states = await BookLifecycle(self._session).states(
            user_id=user_id, book_ids=book_ids
        )
        items = [
            BookOut(
                id=b.id,
                title=b.title,
                author=b.author,
                status=states[b.id].status,
                source_format=b.source_format,
                chapter_count=b.chapter_count,
                word_count=b.word_count,
                note_count=notes.get(b.id, 0),
                error_code=states[b.id].error_code,
                created_at=b.created_at,
            )
            for b in rows
        ]
        return Page[BookOut](items=items, total=total, page=page, size=size)

    # ------------------------------------------------------------ 详情（④屏）

    async def book_detail(self, *, book_id: int, user_id: int) -> BookDetail:
        book = await self._require(book_id, user_id)
        state = await BookLifecycle(self._session).state(
            book_id=book.id, user_id=user_id
        )
        chapters = await self._books.list_chapters(book.id)
        notes = await self._parse.list_notes(book.id)

        # 哪几节短到该让人看一眼。判据与解析时落存疑条目用的是**同一个函数** ——
        # 两处各写一套的话，目录树上标黄的和提示条里说的迟早对不上。
        short_idx = {
            item["idx"]
            for item in short_chapters(
                [
                    {"idx": c.idx, "title": c.title, "word_count": c.word_count}
                    for c in chapters
                ]
            )
        }

        blocked = await ModerationService(self._session).for_book(
            book_id=book.id, user_id=user_id
        )
        pinned = (
            set(blocked.suspect_chapter_ids) if blocked and blocked.pinned else set()
        )

        return BookDetail(
            id=book.id,
            title=book.title,
            author=book.author,
            translator=book.translator,
            contributor=book.contributor,
            publisher=book.publisher,
            published=book.published,
            isbn=book.isbn,
            language=book.language,
            description=book.description,
            source_file=book.source_file,
            source_format=book.source_format,
            storage_path=book.storage_path,
            chapter_count=book.chapter_count,
            word_count=book.word_count,
            status=state.status,
            created_at=book.created_at,
            # ④屏那个「重新解析」按钮要先说一句「有 N 个会话会受影响」
            conversation_count=await ConversationRepository(
                self._session
            ).count_for_book(book.id),
            moderation=_moderation_out(blocked),
            # `blocked` 只在嫌疑收窄到一节时非空 —— 分不出是哪一节就一节都不挂，
            # 随便挂一个等于凭空指认，用户会照着去删一节没问题的正文。
            chapters=[
                _chapter_out(c, c.idx in short_idx, c.id in pinned) for c in chapters
            ],
            notes=[_note_out(n) for n in notes],
            # 直接读⑧屏那份列表，不另算：两页各算各的，迟早一页说装得下、
            # 另一页说要挑章节。用户自己加的端点也在里面。
            brains=[
                BrainFit(
                    name=b.name,
                    model=b.model,
                    context_chars=b.context_chars,
                    context_source=b.context_source,
                    fits=b.fits,
                )
                for b in await BrainService(self._session).list_for_user(
                    user_id=user_id, book_id=book.id
                )
            ],
        )

    async def resolve_note(
        self, *, book_id: int, note_id: int, user_id: int, resolved: bool
    ) -> NoteOut:
        """把一条存疑标记为已核对。核对过的不再打扰。"""
        await self._require(book_id, user_id)
        note = await self._session.get(ParseNote, note_id)
        if note is None or note.book_id != book_id:
            raise NotFoundError("没有这条存疑记录。")
        note.resolved = resolved
        await self._session.commit()
        return _note_out(note)

    # ------------------------------------------------------------ 报告（②屏）

    async def report(self, *, book_id: int, user_id: int) -> ReportOut:
        book = await self._require(book_id, user_id)
        job = await self._parse.latest_job_for_book(book.id)
        stats: dict = dict(job.cleanup_stats or {}) if job else {}

        elapsed = None
        if job and job.started_at and job.finished_at:
            elapsed = (job.finished_at - job.started_at).total_seconds()

        return ReportOut(
            book_id=book.id,
            title=book.title,
            source_file=book.source_file,
            source_size_bytes=_source_size(book),
            elapsed_seconds=elapsed,
            ledger=Ledger.from_json(stats).rows(book=book),
            stats=stats,
            outputs=_outputs(book_dir(book)),
            storage_path=book.storage_path,
            notes=[_note_out(n) for n in await self._parse.list_notes(book.id)],
        )

    async def _require(self, book_id: int, user_id: int) -> Book:
        book = await self._books.get_for_user(book_id, user_id)
        if book is None:
            # 越权与不存在同一个 404：403 等于承认这个 id 确实存在
            raise NotFoundError("没有这本书。它可能已经被删掉了。")
        return book


def _chapter_out(chapter: Chapter, short: bool, blocked: bool = False) -> ChapterOut:
    return ChapterOut(
        id=chapter.id,
        idx=chapter.idx,
        title=chapter.title,
        level=chapter.level,
        word_count=chapter.word_count,
        file_path=chapter.file_path,
        short=short,
        blocked=blocked,
    )


def _moderation_out(found) -> ModerationOut | None:
    if found is None:
        return None
    return ModerationOut(
        times=found.times,
        brain_name=found.brain_name,
        at=found.at,
        suspect_chapter_ids=found.suspect_chapter_ids,
        suspect_titles=found.suspect_titles,
        pinned=found.pinned,
        advice=found.advice,
    )


def _note_out(note: ParseNote) -> NoteOut:
    return NoteOut(
        id=note.id,
        chapter_id=note.chapter_id,
        severity=note.severity,
        code=note.code,
        message=note.message,
        resolved=note.resolved,
        created_at=note.created_at,
    )


def _source_size(book: Book) -> int:
    try:
        return source_path(book).stat().st_size
    except OSError:
        return 0


def _outputs(root: Path | None) -> list[OutputFile]:
    """产出文件清单。

    直接读磁盘而不是从数据库拼：这份清单的意义正是「你可以把这些文件拿走」，
    那它就该反映磁盘上真实的样子，包括用户自己手工改过之后的样子。
    """
    if root is None or not root.is_dir():
        return []

    out: list[OutputFile] = []
    try:
        for entry in sorted(root.iterdir(), key=lambda p: (p.is_dir(), p.name)):
            if entry.is_dir():
                children = list(entry.iterdir())
                out.append(
                    OutputFile(
                        path=f"{entry.name}/",
                        size_bytes=sum(
                            c.stat().st_size for c in children if c.is_file()
                        ),
                        is_dir=True,
                        child_count=len(children),
                    )
                )
            else:
                out.append(
                    OutputFile(
                        path=entry.name,
                        size_bytes=entry.stat().st_size,
                        is_dir=False,
                    )
                )
    except OSError as exc:
        raise ApiError(f"读不到这本书的产物目录：{exc}") from exc
    return out
