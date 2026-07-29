"""Production wiring for one claimed Collector transfer."""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import DisconnectionError, OperationalError, TimeoutError
from urllib3.exceptions import HTTPError as Urllib3HTTPError

from server.app.core.config import get_settings
from server.app.core.time import utcnow
from server.app.modules.collector.bundle_validation import (
    BundleValidationError,
    BundleValidationLimits,
    validate_and_extract_bundle,
)
from server.app.modules.collector.claim_repository import ConsumerLeaseConflict
from server.app.modules.collector.consumer_bundle import import_callback, prepare_consumer_items
from server.app.modules.collector.consumer_processing import (
    ConsumerProcessingCoordinator,
    PermanentConsumerError,
    ProcessingCallbacks,
    RetryPolicy,
)
from server.app.modules.collector.consumer_runtime import (
    LeaseHandle,
    ProcessResult,
    TransferClaim,
)
from server.app.modules.collector.import_service import CollectorImportService
from server.app.modules.collector.inbox import MinioCollectorInbox, get_collector_inbox
from server.app.modules.collector.models import (
    CollectorJob,
    CollectorTransfer,
    CollectorTransferReceipt,
)

SessionFactory = Callable[[], Any]


def _default_limits() -> BundleValidationLimits:
    settings = get_settings()
    return BundleValidationLimits(
        max_archive_bytes=settings.collector_max_archive_bytes,
        max_files=settings.collector_max_bundle_files,
        max_file_bytes=settings.collector_max_bundle_file_bytes,
        max_total_bytes=settings.collector_max_bundle_total_bytes,
        max_manifest_bytes=settings.collector_max_manifest_bytes,
    )


