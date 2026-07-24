"""game auto-cull streak + manual curate flag + cull config"""

import sqlalchemy as sa

from alembic import op

revision: str = "0069_game_cull_and_manual"
down_revision: str | None = "0068_game_ingest_config"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "games",
        sa.Column("not_found_streak", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "games",
        sa.Column("manually_curated", sa.Boolean(), nullable=False, server_default="0"),
    )
    op.add_column(
        "game_ingest_config",
        sa.Column("cull_after_misses", sa.Integer(), nullable=False, server_default="3"),
    )
    op.add_column(
        "game_ingest_config",
        sa.Column("cull_enabled", sa.Boolean(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("game_ingest_config", "cull_enabled")
    op.drop_column("game_ingest_config", "cull_after_misses")
    op.drop_column("games", "manually_curated")
    op.drop_column("games", "not_found_streak")
