"""内容审核拦截：认出来，给出路，并且尽量指出是哪一节（S13）。

严肃文学里的暴力、性、宗教、沉沦描写，在部分国内平台的内容审核里会被整段拒绝。
《悉达多》的「轮回」那一节就是实测中反复被拦的那一节。

这件事和别的失败**长得很像**：都是一个 4xx。但用户要做的事完全不同——
认证失败去填 key，限流等一会儿，模型不存在改名字，而内容审核只能换个大脑或者
调小载入量。把它们混成一句「请求失败」，用户会先去把 key 翻出来检查三遍。

三件事：

* **认出来。** 引擎那一侧已经在 `_is_moderation` 里判得出，但它把结论揉进了
  一句中文里就扔了。本片让 `BrainError` 带上 `kind`，上层才拿得到机读的结论。
* **出路要分场合。** 引擎给的出路是 CLI 的（`--brain` / `--budget`），
  在浏览器里照抄出去就是一句无法执行的建议。网页那一侧要自己写。
* **尽量指出是哪一节。** 供应商不会说是哪一段触发的。但每次被拦时载入了哪几节
  是我们自己的账——拦多了取交集，范围会自己收窄，一分钱不用多花。
"""

from __future__ import annotations

import json
import urllib.error
from pathlib import Path

from fakes import FAKE_KEY, FakeBrain

from booksoul.brain import (
    BrainError,
    _detail_of,
    _http_error_message,
    _kind_of_http,
)
from booksoul.config import BrainConfig
from booksoul.model import count_words
from booksoul_api.core.config import get_settings
from booksoul_api.models.book import Book
from booksoul_api.models.chapter import Chapter
from booksoul_api.models.user import User
from booksoul_api.services.chat_service import ChatService

# ------------------------------------------------------------------ 引擎侧：认出来

CFG = BrainConfig(
    name="qwen",
    provider="openai",
    model="qwen-max",
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    api_key_env="DASHSCOPE_API_KEY",
)


def _http(code: int, payload: dict) -> urllib.error.HTTPError:
    import io

    body = json.dumps(payload, ensure_ascii=False).encode()
    return urllib.error.HTTPError(CFG.base_url, code, "err", {}, io.BytesIO(body))


def test_通义那种审核拒绝认得出来():
    """实测里 DashScope 回的就是这个形状。"""
    exc = _http(
        400,
        {
            "error": {
                "code": "data_inspection_failed",
                "message": "Input data may contain inappropriate content.",
            }
        },
    )
    assert _kind_of_http(exc.code, "Input data may contain inappropriate content.") == (
        "moderation"
    )


def test_中文的审核提示也认得出来():
    assert _kind_of_http(400, "输入包含敏感信息，已被拦截") == "moderation"
    assert _kind_of_http(403, "内容审核不通过") == "moderation"


def test_认证失败不是审核():
    """混成一类，用户会被指去换大脑，而他真正要做的是填 key。"""
    assert _kind_of_http(401, "Incorrect API key provided") == "auth"
    assert _kind_of_http(403, "Permission denied") == "auth"


def test_限流与模型不存在各归各的():
    assert _kind_of_http(429, "Rate limit reached") == "rate_limit"
    assert _kind_of_http(404, "model not found") == "not_found"
    assert _kind_of_http(500, "internal error") == "server"


def test_普通的400不冒充审核():
    """参数写错也是 400。认成审核会把用户支去换大脑，而那救不了他。"""
    assert _kind_of_http(400, "Invalid value for 'temperature'") == ""


def test_审核的报错里仍然带着命令行的出路():
    """CLI 那条路不能因为网页要另一套出路就退化。"""
    exc = _http(400, {"error": {"message": "inappropriate content"}})
    message = _http_error_message(CFG, exc.code, _detail_of(exc))

    assert "内容审核" in message
    assert "--brain" in message, "命令行用户要的是一条能敲的命令"
    assert "--budget" in message


def test_BrainError带着kind():
    err = BrainError("被拦了", kind="moderation")
    assert err.kind == "moderation"
    # 不给就是空串，老代码原样可用
    assert BrainError("随便").kind == ""


