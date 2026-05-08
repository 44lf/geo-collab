from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.app.core.time import utcnow
from server.app.db.base import Base


# 文章分组：用于批量发布任务，一个分组包含多篇文章
class ArticleGroup(Base):
    __tablename__ = "article_groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    items = relationship("ArticleGroupItem", back_populates="group", cascade="all, delete-orphan")
    publish_tasks = relationship("PublishTask", back_populates="group")


# 分组-文章关联表，带排序字段
class ArticleGroupItem(Base):
    __tablename__ = "article_group_items"
    __table_args__ = (UniqueConstraint("group_id", "article_id", name="uq_article_group_items_group_article"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("article_groups.id"), index=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id"), index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    group = relationship("ArticleGroup", back_populates="items")
    article = relationship("Article", back_populates="group_items")
