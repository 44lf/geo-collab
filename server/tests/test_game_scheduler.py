import pytest

from server.app.modules.game_library import types


def _game(source, gid, name, *, shots=None):
    return types.Game(
        source=source,
        game_id=gid,
        name=name,
        score=8.0,
        tags=["养成"],
        platforms=[],
        comment_count=1,
        icon_url=None,
        screenshot_urls=shots or [],
        description="d",
        raw={},
    )


class _FakeSession:
    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def test_run_ingest_once_throttles_consecutive_taptap_detail_requests(monkeypatch):
    from server.app.modules.game_library import registry, scheduler, service
    from server.app.modules.game_library.sources import taptap

    monkeypatch.setattr(
        registry,
        "collect_pool",
        lambda *a, **k: [
            _game("taptap", "1", "游戏1"),
            _game("taptap", "2", "游戏2"),
            _game("taptap", "3", "游戏3"),
        ],
    )
    detail_calls: list[str] = []

    def fake_get_detail(game_id):
        detail_calls.append(game_id)
        return _game("taptap", game_id, f"游戏{game_id}", shots=[f"http://x/{game_id}.jpg"])

    sleeps: list[float] = []
    monkeypatch.setattr(taptap, "get_detail", fake_get_detail)
    monkeypatch.setattr(service, "upsert_game", lambda *a, **k: None)
    monkeypatch.setattr(scheduler.time, "sleep", sleeps.append)

    result = scheduler.run_ingest_once(
        lambda: _FakeSession(),
        targets=[{"source": "taptap", "category": "养成", "max_games": 3}],
    )

    assert result == {"targets": 1, "upserted": 3, "failed": 0}
    assert detail_calls == ["1", "2", "3"]
    assert sleeps == [0.3, 0.3]


def test_seed_targets_do_not_carry_dead_pages_parameter():
    from server.app.modules.game_library import scheduler

    assert all("pages" not in target for target in scheduler.SEED_TARGETS)


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


@pytest.mark.mysql
def test_run_ingest_once_isolates_bad_target_config(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.game_library import registry, scheduler, types

        def fake_collect(source, category, pool_size, **kw):
            return [
                types.Game(
                    source=source,
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

        targets = [
            {"category": "缺source"},
            {"source": "baidu", "category": "经营", "max_games": "1"},
        ]
        result = scheduler.run_ingest_once(app.session_factory, targets=targets)
        assert result["failed"] == 1
        assert result["upserted"] == 1
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_run_ingest_once_isolates_single_game_failure(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.game_library import registry, scheduler, service, types

        games = [
            types.Game(
                source="baidu",
                game_id="bad",
                name="坏游戏",
                score=8.0,
                tags=["经营"],
                platforms=[],
                comment_count=1,
                icon_url=None,
                screenshot_urls=[],
                description="d",
                raw={},
            ),
            types.Game(
                source="baidu",
                game_id="good",
                name="好游戏",
                score=8.0,
                tags=["经营"],
                platforms=[],
                comment_count=1,
                icon_url=None,
                screenshot_urls=[],
                description="d",
                raw={},
            ),
        ]
        seen: list[str] = []

        monkeypatch.setattr(registry, "collect_pool", lambda *a, **k: games)

        def fake_upsert(db, game, *, max_screenshots=6):
            seen.append(game.game_id)
            if game.game_id == "bad":
                raise RuntimeError("bad game")
            return None

        monkeypatch.setattr(service, "upsert_game", fake_upsert)

        result = scheduler.run_ingest_once(
            app.session_factory,
            targets=[{"source": "baidu", "category": "经营"}],
        )
        assert seen == ["bad", "good"]
        assert result["failed"] == 1
        assert result["upserted"] == 1
    finally:
        app.cleanup()