class ClaimedTransferProcessor:
    """Download, validate, normalize, import, and receipt one detached claim."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        inbox: MinioCollectorInbox,
        limits: BundleValidationLimits,
        import_service: CollectorImportService | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._inbox = inbox
        self._limits = limits
        self._import_service = import_service or CollectorImportService()
        self._retry_policy = retry_policy or RetryPolicy()

    def __call__(self, claim: TransferClaim, lease: LeaseHandle) -> ProcessResult:
        try:
            return self._process(claim, lease)
        except ConsumerLeaseConflict:
            lease.released = True
            return ProcessResult.terminal()
        except BundleValidationError as exc:
            return self._terminalize(
                claim,
                lease,
                classification="schema",
                error_summary=f"BundleValidationError: {exc}",
            )
        except PermanentConsumerError as exc:
            return self._terminalize(
                claim,
                lease,
                classification=exc.classification,
                error_summary=f"{type(exc).__name__}: {exc}",
            )
        except (
            OperationalError,
            DisconnectionError,
            TimeoutError,
            Urllib3HTTPError,
            OSError,
        ) as exc:
            if claim.attempt_count >= self._retry_policy.max_transfer_attempts:
                return self._terminalize(
                    claim,
                    lease,
                    classification="consumer_dependency_unavailable",
                    error_summary=(f"{type(exc).__name__}; transfer attempt ceiling reached"),
                )
            return ProcessResult.retry(
                classification="consumer_dependency_unavailable",
                error_summary=type(exc).__name__,
            )
        except Exception as exc:  # noqa: BLE001 - unexpected failures have a finite queue ceiling
            if claim.attempt_count >= self._retry_policy.max_transfer_attempts:
                return self._terminalize(
                    claim,
                    lease,
                    classification="unexpected",
                    error_summary=(f"{type(exc).__name__}; transfer attempt ceiling reached"),
                )
            return ProcessResult.retry(
                classification="consumer_unexpected_error",
                error_summary=type(exc).__name__,
            )

    def _process(self, claim: TransferClaim, lease: LeaseHandle) -> ProcessResult:
        with self._session_factory() as db:
            transfer = db.get(CollectorTransfer, claim.transfer_id)
            if transfer is None:
                raise BundleValidationError("claimed transfer is missing")
            if not _claim_matches(transfer, claim, now=utcnow()):
                raise ConsumerLeaseConflict("claimed transfer identity or lease no longer matches")
            job = db.scalar(
                select(CollectorJob).where(
                    CollectorJob.collector_id == claim.collector_id,
                    CollectorJob.job_id == claim.job_id,
                )
            )
            if job is None:
                raise BundleValidationError("claimed transfer job is missing")

            with tempfile.TemporaryDirectory(prefix="geo-collector-consumer-") as temporary:
                root = Path(temporary)
                archive = self._inbox.download_to(
                    object_key=claim.object_key,
                    destination=root / "bundle.tar.zst",
                    expected_size=transfer.archive_size,
                )
                validated = validate_and_extract_bundle(
                    archive,
                    destination=root / "extracted",
                    transfer=transfer,
                    job_manifest=job.manifest,
                    limits=self._limits,
                )
                items = prepare_consumer_items(validated, job_manifest=job.manifest)
                coordinator = ConsumerProcessingCoordinator(
                    callbacks=ProcessingCallbacks(
                        import_item=import_callback(self._import_service),
                        before_item_commit=lambda *_args: lease.renew(),
                        before_transfer_finalize=lambda *_args: lease.renew(),
                    ),
                    retry_policy=self._retry_policy,
                )
                result = coordinator.process(db, transfer, items)
        if result.status == "processed":
            return ProcessResult.processed()
        if result.status == "ready":
            return ProcessResult.retry_persisted(
                classification=result.error_classification or "consumer_retry"
            )
        return ProcessResult.terminal()

    def _terminalize(
        self,
        claim: TransferClaim,
        lease: LeaseHandle,
        *,
        classification: str,
        error_summary: str,
    ) -> ProcessResult:
        try:
            self._dead_letter(
                claim,
                classification=classification,
                error_summary=error_summary,
            )
        except ConsumerLeaseConflict:
            lease.released = True
        return ProcessResult.terminal()

    def _dead_letter(
        self,
        claim: TransferClaim,
        *,
        classification: str,
        error_summary: str,
    ) -> None:
        with self._session_factory() as db:
            transfer = db.get(CollectorTransfer, claim.transfer_id)
            if transfer is None:
                return
            existing = db.scalar(
                select(CollectorTransferReceipt).where(
                    CollectorTransferReceipt.transfer_id == transfer.id
                )
            )
            if existing is not None:
                return
            completed_at = utcnow()
            safe_summary = error_summary[:1000]
            fenced = db.execute(
                update(CollectorTransfer)
                .where(
                    CollectorTransfer.id == claim.transfer_id,
                    CollectorTransfer.status == "processing",
                    CollectorTransfer.claimed_by == claim.worker_id,
                    CollectorTransfer.attempt_count == claim.attempt_count,
                    CollectorTransfer.lease_until > completed_at,
                )
                .values(
                    status="dead_letter",
                    claimed_by=None,
                    lease_until=None,
                    error_classification=classification[:64],
                    error_summary=safe_summary,
                )
            )
            if fenced.rowcount != 1:
                db.rollback()
                raise ConsumerLeaseConflict("transfer lease fencing token no longer owns the row")
            db.add(
                CollectorTransferReceipt(
                    transfer_id=transfer.id,
                    collector_id=transfer.collector_id,
                    transport_id=transfer.transport_id,
                    archive_sha256=transfer.archive_sha256,
                    status="dead_letter",
                    item_count=0,
                    completed_item_count=0,
                    error_classification=classification[:64],
                    error_summary=safe_summary,
                    replay_evidence_ref=(
                        f"collector://transfers/{transfer.transport_id}/dead-letter"
                    ),
                    completed_at=completed_at,
                )
            )
            transfer.status = "dead_letter"
            transfer.claimed_by = None
            transfer.lease_until = None
            transfer.error_classification = classification[:64]
            transfer.error_summary = safe_summary
            db.commit()


def _claim_matches(
    transfer: CollectorTransfer,
    claim: TransferClaim,
    *,
    now: datetime,
) -> bool:
    return (
        transfer.status == "processing"
        and transfer.claimed_by == claim.worker_id
        and transfer.attempt_count == claim.attempt_count
        and transfer.lease_until is not None
        and transfer.lease_until > now
        and transfer.collector_id == claim.collector_id
        and transfer.transport_id == claim.transport_id
        and transfer.job_id == claim.job_id
        and transfer.run_id == claim.run_id
        and transfer.bundle_id == claim.bundle_id
        and transfer.destination == claim.destination
        and transfer.object_key == claim.object_key
    )


def build_default_claimed_transfer_processor() -> ClaimedTransferProcessor:
    from server.app.db.session import SessionLocal

    return ClaimedTransferProcessor(
        session_factory=SessionLocal,
        inbox=get_collector_inbox(),
        limits=_default_limits(),
    )


def process_claimed_transfer(
    claim: TransferClaim,
    lease: LeaseHandle,
) -> ProcessResult:
    """Default late-bound function used by ``consumer_cli``."""

    return build_default_claimed_transfer_processor()(claim, lease)
