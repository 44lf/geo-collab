"""qref 图片的 MinIO 存取 + sha256 去重建行 + 站内内链 + 孤儿查询。

图片跨篇共享：同 sha256 只存一份、只建一行（UNIQUE(sha256) 兜底并发）。归属由
quality_reference_image_link 表表达；孤儿 = 无任何 active reference 关联的 image。
"""

from __future__ import annotations

import hashlib

from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from server.app.modules.articles.store import guess_image_size
from server.app.modules.image_library import store as image_store_lib
from server.app.modules.quality_reference.models import (
    QualityReference,
    QualityReferenceImage,
    QualityReferenceImageLink,
)
from server.app.shared.errors import ClientError

QREF_BUCKET = "geo-qref-images"

_MIME_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def ensure_qref_bucket() -> None:
    image_store_lib.ensure_bucket(QREF_BUCKET)


def internal_url(image_id: int) -> str:
    return f"/api/quality-reference/images/{image_id}"


def ingest_image(db: Session, data: bytes, mime: str) -> QualityReferenceImage:
    """算 sha256 → 命中复用、未命中传 MinIO 专桶 + 建行。不 commit（调用方控事务）。"""
    sha = hashlib.sha256(data).hexdigest()
    existing = db.query(QualityReferenceImage).filter_by(sha256=sha).first()
    if existing is not None:
        return existing

    ext = _MIME_EXT.get(mime, ".jpg")
    key = f"{sha}{ext}"
    ensure_qref_bucket()
    image_store_lib.upload_image(QREF_BUCKET, key, data, mime)
    width, height = guess_image_size(data)
    img = QualityReferenceImage(
        sha256=sha,
        minio_key=key,
        bucket=QREF_BUCKET,
        mime_type=mime,
        size=len(data),
        width=width,
        height=height,
    )
    try:
        db.add(img)
        db.flush()
        return img
    except IntegrityError:
        db.rollback()  # 并发已插同 sha256 → 回滚重查
        again = db.query(QualityReferenceImage).filter_by(sha256=sha).first()
        if again is not None:
            return again
        raise


def read_image_bytes(db: Session, image_id: int) -> tuple[bytes, str]:
    """代理端点用：按 id 读 MinIO 字节 + mime。不接受任意 key，避免越权读桶。"""
    img = db.get(QualityReferenceImage, image_id)
    if img is None:
        raise ClientError(f"quality_reference_image not found: {image_id}")
    data = image_store_lib.get_object_bytes(img.bucket, img.minio_key)
    return data, img.mime_type


def find_orphan_reference_images(db: Session) -> list[int]:
    """无任何 active reference 关联的 image id（供手动/周期孤儿清理；v1 不自动 GC）。"""
    stmt = select(QualityReferenceImage.id).where(
        ~exists(
            select(QualityReferenceImageLink.id)
            .join(QualityReference, QualityReference.id == QualityReferenceImageLink.reference_id)
            .where(
                QualityReferenceImageLink.image_id == QualityReferenceImage.id,
                QualityReference.is_active == True,  # noqa: E712
            )
        )
    )
    return list(db.execute(stmt).scalars().all())
