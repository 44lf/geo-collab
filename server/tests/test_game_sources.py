import http.cookiejar
import json

from server.app.modules.game_library import registry
from server.app.modules.game_library.sources import baidu, taptap


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

    monkeypatch.setattr(taptap, "urlopen", lambda req, timeout=10: _FakeResponse(payload))

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


def _fake_xsrf_cookie():
    return http.cookiejar.Cookie(
        version=0,
        name="XSRF-TOKEN",
        value="test-token",
        port=None,
        port_specified=False,
        domain="www.taptap.cn",
        domain_specified=True,
        domain_initial_dot=False,
        path="/",
        path_specified=True,
        secure=False,
        expires=None,
        discard=True,
        comment=None,
        comment_url=None,
        rest={},
        rfc2109=False,
    )


def _fake_taptap_opener_factory(search_payload):
    """构造一个替代 `build_opener` 的工厂: 把 XSRF-TOKEN 塞进真实 CookieJar(模拟
    _bootstrap_xsrf 的 GET 响应 Set-Cookie),POST 请求则回放 search_payload。
    """

    class _FakeOpener:
        def open(self, req, timeout=10):
            if req.get_method() == "POST":
                return _FakeResponse(search_payload)
            return _FakeResponse({})

    def _fake_build_opener(*handlers):
        cookie_processor = handlers[0]
        cookie_processor.cookiejar.set_cookie(_fake_xsrf_cookie())
        return _FakeOpener()

    return _fake_build_opener


def test_taptap_search_by_name_maps_brand_hit(monkeypatch):
    search_payload = {
        "data": {
            "list": [
                {
                    "list": [
                        {"type": "moment", "moment": {}},
                        {
                            "type": "brand",
                            "brand": {
                                "app": {
                                    "id": 45213,
                                    "title": "心动小镇",
                                    "score": 9.2,
                                    "tags": [{"value": "养成"}, {"value": "经营"}],
                                    "supported_platforms": [{"key": "android"}],
                                    "icon": {"original_url": "http://x/icon.png"},
                                    "identifier": "com.x.y",
                                    "description": {"text": "慢生活<br>经营"},
                                }
                            },
                        },
                    ]
                }
            ]
        }
    }
    monkeypatch.setattr(taptap, "build_opener", _fake_taptap_opener_factory(search_payload))

    g = taptap.search_by_name("心动小镇")

    assert g is not None
    assert g.source == "taptap"
    assert g.game_id == "45213"
    assert g.name == "心动小镇"
    assert g.score == 9.2
    assert g.tags == ["养成", "经营"]
    assert g.platforms == ["android"]
    assert g.icon_url == "http://x/icon.png"
    assert g.android_package == "com.x.y"
    assert g.comment_count is None  # brand.stat 无评论数,恒为 None
    assert g.screenshot_urls == []  # search_by_name 不返回截图,靠调用方补 get_detail
    assert g.description == "慢生活经营"


def test_taptap_search_by_name_ignores_non_brand_and_title_mismatch(monkeypatch):
    search_payload = {
        "data": {
            "list": [
                {
                    "list": [
                        {"type": "moment", "moment": {}},
                        {
                            "type": "brand",
                            "brand": {"app": {"id": 1, "title": "心动小镇·同人集"}},
                        },
                        {
                            "type": "mix_app",
                            "mix_app": {"app": {"id": 2, "title": "心动小镇"}},
                        },
                    ]
                }
            ]
        }
    }
    monkeypatch.setattr(taptap, "build_opener", _fake_taptap_opener_factory(search_payload))

    assert taptap.search_by_name("心动小镇") is None


def test_taptap_search_by_name_missing_xsrf_cookie_raises(monkeypatch):
    class _NoOpOpener:
        def open(self, req, timeout=10):
            return _FakeResponse({})

    monkeypatch.setattr(taptap, "build_opener", lambda *handlers: _NoOpOpener())

    try:
        taptap.search_by_name("心动小镇")
    except RuntimeError as exc:
        assert "XSRF-TOKEN" in str(exc)
    else:
        raise AssertionError("expected RuntimeError when XSRF-TOKEN cookie is missing")
