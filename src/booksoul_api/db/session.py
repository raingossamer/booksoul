"""异步引擎与会话工厂。

事务边界在 services 层（一个用例一个事务），repositories 不自行 commit——
这条在 `backend.md`「注释与事务」里。`get_session` 只负责把 session 交出去
并在请求结束时关掉，不替上层决定什么时候提交。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from booksoul_api.core.config import get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(), expire_on_commit=False, autoflush=False
        )
    return _session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：每个请求一个 session。"""
    async with get_session_factory()() as session:
        yield session


async def reset_engine() -> None:
    """丢掉当前引擎。测试里切库时用——生产路径不该调它。"""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine, _session_factory = None, None
