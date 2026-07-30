"""Lease-based oldest-first claiming for the Collector Consumer."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from server.app.core.time import utcnow
from server.app.modules.collector.models import CollectorTransfer


class ConsumerLeaseConflict(RuntimeError):
    """A Consumer attempted to mutate a lease it does not own."""


def _validate_lease_request(*, worker_id: str, batch_size: int, lease_duration: timedelta) -> None:
    if not worker_id or worker_id != worker_id.strip() or len(worker_id) > 128:
        raise ValueError("worker_id must be non-empty trimmed text of at most 128 characters")
    if not 1 <= batch_size <= 100:
        raise ValueError("batch_size must be between 1 and 100")
    if not timedelta(seconds=1) <= lease_duration <= timedelta(hours=1):
        raise ValueError("lease_duration must be between 1 second and 1 hour")


def claim_ready_transfers(
    db: Session,
    *,
    worker_id: str,
    batch_size: int,
    lease_duration: timedelta,
    now: datetime | None = None,
) -> list[CollectorTransfer]:
    """Lock and claim the oldest ready or expired-processing transfers."""

    _validate_lease_request(
        worker_id=worker_id,
        batch_size=batch_size,
        lease_duration=lease_duration,
    )
    claimed_at = now or utcnow()
    statement = (
        select(CollectorTransfer)
        .where(
            or_(
                CollectorTransfer.status == "ready",
                and_(
                    CollectorTransfer.status == "retry_wait",
                    CollectorTransfer.ready_at <= claimed_at,
                ),
                and_(
                    CollectorTransfer.status == "processing",
                    CollectorTransfer.lease_until <= claimed_at,
                ),
            )
        )
        .order_by(
            CollectorTransfer.ready_at.asc(),
            CollectorTransfer.id.asc(),
        )
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )
    transfers = list(db.scalars(statement).all())
    for transfer in transfers:
        transfer.status = "processing"
        transfer.claimed_by = worker_id
        transfer.lease_until = claimed_at + lease_duration
        transfer.attempt_count += 1
    db.flush()
    return transfers


def _require_owner(
    transfer: CollectorTransfer,
    *,
    worker_id: str,
    attempt_count: int | None = None,
    now: datetime | None = None,
) -> None:
    if transfer.status != "processing" or transfer.claimed_by != worker_id:
        raise ConsumerLeaseConflict("transfer lease owner does not match")
    if attempt_count is not None and transfer.attempt_count != attempt_count:
        raise ConsumerLeaseConflict("transfer fencing token does not match")
    if now is not None and (transfer.lease_until is None or transfer.lease_until <= now):
        raise ConsumerLeaseConflict("transfer lease has expired")


def renew_transfer_lease(
    db: Session,
    transfer: CollectorTransfer,
    *,
    worker_id: str,
    attempt_count: int | None = None,
    lease_duration: timedelta,
    now: datetime | None = None,
) -> CollectorTransfer:
    _validate_lease_request(
        worker_id=worker_id,
        batch_size=1,
        lease_duration=lease_duration,
    )
    renewed_at = now or utcnow()
    _require_owner(
        transfer,
        worker_id=worker_id,
        attempt_count=attempt_count,
        now=renewed_at,
    )
    transfer.lease_until = renewed_at + lease_duration
    db.flush()
    return transfer


def release_transfer_for_retry(
    db: Session,
    transfer: CollectorTransfer,
    *,
    worker_id: str,
    attempt_count: int | None = None,
    classification: str,
    error_summary: str,
    retry_delay: timedelta = timedelta(seconds=30),
    now: datetime | None = None,
) -> CollectorTransfer:
    released_at = now or utcnow()
    _require_owner(
        transfer,
        worker_id=worker_id,
        attempt_count=attempt_count,
        now=released_at,
    )
    if not classification or classification != classification.strip():
        raise ValueError("classification must be non-empty trimmed text")
    if not timedelta(0) <= retry_delay <= timedelta(hours=1):
        raise ValueError("retry_delay must be between 0 seconds and 1 hour")
    transfer.status = "retry_wait"
    transfer.ready_at = released_at + retry_delay
    transfer.claimed_by = None
    transfer.lease_until = None
    transfer.error_classification = classification[:64]
    transfer.error_summary = error_summary[:1000]
    db.flush()
    return transfer
