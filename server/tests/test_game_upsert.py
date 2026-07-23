import pytest

from server.app.modules.game_library import types


def _game(
    source,
    gid,
    name,
    tags,
    shots,
    score,
    comments=1,
    description="d",
    icon_url="http://x/i.png",
):
    return types.Game(
        source=source,
        game_id=gid,
        name=name,
        score=score,
        tags=tags,
        platforms=["android"],
        comment_count=comments,
        icon_url=icon_url,
        screenshot_urls=shots,
        description=description,
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
                    "baidu",
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


@pytest.mark.mysql
def test_upsert_downloads_before_checking_out_db_connection(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.game_library import service
        from server.app.modules.image_library import service as image_service
        from server.app.modules.image_library import store as minio_store
        from server.app.shared import image_download

        monkeypatch.setattr(minio_store, "ensure_bucket", lambda *a, **k: None)
        monkeypatch.setattr(minio_store, "upload_image", lambda *a, **k: None)

        setup = app.session_factory()
        try:
            image_service.get_or_create_companion_category(setup, "连接探针")
        finally:
            setup.close()

        checked_out: list[int] = []

        def fake_download(url, **kwargs):
            checked_out.append(app.engine.pool.checkedout())
            return b"\xff\xd8\xff", "image/jpeg"

        monkeypatch.setattr(image_download, "download_image", fake_download)

        s = app.session_factory()
        try:
            service.upsert_game(
                s,
                _game("baidu", "1", "连接探针", ["经营"], ["http://x/a.jpg"], 8.0),
            )
            s.commit()
        finally:
            s.close()

        assert checked_out == [0]
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_upsert_none_source_does_not_override_existing_values(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.game_library import service
        from server.app.modules.game_library.models import Game
        from server.app.modules.image_library import store as minio_store
        from server.app.shared import image_download

        monkeypatch.setattr(minio_store, "ensure_bucket", lambda *a, **k: None)
        monkeypatch.setattr(minio_store, "upload_image", lambda *a, **k: None)
        monkeypatch.setattr(image_download, "download_image", lambda *a, **k: None)

        s = app.session_factory()
        try:
            service.upsert_game(
                s,
                _game(
                    "baidu",
                    "1",
                    "空值合并",
                    ["经营"],
                    [],
                    8.0,
                    comments=12,
                    description="已有完整描述",
                    icon_url="http://x/icon-long.png",
                ),
            )
            s.commit()
            service.upsert_game(
                s,
                _game(
                    "baidu",
                    "2",
                    "空值合并",
                    ["养成"],
                    [],
                    None,
                    comments=None,
                    description=None,
                    icon_url=None,
                ),
            )
            s.commit()
            row = s.query(Game).filter(Game.name_normalized == "空值合并").one()
            assert row.score == 8.0
            assert row.comment_count == 12
            assert row.description == "已有完整描述"
            assert row.icon_url == "http://x/icon-long.png"
        finally:
            s.close()
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_upsert_deduplicates_repeated_screenshot_url(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.game_library import service
        from server.app.modules.image_library import store as minio_store
        from server.app.modules.image_library.models import StockImage
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
                    _game(
                        "baidu",
                        "1",
                        "截图去重",
                        ["经营"],
                        ["http://x/a.jpg", "http://x/a.jpg"],
                        8.0,
                    ),
                )
                s.commit()
            assert s.query(StockImage).count() == 1
        finally:
            s.close()
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_upsert_rehosts_cover_and_keeps_it_idempotent(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.game_library import service
        from server.app.modules.image_library import store as minio_store

        monkeypatch.setattr(minio_store, "ensure_bucket", lambda *a, **k: None)
        monkeypatch.setattr(minio_store, "upload_image", lambda *a, **k: None)

        s = app.session_factory()
        try:
            row = service.upsert_game(
                s,
                _game("baidu", "1", "封面转存", ["养成"], [], 8.0),
                pre_downloaded=[],
                pre_downloaded_icon=("http://cdn/icon.png", b"\xff\xd8\xff", "image/jpeg"),
            )
            s.commit()
            # 封面转存自家 MinIO → icon_url 指本地代理，不再是外链。
            assert row.icon_url.startswith("/api/stock-images/")
            local = row.icon_url
            # 已本地 → 再来一轮新外链/新字节都不覆盖（幂等，防外链盗链回填）。
            row2 = service.upsert_game(
                s,
                _game("baidu", "1", "封面转存", ["养成"], [], 8.0),
                pre_downloaded=[],
                pre_downloaded_icon=("http://cdn/icon2.png", b"\xff\xd8\xff", "image/jpeg"),
            )
            s.commit()
            assert row2.icon_url == local
        finally:
            s.close()
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_upsert_with_category_id_skips_name_resolve(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.game_library import service, types
        from server.app.modules.image_library.models import StockCategory

        s = app.session_factory()
        try:
            cat = StockCategory(name="餐厅养成记", bucket_name="canting", kind="companion")
            s.add(cat)
            s.flush()
            g = types.Game(
                source="baidu",
                game_id="1",
                name="餐厅养成记",
                tags=["经营"],
                score=8.0,
                screenshot_urls=[],
            )
            row = service.upsert_game(s, g, category_id=cat.id, pre_downloaded=[])
            s.commit()
            assert row.stock_category_id == cat.id
        finally:
            s.close()
    finally:
        app.cleanup()
