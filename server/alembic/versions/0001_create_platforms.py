"""create platforms

Revision ID: 0001_create_platforms
Revises:
Create Date: 2026-05-06
"""
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001_create_platforms"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "platforms",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_platforms_code"), "platforms", ["code"], unique=True)
    op.bulk_insert(
        sa.table(
            "platforms",
            sa.column("code", sa.String()),
            sa.column("name", sa.String()),
            sa.column("base_url", sa.String()),
            sa.column("enabled", sa.Boolean()),
            sa.column("created_at", sa.DateTime()),
        ),
        [
            {
                "code": "toutiao",
                "name": "头条号",
                "base_url": "https://mp.toutiao.com",
                "enabled": True,
                "created_at": datetime.now(timezone.utc).replace(tzinfo=None),
            }
        ],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_platforms_code"), table_name="platforms")
    op.drop_table("platforms")
