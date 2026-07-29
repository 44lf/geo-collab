from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from datetime import datetime, timedelta

import pytest

from server.app.modules.collector.consumer_cli import (
    ConsumerStartupError,
    build_consumer_command,
    build_shutdown_handler,
    load_consumer_settings,
    main,
)
from server.app.modules.collector.consumer_runtime import (
    BacklogMetrics,
    ConsumerConfig,
    ConsumerRuntime,
    ProcessResult,
    TransferClaim,
)

NOW = datetime(2026, 7, 29, 8, 0)


def _claim(identity: int) -> TransferClaim:
    return TransferClaim(
        transfer_id=identity,
        collector_id="collector-local-1",
        transport_id=f"transport-{identity}",
        job_id="job-1",
        run_id="run-1",
        bundle_id=f"bundle-{identity}",
        destination="geo-production",
        object_key=f"incoming/collector-local-1/transport-{identity}.tar.zst",
        attempt_count=1,
        worker_id="consumer-1",
    )


class FakeQueue:
    def __init__(
        self,
        claims: list[TransferClaim],
        *,
        backlog: BacklogMetrics | None = None,
    ) -> None:
        self.claims = claims
        self.backlog = backlog or BacklogMetrics()
        self.claim_calls: list[tuple[str, int, timedelta, datetime]] = []
        self.renewed: list[int] = []
        self.released: list[tuple[int, str, str]] = []

    def claim(
        self,
        *,
        worker_id: str,
        limit: int,
        lease_duration: timedelta,
        now: datetime,
    ) -> list[TransferClaim]:
        self.claim_calls.append((worker_id, limit, lease_duration, now))
        return self.claims[:limit]

    def renew(
        self,
        claim: TransferClaim,
        *,
        worker_id: str,
        lease_duration: timedelta,
        now: datetime,
    ) -> None:
        self.renewed.append(claim.transfer_id)

    def release(
        self,
        claim: TransferClaim,
        *,
        worker_id: str,
        classification: str,
        error_summary: str,
    ) -> None:
        self.released.append((claim.transfer_id, classification, error_summary))

    def inspect_backlog(self, *, now: datetime) -> BacklogMetrics:
        return self.backlog


def _runtime(
    queue: FakeQueue,
    processor,
    *,
    event_sink=None,
) -> ConsumerRuntime:
    return ConsumerRuntime(
        config=ConsumerConfig(
            worker_id="consumer-a",
            batch_size=2,
            poll_interval=timedelta(seconds=3),
            lease_duration=timedelta(minutes=5),
        ),
        queue=queue,
        processor=processor,
        clock=lambda: NOW,
        event_sink=event_sink,
    )


def test_cycle_claims_oldest_first_with_bound_and_injected_lease_operations():
    queue = FakeQueue(
        [_claim(1), _claim(2), _claim(3)],
        backlog=BacklogMetrics(ready=7, retry_wait=2, processing=1, expired_leases=1),
    )
    processed: list[int] = []
    events: list[Mapping[str, object]] = []

    def processor(claim, lease):
        processed.append(claim.transfer_id)
        lease.renew()
        if claim.transfer_id == 2:
            return ProcessResult.retry(
                classification="destination_unavailable",
                error_summary="database unavailable",
            )
        return ProcessResult.processed()

    report = _runtime(queue, processor, event_sink=events.append).run_cycle()

    assert processed == [1, 2]
    assert queue.claim_calls == [
        ("consumer-a", 2, timedelta(minutes=5), NOW),
    ]
    assert queue.renewed == [1, 2]
    assert queue.released == [
        (2, "destination_unavailable", "database unavailable"),
    ]
    assert report.claimed == 2
    assert report.processed == 1
    assert report.retry_scheduled == 1
    assert report.backlog.pending == 10
    correlation_event = next(event for event in events if event["event"] == "transfer_processed")
    assert correlation_event["collector_id"] == "collector-local-1"
    assert correlation_event["transport_id"] == "transport-1"
    assert correlation_event["run_id"] == "run-1"
    assert correlation_event["bundle_id"] == "bundle-1"
    assert correlation_event["source"] is None
    assert correlation_event["stage"] == "consumer"
    assert str(correlation_event["occurred_at"]).endswith("Z")


def test_shutdown_finishes_current_transfer_and_releases_unstarted_claims():
    queue = FakeQueue([_claim(1), _claim(2)])
    stop_event = threading.Event()
    processed: list[int] = []

    def processor(claim, lease):
        processed.append(claim.transfer_id)
        stop_event.set()
        return ProcessResult.processed()

    report = _runtime(queue, processor).run_cycle(stop_event=stop_event)

    assert processed == [1]
    assert report.released_for_shutdown == 1
    assert queue.released == [
        (2, "consumer_shutdown", "consumer stopped before processing claimed transfer"),
    ]
    assert report.shutdown_requested is True


def test_unhandled_processor_error_releases_retry_without_logging_secret_message():
    queue = FakeQueue([_claim(1)])
    events: list[Mapping[str, object]] = []

    def processor(claim, lease):
        raise RuntimeError("password=hunter2")

    report = _runtime(queue, processor, event_sink=events.append).run_cycle()

    assert report.errors == 1
    assert queue.released == [
        (1, "consumer_unhandled_error", "RuntimeError"),
    ]
    serialized = json.dumps(events)
    assert "hunter2" not in serialized
    assert "RuntimeError" in serialized


