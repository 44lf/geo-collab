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
