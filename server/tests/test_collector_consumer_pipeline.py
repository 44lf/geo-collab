from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from urllib3.exceptions import MaxRetryError

from server.app.modules.collector.bundle_validation import (
    BundleValidationError,
    BundleValidationLimits,
)
from server.app.modules.collector.consumer_pipeline import ClaimedTransferProcessor
from server.app.modules.collector.consumer_processing import (
    IdentityValidationError,
    RetryPolicy,
)
from server.app.modules.collector.consumer_runtime import ProcessResult, TransferClaim


class _Session:
    def __init__(self, *, transfer, scalars, execute_rowcount=1):
        self.transfer = transfer
        self.scalars = list(scalars)
        self.added = []
        self.commits = 0
        self.rollbacks = 0
        self.execute_rowcount = execute_rowcount

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def get(self, _model, transfer_id):
        return self.transfer if transfer_id == self.transfer.id else None

    def scalar(self, _statement):
        return self.scalars.pop(0) if self.scalars else None

    def add(self, value):
        self.added.append(value)

    def execute(self, _statement):
        return SimpleNamespace(rowcount=self.execute_rowcount)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _claim(**changes) -> TransferClaim:
    values = {
        "transfer_id": 11,
        "collector_id": "collector-1",
        "transport_id": "transport-1",
        "job_id": "job-1",
        "run_id": "run-1",
        "bundle_id": "bundle-1",
        "destination": "geo-dev",
        "object_key": "incoming/collector-1/transport-1.tar.zst",
        "attempt_count": 1,
        "worker_id": "worker-1",
    }
    values.update(changes)
    return TransferClaim(
        **values,
    )


def _transfer():
    return SimpleNamespace(
        id=11,
        collector_id="collector-1",
        transport_id="transport-1",
        job_id="job-1",
        run_id="run-1",
        bundle_id="bundle-1",
        destination="geo-dev",
        object_key="incoming/collector-1/transport-1.tar.zst",
        archive_size=12,
        archive_sha256="a" * 64,
        status="processing",
        claimed_by="worker-1",
        lease_until=datetime.now() + timedelta(hours=1),
        attempt_count=1,
        error_classification=None,
        error_summary=None,
    )


def _limits() -> BundleValidationLimits:
    return BundleValidationLimits(
        max_archive_bytes=1024,
        max_files=10,
        max_file_bytes=1024,
        max_total_bytes=4096,
        max_manifest_bytes=1024,
    )


def test_claimed_processor_runs_download_validation_and_coordinator(monkeypatch):
    transfer = _transfer()
    job = SimpleNamespace(manifest={"targets": []})
    session = _Session(transfer=transfer, scalars=[job])
    inbox = MagicMock()

    def download_to(*, object_key, destination: Path, expected_size):
        destination.write_bytes(b"x" * expected_size)
        return destination

    inbox.download_to.side_effect = download_to
    validated = object()
    items = (object(),)
    monkeypatch.setattr(
        "server.app.modules.collector.consumer_pipeline.validate_and_extract_bundle",
        lambda *args, **kwargs: validated,
    )
    monkeypatch.setattr(
        "server.app.modules.collector.consumer_pipeline.prepare_consumer_items",
        lambda *args, **kwargs: items,
    )
    coordinator = MagicMock()
    coordinator.process.return_value = SimpleNamespace(
        status="processed",
        error_classification=None,
    )
    monkeypatch.setattr(
        "server.app.modules.collector.consumer_pipeline.ConsumerProcessingCoordinator",
        lambda **kwargs: coordinator,
    )
    processor = ClaimedTransferProcessor(
        session_factory=lambda: session,
        inbox=inbox,
        limits=_limits(),
        import_service=MagicMock(),
    )

    result = processor(_claim(), MagicMock())

    assert result == ProcessResult.processed()
    inbox.download_to.assert_called_once()
    coordinator.process.assert_called_once_with(session, transfer, items)


def test_permanent_bundle_validation_failure_creates_dead_letter(monkeypatch):
    transfer = _transfer()
    job = SimpleNamespace(manifest={"targets": []})
    processing_session = _Session(transfer=transfer, scalars=[job])
    terminal_session = _Session(transfer=transfer, scalars=[None])
    sessions = iter((processing_session, terminal_session))
    inbox = MagicMock()
    inbox.download_to.side_effect = BundleValidationError("unsafe path")
    processor = ClaimedTransferProcessor(
        session_factory=lambda: next(sessions),
        inbox=inbox,
        limits=_limits(),
        import_service=MagicMock(),
    )

    result = processor(_claim(), MagicMock())

    assert result == ProcessResult.terminal()
    assert transfer.status == "dead_letter"
    assert terminal_session.commits == 1
    assert terminal_session.added[0].status == "dead_letter"
    assert "unsafe path" in terminal_session.added[0].error_summary