def test_processor_persisted_retry_is_not_released_twice():
    queue = FakeQueue([_claim(1)])
    runtime = _runtime(
        queue,
        lambda claim, lease: ProcessResult.retry_persisted(
            classification="business_minio_unavailable"
        ),
    )

    report = runtime.run_cycle()

    assert report.retry_scheduled == 1
    assert queue.released == []


def test_health_snapshot_exposes_readiness_and_backlog_metrics():
    queue = FakeQueue(
        [],
        backlog=BacklogMetrics(
            ready=4,
            retry_wait=3,
            processing=2,
            expired_leases=1,
            dead_letter=5,
            oldest_ready_age_seconds=91.5,
        ),
    )
    runtime = _runtime(queue, lambda claim, lease: ProcessResult.processed())

    report = runtime.run_cycle()
    snapshot = runtime.health_snapshot()

    assert report.claimed == 0
    assert snapshot.healthy is True
    assert snapshot.ready is True
    assert snapshot.backlog.pending == 8
    assert snapshot.backlog.dead_letter == 5
    assert snapshot.backlog.oldest_ready_age_seconds == 91.5
    assert snapshot.last_poll_at == NOW
    assert snapshot.last_success_at == NOW


def test_forever_loop_polls_once_then_stops_and_becomes_unready():
    queue = FakeQueue([])
    stop_event = threading.Event()
    events: list[Mapping[str, object]] = []

    def sink(event):
        events.append(event)
        if event["event"] == "poll_cycle_completed":
            stop_event.set()

    runtime = _runtime(
        queue,
        lambda claim, lease: ProcessResult.processed(),
        event_sink=sink,
    )

    runtime.run_forever(stop_event=stop_event)

    assert len(queue.claim_calls) == 1
    assert [event["event"] for event in events] == [
        "consumer_started",
        "poll_cycle_completed",
        "consumer_stopped",
    ]
    assert runtime.health_snapshot().healthy is False
    assert runtime.health_snapshot().ready is False


def test_invalid_claim_count_is_reported_and_runtime_stays_alive():
    class BrokenQueue(FakeQueue):
        def claim(self, **kwargs):
            self.claim_calls.append(
                (
                    kwargs["worker_id"],
                    kwargs["limit"],
                    kwargs["lease_duration"],
                    kwargs["now"],
                )
            )
            return self.claims

    queue = BrokenQueue([_claim(1), _claim(2), _claim(3)])
    events: list[Mapping[str, object]] = []
    runtime = _runtime(
        queue,
        lambda claim, lease: ProcessResult.processed(),
        event_sink=events.append,
    )

    report = runtime.run_cycle()

    assert report.claimed == 0
    assert report.errors == 1
    assert runtime.health_snapshot().healthy is True
    assert runtime.health_snapshot().ready is False
    assert any(event["event"] == "claim_failed" for event in events)


def test_environment_and_container_command_are_pure_and_validated():
    settings = load_consumer_settings(
        {
            "GEO_COLLECTOR_CONSUMER_WORKER_ID": "consumer-container-1",
            "GEO_COLLECTOR_CONSUMER_BATCH_SIZE": "7",
            "GEO_COLLECTOR_CONSUMER_POLL_SECONDS": "2.5",
            "GEO_COLLECTOR_CONSUMER_LEASE_SECONDS": "120",
        },
        hostname="geo-consumer",
        pid=42,
    )

    assert settings.worker_id == "consumer-container-1"
    assert settings.batch_size == 7
    assert settings.poll_interval == timedelta(seconds=2.5)
    assert settings.lease_duration == timedelta(seconds=120)
    assert build_consumer_command("once") == (
        "python",
        "-m",
        "server.app.modules.collector.consumer_cli",
        "once",
    )

    with pytest.raises(ValueError, match="BATCH_SIZE"):
        load_consumer_settings(
            {"GEO_COLLECTOR_CONSUMER_BATCH_SIZE": "0"},
            hostname="geo-consumer",
            pid=42,
        )


def test_signal_handler_is_pure_until_called_and_idempotently_requests_shutdown():
    stop_event = threading.Event()
    events: list[Mapping[str, object]] = []
    handler = build_shutdown_handler(
        stop_event,
        events.append,
        worker_id="consumer-a",
    )

    assert stop_event.is_set() is False
    handler(15, object())
    handler(15, object())

    assert stop_event.is_set() is True
    assert len(events) == 1
    assert events[0]["event"] == "shutdown_requested"
    assert events[0]["worker_id"] == "consumer-a"


def test_cli_check_prints_machine_readable_readiness(capsys):
    class FakeRuntime:
        def check_readiness(self):
            return {
                "healthy": True,
                "ready": True,
                "backlog": {"pending": 0},
            }

    exit_code = main(
        ["check"],
        environ={},
        runtime_factory=lambda settings, event_sink: FakeRuntime(),
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == {
        "backlog": {"pending": 0},
        "healthy": True,
        "ready": True,
    }


def test_cli_reports_missing_processing_adapter_as_startup_error(capsys):
    def fail_factory(settings, event_sink):
        raise ConsumerStartupError("consumer_processing.process_claimed_transfer is not available")

    exit_code = main(["once"], environ={}, runtime_factory=fail_factory)

    assert exit_code == 2
    assert "process_claimed_transfer is not available" in capsys.readouterr().err
