import pytest
from fastapi.testclient import TestClient


def _seed(s):
    from server.app.modules.game_library.models import Game, GameTag
    from server.app.modules.image_library.models import StockCategory

    companion = StockCategory(
        name="陪衬栏目-web测试",
        bucket_name="companion-web-test",
        kind="companion",
    )
    main = StockCategory(
        name="主推栏目-web测试",
        bucket_name="main-web-test",
        kind="main",
    )
    s.add_all([companion, main])
    s.flush()

    def mk(name, *, score, category_id=None, is_active=True, tags=None):
        g = Game(
            name=name,
            name_normalized=name,
            score=score,
            use_count=0,
            is_active=is_active,
            stock_category_id=category_id,
        )
        s.add(g)
        s.flush()
        for tag in tags or []:
            s.add(GameTag(game_id=g.id, tag=tag))
        return g

    g_companion = mk("陪衬游戏A", score=9.0, category_id=companion.id, tags=["经营"])
    g_main = mk("主推游戏B", score=7.5, category_id=main.id, tags=["射击", "国风"])
    g_plain = mk("素游戏C", score=8.0)
    g_inactive = mk("停用游戏D", score=9.9, is_active=False)
    s.commit()
    # 提前取出 id（session 关闭后 ORM 对象会 detach，属性访问会炸）
    return {
        "companion_category_id": companion.id,
        "main_category_id": main.id,
        "companion_game_id": g_companion.id,
        "main_game_id": g_main.id,
        "plain_game_id": g_plain.id,
        "inactive_game_id": g_inactive.id,
    }


@pytest.mark.mysql
def test_web_list_games_excludes_inactive_and_orders_by_score(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        s = app.session_factory()
        try:
            _seed(s)
        finally:
            s.close()

        r = app.client.get("/api/game-library/games")
        assert r.status_code == 200
        body = r.json()
        assert "total" in body and "items" in body
        names = [item["name"] for item in body["items"]]
        assert "停用游戏D" not in names
        # 分数降序：陪衬游戏A(9.0) > 素游戏C(8.0) > 主推游戏B(7.5)
        assert names == ["陪衬游戏A", "素游戏C", "主推游戏B"]
        assert body["total"] == 3
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_web_list_games_filters_by_kind(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        s = app.session_factory()
        try:
            _seed(s)
        finally:
            s.close()

        r = app.client.get("/api/game-library/games", params={"kind": "companion"})
        assert r.status_code == 200
        body = r.json()
        names = [item["name"] for item in body["items"]]
        assert names == ["陪衬游戏A"]
        assert body["total"] == 1
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_web_get_game_detail_and_404(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        s = app.session_factory()
        try:
            seeded = _seed(s)
        finally:
            s.close()

        game_id = seeded["companion_game_id"]
        r = app.client.get(f"/api/game-library/games/{game_id}")
        assert r.status_code == 200
        detail = r.json()
        assert detail["name"] == "陪衬游戏A"
        assert detail["tags"] == ["经营"]
        assert detail["kind"] == "companion"

        r404 = app.client.get("/api/game-library/games/999999")
        assert r404.status_code == 404
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_web_list_game_tags(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        s = app.session_factory()
        try:
            _seed(s)
        finally:
            s.close()

        r = app.client.get("/api/game-library/tags")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, list)
        tags = {item["tag"] for item in body}
        assert {"经营", "射击", "国风"} <= tags
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_web_update_game(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        s = app.session_factory()
        try:
            seeded = _seed(s)
        finally:
            s.close()

        # companion_game_id 初始有标签 ["经营"]，验证 PATCH 会整体替换而非追加。
        game_id = seeded["companion_game_id"]
        r = app.client.patch(
            f"/api/game-library/games/{game_id}",
            json={
                "name": "改名后的游戏",
                "score": 6.5,
                "description": "新简介",
                "tags": ["卡牌", "二次元"],
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "改名后的游戏"
        assert body["name_normalized"]
        assert body["score"] == 6.5
        assert body["description"] == "新简介"
        assert sorted(body["tags"]) == ["二次元", "卡牌"]
        assert "经营" not in body["tags"]
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_web_update_game_overlapping_tags_does_not_500(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        s = app.session_factory()
        try:
            seeded = _seed(s)
        finally:
            s.close()

        # companion_game_id 初始有标签 ["经营"]；传入含旧标签的重叠集合曾触发
        # UNIQUE(game_id, tag) 违约 → 未捕获 500（最终 review C1）。差量替换后应 200。
        game_id = seeded["companion_game_id"]
        r = app.client.patch(
            f"/api/game-library/games/{game_id}",
            json={"tags": ["经营", "卡牌"]},
        )
        assert r.status_code == 200
        body = r.json()
        assert sorted(body["tags"]) == ["卡牌", "经营"]

        # 100% 重叠（原样传回）同样不能 500。
        r2 = app.client.patch(
            f"/api/game-library/games/{game_id}",
            json={"tags": ["经营", "卡牌"]},
        )
        assert r2.status_code == 200
        assert sorted(r2.json()["tags"]) == ["卡牌", "经营"]
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_web_update_game_rename_conflict_returns_409(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        s = app.session_factory()
        try:
            seeded = _seed(s)
        finally:
            s.close()

        # 把 companion_game_id 改名成 main_game_id 已存在的 name → 撞
        # uq_games_name_normalized → 未捕获 500（最终 review I1）。修复后应 409。
        game_id = seeded["companion_game_id"]
        r = app.client.patch(
            f"/api/game-library/games/{game_id}",
            json={"name": "主推游戏B"},
        )
        assert r.status_code == 409
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_web_update_game_partial_only_touches_given_fields(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        s = app.session_factory()
        try:
            seeded = _seed(s)
        finally:
            s.close()

        game_id = seeded["main_game_id"]
        r = app.client.patch(f"/api/game-library/games/{game_id}", json={"score": 3.3})
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "主推游戏B"
        assert body["score"] == 3.3
        assert sorted(body["tags"]) == ["国风", "射击"]
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_web_update_game_404(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        r = app.client.patch("/api/game-library/games/999999", json={"name": "不存在"})
        assert r.status_code == 404
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_web_delete_game(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        s = app.session_factory()
        try:
            seeded = _seed(s)
        finally:
            s.close()

        game_id = seeded["plain_game_id"]
        r = app.client.delete(f"/api/game-library/games/{game_id}")
        assert r.status_code == 204
        assert r.content == b""

        listing = app.client.get("/api/game-library/games")
        assert listing.status_code == 200
        names = [item["name"] for item in listing.json()["items"]]
        assert "素游戏C" not in names

        # get_game 不按 is_active 过滤，软删后详情仍可见、is_active 反映真实状态。
        detail = app.client.get(f"/api/game-library/games/{game_id}")
        assert detail.status_code == 200
        assert detail.json()["is_active"] is False
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_web_delete_game_404(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        r = app.client.delete("/api/game-library/games/999999")
        assert r.status_code == 404
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_web_game_library_requires_jwt(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        anon_client = TestClient(app.client.app)
        assert anon_client.get("/api/game-library/games").status_code == 401
        assert anon_client.get("/api/game-library/tags").status_code == 401
    finally:
        app.cleanup()
