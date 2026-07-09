"""skill_library_skills 加 category 业务标签列。

已存在的官方 goal 就地修正为 generation（seed 是"存在即 skip"，不能靠它补）。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0059_skill_category"
down_revision: str | None = "0058_skill_library"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "skill_library_skills",
        sa.Column("category", sa.String(length=32), nullable=False, server_default="general"),
    )
    op.execute(
        "UPDATE skill_library_skills SET category='generation' WHERE slug='goal' AND is_official=1"
    )


def downgrade() -> None:
    op.drop_column("skill_library_skills", "category")
