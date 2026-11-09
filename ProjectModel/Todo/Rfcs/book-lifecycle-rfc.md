---
status: implemented
---

# 一本书的生命周期（BookLifecycle）—— Architecture RFC

> 产出于 2026-09-25 的 `improve-codebase-architecture`。审视基线：前端交互走查修整、
> 两处后端缺陷修复（写库那一半的失败保护 + 迁移 `0005`；提问路不再回显服务商原话）之后的代码库，
> 全量测试 464 passed。本 RFC 只描述提案，不改代码；实施须走 RFC 快车道（显式 `rfc to plan`）。
>
> **与已实施 RFC 的关系**：`turn-state-rfc.md`（implemented）定下的锁序——**会话行在前、章节行在后**——
> 以及 `resettle.prepare()` 先清锁定、再拍引文快照的次序，本 RFC 原样继承，只把「次序只靠注释维持」
> 收成一个函数。`turn_state.forget_locks` 仍是选章锁定的唯一清除入口，不动。
>
> **方案选定过程**：四个方向（最小接口 / 最大灵活 / 为最常见调用方优化 / 端口与适配器）各出一版，
> 用户 2026-09-25 选定**混合方案**：方案三的调用面 + 方案一的暂存目录与引擎层替身 + 方案四的
> 类型化账本与黑盒锁序测试。四版在核心机制上收敛（见「Proposed Interface」末尾），分歧只在
> 「调用面的形状、正文怎么落盘、测试替身放在哪、谁来排后台任务」。

## Problem

### 一、书与解析任务的状态有五个写者，排任务靠路由记得

`books.status` / `storage_path` / 书目字段与 `parse_jobs` 的阶段、终局字段，今天由五处各自写：

| 写者 | 写什么 |
|---|---|
| `parse_service.accept()` | 建 `books`（parsing）与 `parse_jobs`，搬原件，提交 |
| `parse_service._pump_progress()`（另一条 session） | 每拍写 `stage` / `progress_*` / `heartbeat_at` |
| `parse_service._record_success()` / `_record_failure()` | 书目字段、`status`、`storage_path`、章节、存疑、任务终局 |
| `parse_service.reparse()` | `status = parsing` + 新任务（**先查再改**） |
| `parse_service.sweep_orphans()`（启动时） | 未收尾的任务 → `orphaned`，parsing 的书 → `failed` |

后台任务由路由自己排：`routers/books.py` 的上传与重解析两个端点各写一遍
`background.add_task(ParseService.run_in_background, …)`，忘了就是一本永远「解析中」的书。

### 二、「能不能读 / 能不能问 / 能不能重解析」三处各判，措辞与状态码各不相同

| 调用方 | 判据 | 结果 |
|---|---|---|
| `chat_service._open` / `_check_readable` | 非 ready 即拒 | 409，两套措辞 |
| `parse_service.reparse` | 正在解析即拒 | **400**（同一种状态，对话回 409） |
| `reader_service.chapter` / `image` | **不看 status**，只看有没有 `storage_path` 与章节行 | 重解析途中照样端出正文——而引擎正在**原地覆盖**同一个目录，索引还是旧的；重解析失败之后端出的是新旧混杂的目录 |

### 三、重解析的善后次序只靠注释维持，测试证明的是抄本

`resettle` 的模块注释写明「`prepare()` 必须在 `replace_chapters` 之前」，真正执行这个次序的只有
`_record_success` 里的三行。测试里另有三份手抄：`tests/test_reparse.py::_reparse_with`、
`tests/test_turn_state.py::_replace_chapters_elsewhere`、以及那条死锁测试里内联的三步。后果是
**锁序测试钉住的是抄本**：把生产代码里的两步对调，它照样是绿的（方案四指出）。

### 四、失败文案：引擎原话直接进了给人看的字段

- `importer.import_file` 把 `str(exc)` 包进 `ImportFailed`，`_record_failure` 原样写进
  `parse_jobs.error_message`，`useImports` 照抄显示。缺解析后端时，这句话是
  `pip install pymupdf`；MOBI 那段提示里有**服务器上的原件路径**、`pip install mobi` 与
  `booksoul parse` 命令；原件丢了则是「文件不存在：/…/uploads/…」。
