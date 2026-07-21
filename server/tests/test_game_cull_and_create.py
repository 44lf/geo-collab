"""游戏库：并集刷新 + 无证据自动软删 + 手动新建 —— 测试。"""

from types import SimpleNamespace

import pytest


def _game(**kw):
    """构造一个 types.Game（默认无截图，避免测试触网下载）。"""
    from server.app.modules.game_library import types

    base = dict(
        source="taptap",
        game_id="1",
        name="X",
        score=None,
        tags=[],
        platforms=[],
        comment_count=None,
        icon_url=None,
        screenshot_urls=[],
        description=None,
        raw={},
    )
    base.update(kw)
    return types.Game(**base)


# ── 纯逻辑（无 DB）────────────────────────────────────────────────────────────


def test_iso_appends_z_for_naive_utc():
    """游戏时间戳预先 isoformat 成字符串会绕过 main.py 只认 datetime 的全局补丁，
    必须自己补 "Z"，否则前端 new Date 把裸 UTC 当本地时区、差 8 小时。"""
    from datetime import UTC, datetime

    from server.app.modules.game_library.ingest_service import _iso as ing_iso
    from server.app.modules.game_library.service import _iso as svc_iso

    naive = datetime(2026, 7, 20, 9, 47, 24)  # naive UTC
    assert svc_iso(naive) == "2026-07-20T09:47:24Z"
    assert ing_iso(naive) == "2026-07-20T09:47:24Z"
    # tz-aware 不重复加 Z（isoformat 已带 +00:00）
    aware = datetime(2026, 7, 20, 9, 47, 24, tzinfo=UTC)
    assert svc_iso(aware).endswith("+00:00")
    assert not svc_iso(aware).endswith("Z")
    assert svc_iso(None) is None


def test_normalized_matcher_forgives_format_diffs_not_substrings():
    from server.app.modules.game_library.ingest_service import _normalized_matcher as m

    assert m("原神", "原神")
    assert m("原 神", "原神")  # 去内部空格
    assert m("死亡细胞!", "死亡细胞")  # 去标点
    assert m("ＡＢＣ", "abc")  # NFKC 折全角 + 小写
    assert m("Old Man's Journey", "old mans journey")  # 撇号/大小写
    # 子串/不同名不误配
    assert not m("斗地主", "欢乐斗地主")
    assert not m("原神", "原神2")
    # None / 空 → False
    assert not m(None, "x")
    assert not m("x", None)
    assert not m("", "x")


def test_collect_from_all_sources_union_and_error_vs_miss(monkeypatch):
    from server.app.modules.game_library import ingest_service
    from server.app.modules.game_library.sources import baidu, taptap

    # 两源都命中 → 并集 2 条
    monkeypatch.setattr(
        taptap,
        "search_by_name",
        lambda name, *, matcher=None: _game(source="taptap", score=8.5, screenshot_urls=["u"]),
    )
    monkeypatch.setattr(
        baidu, "search_by_name", lambda name, *, matcher=None: _game(source="baidu", score=7.0)
    )
    out = ingest_service._collect_from_all_sources("taptap,baidu", "原神")
    assert len(out["hits"]) == 2
    assert out["per_source"] == {"taptap": "hit", "baidu": "hit"}

    # taptap error / baidu miss → error 与 miss 严格区分、无 hit
    def boom(name, *, matcher=None):
        raise RuntimeError("net down")

    monkeypatch.setattr(taptap, "search_by_name", boom)
    monkeypatch.setattr(baidu, "search_by_name", lambda name, *, matcher=None: None)
    out = ingest_service._collect_from_all_sources("taptap,baidu", "原神")
    assert out["hits"] == []
    assert out["per_source"] == {"taptap": "error", "baidu": "miss"}


