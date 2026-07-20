import json

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


def test_registry_collect_pool_keeps_partial_results_on_later_page_failure(monkeypatch):
    calls = []

    def fake_search(source, category, *, page=1, page_size=20, **kw):
        calls.append((page, page_size))
        if page == 2:
            raise RuntimeError("upstream failed")
        return [f"game-{idx}" for idx in range(page_size)]

    monkeypatch.setattr(registry, "search", fake_search)

    pool = registry.collect_pool("baidu", "养成", 30, page_size_cap=20)

    assert len(pool) == 20
    assert calls == [(1, 20), (2, 10)]


def test_registry_collect_pool_zero_page_cap_returns_empty(monkeypatch):
    calls = []

    def fake_search(source, category, *, page=1, page_size=20, **kw):
        calls.append((page, page_size))
        return [object()]

    monkeypatch.setattr(registry, "search", fake_search)

    assert registry.collect_pool("baidu", "养成", 30, page_size_cap=0) == []
    assert calls == []


def test_taptap_get_detail_reads_data_app_mapping(monkeypatch):
    payload = {
        "data": {
            "app": {
                "id": 45213,
                "title": "心动小镇",
                "stat": {"rating": {"score": 9.2}, "review_count": 10},
                "tags": [{"value": "养成"}, {"value": "经营"}, {"value": ""}],
                "platform_info": {"supported_platforms": [{"key": "android"}, {"key": "pc"}]},
                "icon": {"url": "http://x/icon-small.png", "original_url": "http://x/icon.png"},
                "screenshots": [
                    {"original_url": "http://x/1.jpg"},
                    {"url": "http://x/2.jpg"},
                    {},
                ],
                "identifier": "com.xd.xdt",
                "description": {"text": "慢节奏<br>生活"},
            }
        }
    }

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def read(self):
            return json.dumps(payload).encode("utf-8")

    monkeypatch.setattr(taptap, "urlopen", lambda req, timeout=10: FakeResponse())

    game = taptap.get_detail("45213")

    assert game.game_id == "45213"
    assert game.name == "心动小镇"
    assert game.score == 9.2
    assert game.comment_count == 10
    assert game.tags == ["养成", "经营"]
    assert game.platforms == ["android", "pc"]
    assert game.icon_url == "http://x/icon.png"
    assert game.screenshot_urls == ["http://x/1.jpg", "http://x/2.jpg"]
    assert game.android_package == "com.xd.xdt"
    assert game.description == "慢节奏生活"
