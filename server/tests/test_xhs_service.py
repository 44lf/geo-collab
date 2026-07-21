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
