"""把现有单 bundle 迁进新 Skill 库的官方 skill（slug=goal）。幂等：已存在即跳过。

部署时跑一次：python -m server.scripts.seed_skill_library
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

# 注册全模块 mapper（跨模块 relationship 需要）
import server.app.modules.accounts.models  # noqa: F401
import server.app.modules.articles.models  # noqa: F401
import server.app.modules.audit.models  # noqa: F401
import server.app.modules.image_library.models  # noqa: F401
import server.app.modules.tasks.models  # noqa: F401
from server.app.db.session import SessionLocal
from server.app.modules.loop_skills.models import LoopSkillBundleVersion, Skill, SkillVersion
from server.app.modules.loop_skills.service import build_bundle
from server.app.modules.loop_skills.version import LOOP_SKILL_BUNDLE_VERSION

OFFICIAL_NAME = "/goal loop skills"
OFFICIAL_SLUG = "goal"


def seed_skill_library(session: Session) -> None:
    exists = session.execute(
        select(Skill.id).where(Skill.slug == OFFICIAL_SLUG, Skill.is_deleted.is_(False))
    ).first()
    if exists:
        print(f"skill '{OFFICIAL_SLUG}' already seeded, skip")
        return

    skill = Skill(name=OFFICIAL_NAME, slug=OFFICIAL_SLUG, is_official=True, created_by=None)
    session.add(skill)
    session.flush()

    old_rows = (
        session.execute(
            select(LoopSkillBundleVersion)
            .where(LoopSkillBundleVersion.is_deleted.is_(False))
            .order_by(LoopSkillBundleVersion.id)
        )
        .scalars()
        .all()
    )

    current_id = None
    if old_rows:
        for old in old_rows:
            v = SkillVersion(
                skill_id=skill.id,
                version_label=old.version_label,
                bundle_sha256=old.bundle_sha256,
                file_count=old.file_count,
                total_bytes=old.total_size,
                storage_backend="db",
                files=old.files,
                storage_key=None,
                uploaded_by=old.uploaded_by_user_id,
            )
            session.add(v)
            session.flush()
            if old.is_enabled:
                current_id = v.id
        if current_id is None:
            current_id = v.id  # 无 enabled → 取最后一条
    else:
        bundle = build_bundle()
        v = SkillVersion(
            skill_id=skill.id,
            version_label=LOOP_SKILL_BUNDLE_VERSION,
            bundle_sha256=bundle.bundle_sha256,
            file_count=len(bundle.files),
            total_bytes=sum(f.size for f in bundle.files),
            storage_backend="db",
            files=[
                {"path": f.path, "content": f.content, "sha256": f.sha256, "size": f.size}
                for f in bundle.files
            ],
            storage_key=None,
            uploaded_by=None,
        )
        session.add(v)
        session.flush()
        current_id = v.id

    skill.current_version_id = current_id
    session.flush()
    print(f"seeded official skill '{OFFICIAL_SLUG}' with current version id={current_id}")


def main() -> None:
    db = SessionLocal()
    try:
        seed_skill_library(db)
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    main()
