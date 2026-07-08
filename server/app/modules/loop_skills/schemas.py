"""loop_skills 版本管理响应模型。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class BundleVersionMeta(BaseModel):
    id: int
    version_label: str
    bundle_sha256: str
    file_count: int
    total_size: int
    is_enabled: bool
    uploaded_by_user_id: int | None
    notes: str | None
    created_at: datetime


class BundleVersionList(BaseModel):
    versions: list[BundleVersionMeta]


class SkillMeta(BaseModel):
    id: int
    name: str
    slug: str
    is_official: bool
    current_version_label: str | None
    file_count: int
    total_bytes: int
    updated_at: datetime
    uploaded_by: int | None


class SkillList(BaseModel):
    skills: list[SkillMeta]


class SkillVersionMeta(BaseModel):
    id: int
    version_label: str
    bundle_sha256: str
    file_count: int
    total_bytes: int
    uploaded_by: int | None
    uploaded_at: datetime
    is_current: bool


class SkillVersionList(BaseModel):
    versions: list[SkillVersionMeta]


class UploadResult(BaseModel):
    skill_id: int
    slug: str
    version_label: str


class SetCurrentBody(BaseModel):
    version_id: int
