"""users.feishu_open_id 唯一索引（首登自绑防串号）

修订 ID: 0056
上一修订: 0055
创建日期: 2026-07-07
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0056"
down_revision: str | None = "0055"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("uq_users_feishu_open_id", "users", ["feishu_open_id"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_users_feishu_open_id", table_name="users")
