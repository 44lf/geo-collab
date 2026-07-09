"""Task 7 兼容性测试:旧 /loop-skill-bundle/* 只读端点作为别名,继续可用,
内部改读新多 skill 库(`skill_service`)里的官方 skill(slug="goal")当前版本。
"""

from __future__ import annotations

import io
import zipfile

import pytest

pytestmark = pytest.mark.mysql


def _mcp_get(test_app, path: str, monkeypatch, params: dict | None = None):
    """带 MCP token 的 GET(写法沿用 test_loop_skill_bundle_versions.py 里的现成模式)。"""
    monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
    from server.app.core import config

    config.get_settings.cache_clear()
    return test_app.client.get(path, params=params or {}, headers={"X-MCP-Token": "secret"})


def test_old_install_payload_alias_resolves_to_official(monkeypatch):
    """旧 /api/mcp/loop-skill-bundle/install-payload 仍返回官方 skill 当前版本内容。"""
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        from server.app.db.session import SessionLocal
        from server.app.modules.loop_skills import skill_service as svc

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("commands/goal.md", "x")
            zf.writestr("skills/geo-goal-orchestrator/SKILL.md", "y")
        db = SessionLocal()
        try:
            sk, _version_row = svc.create_version(
                db, entries=[("b.zip", buf.getvalue())], name="/goal loop skills", uploaded_by=None
            )
            sk.slug = "goal"
            sk.is_official = True
            db.commit()
        finally:
            db.close()

        r = _mcp_get(app, "/api/mcp/loop-skill-bundle/install-payload", monkeypatch)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert any(f["path"] == "commands/goal.md" for f in body["data"]["files"])
        assert any(
            f["path"] == "skills/geo-goal-orchestrator/SKILL.md" for f in body["data"]["files"]
        )
    finally:
        app.cleanup()


def test_old_info_alias_resolves_to_official(monkeypatch):
    """旧 /api/mcp/loop-skill-bundle/info(user JWT)同样改读官方 skill 当前版本。"""
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        from server.app.db.session import SessionLocal
        from server.app.modules.loop_skills import skill_service as svc

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("skills/geo-goal-orchestrator/SKILL.md", "content-v1")
        db = SessionLocal()
        try:
            sk, _version_row = svc.create_version(
                db, entries=[("b.zip", buf.getvalue())], name="/goal loop skills", uploaded_by=None
            )
            sk.slug = "goal"
            sk.is_official = True
            db.commit()
        finally:
            db.close()

        r = app.client.get("/api/mcp/loop-skill-bundle/info")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["version"] == "v1"
        assert body["files"][0]["path"] == "skills/geo-goal-orchestrator/SKILL.md"
    finally:
        app.cleanup()


def test_old_endpoints_without_official_skill_do_not_crash(monkeypatch):
    """官方 skill 还没 seed(Task 8 之前)时,旧读端点不应 500——info/download.zip
    走全局 400(ValidationError),install-payload 走 200 + ok:false + available。"""
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        assert app.client.get("/api/mcp/loop-skill-bundle/info").status_code == 400
        assert app.client.get("/api/mcp/loop-skill-bundle/download.zip").status_code == 400

        r = _mcp_get(app, "/api/mcp/loop-skill-bundle/install-payload", monkeypatch)
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False
        assert isinstance(body["data"]["available"], list)
    finally:
        app.cleanup()
