"""小红书卡片 MinIO 存储：复用 image_library.store 底层 client，独立 bucket。"""

from __future__ import annotations

from server.app.modules.image_library import store as minio_store

XHS_BUCKET = "geo-xhs-cards"


def ensure_bucket() -> None:
    minio_store.ensure_bucket(XHS_BUCKET)


def put_png(key: str, data: bytes) -> None:
    minio_store.upload_image(XHS_BUCKET, key, data, "image/png")


def get_object(key: str) -> bytes:
    return minio_store.get_object_bytes(XHS_BUCKET, key)
