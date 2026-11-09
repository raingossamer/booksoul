"""书库与书籍。"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, File, Query, UploadFile, status
from fastapi.responses import FileResponse

from booksoul_api.deps import CurrentUser, SessionDep
from booksoul_api.schemas.book import (
    BookDetail,
    BookOut,
    BookStateOut,
    ChapterContent,
    NoteOut,
    NotePatch,
    ReportOut,
)
from booksoul_api.schemas.common import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Page
from booksoul_api.schemas.parse import UploadAccepted
from booksoul_api.services.book_lifecycle import BookLifecycle
from booksoul_api.services.library_service import LibraryService
from booksoul_api.services.reader_service import ReaderService

router = APIRouter(prefix="/api/books", tags=["books"])


@router.get("", response_model=Page[BookOut])
async def list_books(
    user: CurrentUser,
    session: SessionDep,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
) -> Page[BookOut]:
    return await LibraryService(session).list_books(
        user_id=user.id, page=page, size=size
    )


@router.post("", response_model=UploadAccepted, status_code=status.HTTP_202_ACCEPTED)
async def upload_book(
    user: CurrentUser,
    session: SessionDep,
    file: UploadFile = File(...),
) -> UploadAccepted:
    """收下一本书，后台解析（由 `BookLifecycle` 自己排，这里没有第二步）。

    202 而不是 201：解析一本大部头要几十秒，把请求挂在那里等着毫无好处。
    前端拿 jobId 轮询 `GET /api/jobs/{jobId}`（S3 起改成 SSE 实时推）。

    一次一个文件。「一批里单个失败不影响其余」因此是天然成立的——
    每个文件各走各的请求、各有各的 job，互不相干。
    """
    return await BookLifecycle(session).upload(
        user_id=user.id, filename=file.filename or "upload", source=file
    )


@router.post(
    "/{book_id}/reparse",
    response_model=UploadAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def reparse_book(
    book_id: int,
    user: CurrentUser,
    session: SessionDep,
) -> UploadAccepted:
    """用当初那个原始文件重跑一遍，复用同一个 book_id（书名没变时目录也不变）。

    解析被中断（服务重启）或失败之后，这是用户唯一的出路——没有它，
    界面上那句「重新解析」就是一句空话。
    """
    return await BookLifecycle(session).reparse(book_id=book_id, user_id=user.id)


@router.get("/{book_id}", response_model=BookDetail)
async def get_book(book_id: int, user: CurrentUser, session: SessionDep) -> BookDetail:
    """书籍详情（界面稿④屏）：元信息 + 目录树 + 字数 + 存疑条目 + 装得进哪个大脑。"""
    return await LibraryService(session).book_detail(book_id=book_id, user_id=user.id)


@router.get("/{book_id}/state", response_model=BookStateOut)
async def get_book_state(
    book_id: int, user: CurrentUser, session: SessionDep
) -> BookStateOut:
    """这本书此刻的状态。书籍详情在解析中每一拍只问这一句（一条查询），好了再拉整份详情。"""
    state = await BookLifecycle(session).state(book_id=book_id, user_id=user.id)
    return BookStateOut(**asdict(state))


@router.get("/{book_id}/report", response_model=ReportOut)
async def get_report(book_id: int, user: CurrentUser, session: SessionDep) -> ReportOut:
    """解析报告（界面稿②屏）：一本可以逐条核对的账。"""
    return await LibraryService(session).report(book_id=book_id, user_id=user.id)


@router.get("/{book_id}/chapters/{idx}", response_model=ChapterContent)
async def get_chapter(
    book_id: int, idx: int, user: CurrentUser, session: SessionDep
) -> ChapterContent:
    """单章正文（界面稿⑤屏）。

    `idx` 是 1 起的阅读序号，与章节文件名前缀、引擎 `load_from_path(only_idx=...)`
    同源——**不是 chapters.id**。两者在这条路上极易混淆，混了就读到错位的章节。

    返回的是磁盘上那个 md 文件的原文，一个字没动。
    """
    return await ReaderService(session).chapter(
        book_id=book_id, idx=idx, user_id=user.id
    )


@router.get("/{book_id}/images/{name}")
async def get_image(
    book_id: int, name: str, user: CurrentUser, session: SessionDep
) -> FileResponse:
    """章节里引的一张图（⑤屏）。`<img src>` 带不上 Authorization 头，所以前端
    带着 token 把它取成 blob 再显示——这条路和别的接口一样要登录、只认自己的书。

    `nosniff`：类型按扩展名给，不许浏览器照内容去猜。一个叫 .png 的文件里
    要是其实是一段 HTML，猜出来就是一张能跑脚本的页面。
    """
    path, media_type = await ReaderService(session).image(
        book_id=book_id, name=name, user_id=user.id
    )
    return FileResponse(
        path,
        media_type=media_type,
        headers={
            "X-Content-Type-Options": "nosniff",
            # 书里的图不会变（重解析会换目录里的文件，但也换正文）；private：带着登录态取的
            "Cache-Control": "private, max-age=86400",
        },
    )


@router.patch("/{book_id}/notes/{note_id}", response_model=NoteOut)
async def resolve_note(
    book_id: int,
    note_id: int,
    payload: NotePatch,
    user: CurrentUser,
    session: SessionDep,
) -> NoteOut:
    """把一条存疑标为已核对。核对过的不再打扰。"""
    return await LibraryService(session).resolve_note(
        book_id=book_id, note_id=note_id, user_id=user.id, resolved=payload.resolved
    )
