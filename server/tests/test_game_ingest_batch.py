import pytest


def test_in_window_wraps_midnight():
    import datetime as dt

    from server.app.modules.game_library.scheduler import in_window
    from server.app.modules.game_library.scheduler import parse_hhmm as ph

    start, end = ph("23:00"), ph("03:00")
    assert in_window(start, end, dt.datetime(2026, 7, 21, 0, 30))
    assert not in_window(start, end, dt.datetime(2026, 7, 21, 12, 0))


def test_start_configured_ingest_locks_against_reentry(monkeypatch):
    import threading
    import time as time_mod

    from server.app.modules.game_library import scheduler

    started = threading.Event()
    release = threading.Event()

    class FakeCfg:
        batch_size = 1
        source_order = "taptap"
        max_shots = 6
        min_gap_seconds = 0
        max_gap_seconds = 0
        last_run_started_at = None
        last_run_finished_at = None
        last_run_trigger = None
        last_run_summary = None

    class FakeSession:
        def commit(self):
            pass

        def close(self):
            pass

    def fake_session_factory():
        return FakeSession()

    def fake_get_or_create_ingest_config(db):
        return FakeCfg()

    def fake_select_due_games(db, *, limit):
        return [1]

    def fake_refresh_one_game(session_factory, game_id, *, source_order, max_shots):
        started.set()
        release.wait(timeout=5)
        return "refreshed"

    monkeypatch.setattr(
        scheduler.ingest_service,
        "get_or_create_ingest_config",
        fake_get_or_create_ingest_config,
    )
    monkeypatch.setattr(scheduler.ingest_service, "select_due_games", fake_select_due_games)
    monkeypatch.setattr(scheduler.ingest_service, "refresh_one_game", fake_refresh_one_game)

    assert scheduler.is_configured_ingest_running() is False
    try:
        ok = scheduler.start_configured_ingest(fake_session_factory, trigger="manual")
        assert ok is True
        assert started.wait(timeout=5)
        assert scheduler.is_configured_ingest_running() is True

        # 忙时重入必须被拒绝，返回 False（不排队、不起第二个线程）。
        ok2 = scheduler.start_configured_ingest(fake_session_factory, trigger="manual")
        assert ok2 is False
    finally:
        release.set()

    for _ in range(100):
        if not scheduler.is_configured_ingest_running():
            break
        time_mod.sleep(0.05)
    assert scheduler.is_configured_ingest_running() is False


@pytest.mark.mysql
def test_refresh_one_game_error_path_still_advances_last_verified_at(monkeypatch):
    """error 路径也要推进 last_verified_at，否则该游戏在 last_verified_at ASC 里永远排
    最前、每个 tick 都被重选，饿死整批软-LRU 轮转（最终 review I2）。"""
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.game_library import ingest_service, service, types
        from server.app.modules.game_library.models import Game
        from server.app.modules.image_library.models import StockCategory

        s = app.session_factory()
        try:
            comp = StockCategory(name="c-err", bucket_name="c-err", kind="companion")
            s.add(comp)
            s.flush()
            g = Game(
                name="巡检必炸",
                name_normalized="巡检必炸",
                stock_category_id=comp.id,
                is_active=True,
            )
            s.add(g)
            s.commit()
            game_id = g.id
            assert g.last_verified_at is None
        finally:
            s.close()

        hit = types.Game(
            source="baidu",
            game_id="1",
            name="巡检必炸",
            score=8.0,
            tags=[],
            platforms=[],
            comment_count=1,
            icon_url=None,
            screenshot_urls=[],
            description="d",
            raw={},
        )
        monkeypatch.setattr(ingest_service, "_search_by_name", lambda source_order, name: hit)

        def _boom(*args, **kwargs):
            raise RuntimeError("upsert boom")

        monkeypatch.setattr(service, "upsert_game", _boom)

        result = ingest_service.refresh_one_game(
            app.session_factory, game_id, source_order="baidu", max_shots=6
        )
        assert result == "error"

        s2 = app.session_factory()
        try:
            refreshed = s2.get(Game, game_id)
            assert refreshed.last_verified_at is not None
        finally:
            s2.close()
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_select_due_games_companion_only_lru(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.game_library import ingest_service
        from server.app.modules.game_library.models import Game
        from server.app.modules.image_library.models import StockCategory

        s = app.session_factory()
        try:
            comp = StockCategory(name="c", bucket_name="c", kind="companion")
            main = StockCategory(name="m", bucket_name="m", kind="main")
            s.add_all([comp, main])
            s.flush()
            g_new = Game(
                name="new", name_normalized="new", stock_category_id=comp.id, is_active=True
            )  # last_verified NULL
            g_main = Game(
                name="mm", name_normalized="mm", stock_category_id=main.id, is_active=True
            )
            s.add_all([g_new, g_main])
            s.commit()
            due = ingest_service.select_due_games(s, limit=10)
            assert g_new.id in due and g_main.id not in due  # 只取 companion
        finally:
            s.close()
    finally:
        app.cleanup()
