from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.app.core.time import utcnow
from server.app.db.base import Base


class Article(Base):
    __tablename__ = "articles"
    __table_args__ = (CheckConstraint("status in ('draft', 'ready', 'archived')", name="ck_articles_status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300), index=True)
    author: Mapped[str | None] = mapped_column(String(200), nullable=True)
    cover_asset_id: Mapped[str | None] = mapped_column(ForeignKey("assets.id"), nullable=True)
    content_json: Mapped[str] = mapped_column(Text, default="{}")
    content_html: Mapped[str] = mapped_column(Text, default="")
    plain_text: Mapped[str] = mapped_column(Text, default="")
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(30), default="draft", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    cover_asset = relationship("Asset", foreign_keys=[cover_asset_id])
    body_assets = relationship("ArticleBodyAsset", back_populates="article", cascade="all, delete-orphan")
    group_items = relationship("ArticleGroupItem", back_populates="article")
    publish_records = relationship("PublishRecord", back_populates="article")


class ArticleBodyAsset(Base):
    __tablename__ = "article_body_assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id"), index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    editor_node_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    article = relationship("Article", back_populates="body_assets")
    asset = relationship("Asset", back_populates="article_body_links")
