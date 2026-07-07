"""loop_skills 版本管理 —— DB 读写(与纯文件逻辑的 service.py 分离)。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.app.modules.loop_skills.models import LoopSkillBundleVersion
from server.app.modules.loop_skills.service import (
    SkillBundle,
    SkillFile,
    build_bundle,
    build_bundle_from_file_map,  # noqa: F401  (Task 4 用)
)
from server.app.shared.errors import ValidationError

# 上传 zip 压缩后体积上限:几个 md 几十 KB 足够,2MB 防滥用
LOOP_SKILL_MAX_ZIP_BYTES = 2 * 1024 * 1024
# 单条目解压后上限(照搬 accounts import 范式)
LOOP_SKILL_MAX_ENTRY_BYTES = 2 * 1024 * 1024
# 解压后累计体积上限(防高压缩比 zip bomb:2MB 压缩包可膨胀到 GB 级)
LOOP_SKILL_MAX_TOTAL_UNCOMPRESSED = 4 * 1024 * 1024
# 条目数上限(白名单不限数量,靠它挡"海量小文件"膨胀)
LOOP_SKILL_MAX_ENTRIES = 50
# version_label / notes 长度上限(且拒控制字符,防 header 注入 + DB 膨胀)
LOOP_SKILL_MAX_LABEL_LEN = 200
LOOP_SKILL_MAX_NOTES_LEN = 500
# zip 路径白名单
_ALLOWED_TOP = ("README.md",)
_ALLOWED_PREFIXES = ("commands/", "skills/")
_REQUIRED = ("commands/goal.md", "skills/geo-goal-orchestrator/SKILL.md")
# 启用/删除共用的应用级锁名
_ENABLE_LOCK = "geo_loop_skill_enable"


@dataclass(frozen=True)
class VersionMeta:
    id: int
    version_label: str
    bundle_sha256: str
    file_count: int
    total_size: int
    is_enabled: bool
    is_deleted: bool
    uploaded_by_user_id: int | None
    notes: str | None
    created_at: datetime


def _to_meta(row: LoopSkillBundleVersion) -> VersionMeta:
    # 只读非 deferred 列 —— 不触发 files 加载
    return VersionMeta(
        id=row.id,
        version_label=row.version_label,
        bundle_sha256=row.bundle_sha256,
        file_count=row.file_count,
        total_size=row.total_size,
        is_enabled=row.is_enabled,
        is_deleted=row.is_deleted,
        uploaded_by_user_id=row.uploaded_by_user_id,
        notes=row.notes,
        created_at=row.created_at,
    )


def _row_to_bundle(row: LoopSkillBundleVersion) -> SkillBundle:
    files = [SkillFile(**f) for f in row.files]  # 此处才触发 deferred files 加载
    return SkillBundle(version=row.version_label, bundle_sha256=row.bundle_sha256, files=files)


def get_active_bundle(session: Session) -> SkillBundle:
    # order_by(id.desc()).first() —— 不用 .one():容忍并发窗口内瞬时双启用,取最新一条
    row = (
        session.execute(
            select(LoopSkillBundleVersion)
            .where(LoopSkillBundleVersion.is_enabled.is_(True))
            .where(LoopSkillBundleVersion.is_deleted.is_(False))
            .order_by(LoopSkillBundleVersion.id.desc())
        )
        .scalars()
        .first()
    )
    return _row_to_bundle(row) if row is not None else build_bundle()


def get_bundle_by_id(session: Session, version_id: int) -> SkillBundle:
    row = session.get(LoopSkillBundleVersion, version_id)
    if row is None or row.is_deleted:
        raise ValidationError(f"skill 包版本不存在或已删除: {version_id}")
    return _row_to_bundle(row)


def resolve_bundle_for_install(session: Session, version: str | None) -> SkillBundle:
    """install/download 的版本解析:空→启用版;纯数字→id;否则按 label 取最新未删。"""
    if not version:
        return get_active_bundle(session)
    if version.isdigit():
        return get_bundle_by_id(session, int(version))
    row = (
        session.execute(
            select(LoopSkillBundleVersion)
            .where(LoopSkillBundleVersion.version_label == version)
            .where(LoopSkillBundleVersion.is_deleted.is_(False))
            .order_by(LoopSkillBundleVersion.id.desc())
        )
        .scalars()
        .first()
    )
    if row is None:
        raise ValidationError(f"找不到 skill 包版本: {version}")
    return _row_to_bundle(row)


def list_versions(session: Session) -> list[VersionMeta]:
    rows = (
        session.execute(
            select(LoopSkillBundleVersion)
            .where(LoopSkillBundleVersion.is_deleted.is_(False))
            .order_by(LoopSkillBundleVersion.id.desc())
        )
        .scalars()
        .all()
    )
    return [_to_meta(r) for r in rows]
