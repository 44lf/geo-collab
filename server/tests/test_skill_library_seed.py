import pytest

pytestmark = pytest.mark.mysql


def test_seed_from_existing_bundle_rows(monkeypatch):
    from server.app.modules.loop_skills.models import LoopSkillBundleVersion, Skill
    from server.scripts.seed_skill_library import seed_skill_library
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        from server.app.db.session import SessionLocal

        db = SessionLocal()
        try:
            db.add(
                LoopSkillBundleVersion(
                    version_label="2026-07-07-v12",
                    bundle_sha256="a" * 64,
                    files=[
                        {"path": "commands/goal.md", "content": "x", "sha256": "a" * 64, "size": 1}
                    ],
                    file_count=1,
                    total_size=1,
                    is_enabled=True,
                    is_deleted=False,
                    uploaded_by_user_id=None,
                    notes=None,
                )
            )
            db.commit()

            seed_skill_library(db)
            db.commit()

            goal = db.query(Skill).filter(Skill.slug == "goal").one()
            assert goal.is_official is True
            assert goal.current_version_id is not None

            # 幂等：再跑一次不重复建
            seed_skill_library(db)
            db.commit()
            assert db.query(Skill).filter(Skill.slug == "goal").count() == 1
        finally:
            db.close()
    finally:
        app.cleanup()


def test_seed_empty_falls_back_to_templates(monkeypatch):
    from server.app.modules.loop_skills.models import Skill, SkillVersion
    from server.scripts.seed_skill_library import seed_skill_library
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        from server.app.db.session import SessionLocal

        db = SessionLocal()
        try:
            seed_skill_library(db)
            db.commit()
            goal = db.query(Skill).filter(Skill.slug == "goal").one()
            v = db.get(SkillVersion, goal.current_version_id)
            assert v.file_count >= 1  # templates 灌入
        finally:
            db.close()
    finally:
        app.cleanup()


def test_seed_sets_goal_category_generation(monkeypatch):
    from server.app.db.session import SessionLocal
    from server.app.modules.loop_skills import skill_service as svc
    from server.scripts.seed_skill_library import seed_skill_library
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        db = SessionLocal()
        try:
            seed_skill_library(db)
            db.commit()
            goal = next(it for it in svc.list_skills(db) if it.slug == "goal")
            assert goal.category == "generation"
        finally:
            db.close()
    finally:
        app.cleanup()
