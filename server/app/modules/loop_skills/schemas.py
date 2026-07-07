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
