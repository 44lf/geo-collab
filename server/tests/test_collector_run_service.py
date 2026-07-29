from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from server.app.modules.collector.models import CollectorJob, CollectorRun
from server.app.modules.collector.run_service import (
    RunCompletion,
    RunCompletionConflict,
    RunCompletionError,
    record_completed_run,
)

NOW = datetime(2026, 7, 29, 8, 0, tzinfo=UTC)


def _job(**changes):
    values = {
        "id": 3,
        "collector_id": "collector-1",
        "job_id": "job-1",
        "claim_request_id": "claim-1",
        "config_version_id": 1,
        "mode": "refresh",
        "destination": "geo-dev",
        "manifest": {},
        "manifest_sha256": "a" * 64,
        "status": "acknowledged",
    }
    values.update(changes)
    return CollectorJob(**values)


def _completion(**changes):
    values = {
        "run_id": "run-1",
        "job_id": "job-1",
        "source": None,
        "status": "partial",
        "summary": {
            "success": 3,
            "upload_url": "https://minio.test/?X-Amz-Signature=secret",
        },
        "error_classification": None,
        "error_summary": None,
        "started_at": NOW,
        "finished_at": NOW + timedelta(minutes=1),
    }
    values.update(changes)
    return RunCompletion(**values)


def test_record_completed_run_is_scoped_sanitized_and_idempotent():
    job = _job()
    db = MagicMock()
    db.scalar.side_effect = [job, None]

    result = record_completed_run(
        db,
        collector_id="collector-1",
        destination="geo-dev",
        completion=_completion(),
    )

    assert result.created is True
    assert result.run.status == "partial"
    assert result.run.summary == {"success": 3}
    assert result.run.started_at.tzinfo is None
    assert job.status == "completed"

    replay_db = MagicMock()
    replay_db.scalar.side_effect = [job, result.run]
    replay = record_completed_run(
        replay_db,
        collector_id="collector-1",
        destination="geo-dev",
        completion=_completion(),
    )
    assert replay.created is False
    replay_db.add.assert_not_called()


def test_record_completed_run_redacts_secrets_from_error_summary():
    job = _job()
    db = MagicMock()
    db.scalar.side_effect = [job, None]

    result = record_completed_run(
        db,
        collector_id="collector-1",
        destination="geo-dev",
        completion=_completion(
            status="failed",
            error_classification="dependency",
            error_summary=(
                "Authorization: Bearer secret-token "
                "mysql://user:password@db.internal/geo "
                "https://minio.test/inbox?X-Amz-Signature=secret"
            ),
        ),
    )

    assert result.run.error_summary == (
        "Authorization: Bearer [REDACTED] "
        "mysql://[REDACTED]@db.internal/geo "
        "https://minio.test/inbox"
    )
    assert job.status == "failed"


def test_run_identity_conflict_and_cross_destination_are_rejected():
    existing = CollectorRun(
        collector_id="collector-1",
        run_id="run-1",
        job_pk_id=3,
        job_id="job-1",
        status="succeeded",
        current_stage="completed",
        summary={},
        started_at=NOW.replace(tzinfo=None),
        finished_at=(NOW + timedelta(minutes=1)).replace(tzinfo=None),
    )
    conflict_db = MagicMock()
    conflict_db.scalar.side_effect = [_job(), existing]
    with pytest.raises(RunCompletionConflict, match="different"):
        record_completed_run(
            conflict_db,
            collector_id="collector-1",
            destination="geo-dev",
            completion=_completion(),
        )

    denied = MagicMock()
    denied.scalar.return_value = _job(destination="geo-production")
    with pytest.raises(RunCompletionError, match="outside"):
        record_completed_run(
            denied,
            collector_id="collector-1",
            destination="geo-dev",
            completion=_completion(),
        )


def test_run_timestamps_and_status_fail_closed():
    db = MagicMock()
    with pytest.raises(RunCompletionError, match="UTC offset"):
        record_completed_run(
            db,
            collector_id="collector-1",
            destination="geo-dev",
            completion=_completion(started_at=NOW.replace(tzinfo=None)),
        )
    with pytest.raises(RunCompletionError, match="status"):
        record_completed_run(
            db,
            collector_id="collector-1",
            destination="geo-dev",
            completion=_completion(status="running"),
        )
