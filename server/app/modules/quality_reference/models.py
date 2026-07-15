"""高质量参考库 ORM 模型（对抗评审质量门用）。

quality_reference 收纳两类参考文章：
  - origin="own"      —— 本站已审核通过的文章（article_id 指回 articles，软删/删除时靠
                          FK ondelete="SET NULL" 解绑，参考记录本身保留自包含快照）
  - origin="external" —— 站外人写的参考文章（article_id 恒 NULL），只入本表、不进 articles，
                          避免污染正文库。

正文三份并行结构（content_json / content_html / plain_text）与 Article 同款快照惯例，
保证参考记录自包含（不依赖 articles 表存活）。content_hash 做查重（sha256(归一化
title+plain_text)），category 空值＝通用兜底（不按题材过滤也能命中）。
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from server.app.core.time import utcnow
from server.app.db.base import Base


class QualityReference(Base):
    __tablename__ = "quality_reference"
    __table_args__ = (
        CheckConstraint("origin in ('own','external')", name="ck_quality_reference_origin"),
        UniqueConstraint("article_id", name="uq_quality_reference_article_id"),
        UniqueConstraint("content_hash", name="uq_quality_reference_content_hash"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    origin: Mapped[str] = mapped_column(String(16), index=True)  # own | external，服务端盖章
    article_id: Mapped[int | None] = mapped_column(
        ForeignKey("articles.id", ondelete="SET NULL"), nullable=True
    )  # 外部=NULL；软删不触发 SET NULL（见 §13.3）
    title: Mapped[str] = mapped_column(String(300))
    content_json: Mapped[str] = mapped_column(Text, default="{}")  # 序列化字符串
    content_html: Mapped[str] = mapped_column(Text, default="")  # 外部已 nh3 清洗
    plain_text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))  # sha256(归一化 title+plain_text)
    category: Mapped[str | None] = mapped_column(
        String(200), nullable=True, index=True
    )  # 空=通用兜底
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    platform: Mapped[str | None] = mapped_column(String(100), nullable=True)
    added_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
