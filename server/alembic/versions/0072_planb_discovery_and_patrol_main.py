"""game_ingest_config 扩两方向：应用宝榜单发现(discovery_*) + 补全巡检覆盖主推(patrol_include_main)

Plan B 两条腿并入：
- 扩库（应用宝榜单发现）需在 UI 可编辑 → 独立 config 字段落 DB，与补全共用单例行。
- 补全（九游按名巡检）并入现有巡检循环（source_order 加 ninegame，无新列）；仅新增
  patrol_include_main 开关，可选把 select_due_games 从 companion-only 放宽到覆盖 main。

seed_paths / discovery_last_run_summary 用 JSON（MySQL JSON 无 server_default，故 nullable，
NULL = 用内置 LIST_PATHS / 尚未运行）。
"""

import sqlalchemy as sa

from alembic import op

revision: str = "0072_planb_discovery_and_patrol_main"
down_revision: str | None = "0071_game_ingest_baidu_only"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "game_ingest_config",
        sa.Column("discovery_enabled", sa.Boolean(), nullable=False, server_default="0"),
    )
    op.add_column(
        "game_ingest_config",
        sa.Column(
            "discovery_window_start", sa.String(length=5), nullable=False, server_default="04:00"
        ),
    )
    op.add_column(
        "game_ingest_config",
        sa.Column(
            "discovery_window_end", sa.String(length=5), nullable=False, server_default="06:00"
        ),
    )
    op.add_column(
        "game_ingest_config",
        sa.Column("discovery_seed_paths", sa.JSON(), nullable=True),
    )
    op.add_column(
        "game_ingest_config",
        sa.Column("discovery_detail_limit", sa.Integer(), nullable=False, server_default="30"),
    )
    op.add_column(
        "game_ingest_config",
        sa.Column("discovery_max_shots", sa.Integer(), nullable=False, server_default="6"),
    )
    op.add_column(
        "game_ingest_config",
        sa.Column("discovery_min_gap_seconds", sa.Integer(), nullable=False, server_default="20"),
    )
    op.add_column(
        "game_ingest_config",
        sa.Column("discovery_max_gap_seconds", sa.Integer(), nullable=False, server_default="90"),
    )
    op.add_column(
        "game_ingest_config",
        sa.Column("discovery_last_run_started_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "game_ingest_config",
        sa.Column("discovery_last_run_finished_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "game_ingest_config",
        sa.Column("discovery_last_run_summary", sa.JSON(), nullable=True),
    )
    op.add_column(
        "game_ingest_config",
        sa.Column("discovery_last_run_trigger", sa.String(length=12), nullable=True),
    )
    op.add_column(
        "game_ingest_config",
        sa.Column("patrol_include_main", sa.Boolean(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    for col in (
        "patrol_include_main",
        "discovery_last_run_trigger",
        "discovery_last_run_summary",
        "discovery_last_run_finished_at",
        "discovery_last_run_started_at",
        "discovery_max_gap_seconds",
        "discovery_min_gap_seconds",
        "discovery_max_shots",
        "discovery_detail_limit",
        "discovery_seed_paths",
        "discovery_window_end",
        "discovery_window_start",
        "discovery_enabled",
    ):
        op.drop_column("game_ingest_config", col)
