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


def test_extract_image_urls():
    from server.app.modules.quality_reference import import_job

    md = 'a\n\n![x](http://h/1.png)\n\ntext ![y](https://h/2.jpg "t") end'
    assert import_job.extract_image_urls(md) == ["http://h/1.png", "https://h/2.jpg"]


def test_rewrite_image_urls():
    from server.app.modules.quality_reference import import_job

    md = "![x](http://h/1.png) and ![y](http://h/2.png)"
    out = import_job.rewrite_image_urls(md, {"http://h/1.png": "/api/quality-reference/images/1"})
    assert "/api/quality-reference/images/1" in out
    assert "http://h/2.png" in out  # 未映射的原样保留


def test_rewrite_image_urls_drops_unmapped():
    from server.app.modules.quality_reference import import_job

    md = "![x](http://h/1.png) and ![y](http://h/2.png)"
    out = import_job.rewrite_image_urls(
        md,
        {"http://h/1.png": "/api/quality-reference/images/1"},
        drop_unmapped=True,
    )
    assert "/api/quality-reference/images/1" in out
    assert "http://h/2.png" not in out
    assert "![y]" not in out  # 未映射的图片整段剔除，不留外链


def test_run_import_job_full_flow(monkeypatch):
    from server.app.modules.quality_reference import image_store, import_job
    from server.app.modules.quality_reference.models import (
        QualityReference,
        QualityReferenceImageLink,
        QualityReferenceImportJob,
    )
    from server.app.modules.quality_reference.schemas import ImportExternalReferenceRequest
    from server.tests.utils import build_test_app

    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40

    # mock 下载：1.png 成功、bad.png 抛 ImageFetchError（走 skipped）
    def fake_download(url, **kw):
        if "bad" in url:
            raise import_job.ImageFetchError("boom")
        return png, "image/png"

    monkeypatch.setattr(import_job, "download_image", fake_download)
    monkeypatch.setattr(image_store.image_store_lib, "ensure_bucket", lambda b: None)
    monkeypatch.setattr(
        image_store.image_store_lib,
        "upload_image",
        lambda *a, **k: None,
    )

    test_app = build_test_app(monkeypatch)
    try:
        import_job.bg_session_factory = test_app.session_factory
        db = test_app.session_factory()
        try:
            req = ImportExternalReferenceRequest(
                title="站外真品",
                markdown="导语\n\n![ok](http://h/1.png)\n\n![x](http://h/bad.png)",
                source_url="https://ext.example/post/1",
                category="测评",
            )
            job = import_job.create_import_job(db, req)
            job_id = job.job_id
        finally:
            db.close()

        # 同步跑 worker（不 spawn 线程，直接调，便于断言）
        import_job.run_import_job(job_id, test_app.session_factory)

        db = test_app.session_factory()
        try:
            done = db.query(QualityReferenceImportJob).filter_by(job_id=job_id).one()
            assert done.status == "done"
            assert done.images_total == 2
            assert done.images_rehosted == 1
            assert done.images_skipped == 1
            assert done.reference_id is not None
            ref = db.get(QualityReference, done.reference_id)
            assert ref.origin == "external"
            assert ref.is_active is True
            assert ref.source_url == "https://ext.example/post/1"
            # 正文 content_json 含内链 image 节点、外链已改写
            assert "/api/quality-reference/images/" in ref.content_json
            assert "http://h/1.png" not in ref.content_json
            # 下载失败(skipped)的图片外链必须被整段剔除，不留外链(SSRF/破图)
            assert "http://h/bad.png" not in ref.content_json
            assert "http://h/bad.png" not in ref.plain_text
            # image_link 挂到该 ref（成功那张）
            links = db.query(QualityReferenceImageLink).filter_by(reference_id=ref.id).all()
            assert len(links) == 1
        finally:
            db.close()
    finally:
        test_app.cleanup()


