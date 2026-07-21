"""xhs_cards 路由测试：MCP token 鉴权 + compose/status 往返。"""

from __future__ import annotations

import pytest

from server.tests.utils import build_test_app

pytestmark = pytest.mark.mysql


def test_compose_requires_mcp_token(monkeypatch):
    test_app = build_test_app(monkeypatch)
    try:
        monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
        from server.app.core import config

        config.get_settings.cache_clear()

        resp = test_app.client.post("/api/xhs-cards/compose", json={"render_markdown": "x"})
        assert resp.status_code == 401
    finally:
        test_app.cleanup()


def test_compose_and_status(monkeypatch):
    test_app = build_test_app(monkeypatch)
    try:
        monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
        from server.app.core import config

        config.get_settings.cache_clear()

        monkeypatch.setattr(
            "server.app.modules.xhs_cards.router.spawn_render_job", lambda jid: None
        )
        headers = {"X-MCP-Token": "secret"}
        r = test_app.client.post(
            "/api/xhs-cards/compose",
            json={
                "render_markdown": "---\ntitle: T\n---\nA",
                "theme": "sketch",
                "mode": "separator",
            },
            headers=headers,
        )
        assert r.status_code == 202
        jid = r.json()["data"]["job_id"]
        s = test_app.client.get(f"/api/xhs-cards/status/{jid}", headers=headers)
        assert s.status_code == 200 and s.json()["data"]["status"] == "pending"
    finally:
        test_app.cleanup()
