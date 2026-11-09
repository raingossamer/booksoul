"""`Detached` —— 一条不跟着请求走的工作单元。

为什么它值得一个名字：流式回答的落库发生在客户端可能早就断开之后，请求那条
session 随响应结束就关了。于是后台的每一处写入都各自开一条 session、各自决定
什么时候提交、失败了是吞还是抛。四件事散在八个地方，其中两处的 except 块
**逐字相同**——抄错的那一份不会报错。

这些测试打在 `Detached` 的边界上：不需要 HTTP，不需要后台任务，只问它
「提交了没有、失败之后怎么办」。
"""

from __future__ import annotations

import logging

import pytest
from sqlalchemy import select

from booksoul_api.models import User


async def test_正常退出把写入提交掉(db_session):
    """在 `Detached` 里写的东西，另一条 session 读得到——它自己提交了。"""
    from booksoul_api.db.unit_of_work import Detached

    async with Detached(label="留档") as session:
        session.add(User(email="ok@example.com", password_hash="不参与本测试"))

    found = await db_session.scalar(select(User).where(User.email == "ok@example.com"))
    assert found is not None


async def test_异常退出回滚且把异常抛给调用方(db_session):
    """默认策略是「抛」：库里不留半截，调用方也不会以为它成功了。

    选章留档属于这一类——写不进去就是写不进去，下一轮会照着一份不存在的锁定
    继续走，瞒着调用方比报错危险。
    """
    from booksoul_api.db.unit_of_work import Detached

    with pytest.raises(RuntimeError, match="半路崩了"):
        async with Detached(label="留档") as session:
            session.add(User(email="half@example.com", password_hash="不参与本测试"))
            await session.flush()
            raise RuntimeError("半路崩了")

    found = await db_session.scalar(
        select(User).where(User.email == "half@example.com")
    )
    assert found is None, "异常退出必须回滚，库里不能留半截"


async def test_on_error_log吞掉异常只留一条带label的日志(db_session, caplog):
    """另一种策略是「只记日志」：调用方感知不到失败。

    审核台账属于这一类——台账记不上是一件遗憾，把一条**已经说完的回答**变成
    错误不是。`label` 是这条日志唯一的可追性来源，所以它必须出现在日志里。
    """
    from booksoul_api.db.unit_of_work import Detached

    with caplog.at_level(logging.ERROR):
        async with Detached(on_error="log", label="审核台账 会话7") as session:
            session.add(User(email="ledger@example.com", password_hash="不参与本测试"))
            await session.flush()
            raise RuntimeError("台账写炸了")

    assert "审核台账 会话7" in caplog.text, "label 要进日志，否则这条错误无从追查"
    assert "台账写炸了" in caplog.text, "原始异常要留在日志里，别把它吃干净"
    found = await db_session.scalar(
        select(User).where(User.email == "ledger@example.com")
    )
    assert found is None, "吞异常不等于放过那半截写入，照样要回滚"


async def test_同一个实例不可重入(db_session):
    """重入当场抛错，不静默共用同一条 session。

    共用的话，里层退出时那次 commit 会把外层写到一半的东西一起提交掉——
    而且一个字都不报。工作单元的边界只要糊过一次，事务边界就不再是可读的了。
    """
    from booksoul_api.db.unit_of_work import Detached

    unit = Detached(label="留档")
    async with unit as outer:
        with pytest.raises(RuntimeError, match="不可重入"):
            async with unit:
                pass
        # 外层那条 session 没被里层动过，照常用得下去
        outer.add(User(email="reentry@example.com", password_hash="不参与本测试"))

    found = await db_session.scalar(
        select(User).where(User.email == "reentry@example.com")
    )
    assert found is not None


async def test_on_error_log也吃得下提交那一刻才炸的失败(db_session, caplog):
    """失败最可能发生在 commit 那一刻。

    唯一约束、外键、连接断掉——这些都不在 with 体里报，它们在提交时才报。
    只把策略套在 with 体上而漏掉提交，等于这条策略在**最常见的那种失败**上
    根本不生效，而它不生效的时候看起来和生效一模一样。
    """
    from booksoul_api.db.unit_of_work import Detached

    db_session.add(User(email="dup@example.com", password_hash="不参与本测试"))
    await db_session.commit()

    with caplog.at_level(logging.ERROR):
        async with Detached(on_error="log", label="审核台账 会话9") as session:
            session.add(User(email="dup@example.com", password_hash="不参与本测试"))

    assert "审核台账 会话9" in caplog.text, "提交时炸的失败也要按 on_error 处理"
