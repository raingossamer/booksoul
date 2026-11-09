"""SQLAlchemy 2.0 声明式基类。

所有 ORM 实体都挂在这一个 `Base.metadata` 上，Alembic 据此比对 schema 漂移。
注意 schema 的**权威来源是迁移脚本**（它按 `Design/database/sql.md` 的 DDL 原样执行，
连 COMMENT ON 都保住了）；ORM 这边是应用读写用的映射。两处并存必然会漂，
所以另有一条测试拿 information_schema 跟 Base.metadata 对账。
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
