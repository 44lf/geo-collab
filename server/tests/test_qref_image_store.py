"""image_store: sha256 去重、内链形状、孤儿查询（无 active reference 关联的图片）。"""

import pytest

pytestmark = pytest.mark.mysql


def test_ingest_dedups_by_sha256(monkeypatch):
    from server.app.modules.quality_reference import image_store
    from server.app.modules.quality_reference.models import QualityReferenceImage
    from server.tests.utils import build_test_app

    uploads: list[tuple] = []
    monkeypatch.setattr(image_store.image_store_lib, "ensure_bucket", lambda b: None)
    monkeypatch.setattr(
        image_store.image_store_lib,
        "upload_image",
        lambda bucket, key, data, content_type: uploads.append((bucket, key)),
    )

    test_app = build_test_app(monkeypatch)
    try:
        db = test_app.session_factory()
        try:
            png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
            first = image_store.ingest_image(db, png, "image/png")
            db.commit()
            second = image_store.ingest_image(db, png, "image/png")
            db.commit()
            assert first.id == second.id  # 同图第二次复用同行
            assert db.query(QualityReferenceImage).count() == 1
            assert len(uploads) == 1  # 只上传一次
            assert first.minio_key.endswith(".png")
        finally:
            db.close()
    finally:
        test_app.cleanup()


def test_internal_url_shape():
    from server.app.modules.quality_reference import image_store

    assert image_store.internal_url(7) == "/api/quality-reference/images/7"


def test_find_orphan_reference_images(monkeypatch):
    from server.app.modules.quality_reference import image_store
    from server.app.modules.quality_reference.models import (
        QualityReference,
        QualityReferenceImage,
        QualityReferenceImageLink,
    )
    from server.tests.utils import build_test_app

    test_app = build_test_app(monkeypatch)
    try:
        db = test_app.session_factory()
        try:
            orphan_img = QualityReferenceImage(
                sha256="a" * 64,
                minio_key=f"{'a' * 64}.jpg",
                bucket=image_store.QREF_BUCKET,
                mime_type="image/jpeg",
                size=1,
            )
            linked_img = QualityReferenceImage(
                sha256="b" * 64,
                minio_key=f"{'b' * 64}.jpg",
                bucket=image_store.QREF_BUCKET,
                mime_type="image/jpeg",
                size=1,
            )
            db.add_all([orphan_img, linked_img])
            db.flush()

            reference = QualityReference(
                origin="external",
                title="t",
                content_json="{}",
                content_html="<p>t</p>",
                plain_text="t",
                content_hash="c" * 64,
                is_active=True,
            )
            db.add(reference)
            db.flush()
            db.add(QualityReferenceImageLink(reference_id=reference.id, image_id=linked_img.id))
            db.commit()

            orphans = image_store.find_orphan_reference_images(db)
            assert orphan_img.id in orphans
            assert linked_img.id not in orphans

            # 参考被停用后（无 active 关联）→ 关联图片重新变孤儿
            reference.is_active = False
            db.commit()
            orphans_after_deactivate = image_store.find_orphan_reference_images(db)
            assert linked_img.id in orphans_after_deactivate
        finally:
            db.close()
    finally:
        test_app.cleanup()
