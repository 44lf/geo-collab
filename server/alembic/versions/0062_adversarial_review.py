"""adversarial_review：articles 生文溯源 +3 列，新增 quality_reference 高质量参考库表。

对抗评审质量门（同步版）基础层。articles 新增：
  - source_question_category / source_question_texts —— 仅 /goal MCP save 填的生文溯源
    （scheme/pipeline 生成的文章留 NULL）
  - adversarial_score —— verifier skill 后置写入的对抗判分（N 次均值），纯 advisory，不做闸，
    review_status CHECK 未变

quality_reference 是自包含的参考文章快照表（own=站内已审文章 / external=站外参考），
plain_text 建 ngram FULLTEXT 供后续查重/检索复用（与 articles.ft_articles 同款）。

Revision ID: 0062_adversarial_review
Revises: 0061_report_events
Create Date: 2026-07-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0062_adversarial_review"
down_revision: str | None = "0061_report_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("articles", sa.Column("source_question_category", sa.String(200), nullable=True))
    op.create_index(
        "ix_articles_source_question_category", "articles", ["source_question_category"]
    )
    op.add_column("articles", sa.Column("source_question_texts", sa.JSON(), nullable=True))
    op.add_column("articles", sa.Column("adversarial_score", sa.Integer(), nullable=True))

    op.create_table(
        "quality_reference",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column(
            "article_id",
            sa.Integer(),
            sa.ForeignKey("articles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.Column("content_html", sa.Text(), nullable=False),
        sa.Column("plain_text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("category", sa.String(200), nullable=True),
        sa.Column("source_url", sa.String(1000), nullable=True),
        sa.Column("platform", sa.String(100), nullable=True),
        sa.Column("added_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("origin in ('own','external')", name="ck_quality_reference_origin"),
        sa.UniqueConstraint("article_id", name="uq_quality_reference_article_id"),
        sa.UniqueConstraint("content_hash", name="uq_quality_reference_content_hash"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    for col in ("origin", "category", "is_active"):
        op.create_index(f"ix_quality_reference_{col}", "quality_reference", [col])
    op.execute(
        "ALTER TABLE quality_reference "
        "ADD FULLTEXT INDEX ftx_quality_reference_plain (plain_text) WITH PARSER ngram"
    )


def downgrade() -> None:
    op.drop_table("quality_reference")
    op.drop_index("ix_articles_source_question_category", table_name="articles")
    op.drop_column("articles", "adversarial_score")
    op.drop_column("articles", "source_question_texts")
    op.drop_column("articles", "source_question_category")
