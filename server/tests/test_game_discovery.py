"""扩库腿（应用宝榜单发现 → planb/db_ingest）测试。

覆盖 b706639 的三个修复点 + patrol_include_main：
- `_download_shots` 跳过已入库截图（skip_hashes），省重复下载 + 重复付费竖转横（无 DB）。
- `ingest_discovery` **桶归属回归**：撞已有游戏（尤其手工 main）必须复用其原素材桶，
  绝不 re-resolve 到新建空桶把原截图孤立（blocker #1 的守卫）。
- `ingest_discovery` 新游戏走 companion 自动建桶。
- `ingest_discovery` 端到端复用已入库截图 hash（成本回归）。
- `select_due_games(include_main=True)` 放宽覆盖 main（patrol_include_main）。
"""

from __future__ import annotations

import pytest

from server.app.modules.game_library.planb import db_ingest
from server.app.modules.game_library.planb.model import Game as PlanbGame
from server.app.modules.image_library.service import source_url_sha256


def _planb(name, *, source_id="com.x.app", shots=None, icon_url=None, score=8.0):
    return PlanbGame(
        source="yingyongbao",
        source_id=source_id,
        name=name,
        score=score,
        tags=["经营"],
        platforms=["android"],
        comment_count=10,
        icon_url=icon_url,
        screenshot_urls=list(shots or []),
        description="一段描述",
        highlight_comments=[],
    )


# --------------------------------------------------------------------------- #
# _download_shots：纯函数，无 DB
# --------------------------------------------------------------------------- #
def test_download_shots_skips_already_stored(monkeypatch):
    """skip_hashes 里已入库的 source_url_hash 直接跳过下载（不重复付费竖转横）。"""
    stored, fresh = "https://cdn/x/stored.jpg", "https://cdn/x/fresh.jpg"
    downloaded: list[str] = []

    def fake_download(url, **kw):
        downloaded.append(url)
        return b"\xff\xd8\xff", "image/jpeg"

    monkeypatch.setattr(db_ingest.image_download, "download_image", fake_download)
    monkeypatch.setattr(db_ingest.landscape, "to_landscape_if_portrait", lambda d, m: (d, m))

    out = db_ingest._download_shots(
        [stored, fresh], max_shots=6, skip_hashes={source_url_sha256(stored)}
    )

    assert stored not in downloaded  # 已入库 → 跳过
    assert fresh in downloaded
    assert [u for u, _, _ in out] == [fresh]


def test_download_shots_dedups_repeated_url(monkeypatch):
    downloaded: list[str] = []

    def fake_download(url, **kw):
        downloaded.append(url)
        return b"\xff\xd8\xff", "image/jpeg"

    monkeypatch.setattr(db_ingest.image_download, "download_image", fake_download)
    monkeypatch.setattr(db_ingest.landscape, "to_landscape_if_portrait", lambda d, m: (d, m))

    out = db_ingest._download_shots(["https://cdn/a.jpg", "https://cdn/a.jpg"], max_shots=6)

    assert downloaded == ["https://cdn/a.jpg"]  # 同 URL 只下一次
    assert len(out) == 1


# --------------------------------------------------------------------------- #
# ingest_discovery：需要 DB
# --------------------------------------------------------------------------- #
def _mock_minio(monkeypatch):
    from server.app.modules.image_library import store as minio_store

    monkeypatch.setattr(minio_store, "ensure_bucket", lambda *a, **k: None)
    monkeypatch.setattr(minio_store, "upload_image", lambda *a, **k: None)


def _mock_discover(monkeypatch, planb_games):
    monkeypatch.setattr(
        db_ingest.discovery_ingest, "discover", lambda client, **kw: list(planb_games)
    )


