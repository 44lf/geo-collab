"""loop_skills ORM 模型 —— skill 包版本(上传入库 + 版本管理)。

一行 = 一个上传的 skill 包版本;files 存解压后文件列表(JSON),下载时由
build_zip 现组 zip。is_enabled 全局唯一(GET_LOCK 应用级锁保证),is_deleted 逻辑删除。
FK/PK 用 Integer 对齐 users.id(BIGINT→INT 会触发 MySQL errno 150)。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
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


class Skill(Base):
    # NOTE: 物理表名不是 "skills"——server/app/modules/skills/models.py 里已下线休眠的旧
    # LangGraph-era Skill 模型早占了 "skills" 这个表名（迁移 0022 创建，且被
    # generation_sessions.skill_id 一条活的 FK 引用着）。alembic/env.py 与
    # server/tests/utils.py 都无条件同时 import 这两个模块的 models.py 进同一个
    # Base.metadata，两个同名 Table 会在类定义期直接报
    # "Table 'skills' is already defined for this MetaData instance"。
    # CLAUDE.md 明确要求旧 skills 表"保留休眠不 drop、不写迁移"，所以不去动它/它的 FK；
    # 这里改用不冲突的物理表名 "skill_library_skills"，Python 类名/字段名/约束名仍逐字
    # 保持 brief 原样，不影响 Task 2+ 按类名 import 使用。
    __tablename__ = "skill_library_skills"
    __table_args__ = (
        UniqueConstraint("name_active", name="uq_skills_name_active"),
        UniqueConstraint("slug_active", name="uq_skills_slug_active"),
        Index("ix_skills_deleted", "is_deleted"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128))
    slug: Mapped[str] = mapped_column(String(128))
    is_official: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    current_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    # 生成列由迁移创建；ORM 只读映射，插入/更新时不写它（MySQL 自动算）
    name_active: Mapped[str | None] = mapped_column(
        String(128), Computed("CASE WHEN is_deleted THEN NULL ELSE name END"), nullable=True
    )
    slug_active: Mapped[str | None] = mapped_column(
        String(128), Computed("CASE WHEN is_deleted THEN NULL ELSE slug END"), nullable=True
    )


class SkillVersion(Base):
    # 同上：物理表名避开与旧 "skill_versions" 概念无关但为对齐 Skill 表改名而联动改的表名，
    # 保持 (skills, skill_versions) 概念上的一一对应，只是加了 skill_library_ 前缀避让冲突。
    __tablename__ = "skill_library_versions"
    __table_args__ = (
        UniqueConstraint("skill_id", "version_label", name="uq_skill_versions_label"),
        Index("ix_skill_versions_skill", "skill_id", "is_deleted"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    skill_id: Mapped[int] = mapped_column(ForeignKey("skill_library_skills.id"))
    version_label: Mapped[str] = mapped_column(String(32))
    bundle_sha256: Mapped[str] = mapped_column(String(64))
    file_count: Mapped[int] = mapped_column(Integer)
    total_bytes: Mapped[int] = mapped_column(Integer)
    storage_backend: Mapped[str] = mapped_column(String(8), default="db", nullable=False)
    files: Mapped[list | None] = deferred(mapped_column(JSON, nullable=True))
    storage_key: Mapped[str | None] = mapped_column(String(256), nullable=True)
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
