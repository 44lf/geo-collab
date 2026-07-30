from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import server.app.modules.accounts.models  # noqa: F401
import server.app.modules.game_library.models  # noqa: F401
import server.app.modules.image_library.models  # noqa: F401
from server.app.core.security import require_admin
from server.app.db.session import get_db
from server.app.modules.collector.management import (
    collector_management_router,
    get_backlog_view,
    get_run_view,
    get_transfer_view,
    list_node_views,
    list_run_views,
)

NOW = datetime(2026, 7, 29, 8, 0)


def _scalars(*rows):
    result = MagicMock()
    result.all.return_value = list(rows)
    return result


def test_node_views_are_bounded_and_sanitize_failure(monkeypatch) -> None:
    monkeypatch.setattr("server.app.modules.collector.management.utcnow", lambda: NOW)
    node = SimpleNamespace(
        id=1,
        collector_id="collector-1",
        display_name="local",
        destination="geo-dev",
        status="enabled",
        platform="Windows",
        agent_version="0.1.0",
        enabled_sources=["taptap"],
        current_run_id="run-1",
        current_stage="upload",
        spool_pending_count=2,
        last_heartbeat_at=NOW,
        last_success_at=None,
        last_error_at=NOW,
        last_error_summary=(
            "Authorization: Bearer top-secret "
            "mysql://user:password@db.internal/geo "
            "https://minio.test/inbox?X-Amz-Signature=top-secret"
        ),
    )
    extra = SimpleNamespace(**{**node.__dict__, "id": 2})
    db = MagicMock()
    db.scalars.return_value = _scalars(node, extra)

    result = list_node_views(
        db,
        cursor=None,
        limit=1,
        stale_after_seconds=300,
    )

    assert result["next_cursor"] == 1
    assert result["items"][0]["freshness"] == "online"
    encoded = str(result)
    assert "top-secret" not in encoded
    assert "user:password" not in encoded
    assert "X-Amz-Signature" not in encoded


def test_run_view_orders_correlated_events_and_resanitizes_payload() -> None:
    run = SimpleNamespace(
        collector_id="collector-1",
        run_id="run-1",
        job_id="job-1",
        source="taptap",
        status="failed",
        current_stage="collect",
        summary={"upload_url": "https://secret.test/?token=x"},
        error_classification="source_blocked",
        error_summary="https://source.test/path?token=secret",
        started_at=NOW,
        finished_at=NOW,
    )
    event = SimpleNamespace(
        event_id="event-1",
        transport_id=None,
        bundle_id=None,
        source="taptap",
        component="collector-agent",
        component_version="0.1.0",
        stage="collect",
        event_type="blocked",
        level="warning",
        payload={"Cookie": "secret", "status": 403},
        occurred_at=NOW,
        received_at=NOW,
    )
    db = MagicMock()
    db.scalar.return_value = run
    db.scalars.return_value = _scalars(event)

    result = get_run_view(db, run_id="run-1", event_limit=10)

    assert result["events"][0]["event_id"] == "event-1"
    assert result["events"][0]["payload"] == {"status": 403}
    assert result["run"]["summary"] == {}
    assert result["run"]["error_summary"] == "https://source.test/path"


def test_recent_run_list_is_bounded_and_secret_safe() -> None:
    run = SimpleNamespace(
        collector_id="collector-1",
        run_id="run-1",
        job_id="job-1",
        source=None,
        status="failed",
        summary={"authorization": "Bearer secret", "failed": 1},
        error_classification="source_failures",
        error_summary="https://source.test/path?token=secret",
        started_at=NOW,
        finished_at=NOW,
    )
    db = MagicMock()
    db.scalars.return_value = _scalars(run)

    result = list_run_views(db, limit=20)

    assert result["items"][0]["run_id"] == "run-1"
    assert result["items"][0]["summary"] == {"failed": 1}
    assert result["items"][0]["error_summary"] == "https://source.test/path"


def test_transfer_view_exposes_receipts_without_object_key_or_secrets() -> None:
    transfer = SimpleNamespace(
        id=9,
        collector_id="collector-1",
        transport_id="transport-1",
        job_id="job-1",
        run_id="run-1",
        bundle_id="bundle-1",
        destination="geo-dev",
        object_key="incoming/private.tar.zst",
        archive_size=12,
        archive_sha256="a" * 64,
        bundle_schema_version="2",
        status="dead_letter",
        attempt_count=3,
        error_classification="schema",
        error_summary="https://minio.test/object?secret=yes",
        ready_at=NOW,
        processed_at=None,
    )
    receipt = SimpleNamespace(
        status="dead_letter",
        item_count=1,
        completed_item_count=1,
        archive_sha256="a" * 64,
        error_classification="schema",
        error_summary="Authorization: Bearer secret",
        replay_evidence_ref="evidence/transport-1",
        completed_at=NOW,
    )
    item = SimpleNamespace(
        item_key="item-1",
        source="taptap",
        source_item_id="123",
        status="failed",
        game_id=None,
        outcome={"raw_body": "secret"},
        error_classification="identity",
        error_summary="bad identity",
        completed_at=NOW,
    )
    db = MagicMock()
    db.scalar.side_effect = [transfer, receipt]
    db.scalars.return_value = _scalars(item)

    result = get_transfer_view(db, transport_id="transport-1")

    assert "object_key" not in result["transfer"]
    assert result["items"][0]["outcome"] == {}
    assert "secret" not in str(result)


def test_backlog_returns_zero_filled_counts() -> None:
    db = MagicMock()
    db.execute.return_value.all.return_value = [("ready", 3), ("processing", 2)]
    db.scalar.return_value = NOW

    result = get_backlog_view(db)

    assert result == {
        "counts": {
            "ready": 3,
            "processing": 2,
            "retry_wait": 0,
            "failed": 0,
            "dead_letter": 0,
        },
        "oldest_ready_at": NOW,
    }


def _client(*, admin: bool) -> TestClient:
    app = FastAPI()
    app.include_router(collector_management_router, prefix="/api/collector-management")
    app.dependency_overrides[get_db] = lambda: MagicMock()
    if admin:
        app.dependency_overrides[require_admin] = lambda: SimpleNamespace(
            id=1,
            role="admin",
        )
    else:

        def forbidden():
            raise HTTPException(status_code=403, detail="需要管理员权限")

        app.dependency_overrides[require_admin] = forbidden
    return TestClient(app)


def test_management_routes_are_admin_only_and_read_only(monkeypatch) -> None:
    denied = _client(admin=False)
    assert denied.get("/api/collector-management/backlog").status_code == 403

    monkeypatch.setattr(
        "server.app.modules.collector.management.get_backlog_view",
        lambda _db: {"counts": {}, "oldest_ready_at": None},
    )
    allowed = _client(admin=True)
    assert allowed.get("/api/collector-management/backlog").status_code == 200
    paths = {
        route.path: set(route.methods or ())
        for route in allowed.app.routes
        if route.path.startswith("/api/collector-management")
    }
    assert all(methods == {"GET"} for methods in paths.values())
