"""阅读器：把一节正文从磁盘端出来（界面稿⑤屏）。

这一层只做三件事，但每一件都有一个「绝不能这么干」贴在旁边：

1. **原样读出来**。不 strip、不规整、不补标题——⑤屏底下那行源文件路径是一句
   承诺：屏幕上这些字和你照着路径拿到的文件是同一份东西。中间动一下，
   那行路径就变成了一句好听的假话。
2. **文件不在就说不在**。索引说有、磁盘说没有，这时候返回空字符串是最省事也
   最有害的做法：用户会读成「这一章本来就没内容」，然后带着这个错判去重新解析
   一本其实解析得好好的书。CLAUDE.md 那条「不要假装成功」管的就是这种时刻。
3. **不拿数据库里的路径直接拼文件名**。`chapters.file_path` 平时是引擎写的、
   老实的相对路径；但它终究是一条**存在库里**的字符串，把它无条件拼进
   `open()` 等于把「读服务器上任意文件」的开关交给了数据库的写入方。
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from booksoul_api.core.errors import ConflictError, NotFoundError
from booksoul_api.core.paths import under
from booksoul_api.repositories.book_repo import BookRepository
from booksoul_api.schemas.book import ChapterContent
from booksoul_api.services.book_lifecycle import BookLifecycle, Use


class ReaderService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._books = BookRepository(session)

    async def chapter(self, *, book_id: int, idx: int, user_id: int) -> ChapterContent:
        # 读不读得到归门表：没有这本书 / 不是你的 / 还没有一版可读，一律 404。
        # 重解析中、重解析失败之后照读上一次成功的那一版
        book = await BookLifecycle(self._session).require(
            book_id=book_id, user_id=user_id, use=Use.READ
        )

        chapters = await self._books.list_chapters(book.id)
        here = next((c for c in chapters if c.idx == idx), None)
        if here is None:
            raise NotFoundError(
                f"《{book.title}》没有第 {idx} 节。它一共 {len(chapters)} 节。"
            )

        path = book.file(here.file_path)
        if path is None:
            # 不回显那条路径：它是索引里的内容，原样抄回去没有好处
            raise ConflictError(
                f"《{book.title}》第 {here.idx} 节的文件路径指向了书目录外面，"
                "已拒绝读取。这条索引坏了，请重新解析这本书。"
            )
        try:
            markdown = path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            # 索引说有、磁盘说没有 —— 两份记录对不上，是 409 不是 404：
            # 404 会被读成「这一节本来就不存在」，而它明明在目录里
            raise ConflictError(
                f"这一节的正文文件不在了：{here.file_path}。"
                "多半是文件被移动或删掉了 —— 在书籍详情页点「重新解析」可以照着"
                "原始文件重写一遍。"
            ) from exc
        except OSError as exc:
            raise ConflictError(
                f"读不出这一节的正文（{here.file_path}）：{exc.strerror or exc}。"
                "确认一下书库目录还在、还读得动，然后重新解析这本书。"
            ) from exc

        order = [c.idx for c in chapters]
        at = order.index(idx)
        return ChapterContent(
            book_id=book.id,
            book_title=book.title,
            idx=here.idx,
            title=here.title,
            level=here.level,
            word_count=here.word_count,
            file_path=here.file_path,
            source_path=book.display(here.file_path),
            markdown=markdown,
            chapter_count=len(chapters),
            # 真的把前面各节加起来。按节数估比例看着一样真，但它是假的
            preceding_words=sum(c.word_count for c in chapters[:at]),
            prev_idx=order[at - 1] if at > 0 else None,
            next_idx=order[at + 1] if at + 1 < len(order) else None,
        )

    async def image(self, *, book_id: int, name: str, user_id: int) -> tuple[Path, str]:
        """章节里引的一张图：`![](images/<name>)`。返回 (磁盘路径, 媒体类型)。

        **只端 images/ 下面那一层的位图。** 名字是前端从正文里抠出来的，正文又是
        从用户传来的书里转出来的——两层都不可信，所以名字里不许有目录，扩展名走
        白名单。SVG 不在里面：它能带脚本，前端把图取成 `blob:` 地址，那个地址继承
        本站的源；右键「在新标签页打开」，脚本就在本站跑，localStorage 里的 key
        近在咫尺。一张图显示不出来，远比这个便宜。

        不合规、不存在、不是你的书，一律 404，不分说是哪一种。
        """
        book = await BookLifecycle(self._session).require(
            book_id=book_id, user_id=user_id, use=Use.READ
        )

        media_type = _IMAGE_TYPES.get(Path(name).suffix.lower())
        # 以 images/ 为界，不是以书目录为界：书目录里别处的文件（正文、meta.json）
        # 也不许借一个指过去的符号链接端出来
        target = (
            under(book.root / "images", name)
            if media_type and "/" not in name and "\\" not in name
            else None
        )
        if target is None or not target.is_file():
            raise NotFoundError("这本书里没有这张图。")
        return target, media_type


# 位图才端。SVG 故意不在这里，见 `ReaderService.image`
_IMAGE_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".avif": "image/avif",
}
