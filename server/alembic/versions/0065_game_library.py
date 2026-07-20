"""游戏库语料底座：games + game_tags 新表，stock_images 扩 5 列（入库去重 + 图片级用量）。

games 是跨源合并的游戏户口本（name_normalized 唯一去重），game_tags 是归一化标签子表
（UNIQUE(game_id, tag)，随 game 级联删除）。stock_images 新增 source_url/source_url_hash
做入库去重（UNIQUE(category_id, source_url_hash)）+ use_count/last_used_at/
last_used_article_id 做图片级用量软 LRU。

Revision ID: 0065_game_library
Revises: 0064_qref_external_ingestion
Create Date: 2026-07-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0065_game_library"
down_revision: str | None = "0064_qref_external_ingestion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "games",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("name_normalized", sa.String(200), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("comment_count", sa.Integer(), nullable=True),
        sa.Column("platforms", sa.JSON(), nullable=True),
        sa.Column("icon_url", sa.String(1000), nullable=True),
        sa.Column("screenshot_urls", sa.JSON(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "stock_category_id",
            sa.Integer(),
            sa.ForeignKey("stock_categories.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("highlight_comments", sa.JSON(), nullable=True),
        sa.Column("related_hotspots", sa.JSON(), nullable=True),
        sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "last_used_article_id",
            sa.Integer(),
            sa.ForeignKey("articles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("first_seen_at", sa.DateTime(), nullable=True),
        sa.Column("last_verified_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("name_normalized", name="uq_games_name_normalized"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index("ix_games_is_active", "games", ["is_active"])
    op.create_table(
        "game_tags",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "game_id",
            sa.Integer(),
            sa.ForeignKey("games.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tag", sa.String(100), nullable=False),
        sa.Column("axis", sa.String(20), nullable=True),
        sa.UniqueConstraint("game_id", "tag", name="uq_game_tags_game_tag"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index("ix_game_tags_game_id", "game_tags", ["game_id"])
    op.create_index("ix_game_tags_tag", "game_tags", ["tag"])

    op.add_column("stock_images", sa.Column("source_url", sa.String(1000), nullable=True))
    op.add_column("stock_images", sa.Column("source_url_hash", sa.String(64), nullable=True))
    op.add_column(
        "stock_images", sa.Column("use_count", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column("stock_images", sa.Column("last_used_at", sa.DateTime(), nullable=True))
    op.add_column("stock_images", sa.Column("last_used_article_id", sa.Integer(), nullable=True))
    # 列先加成裸 Integer 再补命名 FK：MySQL 不允许在 ALTER TABLE ADD COLUMN 里内联
    # REFERENCES 又不给约束名（后续 downgrade 无法可靠 drop_constraint by name）；
    # 照 0044_wechat_mp_accounts.py 的既有 idiom（先加列，再 create_foreign_key）。
    op.create_foreign_key(
        "fk_stock_images_last_used_article_id",
        "stock_images",
        "articles",
        ["last_used_article_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        "uq_stock_images_category_source_hash",
        "stock_images",
        ["category_id", "source_url_hash"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_stock_images_category_source_hash", "stock_images", type_="unique")
    op.drop_constraint("fk_stock_images_last_used_article_id", "stock_images", type_="foreignkey")
    for col in (
        "last_used_article_id",
        "last_used_at",
        "use_count",
        "source_url_hash",
        "source_url",
    ):
        op.drop_column("stock_images", col)
    op.drop_table("game_tags")
    op.drop_table("games")
