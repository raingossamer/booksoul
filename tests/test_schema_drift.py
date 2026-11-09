"""ORM 定义与迁移建出的真实 schema 对账。

为什么需要这条：`0001_init` 用原生 SQL 执行 `Design/database/sql.md` 的 DDL——
那份 DDL 是被逐条评审过的，连 COMMENT ON COLUMN 都写了理由，翻译成 SQLAlchemy ops
会丢掉一部分。代价是 ORM 成了第二份定义。两份定义并存，漂移不是会不会的问题，
是什么时候的问题。

这条测试让漂移当场变红，而不是等到某天一个字段查出来是 None。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def test_orm模型与迁移建出的表不漂移(migrated_db):
    """只查 ORM ⊆ DB 这个方向。

    DB 里有 ORM 尚未声明的表是正常的——11 张表一次建齐，ORM 侧按切片推进逐个补。
    反过来不行：ORM 声明了 DB 没有的表或列，就是写代码的人以为存在而实际不存在。
    索引与约束也不比对：迁移里的索引是手写的，ORM 侧没有逐个声明，
    差异全是噪音；列级漂移才是会伤人的那种。
    """
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url

    import booksoul_api.models  # noqa: F401 —— import 一下就把所有实体挂上 Base.metadata
    from booksoul_api.db.base import Base

    sync_url = make_url(os.environ["BOOKSOUL_DATABASE_URL"]).set(
        drivername="postgresql+psycopg2"
    )
    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    finally:
        engine.dispose()

    # 只留「ORM 有、DB 没有」与「两边都有但不一致」这两类
    kept = [d for d in diff if _op_name(d) not in _DB_ONLY_IS_FINE]

    assert kept == [], "ORM 与数据库漂了：\n" + "\n".join(repr(d) for d in kept)


_DB_ONLY_IS_FINE = frozenset(
    {
        "remove_table",  # 后续切片才声明的表
        "remove_index",  # 迁移里手写的索引，ORM 侧不重复声明
        "remove_constraint",
        "remove_table_comment",
        "remove_column_comment",
        # COMMENT ON 只活在迁移里，ORM 不重复声明一遍 comment=——
        # 同一段话写两处，迟早只改一处。
        "modify_comment",
        "modify_table_comment",
    }
)


def _op_name(diff_item) -> str:
    """compare_metadata 的条目形如 ('add_column', schema, table, col) 或一串 tuple。"""
    if isinstance(diff_item, list):
        return _op_name(diff_item[0]) if diff_item else ""
    if isinstance(diff_item, tuple) and diff_item:
        return str(diff_item[0])
    return getattr(diff_item, "__visit_name__", "")


def test_每个实体都由models包导出():
    """`models/__init__.py` 漏 import 一个实体，上面那条对账就悄悄漏掉一张表。

    漏掉**不会红**——`compare_metadata` 把「DB 有、ORM 没有」归进 remove_table，
    而那一类是被刻意放行的（见上）。于是新增的表既不在 `Base.metadata` 里，
    也不在漂移检测的覆盖范围内：两个缺口互相遮掩，谁都不报警。

    这里不数张数（下一张表一加就要改一次数字），也不查 `Base.metadata`
    本身——别的测试直接 import 过 `models.moderation`，在全量跑里它早就挂上去了，
    查它等于查测试的执行顺序。查的是**包有没有导出这个名字**：
    `models/__init__.py` 里那句 import 才是「import 一下就全挂上」的唯一依据。
    """
    import importlib
    import pkgutil

    import booksoul_api.models as models_pkg
    from booksoul_api.db.base import Base

    missing = []
    for mod in pkgutil.iter_modules(models_pkg.__path__):
        module = importlib.import_module(f"{models_pkg.__name__}.{mod.name}")
        for name, obj in vars(module).items():
            if not (
                isinstance(obj, type) and issubclass(obj, Base) and obj is not Base
            ):
                continue
            if not getattr(obj, "__tablename__", None):
                continue
            if getattr(models_pkg, name, None) is not obj:
                missing.append(f"{obj.__tablename__}（{mod.name}.{name}）")

    assert sorted(set(missing)) == [], (
        "这些实体没被 models 包导出，import 包时挂不上 Base.metadata："
        + "，".join(sorted(set(missing)))
    )


def test_本功能的迁移可降可升(migrated_db):
    """降到本功能之前（`0002`），再升回最新，两步都不报错，列也回得来。

    降级路径平时没人走，于是它坏了也没人知道——直到真要回滚的那一天，
    而那一天恰好是最不该再多出一个意外的时候。
    """
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect
    from sqlalchemy.engine import make_url

    root = Path(__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))

    def schema() -> tuple[set[str], set[str]]:
        """（conversations 的列, 所有表名）"""
        url = make_url(migrated_db).set(drivername="postgresql+psycopg2")
        engine = create_engine(url)
        try:
            seen = inspect(engine)
            columns = {c["name"] for c in seen.get_columns("conversations")}
            return columns, set(seen.get_table_names())
        finally:
            engine.dispose()

    try:
        command.downgrade(cfg, "0002_moderation_events")
        columns, tables = schema()
        assert "context_chars" not in columns
        assert "brain_choices" not in tables
    finally:
        # 不论断言成败都升回去：停在旧 schema 上，后面每一条测试都会莫名其妙地红
        command.upgrade(cfg, "head")
    columns, tables = schema()
    assert "context_chars" in columns
    assert "brain_choices" in tables


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
