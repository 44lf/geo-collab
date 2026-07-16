"""qref 外部参考导入端点：POST /import-external（建 job）+ GET /import-jobs/{id}（轮询）+
GET /images/{id}（公开图片代理）。

Import 两端走 MCP token 鉴权（与 user JWT 隔离）；图片代理公开无需鉴权。
"""

from __future__ import annotations

import pytest

from server.tests.utils import build_test_app

pytestmark = pytest.mark.mysql


def test_import_endpoint_requires_mcp_token(monkeypatch):
    app_ctx = build_test_app(monkeypatch)
    try:
        # 无 MCP token → 401
        resp = app_ctx.client.post(
            "/api/quality-reference/import-external",
            json={"title": "t", "markdown": "x", "source_url": "https://e/1"},
        )
        assert resp.status_code == 401
    finally:
        app_ctx.cleanup()


def test_import_endpoint_creates_job_and_polls(monkeypatch):
    from server.app.modules.quality_reference import import_job

    # 不真正跑 worker：spawn 置空，仅验证建 job + 轮询
    monkeypatch.setattr(import_job, "spawn_import_job", lambda job_id: None)
    monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
    app_ctx = build_test_app(monkeypatch)
    try:
        headers = {"X-MCP-Token": "secret"}
        resp = app_ctx.client.post(
            "/api/quality-reference/import-external",
            json={"title": "t", "markdown": "![a](http://h/1.png)", "source_url": "https://e/1"},
            headers=headers,
        )
        assert resp.status_code == 202
        job_id = resp.json()["data"]["job_id"]
        assert resp.json()["data"]["status"] == "pending"

        poll = app_ctx.client.get(f"/api/quality-reference/import-jobs/{job_id}", headers=headers)
        assert poll.status_code == 200
        assert poll.json()["data"]["job_id"] == job_id
    finally:
        app_ctx.cleanup()


def test_image_proxy_serves_bytes(monkeypatch):
    from server.app.modules.quality_reference import image_store
    from server.app.modules.quality_reference.models import QualityReferenceImage

    monkeypatch.setattr(
        image_store.image_store_lib,
        "get_object_bytes",
        lambda bucket, key: b"\x89PNG\r\n\x1a\nDATA",
    )
    app_ctx = build_test_app(monkeypatch)
    try:
        db = app_ctx.session_factory()
        try:
            img = QualityReferenceImage(
                sha256="b" * 64,
                minio_key=f"{'b' * 64}.png",
                bucket="geo-qref-images",
                mime_type="image/png",
                size=10,
            )
            db.add(img)
            db.commit()
            image_id = img.id
        finally:
            db.close()

        # 公开端点，无需 token
        resp = app_ctx.client.get(f"/api/quality-reference/images/{image_id}")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/png")
        assert resp.content == b"\x89PNG\r\n\x1a\nDATA"

        assert app_ctx.client.get("/api/quality-reference/images/999999").status_code == 404
    finally:
        app_ctx.cleanup()
