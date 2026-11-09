"""新增 moderation_events：内容审核拦截的证据台账（S13）

Revision ID: 0002_moderation_events
Revises: 0001_init

严肃文学里的暴力、性、宗教、沉沦描写，在部分国内平台的内容审核里会被整段拒绝。
供应商**不会说是哪一段触发的**，只回一个 400。

但「这一次我们送了哪几节过去、结果是拦还是通」是我们自己的账。记下来之后：

    嫌疑 = 每次被拦时都在场的那几节（交集）− 曾经平安送过去的那几节（并集）

两边都需要。**只有交集不够**：实测发现《悉达多》的「扉页」「第一部」「第二部」
各只有几个字，任何预算都装得下，于是每一次选章它们都在场——交集永远排除不掉
它们，范围卡在 3 节再也窄不下去。而一次成功的请求正好证明那几节是干净的。

一分钱都不用多花：两种证据都来自用户本来就要发的那些请求。

设计上的三个决定：

* **成功只在「这本书被这个大脑拦过」之后才记。** 成功的轮次每天几十上百，
  全记下来这张表就成了一份对话流水。而在第一次被拦之前，那些记录一个字都用不上。
  代价是成功那条路上多一次 EXISTS 查询，而它走的是 `(book_id, ...)` 那个索引。
* **通过与拦截同表。** 它们说的是同一件事的两面：「送了这几节，结果如何」。
  拆两张表，取交集那段代码就要同时认两种形状。
* **append-only。** 不更新、不删除。每一行都是一个发生过的事实，
  而「拦了几次」本身就是界面要显示的东西。
* **chapter_ids 存 jsonb 而不是关联表。** 它是一份**快照**：重解析之后那些 id
  就不存在了，而这条证据说的是「当时送出去的是那几节」。做成外键关联表，
  重解析会把证据连带删掉（或者置空成一行看不懂的记录）。
  界面那一侧照样按 id 去对当下的章节，对不上的自然就不显示——降级是对的。
"""

from __future__ import annotations

from alembic import op

revision = "0002_moderation_events"
down_revision = "0001_init"
branch_labels = None
depends_on = None


_UPGRADE = """
CREATE TABLE moderation_events (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    book_id     bigint      NOT NULL REFERENCES books (id) ON DELETE CASCADE,
    brain_name  varchar(32) NOT NULL,
    outcome     varchar(8)  NOT NULL DEFAULT 'blocked',
    chapter_ids jsonb       NOT NULL DEFAULT '[]'::jsonb,
    detail      text        NOT NULL DEFAULT '',
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_moderation_book ON moderation_events (book_id, brain_name, created_at DESC);

ALTER TABLE moderation_events
    ADD CONSTRAINT ck_moderation_outcome CHECK (outcome IN ('blocked', 'passed'));

COMMENT ON TABLE  moderation_events            IS '内容审核拦截的证据台账。append-only：每一行是一次发生过的拦截，拦多了取 chapter_ids 的交集就能收窄到触发的那一节';
COMMENT ON COLUMN moderation_events.chapter_ids IS '这一次送给模型的章节 id 快照。不做外键关联表：重解析之后这些 id 会不存在，而这条证据说的正是「当时送的是那几节」';
COMMENT ON COLUMN moderation_events.detail      IS '供应商回的那句话，截断保存。**不存请求体、不存 key**';
COMMENT ON COLUMN moderation_events.brain_name  IS '哪个大脑。同一本书在通义被拦、在 Claude 不被拦是常态，所以交集与并集都要按大脑分开算，界面也要说得出是谁';
COMMENT ON COLUMN moderation_events.outcome     IS 'blocked=这几节送过去被拦了；passed=这几节平安送过去了。passed 只在这本书被这个大脑拦过之后才记，否则这张表会变成一份对话流水';
"""

_DOWNGRADE = """
DROP TABLE IF EXISTS moderation_events;
"""


def upgrade() -> None:
    op.get_bind().exec_driver_sql(_UPGRADE)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(_DOWNGRADE)
