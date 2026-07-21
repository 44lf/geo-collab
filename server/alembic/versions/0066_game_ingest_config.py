"""game_ingest_config singleton"""
from alembic import op
import sqlalchemy as sa

revision = "0066_game_ingest_config"
down_revision = "0065_game_library"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "game_ingest_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("window_start", sa.String(5), nullable=False, server_default="03:00"),
        sa.Column("window_end", sa.String(5), nullable=False, server_default="06:00"),
        sa.Column("batch_size", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("min_gap_seconds", sa.Integer(), nullable=False, server_default="20"),
        sa.Column("max_gap_seconds", sa.Integer(), nullable=False, server_default="90"),
        sa.Column("source_order", sa.String(50), nullable=False, server_default="taptap,baidu"),
        sa.Column("max_shots", sa.Integer(), nullable=False, server_default="6"),
        sa.Column("last_run_started_at", sa.DateTime(), nullable=True),
        sa.Column("last_run_finished_at", sa.DateTime(), nullable=True),
        sa.Column("last_run_summary", sa.JSON(), nullable=True),
        sa.Column("last_run_trigger", sa.String(12), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.execute("INSERT INTO game_ingest_config (id, updated_at) VALUES (1, NOW())")


def downgrade() -> None:
    op.drop_table("game_ingest_config")