- 错误码靠 `_classify()` 匹配引擎的中文措辞；其中「没有可用的章节」「损坏」两个关键词在引擎里根本不存在。
- `format_service.rejection_reason()` 写好了、有测试，却**没接进上传**：缺后端的格式照样收下、
  建行、存原件，解析失败后再给一句「点重新解析再试一次」——这条出路永远走不通。

### 五、书在磁盘上的位置散在 8 处，重解析留下陈旧文件

- `Path(library_root) / storage_path` 在 `chat_service`、`reader_service`（3 处）、`resettle`、
  `library_service` 各拼一遍；上传原件路径在 `parse_service.source_path_for`；目录名规则是
  `execute()` 里的一个 lambda。
- `writer.write_book` 从不清目录：重解析后章节变少，旧章节文件留着（报告页的产出清单就数错了）；
  书名变了，旧目录永远没人管。
- 阅读页底部那行「照着它能在磁盘上找到这个文件」的路径，在 compose 下是容器里的绝对路径
  `/data/library/…`，与 schema 注释「相对书库根」不符。

### 六、解析进度推送是写在路由里的业务逻辑，而且没有测试

`routers/jobs.py::_stream` 自己开 session、每拍 `rollback()` 刷身份映射、判定终局事件、拼
`JobOut`（`_to_out` 也在路由里）；**没有寿命上限**，一个卡住的任务会让这条流一直轮询下去。
`/api/jobs*` 与上传端点没有任何 HTTP 测试。

### 七、解析账本是一个中文键的字典，横跨四层

写：引擎 `book.stats`（目录来源、spine 文件数、剔除的目录页）、`importer._ledger()`（格式、章节、
字数、元信息命中…）、`_record_success`（引文总数、引文找回、引文位置变了、引文丢失）；读：
`library_service._ledger_rows()`（约 13 个键）与前端 `ImportRow`（章节、字数、脚注、图片）。
少一个键这一行静默消失（这是设计），所以**改一个键名不会报任何错**。「只有真搬过引文才记那一行」
这条规则写了两遍（`parse_service` 与 `library_service`）。

### 八、两个现存缺陷（本 RFC 一并修掉）

1. **进度泵出错会把一次成功的解析记成失败。** `_pump_progress` 没有 try；它若因一次写库失败
   先死了，`_stop()` 里的 `await task` 会把那个异常重新抛出——穿过 `execute()` 内层的 `finally`，
   被外层 `except Exception` 接住，记成 `internal`。
2. **并发重解析会起两个任务。** `reparse()` 先读 `status` 再改，两个几乎同时的请求都能穿过检查，
   两个工作线程同时往同一个目录里写。

> 已在本轮修掉、作为基线的两处：`_record_success` 包进失败保护（`_fail_after_rollback`：撤回、
> 重新取行、记 `internal`、固定文案），以及迁移 `0005` 把 `chapters.source_id` / `books.published` /
> `books.isbn` 放宽成 TEXT。

## Proposed Interface

### 对外：一个类、三个值类型、四个模块函数

