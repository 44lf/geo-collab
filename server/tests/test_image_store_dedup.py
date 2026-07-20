import pytest


@pytest.mark.mysql
def test_store_image_dedup_by_source_url_hash(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.image_library import service
        from server.app.modules.image_library import store as minio_store

        monkeypatch.setattr(minio_store, "ensure_bucket", lambda *a, **k: None)
        monkeypatch.setattr(minio_store, "upload_image", lambda *a, **k: None)

        s = app.session_factory()
        try:
            cat = service.get_or_create_companion_category(s, "餐厅养成记")
            a = service.store_image_bytes(
                s,
                cat,
                b"x",
                "image/jpeg",
                source_url="http://x/1.jpg",
                commit=False,
            )
            b = service.store_image_bytes(
                s,
                cat,
                b"x",
                "image/jpeg",
                source_url="http://x/1.jpg",
                commit=False,
            )
            s.commit()
            assert a is not None and b is not None and a.id == b.id
            assert a.source_url_hash and len(a.source_url_hash) == 64
        finally:
            s.close()
    finally:
        app.cleanup()
