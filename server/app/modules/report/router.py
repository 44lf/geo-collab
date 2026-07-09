"""打点上报事件路由。

GET /api/report-events（user JWT，所有登录用户可读，无 admin 限制）
  - source_module, source_type, source_id, event_type, level
  - start_at, end_at
  - cursor（id 游标，倒序）
  - limit（默认 100，上限 500）

按 id 倒序分页：response.next_cursor 是下一页应传入的 cursor 值。

POST /api/report-events/mcp（MCP token，供 Claude Code Loop 写入事件，见 report_mcp_router）
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from server.app.core.mcp_auth import require_mcp_token
from server.app.db.session import get_db
from server.app.modules.report.schemas import (
    ReportEventList,
    ReportEventMcpPayload,
    ReportEventMcpResponse,
    ReportEventRead,
)
from server.app.modules.report.service import list_events, record_event

router = APIRouter()


@router.get("", response_model=ReportEventList)
def read_report_events(
    source_module: str | None = Query(None),
    source_type: str | None = Query(None),
    source_id: int | None = Query(None),
    event_type: str | None = Query(None),
    level: str | None = Query(None),
    start_at: datetime | None = Query(None),
    end_at: datetime | None = Query(None),
    cursor: int | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> ReportEventList:
    """查询打点上报事件，各过滤参数可叠加，按 id 倒序游标分页。"""
    items, next_cursor = list_events(
        db,
        source_module=source_module,
        source_type=source_type,
        source_id=source_id,
        event_type=event_type,
        level=level,
        start_at=start_at,
        end_at=end_at,
        cursor=cursor,
        limit=limit,
    )
    return ReportEventList(
        items=[ReportEventRead.model_validate(r) for r in items],
        next_cursor=next_cursor,
    )


# === MCP-facing endpoint（不走 user JWT，走 MCP token）===

report_mcp_router = APIRouter()


@report_mcp_router.post(
    "/mcp",
    response_model=ReportEventMcpResponse,
    dependencies=[Depends(require_mcp_token)],
)
def create_report_event_mcp(
    payload: ReportEventMcpPayload,
    db: Session = Depends(get_db),
) -> ReportEventMcpResponse:
    """[MCP] 写入一条打点上报事件。"""
    event_id = record_event(
        db,
        source_module=payload.source_module,
        event_type=payload.event_type,
        message=payload.message,
        level=payload.level,
        source_type=payload.source_type,
        source_id=payload.source_id,
        payload=payload.payload,
    )
    if event_id is None:
        raise HTTPException(status_code=500, detail="failed to record report event")
    return ReportEventMcpResponse(id=event_id)