# ------------------------------------------------------------------ 网页侧

CHAPTERS = {
    "婆罗门之子": "悉达多，俊美的婆罗门之子，与他的好友乔文达一道长大。",
    "沙门": "他学会了静默，学会了斋戒，学会了思考。",
    "轮回": "他沉入轮回，酒色财气，昏沉地活了许多年。",
}


async def _reader_with_book(db_session) -> tuple[User, Book, list[Chapter]]:
    user = User(email="reader@example.com", password_hash="不参与本测试")
    db_session.add(user)
    await db_session.flush()

    storage = f"{user.id}/moderation-fixture-悉达多"
    directory = Path(get_settings().library_root) / storage
    (directory / "chapters").mkdir(parents=True, exist_ok=True)

    records = []
    for n, (title, body) in enumerate(CHAPTERS.items(), 1):
        record = {
            "序号": n,
            "标题": title,
            "文件": f"chapters/{n:03d}-{title}.md",
            "层级": 1,
            "字数": count_words(body),
        }
        (directory / record["文件"]).write_text(
            f"# {title}\n\n{body}", encoding="utf-8"
        )
        records.append(record)

    (directory / "meta.json").write_text(
        json.dumps({"书籍": {"书名": "悉达多"}, "章节": records}, ensure_ascii=False),
        encoding="utf-8",
    )
    book = Book(
        user_id=user.id,
        title="悉达多",
        source_file="悉达多.epub",
        source_format="epub",
        status="ready",
        storage_path=storage,
        chapter_count=len(records),
        word_count=sum(r["字数"] for r in records),
    )
    db_session.add(book)
    await db_session.flush()
    rows = [
        Chapter(
            book_id=book.id,
            idx=r["序号"],
            title=r["标题"],
            level=1,
            word_count=r["字数"],
            file_path=r["文件"],
        )
        for r in records
    ]
    db_session.add_all(rows)
    await db_session.commit()
    return user, book, rows


def _blocked(
    monkeypatch, message: str = "Input data may contain inappropriate content."
):
    def chat(self, system, messages, *, on_text=None):
        FakeBrain.calls.append({"system": system, "messages": messages})
        raise BrainError(f"qwen 请求失败 HTTP 400：{message}", kind="moderation")

    monkeypatch.setattr(FakeBrain, "chat", chat)


# 替身原本那个 chat。`_blocked` 会把它换掉，有的测试要换回来——
# 不能用 monkeypatch.undo()，那会把 `fake_brain` 夹具那一层也一起撤掉。
_NORMAL_CHAT = FakeBrain.chat


def _normal(monkeypatch):
    monkeypatch.setattr(FakeBrain, "chat", _NORMAL_CHAT)


def _rate_limited(monkeypatch):
    def chat(self, system, messages, *, on_text=None):
        raise BrainError("触发限流，请 20 秒后重试", kind="rate_limit")

    monkeypatch.setattr(FakeBrain, "chat", chat)


async def _ask(service, conversation, user, question="他为什么沉沦？", **kw):
    stream = await service.ask(
        conversation_id=conversation.id,
        user_id=user.id,
        question=question,
        brain_key=FAKE_KEY,
        **kw,
    )
    return [event async for event in stream]


async def _open(db_session, user, book, budget: int = 0):
    service = ChatService(db_session)
    conversation = await service.start(
        user_id=user.id,
        book_id=book.id,
        persona="scholar",
        brain_name="qwen",
        budget_chars=budget,
    )
    return service, conversation


async def test_被审核拦下时给的是网页版出路不是命令行(
    db_session, fake_brain, monkeypatch
):
    """引擎那句话里有 `booksoul ask --brain …`。在浏览器里照抄就是一句空话。"""
    user, book, _rows = await _reader_with_book(db_session)
    _blocked(monkeypatch)
    service, conversation = await _open(db_session, user, book)

    events = await _ask(service, conversation, user)
    failed = [p for n, p in ((n, p) for n, p in events) if n == "failed"]
    assert failed, "审核拦截要落在这一路的 failed 上"
    message = failed[0]["message"]

    assert "内容审核" in message
    assert "--brain" not in message and "--budget" not in message
    assert "大脑" in message, "要说得出「换一个大脑」这条出路"


