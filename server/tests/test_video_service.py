from __future__ import annotations

import json

import pytest

from server.app.modules.video import service as vsvc
from server.app.modules.video.models import VideoJob
from server.app.modules.video.schemas import ComposeVideoRequest, Shot, Storyboard
from server.tests.utils import build_test_app


def _seed_article_and_image(db) -> tuple[int, int]:
    """建一条最小 Article + 一个图库栏目 + 一张图，返回 (article_id, asset_id)。"""
    from server.app.modules.articles.models import Article
    from server.app.modules.image_library.models import StockCategory, StockImage
    from server.app.modules.system.models import User

    uid = db.query(User).first().id

    article = Article(
        user_id=uid,
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

    cat = StockCategory(name="测试栏目", bucket_name="test-bucket-xyz", kind="companion")
    db.add(cat)
    db.commit()
    db.refresh(cat)

    img = StockImage(
        category_id=cat.id,
        minio_key="k1.jpg",
        filename="k1.jpg",
        tags=["标签"],
        width=1280,
        height=720,
    )
    db.add(img)
    db.commit()
    db.refresh(img)

    return article.id, img.id


@pytest.mark.mysql
def test_run_video_job_success(monkeypatch):
    test_app = build_test_app(monkeypatch)
    SessionLocal = test_app.session_factory
    try:
        db = SessionLocal()
        article_id, asset_id = _seed_article_and_image(db)
        db.close()

        # mock 掉真实 TTS / ffmpeg / MinIO
        monkeypatch.setattr(
            "server.app.modules.video.service.get_engine",
            lambda code: type("E", (), {"code": "edge", "synthesize": lambda self, t: b"mp3"})(),
        )
        monkeypatch.setattr("server.app.modules.video.service.fc.probe_duration", lambda p: 3.0)
        monkeypatch.setattr("server.app.modules.video.service.fc.run", lambda cmd: None)
        # 取图走真实 MinIO —— mock 掉，避免对不存在的 bucket/对象发真网络请求（会挂）
        monkeypatch.setattr(
            "server.app.modules.video.service.image_store.get_object_bytes",
            lambda bucket, key: b"IMG",
        )
        # ffmpeg run 是 mock 的，产物文件不会真生成 → 读产物字节也 mock
        monkeypatch.setattr("server.app.modules.video.service._read_file", lambda p: b"FAKEMP4")
        stored: dict = {}
        monkeypatch.setattr(
            "server.app.modules.video.store.put_video",
            lambda key, data: stored.__setitem__("video", (key, data)),
        )
        monkeypatch.setattr(
            "server.app.modules.video.store.put_srt",
            lambda key, data: stored.__setitem__("srt", (key, data)),
        )
        monkeypatch.setattr("server.app.modules.video.store.ensure_video_bucket", lambda: None)

        req = ComposeVideoRequest(
            article_id=article_id,
            storyboard=Storyboard(
                title="标题",
                description="描述",
                tags=["标签"],
                shots=[Shot(subtitle="第一段", narration="第一段口播", asset_id=asset_id)],
            ),
        )
        db = SessionLocal()
        job = vsvc.create_video_job(db, req)
        jid = job.job_id
        db.close()

        vsvc.run_video_job(jid, SessionLocal)

        db = SessionLocal()
        done = db.query(VideoJob).filter(VideoJob.job_id == jid).one()
        assert done.status == "done"
        assert done.progress == 1.0
        assert done.video_key and done.srt_key
        assert done.title == "标题"
        db.close()
        assert "video" in stored and "srt" in stored
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_run_video_job_failure_marks_failed(monkeypatch):
    test_app = build_test_app(monkeypatch)
    SessionLocal = test_app.session_factory
    try:
        db = SessionLocal()
        article_id, asset_id = _seed_article_and_image(db)
        db.close()

        def _boom(code):
            raise RuntimeError("tts down")

        monkeypatch.setattr("server.app.modules.video.service.get_engine", _boom)
        monkeypatch.setattr("server.app.modules.video.store.ensure_video_bucket", lambda: None)

        req = ComposeVideoRequest(
            article_id=article_id,
            storyboard=Storyboard(
                title="t", shots=[Shot(subtitle="a", narration="a", asset_id=asset_id)]
            ),
        )
        db = SessionLocal()
        jid = vsvc.create_video_job(db, req).job_id
        db.close()

        vsvc.run_video_job(jid, SessionLocal)

        db = SessionLocal()
        failed = db.query(VideoJob).filter(VideoJob.job_id == jid).one()
        assert failed.status == "failed"
        assert failed.error
        db.close()
    finally:
        test_app.cleanup()
