"""视频库列表端点测试：GET /api/videos（user JWT，只回 done/failed）。"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from server.app.core.time import utcnow
from server.app.modules.video.models import VideoJob
from server.tests.utils import build_test_app


def _seed_article(db, admin_id: int, title: str = "文章标题") -> int:
    from server.app.modules.articles.models import Article

    article = Article(
        user_id=admin_id,
        title=title,
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
    return article.id


def _seed_job(
    db,
    *,
    article_id: int,
    status: str,
    job_id: str,
    video_key: str | None = None,
    srt_key: str | None = None,
    title: str | None = None,
    error: str | None = None,
    tags: list[str] | None = None,
    created_at=None,
) -> VideoJob:
    job = VideoJob(
        job_id=job_id,
        article_id=article_id,
        status=status,
        progress=1.0 if status == "done" else 0.0,
        storyboard={"title": title or "t", "shots": [{"subtitle": "a", "narration": "a"}]},
        title=title,
        video_key=video_key,
        srt_key=srt_key,
        error=error,
        tags=tags,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    if created_at is not None:
        job.created_at = created_at
        db.commit()
        db.refresh(job)
    return job


@pytest.mark.mysql
def test_default_lists_done_and_failed_excludes_pending_running(monkeypatch):
    test_app = build_test_app(monkeypatch)
    try:
        db = test_app.session_factory()
        try:
            aid = _seed_article(db, test_app.admin_id)
            _seed_job(db, article_id=aid, status="done", job_id="j-done", video_key="j-done.mp4")
            _seed_job(db, article_id=aid, status="failed", job_id="j-fail", error="boom")
            _seed_job(db, article_id=aid, status="pending", job_id="j-pend")
            _seed_job(db, article_id=aid, status="running", job_id="j-run")
        finally:
            db.close()

        resp = test_app.client.get("/api/videos")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        got = {i["job_id"] for i in body["items"]}
        assert got == {"j-done", "j-fail"}
        assert body["total"] == 2
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_ordered_by_created_at_desc(monkeypatch):
    test_app = build_test_app(monkeypatch)
    try:
        db = test_app.session_factory()
        try:
            aid = _seed_article(db, test_app.admin_id)
            now = utcnow()
            _seed_job(
                db, article_id=aid, status="done", job_id="old", created_at=now - timedelta(hours=2)
            )
            _seed_job(db, article_id=aid, status="done", job_id="new", created_at=now)
        finally:
            db.close()

        body = test_app.client.get("/api/videos").json()
        assert [i["job_id"] for i in body["items"]] == ["new", "old"]
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_status_filter(monkeypatch):
    test_app = build_test_app(monkeypatch)
    try:
        db = test_app.session_factory()
        try:
            aid = _seed_article(db, test_app.admin_id)
            _seed_job(db, article_id=aid, status="done", job_id="d1", video_key="d1.mp4")
            _seed_job(db, article_id=aid, status="failed", job_id="f1", error="x")
        finally:
            db.close()

        done = test_app.client.get("/api/videos?status=done").json()
        assert {i["job_id"] for i in done["items"]} == {"d1"}
        assert done["total"] == 1

        failed = test_app.client.get("/api/videos?status=failed").json()
        assert {i["job_id"] for i in failed["items"]} == {"f1"}
        assert failed["total"] == 1
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_urls_present_when_keys_set_null_when_absent(monkeypatch):
    test_app = build_test_app(monkeypatch)
    try:
        db = test_app.session_factory()
        try:
            aid = _seed_article(db, test_app.admin_id)
            _seed_job(
                db,
                article_id=aid,
                status="done",
                job_id="withkeys",
                video_key="withkeys.mp4",
                srt_key="withkeys.srt",
            )
            _seed_job(db, article_id=aid, status="done", job_id="nokeys")
        finally:
            db.close()

        items = {i["job_id"]: i for i in test_app.client.get("/api/videos").json()["items"]}
        assert items["withkeys"]["video_url"] == "/api/videos/file/withkeys"
        assert items["withkeys"]["srt_url"] == "/api/videos/srt/withkeys"
        assert items["nokeys"]["video_url"] is None
        assert items["nokeys"]["srt_url"] is None
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_article_title_joined(monkeypatch):
    test_app = build_test_app(monkeypatch)
    try:
        db = test_app.session_factory()
        try:
            aid = _seed_article(db, test_app.admin_id, title="独特标题ABC")
            _seed_job(db, article_id=aid, status="done", job_id="j1", video_key="j1.mp4")
        finally:
            db.close()

        item = test_app.client.get("/api/videos").json()["items"][0]
        assert item["article_title"] == "独特标题ABC"
        assert item["article_id"] == aid
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_pagination_skip_limit_total(monkeypatch):
    test_app = build_test_app(monkeypatch)
    try:
        db = test_app.session_factory()
        try:
            aid = _seed_article(db, test_app.admin_id)
            now = utcnow()
            for n in range(3):
                _seed_job(
                    db,
                    article_id=aid,
                    status="done",
                    job_id=f"j{n}",
                    video_key=f"j{n}.mp4",
                    created_at=now - timedelta(minutes=n),
                )
        finally:
            db.close()

        page1 = test_app.client.get("/api/videos?skip=0&limit=2").json()
        assert len(page1["items"]) == 2
        assert page1["total"] == 3

        page2 = test_app.client.get("/api/videos?skip=2&limit=2").json()
        assert len(page2["items"]) == 1
        assert page2["total"] == 3
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_requires_auth(monkeypatch):
    test_app = build_test_app(monkeypatch)
    try:
        test_app.client.cookies.clear()
        resp = test_app.client.get("/api/videos")
        assert resp.status_code == 401, resp.text
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_invalid_status_422(monkeypatch):
    test_app = build_test_app(monkeypatch)
    try:
        resp = test_app.client.get("/api/videos?status=foo")
        assert resp.status_code == 422, resp.text
    finally:
        test_app.cleanup()
