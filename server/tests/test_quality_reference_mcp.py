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


def test_adopt_from_mcp_requires_token(monkeypatch):
    app_ctx = build_test_app(monkeypatch)
    try:
        r = app_ctx.client.post("/api/quality-reference/adopt-from-mcp", json={"article_id": 1})
        assert r.status_code == 401
    finally:
        app_ctx.cleanup()


def test_adopt_from_mcp_approved_and_idempotent(monkeypatch):
    """已审文章可采纳（origin=own + 回落类目），同篇重复采纳幂等返回同一条。"""
    monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
    app_ctx = build_test_app(monkeypatch)
    db = app_ctx.session_factory()
    try:
        a = _make_article(db, review_status="approved")
        db.commit()
        h = {"X-MCP-Token": "secret"}
        r = app_ctx.client.post(
            "/api/quality-reference/adopt-from-mcp",
            json={"article_id": a.id, "category": "通用"},
            headers=h,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["origin"] == "own" and body["article_id"] == a.id
        assert body["categories"] == ["通用"]  # 无溯源类目 → 回落入参 category
        first_id = body["id"]

        r2 = app_ctx.client.post(
            "/api/quality-reference/adopt-from-mcp", json={"article_id": a.id}, headers=h
        )
        assert r2.status_code == 200 and r2.json()["id"] == first_id  # article_id UNIQUE 幂等
    finally:
        db.close()
        app_ctx.cleanup()


def test_adopt_from_mcp_rejects_unapproved(monkeypatch):
    """未审文章（pending）被 adopt_article 门禁拦下 → 400（命名异常交全局 handler，非 500）。"""
    monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
    app_ctx = build_test_app(monkeypatch)
    db = app_ctx.session_factory()
    try:
        a = _make_article(db, review_status="pending")
        db.commit()
        r = app_ctx.client.post(
            "/api/quality-reference/adopt-from-mcp",
            json={"article_id": a.id},
            headers={"X-MCP-Token": "secret"},
        )
        assert r.status_code == 400
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
        from server.app.modules.quality_reference.service import (
            compute_content_hash,
            set_reference_categories,
        )

        text = "一二三四五六七八九十"
        ref = QualityReference(
            origin="external",
            article_id=None,
            title="参考",
            content_json="{}",
            content_html="",
            plain_text=text,
            content_hash=compute_content_hash("参考", text),
        )
        db.add(ref)
        db.flush()
        set_reference_categories(db, ref.id, [{"category": "通用", "question_texts": None}])
        db.commit()

        h = {"X-MCP-Token": "secret"}
        resp = app_ctx.client.get("/api/quality-reference/pick?category=通用", headers=h)
        assert resp.status_code == 200
        refs = resp.json()["references"]
        assert len(refs) == 1
        assert refs[0]["plain_text"] == text[:5]
        assert refs[0]["origin"] == "external"
        assert refs[0]["category"] == "通用"  # 池 A 填命中类目
    finally:
        db.close()
        app_ctx.cleanup()


def test_adopt_from_mcp_sets_fallback_question_texts(monkeypatch):
    """无溯源文章：回落 category + question_texts 都落到子表关联行（经详情端点核实）。"""
    monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
    app_ctx = build_test_app(monkeypatch)
    db = app_ctx.session_factory()
    try:
        a = _make_article(db, review_status="approved")  # 无 source_question_category
        db.commit()
        r = app_ctx.client.post(
            "/api/quality-reference/adopt-from-mcp",
            json={
                "article_id": a.id,
                "category": "攻略",
                "question_texts": ["怎么开局", "新手推荐"],
            },
            headers={"X-MCP-Token": "secret"},
        )
        assert r.status_code == 200, r.text
        ref_id = r.json()["id"]
        detail = app_ctx.client.get(f"/api/quality-reference/{ref_id}")  # user JWT（admin cookie）
        assert detail.status_code == 200, detail.text
        cats = detail.json()["categories"]
        assert len(cats) == 1
        assert cats[0]["category"] == "攻略"
        assert cats[0]["question_texts"] == ["怎么开局", "新手推荐"]
    finally:
        db.close()
        app_ctx.cleanup()


def test_adopt_from_mcp_provenance_ignores_fallback(monkeypatch):
    """有溯源文章：忽略入参 category/question_texts，走 source_question_* 自动关联。"""
    monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
    app_ctx = build_test_app(monkeypatch)
    db = app_ctx.session_factory()
    try:
        a = _make_article(db, review_status="approved")
        a.source_question_category = "剧情"
        a.source_question_texts = ["主线剧情如何"]
        db.commit()
        r = app_ctx.client.post(
            "/api/quality-reference/adopt-from-mcp",
            json={"article_id": a.id, "category": "攻略", "question_texts": ["无关问题"]},
            headers={"X-MCP-Token": "secret"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["categories"] == ["剧情"]  # 溯源优先
        detail = app_ctx.client.get(f"/api/quality-reference/{body['id']}")
        cats = detail.json()["categories"]
        assert cats[0]["category"] == "剧情"
        assert cats[0]["question_texts"] == ["主线剧情如何"]
    finally:
        db.close()
        app_ctx.cleanup()
