from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from server.app.modules.collector.models import CollectorNode
from server.app.modules.collector.telemetry import (
    CollectorEventInput,
    HeartbeatInput,
    TelemetryValidationError,
    ingest_event_batch,
    node_freshness,
    record_heartbeat,
    sanitize_event_payload,
)

NOW = datetime(2026, 7, 29, 8, 0)


def _node(**changes) -> CollectorNode:
    values = {
        "collector_id": "collector-local-1",
        "display_name": "Local collector",
        "destination": "geo-production",
        "status": "enabled",
        "enabled_sources": ["baidu"],
    }
    values.update(changes)
    return CollectorNode(**values)


def _event(identity: str = "event-1", **changes) -> CollectorEventInput:
    values = {
        "event_id": identity,
        "run_id": "run-1",
        "job_id": "job-1",
        "transport_id": "transport-1",
        "bundle_id": "bundle-1",
        "source": "baidu",
        "component": "collector-agent",
        "component_version": "0.1.0",
        "stage": "upload",
        "event_type": "upload_retry",
        "level": "warning",
        "payload": {"attempt": 2},
        "occurred_at": datetime(2026, 7, 29, 7, 59, tzinfo=UTC),
    }
    values.update(changes)
    return CollectorEventInput(**values)


def test_heartbeat_updates_runtime_state_without_overwriting_server_source_scope():
    node = _node(enabled_sources=["baidu", "taptap"])
    db = MagicMock()
    db.scalar.return_value = node

    updated = record_heartbeat(
        db,
        collector_id="collector-local-1",
        destination="geo-production",
        heartbeat=HeartbeatInput(
            platform="Windows",
            agent_version="0.1.0",
            enabled_sources=("baidu", "taptap"),
            current_run_id="run-1",
            current_stage="upload",
            spool_pending_count=3,
        ),
        received_at=NOW,
    )

    assert updated is node
    assert node.last_heartbeat_at == NOW
    assert node.current_run_id == "run-1"
    assert node.current_stage == "upload"
    assert node.spool_pending_count == 3
    assert node.enabled_sources == ["baidu", "taptap"]
    db.flush.assert_called_once()


def test_heartbeat_cannot_expand_server_source_scope():
    node = _node(enabled_sources=["baidu"])
    db = MagicMock()
    db.scalar.return_value = node

    with pytest.raises(TelemetryValidationError, match="enabled_sources"):
        record_heartbeat(
            db,
            collector_id="collector-local-1",
            destination="geo-production",
            heartbeat=HeartbeatInput(
                platform="Windows",
                agent_version="0.1.0",
                enabled_sources=("baidu", "taptap"),
                current_run_id="run-1",
                current_stage="collect",
                spool_pending_count=0,
            ),
            received_at=NOW,
        )

    assert node.enabled_sources == ["baidu"]
    assert node.last_heartbeat_at is None
    db.flush.assert_not_called()


def test_cross_destination_heartbeat_is_rejected_without_mutation():
    node = _node()
    db = MagicMock()
    db.scalar.return_value = node

    with pytest.raises(TelemetryValidationError, match="outside collector scope"):
        record_heartbeat(
            db,
            collector_id="collector-local-1",
            destination="geo-dev",
            heartbeat=HeartbeatInput(
                platform="Windows",
                agent_version="0.1.0",
                enabled_sources=("baidu",),
                current_run_id=None,
                current_stage="idle",
                spool_pending_count=0,
            ),
            received_at=NOW,
        )
    assert node.last_heartbeat_at is None
    db.flush.assert_not_called()


def test_event_batch_is_bounded_deduplicated_and_uses_server_receive_time():
    db = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = ["event-1"]
    db.scalars.return_value = scalars

    result = ingest_event_batch(
        db,
        collector_id="collector-local-1",
        events=[_event("event-1"), _event("event-2"), _event("event-2")],
        received_at=NOW,
        max_events=100,
        max_payload_bytes=4096,
    )

    assert result.accepted_event_ids == ("event-2",)
    assert result.duplicate_event_ids == ("event-1", "event-2")
    added = db.add.call_args.args[0]
    assert added.event_id == "event-2"
    assert added.received_at == NOW
    assert added.occurred_at == datetime(2026, 7, 29, 7, 59)
    db.flush.assert_called_once()


def test_event_payload_removes_credentials_presigned_queries_and_raw_bodies():
    secret = "collector-super-secret"
    sanitized = sanitize_event_payload(
        {
            "authorization": f"Bearer credential:{secret}",
            "cookie": f"session={secret}",
            "xsrf_token": secret,
            "upload_url": f"https://minio.invalid/object?X-Amz-Signature={secret}",
            "request_url": f"https://source.invalid/path?token={secret}&page=1",
            "response_body": f'{{"secret":"{secret}"}}',
            "nested": {
                "safe": "kept",
                "message": f"Authorization: Bearer {secret}",
            },
        }
    )
    rendered = repr(sanitized)

    assert secret not in rendered
    assert "authorization" not in sanitized
    assert "cookie" not in sanitized
    assert "xsrf_token" not in sanitized
    assert "upload_url" not in sanitized
    assert "response_body" not in sanitized
    assert sanitized["request_url"] == "https://source.invalid/path"
    assert sanitized["nested"]["safe"] == "kept"
    assert "[REDACTED]" in sanitized["nested"]["message"]


def test_event_batch_rejects_oversize_and_invalid_source_before_insert():
    db = MagicMock()
    with pytest.raises(TelemetryValidationError, match="batch limit"):
        ingest_event_batch(
            db,
            collector_id="collector-local-1",
            events=[_event(str(index)) for index in range(2)],
            received_at=NOW,
            max_events=1,
            max_payload_bytes=4096,
        )
    with pytest.raises(TelemetryValidationError, match="payload"):
        ingest_event_batch(
            db,
            collector_id="collector-local-1",
            events=[_event(payload={"safe": "x" * 100})],
            received_at=NOW,
            max_events=100,
            max_payload_bytes=10,
        )
    with pytest.raises(TelemetryValidationError, match="source"):
        ingest_event_batch(
            db,
            collector_id="collector-local-1",
            events=[_event(source="unknown")],
            received_at=NOW,
            max_events=100,
            max_payload_bytes=4096,
        )
    db.add.assert_not_called()


def test_node_freshness_is_derived_without_fabricating_events():
    assert (
        node_freshness(
            _node(last_heartbeat_at=None),
            now=NOW,
            stale_after=timedelta(minutes=2),
        )
        == "never_seen"
    )
    assert (
        node_freshness(
            _node(last_heartbeat_at=NOW - timedelta(seconds=119)),
            now=NOW,
            stale_after=timedelta(minutes=2),
        )
        == "online"
    )
    assert (
        node_freshness(
            _node(last_heartbeat_at=NOW - timedelta(seconds=120)),
            now=NOW,
            stale_after=timedelta(minutes=2),
        )
        == "stale"
    )
