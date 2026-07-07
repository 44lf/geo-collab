"""loop_skill_bundle_versions: skill 包版本入库 + 版本管理.

新表存放上传的 /goal skill 包版本(解压后文件列表存 JSON),支持启用/回退/逻辑删除。
get_active_bundle 有启用版用之、否则回落 templates/ 种子。

Revision ID: 0056
Revises: 0055
Create Date: 2026-07-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0056"
down_revision: str | None = "0055"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "loop_skill_bundle_versions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("version_label", sa.String(length=200), nullable=False),
        sa.Column("bundle_sha256", sa.String(length=64), nullable=False),
        sa.Column("files", sa.JSON(), nullable=False),
        sa.Column("file_count", sa.Integer(), nullable=False),
        sa.Column("total_size", sa.Integer(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "uploaded_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_loop_skill_bundle_enabled",
        "loop_skill_bundle_versions",
        ["is_enabled", "is_deleted"],
    )
    op.create_index(
        "ix_loop_skill_bundle_deleted",
        "loop_skill_bundle_versions",
        ["is_deleted"],
    )


def downgrade() -> None:
    op.drop_index("ix_loop_skill_bundle_enabled", table_name="loop_skill_bundle_versions")
    op.drop_index("ix_loop_skill_bundle_deleted", table_name="loop_skill_bundle_versions")
    op.drop_table("loop_skill_bundle_versions")