```python
# services/book_lifecycle.py —— 取代 parse_service.py
Status = Literal["parsing", "ready", "failed"]


class Use(StrEnum):
    READ = "read"          # 阅读器：正文、书里的图
    ASK = "ask"            # 开会话、提问
    REPARSE = "reparse"    # 重新解析


@dataclass(frozen=True)
class UsableBook:
    """过了门的书：此刻允许这次用途，磁盘位置已经拼好。只读快照，不是 ORM 行。"""

    id: int
    title: str
    word_count: int
    chapter_count: int
    root: Path                                    # 已发布那一版的目录 —— 全项目唯一的拼法

    def file(self, *parts: str) -> Path | None: ...   # = core.paths.under(root, *parts)；越界为 None
    def display(self, rel: str) -> str: ...           # 相对书库根的正斜杠路径（⑤屏那一行）


@dataclass(frozen=True)
class BookState:
    """书库卡片、书籍详情、轮询读的都是它。"""

    book_id: int
    status: Status
    job_id: int | None            # 最近一次任务
    stage: str                    # read / toc / convert / clean / write
    done: int
    total: int
    error_code: str               # 最近一次停下的码；在跑或成功时为 ""
    message: str                  # 给人看的一句：只来自码表，从不来自异常原文
    remedy: str                   # 用户自己能做的下一步（OCR 命令属于这里）

    def allows(self, use: Use) -> bool: ...   # 与 require() 查同一张表


class BookLifecycle:
    """books.status / storage_path / 书目字段、chapters、parse_jobs 终局字段、parse_notes
    （`resolved` 除外）的唯一写者；书库与上传两个根目录下的路径只在这里拼。"""

    def __init__(self, session: AsyncSession) -> None: ...

    # 最常见：守门。404 = 没有 / 不是你的；409 BookNotReady(code=book_parsing | book_failed)
    async def require(self, *, book_id: int, user_id: int, use: Use) -> UsableBook: ...

    # 常见：状态。列级查询，不经身份映射；states() 一页一条查询
    async def state(self, *, book_id: int, user_id: int) -> BookState: ...
    async def states(self, *, user_id: int, book_ids: Sequence[int]) -> dict[int, BookState]: ...

    # 写入口：自己排后台解析，调用方没有第二步
    async def upload(self, *, user_id: int, filename: str, source: Readable) -> UploadAccepted: ...
    #   413 超限 / 400 空文件 / 415 UnsupportedFormat（认得扩展名、这个部署没装后端）——都在建行之前
    async def reparse(self, *, book_id: int, user_id: int) -> UploadAccepted: ...
    #   404；409 book_parsing（包括并发的第二个请求）；409 source_missing（原件不在了，状态不动）

    # 进度：轮询与推送读同一份记录
    async def job(self, *, job_id: int, user_id: int) -> JobOut: ...
    async def watch(self, *, job_id: int, user_id: int) -> AsyncIterator[tuple[str, dict]]: ...
    #   普通协程（同 ChatService.ask）：404 在 await 时抛、在响应头之前；
    #   返回的流自己开 session 轮询库，产出 ("stage" | "done" | "error", 载荷)


# 少见的路：模块级，不要请求 session
async def sweep_orphans() -> int: ...   # lifespan 调；失败照抛（lifespan 那层 try 接）
async def drain() -> None: ...          # 等所有在跑的解析收尾（测试用）
def book_dir(book: Book) -> Path | None: ...   # 报告页产出清单用；没发布过为 None
def source_path(book: Book) -> Path: ...        # 原件位置（取代 source_path_for）
```

**门表**（`require()` 与 `BookState.allows()` 读同一张；措辞只在这里写）：

| status | 有已发布的一版 | READ | ASK | REPARSE |
|---|---|---|---|---|
| parsing（首次） | 否 | 404「还在解析，正文还没写好」 | 409 `book_parsing` | 409 `book_parsing` |
| parsing（重解析中） | 是 | **读旧版** | 409 `book_parsing`「…这一问还没发出去，没花钱」 | 409 `book_parsing` |
| ready | 是 | ✓ | ✓ | ✓ |
| failed（首次） | 否 | 404「没有解析成功」 | 409 `book_failed` | ✓ |
| failed（重解析失败） | 是 | **读旧版** | 409 `book_failed` | ✓ |

「重解析途中照读旧版」之所以安全，是因为在用的目录**从不被原地改写**（见下「暂存与发布」）。

### 两个协作者（私有于本模块的调用关系，但各自可单测）

```python
# services/resettle.py —— prepare / apply 改成私有；次序在全仓库只出现这一次
@dataclass(frozen=True)
class Swapped:
    chapters: list[Chapter]          # 新行（存疑条目要挂 chapter_id）
    relocation: reloc.Report


async def swap(session: AsyncSession, *, book_id: int, records: list[dict], root: Path) -> Swapped:
    """清锁定 + 拍引文落点 → replace_chapters → 按 root 下的**新正文**重定位。
    先会话行、后章节行。不提交。"""


# services/ledger.py —— 那十几个中文键只在这里；库里的存储格式不变
@dataclass(frozen=True)
class Ledger:
    source_format: str
    chapters: int
    words: int
    images: int
    footnotes: int
    meta_hit: int
    meta_fields: int
    deduped_headings: int = 0
    engine: Mapping[str, object] = field(default_factory=dict)   # 解析器自记的几笔，原样透传
    relocation: reloc.Report | None = None                       # 只在真有引文搬家时才有

    def rows(self, *, book: Book) -> list[LedgerRow]: ...   # ②屏；公开
    def to_json(self) -> dict[str, object]: ...             # 写进 parse_jobs.cleanup_stats
    @classmethod
    def from_json(cls, raw: Mapping[str, object] | None) -> Ledger: ...   # 旧行照读
```

