"""脱离请求的工作单元。

`backend.md`「并发与后台任务」写着一条：**活得比调用方长的活儿，自己开一条
session**。流式回答的落库可能发生在客户端早就断开之后，而请求那条 session
随响应结束就关了。

问题是「自己开一条」今天被抄了八遍，每一遍都顺带自己决定了另外三件事：什么时候
提交、异常吞还是抛、日志怎么写。其中两处的 except 块逐字相同——抄错的那一份不会
报错，只会安静地少记一笔账。这个模块把那四件事收成一个名字。
"""

from __future__ import annotations

import logging
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from booksoul_api.db.session import get_session_factory

logger = logging.getLogger(__name__)


class Detached:
    """一条不跟着请求走的工作单元。

    进出各一次；正常退出提交，异常退出回滚。异常之后怎么办由 `on_error` 说了算，
    不再由每个调用点各写一遍 try：

    - `"raise"`（默认）——失败必须让调用方知道。选章留档属于这一类：写不进去
      而瞒着调用方，下一轮会照着一份不存在的锁定继续走。
    - `"log"` ——失败只记一条带 `label` 的日志。审核台账属于这一类：台账记不上
      是一件遗憾，把一条**已经说完的回答**变成错误不是。

    `label` 只进日志，不进任何响应体——它是这条错误唯一的可追性来源。
    """

    def __init__(
        self,
        *,
        on_error: Literal["raise", "log"] = "raise",
        label: str = "",
    ) -> None:
        self._on_error = on_error
        self._label = label
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> AsyncSession:
        # 重入当场抛错。静默共用的话，里层退出时那次 commit 会把外层写到一半的
        # 东西一并提交掉，而且一个字都不报——事务边界糊过一次就不再可读了。
        if self._session is not None:
            raise RuntimeError(
                f"这条工作单元已经开着了，不可重入：{self._label or '（未署名）'}。"
                "需要第二条就再建一个 Detached。"
            )
        self._session = get_session_factory()()
        return self._session

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        session, self._session = self._session, None
        if session is None:  # pragma: no cover —— 没进过 __aenter__ 就退出
            return False
        # 失败最常发生在**提交那一刻**：唯一约束、外键、连接断掉都在那里报，
        # 而不是在 with 体里。只把策略套在 with 体上，这条策略就恰好在最常见的
        # 那种失败上不生效——而它不生效的时候看起来和生效一模一样。
        failure: BaseException | None = exc
        try:
            if exc_type is None:
                await session.commit()
            else:
                await session.rollback()
        except Exception as at_commit:
            failure = at_commit
        finally:
            await session.close()  # close() 会把没收口的事务回滚掉

        if failure is None:
            return False
        if self._on_error == "log":
            logger.exception(
                "后台写入失败：%s", self._label or "（未署名）", exc_info=failure
            )
            return True
        if failure is not exc:
            raise failure  # with 体没抛，是提交炸的——它得自己走出去
        return False  # with 体抛的那个，原样往上走
