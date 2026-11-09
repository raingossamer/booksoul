"""一本书的生命周期：从收件到可用，中间每一步的状态与判断都收在这里。

**门。** 这本书此刻能不能读、能不能问、能不能重新解析。原先三处各判——对话非 ready 即拒、
重解析只拦正在解析、阅读器根本不看状态——同一种状态回的码与话各不相同。现在只有一张门表
（`_GATE`），过了门交回一本**磁盘位置已经拼好**的书（`UsableBook`）：过门与拼路径合成一步，
调用方想漏也漏不掉。

「有已发布的一版」看的是 `storage_path`：解析成功那一刻才写上。重解析中、重解析失败之后，
这本书仍有上一次成功的那一版——读正文照读它，问与重解析按状态拦。照读之所以安全，是因为
在用的那个目录**从不被原地改写**：引擎只往暂存目录里写，提交之后才整个换上去（`_publish`）。

**收件与后台解析。** 真正的解析在 `importer.import_file`（不碰数据库）；这里做数据库那一半：
建行拿 id、把进度与结局写回、失败时把「停在哪、你能做什么」记下来。几个刻意的决定：

1. **排任务归这里，不归路由。** `upload()` / `reparse()` 提交之后当场排上后台解析，调用方
   没有第二步——原先两个端点各写一遍 `background.add_task(...)`，哪一处漏了就是一本永远
   「解析中」的书。
2. **books 行必须在解析之前插入。** 目录名是 `<user_id>/<book_id>-<slug>`，含自身主键，而
   IDENTITY 主键要 INSERT 之后才有。所以 `storage_path` 建行时留空，落盘成功后再回填——
   这也是它在 schema 里可空的原因。
3. **解析跑在线程里。** `import_file` 是同步的 CPU + 磁盘活，直接 await 会把整个事件循环钉住，
   别人的请求全部排队。
4. **原件留着。** 重新解析要从它开始；不留就只剩「删掉重传」一条路，而解析失败恰恰是最需要
   重试的时候。
5. **工作线程不碰 session。** 进度由线程写几个标量，事件循环里另开一个 session 定期落库——
   `AsyncSession` 不是线程安全的，跨线程用它会变成那种偶发、难查、看起来像数据库抽风的 bug。
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePath, PurePosixPath

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from booksoul.writer import slugify
from booksoul_api.core.config import get_settings
from booksoul_api.core.errors import (
    ApiError,
    BookNotReady,
    NotFoundError,
    UnsupportedFormat,
)
from booksoul_api.core.paths import under
from booksoul_api.db.session import get_session_factory
from booksoul_api.db.unit_of_work import Detached
from booksoul_api.models.book import Book
from booksoul_api.models.chapter import Chapter
from booksoul_api.models.parse import ParseJob, ParseNote
from booksoul_api.repositories.book_repo import BookRepository
from booksoul_api.repositories.conversation_repo import ConversationRepository
from booksoul_api.repositories.parse_repo import ParseRepository
from booksoul_api.schemas.parse import JobOut, UploadAccepted
from booksoul_api.services import format_service, resettle
from booksoul_api.services.importer import ImportFailed, ImportOutcome, import_file
from booksoul_api.services.quality import short_chapter_note, short_chapters

logger = logging.getLogger(__name__)

# 重新解析的出路。按 Spec N1，这是用户自己能执行的动作，所以进 remedy。
_REPARSE_HINT = "在书库里点这本书的「重新解析」再试一次。"


@dataclass(frozen=True)
class _Failure:
    message: str  # 给人看的那一句
    remedy: str  # 用户自己能做的下一步；importer 给了更具体的（带真文件名的 OCR 命令）就用它


# 码表：解析停下时给人看的话只从这里来。引擎的原话（安装命令、服务器上的路径、异常链）、
# 数据库的报错（带着 SQL 与参数）只进 ERROR 日志，带书 id 与任务 id——那是写给修它的人看的。
_FAILURES: dict[str, _Failure] = {
    "no_text_layer": _Failure(
        "这本 PDF 是扫描出来的图片，里面没有文字层，抽不出正文。",
        "先做一次 OCR，再把识别过的 PDF 传上来。",
    ),
    "no_content": _Failure(
        "这个文件里没有解析出任何正文。",
        "确认文件没有损坏；或者换一个格式（比如 EPUB）再传一次。",
    ),
    "bad_archive": _Failure(
        "这个文件不完整，或者已经损坏，打不开。",
        "重新下载一份完整的文件，再传一次。",
    ),
    "unknown_format": _Failure(
        "认不出这是什么格式的书。",
        "确认文件没有损坏，或者换一个格式导出之后再传。",
    ),
    "unsupported_format": _Failure(
        "这个部署暂不支持这种格式。",
        "换一个格式（比如 EPUB）再传一次；要用这种格式，找管理员装上对应的解析后端。",
    ),
    "parse_failed": _Failure("这本书没能解析完。", _REPARSE_HINT),
    "internal": _Failure("这次解析没能完成，问题出在服务器这边。", _REPARSE_HINT),
    "orphaned": _Failure("服务重启时这次解析被中断了。", _REPARSE_HINT),
}


def _failure(code: str) -> _Failure:
    # 认不出的码（旧行里可能有）按「没能解析完」说：宁可笼统，也不把旧行里的原话端出去
    return _FAILURES.get(code, _FAILURES["parse_failed"])


class Use(StrEnum):
    """拿这本书来做什么。门表按它分列。"""

    READ = "read"  # 阅读器：正文、书里的图
    ASK = "ask"  # 开会话、提问
    REPARSE = "reparse"  # 重新解析


@dataclass(frozen=True)
class UsableBook:
    """过了门的书：此刻允许这次用途。只读快照，不是 ORM 行——出了请求照样能用。"""

    id: int
    title: str
    word_count: int
    chapter_count: int
    # books.storage_path，相对书库根。从没解析成功过为 None：那样的书只过得了「重新解析」
    # 那一道门，而重新解析要的是原件，不是这里
    shelf: str | None

    @property
    def root(self) -> Path:
        """已发布那一版的目录。书库根只在这里和一本书的路径拼在一起。"""
        if not self.shelf:
            raise LookupError(f"《{self.title}》还没有解析成功过，没有已发布的一版")
        return Path(get_settings().library_root) / self.shelf

    def file(self, *parts: str) -> Path | None:
        """书目录下的一个文件。跑出了书目录（`..`、符号链接、空字符）返回 None，由调用方说怎么办。"""
        return under(self.root, *parts)

    def display(self, rel: str) -> str:
        """⑤屏底下那行路径：从书库根的目录名起算，一律正斜杠。

        不给服务器上的绝对路径：compose 下那是容器里的 `/data/library/…`，在宿主机上照着它
        什么也找不到。从书库根的目录名起算，本地开发与 compose 都是 `library/…`。
        正斜杠是因为这行字给人看、给人复制，不是拿去 `open()` 的。
        """
        library = Path(get_settings().library_root)
        return PurePosixPath(
            library.resolve().name,
            *self.root.relative_to(library).parts,
            *PurePath(rel).parts,
        ).as_posix()


@dataclass(frozen=True)
class _Refusal:
    """门口拦下时的那句话。`code` 为空是 404：对这次用途而言，这本书的这一面并不存在。"""

    code: str
    text: str  # 带 {title}

    def error(self, title: str) -> ApiError:
        message = self.text.format(title=title)
        if self.code:
            return BookNotReady(message, code=self.code)
        return NotFoundError(message)


_PARSING = "book_parsing"
_FAILED = "book_failed"

# 门表：(状态, 有没有已发布的一版) × 用途 → 拦下时说的话；表里没有的格子就是放行。
# 阅读、对话、重解析读的都是这一张，措辞只写在这里。
# 没有 ("ready", False) 这一行：`ck_books_ready_has_path` 让它不可能存在。
_GATE: dict[tuple[str, bool], dict[Use, _Refusal]] = {
    ("parsing", False): {
        Use.READ: _Refusal("", "《{title}》还在解析，正文还没写出来。解析完再来读。"),
        Use.ASK: _Refusal(
            _PARSING,
            "《{title}》还在解析，等它解析完再问——这一问还没发出去，没花钱。",
        ),
        Use.REPARSE: _Refusal(_PARSING, "《{title}》正在解析，等它停下来再说。"),
    },
    ("parsing", True): {
        Use.ASK: _Refusal(
            _PARSING,
            "《{title}》正在重新解析，等它解析完再问——这一问还没发出去，没花钱。",
        ),
        Use.REPARSE: _Refusal(_PARSING, "《{title}》正在重新解析，等它停下来再说。"),
    },
    ("ready", True): {},
    ("failed", False): {
        Use.READ: _Refusal(
            "",
            "《{title}》没有解析成功，没有正文可读。到书籍详情页看解析报告，或重新解析。",
        ),
        Use.ASK: _Refusal(
            _FAILED,
            "《{title}》没有解析成功，没法拿它对话。到书籍详情页看看它停在哪一步。",
        ),
    },
    ("failed", True): {
        Use.ASK: _Refusal(
            _FAILED,
            "《{title}》上一次解析停下了，现在没法拿它对话。到书籍详情页看看它停在哪一步。",
        ),
    },
}

# 状态允许、原件却不在了：重新解析无从谈起。与门表同处写，措辞也只在这里
_SOURCE_MISSING = _Refusal(
    "source_missing",
    "《{title}》当初上传的原件找不到了，没法重新解析。把它再传一次吧。",
)


@dataclass(frozen=True)
class BookState:
    """一本书此刻的状态，连同最近一次解析任务。书库卡片、书籍详情、轮询读的都是它。"""

    book_id: int
    status: str
    published: bool  # 有没有已发布的一版（门表的另一半）
    job_id: int | None  # 最近一次任务；从没排过解析为 None
    stage: str  # read / toc / convert / clean / write
    done: int
    total: int
    error_code: str  # 最近一次停下的码；在跑或成功时为空
    message: str
    remedy: str  # 用户自己能做的下一步

    def allows(self, use: Use) -> bool:
        """与 `require()` 查同一张表：说「能」的时候门一定开。"""
        return use not in _GATE[(self.status, self.published)]


class UploadTooLarge(ApiError):
    status_code = 413


@dataclass
class _Tracker:
    """工作线程写、事件循环读的进度。

    三个标量的赋值在 CPython 里是原子的，不必加锁；事件循环读到上一拍的值
    对进度条毫无影响。真正重要的是这里**没有 session** —— 见模块注释第 5 条。
    """

    stage: str = "read"
    done: int = 0
    total: int = 0

    def update(self, stage: str, done: int, total: int) -> None:
        self.stage, self.done, self.total = stage, done, total


class BookLifecycle:
    """一本书从收件到可用：门（`require`）、收件（`upload`）、重新解析（`reparse`），
    以及它们排出去的后台解析。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._books = BookRepository(session)
        self._parse = ParseRepository(session)

    # ------------------------------------------------------------ 门

    async def require(self, *, book_id: int, user_id: int, use: Use) -> UsableBook:
        """这本书此刻能不能拿来做 `use`。能就交回拼好了路径的书，不能就抛门表里那句话。

        404：没有这本书、不是你的，或者对读正文而言它还没有一版可读。
        409 `BookNotReady`：问与重新解析被它的状态拦下，带 `code`。
        """
        book = await self._books.get_for_user(book_id, user_id)
        if book is None:
            # 越权与不存在同一个 404：403 等于承认这个 id 确实存在
            raise NotFoundError("没有这本书。它可能已经被删掉了。")
        refusal = _GATE[(book.status, bool(book.storage_path))].get(use)
        if refusal is not None:
            raise refusal.error(book.title)
        return UsableBook(
            id=book.id,
            title=book.title,
            word_count=book.word_count,
            chapter_count=book.chapter_count,
            shelf=book.storage_path,
        )

    # ------------------------------------------------------------ 状态

    async def state(self, *, book_id: int, user_id: int) -> BookState:
        """这本书此刻的状态。没有 / 不是你的 → 404。"""
        states = await self.states(user_id=user_id, book_ids=[book_id])
        if book_id not in states:
            raise NotFoundError("没有这本书。它可能已经被删掉了。")
        return states[book_id]

    async def states(
        self, *, user_id: int, book_ids: Sequence[int]
    ) -> dict[int, BookState]:
        """一页书各自的状态，一条查询取齐。不是你的书不在结果里。"""
        return {
            row.book_id: BookState(
                book_id=row.book_id,
                status=row.status,
                published=bool(row.storage_path),
                job_id=row.job_id,
                stage=row.stage or "",
                done=row.progress_done or 0,
                total=row.progress_total or 0,
                error_code=row.error_code or "",
                # 按码从码表取，不读那一列：旧行里可能还留着引擎的原话
                message=_failure(row.error_code).message if row.error_code else "",
                remedy=row.remedy or "",
            )
            for row in await self._parse.book_states(user_id, book_ids)
        }

    # ------------------------------------------------------------ 进度

    async def job(self, *, job_id: int, user_id: int) -> JobOut:
        """一次解析任务眼下的样子（轮询兜底读它）。别人的、不存在的 → 404。"""
        job = await self._parse.get_job_for_user(job_id, user_id)
        if job is None:
            raise NotFoundError("没有这个解析任务。它可能已经随书一起被删掉了。")
        return _job_out(job)

    async def watch(
        self, *, job_id: int, user_id: int
    ) -> AsyncIterator[tuple[str, dict]]:
        """解析进度的事件流：`("stage" | "done" | "error", 载荷)`。

        **普通协程，不是异步生成器**（同 `ChatService.ask`）：404 在 await 这一刻抛，在响应头
        之前。写成生成器的话，检查要等第一次迭代才跑，那时 `200 text/event-stream` 已经发出去，
        看到的是一条莫名断掉的空流。返回的流自己开 session，出了请求照样能用。
        """
        await self.job(job_id=job_id, user_id=user_id)
        return _follow(job_id, user_id)

    # ------------------------------------------------------------ 收件

    async def upload(self, *, user_id: int, filename: str, source) -> UploadAccepted:
        """收下一本书：建出 books 与 parse_jobs 两行，排上后台解析。

        先写临时盘再搬到最终位置：体积校验要在任何一次数据库写入之前完成，否则一个超限的
        上传会留下一行没有文件的书；而最终位置含 book_id，那要 INSERT 之后才有。

        书名先用文件名顶着——真书名要解析完才知道，但书库列表在解析期间就得显示点什么，
        显示一个文件名总好过显示「未命名」。
        """
        # 认得扩展名、这个部署却没装后端的，读一个字节之前就拒：不建行、不存原件。
        # 装什么包只进日志——那是管理员的事，传书的人做不了
        refused = format_service.unsupported(filename)
        if refused is not None:
            logger.error(
                "收件被拒：这个部署没装 %s 的解析后端（%s）",
                refused.label,
                format_service.admin_hint(refused.key),
            )
            raise UnsupportedFormat(format_service.rejection_reason(filename))

        settings = get_settings()
        staging = Path(tempfile.mkdtemp(prefix="booksoul-upload-"))
        staged = staging / (Path(filename).name or "upload")
        final: Path | None = None

        limit = settings.max_upload_mb * 1024 * 1024
        written = 0
        try:
            with open(staged, "wb") as out:
                while chunk := await _read_chunk(source):
                    written += len(chunk)
                    if written > limit:
                        raise UploadTooLarge(
                            f"这个文件超过 {settings.max_upload_mb} MB 的上限。"
                            "换一个体积小些的版本，或者先在本地拆分。"
                        )
                    out.write(chunk)
            if written == 0:
                raise ApiError("这是个空文件，里面没有内容。确认一下再传。")

            book = await self._books.add(
                Book(
                    user_id=user_id,
                    title=Path(filename).stem or "未命名",
                    source_file=Path(filename).name,
                    status="parsing",
                )
            )
            job = await self._parse.add_job(ParseJob(book_id=book.id, stage="read"))

            final = source_path(book)
            final.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(staged), str(final))
            await self._session.commit()
        except Exception:
            # 原件已经搬过去了、那一行却没提交成：没有任何一行指着它，留着就是孤儿。
            # 只接 Exception：被取消时提交可能已经落了，那时删掉原件，这本书就再也
            # 重新解析不了——留一个孤儿文件比这个便宜
            if final is not None:
                shutil.rmtree(final.parent, ignore_errors=True)
            raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)

        _start(book.id, job.id)
        return UploadAccepted(book_id=book.id, job_id=job.id)

    # ------------------------------------------------------------ 重新解析

    async def reparse(self, *, book_id: int, user_id: int) -> UploadAccepted:
        """用原件重跑一遍，复用同一个 book_id（书名没变时目录也不变），排上后台解析。

        回执里的受影响会话数是**真正动手这一刻**数出来的，和界面在确认框里显示的那个
        不一定相等——中间可能又开了一个会话。两处都给，因为它们回答的是两个问题：
        确认框问「值不值得」，回执说「实际动了多少」。

        善后（清选章锁定、引文重定位）不在这里，在解析成功那一刻（`_record_success` →
        `resettle.swap`）：章节是在那一刻才被换掉的。提前清掉锁定而解析又失败了，等于
        白白毁掉几个本来还能用的会话。
        """
        book = await self._books.get_for_user(book_id, user_id)
        if book is None:
            raise NotFoundError("没有这本书。它可能已经被删掉了。")
        # 原件先查：不在的话一行都不写，这本书照旧读得了、问得了
        if not source_path(book).is_file():
            raise _SOURCE_MISSING.error(book.title)
        # 「不在解析中才改成解析中」是一句 UPDATE：两个几乎同时的请求只有一个改得到。
        # 原先先读状态再改，两个都穿得过检查，两个工作线程同时往同一个目录里写
        if not await self._books.claim_for_parsing(book.id, user_id):
            published = bool(book.storage_path)
            raise _GATE[("parsing", published)][Use.REPARSE].error(book.title)

        affected = await ConversationRepository(self._session).count_for_book(book.id)
        job = await self._parse.add_job(ParseJob(book_id=book.id, stage="read"))
        await self._session.commit()

        _start(book.id, job.id)
        return UploadAccepted(
            book_id=book.id, job_id=job.id, affected_conversations=affected
        )

    # ------------------------------------------------------------ 后台解析

    async def _execute(self, book_id: int, job_id: int) -> None:
        book = await self._session.get(Book, book_id)
        job = await self._parse.get_job(job_id)
        if book is None or job is None:  # 用户在解析开始前把书删了
            return

        job.started_at = datetime.now(UTC)
        job.heartbeat_at = datetime.now(UTC)
        await self._session.commit()

        # 先把要用的值取出来：工作线程里碰 ORM 对象可能触发懒加载，而懒加载要用
        # session —— session 不是线程安全的。
        owner_id = book.user_id
        source = source_path(book)
        # 引擎只往暂存目录里写：在用的那一版从不被原地改写，见 `_publish`
        staging = _staging_root() / f"job-{job_id}"
        tracker = _Tracker()
        pump = asyncio.create_task(_pump_progress(job_id, tracker))

        try:
            try:
                outcome = await asyncio.to_thread(
                    import_file,
                    source,
                    out_root=staging.parent,
                    dir_name=staging.name,
                    on_stage=tracker.update,
                )
            finally:
                # 先停泵再落最终状态：否则泵的最后一拍可能把结论盖回去，
                # 界面上就会看到「已完成」又跳回「转换正文」。
                await _stop(pump)
        except ImportFailed as failure:
            # 引擎的原话只进日志（连同回溯）；给人看的那句从码表来，见 `_record_failure`
            logger.error(
                "解析停下了：书 %s 任务 %s（%s）：%s",
                book_id,
                job_id,
                failure.code,
                failure,
                exc_info=True,
            )
            _discard(staging)
            await self._record_failure(book, job, failure.code, remedy=failure.remedy)
            return
        except Exception:  # 引擎之外的意外，同样不能让书卡在解析中
            logger.exception("解析时出了引擎之外的意外：书 %s 任务 %s", book_id, job_id)
            _discard(staging)
            await self._record_failure(book, job, "internal")
            return

        shelf = f"{owner_id}/{book_id}-{slugify(outcome.title)}"
        try:
            await self._record_success(book, job, outcome, staging=staging, shelf=shelf)
        except Exception:
            # 解析本身成了，出事的是写库那一半（某一栏超长、撞了约束……）。不接住的话
            # 后台任务就这么死了：书停在「解析中」，提问永远 409、也不许重新解析，
            # 进度推送一直轮询下去，直到进程重启才被当成孤儿收掉。
            # 异常原文只进日志：数据库的报错带着 SQL 与参数，那不是给人看的
            logger.exception("解析结果没能落库：书 %s 任务 %s", book_id, job_id)
            _discard(staging)
            await self._fail_after_rollback(book_id, job_id)
            return

        # 提交之后才换上去：库里先说新版在哪，盘上再照着换。这一步失败（Windows 上目录
        # 被占着之类）或者进程就死在这儿，暂存目录留着，下次启动清扫照库里的话补完
        try:
            _publish(staging, Path(get_settings().library_root) / shelf, book_id)
        except OSError:
            logger.exception(
                "新版没能换上去，留给下次启动补完：书 %s 任务 %s", book_id, job_id
            )

    async def _fail_after_rollback(self, book_id: int, job_id: int) -> None:
        """写库那一半出了事：整个撤回，再把这本书记成停下了。

        撤回之后手上的 ORM 对象全部过期，得重新取——异步 session 里碰一个过期的属性
        不会自己去读，只会抛错。重解析时换到一半的章节、清掉的锁定也跟着撤回，
        书回到这次解析之前的样子，只是状态记成「停下了」，可以再解析一次。
        """
        await self._session.rollback()
        book = await self._session.get(Book, book_id)
        job = await self._parse.get_job(job_id)
        if book is None or job is None:  # 这当中书被删了
            return
        await self._record_failure(book, job, "internal")

    async def _record_success(
        self,
        book: Book,
        job: ParseJob,
        outcome: ImportOutcome,
        *,
        staging: Path,
        shelf: str,
    ) -> None:
        """一个事务写下这次解析的结果。`shelf` 是新版要换到的位置（提交之后才换，见
        `_publish`）；`staging` 是新正文眼下所在的暂存目录——重定位引文读的是它。"""
        # 书里写什么就是什么，而这几列有宽度：按列宽截断，见 `_fit`
        width = Book.__table__.c
        book.title = _fit(width.title, outcome.title)
        book.author = _fit(width.author, outcome.author)
        book.translator = _fit(width.translator, outcome.translator)
        book.contributor = _fit(width.contributor, outcome.contributor)
        book.publisher = _fit(width.publisher, outcome.publisher)
        book.published = outcome.published
        book.isbn = outcome.isbn
        book.language = _fit(width.language, outcome.language)
        book.description = outcome.description
        book.source_format = outcome.source_format
        book.storage_path = shelf
        book.chapter_count = outcome.chapter_count
        book.word_count = outcome.word_count
        book.status = "ready"

        # 换章节连同它的善后（清锁定、拍引文落点、找回引文）都在 `swap()` 里：先会话行、
        # 后章节行，次序只在那里写一次。首次解析也走这一遍——那时两条查询都空手而归，
        # 而多一个分支就多一条平时走不到、因而没人验过的路。
        chapter_title = Chapter.__table__.c.title
        swapped = await resettle.swap(
            self._session,
            book_id=book.id,
            records=[
                record | {"title": _fit(chapter_title, record["title"])}
                for record in outcome.chapters
            ],
            root=staging,
        )
        by_idx = {row.idx: row.id for row in swapped.chapters}

        notes = [
            ParseNote(
                book_id=book.id,
                severity="doubt",
                code="engine_warning",
                message=warning,
            )
            for warning in outcome.warnings
        ]
        # 字数条即质检信号：短到不可能是正文的章节要能被一眼看见并点开核对。
        # 挂上 chapter_id，界面才跳得过去。
        notes += [
            ParseNote(
                book_id=book.id,
                chapter_id=by_idx.get(short["idx"]),
                severity="doubt",
                code="short_chapter",
                message=short_chapter_note(short),
            )
            for short in short_chapters(outcome.chapters)
        ]
        await self._parse.replace_notes(book.id, notes)

        job.stage = "write"
        job.progress_done = outcome.chapter_count
        job.progress_total = outcome.chapter_count
        job.detected_format = outcome.source_format
        job.error_code = ""
        job.error_message = ""
        job.remedy = ""
        # 引文那一笔只在真有引文要搬家时才记——那条规矩只写在 `Ledger.moved()` 里
        job.cleanup_stats = outcome.ledger.moved(swapped.relocation).to_json()
        job.heartbeat_at = datetime.now(UTC)
        job.finished_at = datetime.now(UTC)
        await self._session.commit()

    async def _record_failure(
        self, book: Book, job: ParseJob, code: str, *, remedy: str = ""
    ) -> None:
        """停下来，并把用户自己能做的那一步一起记下来。

        给人看的那句只从码表来（`_FAILURES`）。`remedy` 是 importer 给的更具体的一步——
        它是我们自己组的话（带真文件名的 OCR 命令），不是引擎的原话；没有就用码表那一步。
        不写 storage_path、不建章节行 —— 一本没有正文的书不该看起来像解析成功了。
        """
        failure = _failure(code)
        book.status = "failed"
        job.error_code = code
        job.error_message = failure.message
        job.remedy = remedy or failure.remedy
        job.heartbeat_at = datetime.now(UTC)
        job.finished_at = datetime.now(UTC)
        await self._session.commit()


