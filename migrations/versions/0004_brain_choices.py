"""新增 brain_choices：用户为内置预设选的模型与窗口（brain-model-picker）

Revision ID: 0004_brain_choices
Revises: 0003_conversation_context_chars

九个内置大脑的模型写死在引擎的预设里。用户在⑧屏填好某家的 key 之后，要能换用
这家的其他模型——这张表存的就是「这个人给这个预设选了哪个模型」。

解析 = **预设 + 选择**：provider / base_url / key 名永远从代码来，这张表**只覆盖
模型与窗口**，所以连放地址的列都没有。供应商换地址时，不会有人手里攥着一份过期的抄本。

设计上的几个决定：

* **与预设按名字弱关联，不做外键。** 预设不在库里（它的真相在 `booksoul.config`），
  没有东西可以指。预设被改名或删掉之后，对不上的行在解析时忽略，不报错、不清理——
  改回原名，选择也就回来了。
* **窗口存 token，并存它的来路**（空串 / vendor / manual）。不存来路，下次打开⑧屏
  就说不出这个数字是供应商报的还是用户填的。窗口为 0 时来路必须为空串：
  那时的来路（内置表 / 估计）是解析时推出来的，不落库。
* **来路用 VARCHAR + CHECK 而不是 ENUM**：沿用 `moderation_events.outcome` 的先例——
  PostgreSQL 加枚举值不能在事务里回滚，改 CHECK 可以。
* **不存任何 key。** 这张表里没有能放 key 的列。
* 已开的会话不读这张表：它们开的时候就把模型与正文额度快照进了 `conversations`。
"""

from __future__ import annotations

from alembic import op

revision = "0004_brain_choices"
down_revision = "0003_conversation_context_chars"
branch_labels = None
depends_on = None


_UPGRADE = """
CREATE TABLE brain_choices (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id        bigint      NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    brain_name     varchar(32) NOT NULL,
    model          varchar(64) NOT NULL,
    context_tokens integer     NOT NULL DEFAULT 0,
    context_source varchar(8)  NOT NULL DEFAULT '',
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_brain_choices_user_brain UNIQUE (user_id, brain_name),
    CONSTRAINT ck_brain_choices_model CHECK (btrim(model) <> ''),
    CONSTRAINT ck_brain_choices_tokens CHECK (context_tokens BETWEEN 0 AND 10000000),
    CONSTRAINT ck_brain_choices_source CHECK (context_source IN ('', 'vendor', 'manual')),
    CONSTRAINT ck_brain_choices_source_needs_window
        CHECK (context_tokens > 0 OR context_source = '')
);

-- UNIQUE (user_id, brain_name) 的首列就是外键列：那条唯一索引同时满足「外键列要有索引」

CREATE TRIGGER trg_brain_choices_touch BEFORE UPDATE ON brain_choices
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

COMMENT ON TABLE  brain_choices                IS '用户为内置预设选的模型与窗口。解析 = 预设（provider / 地址 / key 名，永远从代码来）+ 这里的选择（只覆盖模型与窗口）。【不存 API key】';
COMMENT ON COLUMN brain_choices.brain_name     IS '内置预设的名字。与预设按名字弱关联、不做外键——预设不在库里。对不上任何预设的行在解析时忽略';
COMMENT ON COLUMN brain_choices.context_tokens IS '上下文窗口（token）。0 = 没人给过，解析时按内置表或保守估计；折算成正文字数由 booksoul.ask.budget_from_context_tokens 唯一负责';
COMMENT ON COLUMN brain_choices.context_source IS '窗口的来路：vendor = 列模型时供应商报的；manual = 用户照着文档填的；空串 = 窗口为 0（此时来路由解析推出，不落库）';
"""

_DOWNGRADE = """
DROP TABLE IF EXISTS brain_choices;
"""


def upgrade() -> None:
    op.get_bind().exec_driver_sql(_UPGRADE)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(_DOWNGRADE)
