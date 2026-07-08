import pytest

pytestmark = pytest.mark.mysql


def test_skill_and_version_roundtrip(monkeypatch):
    from server.app.modules.loop_skills.models import Skill, SkillVersion
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        from server.app.db.session import SessionLocal

        db = SessionLocal()
        try:
            sk = Skill(name="/goal loop skills", slug="goal", is_official=True, created_by=None)
            db.add(sk)
            db.flush()
            v = SkillVersion(
                skill_id=sk.id,
                version_label="v1",
                bundle_sha256="a" * 64,
                file_count=1,
                total_bytes=10,
                storage_backend="db",
                files=[{"path": "SKILL.md", "content": "x", "sha256": "a" * 64, "size": 1}],
                storage_key=None,
                uploaded_by=None,
            )
            db.add(v)
            db.flush()
            sk.current_version_id = v.id
            db.commit()

            got = db.get(Skill, sk.id)
            assert got.slug == "goal"
            assert got.current_version_id == v.id
            assert db.get(SkillVersion, v.id).files[0]["path"] == "SKILL.md"
        finally:
            db.close()
    finally:
        app.cleanup()


def test_name_active_unique_allows_reupload_after_softdelete(monkeypatch):
    """未删记录内 name 唯一；软删后可重新建同名（生成列落 NULL 不冲突）。"""
    from server.app.modules.loop_skills.models import Skill
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        from server.app.db.session import SessionLocal

        db = SessionLocal()
        try:
            a = Skill(name="dup", slug="dup", is_official=False, created_by=None, is_deleted=True)
            db.add(a)
            db.commit()
            # 软删的同名可再建活跃记录
            b = Skill(name="dup", slug="dup", is_official=False, created_by=None, is_deleted=False)
            db.add(b)
            db.commit()
            assert b.id != a.id
        finally:
            db.close()
    finally:
        app.cleanup()
