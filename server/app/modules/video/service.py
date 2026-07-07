"""视频生成 service：storyboard → TTS → 取图 → ffmpeg → MinIO → VideoJob。

后台线程执行（bg_session_factory），全程确定性、不调 LLM。
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
import uuid

from sqlalchemy.orm import Session

from server.app.modules.articles.models import Article
from server.app.modules.image_library import store as image_store
from server.app.modules.image_library.models import StockCategory, StockImage
from server.app.modules.video import ffmpeg_compose as fc
from server.app.modules.video import store as video_store
from server.app.modules.video.binaries import cjk_font_path
from server.app.modules.video.engines import get_engine
from server.app.modules.video.models import VideoJob
from server.app.modules.video.schemas import ComposeVideoRequest, Storyboard, validate_asset_ids
from server.app.modules.video.srt import build_srt
from server.app.shared.errors import ClientError, ValidationError

logger = logging.getLogger(__name__)

# 由 create_app() 注入（与 scheme_router / pipelines.router 同款）
bg_session_factory = None

_DIMENSIONS = {"9:16": (1080, 1920), "16:9": (1920, 1080)}


def _read_file(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def create_video_job(db: Session, req: ComposeVideoRequest) -> VideoJob:
    """校验 + 建 pending 行（不渲染）。校验失败抛命名异常。"""
    article = db.get(Article, req.article_id)
    if article is None:
        raise ValidationError(f"文章不存在: {req.article_id}")
    validate_asset_ids(db, req.storyboard)
    job = VideoJob(
        job_id=uuid.uuid4().hex,
        article_id=req.article_id,
        storyboard=req.storyboard.model_dump(),
        engine=req.engine,
        status="pending",
        progress=0.0,
        title=req.storyboard.title,
        description=req.storyboard.description or None,
        tags=req.storyboard.tags or None,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _load_image_bytes(db: Session, asset_id: int | None) -> bytes:
    """按 asset_id 从图库取图字节；asset_id 为空则抛 ClientError（MVP 要求显式指定图片，暂无封面兜底）。"""
    img: StockImage | None = None
    if asset_id is not None:
        img = db.get(StockImage, asset_id)
    if img is None:
        raise ClientError("镜头缺少可用图片（asset_id 不可为空）")
    cat = db.get(StockCategory, img.category_id)
    if cat is None:
        raise ClientError("图片所属栏目不存在")
    return image_store.get_object_bytes(cat.bucket_name, img.minio_key)


def run_video_job(job_id: str, session_factory) -> None:
    """后台线程入口：自建 session 跑整条 pipeline。异常兜底写 failed。"""
    db = session_factory()
    try:
        job = db.query(VideoJob).filter(VideoJob.job_id == job_id).one()
        job.status = "running"
        db.commit()

        storyboard = Storyboard.model_validate(job.storyboard)
        width, height = _DIMENSIONS[storyboard.aspect_ratio]
        engine = get_engine(job.engine)
        video_store.ensure_video_bucket()

        with tempfile.TemporaryDirectory() as tmp:
            cues: list[tuple[str, float]] = []
            shot_paths: list[str] = []
            for i, shot in enumerate(storyboard.shots):
                # 1) 配音
                audio_bytes = engine.synthesize(shot.narration)
                audio_path = os.path.join(tmp, f"a{i}.mp3")
                with open(audio_path, "wb") as fh:
                    fh.write(audio_bytes)
                duration = fc.probe_duration(audio_path)
                if shot.duration_hint:
                    duration = max(duration, shot.duration_hint)
                if duration <= 0:
                    duration = 3.0
                # 2) 取图
                img_bytes = _load_image_bytes(db, shot.asset_id)
                img_path = os.path.join(tmp, f"img{i}.jpg")
                with open(img_path, "wb") as fh:
                    fh.write(img_bytes)
                # 3) 单镜头渲染
                out_path = os.path.join(tmp, f"shot{i}.mp4")
                cmd = fc.build_shot_command(
                    image_path=img_path,
                    audio_path=audio_path,
                    out_path=out_path,
                    duration=duration,
                    subtitle=shot.subtitle,
                    width=width,
                    height=height,
                    font_path=cjk_font_path(),
                )
                fc.run(cmd)
                shot_paths.append(out_path)
                cues.append((shot.subtitle, duration))
                job.progress = round((i + 1) / (len(storyboard.shots) + 1), 3)
                db.commit()

            # 4) 拼接
            list_file = os.path.join(tmp, "list.txt")
            with open(list_file, "w", encoding="utf-8") as fh:
                for p in shot_paths:
                    fh.write(f"file '{p}'\n")
            final_path = os.path.join(tmp, "final.mp4")
            fc.run(fc.build_concat_command(list_file=list_file, out_path=final_path))

            # 5) 落库 MinIO
            video_key = f"{job.job_id}.mp4"
            srt_key = f"{job.job_id}.srt"
            video_store.put_video(video_key, _read_file(final_path))
            video_store.put_srt(srt_key, build_srt(cues).encode("utf-8"))

        job.video_key = video_key
        job.srt_key = srt_key
        job.progress = 1.0
        job.status = "done"
        db.commit()
    except Exception as exc:  # noqa: BLE001 — 后台线程兜底，任何失败落 failed
        logger.exception("视频生成失败: job_id=%s", job_id)
        db.rollback()
        try:
            job = db.query(VideoJob).filter(VideoJob.job_id == job_id).one()
            job.status = "failed"
            job.error = f"{type(exc).__name__}: {str(exc)[:500]}"
            db.commit()
        except Exception:
            logger.exception("写 failed 状态也失败: job_id=%s", job_id)
    finally:
        db.close()


def spawn_video_job(job_id: str) -> None:
    if bg_session_factory is None:
        raise ClientError("bg_session_factory 未注入（create_app 未执行？）")
    factory = bg_session_factory
    threading.Thread(target=run_video_job, args=(job_id, factory), daemon=True).start()
