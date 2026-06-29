"""POST /api/articles/{id}/ai-illustrate：game_positions → IllustrateOptions.game_list 映射.

mock service 层 illustrate_one，只测 endpoint 把 game_positions 正确翻成 options.game_list；
不建真文章/图片（确定性落图与计数已由 ai_format 单测覆盖）。
"""

from __future__ import annotations

import pytest

from server.tests.utils import build_test_app


def _auth(monkeypatch, test_app):
    monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
    from server.app.core import config

    config.get_settings.cache_clear()


@pytest.mark.mysql
def test_game_positions_maps_to_game_list(monkeypatch):
    test_app = build_test_app(monkeypatch)
    try:
        _auth(monkeypatch, test_app)
        from server.app.modules.articles.ai_illustrate_svc import IllustrateResult

        called: dict = {}

        def fake_illustrate_one(*, article_id, main_category_id, user_id, options, session_factory):
            called["game_list"] = options.game_list
            return IllustrateResult(article_id=article_id, images_inserted=1)

        monkeypatch.setattr(
            "server.app.modules.articles.router.illustrate_one", fake_illustrate_one
        )

        r = test_app.client.post(
            "/api/articles/7/ai-illustrate",
            json={
                "main_category_id": 1,
                "game_positions": [
                    {"game": "原神"},
                    {"game": "明日方舟", "category_id": 12},
                ],
            },
            headers={"X-MCP-Token": "secret"},
        )
        assert r.status_code == 200, r.text
        assert called["game_list"] == [
            {"game": "原神"},
            {"game": "明日方舟", "category_id": 12},
        ]
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_game_positions_defaults_to_none(monkeypatch):
    test_app = build_test_app(monkeypatch)
    try:
        _auth(monkeypatch, test_app)
        from server.app.modules.articles.ai_illustrate_svc import IllustrateResult

        called: dict = {}

        def fake_illustrate_one(*, article_id, main_category_id, user_id, options, session_factory):
            called["game_list"] = options.game_list
            return IllustrateResult(article_id=article_id, images_inserted=0)

        monkeypatch.setattr(
            "server.app.modules.articles.router.illustrate_one", fake_illustrate_one
        )

        r = test_app.client.post(
            "/api/articles/7/ai-illustrate",
            json={"main_category_id": 1},
            headers={"X-MCP-Token": "secret"},
        )
        assert r.status_code == 200, r.text
        assert called["game_list"] is None
    finally:
        test_app.cleanup()
