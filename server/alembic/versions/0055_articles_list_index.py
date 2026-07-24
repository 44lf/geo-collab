"""Add composite index (is_deleted, updated_at) for article list ordering.

内容列表默认查询是 `WHERE is_deleted = 0 ORDER BY updated_at DESC LIMIT ...`（见
articles/service.py:list_articles 主分支）。此前 updated_at 无索引，排序走 filesort，
随文章量增长变慢。复合索引 (is_deleted, updated_at) 让优化器先按 is_deleted 定位、
再按 updated_at 有序扫描，消除 filesort。

Revision ID: 0055
Revises: 0054
Create Date: 2026-07-06
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0055"
down_revision: str | None = "0054"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        op.f("ix_articles_is_deleted_updated_at"),
        "articles",
        ["is_deleted", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_articles_is_deleted_updated_at"), table_name="articles")
