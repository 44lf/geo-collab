"""`/api/stock-images/{id}/file` 交付优化：HTTP 缓存头 + 按需 ?w= 缩略图。

见 docs/superpowers/specs/2026-07-22-stock-image-delivery-optimization-design.md。
测试环境无 MinIO：建桶/上传打 no-op，读对象（get_object_bytes）打成返回本地 Pillow 生成的
真字节，让缩放路径真的跑。
"""

import io
import os

import pytest
from PIL import Image

from server.tests.utils import build_test_app

_CACHE_CONTROL = "public, max-age=31536000, immutable"


def _noise_png(width: int, height: int) -> bytes:
    """随机噪声 PNG——噪声不可压缩，保证原图明显大于缩略图（用于 size 断言）。"""
    im = Image.frombytes("RGB", (width, height), os.urandom(width * height * 3))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _patch_store(monkeypatch, *, object_bytes: bytes | None = None) -> None:
    for fn in ("ensure_bucket", "remove_bucket", "empty_bucket", "upload_image"):
        monkeypatch.setattr(
            f"server.app.modules.image_library.router.minio_store.{fn}",
            lambda *a, **k: None,
        )
    if object_bytes is not None:
        monkeypatch.setattr(
            "server.app.modules.image_library.router.minio_store.get_object_bytes",
            lambda *a, **k: object_bytes,
        )


def _create_image(app, monkeypatch, orig_bytes: bytes) -> dict:
    """建栏目 + 传一张图建行；serve 路径的 get_object_bytes 打成返回 orig_bytes。"""
    _patch_store(monkeypatch)
    client = app.client
    cat = client.post(
        "/api/image-library/categories",
        json={"name": "截图桶", "bucket_name": "shots-bucket", "kind": "companion"},
    )
    assert cat.status_code == 201, cat.text
    up = client.post(
        f"/api/image-library/images?category_id={cat.json()['id']}",
        files={"file": ("shot.png", orig_bytes, "image/png")},
    )
    assert up.status_code == 200, up.text
    monkeypatch.setattr(
        "server.app.modules.image_library.router.minio_store.get_object_bytes",
        lambda *a, **k: orig_bytes,
    )
    return up.json()


@pytest.mark.mysql
def test_full_image_has_cache_headers(monkeypatch):
    app = build_test_app(monkeypatch)
    try:
        img = _create_image(app, monkeypatch, _noise_png(800, 600))
        r = app.client.get(img["url"])
        assert r.status_code == 200, r.text
        assert r.headers["Cache-Control"] == _CACHE_CONTROL
        assert r.headers.get("ETag"), "原图响应必须带 ETag"
        assert r.headers["Content-Type"] == "image/png"
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_conditional_request_returns_304(monkeypatch):
    app = build_test_app(monkeypatch)
    try:
        img = _create_image(app, monkeypatch, _noise_png(800, 600))
        first = app.client.get(img["url"])
        etag = first.headers["ETag"]
        second = app.client.get(img["url"], headers={"If-None-Match": etag})
        assert second.status_code == 304, second.text
        assert second.content == b""
        assert second.headers["ETag"] == etag
        assert second.headers["Cache-Control"] == _CACHE_CONTROL
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_thumbnail_returns_smaller_webp(monkeypatch):
    app = build_test_app(monkeypatch)
    try:
        orig = _noise_png(800, 600)
        img = _create_image(app, monkeypatch, orig)
        r = app.client.get(img["url"], params={"w": 320})
        assert r.status_code == 200, r.text
        assert r.headers["Content-Type"] == "image/webp"
        assert Image.open(io.BytesIO(r.content)).width == 320
        assert len(r.content) < len(orig), "缩略图应比原图小"
        assert r.headers["ETag"].endswith('.w320"'), "缩略图 ETag 需带宽度变体"
        assert r.headers["Cache-Control"] == _CACHE_CONTROL
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_invalid_width_returns_400(monkeypatch):
    app = build_test_app(monkeypatch)
    try:
        img = _create_image(app, monkeypatch, _noise_png(800, 600))
        r = app.client.get(img["url"], params={"w": 99999})
        assert r.status_code == 400, r.text
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_width_not_upscaled_serves_original(monkeypatch):
    app = build_test_app(monkeypatch)
    try:
        orig = _noise_png(300, 200)  # 原图比 640 窄
        img = _create_image(app, monkeypatch, orig)
        r = app.client.get(img["url"], params={"w": 640})
        assert r.status_code == 200, r.text
        assert r.headers["Content-Type"] == "image/png"  # 未转 webp
        assert r.content == orig  # 原样返回、不放大
        assert ".w640" not in r.headers["ETag"], "回退原图时 ETag 不带宽度变体"
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_corrupt_image_falls_back_to_original(monkeypatch):
    app = build_test_app(monkeypatch)
    try:
        img = _create_image(app, monkeypatch, _noise_png(800, 600))
        garbage = b"this is definitely not a decodable image"
        monkeypatch.setattr(
            "server.app.modules.image_library.router.minio_store.get_object_bytes",
            lambda *a, **k: garbage,
        )
        r = app.client.get(img["url"], params={"w": 640})
        assert r.status_code == 200, r.text  # 不 500
        assert r.headers["Content-Type"] != "image/webp"  # 降级回原字节
        assert r.content == garbage
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_full_and_thumbnail_etags_differ(monkeypatch):
    app = build_test_app(monkeypatch)
    try:
        img = _create_image(app, monkeypatch, _noise_png(800, 600))
        full = app.client.get(img["url"]).headers["ETag"]
        thumb = app.client.get(img["url"], params={"w": 640}).headers["ETag"]
        assert full != thumb, "全图与缩略图 ETag 必须不同，避免尺寸串味"
    finally:
        app.cleanup()
