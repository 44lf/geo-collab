from __future__ import annotations

import pytest

from server.app.core import config
from server.app.modules.image_library.models import StockCategory, StockImage
from server.tests.utils import build_test_app


@pytest.mark.mysql
def test_list_stock_images_requires_token(monkeypatch):
    test_app = build_test_app(monkeypatch)
    try:
        # No token should return 401
        resp = test_app.client.get("/api/mcp/stock-images?category_id=1")
        assert resp.status_code == 401
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_list_stock_images_returns_briefs(monkeypatch):
    # Setup MCP token
    monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
    config.get_settings.cache_clear()

    test_app = build_test_app(monkeypatch)
    try:
        # Seed a StockCategory and StockImage
        db = test_app.session_factory()
        try:
            cat = StockCategory(
                name="测试栏目", bucket_name="test-bucket-xyz", kind="companion"
            )
            db.add(cat)
            db.commit()
            db.refresh(cat)

            img = StockImage(
                category_id=cat.id,
                minio_key="k1.jpg",
                filename="k1.jpg",
                tags=["标签"],
                width=1280,
                height=720,
            )
            db.add(img)
            db.commit()
            db.refresh(img)

            category_id = cat.id
            asset_id = img.id
        finally:
            db.close()

        # Test the endpoint
        resp = test_app.client.get(
            f"/api/mcp/stock-images?category_id={category_id}",
            headers={"X-MCP-Token": "secret"},
        )
        assert resp.status_code == 200
        items = resp.json()
        assert any(it["asset_id"] == asset_id for it in items)

        # Verify the first item has all required keys
        first = items[0]
        assert set(first) >= {"asset_id", "filename", "tags", "url", "w", "h"}
        assert first["url"].startswith("/api/stock-images/")
    finally:
        test_app.cleanup()