新增异常（`core/errors.py`）：`BookNotReady(ConflictError)`（409，带 `code`：`book_parsing` /
`book_failed` / `source_missing`）、`UnsupportedFormat(ApiError)`（415，`code = "unsupported_format"`）。

### 调用方迁移后

```python
# routers/books.py —— BackgroundTasks 参数消失
@router.post("", response_model=UploadAccepted, status_code=202)
async def upload_book(user: CurrentUser, session: SessionDep, file: UploadFile = File(...)):
    return await BookLifecycle(session).upload(
        user_id=user.id, filename=file.filename or "upload", source=file
    )

@router.post("/{book_id}/reparse", response_model=UploadAccepted, status_code=202)
async def reparse_book(book_id: int, user: CurrentUser, session: SessionDep):
    return await BookLifecycle(session).reparse(book_id=book_id, user_id=user.id)

# routers/jobs.py —— _to_out、_stream、session 工厂、ParseRepository 都离开路由；只剩 SSE 字节拼装
@router.get("/{job_id}/events")
async def job_events(job_id: int, user: CurrentUser, session: SessionDep):
    events = await BookLifecycle(session).watch(job_id=job_id, user_id=user.id)
    return StreamingResponse(_sse(events), media_type="text/event-stream", headers=_NO_BUFFER)

# services/chat_service.py —— _check_readable 删除；路径不再自己拼
book = await BookLifecycle(self._session).require(book_id=…, user_id=…, use=Use.ASK)
BookRef(id=book.id, title=book.title, storage_path=str(book.root), word_count=book.word_count, …)

# services/reader_service.py —— _STATUS_HINT 删除
book = await BookLifecycle(self._session).require(book_id=…, user_id=…, use=Use.READ)
path = book.file(here.file_path)          # None → 409「指向了书目录外面」（阅读器自己的话）
source_path = book.display(here.file_path)

# services/library_service.py
states = await BookLifecycle(self._session).states(user_id=…, book_ids=[b.id for b in rows])
ledger = Ledger.from_json(job.cleanup_stats)
ReportOut(..., ledger=ledger.rows(book=book), outputs=_outputs(book_dir(book)), ...)

# main.py lifespan —— 形状不变，只换 import
from booksoul_api.services.book_lifecycle import sweep_orphans
```

### 它在里面藏了什么（按发生顺序）

1. **收件。** 先流式落临时盘，三道检查都在任何一次写库之前：体积上限（413）、空文件（400）、
   **认得扩展名但这个部署没装后端**（415，接上闲置的 `format_service`；装包命令只进 ERROR 日志）。
   认不出的扩展名照旧交给魔数判定——前端的粗筛本来就拦着它们，后端不必更严。然后建书行拿 id、
   建任务行、把原件搬进 `upload_root/<user>/<book>/`（落盘名也走 `under()`）、提交；提交失败就删掉
   已经搬过去的原件（今天会留孤儿文件）。
2. **排任务归模块。** 提交返回后同步 `asyncio.create_task`，中间没有可被取消的 await；任务强引用在
   `_RUNNING`（同 `turn_state` 的做法），done-callback 记下逃出来的异常。路由不再碰 `BackgroundTasks`。
3. **重解析是一次比较交换。** `UPDATE books SET status='parsing' WHERE id=:b AND user_id=:u AND
   status <> 'parsing' RETURNING …`，与统计受影响会话数、建任务同一个事务。两个并发请求只有一个开跑。
4. **线程纪律。** 进线程前取出标量；工作线程只写 `_Tracker` 的三个标量、只往暂存目录里写；进度泵在
   事件循环上用自己的 session，**出错只记日志，不决定结局**（修第八节第 1 条）；先停泵、再落终局。
