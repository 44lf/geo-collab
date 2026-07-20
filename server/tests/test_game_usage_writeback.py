import pytest


@pytest.mark.mysql
def test_bump_game_usage_increments_and_skips_unknown(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.articles.models import Article
        from server.app.modules.game_library import service
        from server.app.modules.game_library.models import Game

        s = app.session_factory()
        try:
            article = Article(
                user_id=app.admin_id,
                title="usage marker",
                content_json="{}",
                content_html="",
                plain_text="",
                client_request_id="game-usage-test",
            )
            s.add(article)
            s.flush()
            g = Game(name="星露谷", name_normalized="星露谷", use_count=0, is_active=True)
            s.add(g)
            s.flush()
            service.bump_game_usage(s, [g.id, 999999], article_id=article.id)
            s.commit()
            got = s.get(Game, g.id)
            assert got.use_count == 1 and got.last_used_article_id == article.id
            assert got.last_used_at is not None
        finally:
            s.close()
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_save_from_mcp_bumps_selected_games(monkeypatch):
    from server.tests.test_save_article_mcp import _seed_question_and_template
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
        from server.app.core import config
        from server.app.modules.game_library.models import Game

        config.get_settings.cache_clear()
        qid, tpl_id = _seed_question_and_template(app)

        s = app.session_factory()
        try:
            game = Game(name="牧场物语", name_normalized="牧场物语", use_count=0, is_active=True)
            s.add(game)
            s.commit()
            gid = game.id
        finally:
            s.close()

        body = {
            "question_item_id": qid,
            "prompt_template_id": tpl_id,
            "user_id": app.admin_id,
            "title": "牧场物语攻略",
            "markdown_content": "正文...",
            "selected_games": [{"game_id": gid, "name": "牧场物语"}],
        }
        r = app.client.post(
            "/api/articles/save-from-mcp",
            json=body,
            headers={"X-MCP-Token": "secret"},
        )
        assert r.status_code == 200, r.text

        s = app.session_factory()
        try:
            assert s.get(Game, gid).use_count == 1
        finally:
            s.close()
    finally:
        app.cleanup()
