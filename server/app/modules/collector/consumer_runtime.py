"""Standalone Collector Consumer runtime with bounded lease-based polling.

The runtime owns queue coordination, lifecycle, and process-level telemetry.  Bundle
validation/import remains behind the injected ``ConsumerProcessor`` boundary so this
module can run and be tested independently of a particular processing implementation.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.app.core.time import utcnow
from server.app.modules.collector.claim_repository import (
    claim_ready_transfers,
    release_transfer_for_retry,
    renew_transfer_lease,
)
from server.app.modules.collector.models import CollectorTransfer
from server.app.modules.collector.structured_logging import collector_log_record
from server.app.modules.collector.telemetry import sanitize_event_payload

CONSUMER_COMPONENT = "collector-consumer"
CONSUMER_COMPONENT_VERSION = "1"

EventSink = Callable[[Mapping[str, object]], None]
Clock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class ConsumerConfig:
    """Validated knobs for one Consumer process."""

    worker_id: str
    batch_size: int = 10
    poll_interval: timedelta = timedelta(seconds=5)
    lease_duration: timedelta = timedelta(minutes=5)

    def __post_init__(self) -> None:
        if (
            not self.worker_id
            or self.worker_id != self.worker_id.strip()
            or len(self.worker_id) > 128
        ):
            raise ValueError("worker_id must be non-empty trimmed text of at most 128 characters")
        if not 1 <= self.batch_size <= 100:
            raise ValueError("batch_size must be between 1 and 100")
        if not timedelta(0) <= self.poll_interval <= timedelta(hours=1):
            raise ValueError("poll_interval must be between 0 seconds and 1 hour")
        if not timedelta(seconds=1) <= self.lease_duration <= timedelta(hours=1):
            raise ValueError("lease_duration must be between 1 second and 1 hour")


@dataclass(frozen=True, slots=True)
class TransferClaim:
    """Detached transfer identity passed across short database transactions."""

    transfer_id: int
    collector_id: str
    transport_id: str
    job_id: str
    run_id: str
    bundle_id: str
    destination: str
    object_key: str
    attempt_count: int
    worker_id: str
    source: str | None = None

    @classmethod
    def from_model(cls, transfer: CollectorTransfer) -> TransferClaim:
        if not transfer.claimed_by:
            raise ValueError("claimed transfer is missing its lease owner")
        return cls(
            transfer_id=transfer.id,
            collector_id=transfer.collector_id,
            transport_id=transfer.transport_id,
            job_id=transfer.job_id,
            run_id=transfer.run_id,
            bundle_id=transfer.bundle_id,
            destination=transfer.destination,
            object_key=transfer.object_key,
            attempt_count=transfer.attempt_count,
            worker_id=transfer.claimed_by,
            source=None,
        )


@dataclass(frozen=True, slots=True)
class BacklogMetrics:
    """Queue pressure indicators exposed through health/readiness snapshots."""

    ready: int = 0
    retry_wait: int = 0
    processing: int = 0
    expired_leases: int = 0
    dead_letter: int = 0
    oldest_ready_age_seconds: float | None = None

    @property
    def pending(self) -> int:
        return self.ready + self.retry_wait + self.expired_leases

    def to_dict(self) -> dict[str, int | float | None]:
        values = asdict(self)
        values["pending"] = self.pending
        return values


@dataclass(frozen=True, slots=True)
class ProcessResult:
    """Outcome returned by the injected processing implementation."""

    outcome: Literal["processed", "retry", "retry_persisted", "terminal"]
    classification: str | None = None
    error_summary: str | None = None

    @classmethod
    def processed(cls) -> ProcessResult:
        return cls(outcome="processed")

    @classmethod
    def retry(cls, *, classification: str, error_summary: str) -> ProcessResult:
        if not classification or classification != classification.strip():
            raise ValueError("classification must be non-empty trimmed text")
        return cls(
            outcome="retry",
            classification=classification,
            error_summary=error_summary,
        )

    @classmethod
    def retry_persisted(cls, *, classification: str) -> ProcessResult:
        """The processor already released the transfer to durable ready state."""

        if not classification or classification != classification.strip():
            raise ValueError("classification must be non-empty trimmed text")
        return cls(outcome="retry_persisted", classification=classification)

    @classmethod
    def terminal(cls) -> ProcessResult:
        """The processor already persisted a failed/dead-letter terminal result."""

        return cls(outcome="terminal")


class ConsumerQueue(Protocol):
    """Injectable durable queue and lease boundary."""

    def claim(
        self,
        *,
        worker_id: str,
        limit: int,
        lease_duration: timedelta,
        now: datetime,
    ) -> Sequence[TransferClaim]: ...

    def renew(
        self,
        claim: TransferClaim,
        *,
        worker_id: str,
        lease_duration: timedelta,
        now: datetime,
    ) -> None: ...

    def release(
        self,
        claim: TransferClaim,
        *,
        worker_id: str,
        classification: str,
        error_summary: str,
    ) -> None: ...

    def inspect_backlog(self, *, now: datetime) -> BacklogMetrics: ...


class ConsumerProcessor(Protocol):
    def __call__(self, claim: TransferClaim, lease: LeaseHandle) -> ProcessResult: ...


@dataclass(slots=True)
class LeaseHandle:
    """Per-transfer lease controls made available to long-running processors."""

    queue: ConsumerQueue
    claim: TransferClaim
    config: ConsumerConfig
    clock: Clock
    released: bool = False

    def renew(self) -> None:
        if self.released:
            raise RuntimeError("cannot renew a released lease")
        self.queue.renew(
            self.claim,
            worker_id=self.config.worker_id,
            lease_duration=self.config.lease_duration,
            now=self.clock(),
        )

    def release_for_retry(self, *, classification: str, error_summary: str) -> None:
        if self.released:
            return
        self.queue.release(
            self.claim,
            worker_id=self.config.worker_id,
            classification=classification,
            error_summary=error_summary,
        )
        self.released = True


@dataclass(frozen=True, slots=True)
class CycleReport:
    claimed: int = 0
    processed: int = 0
    terminal: int = 0
    retry_scheduled: int = 0
    released_for_shutdown: int = 0
    errors: int = 0
    shutdown_requested: bool = False
    backlog: BacklogMetrics = field(default_factory=BacklogMetrics)


@dataclass(frozen=True, slots=True)
class HealthSnapshot:
    healthy: bool
    ready: bool
    shutdown_requested: bool
    polls: int
    claimed_total: int
    processed_total: int
    retry_total: int
    error_total: int
    last_poll_at: datetime | None
    last_success_at: datetime | None
    last_error_at: datetime | None
    backlog: BacklogMetrics

    def to_dict(self) -> dict[str, object]:
        return {
            "healthy": self.healthy,
            "ready": self.ready,
            "shutdown_requested": self.shutdown_requested,
            "polls": self.polls,
            "claimed_total": self.claimed_total,
            "processed_total": self.processed_total,
            "retry_total": self.retry_total,
            "error_total": self.error_total,
            "last_poll_at": _isoformat(self.last_poll_at),
            "last_success_at": _isoformat(self.last_success_at),
            "last_error_at": _isoformat(self.last_error_at),
            "backlog": self.backlog.to_dict(),
        }


class _RuntimeState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._started = False
        self._stopped = False
        self._ready = False
        self._shutdown_requested = False
        self._polls = 0
        self._claimed_total = 0
        self._processed_total = 0
        self._retry_total = 0
        self._error_total = 0
        self._last_poll_at: datetime | None = None
        self._last_success_at: datetime | None = None
        self._last_error_at: datetime | None = None
        self._backlog = BacklogMetrics()

    def start(self) -> None:
        with self._lock:
            self._started = True
            self._stopped = False

    def record_cycle(self, *, now: datetime, report: CycleReport, queue_ready: bool) -> None:
        with self._lock:
            self._started = True
            self._polls += 1
            self._claimed_total += report.claimed
            self._processed_total += report.processed + report.terminal
            self._retry_total += report.retry_scheduled + report.released_for_shutdown
            self._error_total += report.errors
            self._last_poll_at = now
            self._backlog = report.backlog
            self._ready = queue_ready and not report.shutdown_requested
            self._shutdown_requested = report.shutdown_requested
            if queue_ready:
                self._last_success_at = now
            if report.errors:
                self._last_error_at = now

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            self._ready = False
            self._shutdown_requested = True

    def snapshot(self) -> HealthSnapshot:
        with self._lock:
            return HealthSnapshot(
                healthy=self._started and not self._stopped,
                ready=self._ready,
                shutdown_requested=self._shutdown_requested,
                polls=self._polls,
                claimed_total=self._claimed_total,
                processed_total=self._processed_total,
                retry_total=self._retry_total,
                error_total=self._error_total,
                last_poll_at=self._last_poll_at,
                last_success_at=self._last_success_at,
                last_error_at=self._last_error_at,
                backlog=self._backlog,
            )


class ConsumerRuntime:
    """Bounded polling loop that remains independent from bundle processing."""

    def __init__(
        self,
        *,
        config: ConsumerConfig,
        queue: ConsumerQueue,
        processor: ConsumerProcessor,
        clock: Clock = utcnow,
        event_sink: EventSink | None = None,
    ) -> None:
        self.config = config
        self.queue = queue
        self.processor = processor
        self.clock = clock
        self.event_sink = event_sink or (lambda event: None)
        self._state = _RuntimeState()

    def run_cycle(self, *, stop_event: threading.Event | None = None) -> CycleReport:
        stop = stop_event or threading.Event()
        now = self.clock()
        self._state.start()
        if stop.is_set():
            report = CycleReport(shutdown_requested=True)
            self._state.record_cycle(now=now, report=report, queue_ready=False)
            return report

        try:
            claims = tuple(
                self.queue.claim(
                    worker_id=self.config.worker_id,
                    limit=self.config.batch_size,
                    lease_duration=self.config.lease_duration,
                    now=now,
                )
            )
            if len(claims) > self.config.batch_size:
                raise RuntimeError("queue adapter returned more claims than the configured bound")
        except Exception as exc:  # noqa: BLE001 - one bad poll must not kill the process
            self._emit("claim_failed", level="error", error_type=type(exc).__name__)
            report = CycleReport(errors=1)
            self._state.record_cycle(now=now, report=report, queue_ready=False)
            return report

        processed = 0
        terminal = 0
        retry_scheduled = 0
        released_for_shutdown = 0
        errors = 0
        for index, claim in enumerate(claims):
            if stop.is_set():
                for unstarted in claims[index:]:
                    try:
                        self.queue.release(
                            unstarted,
                            worker_id=self.config.worker_id,
                            classification="consumer_shutdown",
                            error_summary="consumer stopped before processing claimed transfer",
                        )
                        released_for_shutdown += 1
                        self._emit("transfer_released_for_shutdown", claim=unstarted)
                    except Exception as exc:  # noqa: BLE001 - lease expiry remains the fallback
                        errors += 1
                        self._emit(
                            "lease_release_failed",
                            level="error",
                            claim=unstarted,
                            error_type=type(exc).__name__,
                        )
                break

            lease = LeaseHandle(
                queue=self.queue,
                claim=claim,
                config=self.config,
                clock=self.clock,
            )
            self._emit("transfer_processing_started", claim=claim)
            try:
                outcome = self.processor(claim, lease)
                if outcome.outcome == "processed":
                    processed += 1
                    self._emit("transfer_processed", claim=claim)
                elif outcome.outcome == "terminal":
                    terminal += 1
                    self._emit("transfer_terminal", level="warning", claim=claim)
                elif outcome.outcome == "retry":
                    lease.release_for_retry(
                        classification=outcome.classification or "consumer_retry",
                        error_summary=outcome.error_summary or "processor requested retry",
                    )
                    retry_scheduled += 1
                    self._emit(
                        "transfer_retry_scheduled",
                        level="warning",
                        claim=claim,
                        classification=outcome.classification or "consumer_retry",
                    )
                else:
                    retry_scheduled += 1
                    lease.released = True
                    self._emit(
                        "transfer_retry_persisted",
                        level="warning",
                        claim=claim,
                        classification=outcome.classification or "consumer_retry",
                    )
            except Exception as exc:  # noqa: BLE001 - preserve backlog and continue the batch
                errors += 1
                try:
                    lease.release_for_retry(
                        classification="consumer_unhandled_error",
                        error_summary=type(exc).__name__,
                    )
                    retry_scheduled += 1
                except Exception as release_exc:  # noqa: BLE001 - expiry permits later reclaim
                    self._emit(
                        "lease_release_failed",
                        level="error",
                        claim=claim,
                        error_type=type(release_exc).__name__,
                    )
                self._emit(
                    "transfer_processing_failed",
                    level="error",
                    claim=claim,
                    error_type=type(exc).__name__,
                )

        queue_ready = True
        try:
            backlog = self.queue.inspect_backlog(now=self.clock())
        except Exception as exc:  # noqa: BLE001 - health reports this without killing the loop
            queue_ready = False
            backlog = BacklogMetrics()
            errors += 1
            self._emit("backlog_probe_failed", level="error", error_type=type(exc).__name__)

        report = CycleReport(
            claimed=len(claims),
            processed=processed,
            terminal=terminal,
            retry_scheduled=retry_scheduled,
            released_for_shutdown=released_for_shutdown,
            errors=errors,
            shutdown_requested=stop.is_set(),
            backlog=backlog,
        )
        self._state.record_cycle(now=now, report=report, queue_ready=queue_ready)
        self._emit(
            "poll_cycle_completed",
            claimed=report.claimed,
            processed=report.processed,
            terminal=report.terminal,
            retry_scheduled=report.retry_scheduled,
            errors=report.errors,
            backlog_pending=backlog.pending,
        )
        return report

    def run_forever(self, *, stop_event: threading.Event) -> None:
        self._state.start()
        self._emit("consumer_started")
        while not stop_event.is_set():
            self.run_cycle(stop_event=stop_event)
            if stop_event.wait(self.config.poll_interval.total_seconds()):
                break
        self._state.stop()
        self._emit("consumer_stopped")

    def check_readiness(self) -> dict[str, object]:
        """Run a non-claiming queue probe for container health checks."""

        now = self.clock()
        self._state.start()
        errors = 0
        queue_ready = True
        try:
            backlog = self.queue.inspect_backlog(now=now)
        except Exception as exc:  # noqa: BLE001 - readiness must return a result
            backlog = BacklogMetrics()
            errors = 1
            queue_ready = False
            self._emit("readiness_probe_failed", level="error", error_type=type(exc).__name__)
        report = CycleReport(errors=errors, backlog=backlog)
        self._state.record_cycle(now=now, report=report, queue_ready=queue_ready)
        return self.health_snapshot().to_dict()

    def health_snapshot(self) -> HealthSnapshot:
        return self._state.snapshot()

    def _emit(
        self,
        event: str,
        *,
        level: str = "info",
        claim: TransferClaim | None = None,
        **fields: object,
    ) -> None:
        occurred_at = self.clock()
        technical_time = (
            occurred_at.replace(tzinfo=UTC)
            if occurred_at.tzinfo is None
            else occurred_at.astimezone(UTC)
        )
        technical = collector_log_record(
            component=CONSUMER_COMPONENT,
            component_version=CONSUMER_COMPONENT_VERSION,
            stage="consumer",
            event_type=event,
            level=level,
            message=event,
            collector_id=claim.collector_id if claim is not None else None,
            run_id=claim.run_id if claim is not None else None,
            job_id=claim.job_id if claim is not None else None,
            transport_id=claim.transport_id if claim is not None else None,
            bundle_id=claim.bundle_id if claim is not None else None,
            source=claim.source if claim is not None else None,
            fields={"worker_id": self.config.worker_id, **fields},
            occurred_at=technical_time,
        )
        payload: dict[str, object] = {
            **technical,
            "occurred_at": _isoformat(occurred_at),
            "level": level,
            "event": event,
            "stage": "consumer",
            "component": CONSUMER_COMPONENT,
            "component_version": CONSUMER_COMPONENT_VERSION,
            "worker_id": self.config.worker_id,
        }
        if claim is not None:
            payload.update(
                {
                    "collector_id": claim.collector_id,
                    "job_id": claim.job_id,
                    "run_id": claim.run_id,
                    "transport_id": claim.transport_id,
                    "bundle_id": claim.bundle_id,
                    "source": claim.source,
                }
            )
        payload.update(sanitize_event_payload(dict(fields)) or {})
        self.event_sink(payload)


class SqlAlchemyConsumerQueue:
    """Production queue adapter; every coordination operation is a short session."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def claim(
        self,
        *,
        worker_id: str,
        limit: int,
        lease_duration: timedelta,
        now: datetime,
    ) -> Sequence[TransferClaim]:
        with self._session_factory() as db:
            rows = claim_ready_transfers(
                db,
                worker_id=worker_id,
                batch_size=limit,
                lease_duration=lease_duration,
                now=now,
            )
            claims = tuple(TransferClaim.from_model(row) for row in rows)
            db.commit()
            return claims

    def renew(
        self,
        claim: TransferClaim,
        *,
        worker_id: str,
        lease_duration: timedelta,
        now: datetime,
    ) -> None:
        with self._session_factory() as db:
            transfer = _require_transfer(db, claim.transfer_id)
            renew_transfer_lease(
                db,
                transfer,
                worker_id=worker_id,
                attempt_count=claim.attempt_count,
                lease_duration=lease_duration,
                now=now,
            )
            db.commit()

    def release(
        self,
        claim: TransferClaim,
        *,
        worker_id: str,
        classification: str,
        error_summary: str,
    ) -> None:
        with self._session_factory() as db:
            transfer = _require_transfer(db, claim.transfer_id)
            release_transfer_for_retry(
                db,
                transfer,
                worker_id=worker_id,
                attempt_count=claim.attempt_count,
                classification=classification,
                error_summary=error_summary,
            )
            db.commit()

    def inspect_backlog(self, *, now: datetime) -> BacklogMetrics:
        with self._session_factory() as db:
            count_rows = db.execute(
                select(CollectorTransfer.status, func.count(CollectorTransfer.id)).group_by(
                    CollectorTransfer.status
                )
            ).all()
            counts = {str(status): int(count) for status, count in count_rows}
            expired_leases = int(
                db.scalar(
                    select(func.count(CollectorTransfer.id)).where(
                        CollectorTransfer.status == "processing",
                        CollectorTransfer.lease_until <= now,
                    )
                )
                or 0
            )
            oldest_ready_at = db.scalar(
                select(func.min(CollectorTransfer.ready_at)).where(
                    CollectorTransfer.status == "ready"
                )
            )
        oldest_age = (
            max(0.0, (now - oldest_ready_at).total_seconds())
            if oldest_ready_at is not None
            else None
        )
        return BacklogMetrics(
            ready=counts.get("ready", 0),
            retry_wait=counts.get("retry_wait", 0),
            processing=counts.get("processing", 0),
            expired_leases=expired_leases,
            dead_letter=counts.get("dead_letter", 0),
            oldest_ready_age_seconds=oldest_age,
        )


def _require_transfer(db: Session, transfer_id: int) -> CollectorTransfer:
    transfer = db.get(CollectorTransfer, transfer_id)
    if transfer is None:
        raise LookupError(f"collector transfer {transfer_id} does not exist")
    return transfer


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    utc_value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return utc_value.isoformat(timespec="milliseconds").replace("+00:00", "Z")
