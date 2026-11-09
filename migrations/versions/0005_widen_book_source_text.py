"""chapters.source_id、books.published、books.isbn 放宽成 TEXT

Revision ID: 0005_widen_book_source_text
Revises: 0004_brain_choices

这三列的值都是从书的文件里**原样**照抄来的，长度由书决定，不由我们：

* `chapters.source_id` 是来源定位。EPUB 的写法是「压缩包里的路径#锚点」
  （`OEBPS/Text/xxx_chapter_0001.xhtml#toc_anchor_12`），路径深一点、锚点长一点
  就过了 64。原先那句注释说它是 spine idref，那是更早的写法。
* `books.published` 是 dc:date 的原文（`2017`、`2017-01-01T00:00:00+08:00`，
  也有写成一句话的）——本来就说好了原样保留、不转 DATE。
* `books.isbn` 有时带着前缀与说明（`urn:isbn:…`、`ISBN 978-… (平装)`）。

超了会怎样：解析本身成功了，写库时撞上 `value too long`，那一半整个撤回。原先这一撤，
书就永远卡在「解析中」（修在 `parse_service.execute`）；修好之后也只是「停下了」——
而重新解析还是同一个文件、同一个长度，这本书永远进不了书库。一个说明性质的字段
不该拦住整本书。

VARCHAR → TEXT 在 PostgreSQL 里是二进制兼容的类型变更：只改系统目录，不重写表，
也不重建索引（这三列上没有索引）。降级截回原来的宽度（`left()`）：降级本来就是
有损的回退，截断好过降级半路失败。
"""

from __future__ import annotations

from alembic import op

revision = "0005_widen_book_source_text"
down_revision = "0004_brain_choices"
branch_labels = None
depends_on = None


_UPGRADE = """
ALTER TABLE chapters ALTER COLUMN source_id TYPE text;
ALTER TABLE books    ALTER COLUMN published TYPE text;
ALTER TABLE books    ALTER COLUMN isbn      TYPE text;

COMMENT ON COLUMN chapters.source_id IS '来源定位，原样照抄：epub 是压缩包里的「路径#锚点」，pdf 是页码区间（p.3-7），纯文本是行号（line:12）。长度由书决定，所以是 TEXT';
COMMENT ON COLUMN books.published    IS '出版日期原样保留：来源格式不统一（2017 / 2017-01 / 2017-01-01T00:00:00+08:00），转 DATE 会丢信息；长度由书决定，所以是 TEXT';
"""

_DOWNGRADE = """
ALTER TABLE chapters ALTER COLUMN source_id TYPE varchar(64) USING left(source_id, 64);
ALTER TABLE books    ALTER COLUMN published TYPE varchar(32) USING left(published, 32);
ALTER TABLE books    ALTER COLUMN isbn      TYPE varchar(32) USING left(isbn, 32);

COMMENT ON COLUMN chapters.source_id IS '来源定位：epub 的 spine idref / pdf 的页码区间';
COMMENT ON COLUMN books.published    IS '出版日期原样保留：来源格式不统一（2017 / 2017-01 / 2017-01-01），转 DATE 会丢信息';
"""


def upgrade() -> None:
    op.get_bind().exec_driver_sql(_UPGRADE)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(_DOWNGRADE)
