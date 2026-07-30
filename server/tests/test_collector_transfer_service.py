from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from server.app.modules.collector.models import (
    CollectorJob,
    CollectorNode,
    CollectorRun,
    CollectorTransfer,
    CollectorTransferReceipt,
)
from server.app.modules.collector.transfer_service import (
    InboxObjectMetadata,
    TransferDeclaration,
    TransferDeclarationError,
    TransferIdentityConflict,
    TransferIntegrityError,
    TransferStateConflict,
    complete_upload,
    create_or_get_transfer,
    fixed_inbox_object_key,
    get_transfer_status,
    issue_upload_authorization,
)

NOW = datetime(2026, 7, 29, 3, 0)
SHA256 = "a" * 64


def _declaration(**changes) -> TransferDeclaration:
    values = {
        "collector_id": "collector-local-1",
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
    return TransferDeclaration(**values)


def _node(**changes) -> CollectorNode:
    values = {
        "collector_id": "collector-local-1",
        "display_name": "Local collector",
        "destination": "geo-production",
        "status": "enabled",
        "enabled_sources": ["baidu", "ninegame", "yingyongbao", "taptap"],
    }
    values.update(changes)
    return CollectorNode(**values)


def _job(**changes) -> CollectorJob:
    values = {
        "collector_id": "collector-local-1",
        "job_id": "job-1",
        "claim_request_id": "claim-1",
        "config_version_id": 1,
        "mode": "refresh",
        "destination": "geo-production",
        "manifest": {},
        "manifest_sha256": "b" * 64,
        "status": "acknowledged",
    }
    values.update(changes)
    return CollectorJob(**values)


def _run(**changes) -> CollectorRun:
    values = {
        "collector_id": "collector-local-1",
        "run_id": "run-1",
        "job_pk_id": 1,
        "job_id": "job-1",
        "status": "succeeded",
    }
    values.update(changes)
    return CollectorRun(**values)


def _transfer(**changes) -> CollectorTransfer:
    values = {
        "id": 11,
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


def test_transfer_create_has_fixed_key_and_is_idempotent():
    db = MagicMock()
    db.scalar.side_effect = [None, _node(), _job(), _run()]

    first = create_or_get_transfer(db, _declaration(), max_archive_bytes=10_000)
    assert first.created
    assert first.transfer.object_key == fixed_inbox_object_key("collector-local-1", "transport-1")
    db.add.assert_called_once_with(first.transfer)
    db.flush.assert_called_once()

    repeated_db = MagicMock()
    repeated_db.scalar.return_value = first.transfer
    repeated = create_or_get_transfer(repeated_db, _declaration(), max_archive_bytes=10_000)
    assert not repeated.created
    assert repeated.transfer is first.transfer
    repeated_db.add.assert_not_called()


def test_failed_run_can_transfer_bounded_failure_evidence():
    db = MagicMock()
    db.scalar.side_effect = [
        None,
        _node(),
        _job(status="failed"),
        _run(status="failed"),
    ]

    result = create_or_get_transfer(db, _declaration(), max_archive_bytes=10_000)

    assert result.created
    assert result.transfer.run_id == "run-1"


def test_transport_id_reuse_with_different_archive_is_permanent_conflict():
    db = MagicMock()
    db.scalar.return_value = _transfer()
    with pytest.raises(TransferIdentityConflict, match="immutable"):
        create_or_get_transfer(
            db,
            _declaration(archive_sha256="c" * 64),
            max_archive_bytes=10_000,
        )
    db.add.assert_not_called()


@pytest.mark.parametrize(
    ("node", "job", "run"),
    [
        (_node(status="revoked"), _job(), _run()),
        (_node(destination="dev"), _job(), _run()),
        (_node(), _job(collector_id="other"), _run()),
        (_node(), _job(status="available"), _run()),
        (_node(), _job(), _run(job_id="other")),
        (_node(), _job(), _run(status="running")),
    ],
)
def test_transfer_create_rejects_revoked_or_cross_scope_resources(node, job, run):
    db = MagicMock()
    db.scalar.side_effect = [None, node, job, run]
    with pytest.raises(TransferDeclarationError):
        create_or_get_transfer(db, _declaration(), max_archive_bytes=10_000)
    db.add.assert_not_called()


def test_concurrent_duplicate_insert_reloads_identical_winner():
    db = MagicMock()
    winner = _transfer()
    db.scalar.side_effect = [None, _node(), _job(), _run(), winner]
    db.flush.side_effect = IntegrityError("insert", {}, Exception("duplicate"))

    result = create_or_get_transfer(db, _declaration(), max_archive_bytes=10_000)
    assert not result.created
    assert result.transfer is winner
    db.rollback.assert_called_once()


def test_upload_authorization_is_short_lived_fixed_key_and_not_persisted():
    authorizer = MagicMock()
    authorizer.authorize_put.return_value = "https://inbox.invalid/redacted"
    transfer = _transfer()

    authorization = issue_upload_authorization(
        transfer,
        authorizer,
        now=NOW,
        expires_in=timedelta(minutes=5),
    )
    assert authorization.object_key == transfer.object_key
    assert authorization.required_sha256 == transfer.archive_sha256
    assert authorization.expires_at == NOW + timedelta(minutes=5)
    authorizer.authorize_put.assert_called_once_with(
        object_key=transfer.object_key,
        size_bytes=transfer.archive_size,
        sha256=transfer.archive_sha256,
        expires_in=timedelta(minutes=5),
    )
    assert "upload_url" not in set(CollectorTransfer.__table__.columns.keys())

    with pytest.raises(ValueError, match="between 1 and 15"):
        issue_upload_authorization(transfer, authorizer, expires_in=timedelta(hours=1))
    with pytest.raises(TransferStateConflict):
        issue_upload_authorization(_transfer(status="processed"), authorizer)


@pytest.mark.parametrize(
    "metadata",
    [
        InboxObjectMetadata(
            object_key="incoming/other.tar.zst",
            size_bytes=1024,
            sha256=SHA256,
        ),
        InboxObjectMetadata(
            object_key="incoming/collector-local-1/transport-1.tar.zst",
            size_bytes=999,
            sha256=SHA256,
        ),
        InboxObjectMetadata(
            object_key="incoming/collector-local-1/transport-1.tar.zst",
            size_bytes=1024,
            sha256="f" * 64,
        ),
    ],
)
def test_completion_rejects_remote_key_size_or_hash_mismatch(metadata):
    db = MagicMock()
    inspector = MagicMock()
    inspector.stat_object.return_value = metadata
    transfer = _transfer()

    with pytest.raises(TransferIntegrityError):
        complete_upload(db, transfer, inspector, now=NOW)
    assert transfer.status == "created"
    db.flush.assert_not_called()


def test_completion_verifies_object_before_ready_and_retries_idempotently():
    db = MagicMock()
    inspector = MagicMock()
    transfer = _transfer()
    inspector.stat_object.return_value = InboxObjectMetadata(
        object_key=transfer.object_key,
        size_bytes=transfer.archive_size,
        sha256=transfer.archive_sha256,
    )

    completed = complete_upload(db, transfer, inspector, now=NOW)
    assert completed.status == "ready"
    assert completed.ready_at == NOW
    assert completed.uploaded_at == NOW
    db.flush.assert_called_once()

    inspector.reset_mock()
    db.reset_mock()
    assert complete_upload(db, completed, inspector, now=NOW) is completed
    inspector.stat_object.assert_not_called()
    db.flush.assert_not_called()


def test_terminal_status_is_collector_scoped_and_carries_matching_receipt():
    transfer = _transfer(status="processed")
    receipt = CollectorTransferReceipt(
        transfer_id=transfer.id,
        collector_id=transfer.collector_id,
        transport_id=transfer.transport_id,
        archive_sha256=transfer.archive_sha256,
        status="processed",
        item_count=2,
        completed_item_count=2,
        completed_at=NOW,
    )
    db = MagicMock()
    db.scalar.side_effect = [transfer, receipt]

    status = get_transfer_status(db, collector_id="collector-local-1", transport_id="transport-1")
    assert status is not None
    assert status.state == "processed"
    assert status.receipt_status == "processed"
    assert status.object_key == transfer.object_key
    assert status.archive_sha256 == SHA256

    db = MagicMock()
    db.scalar.return_value = None
    assert (
        get_transfer_status(db, collector_id="collector-local-1", transport_id="transport-1")
        is None
    )
