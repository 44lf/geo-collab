"""add idempotency keys and optimistic versions

Revision ID: 0004_idempotency_versions
Revises: 0003_fts5_indexes
Create Date: 2026-05-08 17:40:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004_idempotency_versions"
down_revision: Union[str, None] = "0003_fts5_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("articles", sa.Column("client_request_id", sa.String(length=80), nullable=True))
    op.add_column("articles", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    op.create_index("uq_articles_client_request_id", "articles", ["client_request_id"], unique=True)

    op.add_column("article_groups", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))

    op.add_column("publish_tasks", sa.Column("client_request_id", sa.String(length=80), nullable=True))
    op.create_index("uq_publish_tasks_client_request_id", "publish_tasks", ["client_request_id"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_publish_tasks_client_request_id", table_name="publish_tasks")
    op.drop_column("publish_tasks", "client_request_id")

    op.drop_column("article_groups", "version")

    op.drop_index("uq_articles_client_request_id", table_name="articles")
    op.drop_column("articles", "version")
    op.drop_column("articles", "client_request_id")
