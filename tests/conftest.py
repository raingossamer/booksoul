"""测试夹具。

两件事：
1. 让测试不依赖 editable 安装的状态。CLAUDE.md 记着一个反复踩的坑——
   `pip install -e .` 记的是绝对路径，项目目录改名或搬家后 `booksoul` 就 import
   不到了。测试没理由跟着一起坏，这里直接把 src/ 顶到 sys.path 最前面。
2. 给需要数据库的测试准备一个**独立的**测试库，并用 alembic 把 schema 迁上去。
   走真迁移而不是 metadata.create_all，是因为「迁移能不能建出这套表」本身
   就是一条验收标准，顺手验了比另写一条便宜。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 测试库的默认地址。注意这里是**赋值不是 setdefault**：若沿用外部已设的
# BOOKSOUL_DATABASE_URL，一次 pytest 就会把开发库整张表 TRUNCATE 掉。
# 要换测试库请设 BOOKSOUL_TEST_DATABASE_URL，它不会被误当成开发库。
_DEFAULT_TEST_DB = "postgresql+asyncpg://booksoul:booksoul@127.0.0.1:5432/booksoul_test"
os.environ["BOOKSOUL_DATABASE_URL"] = os.environ.get(
    "BOOKSOUL_TEST_DATABASE_URL", _DEFAULT_TEST_DB
)

# 解析测试真的会写盘，而且每个测试开始前会把这两个目录**清空**（见 `_fresh_engine`）。
# 所以这里也是**赋值不是 setdefault**：外面设过 BOOKSOUL_LIBRARY_ROOT 的话（比如指着
# 你的真书库），沿用它等于让一次 pytest 把真书库清掉。要指定位置请设 BOOKSOUL_TEST_*。
os.environ["BOOKSOUL_LIBRARY_ROOT"] = os.environ.get(
    "BOOKSOUL_TEST_LIBRARY_ROOT"
) or tempfile.mkdtemp(prefix="booksoul-test-library-")
os.environ["BOOKSOUL_UPLOAD_ROOT"] = os.environ.get(
    "BOOKSOUL_TEST_UPLOAD_ROOT"
) or tempfile.mkdtemp(prefix="booksoul-test-uploads-")
# 进度泵的节拍调小：等「中途的进度落库了」时不必干等半秒
os.environ["BOOKSOUL_PARSE_HEARTBEAT_SECONDS"] = "0.05"

# 真书。需要它的用例在它不在时跳过（它不在版本控制里）
SAMPLE_BOOK = _PROJECT_ROOT / "悉达多.epub"


def _admin_dsn(url: str) -> tuple[str, str]:
    """把 SQLAlchemy 连接串拆成「连 postgres 库的 libpq DSN」与「目标库名」。"""
    from sqlalchemy.engine import make_url

    parsed = make_url(url)
    dbname = parsed.database or "booksoul_test"
    admin = parsed.set(database="postgres").set(drivername="postgresql+psycopg2")
    return admin.render_as_string(hide_password=False), dbname


@pytest.fixture(scope="session")
def migrated_db() -> str:
    """建出（或复用）测试库并迁到最新 schema。数据库没跑就跳过，而不是一堆红。"""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    url = os.environ["BOOKSOUL_DATABASE_URL"]
    admin_url, dbname = _admin_dsn(url)

    try:
        engine = create_engine(
            admin_url.replace("+psycopg2", ""), isolation_level="AUTOCOMMIT"
        )
        with engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": dbname}
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{dbname}"'))
        engine.dispose()
    except Exception as exc:  # 连不上就跳过，并把怎么把它跑起来说清楚
        pytest.skip(
            f"连不上 PostgreSQL（{exc.__class__.__name__}）。"
            "起一个：docker compose up -d db（它对本机开 127.0.0.1:5432，"
            "密码与这里的默认连接串一致；测试用的是其中的 booksoul_test 库，"
            "碰不到你的书）"
        )

    cfg = Config(str(_PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_PROJECT_ROOT / "migrations"))
    command.upgrade(cfg, "head")
    return url


def _empty(root: str) -> None:
    """清空一个目录里的东西，目录本身留着。"""
    for entry in Path(root).iterdir():
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()


async def _fresh_engine():
    """丢掉旧引擎，把所有表与书库、上传两个目录清空，让每个测试从同一个起点开始。

    目录也要清：`RESTART IDENTITY` 让每个测试的 id 都从 1 起，不清的话不同测试的书
    会写进同一个 `1/1-…` 目录，彼此看得见对方留下的文件。
    """
    from sqlalchemy import text

    from booksoul_api.core.config import get_settings
    from booksoul_api.db.base import Base
    from booksoul_api.db.session import get_engine, reset_engine

    await reset_engine()
    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    if tables:
        async with get_engine().begin() as conn:
            await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    settings = get_settings()
    _empty(settings.library_root)
    _empty(settings.upload_root)


@pytest.fixture
async def client(migrated_db):
    """一个连着测试库的 HTTP 客户端。每个测试开始前把所有表清空。"""
    from httpx import ASGITransport, AsyncClient

    from booksoul_api.db.session import reset_engine
    from booksoul_api.main import app

    await _fresh_engine()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://booksoul.test") as c:
        yield c

    await reset_engine()


@pytest.fixture
async def db_session(migrated_db):
    """一个连着测试库的 session，给不经 HTTP 的 service 层测试用。"""
    from booksoul_api.db.session import get_session_factory, reset_engine

    await _fresh_engine()
    async with get_session_factory()() as session:
        yield session

    await reset_engine()


@pytest.fixture
def fake_brain(monkeypatch):
    """把引擎的 `brain.create` 换成替身。

    换在引擎那一层而不是服务层：服务层要的正是「和 CLI 走同一条路」，
    在服务层另开一个测试专用分支，等于测了一条生产上不存在的代码路径。
    """
    from fakes import FakeBrain

    from booksoul import brain as brain_mod

    FakeBrain.calls = []
    FakeBrain.script = ["他拒绝的不是佛陀，", "而是把佛陀当作外在权威这条路径。"]
    FakeBrain.raise_after = None
    monkeypatch.setattr(brain_mod, "create", FakeBrain)
    return FakeBrain


@pytest.fixture
def fake_parser(monkeypatch):
    """把引擎的按魔数判格式与解析换成替身：交出一本照字典造的书。

    同样换在引擎那一层（`booksoul.parsers`）：清洗、写盘、落库、换章节全走真代码。
    在服务层替掉 `import_file` 的话，这一路上最容易出事的那几步——写盘、按新正文
    重定位引文——就都没测到。
    """
    from fakes import FakeParser

    from booksoul import parsers

    fake = FakeParser()
    monkeypatch.setattr(parsers, "detect_format", fake.detect_format)
    monkeypatch.setattr(parsers, "parse", fake.parse)
    return fake


@pytest.fixture
async def imported_book(db_session):
    """真引擎 + 样书，经上传与后台解析落进书库。返回 (读者, 书)。样书不在就跳过。"""
    from booksoul_api.db.session import get_session_factory
    from booksoul_api.models.book import Book
    from booksoul_api.models.user import User
    from booksoul_api.services.book_lifecycle import BookLifecycle, drain

    if not SAMPLE_BOOK.is_file():
        pytest.skip("样书不在，跳过需要真书的用例")

    reader = User(email="reader@example.com", password_hash="不参与本测试")
    db_session.add(reader)
    await db_session.commit()  # 上传走它自己的 session，看不见没提交的行
    async with get_session_factory()() as request:
        with open(SAMPLE_BOOK, "rb") as handle:
            accepted = await BookLifecycle(request).upload(
                user_id=reader.id, filename="悉达多.epub", source=handle
            )
    await drain()
    return reader, await db_session.get(Book, accepted.book_id)


@pytest.fixture
def prompts_sandbox(tmp_path, monkeypatch):
    """把 prompts/ 指到一份临时拷贝。

    本片新增的是一个**会往 prompts/ 写字**的端点，而那三个 md 是这个项目
    最值钱的东西——CLAUDE.md 第一条就写着「要调对话质量就改那三个 md」。
    测试若指着真目录跑，一次 pytest 就能把反复打磨过的提示词换成
    「改过的」，而这个目录不在版本控制里，没有第二份。
    """
    import shutil

    from booksoul import persona as persona_mod

    sandbox = tmp_path / "prompts"
    shutil.copytree(Path(persona_mod.prompt_dir()), sandbox)
    monkeypatch.setenv("BOOKSOUL_PROMPTS", str(sandbox))
    return sandbox


@pytest.fixture(autouse=True)
def _no_conversation_left_occupied():
    """每个测试结束时，没有哪个会话还占着席位。

    席位登记是进程内的模块级状态（`services/turn_state`）。一条测试挂着一轮没收尾
    就结束，下一条测试碰上同一个会话 id（每条测试都从 1 开始发号）就是一个莫名其妙
    的 409——症状出现在另一条测试里。这是测试环境的不变量，不是行为断言。
    """
    yield
    from booksoul_api.services import turn_state

    left = sorted(turn_state._SEATS)
    turn_state._SEATS.clear()
    assert not left, f"测试结束时这些会话还占着席位：{left}"


@pytest.fixture(autouse=True)
def _no_parse_left_running():
    """每个测试结束时，没有哪本书还在后台解析。

    上传与重新解析会自己排一个后台任务（`book_lifecycle._RUNNING`）。测试结束了它还在跑，
    下一个测试清空表、换掉引擎之后它才落库——报错出现在另一条测试里，而且时有时无。
    排了解析的测试要在结束前 `await drain()`。
    """
    yield
    from booksoul_api.services import book_lifecycle

    left = sorted(task.get_name() for task in book_lifecycle._RUNNING)
    book_lifecycle._RUNNING.clear()
    assert not left, f"测试结束时这些解析还在跑：{left}"
