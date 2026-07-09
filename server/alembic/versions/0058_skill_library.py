"""skills + skill_versions 两表（多 skill 库）

物理表名注：设计文档/brief 里写的 "skills"/"skill_versions" 在真实 schema 上和已下线休眠的
旧 LangGraph-era skills 模块（server/app/modules/skills/models.py，表由迁移 0022 创建，且被
generation_sessions.skill_id 一条活的 FK 引用）撞名——alembic/env.py 与
server/tests/utils.py 都会同时 import 两边 models.py 进同一个 Base.metadata，两个同名
Table 会在类定义期报 "Table 'skills' is already defined for this MetaData instance"。
CLAUDE.md 明确要求旧 skills 表"保留休眠不 drop、不写迁移"，故不动它/它的 FK，改用不冲突的
物理表名 skill_library_skills / skill_library_versions（ORM 类名/字段名/约束名仍逐字保持
brief 原样，见 server/app/modules/loop_skills/models.py 顶部同款注释）。

Revision ID: 0058_skill_library
Revises: 0057_merge_heads
Create Date: 2026-07-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0058_skill_library"
down_revision: str | Sequence[str] | None = "0057_merge_heads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "skill_library_skills",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("slug", sa.String(128), nullable=False),
        sa.Column("is_official", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("current_version_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column(
            "name_active",
            sa.String(128),
            sa.Computed("CASE WHEN is_deleted THEN NULL ELSE name END"),
            nullable=True,
        ),
        sa.Column(
            "slug_active",
            sa.String(128),
            sa.Computed("CASE WHEN is_deleted THEN NULL ELSE slug END"),
            nullable=True,
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_unique_constraint("uq_skills_name_active", "skill_library_skills", ["name_active"])
    op.create_unique_constraint("uq_skills_slug_active", "skill_library_skills", ["slug_active"])
    op.create_index("ix_skills_deleted", "skill_library_skills", ["is_deleted"])

    op.create_table(
        "skill_library_versions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "skill_id",
            sa.Integer(),
            sa.ForeignKey("skill_library_skills.id"),
            nullable=False,
        ),
        sa.Column("version_label", sa.String(32), nullable=False),
        sa.Column("bundle_sha256", sa.String(64), nullable=False),
        sa.Column("file_count", sa.Integer(), nullable=False),
        sa.Column("total_bytes", sa.Integer(), nullable=False),
        sa.Column("storage_backend", sa.String(8), nullable=False, server_default="db"),
        sa.Column("files", sa.JSON(), nullable=True),
        sa.Column("storage_key", sa.String(256), nullable=True),
        sa.Column("uploaded_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("uploaded_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("skill_id", "version_label", name="uq_skill_versions_label"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index("ix_skill_versions_skill", "skill_library_versions", ["skill_id", "is_deleted"])


def downgrade() -> None:
    op.drop_table("skill_library_versions")
    op.drop_table("skill_library_skills")
