"""多 skill 库服务层：上传/追加版本、读取、列表、回滚、删除、权限。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.app.modules.loop_skills import storage, upload
from server.app.modules.loop_skills.models import Skill, SkillVersion
from server.app.modules.loop_skills.service import (
    SkillBundle,
    build_bundle_from_file_map,
)
from server.app.shared.errors import ClientError, ConflictError, ValidationError


@dataclass(frozen=True)
class SkillListItem:
    id: int
    name: str
    slug: str
    is_official: bool
    current_version_label: str | None
    file_count: int
    total_bytes: int
    updated_at: datetime
    uploaded_by: int | None


def slugify(name: str, session: Session) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "skill"
    slug = base
    n = 1
    while session.execute(
        select(Skill.id).where(Skill.slug == slug, Skill.is_deleted.is_(False))
    ).first():
        n += 1
        slug = f"{base}-{n}"
    return slug


def _active_skill_by_name(session: Session, name: str) -> Skill | None:
    return (
        session.execute(select(Skill).where(Skill.name == name, Skill.is_deleted.is_(False)))
        .scalars()
        .first()
    )


def _next_label(session: Session, skill_id: int) -> str:
    rows = (
        session.execute(select(SkillVersion.version_label).where(SkillVersion.skill_id == skill_id))
        .scalars()
        .all()
    )
    mx = 0
    for r in rows:
        m = re.match(r"v(\d+)$", r)
        if m:
            mx = max(mx, int(m.group(1)))
    return f"v{mx + 1}"


def create_version(
    session: Session, *, entries: list[tuple[str, bytes]], name: str, uploaded_by: int | None
) -> tuple[Skill, SkillVersion]:
    name = (name or "").strip()
    if not name:
        raise ValidationError("skill 名不能为空")
    raw = upload.parse_upload(entries)
    upload.validate_file_map(raw)
    bundle = build_bundle_from_file_map(raw, version="pending")

    skill = _active_skill_by_name(session, name)
    if skill is None:
        skill = Skill(
            name=name, slug=slugify(name, session), is_official=False, created_by=uploaded_by
        )
        session.add(skill)
        session.flush()

    version = SkillVersion(
        skill_id=skill.id,
        version_label=_next_label(session, skill.id),
        bundle_sha256=bundle.bundle_sha256,
        file_count=len(bundle.files),
        total_bytes=sum(f.size for f in bundle.files),
        storage_backend="db",
        files=[
            {"path": f.path, "content": f.content, "sha256": f.sha256, "size": f.size}
            for f in bundle.files
        ],
        storage_key=None,
        uploaded_by=uploaded_by,
    )
    session.add(version)
    session.flush()
    skill.current_version_id = version.id
    session.flush()
    return skill, version


def _version_to_bundle(skill: Skill, row: SkillVersion) -> SkillBundle:
    files = storage.load_version_files(row)
    return SkillBundle(version=row.version_label, bundle_sha256=row.bundle_sha256, files=files)


def _active_skill_by_slug(session: Session, slug: str) -> Skill:
    sk = (
        session.execute(select(Skill).where(Skill.slug == slug, Skill.is_deleted.is_(False)))
        .scalars()
        .first()
    )
    if sk is None:
        raise ValidationError(f"skill 不存在: {slug}")
    return sk


def get_current_bundle(session: Session, slug: str) -> SkillBundle:
    sk = _active_skill_by_slug(session, slug)
    if sk.current_version_id is None:
        raise ValidationError(f"skill 无当前版本: {slug}")
    row = session.get(SkillVersion, sk.current_version_id)
    if row is None:
        raise ValidationError(f"skill 当前版本记录缺失: {slug}")
    return _version_to_bundle(sk, row)


def get_skill_bundle_by_id(session: Session, skill_id: int) -> SkillBundle:
    sk = session.get(Skill, skill_id)
    if sk is None or sk.is_deleted or sk.current_version_id is None:
        raise ValidationError(f"skill 不存在或无当前版本: {skill_id}")
    row = session.get(SkillVersion, sk.current_version_id)
    if row is None:
        raise ValidationError(f"skill 当前版本记录缺失: {skill_id}")
    return _version_to_bundle(sk, row)


def list_skills(session: Session) -> list[SkillListItem]:
    skills = (
        session.execute(
            select(Skill)
            .where(Skill.is_deleted.is_(False))
            .order_by(Skill.is_official.desc(), Skill.id)
        )
        .scalars()
        .all()
    )
    items: list[SkillListItem] = []
    for sk in skills:
        cur = session.get(SkillVersion, sk.current_version_id) if sk.current_version_id else None
        items.append(
            SkillListItem(
                id=sk.id,
                name=sk.name,
                slug=sk.slug,
                is_official=sk.is_official,
                current_version_label=cur.version_label if cur else None,
                file_count=cur.file_count if cur else 0,
                total_bytes=cur.total_bytes if cur else 0,
                updated_at=sk.updated_at,
                uploaded_by=cur.uploaded_by if cur else None,
            )
        )
    return items


@dataclass(frozen=True)
class VersionItem:
    id: int
    version_label: str
    bundle_sha256: str
    file_count: int
    total_bytes: int
    uploaded_by: int | None
    uploaded_at: datetime
    is_current: bool


def list_versions(session: Session, skill_id: int) -> list[VersionItem]:
    sk = session.get(Skill, skill_id)
    if sk is None or sk.is_deleted:
        raise ValidationError(f"skill 不存在: {skill_id}")
    rows = (
        session.execute(
            select(SkillVersion)
            .where(SkillVersion.skill_id == skill_id, SkillVersion.is_deleted.is_(False))
            .order_by(SkillVersion.id.desc())
        )
        .scalars()
        .all()
    )
    return [
        VersionItem(
            id=r.id,
            version_label=r.version_label,
            bundle_sha256=r.bundle_sha256,
            file_count=r.file_count,
            total_bytes=r.total_bytes,
            uploaded_by=r.uploaded_by,
            uploaded_at=r.uploaded_at,
            is_current=(r.id == sk.current_version_id),
        )
        for r in rows
    ]


def set_current(session: Session, skill_id: int, version_id: int) -> None:
    sk = session.get(Skill, skill_id)
    if sk is None or sk.is_deleted:
        raise ValidationError(f"skill 不存在: {skill_id}")
    v = session.get(SkillVersion, version_id)
    if v is None or v.is_deleted or v.skill_id != skill_id:
        raise ValidationError(f"版本不存在: {version_id}")
    sk.current_version_id = version_id
    session.flush()


def delete_version(
    session: Session, skill_id: int, version_id: int, *, user_id: int | None, is_admin: bool
) -> None:
    sk = session.get(Skill, skill_id)
    if sk is None or sk.is_deleted:
        raise ValidationError(f"skill 不存在: {skill_id}")
    v = session.get(SkillVersion, version_id)
    if v is None or v.is_deleted or v.skill_id != skill_id:
        raise ValidationError(f"版本不存在: {version_id}")
    if sk.current_version_id == version_id:
        raise ConflictError("不能删除当前版本，请先切到别的版本再删")
    # 权限：官方包版本仅 admin；否则 属主 或 admin
    if sk.is_official and not is_admin:
        raise ClientError("官方包版本仅管理员可删")
    if not is_admin and v.uploaded_by != user_id:
        raise ClientError("只能删除自己上传的版本")
    v.is_deleted = True
    session.flush()


def delete_skill(session: Session, skill_id: int) -> None:
    """软删整个 skill（router 侧已 require_admin）。"""
    sk = session.get(Skill, skill_id)
    if sk is None or sk.is_deleted:
        raise ValidationError(f"skill 不存在: {skill_id}")
    sk.is_deleted = True
    session.flush()
