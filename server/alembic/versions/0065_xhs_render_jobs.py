"""xhs_render_jobs table

Revision ID: 0065_xhs_render_jobs
Revises: 0064_qref_external_ingestion
"""

import sqlalchemy as sa

from alembic import op

revision = "0065_xhs_render_jobs"
down_revision = "0064_qref_external_ingestion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "xhs_render_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.String(32), nullable=False),
        sa.Column("source_article_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("theme", sa.String(50), nullable=False, server_default="sketch"),
        sa.Column("mode", sa.String(20), nullable=False, server_default="separator"),
        sa.Column("cover_key", sa.String(500), nullable=True),
        sa.Column("card_keys", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_xhs_render_jobs_job_id", "xhs_render_jobs", ["job_id"], unique=True)
    op.create_index(
        "ix_xhs_render_jobs_source_article_id", "xhs_render_jobs", ["source_article_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_xhs_render_jobs_source_article_id", table_name="xhs_render_jobs")
    op.drop_index("ix_xhs_render_jobs_job_id", table_name="xhs_render_jobs")
    op.drop_table("xhs_render_jobs")
