import pytest


@pytest.mark.mysql
def test_ingest_config_singleton_defaults(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.game_library import ingest_service

        s = app.session_factory()
        try:
            cfg = ingest_service.get_or_create_ingest_config(s)
            s.commit()
            assert cfg.id == 1
            assert cfg.enabled is False
            assert cfg.window_start == "03:00" and cfg.window_end == "06:00"
            assert cfg.batch_size == 30
            assert cfg.source_order == "taptap,baidu"
            # 再取一次仍是同一行（单例）
            again = ingest_service.get_or_create_ingest_config(s)
            assert again.id == 1
        finally:
            s.close()
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_update_ingest_config_validates(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.game_library import ingest_service
        from server.app.shared.errors import ValidationError

        s = app.session_factory()
        try:
            cfg = ingest_service.update_ingest_config(
                s, {"enabled": True, "window_start": "02:00", "batch_size": 10}
            )
            s.commit()
            assert cfg.enabled is True and cfg.window_start == "02:00" and cfg.batch_size == 10
            d = ingest_service.ingest_config_to_dict(cfg, running=False)
            assert d["enabled"] is True and d["running"] is False and d["batch_size"] == 10
            with pytest.raises(ValidationError):
                ingest_service.update_ingest_config(s, {"window_start": "9am"})
            with pytest.raises(ValidationError):
                ingest_service.update_ingest_config(
                    s, {"min_gap_seconds": 100, "max_gap_seconds": 10}
                )
        finally:
            s.close()
    finally:
        app.cleanup()
