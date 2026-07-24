"""xhs_cards.previews 逻辑测试（不触 Playwright / MinIO 真实网络）。"""

import pytest

from server.app.modules.xhs_cards import previews, render, store


def test_preview_keys_versioned():
    v = previews.PREVIEW_VERSION
    cover, card = previews.preview_keys("sketch")
    assert cover == f"theme-previews/{v}/sketch/cover.png"
    assert card == f"theme-previews/{v}/sketch/card.png"


def test_list_theme_previews_cached_flag(monkeypatch):
    # 只有 sketch 的两个 key 有缓存（用 preview_keys 派生，版本无关）
    sketch_keys = set(previews.preview_keys("sketch"))
    monkeypatch.setattr(store, "object_exists", lambda key: key in sketch_keys)
    rows = previews.list_theme_previews()
    assert {r["name"] for r in rows} == set(render.AVAILABLE_THEMES)
    by = {r["name"]: r for r in rows}
    assert by["sketch"]["cached"] is True
    assert by["default"]["cached"] is False
    assert by["sketch"]["cover_url"] == "/api/xhs-cards/themes/sketch/preview/cover"
    assert by["sketch"]["card_url"] == "/api/xhs-cards/themes/sketch/preview/card"
    assert by["sketch"]["label"]  # 非空


def test_regenerate_all_puts_cover_and_card(monkeypatch):
    puts = []

    async def fake_render(md, *, theme, mode, width=1080, height=1440, dpr=2):
        return {"cover": b"COVER", "cards": [b"CARD1", b"CARD2"]}

    monkeypatch.setattr(render, "render_markdown_to_card_bytes", fake_render)
    monkeypatch.setattr(store, "ensure_bucket", lambda: None)
    monkeypatch.setattr(store, "put_png", lambda k, d: puts.append((k, d)))
    previews.regenerate_all_previews()
    # 每主题 2 次 put（cover + card[0]）
    assert len(puts) == 2 * len(render.AVAILABLE_THEMES)
    sk_cover, sk_card = previews.preview_keys("sketch")
    assert (sk_cover, b"COVER") in puts
    assert (sk_card, b"CARD1") in puts


def test_regenerate_skips_failing_theme(monkeypatch):
    puts = []

    async def flaky(md, *, theme, mode, width=1080, height=1440, dpr=2):
        if theme == "sketch":
            raise RuntimeError("boom")
        return {"cover": b"C", "cards": [b"D"]}

    monkeypatch.setattr(render, "render_markdown_to_card_bytes", flaky)
    monkeypatch.setattr(store, "ensure_bucket", lambda: None)
    monkeypatch.setattr(store, "put_png", lambda k, d: puts.append(k))
    previews.regenerate_all_previews()  # 不抛
    sketch_keys = set(previews.preview_keys("sketch"))
    default_keys = set(previews.preview_keys("default"))
    assert not (sketch_keys & set(puts))  # 失败主题一张都没写
    assert default_keys & set(puts)  # 其它主题照常写


def test_ensure_prewarmed_spawns_when_incomplete(monkeypatch):
    called = []
    monkeypatch.setattr(
        previews, "list_theme_previews", lambda: [{"cached": False}, {"cached": True}]
    )
    monkeypatch.setattr(previews, "spawn_regenerate", lambda: called.append(True) or True)
    assert previews.ensure_prewarmed() is True
    assert called == [True]


def test_ensure_prewarmed_skips_when_all_cached(monkeypatch):
    called = []
    monkeypatch.setattr(
        previews, "list_theme_previews", lambda: [{"cached": True}, {"cached": True}]
    )
    monkeypatch.setattr(previews, "spawn_regenerate", lambda: called.append(True) or True)
    assert previews.ensure_prewarmed() is False
    assert called == []


@pytest.mark.mysql
def test_themes_requires_login(monkeypatch):
    from server.tests.utils import build_test_app

    test_app = build_test_app(monkeypatch)
    try:
        # 未登录 client：清 cookie 后请求应 401
        c = test_app.client
        c.cookies.clear()
        r = c.get("/api/xhs-cards/themes")
        assert r.status_code == 401
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_themes_list_and_regenerate(monkeypatch):
    from server.tests.utils import build_test_app

    test_app = build_test_app(monkeypatch)
    try:
        monkeypatch.setattr(previews, "spawn_regenerate", lambda: True)  # 不真起线程
        # 判定「未生成」用桩，避免依赖 MinIO 真实残留状态（跨测试运行不可靠）
        monkeypatch.setattr(store, "object_exists", lambda key: False)
        r = test_app.client.get("/api/xhs-cards/themes")
        assert r.status_code == 200
        data = r.json()["data"]
        assert "generating" in data  # 新形状：{themes, generating}
        themes = data["themes"]
        assert len(themes) >= 8 and "cached" in themes[0] and "cover_url" in themes[0]

        g = test_app.client.post("/api/xhs-cards/themes/regenerate")
        assert g.status_code == 202

        # 未生成的预览图 → 404（object_exists 桩恒 False）
        p = test_app.client.get("/api/xhs-cards/themes/sketch/preview/cover")
        assert p.status_code == 404
        # 未知主题 → 404
        p2 = test_app.client.get("/api/xhs-cards/themes/nope/preview/cover")
        assert p2.status_code == 404
    finally:
        test_app.cleanup()