def test_permanent_preparation_identity_failure_creates_dead_letter(monkeypatch):
    transfer = _transfer()
    job = SimpleNamespace(manifest={"targets": []})
    processing_session = _Session(transfer=transfer, scalars=[job])
    terminal_session = _Session(transfer=transfer, scalars=[None])
    sessions = iter((processing_session, terminal_session))
    inbox = MagicMock()

    def download_to(*, object_key, destination: Path, expected_size):
        destination.write_bytes(b"x" * expected_size)
        return destination

    inbox.download_to.side_effect = download_to
    monkeypatch.setattr(
        "server.app.modules.collector.consumer_pipeline.validate_and_extract_bundle",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        "server.app.modules.collector.consumer_pipeline.prepare_consumer_items",
        lambda *args, **kwargs: (_ for _ in ()).throw(IdentityValidationError("target mismatch")),
    )
    processor = ClaimedTransferProcessor(
        session_factory=lambda: next(sessions),
        inbox=inbox,
        limits=_limits(),
        import_service=MagicMock(),
    )

    result = processor(_claim(), MagicMock())

    assert result == ProcessResult.terminal()
    assert transfer.status == "dead_letter"
    assert terminal_session.added[0].error_classification == "identity"


def test_temporary_inbox_failure_returns_retry_without_terminal_mutation():
    transfer = _transfer()
    job = SimpleNamespace(manifest={"targets": []})
    session = _Session(transfer=transfer, scalars=[job])
    inbox = MagicMock()
    inbox.download_to.side_effect = OSError("MinIO unavailable")
    processor = ClaimedTransferProcessor(
        session_factory=lambda: session,
        inbox=inbox,
        limits=_limits(),
        import_service=MagicMock(),
    )

    result = processor(_claim(), MagicMock())

    assert result.outcome == "retry"
    assert result.classification == "consumer_dependency_unavailable"
    assert transfer.status == "processing"


def test_minio_connection_retry_exhaustion_is_a_dependency_outage():
    transfer = _transfer()
    job = SimpleNamespace(manifest={"targets": []})
    session = _Session(transfer=transfer, scalars=[job])
    inbox = MagicMock()
    inbox.download_to.side_effect = MaxRetryError(
        pool=None,
        url="/geo-collector-inbox/object",
        reason=ConnectionRefusedError(),
    )
    processor = ClaimedTransferProcessor(
        session_factory=lambda: session,
        inbox=inbox,
        limits=_limits(),
        import_service=MagicMock(),
    )

    result = processor(_claim(), MagicMock())

    assert result.outcome == "retry"
    assert result.classification == "consumer_dependency_unavailable"
    assert transfer.status == "processing"


def test_stale_claim_is_fenced_before_download_or_business_mutation():
    transfer = _transfer()
    transfer.attempt_count = 2
    session = _Session(transfer=transfer, scalars=[])
    inbox = MagicMock()
    lease = MagicMock()
    processor = ClaimedTransferProcessor(
        session_factory=lambda: session,
        inbox=inbox,
        limits=_limits(),
        import_service=MagicMock(),
    )

    result = processor(_claim(attempt_count=1), lease)

    assert result == ProcessResult.terminal()
    assert lease.released is True
    inbox.download_to.assert_not_called()
    assert session.added == []
    assert session.commits == 0


def test_dead_letter_write_is_fenced_when_lease_was_reclaimed():
    transfer = _transfer()
    job = SimpleNamespace(manifest={"targets": []})
    processing_session = _Session(transfer=transfer, scalars=[job])
    terminal_session = _Session(
        transfer=transfer,
        scalars=[None],
        execute_rowcount=0,
    )
    sessions = iter((processing_session, terminal_session))
    inbox = MagicMock()
    inbox.download_to.side_effect = BundleValidationError("unsafe path")
    lease = MagicMock()
    processor = ClaimedTransferProcessor(
        session_factory=lambda: next(sessions),
        inbox=inbox,
        limits=_limits(),
        import_service=MagicMock(),
    )

    result = processor(_claim(), lease)

    assert result == ProcessResult.terminal()
    assert lease.released is True
    assert terminal_session.rollbacks == 1
    assert terminal_session.added == []
    assert terminal_session.commits == 0
    assert transfer.status == "processing"


def test_transfer_attempt_ceiling_dead_letters_repeated_dependency_failure():
    transfer = _transfer()
    transfer.attempt_count = 5
    job = SimpleNamespace(manifest={"targets": []})
    processing_session = _Session(transfer=transfer, scalars=[job])
    terminal_session = _Session(transfer=transfer, scalars=[None])
    sessions = iter((processing_session, terminal_session))
    inbox = MagicMock()
    inbox.download_to.side_effect = OSError("MinIO unavailable")
    processor = ClaimedTransferProcessor(
        session_factory=lambda: next(sessions),
        inbox=inbox,
        limits=_limits(),
        import_service=MagicMock(),
        retry_policy=RetryPolicy(max_transfer_attempts=5),
    )

    result = processor(_claim(attempt_count=5), MagicMock())

    assert result == ProcessResult.terminal()
    assert transfer.status == "dead_letter"
    assert terminal_session.commits == 1
    receipt = terminal_session.added[0]
    assert receipt.error_classification == "consumer_dependency_unavailable"
    assert "attempt ceiling reached" in receipt.error_summary