5. **暂存与发布。** 引擎只写进 `library_root/.staging/job-{id}/`。成功事务**提交之后**才发布：
   现有目录挪到 `.retired`，暂存目录换成 `{user}/{book}-{slug}`，再删掉退役的那份；失败只删暂存。
   于是：在用的目录从不被原地改写（重解析中、重解析失败后都读得到完整的旧版）；章节变少不留旧文件；
   书名变了旧目录一并收掉。崩在「已提交、未发布完」之间，由启动清扫按任务终态补完发布（roll-forward）。
6. **成功落库，一个事务。** 书目字段**按列宽截断**（title、author、translator、contributor、publisher、
   language、chapters.title；结尾加「…」，列宽从 ORM 列定义读，不另写一份数字）→ `resettle.swap()`
   （先会话行、后章节行；重定位读暂存目录里的新正文）→ 存疑条目 → 账本（引文那一行只在 total>0 时有）
   → 任务终局字段 → 提交。任何一步抛错整个撤回，记 `internal`（今天刚修好的逻辑搬进来）。
7. **每条终局都落在 ready 或 failed，且 `finished_at` 有值。** 引擎失败、引擎之外的意外、写库失败、
   泵出错（不改结局）、进程重启（启动清扫）。
8. **失败文案分两半。** `error_message` 只来自码表（`no_text_layer / no_content / bad_archive /
   unknown_format / unsupported_format / parse_failed / internal / orphaned`）；`remedy` 只放用户自己
   能做的事（OCR 命令带真文件名，其余给「重新解析」或「再传一次」）；引擎原话、回溯、SQL、服务器路径
   只进 ERROR 日志，带书 id 与任务 id。`_classify` 的子串匹配留在 importer 内部，由测试钉住。
9. **进度推送。** 每拍一条短 session 新读（不再靠每拍 `rollback()` 这条隐式约定）；有变化才推；
   终局映射成 `done` 或带 `errorCode / remedy / bookId` 的 `error`；超过 `job_stream_max_seconds`
   （新配置）仍无终局就静默收尾——前端 `useImports` 本来就有轮询兜底。守「SSE 轮询库，不订阅进程内状态」。
10. **状态读取。** `states()` 用一条 LATERAL 查询取齐一页书各自的最近任务，走 repository、带 user_id。
11. **启动清扫。** 单实例判据：未收尾的任务 → `orphaned`，parsing 的书 → `failed`（有已发布一版的
    也按门表照读旧版）；`.staging` 与 `.retired` 按任务终态补完或清掉。失败照抛。

### 四版收敛的核心（选型时的佐证）

四版不约而同：一个模块拥有全部写与判断；给人看的话只来自码表；重解析改比较交换；每条终局落在
ready / failed；路径只在一处拼；进度推送搬出路由并加寿命；`format_service` 接进上传。分歧与取舍：

| 分歧 | 取 | 理由 |
|---|---|---|
| 调用面形状 | 方案三：`require()` 交回带 `root` 的 `UsableBook` | 过门与拼路径合成一步，调用方写不错。方案一的 `Look` 握着请求 session、出了请求就不能用；方案四的 `require` + `shelf` 两步，漏一步不报错 |
| 正文怎么落盘 | 方案一：暂存、提交后换入 | 只有它让「重解析中照读旧版」变得安全。方案三因此只能一律不让读；方案二每次换代会改 `storage_path` 的对外含义 |
| 测试替身放哪 | 方案一：引擎层替身，不设端口 | 与 `fake_brain` 换在 `booksoul.brain.create` 同一个做法；落盘、落库、发布全走真代码。方案四的 `Importer` 端口理由成立，但今天只有一个生产实现——`UnitOfWork` 端口正是因此被撤掉的 |
| 谁排后台任务 | 方案一 / 三：模块自己排 | 堵上「路由忘了排」；有 `turn_state._RUNNING` 的先例 |
| 账本 | 方案四：类型化 `Ledger`，存储格式不变 | 类型比一个装着字符串键的模块更能挡住改名 |
| 锁序测试 | 方案四：黑盒 | 见 Testing Strategy |

## Dependency Strategy

