from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.app.db.session import get_db
from server.app.modules.collector.auth import CollectorPrincipal, require_collector_principal
from server.app.modules.collector.control_service import ConfigurationResult, JobClaimResult
from server.app.modules.collector.inbox import get_collector_inbox
from server.app.modules.collector.models import (
    CollectorConfigVersion,
    CollectorJob,
    CollectorRun,
    CollectorTransfer,
)
from server.app.modules.collector.router import collector_gateway_router
from server.app.modules.collector.run_service import RunCompletionResult
from server.app.modules.collector.transfer_service import (
    CreateTransferResult,
    InboxObjectMetadata,
)

NOW = datetime(2026, 7, 29, 5, 0)
SHA256 = "a" * 64


class _FakeInbox:
    def authorize_put(self, *, object_key, size_bytes, sha256, expires_in):
        return "https://inbox.invalid/signed-secret"

    def stat_object(self, *, object_key):
        return InboxObjectMetadata(
            object_key=object_key,
            size_bytes=1024,
            sha256=SHA256,
        )


def _transfer(**changes):
    values = {
        "id": 1,
        "collector_id": "collector-local-1",
        "transport_id": "transport-1",
        "job_id": "job-1",
        "run_id": "run-1",
        "bundle_id": "bundle-1",
        "destination": "geo-production",
        "object_key": "incoming/collector-local-1/transport-1.tar.zst",
        "archive_size": 1024,
        "archive_sha256": SHA256,
        "bundle_schema_version": "2",
        "status": "created",
        "attempt_count": 0,
    }
    values.update(changes)
    return CollectorTransfer(**values)


def _client(monkeypatch, *, principal=None):
    app = FastAPI()
    app.include_router(collector_gateway_router, prefix="/api/collector")
    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_collector_principal] = lambda: (
        principal
        or CollectorPrincipal(
            collector_id="collector-local-1",
            credential_id="credential-1",
            destination="geo-production",
            enabled_sources=("baidu", "ninegame", "yingyongbao", "taptap"),
        )
    )
    app.dependency_overrides[get_collector_inbox] = _FakeInbox
    return TestClient(app), db


def _payload(**changes):
    values = {
        "transport_id": "transport-1",
        "job_id": "job-1",
        "run_id": "run-1",
        "bundle_id": "bundle-1",
        "destination": "geo-production",
        "archive_size": 1024,
        "archive_sha256": SHA256,
        "bundle_schema_version": "2",
    }
    values.update(changes)
    return values


def test_transfer_create_api_returns_ephemeral_upload_without_persisting_url(monkeypatch):
    transfer = _transfer()
    monkeypatch.setattr(
        "server.app.modules.collector.router.create_or_get_transfer",
        lambda *args, **kwargs: CreateTransferResult(transfer=transfer, created=True),
    )
    client, _db = _client(monkeypatch)

    response = client.post("/api/collector/transfers", json=_payload())

    assert response.status_code == 201
    body = response.json()
    assert body["created"] is True
    assert body["state"] == "created"
    assert body["upload"]["object_key"] == transfer.object_key
    assert body["upload"]["required_sha256"] == SHA256
    assert body["upload"]["upload_url"].startswith("https://inbox.invalid/")
    assert "upload_url" not in set(CollectorTransfer.__table__.columns.keys())


def test_transfer_api_rejects_cross_destination_before_service(monkeypatch):
    create = MagicMock()
    monkeypatch.setattr("server.app.modules.collector.router.create_or_get_transfer", create)
    client, _db = _client(monkeypatch)

    response = client.post(
        "/api/collector/transfers",
        json=_payload(destination="geo-dev"),
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "resource is outside collector scope"}
    create.assert_not_called()


def test_completion_api_verifies_inbox_and_is_idempotent(monkeypatch):
    transfer = _transfer()
    find = MagicMock(return_value=transfer)
    monkeypatch.setattr("server.app.modules.collector.router.find_owned_transfer", find)
    client, _db = _client(monkeypatch)

    first = client.post("/api/collector/transfers/transport-1/complete")
    second = client.post("/api/collector/transfers/transport-1/complete")

    assert first.status_code == 200
    assert first.json()["state"] == "ready"
    assert second.status_code == 200
    assert second.json()["state"] == "ready"


def test_status_api_is_collector_scoped_and_never_returns_upload_url(monkeypatch):
    status = SimpleTransferStatus()
    monkeypatch.setattr(
        "server.app.modules.collector.router.get_transfer_status",
        lambda *args, **kwargs: status,
    )
    client, _db = _client(monkeypatch)

    response = client.get("/api/collector/transfers/transport-1")

    assert response.status_code == 200
    assert response.json()["state"] == "processed"
    assert response.json()["archive_sha256"] == SHA256
    assert "upload_url" not in response.text


