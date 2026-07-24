"""qref 外部参考异步入库：图片资源表 + reference↔image 关联表 + 导入 job 表。

站外文章爬取 → 异步入高质量库外部参考（MCP 入口）。图片跨篇 sha256 去重共享（无
reference_id，归属走 link 表）；job 表仿 video_jobs 支撑建 job 秒回 + 轮询。

Revision ID: 0064_qref_external_ingestion
Revises: 0063_qref_multi_category
Create Date: 2026-07-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0064_qref_external_ingestion"
down_revision: str | None = "0063_qref_multi_category"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "quality_reference_image",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("minio_key", sa.String(500), nullable=False),
        sa.Column("bucket", sa.String(100), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("sha256", name="uq_qref_image_sha256"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_table(
        "quality_reference_image_link",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("reference_id", sa.Integer(), nullable=False),
        sa.Column("image_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["reference_id"],
            ["quality_reference.id"],
            name="fk_qref_image_link_reference_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["image_id"],
            ["quality_reference_image.id"],
            name="fk_qref_image_link_image_id",
        ),
        sa.UniqueConstraint("reference_id", "image_id", name="uq_qref_image_link_ref_img"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index("ix_qref_image_link_image_id", "quality_reference_image_link", ["image_id"])
    op.create_table(
        "quality_reference_import_job",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.String(32), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("progress", sa.Float(), nullable=False, server_default="0"),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("markdown", sa.Text(), nullable=False),
        sa.Column("platform", sa.String(100), nullable=True),
        sa.Column("source_url", sa.String(1000), nullable=False),
        sa.Column("category", sa.String(200), nullable=True),
        sa.Column("question_texts", sa.JSON(), nullable=True),
        sa.Column("reference_id", sa.Integer(), nullable=True),
        sa.Column("images_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("images_rehosted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("images_skipped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("job_id", name="uq_qref_import_job_job_id"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index("ix_qref_import_job_job_id", "quality_reference_import_job", ["job_id"])


def downgrade() -> None:
    op.drop_table("quality_reference_import_job")
    op.drop_table("quality_reference_image_link")
    op.drop_table("quality_reference_image")
