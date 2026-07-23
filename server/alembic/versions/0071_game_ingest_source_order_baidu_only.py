"""game_ingest_config.source_order 默认改 baidu-only + 规整存量行

taptap 游戏库爬虫被移除（IP 级封禁不可用），source_order 默认与存量单例行
从 "taptap,baidu" 收敛为 "baidu"。运行时对未知源本就优雅跳过，此迁移仅为 DB/UI 一致。
"""

import sqlalchemy as sa

from alembic import op

revision: str = "0071_game_ingest_source_order_baidu_only"
down_revision: str | None = "0070_prompt_template_platform"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.alter_column(
        "game_ingest_config",
        "source_order",
        existing_type=sa.String(length=50),
        nullable=False,
        server_default="baidu",
    )
    op.execute(
        "UPDATE game_ingest_config SET source_order='baidu' "
        "WHERE source_order='taptap,baidu'"
    )


def downgrade() -> None:
    op.alter_column(
        "game_ingest_config",
        "source_order",
        existing_type=sa.String(length=50),
        nullable=False,
        server_default="taptap,baidu",
    )
    op.execute(
        "UPDATE game_ingest_config SET source_order='taptap,baidu' "
        "WHERE source_order='baidu'"
    )