async def test_审核拦截会被记下来并带上这一轮载入的章节(
    db_session, fake_brain, monkeypatch
):
    from booksoul_api.services.moderation import ModerationService

    user, book, rows = await _reader_with_book(db_session)
    _blocked(monkeypatch)
    service, conversation = await _open(db_session, user, book)
    await _ask(service, conversation, user)

    await db_session.commit()
    out = await ModerationService(db_session).for_book(book_id=book.id, user_id=user.id)
    assert out is not None
    assert out.times == 1
    assert out.brain_name == "qwen"
    # 整本装得下，三节全都送过去了，所以三节都在嫌疑里
    assert set(out.suspect_chapter_ids) == {r.id for r in rows}


async def test_限流不算审核拦截(db_session, fake_brain, monkeypatch):
    """两件事要分开。混起来的话，一次限流会给这本书挂上一条永久的审核警告。"""
    from booksoul_api.services.moderation import ModerationService

    user, book, _rows = await _reader_with_book(db_session)
    _rate_limited(monkeypatch)
    service, conversation = await _open(db_session, user, book)
    await _ask(service, conversation, user)

    await db_session.commit()
    assert (
        await ModerationService(db_session).for_book(book_id=book.id, user_id=user.id)
    ) is None


async def test_拦两次取交集范围自己收窄(db_session, fake_brain, monkeypatch):
    """供应商不说是哪一段触发的，但每次载入了哪几节是我们自己的账。

    第一次整本送过去被拦（三节全在嫌疑里），第二次调小预算只送两节又被拦——
    交集只剩那两节。**一分钱都不用多花**，范围自己收窄了。
    """
    from booksoul_api.services.moderation import ModerationService

    user, book, rows = await _reader_with_book(db_session)
    _blocked(monkeypatch)

    service, first = await _open(db_session, user, book)
    await _ask(service, first, user)

    # 预算只够两节，选章会挑走相关度最高的那两节
    service, second = await _open(db_session, user, book, budget=60)
    await _ask(service, second, user, "他沉沦的那一段说了什么？")

    await db_session.commit()
    out = await ModerationService(db_session).for_book(book_id=book.id, user_id=user.id)
    assert out.times == 2
    assert len(out.suspect_chapter_ids) < len(rows), "交集应该比第一次窄"
    assert out.suspect_chapter_ids


async def test_嫌疑缩到一节时点名而且那一节在目录树上挂重墨(
    db_session, fake_brain, monkeypatch
):
    from booksoul_api.repositories.moderation_repo import ModerationRepository
    from booksoul_api.services.library_service import LibraryService
    from booksoul_api.services.moderation import ModerationService

    user, book, rows = await _reader_with_book(db_session)
    轮回 = next(r for r in rows if r.title == "轮回")

    # 直接造两条证据：第一次三节全在，第二次只剩「轮回」
    repo = ModerationRepository(db_session)
    await repo.record(
        book_id=book.id,
        brain_name="qwen",
        chapter_ids=[r.id for r in rows],
        detail="inappropriate content",
    )
    await repo.record(
        book_id=book.id,
        brain_name="qwen",
        chapter_ids=[轮回.id],
        detail="inappropriate content",
    )
    await db_session.commit()

    out = await ModerationService(db_session).for_book(book_id=book.id, user_id=user.id)
    assert out.suspect_chapter_ids == [轮回.id]
    assert "轮回" in out.advice, "定位到了就点名"

    detail = await LibraryService(db_session).book_detail(
        book_id=book.id, user_id=user.id
    )
    blocked = [c for c in detail.chapters if c.blocked]
    assert [c.title for c in blocked] == ["轮回"]
    assert detail.moderation is not None
    assert detail.moderation.times == 2


