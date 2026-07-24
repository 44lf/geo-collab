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

revision: str = "0072_planb_discovery_patrol"
down_revision: str | None = "0071_game_ingest_baidu_only"
branch_labels: str | None = None
depends_on: str | None = None


def _new_columns() -> list[sa.Column]:
    """本迁移新增的 13 列；每次调用返回全新 Column 实例，避免跨 op.add_column 复用绑定状态。"""
    return [
        sa.Column("discovery_enabled", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column(
            "discovery_window_start", sa.String(length=5), nullable=False, server_default="04:00"
        ),
        sa.Column(
            "discovery_window_end", sa.String(length=5), nullable=False, server_default="06:00"
        ),
        sa.Column("discovery_seed_paths", sa.JSON(), nullable=True),
        sa.Column("discovery_detail_limit", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("discovery_max_shots", sa.Integer(), nullable=False, server_default="6"),
        sa.Column("discovery_min_gap_seconds", sa.Integer(), nullable=False, server_default="20"),
        sa.Column("discovery_max_gap_seconds", sa.Integer(), nullable=False, server_default="90"),
        sa.Column("discovery_last_run_started_at", sa.DateTime(), nullable=True),
        sa.Column("discovery_last_run_finished_at", sa.DateTime(), nullable=True),
        sa.Column("discovery_last_run_summary", sa.JSON(), nullable=True),
        sa.Column("discovery_last_run_trigger", sa.String(length=12), nullable=True),
        sa.Column("patrol_include_main", sa.Boolean(), nullable=False, server_default="0"),
    ]


def _existing_columns() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("game_ingest_config")}


def upgrade() -> None:
    # 幂等：只加尚不存在的列。首发（release-1.0.32）时 MySQL 非事务 DDL 已把这 13 列落库，
    # 但末尾 alembic_version UPDATE 因旧 revision id 超 32 字符（1406 Data too long）回滚 →
    # 线上"列已在、版本仍停 0071"。收窄 id + 幂等，让重发能安全收敛（无论列在不在）。
    existing = _existing_columns()
    for col in _new_columns():
        if col.name not in existing:
            op.add_column("game_ingest_config", col)


def downgrade() -> None:
    existing = _existing_columns()
    for col in reversed(_new_columns()):
        if col.name in existing:
            op.drop_column("game_ingest_config", col.name)