def test_heartbeat_and_event_endpoints_use_authenticated_collector_scope(monkeypatch):
    from server.app.modules.collector.models import CollectorNode

    client, db = _client(monkeypatch)
    node = CollectorNode(
        collector_id="collector-local-1",
        display_name="Local collector",
        destination="geo-production",
        status="enabled",
        enabled_sources=["baidu"],
    )
    db.scalar.return_value = node
    db.scalars.return_value.all.return_value = []

    heartbeat = client.post(
        "/api/collector/heartbeat",
        json={
            "platform": "Windows",
            "agent_version": "0.1.0",
            "enabled_sources": ["baidu", "taptap"],
            "current_run_id": "run-1",
            "current_stage": "upload",
            "spool_pending_count": 2,
        },
    )
    event = client.post(
        "/api/collector/events",
        json={
            "events": [
                {
                    "event_id": "event-1",
                    "run_id": "run-1",
                    "job_id": "job-1",
                    "transport_id": "transport-1",
                    "bundle_id": "bundle-1",
                    "source": "baidu",
                    "component": "collector-agent",
                    "component_version": "0.1.0",
                    "stage": "upload",
                    "event_type": "upload_started",
                    "level": "info",
                    "payload": {"safe": True},
                    "occurred_at": "2026-07-29T08:00:00Z",
                }
            ]
        },
    )

    assert heartbeat.status_code == 200
    assert heartbeat.json()["collector_id"] == "collector-local-1"
    assert node.destination == "geo-production"
    assert event.status_code == 200
    assert event.json()["accepted_event_ids"] == ["event-1"]


def test_configuration_and_job_endpoints_preserve_versions_and_immutable_hash(
    monkeypatch,
):
    config = CollectorConfigVersion(
        id=7,
        collector_id="collector-local-1",
        version_no=3,
        snapshot={"schema_version": 1, "refresh": {"enabled": True}},
        snapshot_sha256="e" * 64,
        max_staleness_seconds=3600,
    )
    job = CollectorJob(
        collector_id="collector-local-1",
        job_id="job-1",
        claim_request_id="claim-1",
        config_version_id=7,
        mode="refresh",
        destination="geo-production",
        manifest={"schema_version": 1, "job_id": "job-1"},
        manifest_sha256="f" * 64,
        status="claimed",
    )
    monkeypatch.setattr(
        "server.app.modules.collector.router.read_configuration",
        lambda *args, **kwargs: ConfigurationResult(unchanged=False, config=config),
    )
    monkeypatch.setattr(
        "server.app.modules.collector.router.claim_job",
        lambda *args, **kwargs: JobClaimResult(job=job, created=True),
    )

    def acknowledge(*args, **kwargs):
        job.status = "acknowledged"
        return job

    monkeypatch.setattr(
        "server.app.modules.collector.router.acknowledge_job",
        acknowledge,
    )
    client, _db = _client(monkeypatch)

    configuration = client.get("/api/collector/configuration?current_version=2")
    claimed = client.post(
        "/api/collector/jobs/claim",
        json={"claim_request_id": "claim-1", "mode": "refresh"},
    )
    acked = client.post(
        "/api/collector/jobs/job-1/ack",
        json={"manifest_sha256": "f" * 64},
    )

    assert configuration.status_code == 200
    assert configuration.json()["version"] == 3
    assert configuration.json()["snapshot_sha256"] == "e" * 64
    assert claimed.status_code == 200
    assert claimed.json()["job"]["claim_request_id"] == "claim-1"
    assert claimed.json()["job"]["manifest_sha256"] == "f" * 64
    assert acked.status_code == 200
    assert acked.json()["status"] == "acknowledged"


def test_run_completion_api_uses_authenticated_scope(monkeypatch):
    run = CollectorRun(
        collector_id="collector-local-1",
        run_id="run-1",
        job_pk_id=1,
        job_id="job-1",
        status="succeeded",
        current_stage="completed",
        summary={"success": 2},
        started_at=NOW,
        finished_at=NOW,
    )
    complete = MagicMock(return_value=RunCompletionResult(run=run, created=True))
    monkeypatch.setattr(
        "server.app.modules.collector.router.record_completed_run",
        complete,
    )
    client, db = _client(monkeypatch)

    response = client.post(
        "/api/collector/runs/complete",
        json={
            "run_id": "run-1",
            "job_id": "job-1",
            "status": "succeeded",
            "summary": {"success": 2},
            "started_at": "2026-07-29T05:00:00Z",
            "finished_at": "2026-07-29T05:00:00Z",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "created": True,
        "collector_id": "collector-local-1",
        "run_id": "run-1",
        "job_id": "job-1",
        "status": "succeeded",
    }
    assert complete.call_args.args[0] is db
    assert complete.call_args.kwargs["collector_id"] == "collector-local-1"
    assert complete.call_args.kwargs["destination"] == "geo-production"


class SimpleTransferStatus:
    collector_id = "collector-local-1"
    transport_id = "transport-1"
    object_key = "incoming/collector-local-1/transport-1.tar.zst"
    archive_sha256 = SHA256
    state = "processed"
    receipt_status = "processed"
    receipt_completed_at = NOW
    error_classification = None
    error_summary = None
