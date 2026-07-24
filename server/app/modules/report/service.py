"""打点上报事件写入与查询服务。

设计原则（对齐 audit/service.py）：
  - 写入失败不影响主流程：任何异常被捕获并吞下，仅记 logger.warning。
  - 不做敏感字段脱敏——这是系统内部埋点，不是用户提交数据。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from server.app.modules.report.models import ReportEvent

_logger = logging.getLogger(__name__)


def record_event(
    db: Session,
    *,
    source_module: str,
    event_type: str,
    message: str,
    level: str = "info",
    source_type: str | None = None,
    source_id: int | None = None,
    payload: dict[str, Any] | None = None,
) -> int | None:
    """写入一条埋点事件，返回新记录 id。任何异常都被吞下只记 warning，返回 None，不影响调用方主流程。"""
    try:
        entry = ReportEvent(
            source_module=source_module,
            source_type=source_type,
            source_id=source_id,
            event_type=event_type,
            level=level,
            message=message,
            payload_json=payload,
        )
        db.add(entry)
        db.commit()
        return entry.id
    except Exception:
        _logger.warning(
            "report event write failed: source_module=%s event_type=%s",
            source_module,
            event_type,
            exc_info=True,
        )
        try:
            db.rollback()
        except Exception:
            pass
        return None


def list_events(
    db: Session,
    *,
    source_module: str | None = None,
    source_type: str | None = None,
    source_id: int | None = None,
    event_type: str | None = None,
    level: str | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    cursor: int | None = None,
    limit: int = 100,
) -> tuple[list[ReportEvent], int | None]:
    """按 id 倒序游标分页查询埋点事件，返回 (本页记录, 下一页游标)。

    cursor 是上一页最后一条的 id，传入后只取 id 更小（更旧）的记录。
    下一页游标为 None 表示没有下一页。
    """
    q = db.query(ReportEvent)
    if source_module:
        q = q.filter(ReportEvent.source_module == source_module)
    if source_type:
        q = q.filter(ReportEvent.source_type == source_type)
    if source_id is not None:
        q = q.filter(ReportEvent.source_id == source_id)
    if event_type:
        q = q.filter(ReportEvent.event_type == event_type)
    if level:
        q = q.filter(ReportEvent.level == level)
    if start_at is not None:
        q = q.filter(ReportEvent.created_at >= start_at)
    if end_at is not None:
        q = q.filter(ReportEvent.created_at <= end_at)
    if cursor is not None:
        q = q.filter(ReportEvent.id < cursor)

    rows = q.order_by(ReportEvent.id.desc()).limit(limit + 1).all()
    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = items[-1].id if (has_more and items) else None
    return items, next_cursor
