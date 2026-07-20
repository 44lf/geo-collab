import pytest


@pytest.mark.mysql
def test_run_ingest_once_isolates_target_failure(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.game_library import registry, scheduler, service, types
        from server.app.modules.game_library.models import Game

        def fake_collect(source, category, pool_size, **kw):
            if category == "boom":
                raise RuntimeError("source down")
            return [
                types.Game(
                    source="baidu",
                    game_id="1",
                    name=f"{category}游戏",
                    score=8.0,
                    tags=[category],
                    platforms=[],
                    comment_count=1,
                    icon_url=None,
                    screenshot_urls=[],
                    description="d",
                    raw={},
                )
            ]

        monkeypatch.setattr(registry, "collect_pool", fake_collect)
        monkeypatch.setattr(service, "download_image", lambda *a, **k: None, raising=False)

        targets = [
            {"source": "baidu", "category": "经营"},
            {"source": "baidu", "category": "boom"},
        ]
        result = scheduler.run_ingest_once(app.session_factory, targets=targets)
        assert result["failed"] == 1 and result["upserted"] >= 1

        s = app.session_factory()
        try:
            assert s.query(Game).count() == 1
        finally:
            s.close()
    finally:
        app.cleanup()
