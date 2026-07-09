"""多 skill 库 HTTP 路由测试：/api/mcp/skills/*（上传/列表/版本/回滚/下载/删除）。"""

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


def test_upload_list_setcurrent_download(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        c = app.client  # TestClient，已带 admin JWT cookie
        # 上传 v1
        r = c.post(
            "/api/mcp/skills/upload",
            files={"files": ("b.zip", _zip({"SKILL.md": b"one"}), "application/zip")},
            data={"name": "writer"},
        )
        assert r.status_code == 200, r.text
        sid = r.json()["skill_id"]
        assert r.json()["version_label"] == "v1"

        # 列表含官方 goal(seed 后) + writer；此处至少含 writer
        r = c.get("/api/mcp/skills")
        assert r.status_code == 200
        assert any(s["slug"] for s in r.json()["skills"])

        # 上传 v2 → 版本历史 2 条
        c.post(
            "/api/mcp/skills/upload",
            files={"files": ("b.zip", _zip({"SKILL.md": b"two"}), "application/zip")},
            data={"name": "writer"},
        )
        r = c.get(f"/api/mcp/skills/{sid}/versions")
        labels = [v["version_label"] for v in r.json()["versions"]]
        assert labels == ["v2", "v1"]
        v1_id = [v["id"] for v in r.json()["versions"] if v["version_label"] == "v1"][0]

        # 回滚到 v1
        r = c.post(f"/api/mcp/skills/{sid}/set-current", json={"version_id": v1_id})
        assert r.status_code == 204

        # 下载当前版本 zip
        r = c.get(f"/api/mcp/skills/{sid}/download.zip")
        assert r.status_code == 200
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        assert zf.read("SKILL.md") == b"one"

        # 删当前版本 → 409
        r = c.delete(f"/api/mcp/skills/{sid}/versions/{v1_id}")
        assert r.status_code == 409
    finally:
        app.cleanup()


def test_operator_cannot_delete_others_version_403(monkeypatch):
    """非 admin、非属主 删除他人上传的非当前版本 → 403（M-5：越权删版本 HTTP 回归）。"""
    from server.tests.utils import build_test_app, create_extra_user

    app = build_test_app(monkeypatch)
    try:
        c = app.client  # admin，已带 admin JWT cookie
        r = c.post(
            "/api/mcp/skills/upload",
            files={"files": ("b.zip", _zip({"SKILL.md": b"one"}), "application/zip")},
            data={"name": "custom-pkg"},
        )
        assert r.status_code == 200, r.text
        sid = r.json()["skill_id"]

        r = c.post(
            "/api/mcp/skills/upload",
            files={"files": ("b.zip", _zip({"SKILL.md": b"two"}), "application/zip")},
            data={"name": "custom-pkg"},
        )
        assert r.status_code == 200, r.text

        r = c.get(f"/api/mcp/skills/{sid}/versions")
        v1_id = [v["id"] for v in r.json()["versions"] if v["version_label"] == "v1"][0]

        _uid, op_client = create_extra_user(app, "op-del-others")
        r = op_client.delete(f"/api/mcp/skills/{sid}/versions/{v1_id}")
        assert r.status_code == 403, r.text
    finally:
        app.cleanup()


def test_operator_cannot_delete_official_version_403(monkeypatch):
    """非 admin 删官方包版本 → 403，即使目标不是当前版本（越过所有权检查同样拦）。"""
    from server.app.modules.loop_skills import skill_service as svc
    from server.tests.utils import build_test_app, create_extra_user

    app = build_test_app(monkeypatch)
    try:
        from server.app.db.session import SessionLocal

        db = SessionLocal()
        try:
            sk, v1 = svc.create_version(
                db,
                entries=[("b.zip", _zip({"SKILL.md": b"1"}))],
                name="official-pkg-del",
                uploaded_by=None,
            )
            sk.is_official = True
            db.commit()
            sid = sk.id
            v1_id = v1.id
            # 追加 v2 当 current，让 v1 成为可删的非当前版本
            svc.create_version(
                db,
                entries=[("b.zip", _zip({"SKILL.md": b"2"}))],
                name="official-pkg-del",
                uploaded_by=None,
                is_admin=True,
            )
            db.commit()
        finally:
            db.close()

        _uid, op_client = create_extra_user(app, "op-del-official")
        r = op_client.delete(f"/api/mcp/skills/{sid}/versions/{v1_id}")
        assert r.status_code == 403, r.text
    finally:
        app.cleanup()


def test_upload_with_category(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        c = app.client
        r = c.post(
            "/api/mcp/skills/upload",
            files={"files": ("b.zip", _zip({"SKILL.md": b"one"}), "application/zip")},
            data={"name": "cat-writer", "category": "distribute"},
        )
        assert r.status_code == 200, r.text
        skills = c.get("/api/mcp/skills").json()["skills"]
        row = next(s for s in skills if s["slug"] == r.json()["slug"])
        assert row["category"] == "distribute"

        # 不传 category → 默认 general
        r2 = c.post(
            "/api/mcp/skills/upload",
            files={"files": ("b.zip", _zip({"SKILL.md": b"x"}), "application/zip")},
            data={"name": "cat-default-api"},
        )
        skills2 = c.get("/api/mcp/skills").json()["skills"]
        row2 = next(s for s in skills2 if s["slug"] == r2.json()["slug"])
        assert row2["category"] == "general"
    finally:
        app.cleanup()


def test_mcp_list_skills_catalog(monkeypatch):
    from server.tests.utils import build_test_app

    HDR = {"X-MCP-Token": "secret"}
    app = build_test_app(monkeypatch)
    try:
        c = app.client
        # 无 MCP token → 401
        assert c.get("/api/mcp/skills/catalog").status_code == 401

        monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
        from server.app.core import config

        config.get_settings.cache_clear()

        c.post(
            "/api/mcp/skills/upload",
            files={
                "files": (
                    "b.zip",
                    _zip({"skills/w1/SKILL.md": b"x", "skills/w2/SKILL.md": b"y"}),
                    "application/zip",
                )
            },
            data={"name": "gen-pkg", "category": "generation"},
        )
        c.post(
            "/api/mcp/skills/upload",
            files={"files": ("b.zip", _zip({"SKILL.md": b"x"}), "application/zip")},
            data={"name": "dist-pkg", "category": "distribute"},
        )

        # 带 token → 200，返回全部
        r = c.get("/api/mcp/skills/catalog", headers=HDR)
        assert r.status_code == 200, r.text
        by_slug = {s["slug"]: s for s in r.json()["skills"]}
        assert "gen-pkg" in by_slug and "dist-pkg" in by_slug
        # units 提取
        assert sorted(by_slug["gen-pkg"]["units"]) == ["w1", "w2"]
        assert by_slug["dist-pkg"]["units"] == ["dist-pkg"]  # 单文件包回落 slug

        # category 筛
        r2 = c.get("/api/mcp/skills/catalog?category=distribute", headers=HDR)
        slugs = {s["slug"] for s in r2.json()["skills"]}
        assert "dist-pkg" in slugs and "gen-pkg" not in slugs
    finally:
        app.cleanup()


def test_operator_cannot_upload_official_name_403(monkeypatch):
    """非 admin 用官方包同名上传（追加版本）→ 403（I-1：官方包上传收 admin）。"""
    from server.app.modules.loop_skills import skill_service as svc
    from server.tests.utils import build_test_app, create_extra_user

    app = build_test_app(monkeypatch)
    try:
        from server.app.db.session import SessionLocal

        db = SessionLocal()
        try:
            sk, _v1 = svc.create_version(
                db,
                entries=[("b.zip", _zip({"SKILL.md": b"1"}))],
                name="official-pkg-upload",
                uploaded_by=None,
            )
            sk.is_official = True
            db.commit()
        finally:
            db.close()

        _uid, op_client = create_extra_user(app, "op-upload-official")
        r = op_client.post(
            "/api/mcp/skills/upload",
            files={"files": ("b.zip", _zip({"SKILL.md": b"two"}), "application/zip")},
            data={"name": "official-pkg-upload"},
        )
        assert r.status_code == 403, r.text
    finally:
        app.cleanup()


def test_add_version_endpoint_appends(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        c = app.client  # admin
        r = c.post(
            "/api/mcp/skills/upload",
            files={"files": ("b.zip", _zip({"SKILL.md": b"one"}), "application/zip")},
            data={"name": "endpoint-add"},
        )
        assert r.status_code == 200, r.text
        sid = r.json()["skill_id"]

        r = c.post(
            f"/api/mcp/skills/{sid}/versions",
            files={"files": ("b.zip", _zip({"SKILL.md": b"two"}), "application/zip")},
        )
        assert r.status_code == 200, r.text
        assert r.json()["version_label"] == "v2"
        assert r.json()["skill_id"] == sid

        labels = [
            v["version_label"] for v in c.get(f"/api/mcp/skills/{sid}/versions").json()["versions"]
        ]
        assert labels == ["v2", "v1"]
    finally:
        app.cleanup()


def test_add_version_endpoint_official_non_admin_403(monkeypatch):
    from server.app.modules.loop_skills import skill_service as svc
    from server.tests.utils import build_test_app, create_extra_user

    app = build_test_app(monkeypatch)
    try:
        from server.app.db.session import SessionLocal

        db = SessionLocal()
        try:
            sk, _ = svc.create_version(
                db,
                entries=[("b.zip", _zip({"SKILL.md": b"1"}))],
                name="off-endpoint",
                uploaded_by=None,
            )
            sk.is_official = True
            db.commit()
            sid = sk.id
        finally:
            db.close()

        _uid, op_client = create_extra_user(app, "op-add-official")
        r = op_client.post(
            f"/api/mcp/skills/{sid}/versions",
            files={"files": ("b.zip", _zip({"SKILL.md": b"2"}), "application/zip")},
        )
        assert r.status_code == 403, r.text
    finally:
        app.cleanup()


def test_add_version_endpoint_non_official_any_user(monkeypatch):
    from server.tests.utils import build_test_app, create_extra_user

    app = build_test_app(monkeypatch)
    try:
        c = app.client  # admin 建一个非官方包
        sid = c.post(
            "/api/mcp/skills/upload",
            files={"files": ("b.zip", _zip({"SKILL.md": b"one"}), "application/zip")},
            data={"name": "shared-pkg"},
        ).json()["skill_id"]

        _uid, op_client = create_extra_user(app, "op-add-shared")
        r = op_client.post(
            f"/api/mcp/skills/{sid}/versions",
            files={"files": ("b.zip", _zip({"SKILL.md": b"two"}), "application/zip")},
        )
        assert r.status_code == 200, r.text
        assert r.json()["version_label"] == "v2"
    finally:
        app.cleanup()


def test_add_version_endpoint_missing_skill_md_400(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        c = app.client
        sid = c.post(
            "/api/mcp/skills/upload",
            files={"files": ("b.zip", _zip({"SKILL.md": b"one"}), "application/zip")},
            data={"name": "no-skillmd-add"},
        ).json()["skill_id"]

        r = c.post(
            f"/api/mcp/skills/{sid}/versions",
            files={"files": ("b.zip", _zip({"README.md": b"x"}), "application/zip")},
        )
        assert r.status_code == 400, r.text
    finally:
        app.cleanup()


def test_add_version_endpoint_skill_not_found_400(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        c = app.client
        r = c.post(
            "/api/mcp/skills/999999/versions",
            files={"files": ("b.zip", _zip({"SKILL.md": b"x"}), "application/zip")},
        )
        assert r.status_code == 400, r.text
    finally:
        app.cleanup()
