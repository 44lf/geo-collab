import pytest


@pytest.mark.mysql
def test_game_library_endpoints_auth(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
        from server.app.core import config

        config.get_settings.cache_clear()

        assert app.client.get("/api/mcp/game-library/tags").status_code == 401

        r = app.client.get(
            "/api/mcp/game-library/tags",
            headers={"X-MCP-Token": "secret"},
        )
        assert r.status_code == 200 and isinstance(r.json(), list)

        r2 = app.client.post(
            "/api/mcp/game-library/query",
            json={"relevant_tags": ["经营"]},
            headers={"X-MCP-Token": "secret"},
        )
        assert r2.status_code == 200 and isinstance(r2.json(), list)
    finally:
        app.cleanup()


def test_mcp_tools_count_is_39():
    from server.app.modules.mcp_catalog.connect_router import MCP_TOOLS_COUNT

    assert MCP_TOOLS_COUNT == 39
