"""xhs_cards 路由：MCP token 的 compose/status + 公开的图片服务。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from server.app.core.mcp_auth import require_mcp_token
from server.app.core.mcp_errors import mcp_exception_response
from server.app.core.security import get_current_user
from server.app.db.session import get_db
from server.app.modules.xhs_cards import previews, store
from server.app.modules.xhs_cards.models import XhsRenderJob
from server.app.modules.xhs_cards.schemas import ComposeXhsRequest
from server.app.modules.xhs_cards.service import create_render_job, spawn_render_job
from server.app.shared.errors import ClientError, ConflictError, ValidationError

logger = logging.getLogger(__name__)

xhs_mcp_router = APIRouter(dependencies=[Depends(require_mcp_token)])
xhs_files_router = APIRouter()  # 公开：卡片 PNG 供入库 markdown 引用


def _to_status(job: XhsRenderJob) -> dict:
    n = len(job.card_keys or [])
    return {
        "job_id": job.job_id,
        "source_article_id": job.source_article_id,
        "status": job.status,
        "theme": job.theme,
        "mode": job.mode,
        "cover_url": f"/api/xhs-cards/file/{job.job_id}/cover" if job.cover_key else None,
        "card_urls": [f"/api/xhs-cards/file/{job.job_id}/card/{i}" for i in range(1, n + 1)],
        "error": job.error,
    }


@xhs_mcp_router.post("/compose", status_code=202)
def compose(req: ComposeXhsRequest, db: Session = Depends(get_db)) -> dict:
    """[MCP] 建小红书卡片渲染任务 + 起后台渲染线程。202 立即返回 job_id。"""
    try:
        job = create_render_job(db, req)
    except (ValidationError, ClientError, ConflictError):
        raise
    except HTTPException:
        raise
    except Exception as exc:
        raise mcp_exception_response(exc, context="create_render_job") from exc
    spawn_render_job(job.job_id)
    return {"ok": True, "data": _to_status(job), "error": None}


@xhs_mcp_router.get("/status/{job_id}")
def status(job_id: str, db: Session = Depends(get_db)) -> dict:
    """[MCP] 查渲染任务状态 + 产物 URL。"""
    job = db.query(XhsRenderJob).filter(XhsRenderJob.job_id == job_id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="渲染任务不存在")
    return {"ok": True, "data": _to_status(job), "error": None}


def _serve(job_id: str, key: str | None) -> Response:
    if not key:
        raise HTTPException(status_code=404, detail="产物尚未生成")
    try:
        data = store.get_object(key)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"MinIO 读取失败: {exc}") from exc
    return Response(content=data, media_type="image/png")


@xhs_files_router.get("/file/{job_id}/cover")
def serve_cover(job_id: str, db: Session = Depends(get_db)) -> Response:
    job = db.query(XhsRenderJob).filter(XhsRenderJob.job_id == job_id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="渲染任务不存在")
    return _serve(job_id, job.cover_key)


@xhs_files_router.get("/file/{job_id}/card/{idx}")
def serve_card(job_id: str, idx: int, db: Session = Depends(get_db)) -> Response:
    job = db.query(XhsRenderJob).filter(XhsRenderJob.job_id == job_id).first()
    if job is None or not job.card_keys or idx < 1 or idx > len(job.card_keys):
        raise HTTPException(status_code=404, detail="卡片不存在")
    return _serve(job_id, job.card_keys[idx - 1])


xhs_gallery_router = APIRouter(dependencies=[Depends(get_current_user)])  # 样式库：任何登录用户


@xhs_gallery_router.get("/themes")
def list_themes() -> dict:
    return {"ok": True, "data": previews.preview_gallery_state(), "error": None}


@xhs_gallery_router.get("/themes/{name}/preview/{kind}")
def theme_preview(name: str, kind: str) -> Response:
    data = previews.get_preview_bytes(name, kind)
    if data is None:
        raise HTTPException(status_code=404, detail="预览未生成或主题不存在")
    return Response(content=data, media_type="image/png")


@xhs_gallery_router.post("/themes/regenerate", status_code=202)
def regenerate_themes() -> dict:
    started = previews.spawn_regenerate()
    return {
        "ok": True,
        "data": {"status": "generating" if started else "already_running"},
        "error": None,
    }
