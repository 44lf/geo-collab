from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.app.core.time import utcnow
from server.app.db.base import Base


# 文章：三份存储（Tiptap JSON、HTML、纯文本），关联封面和正文图片
class Article(Base):
    __tablename__ = "articles"
    __table_args__ = (CheckConstraint("status in ('draft', 'ready', 'archived')", name="ck_articles_status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300), index=True)
    author: Mapped[str | None] = mapped_column(String(200), nullable=True)
    cover_asset_id: Mapped[str | None] = mapped_column(ForeignKey("assets.id"), nullable=True)
    content_json: Mapped[str] = mapped_column(Text, default="{}")  # Tiptap 编辑器 JSON
    content_html: Mapped[str] = mapped_column(Text, default="")  # 渲染用 HTML
    plain_text: Mapped[str] = mapped_column(Text, default="")  # 纯文本，用于发布
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(30), default="draft", index=True)  # draft / ready / archived
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    cover_asset = relationship("Asset", foreign_keys=[cover_asset_id])
    body_assets = relationship("ArticleBodyAsset", back_populates="article", cascade="all, delete-orphan")
    group_items = relationship("ArticleGroupItem", back_populates="article")
    publish_records = relationship("PublishRecord", back_populates="article")


# 文章正文中的图片关联表，记录每张图片在正文中的位置
class ArticleBodyAsset(Base):
    __tablename__ = "article_body_assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id"), index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)  # 在正文中的排序位置
    editor_node_id: Mapped[str | None] = mapped_column(String(200), nullable=True)  # Tiptap 节点 ID
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    article = relationship("Article", back_populates="body_assets")
    asset = relationship("Asset", back_populates="article_body_links")
