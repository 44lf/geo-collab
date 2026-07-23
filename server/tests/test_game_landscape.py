"""游戏库存图竖转横（Seedream 4.0 扩图）单测：纯函数 + best-effort 韧性，不打真实 API、不需 DB。"""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest

from server.app.modules.game_library import landscape


def _png(width: int, height: int) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), (200, 210, 220)).save(buf, format="PNG")
    return buf.getvalue()


def _settings(**over) -> SimpleNamespace:
    base = dict(
        game_landscape_enabled=True,
        game_landscape_api_key="ark-test-key",
        game_landscape_model="doubao-seedream-4-0-250828",
        game_landscape_base_url="https://ark.example/api/v3",
        game_landscape_size="2048x1152",
        game_landscape_timeout_seconds=30,
    )
    base.update(over)
    return SimpleNamespace(**base)


# ── is_portrait ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "w,h,expected",
    [
        (720, 1280, True),  # 竖
        (1280, 720, False),  # 横
        (500, 500, False),  # 方
        (0, 100, False),  # 缺尺寸
        (100, 0, False),
        (None, None, False),
    ],
)
def test_is_portrait(w, h, expected):
    assert landscape.is_portrait(w, h) is expected


# ── 门禁：未启用 / 无 key / 横图 → 透传且零调用 ────────────────────────────────
def test_passthrough_when_disabled(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(landscape, "_seedream_edit", lambda *a, **k: called.__setitem__("n", 1))
    data = _png(720, 1280)
    out, mime = landscape.to_landscape_if_portrait(
        data, "image/png", settings=_settings(game_landscape_enabled=False)
    )
    assert out is data and mime == "image/png"
    assert called["n"] == 0


def test_passthrough_when_no_key(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(landscape, "_seedream_edit", lambda *a, **k: called.__setitem__("n", 1))
    data = _png(720, 1280)
    out, mime = landscape.to_landscape_if_portrait(
        data, "image/png", settings=_settings(game_landscape_api_key="")
    )
    assert out is data and called["n"] == 0


def test_landscape_input_is_not_converted(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(landscape, "_seedream_edit", lambda *a, **k: called.__setitem__("n", 1))
    data = _png(1280, 720)  # 横图
    out, mime = landscape.to_landscape_if_portrait(data, "image/png", settings=_settings())
    assert out is data and called["n"] == 0  # 横图零 API 调用


def test_non_image_passthrough(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(landscape, "_seedream_edit", lambda *a, **k: called.__setitem__("n", 1))
    data = b"not-an-image"
    out, mime = landscape.to_landscape_if_portrait(data, "image/png", settings=_settings())
    assert out is data and called["n"] == 0


# ── 转换路径 ─────────────────────────────────────────────────────────────────
def test_portrait_converts(monkeypatch):
    monkeypatch.setattr(
        landscape,
        "_seedream_edit",
        lambda data, mime, settings: (b"LANDSCAPE-BYTES", "image/png"),
    )
    data = _png(720, 1280)
    out, mime = landscape.to_landscape_if_portrait(data, "image/png", settings=_settings())
    assert out == b"LANDSCAPE-BYTES" and mime == "image/png"


def test_convert_failure_keeps_original(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(landscape, "_seedream_edit", boom)
    data = _png(720, 1280)
    out, mime = landscape.to_landscape_if_portrait(data, "image/png", settings=_settings())
    assert out is data and mime == "image/png"  # 失败原样保留竖图


def test_convert_returns_none_keeps_original(monkeypatch):
    monkeypatch.setattr(landscape, "_seedream_edit", lambda *a, **k: None)
    data = _png(720, 1280)
    out, mime = landscape.to_landscape_if_portrait(data, "image/png", settings=_settings())
    assert out is data


# ── _seedream_edit 解析（monkeypatch httpx，不打真实网络）───────────────────────
def _fake_post(payload):
    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return payload

    return lambda *a, **k: FakeResp()


def test_seedream_edit_parses_b64(monkeypatch):
    import base64

    png = _png(2048, 1152)  # 真 PNG，mime 按文件头嗅探而非硬编码
    monkeypatch.setattr(
        "httpx.post", _fake_post({"data": [{"b64_json": base64.b64encode(png).decode()}]})
    )
    out = landscape._seedream_edit(b"input", "image/png", _settings())
    assert out == (png, "image/png")


def test_seedream_edit_b64_non_image_returns_none(monkeypatch):
    import base64

    # b64 内容不是图片（嗅探失败）→ 当失败处理返回 None，上层原样保留竖图
    monkeypatch.setattr(
        "httpx.post", _fake_post({"data": [{"b64_json": base64.b64encode(b"garbage").decode()}]})
    )
    assert landscape._seedream_edit(b"input", "image/png", _settings()) is None


def test_seedream_edit_url_fallback(monkeypatch):
    # 上游改走 url：复用通用下载器（此处 stub），透传其 (bytes, mime)
    monkeypatch.setattr("httpx.post", _fake_post({"data": [{"url": "https://cdn.example/y.png"}]}))
    monkeypatch.setattr(
        "server.app.shared.image_download.download_image",
        lambda url, **k: (b"DOWNLOADED", "image/jpeg"),
    )
    out = landscape._seedream_edit(b"input", "image/png", _settings())
    assert out == (b"DOWNLOADED", "image/jpeg")


def test_seedream_edit_empty_data_returns_none(monkeypatch):
    monkeypatch.setattr("httpx.post", _fake_post({"data": []}))
    assert landscape._seedream_edit(b"input", "image/png", _settings()) is None


def test_seedream_edit_empty_item_returns_none(monkeypatch):
    # 有 data 但既无 b64_json 也无 url → None
    monkeypatch.setattr("httpx.post", _fake_post({"data": [{}]}))
    assert landscape._seedream_edit(b"input", "image/png", _settings()) is None
