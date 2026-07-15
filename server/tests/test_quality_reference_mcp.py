"""quality_reference MCP 端点：GET /pick + POST /{id}/adversarial-score。

两条都走 MCP token 鉴权（与 user JWT 隔离）；record_score 过滤软删文章返回 404、
不改 review_status。详见 docs/superpowers/specs/2026-07-13-adversarial-review-quality-gate-design.md。
"""

from __future__ import annotations

import pytest

from server.app.modules.articles.models import Article
from server.tests.utils import build_test_app

pytestmark = pytest.mark.mysql


def _make_article(db, **kw):
    a = Article(
        user_id=1,
        title="T",
        content_json="{}",
        content_html="",
        plain_text="正文",
        word_count=2,
        status="draft",
        review_status=kw.get("review_status", "pending"),
        is_deleted=kw.get("is_deleted", False),
    )
    db.add(a)
    db.flush()
    return a


def test_pick_requires_mcp_token(monkeypatch):
    app_ctx = build_test_app(monkeypatch)
    try:
        assert app_ctx.client.get("/api/quality-reference/pick?category=通用").status_code == 401
    finally:
        app_ctx.cleanup()


def test_record_score_writes_and_rejects_deleted(monkeypatch):
    monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
    app_ctx = build_test_app(monkeypatch)
    db = app_ctx.session_factory()
    try:
        a = _make_article(db)
        db.commit()
        h = {"X-MCP-Token": "secret"}
        resp = app_ctx.client.post(
            f"/api/articles/{a.id}/adversarial-score", json={"score": 72}, headers=h
        )
        assert resp.json()["adversarial_score"] == 72

        d = _make_article(db, is_deleted=True)
        db.commit()
        resp2 = app_ctx.client.post(
            f"/api/articles/{d.id}/adversarial-score", json={"score": 50}, headers=h
        )
        assert resp2.status_code == 404
    finally:
        db.close()
        app_ctx.cleanup()


def test_pick_truncates_plain_text(monkeypatch):
    """pick 用配置的 truncate_chars 截断 plain_text；k 默认取 GEO_ADVERSARIAL_TOPK。"""
    monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
    monkeypatch.setenv("GEO_ADVERSARIAL_REF_TRUNCATE_CHARS", "5")
    app_ctx = build_test_app(monkeypatch)
    db = app_ctx.session_factory()
    try:
        from server.app.modules.quality_reference.models import QualityReference
        from server.app.modules.quality_reference.service import compute_content_hash

        text = "一二三四五六七八九十"
        ref = QualityReference(
            origin="external",
            article_id=None,
            title="参考",
            content_json="{}",
            content_html="",
            plain_text=text,
            content_hash=compute_content_hash("参考", text),
            category="通用",
        )
        db.add(ref)
        db.commit()

        h = {"X-MCP-Token": "secret"}
        resp = app_ctx.client.get("/api/quality-reference/pick?category=通用", headers=h)
        assert resp.status_code == 200
        refs = resp.json()["references"]
        assert len(refs) == 1
        assert refs[0]["plain_text"] == text[:5]
        assert refs[0]["origin"] == "external"
    finally:
        db.close()
        app_ctx.cleanup()
