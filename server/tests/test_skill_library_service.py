import io
import zipfile

import pytest

pytestmark = pytest.mark.mysql


def _zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for k, v in files.items():
            zf.writestr(k, v)
    return buf.getvalue()


def _db():
    from server.app.db.session import SessionLocal

    return SessionLocal()


def test_create_then_append_version(monkeypatch):
    from server.app.modules.loop_skills import skill_service as svc
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        db = _db()
        try:
            sk, v1 = svc.create_version(
                db, entries=[("b.zip", _zip({"SKILL.md": b"one"}))], name="writer", uploaded_by=None
            )
            db.commit()
            assert v1.version_label == "v1"
            assert sk.current_version_id == v1.id

            sk2, v2 = svc.create_version(
                db, entries=[("b.zip", _zip({"SKILL.md": b"two"}))], name="writer", uploaded_by=None
            )
            db.commit()
            assert sk2.id == sk.id  # 同名追加
            assert v2.version_label == "v2"
            assert sk2.current_version_id == v2.id  # 当前指针移到 v2

            items = svc.list_skills(db)
            assert any(it.slug == sk.slug and it.current_version_label == "v2" for it in items)

            bundle = svc.get_current_bundle(db, sk.slug)
            assert bundle.files[0].content == "two"
        finally:
            db.close()
    finally:
        app.cleanup()


def test_create_rejects_missing_skill_md(monkeypatch):
    from server.app.modules.loop_skills import skill_service as svc
    from server.app.shared.errors import ValidationError
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        db = _db()
        try:
            with pytest.raises(ValidationError):
                svc.create_version(
                    db, entries=[("b.zip", _zip({"README.md": b"x"}))], name="bad", uploaded_by=None
                )
        finally:
            db.close()
    finally:
        app.cleanup()
