"""Alembic 环境。连接串从 Settings 读，不在 alembic.ini 里复制一份。

用同步驱动（psycopg2）而非 asyncpg：迁移是一次性串行操作，异步在这里没有任何
好处，只会多一层 run_sync 包装。应用侧仍然是异步的，两者各取所需。

另：`alembic.ini` 必须保持纯 ASCII。configparser 按系统区域编码读它，中文 Windows
上是 GBK，UTF-8 的中文注释会直接 UnicodeDecodeError。所以说明写在这里而不是那里。
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from booksoul_api.core.config import get_settings
from booksoul_api.db.base import Base
from booksoul_api.models import *  # noqa: F401,F403  —— 让 Base.metadata 装齐所有表

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().sync_database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
