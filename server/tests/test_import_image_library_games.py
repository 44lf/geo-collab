"""测试游戏库：图库栏目→游戏 幂等导入。"""

import pytest


@pytest.mark.mysql
def test_import_creates_and_is_idempotent(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.game_library import importer
        from server.app.modules.game_library.models import Game
        from server.app.modules.image_library.models import StockCategory

        s = app.session_factory()
        try:
            s.add(StockCategory(name="餐厅养成记", bucket_name="cant", kind="companion"))
            s.add(StockCategory(name="心动小镇", bucket_name="xin", kind="main"))
            s.flush()
            r1 = importer.import_image_categories_as_games(s)
            s.commit()
            assert r1["created"] == 2
            names = {g.name for g in s.query(Game).all()}
            assert names == {"餐厅养成记", "心动小镇"}
            r2 = importer.import_image_categories_as_games(s)
            s.commit()
            assert r2["created"] == 0 and r2["skipped"] == 2  # 幂等
        finally:
            s.close()
    finally:
        app.cleanup()
