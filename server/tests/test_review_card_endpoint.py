import json

import pytest

from server.tests.utils import build_test_app


def _make_article(test_app) -> int:
    from server.app.modules.articles.models import Article

    with test_app.session_factory() as db:
        a = Article(
            user_id=test_app.admin_id,
            title="t",
            author="",
            # Article.content_json is a Text column (str), not a JSON column — unlike the
            # ArticleCreate/create_article() path used elsewhere, direct ORM construction
            # needs an already-serialized string or pymysql rejects the raw dict param.
            content_json=json.dumps({"type": "doc", "content": []}, ensure_ascii=False),
            content_html="",
            plain_text="",
            review_status="pending",
        )
        db.add(a)
        db.commit()
        db.refresh(a)
        return a.id


@pytest.mark.mysql
def test_review_card_requires_mcp_token(monkeypatch):
    test_app = build_test_app(monkeypatch)
    try:
        monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
        from server.app.core import config

        config.get_settings.cache_clear()
        r = test_app.client.post("/api/articles/1/review-card", json={"title": "t"})
        assert r.status_code == 401
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_review_card_builds_review_url_and_sends(monkeypatch):
    test_app = build_test_app(monkeypatch)
    try:
        monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
        monkeypatch.setenv("GEO_FEISHU_REVIEW_CARD_ENABLED", "true")
        monkeypatch.setenv("GEO_FEISHU_REVIEW_CHAT_ID", "oc_abc")
        monkeypatch.setenv("GEO_PUBLIC_BASE_URL", "https://geo.example.com")
        # 断言「无 app_id → 裸永久链接」，必须显式清掉本机 .env 可能带的 GEO_FEISHU_APP_ID，
        # 否则该值经 pydantic-settings 从 .env 文件回落进 get_settings()，build_review_link
        # 会改吐 AppLink（env_file 的值要用 os.environ 设空串压过，delenv 不够）。
        monkeypatch.setenv("GEO_FEISHU_APP_ID", "")
        from server.app.core import config

        config.get_settings.cache_clear()

        captured = {}

        def fake_send(**kwargs):
            captured.update(kwargs)
            return "om_1"

        monkeypatch.setattr("server.app.modules.articles.routers.mcp.send_review_card", fake_send)

        aid = _make_article(test_app)
        r = test_app.client.post(
            f"/api/articles/{aid}/review-card",
            json={"title": "t", "question": "q", "score": 88, "decision": "approved"},
            headers={"X-MCP-Token": "secret"},
        )
        assert r.status_code == 200, r.text
        assert r.json() == {"sent": True, "message_id": "om_1"}
        assert captured["review_url"] == f"https://geo.example.com/article/{aid}"
        assert captured["chat_id"] == "oc_abc"
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_review_card_uses_applink_when_app_id_set(monkeypatch):
    # 配了飞书自建应用 app_id → 按钮链接应是网页应用 AppLink（web_app/open），
    # 飞书端内以网页应用身份打开、注入 h5sdk，H5 免登才生效。
    test_app = build_test_app(monkeypatch)
    try:
        monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
        monkeypatch.setenv("GEO_FEISHU_REVIEW_CARD_ENABLED", "true")
        monkeypatch.setenv("GEO_FEISHU_REVIEW_CHAT_ID", "oc_abc")
        monkeypatch.setenv("GEO_PUBLIC_BASE_URL", "https://geo.example.com")
        monkeypatch.setenv("GEO_FEISHU_APP_ID", "cli_abc123")
        from server.app.core import config

        config.get_settings.cache_clear()

        captured = {}

        def fake_send(**kwargs):
            captured.update(kwargs)
            return "om_1"

        monkeypatch.setattr("server.app.modules.articles.routers.mcp.send_review_card", fake_send)

        aid = _make_article(test_app)
        r = test_app.client.post(
            f"/api/articles/{aid}/review-card",
            json={"title": "t", "question": "q", "score": 88, "decision": "approved"},
            headers={"X-MCP-Token": "secret"},
        )
        assert r.status_code == 200, r.text
        assert captured["review_url"] == (
            f"https://applink.feishu.cn/client/web_app/open?appId=cli_abc123&path=/article/{aid}"
        )
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_review_card_404_when_article_missing(monkeypatch):
    test_app = build_test_app(monkeypatch)
    try:
        monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
        from server.app.core import config

        config.get_settings.cache_clear()
        r = test_app.client.post(
            "/api/articles/999999/review-card",
            json={"title": "t"},
            headers={"X-MCP-Token": "secret"},
        )
        assert r.status_code == 404
    finally:
        test_app.cleanup()
