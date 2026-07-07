"""loop_skills ORM 模型 —— skill 包版本(上传入库 + 版本管理)。

一行 = 一个上传的 skill 包版本;files 存解压后文件列表(JSON),下载时由
build_zip 现组 zip。is_enabled 全局唯一(GET_LOCK 应用级锁保证),is_deleted 逻辑删除。
FK/PK 用 Integer 对齐 users.id(BIGINT→INT 会触发 MySQL errno 150)。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, deferred, mapped_column

from server.app.core.time import utcnow
from server.app.db.base import Base


class LoopSkillBundleVersion(Base):
    __tablename__ = "loop_skill_bundle_versions"
    __table_args__ = (
        Index("ix_loop_skill_bundle_enabled", "is_enabled", "is_deleted"),
        Index("ix_loop_skill_bundle_deleted", "is_deleted"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version_label: Mapped[str] = mapped_column(String(200))
    bundle_sha256: Mapped[str] = mapped_column(String(64))
    # files 是大 JSON([{path,content,sha256,size}]);列表查询不需要它 → deferred 惰性加载,
    # 只有组包给具体版本时才拉。file_count/total_size 去规范化冗余,让列表零成本。
    files: Mapped[list] = deferred(mapped_column(JSON))
    file_count: Mapped[int] = mapped_column(Integer)
    total_size: Mapped[int] = mapped_column(Integer)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    uploaded_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
