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


def test_set_current_and_delete_matrix(monkeypatch):
    from server.app.modules.loop_skills import skill_service as svc
    from server.app.shared.errors import ClientError, ConflictError
    from server.tests.utils import build_test_app, create_extra_user

    app = build_test_app(monkeypatch)
    try:
        # uploaded_by / created_by 都有 FK 指向 users.id：build_test_app 只造了 admin，
        # 这里再造一个真实用户当 v2 的属主，避免 IntegrityError(#1452)。
        uid2, _ = create_extra_user(app, "skilluploader")
        db = _db()
        try:
            sk, v1 = svc.create_version(
                db,
                entries=[("b.zip", _zip({"SKILL.md": b"1"}))],
                name="w",
                uploaded_by=app.admin_id,
            )
            _, v2 = svc.create_version(
                db, entries=[("b.zip", _zip({"SKILL.md": b"2"}))], name="w", uploaded_by=uid2
            )
            db.commit()

            # 回滚到 v1
            svc.set_current(db, sk.id, v1.id)
            db.commit()
            db.refresh(sk)
            assert sk.current_version_id == v1.id

            # 当前版本(v1)不可删 → 409
            with pytest.raises(ConflictError):
                svc.delete_version(db, sk.id, v1.id, user_id=app.admin_id, is_admin=False)

            # 非属主删非当前版本(v2 由 uid2 上传) → 403 语义(ClientError)
            with pytest.raises(ClientError):
                svc.delete_version(db, sk.id, v2.id, user_id=app.admin_id, is_admin=False)

            # admin 删非当前版本 OK
            svc.delete_version(db, sk.id, v2.id, user_id=app.admin_id, is_admin=True)
            db.commit()
            assert all(x.version_label != "v2" for x in svc.list_versions(db, sk.id))
        finally:
            db.close()
    finally:
        app.cleanup()


def test_create_version_category_default_and_custom(monkeypatch):
    import io
    import zipfile

    from server.app.db.session import SessionLocal
    from server.app.modules.loop_skills import skill_service as svc
    from server.tests.utils import build_test_app

    def _zip(files):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for k, v in files.items():
                zf.writestr(k, v)
        return buf.getvalue()

    app = build_test_app(monkeypatch)
    try:
        db = SessionLocal()
        try:
            sk_def, _ = svc.create_version(
                db,
                entries=[("b.zip", _zip({"SKILL.md": b"x"}))],
                name="cat-default",
                uploaded_by=None,
            )
            assert sk_def.category == "general"

            sk_gen, _ = svc.create_version(
                db,
                entries=[("b.zip", _zip({"SKILL.md": b"x"}))],
                name="cat-gen",
                uploaded_by=None,
                category="generation",
            )
            assert sk_gen.category == "generation"
            db.commit()

            items = {it.name: it for it in svc.list_skills(db)}
            assert items["cat-gen"].category == "generation"
        finally:
            db.close()
    finally:
        app.cleanup()


def test_extract_unit_names():
    from server.app.modules.loop_skills.service import SkillFile
    from server.app.modules.loop_skills.skill_service import extract_unit_names

    multi = [
        SkillFile(path="README.md", size=1, sha256="a", content="x"),
        SkillFile(path="commands/goal.md", size=1, sha256="b", content="x"),
        SkillFile(path="skills/geo-goal-orchestrator/SKILL.md", size=1, sha256="c", content="x"),
        SkillFile(path="skills/geo-article-writer/SKILL.md", size=1, sha256="d", content="x"),
    ]
    assert extract_unit_names(multi, fallback_slug="goal") == [
        "geo-goal-orchestrator",
        "geo-article-writer",
    ]

    single = [SkillFile(path="SKILL.md", size=1, sha256="e", content="x")]
    assert extract_unit_names(single, fallback_slug="my-writer") == ["my-writer"]


