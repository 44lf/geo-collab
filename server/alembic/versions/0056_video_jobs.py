"""create video_jobs

存视频异步任务的状态机与产物引用。产物 mp4/srt 以 MinIO 对象 key 记录（不复用
Asset，避免其 article-attachment 语义耦合）。

Revision ID: 0056_video_jobs
Revises: 0055
Create Date: 2026-07-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0056_video_jobs"
down_revision: str | None = "0055"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "video_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.String(length=32), nullable=False),
        sa.Column("article_id", sa.Integer(), sa.ForeignKey("articles.id"), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("progress", sa.Float(), nullable=False, server_default="0"),
        sa.Column("storyboard", sa.JSON(), nullable=False),
        sa.Column("engine", sa.String(length=50), nullable=True),
        sa.Column("video_key", sa.String(length=500), nullable=True),
        sa.Column("srt_key", sa.String(length=500), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_video_jobs_job_id", "video_jobs", ["job_id"], unique=True)
    op.create_index("ix_video_jobs_article_id", "video_jobs", ["article_id"])


def downgrade() -> None:
    op.drop_index("ix_video_jobs_article_id", table_name="video_jobs")
    op.drop_index("ix_video_jobs_job_id", table_name="video_jobs")
    op.drop_table("video_jobs")
