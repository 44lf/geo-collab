"""qref 多对多问题类型：quality_reference.category 单列 → 子表 quality_reference_category。

对抗评审质量门增量。一篇高质量参考可关联多个问题类型（own + external 皆可），并存每类型
的问题词（question_texts）。铁律：quality_reference 的 content_hash / article_id UNIQUE
原样不动，多关联只靠本子表表达（绝不给 quality_reference 加行）。「通用兜底」语义从
category IS NULL 改成「该 reference 在子表无任何行」。

upgrade 回填：把每条 quality_reference.category（非空）迁成一条子表行；own 参考 LEFT JOIN
articles 拿 source_question_texts 存进 question_texts，external（article_id NULL）→ NULL。
downgrade lossy：多类型时只取任意一条 category 回填单列（best-effort）。

Revision ID: 0063_qref_multi_category
Revises: 0062_adversarial_review
Create Date: 2026-07-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0063_qref_multi_category"
down_revision: str | None = "0062_adversarial_review"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "quality_reference_category",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("reference_id", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(200), nullable=False),
        sa.Column("question_texts", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["reference_id"],
            ["quality_reference.id"],
            name="fk_qref_category_reference_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("reference_id", "category", name="uq_qref_category_ref_cat"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index(
        "ix_quality_reference_category_category",
        "quality_reference_category",
        ["category"],
    )

    # 回填：单列 category → 子表；own 参考 join articles 拿 source_question_texts，external→NULL。
    op.execute(
        "INSERT INTO quality_reference_category "
        "(reference_id, category, question_texts, created_at) "
        "SELECT qr.id, qr.category, a.source_question_texts, NOW() "
        "FROM quality_reference qr "
        "LEFT JOIN articles a ON a.id = qr.article_id "
        "WHERE qr.category IS NOT NULL"
    )

    op.drop_index("ix_quality_reference_category", table_name="quality_reference")
    op.drop_column("quality_reference", "category")


def downgrade() -> None:
    op.add_column("quality_reference", sa.Column("category", sa.String(200), nullable=True))
    op.create_index("ix_quality_reference_category", "quality_reference", ["category"])
    # lossy best-effort：多类型只取任意一条回填单列。
    op.execute(
        "UPDATE quality_reference qr "
        "SET category = ("
        "  SELECT qrc.category FROM quality_reference_category qrc "
        "  WHERE qrc.reference_id = qr.id LIMIT 1"
        ")"
    )
    op.drop_table("quality_reference_category")
