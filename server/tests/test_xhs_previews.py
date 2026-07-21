"""xhs_cards.previews 逻辑测试（不触 Playwright / MinIO 真实网络）。"""

from server.app.modules.xhs_cards import previews, render, store


def test_preview_keys():
    cover, card = previews.preview_keys("sketch")
    assert cover == "theme-previews/sketch/cover.png"
    assert card == "theme-previews/sketch/card.png"


def test_list_theme_previews_cached_flag(monkeypatch):
    # 只有 sketch 有缓存
    def fake_exists(key):
        return key.startswith("theme-previews/sketch/")

    monkeypatch.setattr(store, "object_exists", fake_exists)
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
    assert ("theme-previews/sketch/cover.png", b"COVER") in puts
    assert ("theme-previews/sketch/card.png", b"CARD1") in puts


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
    assert not any(k.startswith("theme-previews/sketch/") for k in puts)
    assert any(k.startswith("theme-previews/default/") for k in puts)
