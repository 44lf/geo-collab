"""qref 外部参考异步导入 MCP 端点：POST /import-external（建 job）+ GET /import-jobs/{id}（轮询）。

走 MCP token 鉴权（与 user JWT 隔离），供 Claude Code 侧 import_external_reference 工具调用。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from server.app.core.mcp_auth import require_mcp_token
from server.app.core.mcp_errors import mcp_exception_response
from server.app.db.session import get_db
from server.app.modules.quality_reference import import_job as ij
from server.app.modules.quality_reference.schemas import (
    ImportExternalReferenceRequest,
    ImportJobStatus,
)
from server.app.shared.errors import ClientError, ValidationError

quality_reference_import_router = APIRouter(dependencies=[Depends(require_mcp_token)])


@quality_reference_import_router.post("/quality-reference/import-external", status_code=202)
def import_external_reference(
    req: ImportExternalReferenceRequest, db: Session = Depends(get_db)
) -> dict:
    """[MCP] 建异步导入 job + 起后台线程。202 立即返回 job_id（规避 MCP 30s 超时）。"""
    try:
        job = ij.create_import_job(db, req)
    except (ValidationError, ClientError):
        raise
    except HTTPException:
        raise
    except Exception as exc:
        raise mcp_exception_response(exc, context="create_import_job") from exc
    ij.spawn_import_job(job.job_id)
    return {"ok": True, "data": ImportJobStatus.model_validate(job).model_dump(), "error": None}


@quality_reference_import_router.get("/quality-reference/import-jobs/{job_id}")
def get_external_reference_status(job_id: str, db: Session = Depends(get_db)) -> dict:
    """[MCP] 查导入 job 状态 + 统计。"""
    job = ij.get_import_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="导入任务不存在")
    return {"ok": True, "data": ImportJobStatus.model_validate(job).model_dump(), "error": None}
