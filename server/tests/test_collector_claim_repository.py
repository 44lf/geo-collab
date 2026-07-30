from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import mysql

from server.app.modules.collector.claim_repository import (
    ConsumerLeaseConflict,
    claim_ready_transfers,
    release_transfer_for_retry,
    renew_transfer_lease,
)
from server.app.modules.collector.models import CollectorTransfer

NOW = datetime(2026, 7, 29, 6, 0)


def _transfer(identity: int, **changes) -> CollectorTransfer:
    values = {
        "id": identity,
        "collector_id": "collector-local-1",
        "transport_id": f"transport-{identity}",
        "job_id": "job-1",
        "run_id": "run-1",
        "bundle_id": f"bundle-{identity}",
        "destination": "geo-production",
        "object_key": f"incoming/collector-local-1/transport-{identity}.tar.zst",
        "archive_size": 1024,
        "archive_sha256": f"{identity:x}".rjust(64, "0"),
        "bundle_schema_version": "2",
        "status": "ready",
        "ready_at": NOW - timedelta(minutes=10 - identity),
        "attempt_count": 0,
    }
    values.update(changes)
    return CollectorTransfer(**values)


def test_claim_is_oldest_first_bounded_and_uses_skip_locked():
    rows = [_transfer(1), _transfer(2)]
    scalars = MagicMock()
    scalars.all.return_value = rows
    db = MagicMock()
    db.scalars.return_value = scalars

    claimed = claim_ready_transfers(
        db,
        worker_id="consumer-a",
        batch_size=2,
        lease_duration=timedelta(minutes=5),
        now=NOW,
    )

    assert claimed == rows
    assert all(row.status == "processing" for row in rows)
    assert all(row.claimed_by == "consumer-a" for row in rows)
    assert all(row.lease_until == NOW + timedelta(minutes=5) for row in rows)
    statement = db.scalars.call_args.args[0]
    sql = str(statement.compile(dialect=mysql.dialect()))
    assert "ORDER BY" in sql
    assert "collector_transfers.ready_at" in sql
    assert "retry_wait" in str(
        statement.compile(dialect=mysql.dialect(), compile_kwargs={"literal_binds": True})
    )
    assert "LIMIT" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql
    db.flush.assert_called_once()


def test_expired_processing_lease_is_reclaimable_without_new_transfer():
    expired = _transfer(
        1,
        status="processing",
        claimed_by="dead-consumer",
        lease_until=NOW - timedelta(seconds=1),
    )
    scalars = MagicMock()
    scalars.all.return_value = [expired]
    db = MagicMock()
    db.scalars.return_value = scalars

    claimed = claim_ready_transfers(
        db,
        worker_id="consumer-b",
        batch_size=10,
        lease_duration=timedelta(minutes=2),
        now=NOW,
    )

    assert claimed == [expired]
    assert expired.claimed_by == "consumer-b"
    assert expired.lease_until == NOW + timedelta(minutes=2)
    assert expired.attempt_count == 1


def test_active_processing_lease_is_excluded_by_claim_query():
    db = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = []
    db.scalars.return_value = scalars

    assert (
        claim_ready_transfers(
            db,
            worker_id="consumer-b",
            batch_size=10,
            lease_duration=timedelta(minutes=2),
            now=NOW,
        )
        == []
    )
    statement = db.scalars.call_args.args[0]
    sql = str(statement.compile(dialect=mysql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "status = 'ready'" in sql
    assert "status = 'processing'" in sql
    assert "lease_until <=" in sql


def test_only_lease_owner_can_renew_or_release_for_retry():
    transfer = _transfer(
        1,
        status="processing",
        claimed_by="consumer-a",
        lease_until=NOW + timedelta(minutes=1),
    )
    db = MagicMock()

    renewed = renew_transfer_lease(
        db,
        transfer,
        worker_id="consumer-a",
        lease_duration=timedelta(minutes=5),
        now=NOW,
    )
    assert renewed.lease_until == NOW + timedelta(minutes=5)

    with pytest.raises(ConsumerLeaseConflict, match="lease owner"):
        release_transfer_for_retry(
            db,
            transfer,
            worker_id="consumer-b",
            classification="destination_unavailable",
            error_summary="database unavailable",
        )

    released = release_transfer_for_retry(
        db,
        transfer,
        worker_id="consumer-a",
        classification="destination_unavailable",
        error_summary="database unavailable",
        now=NOW,
    )
    assert released.status == "retry_wait"
    assert released.claimed_by is None
    assert released.lease_until is None
    assert released.ready_at == NOW + timedelta(seconds=30)
    assert released.error_summary == "database unavailable"


def test_fencing_token_and_expired_lease_cannot_renew_or_release():
    transfer = _transfer(
        1,
        status="processing",
        claimed_by="consumer-a",
        lease_until=NOW + timedelta(minutes=1),
        attempt_count=2,
    )
    db = MagicMock()

    with pytest.raises(ConsumerLeaseConflict, match="fencing token"):
        renew_transfer_lease(
            db,
            transfer,
            worker_id="consumer-a",
            attempt_count=1,
            lease_duration=timedelta(minutes=5),
            now=NOW,
        )

    transfer.lease_until = NOW - timedelta(seconds=1)
    with pytest.raises(ConsumerLeaseConflict, match="expired"):
        release_transfer_for_retry(
            db,
            transfer,
            worker_id="consumer-a",
            attempt_count=2,
            classification="destination_unavailable",
            error_summary="database unavailable",
            now=NOW,
        )

    assert transfer.status == "processing"
    assert transfer.claimed_by == "consumer-a"
    db.flush.assert_not_called()


@pytest.mark.parametrize(
    ("batch_size", "lease"),
    [
        (0, timedelta(minutes=1)),
        (101, timedelta(minutes=1)),
        (1, timedelta(0)),
        (1, timedelta(hours=2)),
    ],
)
def test_claim_bounds_are_enforced(batch_size, lease):
    with pytest.raises(ValueError):
        claim_ready_transfers(
            MagicMock(),
            worker_id="consumer-a",
            batch_size=batch_size,
            lease_duration=lease,
            now=NOW,
        )
