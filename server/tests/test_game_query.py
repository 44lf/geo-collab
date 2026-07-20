import pytest
from sqlalchemy import event


def _seed(s):
    from server.app.modules.game_library.models import Game, GameTag

    def mk(name, tags, score):
        g = Game(name=name, name_normalized=name, score=score, use_count=0, is_active=True)
        s.add(g)
        s.flush()
        for tag in tags:
            s.add(GameTag(game_id=g.id, tag=tag))
        return g

    mk("经营A", ["经营"], 9.0)
    mk("离题B", ["射击"], 9.9)
    mk("经营C", ["经营", "射击"], 7.0)
    s.commit()


@pytest.mark.mysql
def test_relevant_gates_diversity_only_ranks(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.game_library import service

        s = app.session_factory()
        try:
            _seed(s)
            out = service.query_games_by_tags(
                s,
                relevant_tags=["经营"],
                diversity_tags=["射击"],
                limit=10,
            )
            names = [g["name"] for g in out]
            assert "离题B" not in names
            assert set(names) == {"经营A", "经营C"}
        finally:
            s.close()
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_query_games_eager_loads_tags(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.game_library import service

        seed_session = app.session_factory()
        try:
            _seed(seed_session)
        finally:
            seed_session.close()

        statements: list[str] = []

        def before_cursor_execute(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(statement.lower())

        event.listen(app.engine, "before_cursor_execute", before_cursor_execute)
        s = app.session_factory()
        try:
            out = service.query_games_by_tags(
                s,
                relevant_tags=["经营"],
                diversity_tags=["射击"],
                limit=10,
            )
            assert [g["name"] for g in out] == ["经营C", "经营A"]
        finally:
            event.remove(app.engine, "before_cursor_execute", before_cursor_execute)
            s.close()

        tag_selects = [stmt for stmt in statements if "game_tags" in stmt]
        assert len(tag_selects) <= 2
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_query_games_filters_exclude_and_min_score(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.game_library import service

        s = app.session_factory()
        try:
            _seed(s)
            out = service.query_games_by_tags(
                s,
                relevant_tags=["经营"],
                exclude_tags=["射击"],
                min_score=8.0,
                limit=10,
            )
            assert [g["name"] for g in out] == ["经营A"]
        finally:
            s.close()
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_query_games_empty_relevant_tags_returns_empty(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.game_library import service

        s = app.session_factory()
        try:
            _seed(s)
            assert service.query_games_by_tags(s, relevant_tags=["", "  "], limit=10) == []
        finally:
            s.close()
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_list_game_tags_counts_desc(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.game_library import service

        s = app.session_factory()
        try:
            _seed(s)
            tags = service.list_game_tags(s)
            counts = {t["tag"]: t["game_count"] for t in tags}
            assert counts["经营"] == 2 and counts["射击"] == 2
        finally:
            s.close()
    finally:
        app.cleanup()
