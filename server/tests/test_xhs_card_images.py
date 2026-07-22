import pytest

pytestmark = pytest.mark.mysql


def test_search_and_store_web_image(monkeypatch):
    from server.tests.utils import build_test_app

    test_app = build_test_app(monkeypatch)
    try:
        from server.app.modules.image_library import service
        from server.app.shared import baidu

        class Cand:
            url = "http://x/a.jpg"
            source_url = "http://x/a"
            width = 800
            height = 450

        monkeypatch.setattr(baidu, "search_landscape_images", lambda kw, **k: [Cand()])
        monkeypatch.setattr(baidu, "download_image", lambda url: (b"\x89PNGxxxx", "image/jpeg"))
        with test_app.session_factory() as db:
            res = service.search_and_store_web_image(db, "餐厅养成记")
        assert res is not None
        url, sid = res
        assert url.startswith("/api/stock-images/") and url.endswith("/file")

        # 搜不到 → None
        monkeypatch.setattr(baidu, "search_landscape_images", lambda kw, **k: [])
        with test_app.session_factory() as db:
            assert service.search_and_store_web_image(db, "无此游戏") is None
    finally:
        test_app.cleanup()


def test_search_web_image_endpoint_requires_token(monkeypatch):
    from server.tests.utils import build_test_app

    test_app = build_test_app(monkeypatch)
    try:
        r = test_app.client.post("/api/mcp/search-web-image", json={"keyword": "x"})
        assert r.status_code == 401
    finally:
        test_app.cleanup()


def test_search_web_image_endpoint(monkeypatch):
    from server.tests.utils import build_test_app

    test_app = build_test_app(monkeypatch)
    try:
        monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
        from server.app.core import config

        config.get_settings.cache_clear()

        from server.app.modules.image_library import mcp_router

        monkeypatch.setattr(
            mcp_router, "search_and_store_web_image", lambda db, kw: ("/api/stock-images/5/file", 5)
        )
        r = test_app.client.post(
            "/api/mcp/search-web-image",
            json={"keyword": "餐厅养成记"},
            headers={"X-MCP-Token": "secret"},
        )
        assert r.status_code == 200
        assert r.json()["data"]["url"] == "/api/stock-images/5/file"
        # 搜不到 → url null
        monkeypatch.setattr(mcp_router, "search_and_store_web_image", lambda db, kw: None)
        r2 = test_app.client.post(
            "/api/mcp/search-web-image",
            json={"keyword": "x"},
            headers={"X-MCP-Token": "secret"},
        )
        assert r2.status_code == 200 and r2.json()["data"]["url"] is None
    finally:
        test_app.cleanup()