@pytest.mark.mysql
def test_ingest_discovery_new_game_creates_companion(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.game_library.models import Game
        from server.app.modules.image_library.models import StockCategory

        _mock_minio(monkeypatch)
        _mock_discover(monkeypatch, [_planb("全新扩库游戏X", source_id="com.x.newx")])

        summary = db_ingest.ingest_discovery(app.session_factory, min_interval=0)
        assert summary["discovered"] == 1
        assert summary["new"] == 1
        assert summary["upserted"] == 1

        s = app.session_factory()
        try:
            row = s.query(Game).filter(Game.name_normalized == "全新扩库游戏X").one()
            cat = s.get(StockCategory, row.stock_category_id)
            assert cat is not None and cat.kind == "companion"
        finally:
            s.close()
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_ingest_discovery_existing_game_keeps_its_category(monkeypatch):
    """桶归属回归（blocker #1）：撞已有游戏（挂在手工 main 桶上，桶名≠游戏名）时，
    必须复用该 main 桶、不 re-resolve 到新建 companion 空桶。买版前生产就栽在这。"""
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.articles.formatting.document import _normalize_game_name
        from server.app.modules.game_library.models import Game
        from server.app.modules.image_library.models import StockCategory

        _mock_minio(monkeypatch)

        norm = _normalize_game_name("餐厅养成记") or "餐厅养成记"
        s = app.session_factory()
        try:
            # 手工 main 桶：桶名带装饰、精确 ≠ 游戏名（正是跨源分叉触发 bug 的场景）。
            main_cat = StockCategory(name="主推·餐厅养成记", bucket_name="zhutui-ct", kind="main")
            s.add(main_cat)
            s.flush()
            g = Game(
                name="餐厅养成记",
                name_normalized=norm,
                stock_category_id=main_cat.id,
                is_active=True,
            )
            s.add(g)
            s.commit()
            main_cat_id = main_cat.id
            game_id = g.id
        finally:
            s.close()

        # 应用宝发现同名游戏（score 更高，触发合并）。
        _mock_discover(monkeypatch, [_planb("餐厅养成记", source_id="com.x.ct", score=9.5)])
        summary = db_ingest.ingest_discovery(app.session_factory, min_interval=0)
        assert summary["new"] == 0  # 不是新游戏
        assert summary["upserted"] == 1

        s2 = app.session_factory()
        try:
            row = s2.get(Game, game_id)
            # 关键断言：桶归属不变，仍是原 main 桶（旧 bug 会改指到新建 companion）。
            assert row.stock_category_id == main_cat_id
            # 绝不新建以游戏名命名的 companion 桶。
            leaked = s2.query(StockCategory).filter(StockCategory.name == "餐厅养成记").first()
            assert leaked is None
            # 未产生重复游戏行；合并生效（score 取更高）。
            assert s2.query(Game).filter(Game.name_normalized == norm).count() == 1
            assert row.score == 9.5
        finally:
            s2.close()
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_ingest_discovery_reuses_existing_shot_hashes(monkeypatch):
    """端到端成本回归：已入库截图 URL 不再重复下载 + 付费竖转横，仅新 URL 走下载。"""
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.articles.formatting.document import _normalize_game_name
        from server.app.modules.game_library.models import Game
        from server.app.modules.image_library.models import StockCategory, StockImage

        _mock_minio(monkeypatch)

        stored, fresh = "https://cdn/x/stored.jpg", "https://cdn/x/fresh.jpg"
        norm = _normalize_game_name("蛋仔派对") or "蛋仔派对"
        s = app.session_factory()
        try:
            comp = StockCategory(name="蛋仔派对", bucket_name="danzai", kind="companion")
            s.add(comp)
            s.flush()
            g = Game(
                name="蛋仔派对", name_normalized=norm, stock_category_id=comp.id, is_active=True
            )
            s.add(g)
            s.flush()
            s.add(
                StockImage(
                    category_id=comp.id,
                    minio_key="k.jpg",
                    filename="k.jpg",
                    source_url=stored,
                    source_url_hash=source_url_sha256(stored),
                )
            )
            s.commit()
        finally:
            s.close()

        downloaded: list[str] = []

        def fake_download(url, **kw):
            downloaded.append(url)
            return b"\xff\xd8\xff", "image/jpeg"

        monkeypatch.setattr(db_ingest.image_download, "download_image", fake_download)
        monkeypatch.setattr(db_ingest.landscape, "to_landscape_if_portrait", lambda d, m: (d, m))
        _mock_discover(
            monkeypatch, [_planb("蛋仔派对", source_id="com.x.dz", shots=[stored, fresh])]
        )

        db_ingest.ingest_discovery(app.session_factory, min_interval=0)

        assert stored not in downloaded  # 已入库 → 跳过下载+竖转横
        assert fresh in downloaded
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_select_due_games_include_main_widens_to_main(monkeypatch):
    """patrol_include_main=True 时 select_due_games 放宽覆盖 main；默认仍只 companion。"""
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
            g_comp = Game(
                name="c1", name_normalized="c1", stock_category_id=comp.id, is_active=True
            )
            g_main = Game(
                name="m1", name_normalized="m1", stock_category_id=main.id, is_active=True
            )
            s.add_all([g_comp, g_main])
            s.commit()

            default_due = ingest_service.select_due_games(s, limit=10)
            assert g_comp.id in default_due and g_main.id not in default_due  # 默认只 companion

            widened = ingest_service.select_due_games(s, limit=10, include_main=True)
            assert g_comp.id in widened and g_main.id in widened  # 放宽后覆盖 main
        finally:
            s.close()
    finally:
        app.cleanup()
