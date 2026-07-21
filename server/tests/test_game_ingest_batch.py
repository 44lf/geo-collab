import pytest


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
