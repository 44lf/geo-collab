"""游戏库 ORM：games（跨源合并的游戏户口本）+ game_tags（归一化标签）。"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.app.core.time import utcnow
from server.app.db.base import Base


class Game(Base):
    __tablename__ = "games"
    __table_args__ = (UniqueConstraint("name_normalized", name="uq_games_name_normalized"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    name_normalized: Mapped[str] = mapped_column(String(200), nullable=False)
    sources: Mapped[list | None] = mapped_column(JSON, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    comment_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    platforms: Mapped[list | None] = mapped_column(JSON, nullable=True)
    icon_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    screenshot_urls: Mapped[list | None] = mapped_column(JSON, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    stock_category_id: Mapped[int | None] = mapped_column(
        ForeignKey("stock_categories.id", ondelete="SET NULL"), nullable=True
    )
    highlight_comments: Mapped[list | None] = mapped_column(JSON, nullable=True)
    related_hotspots: Mapped[list | None] = mapped_column(JSON, nullable=True)
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_used_article_id: Mapped[int | None] = mapped_column(
        ForeignKey("articles.id", ondelete="SET NULL"), nullable=True
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1", index=True
    )
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    tags = relationship("GameTag", back_populates="game", cascade="all, delete-orphan")


class GameTag(Base):
    __tablename__ = "game_tags"
    __table_args__ = (UniqueConstraint("game_id", "tag", name="uq_game_tags_game_tag"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    tag: Mapped[str] = mapped_column(String(100), index=True)
    axis: Mapped[str | None] = mapped_column(String(20), nullable=True)

    game = relationship("Game", back_populates="tags")


class GameIngestConfig(Base):
    __tablename__ = "game_ingest_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    window_start: Mapped[str] = mapped_column(String(5), nullable=False, server_default="03:00")
    window_end: Mapped[str] = mapped_column(String(5), nullable=False, server_default="06:00")
    batch_size: Mapped[int] = mapped_column(Integer, nullable=False, server_default="30")
    min_gap_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default="20")
    max_gap_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default="90")
    source_order: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default="taptap,baidu"
    )
    max_shots: Mapped[int] = mapped_column(Integer, nullable=False, server_default="6")
    last_run_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_run_finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_run_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    last_run_trigger: Mapped[str | None] = mapped_column(String(12), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
