import json
from datetime import datetime, timedelta

from server.app.modules.performance.service import get_template_performance, record_publish_metrics
from server.tests.utils import build_test_app


def _seed_template_performance_article(
    db,
    *,
    user_id: int,
    template_id: int | None,
    created_at: datetime,
    metrics: dict | None,
    review_status: str,
):
    from server.app.modules.articles.models import Article

    article = Article(
        user_id=user_id,
        title="template performance article",
        content_json=json.dumps({"type": "doc", "content": []}),
        content_html="",
        plain_text="",
        word_count=0,
        status="ready",
        review_status=review_status,
        source_template_id=template_id,
        created_at=created_at,
        metrics=metrics,
    )
    db.add(article)


def _seed_prompt_template(db, *, user_id: int, name: str):
    from server.app.modules.prompt_templates.models import PromptTemplate

    template = PromptTemplate(
        name=name,
        content="write an article",
        scope="generation",
        user_id=user_id,
        is_enabled=True,
    )
    db.add(template)
    db.flush()
    return template


def test_template_performance_aggregates_matching_window_articles(monkeypatch):
    """Only matching, in-window articles contribute; missing metrics stay out of each average."""
    from server.app.core import config as core_config

    monkeypatch.setenv("GEO_MCP_TOKEN", "performance-test-token")
    core_config.get_settings.cache_clear()
    test_app = build_test_app(monkeypatch)
    try:
        from server.app.modules.performance import service

        now = datetime(2026, 7, 27, 12, 0, 0)
        monkeypatch.setattr(service, "utcnow", lambda: now)
        db = test_app.session_factory()
        try:
            template = _seed_prompt_template(db, user_id=test_app.admin_id, name="target template")
            other_template = _seed_prompt_template(
                db, user_id=test_app.admin_id, name="other template"
            )

            _seed_template_performance_article(
                db,
                user_id=test_app.admin_id,
                template_id=template.id,
                created_at=now,
                metrics={"views": 100, "likes": 8},
                review_status="approved",
            )
            _seed_template_performance_article(
                db,
                user_id=test_app.admin_id,
                template_id=template.id,
                created_at=now - timedelta(days=1),
                metrics={"views": 0},
                review_status="pending",
            )
            _seed_template_performance_article(
                db,
                user_id=test_app.admin_id,
                template_id=template.id,
                created_at=now - timedelta(days=2),
                metrics={"likes": 0},
                review_status="pending",
            )
            _seed_template_performance_article(
                db,
                user_id=test_app.admin_id,
                template_id=template.id,
                created_at=now - timedelta(days=8),
                metrics={"views": 900, "likes": 90},
                review_status="approved",
            )
            _seed_template_performance_article(
                db,
                user_id=test_app.admin_id,
                template_id=other_template.id,
                created_at=now,
                metrics={"views": 999, "likes": 99},
                review_status="approved",
            )
            db.commit()
            template_id = template.id

            result = get_template_performance(db, template.id, window_days=7)

            assert result == {
                "template_id": template.id,
                "window_days": 7,
                "article_count": 3,
                "avg_views": 50,
                "avg_likes": 4,
                "approval_rate": 1 / 3,
            }
        finally:
            db.close()

        response = test_app.client.get(
            f"/api/prompt-templates/{template_id}/performance",
            params={"window_days": 7},
            headers={"X-MCP-Token": "performance-test-token"},
        )
        assert response.status_code == 200, response.text
        assert response.json() == result
    finally:
        test_app.cleanup()
        core_config.get_settings.cache_clear()


def test_template_performance_empty_window_returns_null_averages(monkeypatch):
    test_app = build_test_app(monkeypatch)
    try:
        from server.app.modules.performance import service

        now = datetime(2026, 7, 27, 12, 0, 0)
        monkeypatch.setattr(service, "utcnow", lambda: now)
        db = test_app.session_factory()
        try:
            template = _seed_prompt_template(db, user_id=test_app.admin_id, name="empty template")
            db.commit()

            result = get_template_performance(db, template.id, window_days=7)

            assert result == {
                "template_id": template.id,
                "window_days": 7,
                "article_count": 0,
                "avg_views": None,
                "avg_likes": None,
                "approval_rate": None,
            }
        finally:
            db.close()
    finally:
        test_app.cleanup()


def test_template_performance_returns_zero_approval_rate_for_nonempty_pending_articles(monkeypatch):
    test_app = build_test_app(monkeypatch)
    try:
        from server.app.modules.performance import service

        now = datetime(2026, 7, 27, 12, 0, 0)
        monkeypatch.setattr(service, "utcnow", lambda: now)
        db = test_app.session_factory()
        try:
            template = _seed_prompt_template(db, user_id=test_app.admin_id, name="pending template")
            _seed_template_performance_article(
                db,
                user_id=test_app.admin_id,
                template_id=template.id,
                created_at=now - timedelta(days=7),
                metrics=None,
                review_status="pending",
            )
            db.commit()

            result = get_template_performance(db, template.id, window_days=7)

            assert result["article_count"] == 1
            assert result["avg_views"] is None
            assert result["avg_likes"] is None
            assert result["approval_rate"] == 0.0
        finally:
            db.close()
    finally:
        test_app.cleanup()


def test_record_publish_metrics_merges_into_article(monkeypatch):
    test_app = build_test_app(monkeypatch)
    try:
        from server.app.modules.accounts.models import Account
        from server.app.modules.articles.models import Article
        from server.app.modules.system.models import Platform
        from server.app.modules.tasks.models import PublishRecord, PublishTask

        db = test_app.session_factory()
        try:
            platform = Platform(
                code="toutiao_perf_test",
                name="Toutiao Perf Test",
                base_url="https://mp.toutiao.com",
            )
            db.add(platform)
            db.flush()

            account = Account(
                user_id=test_app.admin_id,
                platform=platform,
                display_name="perf test account",
                platform_user_id="perf-test-user",
                status="valid",
                state_path="browser_states/toutiao/perf/storage_state.json",
            )
            db.add(account)
            db.flush()

            a = Article(
                user_id=test_app.admin_id,
                title="t",
                content_json=json.dumps({"type": "doc", "content": []}),
                content_html="",
                plain_text="",
                word_count=0,
                status="ready",
                review_status="approved",
                metrics={"views": 100},  # 已有的会被合并
            )
            db.add(a)
            db.flush()

            task = PublishTask(
                user_id=test_app.admin_id,
                name="perf test task",
                task_type="single",
                status="succeeded",
                platform=platform,
                article=a,
            )
            db.add(task)
            db.flush()

            r = PublishRecord(
                task=task,
                article=a,
                platform=platform,
                account=account,
                status="succeeded",
            )
            db.add(r)
            db.commit()
            aid, rid = a.id, r.id
        finally:
            db.close()

        db = test_app.session_factory()
        try:
            record_publish_metrics(db, rid, {"likes": 50, "comments": 5})
            db.commit()
            a = db.query(Article).filter(Article.id == aid).first()
            assert a.metrics["views"] == 100  # 保留
            assert a.metrics["likes"] == 50  # 新增
            assert a.metrics["comments"] == 5
        finally:
            db.close()
    finally:
        test_app.cleanup()