- **引擎（In-process）**：只在后台任务的工作线程里、经 `importer.import_file` 调用。测试要控制内容、
  时机、失败方式时，在**引擎这一层**换替身：新夹具 `fake_parser`，monkeypatch
  `booksoul.parsers.detect_format` 与 `booksoul.parsers.parse`，按字典造一本引擎 `Book` 交回；可挂
  `threading.Event` 闸门、可抛指定异常。清洗、`write_book`、落库、发布全是真的。今天在服务层替掉
  `import_file` 的两种写法（`_parsed_as`、`slow_import`）退役——它们不写盘，已经和真引擎对不上。
  **不设端口**：只有一个生产实现。若将来真要把解析挪进进程池（纯 Python 解析在线程里握着 GIL），
  再把这一层替身升格为端口，调用方一行不改。
- **PostgreSQL（Local-substitutable）**：真库 `booksoul_test`。比较交换、行锁先后、`ON DELETE SET NULL`
  的时机、`ck_books_ready_has_path`、LATERAL，只有真库上才有意义。并发测试从 `get_session_factory()`
  再开第二、第三条 session。
- **书库 / 上传目录（Local-substitutable）**：conftest 已用环境变量指到临时目录。**要补一条**：每个测试
  前清空两个根目录——`RESTART IDENTITY` 让每个测试的 id 都从 1 起，今天不同测试的书会撞进同一个目录，
  只是靠原地覆盖才没出事；「发布」一旦改成换目录，这件事会当场暴露。
- **后台调度（In-process）**：模块自己的任务表，不设端口。测试 `await drain()`；新增 autouse 夹具
  `_no_parse_left_running`（与 `_no_conversation_left_occupied` 同类）。心跳、推送间隔、推送寿命都是配置项，
  测试调小，不 mock 时钟。

## Testing Strategy

### 需编写的边界测试（新文件 `tests/test_book_lifecycle.py`，除注明外用 `fake_parser`）

1. `test_上传之后不用谁再排一次解析就会跑完`：真引擎 + 样书；`drain()` 后 ready，`require(ASK)` 放行，
   `storage_path` 形如 `{uid}/{bid}-悉达多`，`UsableBook.root` 就是那个目录。
2. `test_扩展名是错的也按内容解析`：epub 改名成 .txt 上传，结果 ready、`source_format == "epub"`。
3. `test_超限空文件与部署不支持的格式在建行之前就被拒`：413 / 400 / 415；books 行数为 0，上传目录为空；
   415 的话里没有 `pip`。
4. `test_扫描件停下来并给出带真文件名的OCR命令`：真引擎 + 现造的扫描 PDF。
5. `test_失败说明只来自码表_原文进日志`：替身抛出带 `pip install`、服务器路径的异常；`error_message` /
   `remedy` 干净；caplog 里有原文与任务 id。
6. `test_写库那一半出错_书落在failed_章节撤回_还能再解析`：触发改成 `idx=0`（撞 `ck_chapters_idx_positive`）
   ——元信息截断之后，300 字的章节标题不再报错。
7. `test_进度泵出错不会把一次成功的解析记成失败`。
8. `test_解析中途的进度从另一条连接看得见`：替身停在闸门上，另一条 session 读 `state()` 看到 convert 20/64。
9. `test_同时两次重新解析只有一个开跑`：两条 session gather，一个 202、一个 409 `book_parsing`，
   `parse_jobs` 只多一行。
10. `test_原件不在时重新解析409且状态不动`。
11. `test_能不能读能不能问_一张表说了算`：参数化门表的五行 × 三种用途；`state().allows(u)` 为真当且仅当
    `require(use=u)` 不抛；别人的书 404。
12. `test_重新解析途中照读旧版_成功后换新且不留旧章节文件`：V1 → V2（节数变少、书名变了）；闸门挡着时
    阅读器逐字读到 V1；`drain()` 后读到 V2，`chapters/` 下文件数等于 V2 节数，旧目录不在。
13. `test_重新解析失败_旧版照读_提问409`。
14. `test_重新解析之后锁定清空_引文按原地不动位置变了丢了分开记账`：V1 → V2 / V3；经 `CitationService`
    看落点与 `relocated`；报告里「引文重定位」一行；首次解析的报告里没有这一行。