def test_run_import_job_failed_path_releases_semaphore(monkeypatch):
    """Phase B（import_external）抛非 ImageFetchError → job 落 failed，且信号量必须被释放。

    锁定 run_import_job 的 acquire/release 配对：即使 worker 主体在 db.commit() 之前
    的中段异常退出，外层 finally 也必须放回许可（否则 BoundedSemaphore 永久收窄）。
    """
    import threading

    from server.app.modules.quality_reference import image_store, import_job
    from server.app.modules.quality_reference.models import QualityReferenceImportJob
    from server.app.modules.quality_reference.schemas import ImportExternalReferenceRequest
    from server.tests.utils import build_test_app

    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40

    def fake_download(url, **kw):
        return png, "image/png"

    def boom_import_external(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(import_job, "download_image", fake_download)
    monkeypatch.setattr(image_store.image_store_lib, "ensure_bucket", lambda b: None)
    monkeypatch.setattr(image_store.image_store_lib, "upload_image", lambda *a, **k: None)
    monkeypatch.setattr(import_job.service, "import_external", boom_import_external)
    # 收紧到 1 并发，release 断言更直观
    monkeypatch.setattr(import_job, "_IMPORT_SEMAPHORE", threading.BoundedSemaphore(1))

    test_app = build_test_app(monkeypatch)
    try:
        db = test_app.session_factory()
        try:
            req = ImportExternalReferenceRequest(
                title="站外真品",
                markdown="导语\n\n![ok](http://h/1.png)",
                source_url="https://ext.example/post/2",
                category="测评",
            )
            job = import_job.create_import_job(db, req)
            job_id = job.job_id
        finally:
            db.close()

        import_job.run_import_job(job_id, test_app.session_factory)

        db = test_app.session_factory()
        try:
            failed = db.query(QualityReferenceImportJob).filter_by(job_id=job_id).one()
            assert failed.status == "failed"
            assert failed.error
        finally:
            db.close()

        # 许可必须已释放：非阻塞 acquire 应能拿到，再放回去
        assert import_job._IMPORT_SEMAPHORE.acquire(blocking=False) is True
        import_job._IMPORT_SEMAPHORE.release()
    finally:
        test_app.cleanup()


def test_import_jobs_respect_concurrency_bound(monkeypatch):
    import threading
    import time

    from server.app.modules.quality_reference import image_store, import_job
    from server.app.modules.quality_reference.schemas import ImportExternalReferenceRequest
    from server.tests.utils import build_test_app

    # 收紧到 2 并发，便于断言（覆盖模块级信号量）
    monkeypatch.setattr(import_job, "_IMPORT_SEMAPHORE", threading.BoundedSemaphore(2))

    inside = 0
    peak = 0
    lock = threading.Lock()
    gate = threading.Event()
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40

    def blocking_download(url, **kw):
        nonlocal inside, peak
        with lock:
            inside += 1
            peak = max(peak, inside)
        gate.wait(timeout=5)  # 卡住制造重叠
        with lock:
            inside -= 1
        return png, "image/png"

    monkeypatch.setattr(import_job, "download_image", blocking_download)
    monkeypatch.setattr(image_store.image_store_lib, "ensure_bucket", lambda b: None)
    monkeypatch.setattr(image_store.image_store_lib, "upload_image", lambda *a, **k: None)

    test_app = build_test_app(monkeypatch)
    try:
        import_job.bg_session_factory = test_app.session_factory
        db = test_app.session_factory()
        job_ids = []
        try:
            for i in range(4):
                req = ImportExternalReferenceRequest(
                    title=f"t{i}",
                    markdown=f"![a](http://h/{i}.png)",
                    source_url=f"https://e/{i}",
                )
                job_ids.append(import_job.create_import_job(db, req).job_id)
        finally:
            db.close()

        threads = [
            threading.Thread(target=import_job.run_import_job, args=(jid, test_app.session_factory))
            for jid in job_ids
        ]
        for t in threads:
            t.start()
        time.sleep(0.5)  # 给线程抢闸并卡在 download
        assert peak <= 2, f"并发闸失效：峰值 {peak} > 2"
        gate.set()  # 放行跑完
        for t in threads:
            t.join(timeout=10)
    finally:
        test_app.cleanup()


def test_max_concurrent_clamped_to_at_least_one(monkeypatch):
    """GEO_QREF_IMPORT_MAX_CONCURRENT=0（或负数）不能把 BoundedSemaphore 建成 0——
    否则每个导入 job 永久卡在 acquire()。模块级常量在 import 时求值，用 importlib.reload
    在改过环境变量后重新触发求值。"""
    import importlib

    from server.app.modules.quality_reference import import_job

    monkeypatch.setenv("GEO_QREF_IMPORT_MAX_CONCURRENT", "0")
    try:
        reloaded = importlib.reload(import_job)
        assert reloaded._MAX_CONCURRENT >= 1
    finally:
        importlib.reload(import_job)  # 恢复真实环境（还原默认值），不污染后续测试


def test_create_import_job_rejects_non_http_source_url(monkeypatch):
    from server.app.modules.quality_reference import import_job
    from server.app.modules.quality_reference.schemas import ImportExternalReferenceRequest
    from server.app.shared.errors import ValidationError
    from server.tests.utils import build_test_app

    test_app = build_test_app(monkeypatch)
    try:
        db = test_app.session_factory()
        try:
            req = ImportExternalReferenceRequest(
                title="t",
                markdown="x",
                source_url="javascript:alert(1)",
            )
            with pytest.raises(ValidationError):
                import_job.create_import_job(db, req)
        finally:
            db.close()
    finally:
        test_app.cleanup()
