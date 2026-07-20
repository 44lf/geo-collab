import pytest


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