15. **`test_换章节之前已经握着会话行`（黑盒锁序）**：第三条 session 对一行章节加 `FOR KEY SHARE`，让
    `swap()` 卡在删章节上；此时第四条 session 对会话行 `SELECT … FOR UPDATE NOWAIT` **必须失败**。
    这才真正钉住生产代码的锁序：把 `swap()` 里两步对调，这条当场变红。
16. `test_重启清扫_孤儿收口_暂存清掉_已提交未发布的补完`。
17. `test_进度推送阶段变了才推_结局只推一次_到寿命就收`：失败时 `error` 带 `errorCode / remedy / bookId`；
    寿命到了静默收尾，不带 `errorCode`。
18. HTTP（第一批，覆盖路由）：`test_上传202_camelCase回执`、`test_任务查询与事件流都认得它`、
    `test_别人的任务_查询与事件流都是404_都在开流之前`、`test_重新解析撞上正在解析_409`。
19. （可选，架构守卫）`test_书库根与上传根只在一处拼`：扫描 `src/booksoul_api`，`library_root` /
    `upload_root` 只允许出现在 `book_lifecycle.py` 与 `core/config.py`。

`tests/test_ledger.py`：`Ledger.rows()` 的行序与「没数出来的不出行」；`from_json(to_json(x)) == x`；
今天库里旧行的 `cleanup_stats` 照读，渲染出的行与今天逐行相同（拿现有渲染结果做金标准）。

### 需删除 / 改写的旧测试

- `tests/test_parse_service.py`：并入 `test_book_lifecycle.py`。`_upload` / `service.execute(...)` 改为
  `upload()` / `reparse()` + `drain()`；`_parsed_as`、`slow_import` 删除（由 `fake_parser` 取代）；
  `test_书库卡片带上未核对的存疑条数` 与解析无关，搬到 `test_library.py`；`test_清扫失败不拦着服务起来`
  保留，只换 import。
- `tests/test_reparse.py`：删掉 `_reparse_with`（手抄的次序）与直写目录的 `_write`；引文与锁定的测试改为
  上传 V1 → 造会话与引文 → 重新解析 V2 / V3 → `drain()`；两条直调私有 `_ledger_rows` 的改成对
  `Ledger.rows()` 断言（那条 39 条引文的算术留作 `test_ledger.py` 的单元测试）。**段号期望要重算**：真
  writer 写出的章节文件在标题之后多一块元信息引用块，`paragraphs()` 会把它算作一块。
- `tests/test_turn_state.py`：`_replace_chapters_elsewhere` 改为经生命周期用同一份正文重解析一次（正文
  不变、章节 id 全新）；死锁测试里内联的三步改为一次 `resettle.swap()`，锁序本身由新测试 15 钉住。
  `_wipe_locks` 那几条保留——它们测的是 `turn_state` 对清锁的反应。
- `tests/test_import.py`：`test_解析时按五个阶段回报进度` 保留，作为 importer 唯一的内部契约测试（五个
  阶段的先后在边界上观察不到，除非赌时序——明说的例外）；其余由边界测试 2 / 4 / 5 取代。
- `tests/test_book_detail.py`、`tests/test_reader.py`、`tests/test_parse_service.py` 里的三份 `_imported`：
  合并成 conftest 的一个 `imported_book` 夹具（真引擎）。
- `tests/test_book_detail.py` 的「装得下」那条同义反复不在本 RFC 范围（见候选 B）。

### 测试环境需求

没有新依赖。conftest 新增：每个测试前清空书库根与上传根；`fake_parser` 夹具；autouse
`_no_parse_left_running`；`BOOKSOUL_JOB_POLL_SECONDS` / `BOOKSOUL_PARSE_HEARTBEAT_SECONDS` 调小。

## Implementation Recommendations

- **模块拥有**：一本书从收件到可用的全部状态迁移——`books` 的状态、`storage_path` 与书目字段，
  `chapters`，`parse_jobs` 的全部生命周期字段，`parse_notes` 的增删（`resolved` 由书库那边写）；
  书库根与上传根下的全部路径；「能不能读 / 问 / 重解析」的门表与措辞；解析账本的类型。
