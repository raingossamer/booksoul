"""SQLAlchemy ORM 实体。

这里是应用读写数据库用的映射；schema 的**权威来源是迁移脚本**
（`migrations/versions/0001_init.py`，按 `Design/database/sql.md` 的 DDL 原样执行）。
两份定义并存必然会漂，所以 `tests/test_schema_drift.py` 拿 information_schema
跟 `Base.metadata` 对账——漂了就红，而不是等到线上某个字段查不出来。

字段命名一律 snake_case，与引擎和数据库同形，不做转换；转 camelCase 只发生在
Pydantic 序列化那一处（见 `backend.md`「跨层命名对照」）。
"""

from __future__ import annotations

from booksoul_api.models.book import Book
from booksoul_api.models.brain import Brain
from booksoul_api.models.brain_choice import BrainChoice
from booksoul_api.models.chapter import Chapter
from booksoul_api.models.citation import Citation
from booksoul_api.models.conversation import Conversation
from booksoul_api.models.message import Message
from booksoul_api.models.moderation import ModerationEvent
from booksoul_api.models.parse import ParseJob, ParseNote
from booksoul_api.models.retrieval import Retrieval, RetrievalItem
from booksoul_api.models.user import User

__all__ = [
    "Book",
    "Brain",
    "BrainChoice",
    "Chapter",
    "Citation",
    "Conversation",
    "Message",
    "ModerationEvent",
    "ParseJob",
    "ParseNote",
    "Retrieval",
    "RetrievalItem",
    "User",
]
