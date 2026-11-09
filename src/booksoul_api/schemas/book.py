"""书库与书籍的请求与响应。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from booksoul_api.schemas import ApiModel


class BookOut(ApiModel):
    """书库卡片（界面稿③屏）需要的字段。

    `status` 直接给英文枚举值，中文展示名由前端映射——枚举值入库一律英文，
    中文不进数据库（见 `database/sql.md`「约定」）。
    """

    id: int
    title: str
    author: str
    status: str
    source_format: str
    chapter_count: int
    word_count: int
    # 未核对的存疑条数。卡片上那句「2 处存疑」是「解析成功不等于解析正确」
    # 唯一的界面出口——核对过的不再计数，免得用户被同一条反复打扰。
    note_count: int
    # 失败的卡片要说清「停在哪」。只给稳定的英文码，中文短语由前端映射；
    # 完整的补救命令留给①屏（S3），卡片上放不下也不该放。
    error_code: str
    created_at: datetime


class ChapterOut(ApiModel):
    """目录树上的一节（界面稿④屏）。"""

    id: int
    idx: int
    title: str
    level: int
    word_count: int
    file_path: str
    # 字数条不只是统计，它是质检工具。这一节短到该让人看一眼吗？
    short: bool
    # 这一节被内容审核点名了（S13）。**只有嫌疑收窄到它一节时才为真**——
    # 分不出是哪一节就一节都不挂，随便挂一个等于凭空指认。
    blocked: bool = False


class NoteOut(ApiModel):
    """一条存疑或阻断（界面稿④屏的提示条）。"""

    id: int
    chapter_id: int | None
    severity: str
    code: str
    message: str
    resolved: bool
    created_at: datetime


class BrainFit(ApiModel):
    """这本书装得进这个大脑吗（界面稿④屏右栏）。

    取自⑧屏那份列表（`BrainService.list_for_user`），不另算：两页各算各的，
    迟早一页说装得下、另一页说要挑章节。字段含义与 `BrainOut` 同名字段一致——
    `fits` 为 None 表示窗口是估计的，不下结论。
    """

    name: str
    model: str
    context_chars: int
    context_source: str
    fits: bool | None


class ModerationOut(ApiModel):
    """这本书被内容审核拦过（界面稿④屏那条重墨提示）。

    没被拦过时整个字段是 null，不是一个「0 次」的空对象 —— **成功不给颜色**，
    没被拦是默认状态，界面上不该有东西替它占位置。
    """

    times: int
    brain_name: str
    at: datetime
    # 每次被拦时都在场的那几节。只剩一节时 `pinned` 为真，那一节才挂重墨。
    suspect_chapter_ids: list[int]
    suspect_titles: list[str]
    pinned: bool
    # 一句能照着做的话：定位到了就点名，没定位到就说怎么缩小范围
    advice: str


class BookDetail(ApiModel):
    """书籍详情（界面稿④屏）。

    元信息一律原样给，空就是空 —— 前端显示「未标注」而不是替来源猜。
    `translator` 空而 `contributor` 非空是一种有意义的状态：来源提了这个人，
    但没说他是译者，于是我们也不说。
    """

    id: int
    title: str
    author: str
    translator: str
    contributor: str
    publisher: str
    published: str
    isbn: str
    language: str
    description: str
    source_file: str
    source_format: str
    storage_path: str | None
    chapter_count: int
    word_count: int
    status: str
    created_at: datetime
    # 有几个会话跟这本书聊过。④屏那个「重新解析」按钮按它决定要不要先问一句
    # ——重解析会清掉所有会话的选章锁定，那几个会话的下一轮要按全价重算。
    conversation_count: int = 0
    # 被内容审核拦过的账。没被拦过就是 null。
    moderation: ModerationOut | None = None

    chapters: list[ChapterOut]
    notes: list[NoteOut]
    brains: list[BrainFit]


class BookStateOut(ApiModel):
    """一本书此刻的状态，连同最近一次解析任务（`GET /api/books/{id}/state`）。

    书籍详情在解析中每一拍只问这一句，好了才去拉整份详情。`message` 只来自码表，
    `remedy` 是用户自己能做的下一步；在跑或成功时两者都是空串。
    """

    book_id: int
    status: str
    published: bool  # 有没有上一次成功的那一版（重解析中照读它）
    job_id: int | None
    stage: str
    done: int
    total: int
    error_code: str
    message: str
    remedy: str


class NotePatch(ApiModel):
    resolved: bool = True


class LedgerRow(ApiModel):
    """解析账本里的一行（界面稿②屏）。"""

    label: str
    detail: str


class OutputFile(ApiModel):
    path: str
    size_bytes: int
    is_dir: bool
    child_count: int = 0


class ReportOut(ApiModel):
    """解析报告（界面稿②屏）：一本可以逐条核对的账。"""

    book_id: int
    title: str
    source_file: str
    source_size_bytes: int
    elapsed_seconds: float | None
    ledger: list[LedgerRow]
    # 原始账本数据。界面若要自己组织，不必回头去解析上面那些中文句子。
    stats: dict[str, Any]
    outputs: list[OutputFile]
    storage_path: str | None
    notes: list[NoteOut]


class ChapterContent(ApiModel):
    """一节正文（界面稿⑤屏）。

    `markdown` 是**磁盘上那个文件的原文**，一个字没动。这一屏的全部承诺就是
    「你看到的就是你能拿走的」——中间做任何「整理」，这句话就不成立了。

    位置三件套（`idx` / `chapter_count` / `preceding_words`）合起来回答⑤屏左下
    那条位置条：这一节在全书的哪里。`preceding_words` 是前面各节字数的和，
    不是按节数估的比例——估出来的数字看着一样真，但它是假的。
    """

    book_id: int
    book_title: str
    idx: int
    title: str
    level: int
    word_count: int
    # 相对书目录，如 chapters/003-婆罗门之子.md
    file_path: str
    # 从 library/ 起算的完整相对路径，界面上直接显示给人照着找
    source_path: str
    markdown: str

    chapter_count: int
    preceding_words: int
    prev_idx: int | None
    next_idx: int | None
