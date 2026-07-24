import json

from server.app.modules.game_library import registry
from server.app.modules.game_library.sources import baidu


class _FakeResponse:
    """Minimal context-manager stand-in for `urlopen`'s response object."""

    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


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


def test_baidu_search_by_name_maps_exact_match(monkeypatch):
    payload = {
        "result": {
            "data": [
                {
                    "gameId": 1,
                    "gameName": "餐厅养成记",
                    "gameTypes": ["手游", "策略经营"],
                    "gameIcon": "http://x/icon.png",
                },
                # 同前缀但非精确同名的联想结果,必须被跳过而不是误命中
                {"gameId": 2, "gameName": "餐厅养成记2", "gameTypes": ["手游"]},
            ]
        }
    }
    monkeypatch.setattr(baidu, "urlopen", lambda req, timeout=10: _FakeResponse(payload))

    g = baidu.search_by_name("餐厅养成记")

    assert g is not None
    assert g.source == "baidu"
    assert g.game_id == "1"
    assert g.name == "餐厅养成记"
    assert g.tags == ["手游", "策略经营"]
    assert g.icon_url == "http://x/icon.png"
    # game_query 接口本身不携带这些字段,恒为 None/空
    assert g.score is None
    assert g.platforms == []
    assert g.comment_count is None
    assert g.screenshot_urls == []
    assert g.description is None


def test_baidu_search_by_name_no_exact_match_returns_none(monkeypatch):
    payload = {"result": {"data": [{"gameId": 9, "gameName": "餐厅养成记·豪华版"}]}}
    monkeypatch.setattr(baidu, "urlopen", lambda req, timeout=10: _FakeResponse(payload))

    assert baidu.search_by_name("餐厅养成记") is None


def test_baidu_search_by_name_empty_result_returns_none(monkeypatch):
    payload = {"result": {"data": []}}
    monkeypatch.setattr(baidu, "urlopen", lambda req, timeout=10: _FakeResponse(payload))

    assert baidu.search_by_name("不存在的游戏") is None