async def test_只被拦过一次时不点名而是教怎么缩小范围(
    db_session, fake_brain, monkeypatch
):
    """三节里有一节触发了审核，但分不出是哪一节。

    随便挑一节挂上重墨，等于凭空指认——用户会照着去删一节没问题的正文。
    这时该说的是「怎么把范围缩小」。
    """
    from booksoul_api.repositories.moderation_repo import ModerationRepository
    from booksoul_api.services.library_service import LibraryService
    from booksoul_api.services.moderation import ModerationService

    user, book, rows = await _reader_with_book(db_session)
    await ModerationRepository(db_session).record(
        book_id=book.id,
        brain_name="qwen",
        chapter_ids=[r.id for r in rows],
        detail="inappropriate content",
    )
    await db_session.commit()

    out = await ModerationService(db_session).for_book(book_id=book.id, user_id=user.id)
    assert len(out.suspect_chapter_ids) == 3
    assert "3" in out.advice
    assert "预算" in out.advice, "要告诉用户怎么缩小范围"

    detail = await LibraryService(db_session).book_detail(
        book_id=book.id, user_id=user.id
    )
    assert not any(c.blocked for c in detail.chapters), "分不出是哪一节就一节都不挂"


async def test_没被拦过的书这一项是空的(db_session):
    from booksoul_api.services.library_service import LibraryService

    user, book, _rows = await _reader_with_book(db_session)
    detail = await LibraryService(db_session).book_detail(
        book_id=book.id, user_id=user.id
    )

    assert detail.moderation is None
    assert not any(c.blocked for c in detail.chapters)


async def test_别人的书拦截记录看不到(db_session):
    from booksoul_api.services.moderation import ModerationService

    _user, book, rows = await _reader_with_book(db_session)
    from booksoul_api.repositories.moderation_repo import ModerationRepository

    await ModerationRepository(db_session).record(
        book_id=book.id,
        brain_name="qwen",
        chapter_ids=[r.id for r in rows],
        detail="x",
    )
    other = User(email="other@example.com", password_hash="x")
    db_session.add(other)
    await db_session.commit()

    assert (
        await ModerationService(db_session).for_book(book_id=book.id, user_id=other.id)
    ) is None


# ------------------------------------------------------------------ 另一半证据：通过


async def test_平安送过去的那几节从嫌疑里剔掉(db_session):
    """光靠交集排除不掉几个字的小节。

    《悉达多》的「扉页」254 字、「第一部」5 字，任何预算都装得下，于是每一次
    选章它们都在场——交集永远排除不掉它们，范围会卡住再也窄不下去。
    而一次成功的请求正好证明那几节是干净的。
    """
    from booksoul_api.repositories.moderation_repo import ModerationRepository
    from booksoul_api.services.moderation import ModerationService

    user, book, rows = await _reader_with_book(db_session)
    扉页, 沙门, 轮回 = rows
    repo = ModerationRepository(db_session)

    # 两次被拦，交集卡在「扉页 + 轮回」——扉页只是因为它小，每次都装得下
    await repo.record(
        book_id=book.id,
        brain_name="qwen",
        chapter_ids=[扉页.id, 沙门.id, 轮回.id],
        detail="x",
    )
    await repo.record(
        book_id=book.id, brain_name="qwen", chapter_ids=[扉页.id, 轮回.id], detail="x"
    )
    await db_session.commit()

    out = await ModerationService(db_session).for_book(book_id=book.id, user_id=user.id)
    assert set(out.suspect_chapter_ids) == {扉页.id, 轮回.id}
    assert out.pinned is False

    # 后来有一轮平安送过去了，里面有扉页 —— 它洗清了
    await repo.record(
        book_id=book.id,
        brain_name="qwen",
        chapter_ids=[扉页.id, 沙门.id],
        outcome="passed",
    )
    await db_session.commit()

    out = await ModerationService(db_session).for_book(book_id=book.id, user_id=user.id)
    assert out.suspect_chapter_ids == [轮回.id]
    assert out.pinned is True
    assert "轮回" in out.advice
    assert out.times == 2, "通过不算进「拦了几次」"


