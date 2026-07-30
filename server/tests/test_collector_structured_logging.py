from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from server.app.modules.collector.structured_logging import collector_log_record

NOW = datetime(2026, 7, 29, 8, 0, tzinfo=UTC)


def test_collector_log_is_utc_correlated_and_otel_compatible():
    record = collector_log_record(
        component="collector-consumer",
        component_version="1",
        stage="import",
        event_type="item_failed",
        level="error",
        message="temporary destination error",
        collector_id="collector-1",
        run_id="run-1",
        job_id="job-1",
        transport_id="transport-1",
        bundle_id="bundle-1",
        source="taptap",
        error=TimeoutError("secret"),
        occurred_at=NOW,
    )

    assert record["timestamp"] == "2026-07-29T08:00:00.000Z"
    assert record["severity_text"] == "ERROR"
    assert record["attributes"]["collector.id"] == "collector-1"
    assert record["attributes"]["transport.id"] == "transport-1"
    assert len(record["attributes"]["error.fingerprint"]) == 24


def test_collector_log_removes_credentials_queries_and_raw_body():
    record = collector_log_record(
        component="collector-gateway",
        component_version="1",
        stage="upload",
        event_type="failed",
        level="warning",
        message=(
            "Authorization: Bearer secret "
            "https://minio.test/object?X-Amz-Signature=secret "
            "mysql://user:secret@db/geo"
        ),
        fields={
            "Cookie": "sid=secret",
            "xsrf_token": "secret",
            "raw_body": "<secret>",
            "safe_url": "https://source.test/game?id=secret",
        },
        occurred_at=NOW,
    )

    encoded = json.dumps(record)
    assert "Bearer secret" not in encoded
    assert "X-Amz-Signature" not in encoded
    assert "sid=secret" not in encoded
    assert "<secret>" not in encoded
    assert "mysql://user:secret@" not in encoded


def test_collector_log_rejects_naive_time_and_unknown_level():
    with pytest.raises(ValueError, match="UTC offset"):
        collector_log_record(
            component="collector-consumer",
            component_version="1",
            stage="import",
            event_type="failed",
            level="error",
            message="failed",
            occurred_at=datetime(2026, 7, 29),
        )
    with pytest.raises(ValueError, match="unsupported"):
        collector_log_record(
            component="collector-consumer",
            component_version="1",
            stage="import",
            event_type="failed",
            level="notice",
            message="failed",
            occurred_at=NOW,
        )