def test_collect_taptap_hit_without_shots_calls_get_detail(monkeypatch):
    from server.app.modules.game_library import ingest_service
    from server.app.modules.game_library.sources import taptap

    monkeypatch.setattr(
        taptap,
        "search_by_name",
        lambda name, *, matcher=None: _game(game_id="42", screenshot_urls=[]),
    )
    called = {}

    def fake_detail(gid):
        called["gid"] = gid
        return _game(game_id="42", screenshot_urls=["a", "b"])

    monkeypatch.setattr(taptap, "get_detail", fake_detail)
    out = ingest_service._collect_from_all_sources("taptap", "原神")
    assert called["gid"] == "42"
    assert out["hits"][0].screenshot_urls == ["a", "b"]


def test_has_evidence():
    from server.app.modules.game_library.ingest_service import _has_evidence

    assert not _has_evidence(
        SimpleNamespace(score=None, description=None, comment_count=None, sources=[])
    )
    assert not _has_evidence(
        SimpleNamespace(score=None, description="  ", comment_count=None, sources=None)
    )
    assert _has_evidence(
        SimpleNamespace(score=8.0, description=None, comment_count=None, sources=[])
    )
    assert _has_evidence(
        SimpleNamespace(score=None, description="好玩", comment_count=None, sources=[])
    )
    assert _has_evidence(SimpleNamespace(score=None, description=None, comment_count=5, sources=[]))
    assert _has_evidence(
        SimpleNamespace(
            score=None, description=None, comment_count=None, sources=[{"source": "taptap"}]
        )
    )


# ── DB 后端（mysql）──────────────────────────────────────────────────────────


def _mk_companion_game(s, name, **kw):
    from server.app.modules.game_library.models import Game
    from server.app.modules.image_library.models import StockCategory

    cat = StockCategory(name=f"cat-{name}", bucket_name=f"bk-{name}"[:60], kind="companion")
    s.add(cat)
    s.flush()
    g = Game(
        name=name,
        name_normalized=name,
        stock_category_id=cat.id,
        is_active=True,
        sources=[],
        **kw,
    )
    s.add(g)
    s.commit()
    return g.id


