"""按标题搜自有文章的 MCP 端点 + 服务函数（高质量库入库便利化）。

GET /api/mcp/articles/search：默认只回 approved、already_adopted 标注、snippet 截断、
路由不撞 /articles/{id:int}、MCP token 鉴权。详见
docs/superpowers/specs/2026-07-16-search-articles-by-title-adopt-design.md。
"""

from __future__ import annotations

import pytest

from server.tests.utils import build_test_app

pytestmark = pytest.mark.mysql


def _mk(db, *, title, review_status="approved", plain_text="正文内容"):
    from server.app.modules.articles.models import Article

    a = Article(
        user_id=1,
        title=title,
        content_json="{}",
        content_html="",
        plain_text=plain_text,
        word_count=len(plain_text),
        status="draft",
        review_status=review_status,
        is_deleted=False,
    )
    db.add(a)
    db.flush()
    return a


def test_search_by_title_matches_and_filters(monkeypatch):
    monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
    app_ctx = build_test_app(monkeypatch)
    db = app_ctx.session_factory()
    try:
        from server.app.modules.articles import search_by_title

        _mk(db, title="周年庆盘点", review_status="approved")
        _mk(db, title="周年庆前瞻", review_status="pending")
        _mk(db, title="无关文章", review_status="approved")
        db.commit()

        hits = search_by_title(db, title="周年庆")  # 不过滤状态
        assert {h.title for h in hits} == {"周年庆盘点", "周年庆前瞻"}

        approved = search_by_title(db, title="周年庆", review_status="approved")
        assert [h.title for h in approved] == ["周年庆盘点"]

        assert search_by_title(db, title="   ") == []  # 空标题 → []
    finally:
        db.close()
        app_ctx.cleanup()


def test_search_endpoint_defaults_approved_and_flags_adopted(monkeypatch):
    monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
    app_ctx = build_test_app(monkeypatch)
    db = app_ctx.session_factory()
    try:
        a1 = _mk(
            db, title="周年庆盘点", review_status="approved", plain_text="开篇正文很长很长很长"
        )
        _mk(db, title="周年庆前瞻", review_status="pending")
        db.commit()

        from server.app.modules.quality_reference import service as qsvc

        qsvc.adopt_article(db, user_id=app_ctx.admin_id, article_id=a1.id)  # 采纳 a1 进库
        db.commit()

        h = {"X-MCP-Token": "secret"}
        r = app_ctx.client.get("/api/mcp/articles/search", params={"title": "周年庆"}, headers=h)
        assert r.status_code == 200, r.text
        items = r.json()["items"]
        assert [i["title"] for i in items] == ["周年庆盘点"]  # 默认只回 approved
        assert items[0]["already_adopted"] is True
        assert items[0]["snippet"].startswith("开篇正文")

        r2 = app_ctx.client.get(
            "/api/mcp/articles/search",
            params={"title": "周年庆", "review_status": "all"},
            headers=h,
        )
        assert {i["title"] for i in r2.json()["items"]} == {"周年庆盘点", "周年庆前瞻"}
    finally:
        db.close()
        app_ctx.cleanup()


def test_search_route_does_not_shadow_get_by_id(monkeypatch):
    monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
    app_ctx = build_test_app(monkeypatch)
    db = app_ctx.session_factory()
    try:
        a = _mk(db, title="独苗文章", review_status="approved")
        db.commit()
        h = {"X-MCP-Token": "secret"}
        r_search = app_ctx.client.get(
            "/api/mcp/articles/search", params={"title": "独苗"}, headers=h
        )
        assert r_search.status_code == 200  # /search 命中搜索，不被 {id:int} 当 422
        r_get = app_ctx.client.get(f"/api/mcp/articles/{a.id}", headers=h)
        assert r_get.status_code == 200 and r_get.json()["id"] == a.id  # 单篇仍正常
    finally:
        db.close()
        app_ctx.cleanup()


def test_search_requires_mcp_token(monkeypatch):
    app_ctx = build_test_app(monkeypatch)  # 未设 GEO_MCP_TOKEN → 全 401
    try:
        r = app_ctx.client.get("/api/mcp/articles/search", params={"title": "x"})
        assert r.status_code == 401
    finally:
        app_ctx.cleanup()
