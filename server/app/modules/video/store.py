"""视频产物 MinIO 存储：复用 image_library.store 的底层 client，独立 bucket。"""

from __future__ import annotations

from server.app.modules.image_library import store as minio_store

VIDEO_BUCKET = "geo-videos"


def ensure_video_bucket() -> None:
    minio_store.ensure_bucket(VIDEO_BUCKET)


def put_video(key: str, data: bytes) -> None:
    minio_store.upload_image(VIDEO_BUCKET, key, data, "video/mp4")


def put_srt(key: str, data: bytes) -> None:
    minio_store.upload_image(VIDEO_BUCKET, key, data, "application/x-subrip")


def get_object(key: str) -> bytes:
    return minio_store.get_object_bytes(VIDEO_BUCKET, key)