async def test_别的大脑那边成功不算数(db_session):
    """同一本书在通义被拦、在 Claude 不被拦是常态。

    一次 Claude 的成功证明不了通义那边什么，拿它去洗清一节就是在编。
    """
    from booksoul_api.repositories.moderation_repo import ModerationRepository
    from booksoul_api.services.moderation import ModerationService

    user, book, rows = await _reader_with_book(db_session)
    repo = ModerationRepository(db_session)
    await repo.record(
        book_id=book.id,
        brain_name="qwen",
        chapter_ids=[r.id for r in rows],
        detail="x",
    )
    await repo.record(
        book_id=book.id,
        brain_name="claude",
        chapter_ids=[r.id for r in rows],
        outcome="passed",
    )
    await db_session.commit()

    out = await ModerationService(db_session).for_book(book_id=book.id, user_id=user.id)
    assert len(out.suspect_chapter_ids) == len(rows), "别家的成功不该洗清任何一节"


async def test_没被拦过时成功的轮次什么都不记(db_session, fake_brain):
    """否则这张表会变成一份对话流水。"""
    from sqlalchemy import func, select

    from booksoul_api.models.moderation import ModerationEvent

    user, book, _rows = await _reader_with_book(db_session)
    service, conversation = await _open(db_session, user, book)
    await _ask(service, conversation, user)
    await db_session.commit()

    count = await db_session.scalar(select(func.count()).select_from(ModerationEvent))
    assert count == 0


async def test_被拦过之后成功的轮次才开始记(db_session, fake_brain, monkeypatch):
    from sqlalchemy import select

    from booksoul_api.models.moderation import ModerationEvent

    user, book, _rows = await _reader_with_book(db_session)

    _blocked(monkeypatch)
    service, first = await _open(db_session, user, book)
    await _ask(service, first, user)

    # 换回正常的假模型：这一轮说得完
    _normal(monkeypatch)
    service, second = await _open(db_session, user, book)
    await _ask(service, second, user, "那他后来怎么样了？")
    await db_session.commit()

    rows = list(
        (
            await db_session.execute(
                select(ModerationEvent).order_by(ModerationEvent.id)
            )
        ).scalars()
    )
    assert [r.outcome for r in rows] == ["blocked", "passed"]


async def test_台账写不进去也不耽误那一路的错误消息送达(
    db_session, fake_brain, monkeypatch
):
    """台账记不上是一件遗憾，把一条已经组好的错误消息丢掉不是。

    这一条今天靠两个**逐字相同**的 except 块守着；收进 `Detached` 之后靠
    `on_error="log"` 守着。换的是实现，这条断言一个字都不用改——它也正是
    这次迁移的回归网。
    """
    from booksoul_api.repositories.moderation_repo import ModerationRepository

    user, book, _rows = await _reader_with_book(db_session)
    _blocked(monkeypatch)

    async def 写不进去(self, **kw):
        raise RuntimeError("台账表写不进去")

    monkeypatch.setattr(ModerationRepository, "record", 写不进去)

    service, conversation = await _open(db_session, user, book)
    events = await _ask(service, conversation, user)

    failed = [p for n, p in events if n == "failed"]
    assert failed, "台账写失败不该把这一路的 failed 事件也弄丢"
    assert "内容审核" in failed[0]["message"]
    assert "大脑" in failed[0]["message"], "网页版的那条出路照常给"


async def test_台账写不进去也不耽误一条已经说完的回答(
    db_session, fake_brain, monkeypatch
):
    """`_record_passed` 跑在回答说完**之后**，而且在 `_one` 的 try 之外。

    它要是往外抛，异常会一路逃到任务边界：回答已经说完、钱已经花掉，
    却因为一笔记不上的账而在日志里变成一条没人处理的异常。
    """
    from booksoul_api.repositories.moderation_repo import ModerationRepository

    user, book, _rows = await _reader_with_book(db_session)

    _blocked(monkeypatch)
    service, first = await _open(db_session, user, book)
    await _ask(service, first, user)

    _normal(monkeypatch)

    async def 写不进去(self, **kw):
        raise RuntimeError("台账表写不进去")

    monkeypatch.setattr(ModerationRepository, "record", 写不进去)

    service, second = await _open(db_session, user, book)
    events = await _ask(service, second, user, "那他后来怎么样了？")

    answered = [p for n, p in events if n == "answered"]
    assert answered, "回答已经说完了，台账写不进去不该把它变成一次失败"
    assert not [p for n, p in events if n == "failed"]
