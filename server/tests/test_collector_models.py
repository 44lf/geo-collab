from __future__ import annotations

from sqlalchemy import CheckConstraint, UniqueConstraint

from server.app.modules.collector.models import (
    CollectorConfigVersion,
    CollectorCredential,
    CollectorEvent,
    CollectorItemReceipt,
    CollectorJob,
    CollectorNode,
    CollectorRun,
    CollectorTransfer,
    CollectorTransferReceipt,
)


def _unique_columns(model: type) -> set[tuple[str, ...]]:
    return {
        tuple(constraint.columns.keys())
        for constraint in model.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def _check_names(model: type) -> set[str | None]:
    return {
        constraint.name
        for constraint in model.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }


def test_collector_models_define_durable_entities_and_hash_only_credentials():
    assert {
        CollectorNode.__tablename__,
        CollectorCredential.__tablename__,
        CollectorConfigVersion.__tablename__,
        CollectorJob.__tablename__,
        CollectorRun.__tablename__,
        CollectorEvent.__tablename__,
        CollectorTransfer.__tablename__,
        CollectorTransferReceipt.__tablename__,
        CollectorItemReceipt.__tablename__,
    } == {
        "collector_nodes",
        "collector_credentials",
        "collector_config_versions",
        "collector_jobs",
        "collector_runs",
        "collector_events",
        "collector_transfers",
        "collector_transfer_receipts",
        "collector_item_receipts",
    }

    credential_columns = set(CollectorCredential.__table__.columns.keys())
    assert "credential_hash" in credential_columns
    assert {
        "credential",
        "secret",
        "token",
        "api_key",
        "credential_plaintext",
    }.isdisjoint(credential_columns)

    config_columns = CollectorConfigVersion.__table__.columns
    assert not config_columns["snapshot"].nullable
    assert not config_columns["snapshot_sha256"].nullable

    job_columns = CollectorJob.__table__.columns
    assert not job_columns["manifest"].nullable
    assert not job_columns["manifest_sha256"].nullable


def test_collector_models_enforce_identity_idempotency_and_receipt_uniqueness():
    assert ("collector_id",) in _unique_columns(CollectorNode)
    assert ("credential_id",) in _unique_columns(CollectorCredential)
    assert ("collector_id", "version_no") in _unique_columns(CollectorConfigVersion)
    assert ("collector_id", "job_id") in _unique_columns(CollectorJob)
    assert ("collector_id", "run_id") in _unique_columns(CollectorRun)
    assert ("collector_id", "event_id") in _unique_columns(CollectorEvent)
    assert ("collector_id", "transport_id") in _unique_columns(CollectorTransfer)
    assert ("transfer_id",) in _unique_columns(CollectorTransferReceipt)
    assert ("transfer_id", "item_key") in _unique_columns(CollectorItemReceipt)


def test_collector_transfer_has_immutable_archive_ready_claim_and_attempt_state():
    columns = CollectorTransfer.__table__.columns
    for name in (
        "collector_id",
        "transport_id",
        "destination",
        "object_key",
        "archive_size",
        "archive_sha256",
        "status",
        "ready_at",
        "claimed_by",
        "lease_until",
        "attempt_count",
    ):
        assert name in columns

    for name in (
        "collector_id",
        "transport_id",
        "destination",
        "object_key",
        "archive_size",
        "archive_sha256",
        "status",
        "attempt_count",
    ):
        assert not columns[name].nullable

    assert {
        "ck_collector_transfers_status",
        "ck_collector_transfers_archive_size",
        "ck_collector_transfers_attempt_count",
        "ck_collector_transfers_claim_lease",
    }.issubset(_check_names(CollectorTransfer))

    assert "ck_collector_nodes_status" in _check_names(CollectorNode)
    assert "ck_collector_credentials_status" in _check_names(CollectorCredential)
    assert "ck_collector_jobs_mode" in _check_names(CollectorJob)
    assert "ck_collector_jobs_status" in _check_names(CollectorJob)
    assert "ck_collector_runs_status" in _check_names(CollectorRun)
    assert "ck_collector_events_level" in _check_names(CollectorEvent)
    assert "ck_collector_transfer_receipts_status" in _check_names(CollectorTransferReceipt)
    assert "ck_collector_item_receipts_status" in _check_names(CollectorItemReceipt)
