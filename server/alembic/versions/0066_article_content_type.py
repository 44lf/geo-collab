"""articles.content_type

Revision ID: 0066_article_content_type
Revises: 0065_xhs_render_jobs
"""

import sqlalchemy as sa

from alembic import op

revision = "0066_article_content_type"
down_revision = "0065_xhs_render_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("articles", sa.Column("content_type", sa.String(40), nullable=True))
    op.create_index("ix_articles_content_type", "articles", ["content_type"])


def downgrade() -> None:
    op.drop_index("ix_articles_content_type", table_name="articles")
    op.drop_column("articles", "content_type")
