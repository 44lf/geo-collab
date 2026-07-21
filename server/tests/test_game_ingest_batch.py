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
        cull_after_misses = 3
        cull_enabled = True
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

    def fake_refresh_one_game(session_factory, game_id, *, source_order, max_shots, **kwargs):
        started.set()
        release.wait(timeout=5)
        return {"outcome": "refreshed", "per_source": {}}

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


class _QuotaCfg:
    """手动批次用的假配置。gap=0 让测试不真 sleep。"""

    source_order = "taptap"
    max_shots = 6
    min_gap_seconds = 0
    max_gap_seconds = 0
    cull_after_misses = 3
    cull_enabled = True
    last_run_started_at = None
    last_run_finished_at = None
    last_run_trigger = None
    last_run_summary = None

    def __init__(self, batch_size):
        self.batch_size = batch_size


class _QuotaSession:
    def commit(self):
        pass

    def close(self):
        pass


def _run_batch_with_outcomes(monkeypatch, *, batch_size, due_ids, outcomes):
    """跑一次手动批次，注入固定的 select_due_games 序列与每个游戏的 refresh 结果。
    返回 (summary, attempted_ids)。"""
    from server.app.modules.game_library import scheduler

    cfg = _QuotaCfg(batch_size)
    attempted: list[int] = []

    monkeypatch.setattr(scheduler.ingest_service, "get_or_create_ingest_config", lambda db: cfg)
    monkeypatch.setattr(
        scheduler.ingest_service,
        "select_due_games",
        lambda db, *, limit: list(due_ids)[:limit],
    )

    def fake_refresh(session_factory, game_id, **kwargs):
        attempted.append(game_id)
        return {"outcome": outcomes[game_id], "per_source": {}}

    monkeypatch.setattr(scheduler.ingest_service, "refresh_one_game", fake_refresh)
    monkeypatch.setattr(scheduler.time, "sleep", lambda *a, **k: None)

    scheduler._run_configured_batch(lambda: _QuotaSession(), trigger="manual")
    return cfg.last_run_summary, attempted


def test_manual_batch_not_found_does_not_consume_quota(monkeypatch):
    # 名额=2：搜不到不占，继续往下取直到攒够 2 个 refreshed；第 5 个不该被碰到。
    summary, attempted = _run_batch_with_outcomes(
        monkeypatch,
        batch_size=2,
        due_ids=[1, 2, 3, 4, 5],
        outcomes={1: "not_found", 2: "refreshed", 3: "not_found", 4: "refreshed", 5: "refreshed"},
    )
    assert attempted == [1, 2, 3, 4]  # 攒够 2 个成功即停，第 5 个不动
    assert summary["refreshed"] == 2
    assert summary["not_found"] == 2
    assert summary["attempts"] == 4
    assert summary["cap_reached"] is False


def test_manual_batch_attempt_cap_stops_churn(monkeypatch):
    # 整池都搜不到：名额=2 → 尝试上限=8，跑满 8 次就停、标 cap_reached，不刷全库。
    summary, attempted = _run_batch_with_outcomes(
        monkeypatch,
        batch_size=2,
        due_ids=list(range(1, 100)),
        outcomes={i: "not_found" for i in range(1, 100)},
    )
    assert len(attempted) == 8  # batch_size(2) × _ATTEMPT_MULTIPLIER(4)
    assert summary["refreshed"] == 0
    assert summary["not_found"] == 8
    assert summary["attempts"] == 8
    assert summary["cap_reached"] is True


@pytest.mark.mysql
def test_manual_batch_writes_report_event(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.game_library import ingest_service, scheduler
        from server.app.modules.report.models import ReportEvent

        monkeypatch.setattr(
            scheduler.ingest_service,
            "select_due_games",
            lambda db, *, limit: [101, 102, 103][:limit],
        )
        outcomes = {
            101: ("refreshed", "甲游戏"),
            102: ("not_found", "乙游戏"),
            103: ("refreshed", "丙游戏"),
        }

        def fake_refresh(session_factory, game_id, **kwargs):
            oc, nm = outcomes[game_id]
            return {"outcome": oc, "per_source": {}, "game_id": game_id, "name": nm}

        monkeypatch.setattr(scheduler.ingest_service, "refresh_one_game", fake_refresh)
        monkeypatch.setattr(scheduler.time, "sleep", lambda *a, **k: None)

        s = app.session_factory()
        try:
            cfg = ingest_service.get_or_create_ingest_config(s)
            cfg.batch_size = 2
            cfg.min_gap_seconds = 0
            cfg.max_gap_seconds = 0
            s.commit()
        finally:
            s.close()

        scheduler._run_configured_batch(app.session_factory, trigger="manual")

        s2 = app.session_factory()
        try:
            ev = (
                s2.query(ReportEvent)
                .filter(ReportEvent.source_module == "game_ingest")
                .order_by(ReportEvent.id.desc())
                .first()
            )
            assert ev is not None
            assert ev.event_type == "ingest_batch"
            p = ev.payload_json
            assert p["counts"]["refreshed"] == 2
            assert p["counts"]["not_found"] == 1
            names = {g["name"] for g in p["games"]["refreshed"]}
            assert names == {"甲游戏", "丙游戏"}
        finally:
            s2.close()
    finally:
        app.cleanup()


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
        monkeypatch.setattr(
            ingest_service,
            "_collect_from_all_sources",
            lambda source_order, name: {"hits": [hit], "per_source": {"baidu": "hit"}},
        )

        def _boom(*args, **kwargs):
            raise RuntimeError("upsert boom")

        monkeypatch.setattr(service, "upsert_game", _boom)

        result = ingest_service.refresh_one_game(
            app.session_factory, game_id, source_order="baidu", max_shots=6
        )
        assert result["outcome"] == "error"

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
