"""小红书卡片渲染 service：render-markdown → Playwright → MinIO → XhsRenderJob。

后台线程执行（bg_session_factory），全程确定性、不调 LLM。

`render_markdown` 是大文本、不落 job 表：`create_render_job` 把它暂存进进程内 dict
（`_PENDING_MD`，key=job_id），`run_render_job` 取用后立即 pop 掉。这与 pipelines /
generation 一致——没有独立 worker，`bg_session_factory` 就是同进程的 `SessionLocal`，
`create_render_job` 与 `run_render_job` 总在同一个进程内配对执行。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from server.app.modules.xhs_cards import render, store
from server.app.modules.xhs_cards.models import XhsRenderJob
from server.app.modules.xhs_cards.schemas import ComposeXhsRequest
from server.app.shared.errors import ClientError

logger = logging.getLogger(__name__)

# 由 create_app() 注入（与 video.service / scheme_router / pipelines.router 同款）
bg_session_factory: Callable[[], Any] | None = None

# 进程内暂存：job_id -> render_markdown / (width, dpr)。见模块 docstring。
_PENDING_MD: dict[str, str] = {}
_RENDER_PARAMS: dict[str, tuple[int, int]] = {}


def create_render_job(db: Session, req: ComposeXhsRequest) -> XhsRenderJob:
    """建 pending 行（不渲染），并把大文本 markdown 暂存进程内 dict。"""
    job = XhsRenderJob(
        job_id=uuid.uuid4().hex,
        source_article_id=req.source_article_id,
        status="pending",
        theme=req.theme,
        mode=req.mode,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    _PENDING_MD[job.job_id] = req.render_markdown
    _RENDER_PARAMS[job.job_id] = (req.width, req.dpr)
    return job


def run_render_job(job_id: str, session_factory) -> None:
    """后台线程入口：自建 session 跑整条 pipeline。异常兜底写 failed。"""
    db = session_factory()
    md = _PENDING_MD.pop(job_id, "")
    width, dpr = _RENDER_PARAMS.pop(job_id, (1080, 2))
    try:
        job = db.query(XhsRenderJob).filter(XhsRenderJob.job_id == job_id).one()
        job.status = "running"
        db.commit()

        store.ensure_bucket()
        result = asyncio.run(
            render.render_markdown_to_card_bytes(
                md, theme=job.theme, mode=job.mode, width=width, dpr=dpr
            )
        )

        cover_key = f"{job.job_id}/cover.png"
        store.put_png(cover_key, result["cover"])
        card_keys: list[str] = []
        for i, png in enumerate(result["cards"], start=1):
            k = f"{job.job_id}/card_{i}.png"
            store.put_png(k, png)
            card_keys.append(k)

        job.cover_key = cover_key
        job.card_keys = card_keys
        job.status = "done"
        db.commit()
    except Exception as exc:  # noqa: BLE001 — 后台线程兜底，任何失败落 failed
        logger.exception("xhs 渲染失败: job_id=%s", job_id)
        db.rollback()
        try:
            job = db.query(XhsRenderJob).filter(XhsRenderJob.job_id == job_id).one()
            job.status = "failed"
            job.error = f"{type(exc).__name__}: {str(exc)[:500]}"
            db.commit()
        except Exception:
            logger.exception("写 failed 状态也失败: job_id=%s", job_id)
    finally:
        db.close()


def spawn_render_job(job_id: str) -> None:
    if bg_session_factory is None:
        raise ClientError("bg_session_factory 未注入（create_app 未执行？）")
    factory = bg_session_factory
    threading.Thread(target=run_render_job, args=(job_id, factory), daemon=True).start()


def list_render_jobs(
    db: Session,
    *,
    status: str | None = None,
    skip: int = 0,
    limit: int = 24,
) -> tuple[list[XhsRenderJob], int]:
    q = db.query(XhsRenderJob)
    if status:
        q = q.filter(XhsRenderJob.status == status)
    total = q.count()
    rows = q.order_by(XhsRenderJob.created_at.desc()).offset(skip).limit(limit).all()
    return rows, total
