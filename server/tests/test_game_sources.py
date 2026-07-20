from server.app.modules.game_library import registry
from server.app.modules.game_library.sources import baidu, taptap


def test_baidu_to_game_maps_screenshots_and_score():
    raw = {
        "gameId": 123,
        "gameName": "餐厅养成记",
        "gameScore": "8.7",
        "gameTags": ["经营", "养成"],
        "gamePlatform": ["Android", "IOS"],
        "commentCount": 42,
        "gameIcon": "http://x/icon.png",
        "gameOfficialPic": ["http://x/1.jpg", "http://x/2.jpg"],
        "gameDesc": "开一家餐厅",
    }
    g = baidu._to_game(raw)
    assert g.source == "baidu" and g.game_id == "123" and g.name == "餐厅养成记"
    assert g.score == 8.7 and g.comment_count == 42
    assert g.screenshot_urls == ["http://x/1.jpg", "http://x/2.jpg"]
    assert g.tags == ["经营", "养成"]


def test_taptap_by_tag_has_placeholder_tag_and_no_screenshots():
    raw = {
        "id": 45213,
        "title": "心动小镇",
        "stat": {"rating": {"score": 9.2}, "review_count": 10},
        "icon": {"original_url": "http://x/i.png"},
        "identifier": "com.x.y",
    }
    g = taptap._to_game(raw, "养成")
    assert g.tags == ["养成"]  # 占位=查询分类名
    assert g.screenshot_urls == []  # 列表接口无截图
    assert g.android_package == "com.x.y"


def test_registry_collect_pool_paginates(monkeypatch):
    calls = []

    def fake_search(source, category, *, page=1, page_size=20, **kw):
        calls.append((page, page_size))
        return [] if page > 2 else [object()] * page_size

    monkeypatch.setattr(registry, "search", fake_search)
    pool = registry.collect_pool("baidu", "养成", 30, page_size_cap=20)
    assert len(pool) == 30 and calls[0] == (1, 20)
