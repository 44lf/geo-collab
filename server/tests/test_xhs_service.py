"""xhs_cards service / model 测试。"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.mysql


def test_render_job_row(monkeypatch):
    from server.tests.utils import build_test_app

    test_app = build_test_app(monkeypatch)
    try:
        from server.app.modules.xhs_cards.models import XhsRenderJob

        with test_app.session_factory() as db:
            job = XhsRenderJob(job_id="abc123", status="pending", theme="sketch", mode="separator")
            db.add(job)
            db.commit()
            db.refresh(job)
            assert job.id is not None
            got = db.query(XhsRenderJob).filter_by(job_id="abc123").one()
            assert got.status == "pending"
    finally:
        test_app.cleanup()


def test_run_render_job_success(monkeypatch):
    from server.tests.utils import build_test_app

    test_app = build_test_app(monkeypatch)
    try:
        from server.app.modules.xhs_cards import render, service, store
        from server.app.modules.xhs_cards.models import XhsRenderJob
        from server.app.modules.xhs_cards.schemas import ComposeXhsRequest

        async def fake_render(md, *, theme, mode, width=1080, height=1440, dpr=2):
            return {"cover": b"\x89PNGcover", "cards": [b"\x89PNGa", b"\x89PNGb"]}

        monkeypatch.setattr(render, "render_markdown_to_card_bytes", fake_render)
        monkeypatch.setattr(store, "ensure_bucket", lambda: None)
        monkeypatch.setattr(store, "put_png", lambda k, d: None)

        sf = test_app.session_factory
        with sf() as db:
            job = service.create_render_job(
                db, ComposeXhsRequest(render_markdown="---\ntitle: T\n---\nA\n---\nB")
            )
            jid = job.job_id

        service.run_render_job(jid, sf)

        with sf() as db:
            got = db.query(XhsRenderJob).filter_by(job_id=jid).one()
            assert got.status == "done"
            assert got.cover_key and len(got.card_keys) == 2
    finally:
        test_app.cleanup()


def test_run_render_job_failure(monkeypatch):
    from server.tests.utils import build_test_app

    test_app = build_test_app(monkeypatch)
    try:
        from server.app.modules.xhs_cards import render, service, store
        from server.app.modules.xhs_cards.models import XhsRenderJob
        from server.app.modules.xhs_cards.schemas import ComposeXhsRequest

        async def boom(*a, **k):
            raise RuntimeError("render boom")

        monkeypatch.setattr(store, "ensure_bucket", lambda: None)
        monkeypatch.setattr(render, "render_markdown_to_card_bytes", boom)

        sf = test_app.session_factory
        with sf() as db:
            job = service.create_render_job(db, ComposeXhsRequest(render_markdown="x"))
            jid = job.job_id

        service.run_render_job(jid, sf)

        with sf() as db:
            got = db.query(XhsRenderJob).filter_by(job_id=jid).one()
            assert got.status == "failed"
            assert "render boom" in got.error
    finally:
        test_app.cleanup()
