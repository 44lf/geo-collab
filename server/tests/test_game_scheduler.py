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


def test_run_ingest_once_upserts_pool_and_dedups(monkeypatch):
    from server.app.modules.game_library import registry, scheduler, service

    monkeypatch.setattr(
        registry,
        "collect_pool",
        lambda *a, **k: [
            _game("baidu", "1", "游戏1"),
            _game("baidu", "2", "游戏2"),
            _game("baidu", "1", "游戏1"),  # 同 (source, game_id) → 去重跳过
        ],
    )
    upserts: list[str] = []
    monkeypatch.setattr(service, "upsert_game", lambda db, game, **k: upserts.append(game.game_id))

    result = scheduler.run_ingest_once(
        lambda: _FakeSession(),
        targets=[{"source": "baidu", "category": "经营", "max_games": 3}],
    )

    assert result == {"targets": 1, "upserted": 2, "failed": 0}  # 去重后 2 条
    assert upserts == ["1", "2"]


def test_seed_targets_are_baidu_only_without_dead_pages_param():
    from server.app.modules.game_library import scheduler

    assert scheduler.SEED_TARGETS
    assert all(target["source"] == "baidu" for target in scheduler.SEED_TARGETS)
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
