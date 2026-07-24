"""get_article（MCP）暴露溯源字段（source_question_category / source_question_texts）。

Task 6：字段已在 Task 1 落到 Article ORM（source_question_category / source_question_texts /
adversarial_score），本测试验证序列化层（ArticleRead + to_article_read）把前两个带出去，
供 /goal verifier 的 get_article 读 category 去调 pick_quality_references。
"""

import pytest

from server.app.modules.articles.models import Article
from server.tests.utils import build_test_app

pytestmark = pytest.mark.mysql


@pytest.fixture
def app_ctx(monkeypatch):
    monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
    ctx = build_test_app(monkeypatch)
    try:
        yield ctx
    finally:
        ctx.cleanup()


def test_get_article_exposes_source_question_category(app_ctx):
    db = app_ctx.session_factory()
    try:
        a = Article(
            user_id=app_ctx.admin_id,
            title="T",
            content_json="{}",
            content_html="",
            plain_text="正文",
            word_count=2,
            status="draft",
            review_status="pending",
            source_question_category="餐厅",
            source_question_texts=["怎么开店"],
        )
        db.add(a)
        db.commit()
        article_id = a.id

        resp = app_ctx.client.get(
            f"/api/mcp/articles/{article_id}", headers={"X-MCP-Token": "secret"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["source_question_category"] == "餐厅"
        assert body["source_question_texts"] == ["怎么开店"]
    finally:
        db.close()
