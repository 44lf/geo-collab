"""Admin-only, read-only Collector management queries."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.app.core.security import require_admin
from server.app.core.time import utcnow
from server.app.db.session import get_db
from server.app.modules.collector.models import (
    CollectorEvent,
    CollectorItemReceipt,
    CollectorNode,
    CollectorRun,
    CollectorTransfer,
    CollectorTransferReceipt,
)
from server.app.modules.collector.telemetry import (
    node_freshness,
    sanitize_event_payload,
    sanitize_log_text,
)
from server.app.modules.system.models import User

collector_management_router = APIRouter()


def _safe_failure(value: str | None) -> str | None:
    if value is None:
        return None
    return sanitize_log_text(value)[:1000]


def list_node_views(
    db: Session,
    *,
    cursor: int | None,
    limit: int,
    stale_after_seconds: int,
) -> dict[str, Any]:
    statement = select(CollectorNode).order_by(CollectorNode.id.asc()).limit(limit + 1)
    if cursor is not None:
        statement = statement.where(CollectorNode.id > cursor)
    rows = list(db.scalars(statement).all())
    page = rows[:limit]
    now = utcnow()
    stale_after = timedelta(seconds=stale_after_seconds)
    return {
        "items": [
            {
                "id": row.id,
                "collector_id": row.collector_id,
                "display_name": row.display_name,
                "destination": row.destination,
                "status": row.status,
                "freshness": node_freshness(row, now=now, stale_after=stale_after),
                "platform": row.platform,
                "agent_version": row.agent_version,
                "enabled_sources": row.enabled_sources,
                "current_run_id": row.current_run_id,
                "current_stage": row.current_stage,
                "spool_pending_count": row.spool_pending_count,
                "last_heartbeat_at": row.last_heartbeat_at,
                "last_success_at": row.last_success_at,
                "last_error_at": row.last_error_at,
                "last_error_summary": _safe_failure(row.last_error_summary),
            }
            for row in page
        ],
        "next_cursor": page[-1].id if len(rows) > limit else None,
    }


def get_run_view(db: Session, *, run_id: str, event_limit: int) -> dict[str, Any]:
    run = db.scalar(select(CollectorRun).where(CollectorRun.run_id == run_id))
    if run is None:
        raise LookupError("collector run not found")
    statement = (
        select(CollectorEvent)
        .where(
            CollectorEvent.collector_id == run.collector_id,
            CollectorEvent.run_id == run.run_id,
        )
        .order_by(CollectorEvent.occurred_at.asc(), CollectorEvent.id.asc())
        .limit(event_limit)
    )
    events = list(db.scalars(statement).all())
    return {
        "run": {
            "collector_id": run.collector_id,
            "run_id": run.run_id,
            "job_id": run.job_id,
            "source": run.source,
            "status": run.status,
            "current_stage": run.current_stage,
            "summary": sanitize_event_payload(run.summary),
            "error_classification": run.error_classification,
            "error_summary": _safe_failure(run.error_summary),
            "started_at": run.started_at,
            "finished_at": run.finished_at,
        },
        "events": [
            {
                "event_id": event.event_id,
                "transport_id": event.transport_id,
                "bundle_id": event.bundle_id,
                "source": event.source,
                "component": event.component,
                "component_version": event.component_version,
                "stage": event.stage,
                "event_type": event.event_type,
                "level": event.level,
                "payload": sanitize_event_payload(event.payload),
                "occurred_at": event.occurred_at,
                "received_at": event.received_at,
            }
            for event in events
        ],
    }


def list_run_views(db: Session, *, limit: int) -> dict[str, Any]:
    rows = list(
        db.scalars(
            select(CollectorRun)
            .order_by(CollectorRun.finished_at.desc(), CollectorRun.id.desc())
            .limit(limit)
        ).all()
    )
    return {
        "items": [
            {
                "collector_id": run.collector_id,
                "run_id": run.run_id,
                "job_id": run.job_id,
                "source": run.source,
                "status": run.status,
                "summary": sanitize_event_payload(run.summary),
                "error_classification": run.error_classification,
                "error_summary": _safe_failure(run.error_summary),
                "started_at": run.started_at,
                "finished_at": run.finished_at,
            }
            for run in rows
        ]
    }


def get_transfer_view(db: Session, *, transport_id: str) -> dict[str, Any]:
    transfer = db.scalar(
        select(CollectorTransfer).where(CollectorTransfer.transport_id == transport_id)
    )
    if transfer is None:
        raise LookupError("collector transfer not found")
    receipt = db.scalar(
        select(CollectorTransferReceipt).where(CollectorTransferReceipt.transfer_id == transfer.id)
    )
    items = list(
        db.scalars(
            select(CollectorItemReceipt)
            .where(CollectorItemReceipt.transfer_id == transfer.id)
            .order_by(CollectorItemReceipt.id.asc())
        ).all()
    )
    return {
        "transfer": {
            "collector_id": transfer.collector_id,
            "transport_id": transfer.transport_id,
            "job_id": transfer.job_id,
            "run_id": transfer.run_id,
            "bundle_id": transfer.bundle_id,
            "destination": transfer.destination,
            "archive_size": transfer.archive_size,
            "archive_sha256": transfer.archive_sha256,
            "status": transfer.status,
            "attempt_count": transfer.attempt_count,
            "error_classification": transfer.error_classification,
            "error_summary": _safe_failure(transfer.error_summary),
            "ready_at": transfer.ready_at,
            "processed_at": transfer.processed_at,
        },
        "receipt": (
            None
            if receipt is None
            else {
                "status": receipt.status,
                "item_count": receipt.item_count,
                "completed_item_count": receipt.completed_item_count,
                "archive_sha256": receipt.archive_sha256,
                "error_classification": receipt.error_classification,
                "error_summary": _safe_failure(receipt.error_summary),
                "replay_evidence_ref": receipt.replay_evidence_ref,
                "completed_at": receipt.completed_at,
            }
        ),
        "items": [
            {
                "item_key": item.item_key,
                "source": item.source,
                "source_item_id": item.source_item_id,
                "status": item.status,
                "game_id": item.game_id,
                "outcome": sanitize_event_payload(item.outcome),
                "error_classification": item.error_classification,
                "error_summary": _safe_failure(item.error_summary),
                "completed_at": item.completed_at,
            }
            for item in items
        ],
    }


def get_backlog_view(db: Session) -> dict[str, Any]:
    rows = db.execute(
        select(CollectorTransfer.status, func.count(CollectorTransfer.id))
        .where(
            CollectorTransfer.status.in_(
                ("ready", "processing", "retry_wait", "failed", "dead_letter")
            )
        )
        .group_by(CollectorTransfer.status)
    ).all()
    oldest_ready_at = db.scalar(
        select(func.min(CollectorTransfer.ready_at)).where(CollectorTransfer.status == "ready")
    )
    counts = {
        status: 0 for status in ("ready", "processing", "retry_wait", "failed", "dead_letter")
    }
    counts.update({str(status): int(count) for status, count in rows})
    return {"counts": counts, "oldest_ready_at": oldest_ready_at}


@collector_management_router.get("/nodes")
def nodes(
    cursor: int | None = Query(None, ge=1),
    limit: int = Query(100, ge=1, le=500),
    stale_after_seconds: int = Query(300, ge=30, le=86400),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> dict[str, Any]:
    return list_node_views(
        db,
        cursor=cursor,
        limit=limit,
        stale_after_seconds=stale_after_seconds,
    )


@collector_management_router.get("/runs/{run_id}")
def run_detail(
    run_id: str,
    event_limit: int = Query(500, ge=1, le=2000),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> dict[str, Any]:
    try:
        return get_run_view(db, run_id=run_id, event_limit=event_limit)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@collector_management_router.get("/runs")
def runs(
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> dict[str, Any]:
    return list_run_views(db, limit=limit)


@collector_management_router.get("/transfers/{transport_id}")
def transfer_detail(
    transport_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> dict[str, Any]:
    try:
        return get_transfer_view(db, transport_id=transport_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@collector_management_router.get("/backlog")
def backlog(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> dict[str, Any]:
    return get_backlog_view(db)
