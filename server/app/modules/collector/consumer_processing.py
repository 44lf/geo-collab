"""Crash-safe item transaction coordination for the Collector Consumer.

The injected importer must write through the supplied session without committing. This
coordinator adds the matching item receipt and commits both effects together. It deliberately
does not depend on ``CollectorImportService`` directly; the runtime injects its transaction-aware
session methods without duplicating the coordination rules here.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from sqlalchemy import select, update
from sqlalchemy.exc import DisconnectionError, OperationalError, TimeoutError

from server.app.core.time import utcnow
from server.app.modules.collector.claim_repository import ConsumerLeaseConflict
from server.app.modules.collector.models import (
    CollectorItemReceipt,
    CollectorTransfer,
    CollectorTransferReceipt,
)

ItemReceiptLoader = Callable[[Any, CollectorTransfer], Sequence[CollectorItemReceipt]]
TransferReceiptLoader = Callable[[Any, CollectorTransfer], CollectorTransferReceipt | None]
ItemImporter = Callable[[Any, "ConsumerItem"], "ItemImportResult"]
ItemCommitHook = Callable[
    [Any, CollectorTransfer, "ConsumerItem", CollectorItemReceipt],
    None,
]
TransferFinalizeHook = Callable[[Any, CollectorTransfer], None]


class ConsumerProcessingError(RuntimeError):
    """Base class for errors with an explicit safe processing classification."""

    classification = "permanent"


class RetryableConsumerError(ConsumerProcessingError):
    """A temporary destination failure that may be retried without changing identity."""


class TemporaryMySQLError(RetryableConsumerError):
    classification = "mysql_unavailable"


class TemporaryBusinessMinioError(RetryableConsumerError):
    classification = "business_minio_unavailable"


class PermanentConsumerError(ConsumerProcessingError):
    """A deterministic failure that must not be retried automatically."""


class SchemaValidationError(PermanentConsumerError):
    classification = "schema"


class IdentityValidationError(PermanentConsumerError):
    classification = "identity"


class SecurityValidationError(PermanentConsumerError):
    classification = "security"


class DiscoveryConflictError(PermanentConsumerError):
    """One discovery item is ambiguous and must be isolated from sibling items."""

    classification = "discovery_conflict"


@dataclass(frozen=True)
class ConsumerItem:
    item_key: str
    source: str
    source_item_id: str | None
    mode: Literal["refresh", "discovery"]
    payload: dict[str, Any]

    def __post_init__(self) -> None:
        for field_name in ("item_key", "source"):
            value = getattr(self, field_name)
            if not value or value != value.strip():
                raise ValueError(f"{field_name} must be non-empty trimmed text")
        if len(self.item_key) > 255:
            raise ValueError("item_key must be at most 255 characters")
        if len(self.source) > 32:
            raise ValueError("source must be at most 32 characters")
        if self.source_item_id is not None and len(self.source_item_id) > 255:
            raise ValueError("source_item_id must be at most 255 characters")


@dataclass(frozen=True)
class ItemImportResult:
    status: Literal["succeeded", "skipped"]
    game_id: int | None = None
    outcome: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.status == "succeeded" and self.game_id is None:
            raise ValueError("succeeded import requires game_id")
        if self.game_id is not None and self.game_id <= 0:
            raise ValueError("game_id must be positive")

    @classmethod
    def succeeded(
        cls,
        *,
        game_id: int,
        outcome: dict[str, Any] | None = None,
    ) -> ItemImportResult:
        return cls(status="succeeded", game_id=game_id, outcome=outcome)

    @classmethod
    def skipped(
        cls,
        *,
        outcome: dict[str, Any] | None = None,
    ) -> ItemImportResult:
        return cls(status="skipped", outcome=outcome)


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    max_transfer_attempts: int = 5
    initial_delay_seconds: float = 1.0
    multiplier: float = 2.0
    max_delay_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= 10:
            raise ValueError("max_attempts must be between 1 and 10")
        if not 1 <= self.max_transfer_attempts <= 100:
            raise ValueError("max_transfer_attempts must be between 1 and 100")
        if not 0 <= self.initial_delay_seconds <= 60:
            raise ValueError("initial_delay_seconds must be between 0 and 60")
        if not 1 <= self.multiplier <= 10:
            raise ValueError("multiplier must be between 1 and 10")
        if not self.initial_delay_seconds <= self.max_delay_seconds <= 3600:
            raise ValueError(
                "max_delay_seconds must be at least the initial delay and at most 3600"
            )

    def delay_after_failure(self, failure_number: int) -> float:
        return min(
            self.initial_delay_seconds * self.multiplier ** (failure_number - 1),
            self.max_delay_seconds,
        )


def _load_item_receipts(
    db: Any,
    transfer: CollectorTransfer,
) -> tuple[CollectorItemReceipt, ...]:
    statement = select(CollectorItemReceipt).where(CollectorItemReceipt.transfer_id == transfer.id)
    return tuple(db.scalars(statement).all())


def _load_transfer_receipt(
    db: Any,
    transfer: CollectorTransfer,
) -> CollectorTransferReceipt | None:
    statement = select(CollectorTransferReceipt).where(
        CollectorTransferReceipt.transfer_id == transfer.id
    )
    return db.scalar(statement)


@dataclass(frozen=True)
class ProcessingCallbacks:
    import_item: ItemImporter
    load_item_receipts: ItemReceiptLoader = _load_item_receipts
    load_transfer_receipt: TransferReceiptLoader = _load_transfer_receipt
    before_item_commit: ItemCommitHook | None = None
    after_item_commit: ItemCommitHook | None = None
    before_transfer_finalize: TransferFinalizeHook | None = None


@dataclass(frozen=True)
class ProcessingResult:
    status: Literal["processed", "ready", "dead_letter"]
    completed_item_count: int
    skipped_existing_count: int = 0
    isolated_conflict_count: int = 0
    retry_count: int = 0
    error_classification: str | None = None


@dataclass(frozen=True)
class _ErrorDecision:
    disposition: Literal["retry", "dead_letter", "isolate"]
    classification: str


_SENSITIVE_QUERY_RE = re.compile(r"(?i)(token|secret|signature|credential|password)=([^&\s]+)")
_BEARER_RE = re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;]+")
_URI_PASSWORD_RE = re.compile(r"(?i)(://[^:/\s]+:)[^@\s]+(@)")


def _safe_error_summary(exc: BaseException) -> str:
    message = _SENSITIVE_QUERY_RE.sub(r"\1=<redacted>", str(exc))
    message = _BEARER_RE.sub(r"\1<redacted>", message)
    message = _URI_PASSWORD_RE.sub(r"\1<redacted>\2", message)
    return f"{type(exc).__name__}: {message}"[:1000]


def _fenced_transfer_update(
    db: Any,
    transfer: CollectorTransfer,
    *,
    now: datetime,
    values: dict[str, Any],
) -> None:
    owner = transfer.claimed_by
    attempt_count = transfer.attempt_count
    if not owner:
        db.rollback()
        raise ConsumerLeaseConflict("transfer lease has no owner")
    result = db.execute(
        update(CollectorTransfer)
        .where(
            CollectorTransfer.id == transfer.id,
            CollectorTransfer.status == "processing",
            CollectorTransfer.claimed_by == owner,
            CollectorTransfer.attempt_count == attempt_count,
            CollectorTransfer.lease_until > now,
        )
        .values(**values)
    )
    if result.rowcount != 1:
        db.rollback()
        raise ConsumerLeaseConflict("transfer lease fencing token no longer owns the row")


def _classify_error(exc: Exception) -> _ErrorDecision:
    if isinstance(exc, DiscoveryConflictError):
        return _ErrorDecision("isolate", exc.classification)
    if isinstance(exc, RetryableConsumerError):
        return _ErrorDecision("retry", exc.classification)
    if isinstance(exc, (OperationalError, DisconnectionError, TimeoutError)):
        return _ErrorDecision("retry", "mysql_unavailable")
    if isinstance(exc, PermanentConsumerError):
        return _ErrorDecision("dead_letter", exc.classification)
    return _ErrorDecision("dead_letter", "unexpected")


class ConsumerProcessingCoordinator:
    """Coordinate item business writes, receipts, retries, and terminal transfer state."""

    def __init__(
        self,
        *,
        callbacks: ProcessingCallbacks,
        retry_policy: RetryPolicy | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._callbacks = callbacks
        self._retry_policy = retry_policy or RetryPolicy()
        self._sleep = sleep

    def process(
        self,
        db: Any,
        transfer: CollectorTransfer,
        items: Sequence[ConsumerItem],
    ) -> ProcessingResult:
        item_tuple = tuple(items)
        self._validate_items(item_tuple)

        transfer_receipt = self._callbacks.load_transfer_receipt(db, transfer)
        if transfer_receipt is not None:
            return ProcessingResult(
                status=("processed" if transfer_receipt.status == "processed" else "dead_letter"),
                completed_item_count=transfer_receipt.completed_item_count,
                error_classification=transfer_receipt.error_classification,
            )

        receipts = {
            receipt.item_key: receipt
            for receipt in self._callbacks.load_item_receipts(db, transfer)
        }
        skipped_existing_count = 0
        isolated_conflict_count = 0
        retry_count = 0

        for item in item_tuple:
            existing = receipts.get(item.item_key)
            if existing is not None and existing.status in {"succeeded", "skipped"}:
                skipped_existing_count += 1
                if existing.error_classification == "discovery_conflict":
                    isolated_conflict_count += 1
                continue
            if existing is not None:
                return self._mark_dead_letter(
                    db,
                    transfer,
                    items=item_tuple,
                    receipts=receipts,
                    classification=existing.error_classification or "permanent",
                    error_summary=existing.error_summary or "existing failed item receipt",
                    failed_item=None,
                    skipped_existing_count=skipped_existing_count,
                    isolated_conflict_count=isolated_conflict_count,
                    retry_count=retry_count,
                )

            item_result = self._process_item(db, transfer, item)
            retry_count += item_result.retry_count
            if item_result.status == "ready":
                return ProcessingResult(
                    status="ready",
                    completed_item_count=len(receipts),
                    skipped_existing_count=skipped_existing_count,
                    isolated_conflict_count=isolated_conflict_count,
                    retry_count=retry_count,
                    error_classification=item_result.error_classification,
                )
            if item_result.status == "dead_letter":
                return self._mark_dead_letter(
                    db,
                    transfer,
                    items=item_tuple,
                    receipts=receipts,
                    classification=item_result.error_classification or "permanent",
                    error_summary=item_result.error_summary or "permanent item failure",
                    failed_item=item,
                    skipped_existing_count=skipped_existing_count,
                    isolated_conflict_count=isolated_conflict_count,
                    retry_count=retry_count,
                )

            receipt = item_result.receipt
            if receipt is None:
                raise RuntimeError("terminal item result did not provide a receipt")
            receipts[item.item_key] = receipt
            if receipt.error_classification == "discovery_conflict":
                isolated_conflict_count += 1

        return self._mark_processed(
            db,
            transfer,
            items=item_tuple,
            receipts=receipts,
            skipped_existing_count=skipped_existing_count,
            isolated_conflict_count=isolated_conflict_count,
            retry_count=retry_count,
        )

    @staticmethod
    def _validate_items(items: tuple[ConsumerItem, ...]) -> None:
        if not items:
            raise ValueError("items must not be empty")
        item_keys = tuple(item.item_key for item in items)
        if len(item_keys) != len(set(item_keys)):
            raise ValueError("item_key values must be unique")

    def _process_item(
        self,
        db: Any,
        transfer: CollectorTransfer,
        item: ConsumerItem,
    ) -> _ItemProcessingResult:
        retry_count = 0
        for attempt in range(1, self._retry_policy.max_attempts + 1):
            try:
                outcome = self._callbacks.import_item(db, item)
                receipt = CollectorItemReceipt(
                    transfer_id=transfer.id,
                    transport_id=transfer.transport_id,
                    item_key=item.item_key,
                    source=item.source,
                    source_item_id=item.source_item_id,
                    status=outcome.status,
                    game_id=outcome.game_id,
                    outcome=outcome.outcome,
                    error_classification=None,
                    error_summary=None,
                    completed_at=utcnow(),
                )
                db.add(receipt)
                if self._callbacks.before_item_commit is not None:
                    self._callbacks.before_item_commit(db, transfer, item, receipt)
                db.commit()
            except Exception as exc:
                db.rollback()
                if isinstance(exc, ConsumerLeaseConflict):
                    raise
                decision = _classify_error(exc)
                if decision.disposition == "isolate":
                    if item.mode != "discovery":
                        return _ItemProcessingResult(
                            status="dead_letter",
                            retry_count=retry_count,
                            error_classification="identity",
                            error_summary=_safe_error_summary(exc),
                        )
                    receipt = self._commit_isolated_conflict(
                        db,
                        transfer,
                        item,
                        error_summary=_safe_error_summary(exc),
                    )
                    return _ItemProcessingResult(
                        status="terminal",
                        receipt=receipt,
                        retry_count=retry_count,
                    )
                if decision.disposition == "dead_letter":
                    return _ItemProcessingResult(
                        status="dead_letter",
                        retry_count=retry_count,
                        error_classification=decision.classification,
                        error_summary=_safe_error_summary(exc),
                    )
                retry_count += 1
                if attempt == self._retry_policy.max_attempts:
                    if (
                        getattr(transfer, "attempt_count", 1)
                        >= self._retry_policy.max_transfer_attempts
                    ):
                        return _ItemProcessingResult(
                            status="dead_letter",
                            retry_count=retry_count,
                            error_classification=decision.classification,
                            error_summary=(
                                f"{_safe_error_summary(exc)}; transfer attempt ceiling reached"
                            ),
                        )
                    if self._callbacks.before_transfer_finalize is not None:
                        self._callbacks.before_transfer_finalize(db, transfer)
                    self._release_ready(
                        db,
                        transfer,
                        classification=decision.classification,
                        error_summary=_safe_error_summary(exc),
                        retry_delay=timedelta(
                            seconds=self._retry_policy.delay_after_failure(attempt)
                        ),
                    )
                    return _ItemProcessingResult(
                        status="ready",
                        retry_count=retry_count,
                        error_classification=decision.classification,
                    )
                self._sleep(self._retry_policy.delay_after_failure(attempt))
                continue

            if self._callbacks.after_item_commit is not None:
                self._callbacks.after_item_commit(db, transfer, item, receipt)
            return _ItemProcessingResult(
                status="terminal",
                receipt=receipt,
                retry_count=retry_count,
            )

        raise AssertionError("retry loop exhausted without a terminal result")

    def _commit_isolated_conflict(
        self,
        db: Any,
        transfer: CollectorTransfer,
        item: ConsumerItem,
        *,
        error_summary: str,
    ) -> CollectorItemReceipt:
        receipt = CollectorItemReceipt(
            transfer_id=transfer.id,
            transport_id=transfer.transport_id,
            item_key=item.item_key,
            source=item.source,
            source_item_id=item.source_item_id,
            status="skipped",
            game_id=None,
            outcome={"isolated": True},
            error_classification="discovery_conflict",
            error_summary=error_summary,
            completed_at=utcnow(),
        )
        db.add(receipt)
        if self._callbacks.before_item_commit is not None:
            self._callbacks.before_item_commit(db, transfer, item, receipt)
        db.commit()
        if self._callbacks.after_item_commit is not None:
            self._callbacks.after_item_commit(db, transfer, item, receipt)
        return receipt

    def _release_ready(
        self,
        db: Any,
        transfer: CollectorTransfer,
        *,
        classification: str,
        error_summary: str,
        retry_delay: timedelta,
    ) -> None:
        released_at = utcnow()
        ready_at = released_at + retry_delay
        safe_classification = classification[:64]
        safe_summary = error_summary[:1000]
        _fenced_transfer_update(
            db,
            transfer,
            now=released_at,
            values={
                "status": "retry_wait",
                "ready_at": ready_at,
                "claimed_by": None,
                "lease_until": None,
                "error_classification": safe_classification,
                "error_summary": safe_summary,
            },
        )
        transfer.status = "retry_wait"
        transfer.ready_at = ready_at
        transfer.claimed_by = None
        transfer.lease_until = None
        transfer.error_classification = safe_classification
        transfer.error_summary = safe_summary
        db.commit()

    def _mark_processed(
        self,
        db: Any,
        transfer: CollectorTransfer,
        *,
        items: tuple[ConsumerItem, ...],
        receipts: dict[str, CollectorItemReceipt],
        skipped_existing_count: int,
        isolated_conflict_count: int,
        retry_count: int,
    ) -> ProcessingResult:
        incomplete = [
            item.item_key
            for item in items
            if item.item_key not in receipts
            or receipts[item.item_key].status not in {"succeeded", "skipped"}
        ]
        if incomplete:
            raise RuntimeError(f"cannot process transfer with incomplete items: {incomplete}")
        if self._callbacks.before_transfer_finalize is not None:
            self._callbacks.before_transfer_finalize(db, transfer)
        completed_at = utcnow()
        _fenced_transfer_update(
            db,
            transfer,
            now=completed_at,
            values={
                "status": "processed",
                "claimed_by": None,
                "lease_until": None,
                "error_classification": None,
                "error_summary": None,
                "processed_at": completed_at,
            },
        )
        receipt = CollectorTransferReceipt(
            transfer_id=transfer.id,
            collector_id=transfer.collector_id,
            transport_id=transfer.transport_id,
            archive_sha256=transfer.archive_sha256,
            status="processed",
            item_count=len(items),
            completed_item_count=len(receipts),
            error_classification=None,
            error_summary=None,
            replay_evidence_ref=None,
            completed_at=completed_at,
        )
        db.add(receipt)
        transfer.status = "processed"
        transfer.claimed_by = None
        transfer.lease_until = None
        transfer.error_classification = None
        transfer.error_summary = None
        transfer.processed_at = completed_at
        db.commit()
        return ProcessingResult(
            status="processed",
            completed_item_count=len(receipts),
            skipped_existing_count=skipped_existing_count,
            isolated_conflict_count=isolated_conflict_count,
            retry_count=retry_count,
        )

    def _mark_dead_letter(
        self,
        db: Any,
        transfer: CollectorTransfer,
        *,
        items: tuple[ConsumerItem, ...],
        receipts: dict[str, CollectorItemReceipt],
        classification: str,
        error_summary: str,
        failed_item: ConsumerItem | None,
        skipped_existing_count: int,
        isolated_conflict_count: int,
        retry_count: int,
    ) -> ProcessingResult:
        if self._callbacks.before_transfer_finalize is not None:
            self._callbacks.before_transfer_finalize(db, transfer)
        completed_at = utcnow()
        safe_classification = classification[:64]
        safe_summary = error_summary[:1000]
        _fenced_transfer_update(
            db,
            transfer,
            now=completed_at,
            values={
                "status": "dead_letter",
                "claimed_by": None,
                "lease_until": None,
                "error_classification": safe_classification,
                "error_summary": safe_summary,
            },
        )
        if failed_item is not None and failed_item.item_key not in receipts:
            failed_receipt = CollectorItemReceipt(
                transfer_id=transfer.id,
                transport_id=transfer.transport_id,
                item_key=failed_item.item_key,
                source=failed_item.source,
                source_item_id=failed_item.source_item_id,
                status="failed",
                game_id=None,
                outcome=None,
                error_classification=safe_classification,
                error_summary=safe_summary,
                completed_at=completed_at,
            )
            db.add(failed_receipt)
            receipts[failed_item.item_key] = failed_receipt
        transfer_receipt = CollectorTransferReceipt(
            transfer_id=transfer.id,
            collector_id=transfer.collector_id,
            transport_id=transfer.transport_id,
            archive_sha256=transfer.archive_sha256,
            status="dead_letter",
            item_count=len(items),
            completed_item_count=len(receipts),
            error_classification=safe_classification,
            error_summary=safe_summary,
            replay_evidence_ref=(f"collector://transfers/{transfer.transport_id}/dead-letter"),
            completed_at=completed_at,
        )
        db.add(transfer_receipt)
        transfer.status = "dead_letter"
        transfer.claimed_by = None
        transfer.lease_until = None
        transfer.error_classification = safe_classification
        transfer.error_summary = safe_summary
        db.commit()
        return ProcessingResult(
            status="dead_letter",
            completed_item_count=len(receipts),
            skipped_existing_count=skipped_existing_count,
            isolated_conflict_count=isolated_conflict_count,
            retry_count=retry_count,
            error_classification=classification,
        )


@dataclass(frozen=True)
class _ItemProcessingResult:
    status: Literal["terminal", "ready", "dead_letter"]
    receipt: CollectorItemReceipt | None = None
    retry_count: int = 0
    error_classification: str | None = None
    error_summary: str | None = None


def process_claimed_transfer(claim: Any, lease: Any) -> Any:
    """Late-bound production adapter retained for ``consumer_cli`` compatibility."""

    from server.app.modules.collector.consumer_pipeline import (
        process_claimed_transfer as process,
    )

    return process(claim, lease)
