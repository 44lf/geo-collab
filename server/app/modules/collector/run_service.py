"""Idempotent completed-run registration before Bundle transfer creation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from server.app.modules.collector.models import CollectorJob, CollectorRun
from server.app.modules.collector.telemetry import sanitize_event_payload, sanitize_log_text

_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_STATUSES = {"succeeded", "partial", "failed"}
_SOURCES = {"baidu", "ninegame", "yingyongbao", "taptap"}


class RunCompletionError(RuntimeError):
    pass


class RunCompletionConflict(RunCompletionError):
    pass


@dataclass(frozen=True, slots=True)
class RunCompletion:
    run_id: str
    job_id: str
    source: str | None
    status: str
    summary: dict[str, Any]
    error_classification: str | None
    error_summary: str | None
    started_at: datetime
    finished_at: datetime


@dataclass(frozen=True, slots=True)
class RunCompletionResult:
    run: CollectorRun
    created: bool


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise RunCompletionError("run timestamps must include a UTC offset")
    return value.astimezone(UTC).replace(tzinfo=None)


def _validate(
    completion: RunCompletion,
) -> tuple[datetime, datetime, dict[str, Any], str | None]:
    if (
        _SAFE_ID.fullmatch(completion.run_id) is None
        or _SAFE_ID.fullmatch(completion.job_id) is None
    ):
        raise RunCompletionError("run identity is invalid")
    if completion.status not in _STATUSES:
        raise RunCompletionError("run status is invalid")
    if completion.source is not None and completion.source not in _SOURCES:
        raise RunCompletionError("run source is invalid")
    started_at = _naive_utc(completion.started_at)
    finished_at = _naive_utc(completion.finished_at)
    if finished_at < started_at:
        raise RunCompletionError("run finished_at precedes started_at")
    summary = sanitize_event_payload(completion.summary) or {}
    if len(json.dumps(summary, ensure_ascii=False).encode("utf-8")) > 64 * 1024:
        raise RunCompletionError("run summary exceeds byte limit")
    if completion.error_classification is not None and (
        not completion.error_classification.strip() or len(completion.error_classification) > 64
    ):
        raise RunCompletionError("run error classification is invalid")
    error_summary = (
        sanitize_log_text(completion.error_summary)
        if completion.error_summary is not None
        else None
    )
    if error_summary is not None and (not error_summary.strip() or len(error_summary) > 1000):
        raise RunCompletionError("run error summary is invalid")
    return started_at, finished_at, summary, error_summary


def _same(
    run: CollectorRun,
    completion: RunCompletion,
    *,
    started_at: datetime,
    finished_at: datetime,
    summary: dict[str, Any],
    error_summary: str | None,
) -> bool:
    return (
        run.job_id == completion.job_id
        and run.source == completion.source
        and run.status == completion.status
        and run.summary == summary
        and run.error_classification == completion.error_classification
        and run.error_summary == error_summary
        and run.started_at == started_at
        and run.finished_at == finished_at
    )


def record_completed_run(
    db: Session,
    *,
    collector_id: str,
    destination: str,
    completion: RunCompletion,
) -> RunCompletionResult:
    started_at, finished_at, summary, error_summary = _validate(completion)
    job = cast(
        CollectorJob | None,
        db.scalar(
            select(CollectorJob).where(
                CollectorJob.collector_id == collector_id,
                CollectorJob.job_id == completion.job_id,
            )
        ),
    )
    if (
        job is None
        or job.destination != destination
        or job.status not in {"claimed", "acknowledged", "completed", "failed"}
    ):
        raise RunCompletionError("run job is missing or outside collector scope")
    existing = cast(
        CollectorRun | None,
        db.scalar(
            select(CollectorRun).where(
                CollectorRun.collector_id == collector_id,
                CollectorRun.run_id == completion.run_id,
            )
        ),
    )
    if existing is not None:
        if not _same(
            existing,
            completion,
            started_at=started_at,
            finished_at=finished_at,
            summary=summary,
            error_summary=error_summary,
        ):
            raise RunCompletionConflict("run identity already has different terminal fields")
        return RunCompletionResult(run=existing, created=False)
    run = CollectorRun(
        collector_id=collector_id,
        run_id=completion.run_id,
        job_pk_id=job.id,
        job_id=job.job_id,
        source=completion.source,
        status=completion.status,
        current_stage="completed",
        summary=summary,
        error_classification=completion.error_classification,
        error_summary=error_summary,
        started_at=started_at,
        finished_at=finished_at,
    )
    db.add(run)
    job.status = "failed" if completion.status == "failed" else "completed"
    job.completed_at = finished_at
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        winner = cast(
            CollectorRun | None,
            db.scalar(
                select(CollectorRun).where(
                    CollectorRun.collector_id == collector_id,
                    CollectorRun.run_id == completion.run_id,
                )
            ),
        )
        if winner is None or not _same(
            winner,
            completion,
            started_at=started_at,
            finished_at=finished_at,
            summary=summary,
            error_summary=error_summary,
        ):
            raise RunCompletionConflict("concurrent run identity conflict") from None
        return RunCompletionResult(run=winner, created=False)
    return RunCompletionResult(run=run, created=True)
