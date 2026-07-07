from __future__ import annotations

import json
import uuid

import pytest

from server.app.modules.articles.models import Article
from server.app.modules.video.models import VideoJob
from server.tests.utils import build_test_app


@pytest.mark.mysql
def test_video_job_insert_and_query(monkeypatch):
    test_app = build_test_app(monkeypatch)
    try:
        db = test_app.session_factory()
        try:
            article = Article(
                user_id=test_app.admin_id,
                title="test",
                content_json=json.dumps({"type": "doc", "content": []}),
                content_html="",
                plain_text="正文",
                word_count=0,
                status="draft",
                review_status="pending",
            )
            db.add(article)
            db.commit()
            db.refresh(article)

            jid = uuid.uuid4().hex
            job = VideoJob(
                job_id=jid,
                article_id=article.id,
                storyboard={"title": "t", "shots": []},
                status="pending",
                progress=0.0,
            )
            db.add(job)
            db.commit()
            db.refresh(job)
            assert job.id is not None
            assert job.status == "pending"
            fetched = db.query(VideoJob).filter(VideoJob.job_id == jid).one()
            assert fetched.storyboard == {"title": "t", "shots": []}
        finally:
            db.close()
    finally:
        test_app.cleanup()