- **应隐藏**：线程与 session 的纪律、进度泵、比较交换、暂存与发布、重解析的善后次序、失败文案的两半、
  后台任务的排程与强引用。
- **应暴露**：`require` / `state` / `states` / `upload` / `reparse` / `job` / `watch`，外加 `sweep_orphans` /
  `drain` / `book_dir` / `source_path`；值类型 `Use` / `UsableBook` / `BookState`；异常 `BookNotReady` /
  `UnsupportedFormat`。
- **调用方怎么迁**：对话与阅读改走 `require()`，删掉各自的状态判断与路径拼接；书库列表与详情改读
  `states()` / `state()`；报告改读 `Ledger` 与 `book_dir()` / `source_path()`；两个路由去掉
  `BackgroundTasks` 与 SSE 轮询循环，只剩字节拼装；`main.lifespan` 只换 import。
- **建议的切片顺序**（每片都能独立交付、全量测试绿）：
  1. `resettle.swap()` 收掉三份手抄，补黑盒锁序测试（测试 15）。
  2. `require()` + 门表 + `BookNotReady`，接到对话与阅读（此时 READ 仍按今天的「有发布版就读」）。
  3. `upload` / `reparse` / 自排任务 / 比较交换搬进来，路由去掉 `BackgroundTasks`；泵出错隔离；码表；
     接上 `format_service`；元信息按列宽截断。
  4. `job` / `watch` 离开路由，加推送寿命；补第一批 HTTP 测试。
  5. `state` / `states`、`Ledger`、`book_dir` / `source_path` / `UsableBook.display()`（顺带修掉阅读页的容器绝对路径）。
  6. 暂存与发布、启动清扫的磁盘对账；此后「重解析中照读旧版」才真正安全。
  7. （可选）`GET /api/books/{id}/state`：书籍详情在解析中改为轮询这个轻量接口，不再每 1.5 秒拉一次整份
     详情；前端随之改 `BookDetailView` 的轮询目标。
- **实施时要当面确认的行为变化**（对外契约之外）：重新解析撞上正在解析从 400 改为 409（带 code）；原件
  不在从 400 改为 409 `source_missing`；`error_message` 不再带引擎原话，排障看日志；上传时拒收「认得但
  这个部署不支持」的格式（415）。前端只显示服务端那句话，以上都不需要改前端代码；`BookCard` 可以为
  `unsupported_format` / `internal` 配专门的说法（今天走兜底「停下了」）。
- **没有 schema 变更**。可选：部分唯一索引 `parse_jobs(book_id) WHERE finished_at IS NULL` 兜底并发；
  一条数据迁移按码重写旧行的 `error_message`（旧行里可能有路径或安装命令）。

## 不在本 RFC 范围内

本轮审视列出的其余候选，各自另起 RFC：

- **候选 B「整本装得下」两把尺子**：界面按 `books.word_count`（汉字 + 西文词），选章按
  `sum(len(content))`（全部字符）。仓库自带的《悉达多》配 deepseek-chat：48,155 字 / 58,447 字符 /
  额度 54,000——界面说整本装得下，提问却挑章节并锁定。
- **候选 C 的余下部分**：提问路的回显已在本轮修掉（`TurnRunner._failure_text`）；列模型与提问两张措辞表
  （`brain_service._LIST_FAILURES` / `turn._ASK_FAILURES`）合一、引擎给解析失败一个类似 `BrainError.kind`
  的机读类别（从而删掉 `_classify` 的子串匹配）。
- **候选 D 大脑的概念**：`BrainOut` 兼作领域对象、按名字取大脑有两套解析（网页那套丢掉配置文件里的
  `max_tokens` / `temperature`）、配置文件每个请求读一遍盘、测试读到开发者本机的配置。
- **候选 E「这一问还在答吗」对页面不可见**：刷新或断线之后只能靠「再问同一句」挂回正在说的那一段
  （本轮前端的「重问」即是如此）；`ChatView` 与 `useAsk` 之间那条缝。
- 方案二的扩展点（取消解析、批量收件、URL 收件、队列调度、多实例清扫判据）：今天没有用例在等它们。
- 对读时被内容审核拦一次记三次（`moderation_events` 每一路各记一行）。
