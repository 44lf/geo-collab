"""video 路由：MCP token 的 compose/status + 公开的文件服务。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from server.app.core.mcp_auth import require_mcp_token
from server.app.core.mcp_errors import mcp_exception_response
from server.app.db.session import get_db
from server.app.modules.video import store as video_store
from server.app.modules.video.models import VideoJob
from server.app.modules.video.schemas import ComposeVideoRequest
from server.app.modules.video.service import create_video_job, spawn_video_job
from server.app.shared.errors import ClientError, ConflictError, ValidationError

logger = logging.getLogger(__name__)

video_mcp_router = APIRouter(dependencies=[Depends(require_mcp_token)])
video_files_router = APIRouter()  # 公开：产物供人工上传时下载


def _to_status(job: VideoJob) -> dict:
    return {
        "job_id": job.job_id,
        "article_id": job.article_id,
        "status": job.status,
        "progress": job.progress,
        "video_url": f"/api/videos/file/{job.job_id}" if job.video_key else None,
        "srt_url": f"/api/videos/srt/{job.job_id}" if job.srt_key else None,
        "title": job.title,
        "description": job.description,
        "tags": job.tags or [],
        "error": job.error,
    }


@video_mcp_router.post("/compose", status_code=202)
def compose(req: ComposeVideoRequest, db: Session = Depends(get_db)) -> dict:
    """[MCP] 建视频任务 + 起后台渲染线程。202 立即返回 job_id。"""
    try:
        job = create_video_job(db, req)
    except (ValidationError, ClientError, ConflictError):
        raise
    except HTTPException:
        raise
    except Exception as exc:
        raise mcp_exception_response(
            exc, context=f"create_video_job article_id={req.article_id}"
        ) from exc
    spawn_video_job(job.job_id)
    return {"ok": True, "data": _to_status(job), "error": None}


@video_mcp_router.get("/status/{job_id}")
def status(job_id: str, db: Session = Depends(get_db)) -> dict:
    """[MCP] 查视频任务状态 + 产物 URL。"""
    job = db.query(VideoJob).filter(VideoJob.job_id == job_id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="视频任务不存在")
    return {"ok": True, "data": _to_status(job), "error": None}


def _serve(job_id: str, db: Session, kind: str) -> Response:
    job = db.query(VideoJob).filter(VideoJob.job_id == job_id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="视频任务不存在")
    key = job.video_key if kind == "video" else job.srt_key
    if not key:
        raise HTTPException(status_code=404, detail="产物尚未生成")
    try:
        data = video_store.get_object(key)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"MinIO 读取失败: {exc}") from exc
    media = "video/mp4" if kind == "video" else "application/x-subrip"
    return Response(content=data, media_type=media)


@video_files_router.get("/file/{job_id}")
def serve_video(job_id: str, db: Session = Depends(get_db)) -> Response:
    return _serve(job_id, db, "video")


@video_files_router.get("/srt/{job_id}")
def serve_srt(job_id: str, db: Session = Depends(get_db)) -> Response:
    return _serve(job_id, db, "srt")
