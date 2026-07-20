import pytest

from server.app.modules.game_library import types


def _game(source, gid, name, tags, shots, score, comments=1):
    return types.Game(
        source=source,
        game_id=gid,
        name=name,
        score=score,
        tags=tags,
        platforms=["android"],
        comment_count=comments,
        icon_url="http://x/i.png",
        screenshot_urls=shots,
        description="d",
        raw={},
    )


@pytest.mark.mysql
def test_upsert_merges_two_sources_into_one_row(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.game_library import service
        from server.app.modules.game_library.models import Game, GameTag
        from server.app.modules.image_library import store as minio_store
        from server.app.shared import image_download

        monkeypatch.setattr(minio_store, "ensure_bucket", lambda *a, **k: None)
        monkeypatch.setattr(minio_store, "upload_image", lambda *a, **k: None)
        monkeypatch.setattr(
            image_download,
            "download_image",
            lambda url, **k: (b"\xff\xd8\xff", "image/jpeg"),
        )

        s = app.session_factory()
        try:
            service.upsert_game(
                s,
                _game("baidu", "1", "餐厅养成记", ["经营"], ["http://x/b1.jpg"], 8.0),
            )
            s.commit()
            service.upsert_game(
                s,
                _game(
                    "taptap",
                    "9",
                    "餐厅养成记",
                    ["养成"],
                    ["http://x/t1.jpg"],
                    9.2,
                    comments=50,
                ),
            )
            s.commit()
            rows = s.query(Game).filter(Game.name_normalized == "餐厅养成记").all()
            assert len(rows) == 1
            g = rows[0]
            assert g.score == 9.2 and g.comment_count == 50
            assert len(g.sources) == 2
            tags = {t.tag for t in s.query(GameTag).filter(GameTag.game_id == g.id)}
            assert tags == {"经营", "养成"}
        finally:
            s.close()
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_upsert_idempotent_same_source(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.game_library import service
        from server.app.modules.game_library.models import Game
        from server.app.modules.image_library import store as minio_store
        from server.app.shared import image_download

        monkeypatch.setattr(minio_store, "ensure_bucket", lambda *a, **k: None)
        monkeypatch.setattr(minio_store, "upload_image", lambda *a, **k: None)
        monkeypatch.setattr(
            image_download,
            "download_image",
            lambda url, **k: (b"\xff\xd8\xff", "image/jpeg"),
        )

        s = app.session_factory()
        try:
            for _ in range(2):
                service.upsert_game(
                    s,
                    _game("baidu", "1", "星露谷", ["模拟"], ["http://x/a.jpg"], 9.0),
                )
                s.commit()
            assert s.query(Game).count() == 1
        finally:
            s.close()
    finally:
        app.cleanup()
