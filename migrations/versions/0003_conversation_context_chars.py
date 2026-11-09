"""conversations 加 context_chars：开会话时快照下来的正文额度（brain-model-picker）

Revision ID: 0003_conversation_context_chars
Revises: 0002_moderation_events

会话的模型早就快照了（`conversations.model`），正文额度却没有：提问时按模型名
现查引擎的内置表，认不出的一律按 10 万字。于是自定义端点填的窗口，⑧屏用了、
提问时却被无视——⑧屏说「整本装得下」，真提问时在挑章节；反过来小窗口的模型
被塞一个超长请求，供应商直接拒掉。

快照的是**字**而不是窗口（token）：提问时的预算与「装得下吗」用的都是字数，
而内置表命中时手里只有字数，从字倒推 token 是有损的。

* **0 = 这一列加上之前就开着的会话**，提问时走老路径（按模型名查表），行为逐字不变。
  不回填：回填要替每一个旧会话猜「它开的时候窗口是多少」，而那个数当时根本没算过。
* PostgreSQL 11+ 加一个带常量默认值的 NOT NULL 列是纯元数据操作，不重写表、不长时间锁表。
"""

from __future__ import annotations

from alembic import op

revision = "0003_conversation_context_chars"
down_revision = "0002_moderation_events"
branch_labels = None
depends_on = None


_UPGRADE = """
ALTER TABLE conversations
    ADD COLUMN context_chars integer NOT NULL DEFAULT 0;

COMMENT ON COLUMN conversations.context_chars IS '开会话时快照的正文额度（字），与⑧屏当时显示的是同一个数。提问预算的优先级：budget_chars（用户手调）> context_chars > 按 model 查引擎内置表。0 = 这一列加上之前开的会话，走最后那条老路径';
"""

_DOWNGRADE = """
ALTER TABLE conversations DROP COLUMN IF EXISTS context_chars;
"""


def upgrade() -> None:
    op.get_bind().exec_driver_sql(_UPGRADE)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(_DOWNGRADE)
