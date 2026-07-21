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