def test_create_version_rejects_bad_category(monkeypatch):
    import io
    import zipfile

    import pytest

    from server.app.db.session import SessionLocal
    from server.app.modules.loop_skills import skill_service as svc
    from server.app.shared.errors import ValidationError
    from server.tests.utils import build_test_app

    def _zip(files):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for k, v in files.items():
                zf.writestr(k, v)
        return buf.getvalue()

    app = build_test_app(monkeypatch)
    try:
        db = SessionLocal()
        try:
            with pytest.raises(ValidationError):
                svc.create_version(
                    db,
                    entries=[("b.zip", _zip({"SKILL.md": b"x"}))],
                    name="cat-bad",
                    uploaded_by=None,
                    category="nonsense",
                )
        finally:
            db.close()
    finally:
        app.cleanup()


def test_add_version_appends_and_switches_current(monkeypatch):
    from server.app.modules.loop_skills import skill_service as svc
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        db = _db()
        try:
            sk, v1 = svc.create_version(
                db, entries=[("b.zip", _zip({"SKILL.md": b"one"}))], name="w-add", uploaded_by=None
            )
            db.commit()

            sk2, v2 = svc.add_version(
                db,
                skill_id=sk.id,
                entries=[("b.zip", _zip({"SKILL.md": b"two"}))],
                uploaded_by=None,
            )
            db.commit()
            assert sk2.id == sk.id
            assert v2.version_label == "v2"
            assert sk2.current_version_id == v2.id
            assert svc.get_current_bundle(db, sk.slug).files[0].content == "two"
        finally:
            db.close()
    finally:
        app.cleanup()


def test_add_version_official_requires_admin(monkeypatch):
    from server.app.modules.loop_skills import skill_service as svc
    from server.app.shared.errors import ClientError
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        db = _db()
        try:
            sk, _ = svc.create_version(
                db, entries=[("b.zip", _zip({"SKILL.md": b"1"}))], name="off-add", uploaded_by=None
            )
            sk.is_official = True
            db.commit()

            # 非 admin 追加官方包 → ClientError
            with pytest.raises(ClientError):
                svc.add_version(
                    db,
                    skill_id=sk.id,
                    entries=[("b.zip", _zip({"SKILL.md": b"2"}))],
                    uploaded_by=None,
                    is_admin=False,
                )
            # admin 追加官方包 → OK
            _, v2 = svc.add_version(
                db,
                skill_id=sk.id,
                entries=[("b.zip", _zip({"SKILL.md": b"2"}))],
                uploaded_by=None,
                is_admin=True,
            )
            db.commit()
            assert v2.version_label == "v2"
        finally:
            db.close()
    finally:
        app.cleanup()


def test_add_version_missing_skill_raises(monkeypatch):
    from server.app.modules.loop_skills import skill_service as svc
    from server.app.shared.errors import ValidationError
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        db = _db()
        try:
            with pytest.raises(ValidationError):
                svc.add_version(
                    db,
                    skill_id=999999,
                    entries=[("b.zip", _zip({"SKILL.md": b"x"}))],
                    uploaded_by=None,
                )
        finally:
            db.close()
    finally:
        app.cleanup()


def test_add_version_complete_replacement(monkeypatch):
    """追加时是完全替换：上传 1 文件到原 3 文件包 → 新版本只有 1 文件（无 auto-inherit）。"""
    from server.app.modules.loop_skills import skill_service as svc
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        db = _db()
        try:
            sk, _ = svc.create_version(
                db,
                entries=[
                    (
                        "b.zip",
                        _zip(
                            {
                                "SKILL.md": b"a",
                                "commands/goal.md": b"b",
                                "README.md": b"c",
                            }
                        ),
                    )
                ],
                name="repl-add",
                uploaded_by=None,
            )
            db.commit()

            _, v2 = svc.add_version(
                db,
                skill_id=sk.id,
                entries=[("b.zip", _zip({"SKILL.md": b"only"}))],
                uploaded_by=None,
            )
            db.commit()
            assert v2.file_count == 1
            bundle = svc.get_current_bundle(db, sk.slug)
            assert [f.path for f in bundle.files] == ["SKILL.md"]
        finally:
            db.close()
    finally:
        app.cleanup()