def _job_out(job: ParseJob) -> JobOut:
    return JobOut(
        id=job.id,
        book_id=job.book_id,
        stage=job.stage,
        progress_done=job.progress_done,
        progress_total=job.progress_total,
        detected_format=job.detected_format,
        error_code=job.error_code,
        # 按码从码表取，不读那一列：旧行里可能还留着引擎的原话
        error_message=_failure(job.error_code).message if job.error_code else "",
        remedy=job.remedy,
        cleanup_stats=job.cleanup_stats or {},
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


async def _follow(job_id: int, user_id: int) -> AsyncIterator[tuple[str, dict]]:
    """轮询数据库，有变化才推；结局推一次就收；到寿命还没结局就静默收尾。

    推送读的是库，不订阅进程内的状态（`backend.md` 规范）：解析跑在另一个任务里，两边的
    真相迟早分叉，分叉之后进度条和刷新之后看到的状态就对不上了。每拍开一条短 session
    新读——原先一条 session 活到底，靠每拍 `rollback()` 刷掉身份映射，漏了那一句，进度条就
    永远停在第一拍。到寿命不宣布结局：前端没收到结局就接着轮询 `GET /api/jobs/{id}`。
    """
    settings = get_settings()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + settings.job_stream_max_seconds
    last: dict | None = None
    try:
        while True:
            async with get_session_factory()() as session:
                job = await ParseRepository(session).get_job_for_user(job_id, user_id)
            if job is None:
                yield "error", {"detail": "这个解析任务不见了。"}
                return

            payload = _job_out(job).model_dump(by_alias=True, mode="json")
            if payload != last:
                yield "stage", payload
                last = payload

            if job.finished_at is not None:
                if job.error_code:
                    yield (
                        "error",
                        {
                            "detail": payload["errorMessage"],
                            "errorCode": job.error_code,
                            "remedy": job.remedy,
                            "bookId": job.book_id,
                        },
                    )
                else:
                    yield "done", payload
                return
            if loop.time() >= deadline:
                return
            await asyncio.sleep(settings.job_poll_seconds)
    except Exception:
        # 用户关了页面是取消（不在这里）；这里是推送自己出了事。不带 errorCode：
        # 那不是解析的结局，前端据此转去轮询
        logger.exception("解析进度推送中断：任务 %s", job_id)
        yield "error", {"detail": "进度推送中断了，刷新一下页面。"}


def _fit(column, value: str) -> str:
    """按列宽截断，结尾换成「…」——看得出是被截的，不会被当成原文。

    书里写的书名、作者、章节标题多长都有可能；超了列宽就在写库那一刻整本失败，为了一个
    显示不全也无妨的书名丢掉整本书。列宽从 ORM 列定义读，不另写一份数字。
    VARCHAR(n) 在 PostgreSQL 里数的是字符，不是字节，所以按字符截。
    """
    limit = column.type.length
    if not limit or len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def _staging_root() -> Path:
    """引擎写新正文的地方。和在用的书同在书库根下（同一个卷），换上去才是一次 rename。"""
    return Path(get_settings().library_root) / ".staging"


def _retired_root() -> Path:
    return Path(get_settings().library_root) / ".retired"


def _discard(staging: Path) -> None:
    """这次解析没成：暂存目录整个删掉。在用的那一版一个字都没动过。"""
    shutil.rmtree(staging, ignore_errors=True)


def _publish(staging: Path, target: Path, book_id: int) -> None:
    """把暂存目录换成这本书在用的那一版。**提交之后**才调。

    在用的目录从不被原地改写：先把它挪开，暂存目录整个换上去，再删掉挪开的那一份。于是
    重解析途中、重解析失败之后读到的都是完整的旧版；章节变少不留旧文件；书名变了，旧名字
    的目录一并收掉。换目录是一次 rename——`backend.md`「写文件要么整体成功、要么一个字
    不动」用在目录上。Windows 上 rename 的目标不能已存在，所以先挪开、后换入。
    """
    retired = _retired_root() / staging.name
    shutil.rmtree(retired, ignore_errors=True)
    retired.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.rename(retired)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging.rename(target)
    shutil.rmtree(retired, ignore_errors=True)
    # 书名变了：旧名字的目录不再有任何一行指着它
    for stale in target.parent.glob(f"{book_id}-*"):
        if stale != target:
            shutil.rmtree(stale, ignore_errors=True)


def book_dir(book: Book) -> Path | None:
    """这本书已发布那一版的目录；从没解析成功过为 None。报告页的产出清单读它。"""
    if not book.storage_path:
        return None
    return Path(get_settings().library_root) / book.storage_path


def source_path(book: Book) -> Path:
    """一本书的原件在哪。

    路径是算出来的不是存出来的：user_id 与 id 都在行上，再存一列只会多一处
    可能对不上的真相。
    """
    return (
        Path(get_settings().upload_root)
        / str(book.user_id)
        / str(book.id)
        / (book.source_file or "upload")
    )


# 在跑的后台解析。强引用在这里：事件循环只弱引用任务，没人握着的任务可能跑到一半
# 被垃圾回收掉（`turn_state._RUNNING` 同理）
_RUNNING: set[asyncio.Task] = set()


def _start(book_id: int, job_id: int) -> None:
    """排上后台解析。**同步**：从调用方提交返回到任务排上，中间没有可被取消的 await——
    客户端断开会取消请求协程，排在一个 await 后面的任务可能永远排不出去。"""
    task = asyncio.create_task(
        _parse(book_id, job_id), name=f"解析 书{book_id} 任务{job_id}"
    )
    _RUNNING.add(task)
    task.add_done_callback(_finished)


def _finished(task: asyncio.Task) -> None:
    _RUNNING.discard(task)
    if not task.cancelled() and task.exception() is not None:
        # 结局在任务里已经落库（见 `_execute`）；逃到这里的是连结局都没能落下的那一种
        logger.error("后台解析没能收尾：%s", task.get_name(), exc_info=task.exception())


async def drain() -> None:
    """等所有在跑的解析收尾。测试用——生产代码里谁也不该等它。"""
    while _RUNNING:
        await asyncio.gather(*_RUNNING, return_exceptions=True)


async def _parse(book_id: int, job_id: int) -> None:
    """后台解析一本书。自己开 session——请求那一条早已关掉。

    **不走 `Detached`**：它是 session 的**提供者**，不是一个工作单元。解析在里面按自己的
    节奏提交好几次（开始一次、结局一次），退出时再由外面补一次提交没有意义，还会把
    「这一段是一个事务」这件事说成假话。
    """
    async with get_session_factory()() as session:
        await BookLifecycle(session)._execute(book_id, job_id)


async def sweep_orphans() -> int:
    """服务启动时收尾上一条命留下的解析任务，返回收了几个。

    本轮是单实例（Grill D7），所以判据很干脆：**进程刚起来，那么任何一个还没
    收尾的任务都已经没人在跑了** —— 不必看心跳新旧。本地 `uvicorn --reload`
    存个文件就重启，这条路每天都会踩到。

    `heartbeat_at` 仍然一路维护着：多实例部署时判据要换成「心跳停滞超过 N 秒」，
    那时 Grill R7 的超时尺度才真正需要裁决。这里先把值留成配置项。

    盘上也要对账：暂存目录里，已经提交成功、还没来得及换上去的那一版照库里记的位置补完
    （roll-forward）；其余的——解析到一半的、失败了没删完的——删掉；退役目录一并清掉。
    """
    staged = _staged()
    # 失败照抛：接住它的是 `main.lifespan` 那层 try（清扫失败不拦着服务起来）。
    # 在这里吞掉的话，`return len(book_ids)` 会在什么都没提交的情况下照样报出
    # 一个数字 —— 启动日志里写着「收了 3 个」，而库里一个都没收。
    async with Detached(label="启动清扫解析任务") as session:
        stale = (
            await session.execute(
                select(ParseJob).where(ParseJob.finished_at.is_(None))
            )
        ).scalars()

        book_ids: list[int] = []
        orphaned = _FAILURES["orphaned"]
        for job in stale:
            job.error_code = "orphaned"
            job.error_message = orphaned.message
            job.remedy = orphaned.remedy
            job.finished_at = datetime.now(UTC)
            book_ids.append(job.book_id)

        if book_ids:
            await session.execute(
                update(Book)
                .where(Book.id.in_(book_ids), Book.status == "parsing")
                .values(status="failed")
            )

        committed = {}
        if staged:
            # 成功提交过的：库里已经说新版在哪了，盘上照着补
            rows = await session.execute(
                select(ParseJob.id, Book.id, Book.storage_path)
                .join(Book, Book.id == ParseJob.book_id)
                .where(
                    ParseJob.id.in_(staged),
                    ParseJob.finished_at.is_not(None),
                    ParseJob.error_code == "",
                    Book.storage_path.is_not(None),
                )
            )
            committed = {job_id: (book_id, shelf) for job_id, book_id, shelf in rows}

    # 库先提交，盘后动：盘上怎么处理，照的是库里已经落定的话
    library = Path(get_settings().library_root)
    for job_id, staging in staged.items():
        if job_id in committed:
            book_id, shelf = committed[job_id]
            _publish(staging, library / shelf, book_id)
            logger.warning(
                "上次退出时书 %s 的新版已提交、还没换上去，已补完（任务 %s）",
                book_id,
                job_id,
            )
        else:
            _discard(staging)
    shutil.rmtree(_retired_root(), ignore_errors=True)
    return len(book_ids)


def _staged() -> dict[int, Path]:
    """暂存目录里还留着的，按任务 id 排好。不是 `job-<id>` 形状的东西本来就不该在这里，删掉。"""
    root = _staging_root()
    if not root.is_dir():
        return {}
    found: dict[int, Path] = {}
    for entry in root.iterdir():
        suffix = entry.name.removeprefix("job-")
        if entry.is_dir() and suffix.isdigit():
            found[int(suffix)] = entry
        else:
            _discard(entry) if entry.is_dir() else entry.unlink(missing_ok=True)
    return found


async def _pump_progress(job_id: int, tracker: _Tracker) -> None:
    """定期把进度与心跳落库。用自己的 session，不和调用方抢。

    **泵不决定结局。** 进度与心跳只是给人看的：某一拍没落下，撤回、记一笔、下一拍再来。
    原先它一出错就带着异常死掉，停泵那一步再把异常抛回解析，一次成功的解析被记成失败。

    **不走 `Detached`**：这是个 `while True` 的长活循环，每一拍提交一次，
    生命周期由外部 cancel 决定，不是「进出各一次」。硬塞进去会让那个抽象
    同时服务两种生命周期，而它只为其中一种做对了收口。
    """
    interval = get_settings().parse_heartbeat_seconds
    async with get_session_factory()() as session:
        while True:
            await asyncio.sleep(interval)
            try:
                await session.execute(
                    update(ParseJob)
                    .where(ParseJob.id == job_id)
                    .values(
                        stage=tracker.stage,
                        progress_done=tracker.done,
                        progress_total=tracker.total,
                        heartbeat_at=func.now(),
                    )
                )
                await session.commit()
            except Exception:
                logger.warning(
                    "进度没能落库（任务 %s），下一拍再来", job_id, exc_info=True
                )
                await session.rollback()


async def _stop(pump: asyncio.Task) -> None:
    """停泵。泵自己出过什么事都不往外抛——它不决定结局，见 `_pump_progress`。"""
    pump.cancel()
    try:
        await pump
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.error("进度泵自己停下了（结局不受影响）", exc_info=True)


async def _read_chunk(source, size: int = 1024 * 1024) -> bytes:
    """UploadFile.read 是协程，普通文件对象的 read 不是。两种都收。"""
    chunk = source.read(size)
    if asyncio.iscoroutine(chunk):
        chunk = await chunk
    return chunk
