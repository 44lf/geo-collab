"""auto_review_decisions 加 pass_line 列。

记录本次评分所用的合格线，供内容列表显示「真实分 / 合格线」。
可空、加法式，对老数据无影响（老行 pass_line=NULL → 序列化走纯数字分支）。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0060_auto_review_pass_line"
down_revision: str | None = "0059_skill_category"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "auto_review_decisions",
        sa.Column("pass_line", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("auto_review_decisions", "pass_line")
