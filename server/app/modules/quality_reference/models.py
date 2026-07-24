"""高质量参考库 ORM 模型（对抗评审质量门用）。

quality_reference 收纳两类参考文章：
  - origin="own"      —— 本站已审核通过的文章（article_id 指回 articles，软删/删除时靠
                          FK ondelete="SET NULL" 解绑，参考记录本身保留自包含快照）
  - origin="external" —— 站外人写的参考文章（article_id 恒 NULL），只入本表、不进 articles，
                          避免污染正文库。

正文三份并行结构（content_json / content_html / plain_text）与 Article 同款快照惯例，
保证参考记录自包含（不依赖 articles 表存活）。content_hash 做查重（sha256(归一化
title+plain_text)）。问题类型关联走子表 quality_reference_category（多对多）：一篇参考
可挂多个类型 + 每类型可选问题词；「通用兜底」＝该参考在子表**无任何行**（不再是单列
category IS NULL）。
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
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
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    platform: Mapped[str | None] = mapped_column(String(100), nullable=True)
    added_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    # 问题类型关联（多对多子表）；replace-all 走 service.set_reference_categories。
    categories: Mapped[list["QualityReferenceCategory"]] = relationship(
        back_populates="reference",
        cascade="all, delete-orphan",
        passive_deletes=True,  # 依赖 DB ON DELETE CASCADE 清理子行
        order_by="QualityReferenceCategory.id",
    )


class QualityReferenceCategory(Base):
    """quality_reference 的问题类型关联（多对多子表）。

    一篇参考可关联多个问题类型，每类型可选存问题词（question_texts）。UNIQUE(reference_id,
    category) 去重；FK ON DELETE CASCADE 随 reference 删除清理。某参考在本表无任何行＝通用兜底。
    """

    __tablename__ = "quality_reference_category"
    __table_args__ = (
        UniqueConstraint("reference_id", "category", name="uq_qref_category_ref_cat"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    reference_id: Mapped[int] = mapped_column(
        ForeignKey("quality_reference.id", ondelete="CASCADE"), nullable=False
    )  # UNIQUE(reference_id,category) 的最左前缀即满足 FK 所需索引，无需单独建 index
    category: Mapped[str] = mapped_column(String(200), index=True)
    question_texts: Mapped[list | None] = mapped_column(JSON, nullable=True)  # 该类型下的问题词
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    reference: Mapped["QualityReference"] = relationship(back_populates="categories")


class QualityReferenceImage(Base):
    """去重共享的图片资源（不属于单篇；归属由 QualityReferenceImageLink 表达）。"""

    __tablename__ = "quality_reference_image"
    __table_args__ = (UniqueConstraint("sha256", name="uq_qref_image_sha256"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64))  # 去重键（内容哈希）
    minio_key: Mapped[str] = mapped_column(String(500))  # 专桶内对象 key（sha256+ext）
    bucket: Mapped[str] = mapped_column(String(100))  # 冗余记桶名，便于将来迁桶
    mime_type: Mapped[str] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class QualityReferenceImageLink(Base):
    """reference ↔ image 关联（多对多，供孤儿清理）。"""

    __tablename__ = "quality_reference_image_link"
    __table_args__ = (
        UniqueConstraint("reference_id", "image_id", name="uq_qref_image_link_ref_img"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    reference_id: Mapped[int] = mapped_column(
        ForeignKey("quality_reference.id", ondelete="CASCADE"), nullable=False, index=True
    )
    image_id: Mapped[int] = mapped_column(
        ForeignKey("quality_reference_image.id"), nullable=False, index=True
    )


class QualityReferenceImportJob(Base):
    """异步导入 job（仿 VideoJob）：建 job 秒回 → 后台线程跑 → 轮询状态。"""

    __tablename__ = "quality_reference_import_job"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    status: Mapped[str] = mapped_column(
        String(20), server_default="pending"
    )  # pending/running/done/failed
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    title: Mapped[str] = mapped_column(String(300))
    markdown: Mapped[str] = mapped_column(Text)  # 输入快照
    platform: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_url: Mapped[str] = mapped_column(String(1000))  # 必填
    category: Mapped[str | None] = mapped_column(String(200), nullable=True)
    question_texts: Mapped[list | None] = mapped_column(JSON, nullable=True)
    reference_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 成功回填
    images_total: Mapped[int] = mapped_column(Integer, default=0)
    images_rehosted: Mapped[int] = mapped_column(Integer, default=0)
    images_skipped: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
