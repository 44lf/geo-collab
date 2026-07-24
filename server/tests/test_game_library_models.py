import pytest


@pytest.mark.mysql
def test_game_and_tags_roundtrip(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.game_library.models import Game, GameTag

        s = app.session_factory()
        try:
            g = Game(
                name="餐厅养成记",
                name_normalized="餐厅养成记",
                sources=[{"source": "baidu", "source_game_id": "1"}],
                score=8.7,
                use_count=0,
            )
            s.add(g)
            s.flush()
            s.add(GameTag(game_id=g.id, tag="经营"))
            s.commit()
            assert g.id is not None and g.use_count == 0
            got = s.get(Game, g.id)
            assert got.name_normalized == "餐厅养成记"
        finally:
            s.close()
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_stock_image_has_usage_and_hash_columns(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        from sqlalchemy import inspect

        cols = {c["name"] for c in inspect(app.engine).get_columns("stock_images")}
        assert {
            "source_url",
            "source_url_hash",
            "use_count",
            "last_used_at",
            "last_used_article_id",
        } <= cols
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_stock_image_source_url_hash_unique_constraint_in_orm_schema(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        from sqlalchemy import inspect

        uniques = {
            tuple(u["column_names"])
            for u in inspect(app.engine).get_unique_constraints("stock_images")
        }
        assert ("category_id", "source_url_hash") in uniques
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_games_is_active_index_in_orm_schema(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        from sqlalchemy import inspect

        indexes = {
            tuple(index["column_names"]) for index in inspect(app.engine).get_indexes("games")
        }
        assert ("is_active",) in indexes
    finally:
        app.cleanup()