@pytest.mark.mysql
def test_refresh_union_merges_and_resets_streak(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.game_library import ingest_service
        from server.app.modules.game_library.models import Game

        s = app.session_factory()
        try:
            gid = _mk_companion_game(s, "并集游戏", not_found_streak=2, score=None)
        finally:
            s.close()

        monkeypatch.setattr(
            ingest_service,
            "_collect_from_all_sources",
            lambda source_order, name: {
                "hits": [
                    _game(source="taptap", score=8.5, tags=["卡牌"]),
                    _game(source="baidu", score=7.0, tags=["策略"]),
                ],
                "per_source": {"taptap": "hit", "baidu": "hit"},
            },
        )

        result = ingest_service.refresh_one_game(
            app.session_factory, gid, source_order="taptap,baidu", max_shots=6
        )
        assert result["outcome"] == "refreshed"

        s2 = app.session_factory()
        try:
            g = s2.get(Game, gid)
            assert g.score == 8.5  # 取 max
            assert g.not_found_streak == 0  # 命中归零
            assert len(g.sources) == 2  # 两源并集
        finally:
            s2.close()
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_refresh_all_miss_increments_and_culls_when_no_evidence(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.game_library import ingest_service
        from server.app.modules.game_library.models import Game

        s = app.session_factory()
        try:
            gid = _mk_companion_game(s, "空壳游戏", not_found_streak=2, score=None)
        finally:
            s.close()

        monkeypatch.setattr(
            ingest_service,
            "_collect_from_all_sources",
            lambda source_order, name: {
                "hits": [],
                "per_source": {"taptap": "miss", "baidu": "miss"},
            },
        )
        result = ingest_service.refresh_one_game(
            app.session_factory,
            gid,
            source_order="taptap,baidu",
            max_shots=6,
            cull_after_misses=3,
            cull_enabled=True,
        )
        assert result["outcome"] == "culled"

        s2 = app.session_factory()
        try:
            g = s2.get(Game, gid)
            assert g.not_found_streak == 3
            assert g.is_active is False
        finally:
            s2.close()
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_refresh_all_miss_but_has_evidence_not_culled(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.game_library import ingest_service
        from server.app.modules.game_library.models import Game

        s = app.session_factory()
        try:
            gid = _mk_companion_game(s, "有分游戏", not_found_streak=2, score=8.0)
        finally:
            s.close()

        monkeypatch.setattr(
            ingest_service,
            "_collect_from_all_sources",
            lambda source_order, name: {
                "hits": [],
                "per_source": {"taptap": "miss", "baidu": "miss"},
            },
        )
        result = ingest_service.refresh_one_game(
            app.session_factory,
            gid,
            source_order="taptap,baidu",
            max_shots=6,
            cull_after_misses=3,
            cull_enabled=True,
        )
        assert result["outcome"] == "not_found"

        s2 = app.session_factory()
        try:
            g = s2.get(Game, gid)
            assert g.is_active is True  # 有证据 → 豁免
        finally:
            s2.close()
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_refresh_error_does_not_increment_streak(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.game_library import ingest_service
        from server.app.modules.game_library.models import Game

        s = app.session_factory()
        try:
            gid = _mk_companion_game(s, "报错游戏", not_found_streak=2, score=None)
        finally:
            s.close()

        monkeypatch.setattr(
            ingest_service,
            "_collect_from_all_sources",
            lambda source_order, name: {
                "hits": [],
                "per_source": {"taptap": "error", "baidu": "miss"},
            },
        )
        result = ingest_service.refresh_one_game(
            app.session_factory,
            gid,
            source_order="taptap,baidu",
            max_shots=6,
            cull_after_misses=3,
            cull_enabled=True,
        )
        assert result["outcome"] == "error"

        s2 = app.session_factory()
        try:
            g = s2.get(Game, gid)
            assert g.not_found_streak == 2  # error 不动 streak
            assert g.is_active is True
        finally:
            s2.close()
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_refresh_manually_curated_is_cull_exempt(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.game_library import ingest_service
        from server.app.modules.game_library.models import Game

        s = app.session_factory()
        try:
            gid = _mk_companion_game(
                s, "人工游戏", not_found_streak=2, score=None, manually_curated=True
            )
        finally:
            s.close()

        monkeypatch.setattr(
            ingest_service,
            "_collect_from_all_sources",
            lambda source_order, name: {
                "hits": [],
                "per_source": {"taptap": "miss", "baidu": "miss"},
            },
        )
        result = ingest_service.refresh_one_game(
            app.session_factory,
            gid,
            source_order="taptap,baidu",
            max_shots=6,
            cull_after_misses=3,
            cull_enabled=True,
        )
        assert result["outcome"] == "not_found"

        s2 = app.session_factory()
        try:
            assert s2.get(Game, gid).is_active is True
        finally:
            s2.close()
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_create_game_dedup_and_manually_curated(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.game_library import service
        from server.app.modules.game_library.models import Game
        from server.app.modules.image_library.models import StockCategory
        from server.app.shared.errors import ConflictError

        def fake_goc(db, name, *, commit=False):
            cat = StockCategory(name=name, bucket_name=f"cbk-{name}"[:60], kind="companion")
            db.add(cat)
            db.flush()
            return cat

        monkeypatch.setattr(service, "get_or_create_companion_category", fake_goc)

        s = app.session_factory()
        try:
            g = service.create_game(s, name="手动新游", score=9.0, tags=["动作", "动作"])
            s.commit()
            assert g.manually_curated is True
            assert g.stock_category_id is not None
            row = s.get(Game, g.id)
            assert row.score == 9.0
            assert {t.tag for t in row.tags} == {"动作"}  # 去重

            with pytest.raises(ConflictError):
                service.create_game(s, name="手动新游")
        finally:
            s.close()
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_update_game_sets_manually_curated(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.game_library import service
        from server.app.modules.game_library.models import Game

        s = app.session_factory()
        try:
            gid = _mk_companion_game(s, "待编辑", score=None)
            assert s.get(Game, gid).manually_curated is False
            service.update_game(s, gid, {"score": 6.5})
            s.commit()
            assert s.get(Game, gid).manually_curated is True
        finally:
            s.close()
    finally:
        app.cleanup()
