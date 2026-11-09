"""内容审核拦截：认出来、给出路、尽量指出是哪一节（S13）。

## 为什么它值得单独一块

严肃文学里的暴力、性、宗教、沉沦描写，在部分国内平台的内容审核里会被整段拒绝。
这是读文学作品时的**常见障碍**，不是罕见故障——《悉达多》实测里反复被拦的是
「轮回」那一节。

它和别的失败长得几乎一样（都是一个 4xx），可用户要做的事完全不同：认证失败去
填 key，限流等一会儿，模型不存在改名字，而审核只能换个大脑或者调小载入量。
混成一句「请求失败」，用户会先把 key 翻出来检查三遍。

## 怎么定位到是哪一节

供应商**不说**是哪一段触发的。硬要定位只有一条路：把章节拆开一节一节地试——
那是一串真金白银的调用，而这个项目从头到尾的立场是「用户花的钱不能白花」。

所以走另一条路。「这一次送了哪几节过去、结果是拦还是通」是我们自己的账：

    嫌疑 = 每次被拦时都在场的那几节（交集） − 曾经平安送过去的那几节（并集）

**两边都需要。** 只有交集不够：实测里《悉达多》的「扉页」254 字、「第一部」
5 字，任何预算都装得下，于是每一次选章它们都在场——交集永远排除不掉它们，
范围卡在 3 节再也窄不下去。而一次成功的请求正好证明那几节是干净的。

两种证据都来自用户本来就要发的那些请求，**一分钱不用多花**。

按大脑分开算：同一本书在通义被拦、在 Claude 不被拦是常态，一次 Claude 的成功
证明不了通义那边什么。

收窄到一节就点名，④屏目录树上那一行挂重墨。**没收窄到就一节都不点名**，
转而告诉用户下一步怎么缩小范围——随便挂一节重墨等于凭空指认，用户会照着去删
一节没问题的正文。

"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from booksoul_api.models.chapter import Chapter
from booksoul_api.repositories.moderation_repo import ModerationRepository


@dataclass(frozen=True)
class Moderation:
    """这本书被审核拦过的账，以及一句能照着做的话。"""

    times: int
    brain_name: str
    at: datetime
    # 嫌疑章节：每一次被拦时都在场的那几节。只剩一节时才算定位到了。
    suspect_chapter_ids: list[int]
    suspect_titles: list[str]
    # 定位到了就点名；没定位到就说怎么缩小范围。两种情况都要能照着做。
    advice: str

    @property
    def pinned(self) -> bool:
        """定位到具体哪一节了吗。只有它为真时，目录树上才挂重墨。"""
        return len(self.suspect_chapter_ids) == 1


class ModerationService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._events = ModerationRepository(session)

    async def for_book(self, *, book_id: int, user_id: int) -> Moderation | None:
        """这本书被拦过没有。没被拦过返回 None —— 不是一个「0 次」的空对象。

        **成功不给颜色**（tokens.css）。没被拦过是默认状态，界面上不该有任何
        「本书未被拦截 ✓」之类的东西占位置。
        """
        events = await self._events.for_book(book_id, user_id)
        blocked = [e for e in events if e.outcome == "blocked"]
        if not blocked:
            return None

        # 只算最近一次拦下这本书的那个大脑。换个大脑就是另一回事了——
        # 一次 Claude 的成功证明不了通义那边什么。
        brain = blocked[0].brain_name
        mine = [e for e in blocked if e.brain_name == brain]

        # 交集：每次被拦时都在场的那几节。只会越取越小，所以收窄是单调的。
        suspects: set[int] = set()
        for n, event in enumerate(mine):
            ids = {int(i) for i in (event.chapter_ids or [])}
            suspects = ids if n == 0 else (suspects & ids)

        # 再减掉曾经平安送过去的那些。没有这一步，几个字的小节会永远赖在
        # 嫌疑里——它们任何预算都装得下，每次选章都在场。
        for event in events:
            if event.outcome == "passed" and event.brain_name == brain:
                suspects -= {int(i) for i in (event.chapter_ids or [])}

        # 重解析之后旧 id 不存在了，对不上的自然落选——降级是对的，
        # 总比按一个死 id 去点名一节不存在的章节好。
        rows = (
            await self._session.execute(
                select(Chapter)
                .where(Chapter.book_id == book_id, Chapter.id.in_(suspects or {0}))
                .order_by(Chapter.idx)
            )
        ).scalars()
        alive = [(row.id, row.title) for row in rows]

        newest = mine[0]
        return Moderation(
            times=len(mine),
            brain_name=newest.brain_name,
            at=newest.created_at,
            suspect_chapter_ids=[cid for cid, _ in alive],
            suspect_titles=[title for _, title in alive],
            advice=_advice(newest.brain_name, [t for _, t in alive]),
        )


def _advice(brain_name: str, titles: list[str]) -> str:
    """一句能照着做的话。

    分两种：定位到了就点名那一节；没定位到就告诉用户**下一步怎么缩小范围**。
    第二种同样是可执行的动作，不是一句「暂时无法确定」。
    """
    head = f"{brain_name} 的内容审核拦下过这本书。"
    if len(titles) == 1:
        return (
            f"{head}每次被拦时都在场的只有《{titles[0]}》这一节 —— "
            "多半是它触发的。换一个大脑（Claude / DeepSeek / Kimi / 本机 Ollama）"
            "最省事；也可以调小载入预算，让检索避开这一节。"
        )
    if titles:
        return (
            f"{head}这 {len(titles)} 节每次都在场，还分不出是哪一节触发的。"
            "换一个大脑最省事；想定位的话，把载入预算调小一点再问一次 —— "
            "每被拦一次，这个范围就会窄一些。"
        )
    # 交集空了：几次拦截载入的章节没有交集。可能是重解析换掉了 id，
    # 也可能真的不止一节会触发。这时**不要编**，照实说。
    return (
        f"{head}几次拦截载入的章节没有共同的那一节 —— "
        "可能不止一处触发，也可能这本书重新解析过。换一个大脑最省事。"
    )
