"""种入 TapTap 平台（cookie-session API 账号；API 账号列已由 0044 加好，本迁移只插 platforms 行）

修订 ID: 0050
上一修订: 0049
创建日期: 2026-06-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0050"
down_revision: str | None = "0049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    exists = conn.execute(sa.text("SELECT id FROM platforms WHERE code = 'taptap'")).first()
    if exists is None:
        conn.execute(
            sa.text(
                "INSERT INTO platforms (code, name, base_url, enabled, created_at) "
                "VALUES ('taptap', 'TapTap', 'https://www.taptap.cn', 1, NOW())"
            )
        )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DELETE FROM platforms WHERE code = 'taptap'"))
