from datetime import timedelta

import pytest


@pytest.mark.mysql
def test_pick_image_prefers_least_recently_used(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        from server.app.core.time import utcnow
        from server.app.modules.image_library.models import StockCategory, StockImage
        from server.app.modules.image_library.selector import ImageQuery, pick_image_id

        s = app.session_factory()
        try:
            cat = StockCategory(name="牧场物语", bucket_name="mcwl", kind="companion")
            s.add(cat)
            s.flush()
            old = StockImage(
                category_id=cat.id,
                minio_key="k1",
                filename="a",
                use_count=5,
                last_used_at=utcnow() - timedelta(days=10),
            )
            fresh = StockImage(
                category_id=cat.id,
                minio_key="k2",
                filename="b",
                use_count=0,
                last_used_at=None,
            )
            s.add_all([old, fresh])
            s.commit()
            picked = pick_image_id(ImageQuery(category_ids=[cat.id]), s)
            assert picked == fresh.id
        finally:
            s.close()
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_bump_stock_image_usage(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.articles.models import Article
        from server.app.modules.image_library.models import StockCategory, StockImage
        from server.app.modules.image_library.service import bump_stock_image_usage

        s = app.session_factory()
        try:
            article = Article(
                user_id=app.admin_id,
                title="image usage marker",
                content_json="{}",
                content_html="",
                plain_text="",
                client_request_id="image-usage-test",
            )
            s.add(article)
            cat = StockCategory(name="c", bucket_name="c", kind="companion")
            s.add(cat)
            s.flush()
            img = StockImage(category_id=cat.id, minio_key="k", filename="f", use_count=0)
            s.add(img)
            s.flush()
            bump_stock_image_usage(s, [img.id], article_id=article.id)
            s.commit()
            got = s.get(StockImage, img.id)
            assert got.use_count == 1 and got.last_used_article_id == article.id
        finally:
            s.close()
    finally:
        app.cleanup()
