"""video 路由测试：MCP token 鉴权 + compose/status 往返。"""

from __future__ import annotations

import json

import pytest

from server.tests.utils import build_test_app


@pytest.mark.mysql
def test_compose_requires_mcp_token(monkeypatch):
    test_app = build_test_app(monkeypatch)
    try:
        monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
        from server.app.core import config

        config.get_settings.cache_clear()

        resp = test_app.client.post("/api/videos/compose", json={"article_id": 1, "storyboard": {}})
        assert resp.status_code == 401
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_compose_and_status_roundtrip(monkeypatch):
    test_app = build_test_app(monkeypatch)
    try:
        monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
        from server.app.core import config

        config.get_settings.cache_clear()

        from server.app.modules.articles.models import Article
        from server.app.modules.image_library.models import StockCategory, StockImage

        db = test_app.session_factory()
        try:
            article = Article(
                user_id=test_app.admin_id,
                title="test",
                content_json=json.dumps({"type": "doc", "content": []}),
                content_html="",
                plain_text="正文",
                word_count=0,
                status="draft",
                review_status="pending",
            )
            db.add(article)
            db.commit()
            article_id = article.id

            cat = StockCategory(name="测试栏目", bucket_name="test-bucket-xyz", kind="companion")
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
            asset_id = img.id
        finally:
            db.close()

        # 不真正起渲染线程：mock spawn 为 no-op
        monkeypatch.setattr("server.app.modules.video.router.spawn_video_job", lambda jid: None)

        headers = {"X-MCP-Token": "secret"}
        body = {
            "article_id": article_id,
            "storyboard": {
                "title": "标题",
                "shots": [{"subtitle": "a", "narration": "a", "asset_id": asset_id}],
            },
        }
        resp = test_app.client.post("/api/videos/compose", json=body, headers=headers)
        assert resp.status_code == 202
        jid = resp.json()["data"]["job_id"]

        st = test_app.client.get(f"/api/videos/status/{jid}", headers=headers)
        assert st.status_code == 200
        data = st.json()["data"]
        assert data["job_id"] == jid
        assert data["status"] in ("pending", "running")
    finally:
        test_app.cleanup()
