"""quality_reference 三张新表（图片资源 / reference↔image 关联 / 导入 job）ORM round-trip。

build_test_app 用 ORM Base.metadata.create_all 建表（不跑迁移），只要 3 个 ORM 类挂在
server/app/modules/quality_reference/models.py 上（main.py 已 import 该模块触发注册），
这里就能拿到对应表。迁移脚本本身的正确性由 test_qref_external_ingestion_migration.py 覆盖。
"""

import pytest

pytestmark = pytest.mark.mysql


def test_models_round_trip(monkeypatch):
    from server.app.modules.quality_reference.models import (
        QualityReferenceImage,
        QualityReferenceImportJob,
    )
    from server.tests.utils import build_test_app

    test_app = build_test_app(monkeypatch)
    try:
        db = test_app.session_factory()
        try:
            img = QualityReferenceImage(
                sha256="a" * 64,
                minio_key=f"{'a' * 64}.jpg",
                bucket="geo-qref-images",
                mime_type="image/jpeg",
                size=123,
            )
            db.add(img)
            db.flush()
            job = QualityReferenceImportJob(
                job_id="job123",
                status="pending",
                title="t",
                markdown="![x](u)",
                source_url="https://e/x",
            )
            db.add(job)
            db.commit()
            assert db.query(QualityReferenceImage).filter_by(sha256="a" * 64).one().size == 123
            assert (
                db.query(QualityReferenceImportJob).filter_by(job_id="job123").one().status
                == "pending"
            )
        finally:
            db.close()
    finally:
        test_app.cleanup()
