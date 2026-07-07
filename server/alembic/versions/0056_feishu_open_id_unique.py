"""users.feishu_open_id 唯一索引（首登自绑防串号）

修订 ID: 0056_feishu_open_id_unique
上一修订: 0055
创建日期: 2026-07-07

注:原 id "0056" 与 0056_loop_skill_bundle_versions 重复(两 MR 并行合入),
alembic 直接报 "Revision 0056 is present more than once"。loop-skill 合入更早、
可能已有环境以 "0056" 应用过它,故重命名本迁移的 id 消歧。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0056_feishu_open_id_unique"
down_revision: str | None = "0055"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("uq_users_feishu_open_id", "users", ["feishu_open_id"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_users_feishu_open_id", table_name="users")
