from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from server.app.modules.collector.claim_repository import ConsumerLeaseConflict
from server.app.modules.collector.consumer_processing import (
    ConsumerItem,
    ConsumerProcessingCoordinator,
    DiscoveryConflictError,
    IdentityValidationError,
    ItemImportResult,
    ProcessingCallbacks,
    RetryPolicy,
    SchemaValidationError,
    SecurityValidationError,
    TemporaryBusinessMinioError,
    TemporaryMySQLError,
)
from server.app.modules.collector.models import (
    CollectorItemReceipt,
    CollectorTransferReceipt,
)


class SimulatedCrash(BaseException):
    pass


class FakeSession:
    def __init__(self) -> None:
        self.item_receipts: list[CollectorItemReceipt] = []
        self.transfer_receipts: list[CollectorTransferReceipt] = []
        self.committed_games: list[str] = []
        self._pending_objects: list[Any] = []
        self._pending_games: list[str] = []
        self.commit_count = 0
        self.rollback_count = 0
        self.commit_errors: list[Exception] = []
        self.execute_rowcounts: list[int] = []

    def add(self, value: Any) -> None:
        self._pending_objects.append(value)

    def stage_game_write(self, item_key: str) -> None:
        self._pending_games.append(item_key)

    def commit(self) -> None:
        self.commit_count += 1
        if self.commit_errors:
            raise self.commit_errors.pop(0)
        self.committed_games.extend(self._pending_games)
        for value in self._pending_objects:
            if isinstance(value, CollectorItemReceipt):
                self.item_receipts.append(value)
            elif isinstance(value, CollectorTransferReceipt):
                self.transfer_receipts.append(value)
        self._pending_games.clear()
        self._pending_objects.clear()

    def rollback(self) -> None:
        self.rollback_count += 1
        self._pending_games.clear()
        self._pending_objects.clear()

    def execute(self, _statement: Any) -> SimpleNamespace:
        rowcount = self.execute_rowcounts.pop(0) if self.execute_rowcounts else 1
        return SimpleNamespace(rowcount=rowcount)


def _transfer() -> SimpleNamespace:
    return SimpleNamespace(
        id=71,
        collector_id="collector-1",
        transport_id="transport-1",
        archive_sha256="a" * 64,
        status="processing",
        attempt_count=1,
        claimed_by="consumer-1",
        lease_until=object(),
        error_classification=None,
        error_summary=None,
        processed_at=None,
    )


def _items() -> tuple[ConsumerItem, ...]:
    return (
        ConsumerItem(
            item_key="item-a",
            source="baidu",
            source_item_id="source-a",
            mode="refresh",
            payload={"name": "A"},
        ),
        ConsumerItem(
            item_key="item-b",
            source="yingyongbao",
            source_item_id="source-b",
            mode="discovery",
            payload={"name": "B"},
        ),
    )


def _load_items(db: FakeSession, transfer: SimpleNamespace) -> tuple[CollectorItemReceipt, ...]:
    return tuple(receipt for receipt in db.item_receipts if receipt.transfer_id == transfer.id)


def _load_transfer(db: FakeSession, transfer: SimpleNamespace) -> CollectorTransferReceipt | None:
    return next(
        (receipt for receipt in db.transfer_receipts if receipt.transfer_id == transfer.id),
        None,
    )


def _callbacks(
    importer: Any,
    **overrides: Any,
) -> ProcessingCallbacks:
    values = {
        "import_item": importer,
        "load_item_receipts": _load_items,
        "load_transfer_receipt": _load_transfer,
    }
    values.update(overrides)
    return ProcessingCallbacks(**values)


def test_game_write_and_item_receipt_share_commit_and_replay_skips_committed() -> None:
    db = FakeSession()
    transfer = _transfer()
    calls: list[str] = []

    def importer(session: FakeSession, item: ConsumerItem) -> ItemImportResult:
        calls.append(item.item_key)
        session.stage_game_write(item.item_key)
        return ItemImportResult.succeeded(game_id=len(calls))

    def crash_after_first(
        _db: FakeSession,
        _transfer: SimpleNamespace,
        item: ConsumerItem,
        _receipt: CollectorItemReceipt,
    ) -> None:
        if item.item_key == "item-a":
            raise SimulatedCrash

    crashing = ConsumerProcessingCoordinator(
        callbacks=_callbacks(importer, after_item_commit=crash_after_first)
    )
    with pytest.raises(SimulatedCrash):
        crashing.process(db, transfer, _items())

    assert db.committed_games == ["item-a"]
    assert [receipt.item_key for receipt in db.item_receipts] == ["item-a"]
    assert db.transfer_receipts == []

    replay = ConsumerProcessingCoordinator(callbacks=_callbacks(importer))
    result = replay.process(db, transfer, _items())

    assert result.status == "processed"
    assert calls == ["item-a", "item-b"]
    assert db.committed_games == ["item-a", "item-b"]
    assert [receipt.item_key for receipt in db.item_receipts] == [
        "item-a",
        "item-b",
    ]
    assert db.transfer_receipts[0].status == "processed"
    assert db.transfer_receipts[0].completed_item_count == 2


def test_crash_before_commit_persists_neither_game_nor_item_receipt() -> None:
    db = FakeSession()
    transfer = _transfer()
    crashed = False

    def importer(session: FakeSession, item: ConsumerItem) -> ItemImportResult:
        session.stage_game_write(item.item_key)
        return ItemImportResult.succeeded(game_id=11)

    def crash_before_commit(
        _db: FakeSession,
        _transfer: SimpleNamespace,
        _item: ConsumerItem,
        _receipt: CollectorItemReceipt,
    ) -> None:
        nonlocal crashed
        if not crashed:
            crashed = True
            raise SimulatedCrash

    coordinator = ConsumerProcessingCoordinator(
        callbacks=_callbacks(importer, before_item_commit=crash_before_commit)
    )
    with pytest.raises(SimulatedCrash):
        coordinator.process(db, transfer, (_items()[0],))

    db.rollback()  # connection close/rollback after the simulated process death
    assert db.committed_games == []
    assert db.item_receipts == []

    replay = ConsumerProcessingCoordinator(callbacks=_callbacks(importer))
    result = replay.process(db, transfer, (_items()[0],))

    assert result.status == "processed"
    assert db.committed_games == ["item-a"]
    assert [receipt.item_key for receipt in db.item_receipts] == ["item-a"]


def test_temporary_mysql_failure_retries_with_bounded_backoff_then_succeeds() -> None:
    db = FakeSession()
    transfer = _transfer()
    attempts = 0
    delays: list[float] = []

    def importer(session: FakeSession, item: ConsumerItem) -> ItemImportResult:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise TemporaryMySQLError("mysql unavailable")
        session.stage_game_write(item.item_key)
        return ItemImportResult.succeeded(game_id=31)

    coordinator = ConsumerProcessingCoordinator(
        callbacks=_callbacks(importer),
        retry_policy=RetryPolicy(
            max_attempts=3,
            initial_delay_seconds=1,
            multiplier=2,
            max_delay_seconds=10,
        ),
        sleep=delays.append,
    )

    result = coordinator.process(db, transfer, (_items()[0],))

    assert result.status == "processed"
    assert attempts == 3
    assert delays == [1, 2]
    assert db.rollback_count == 2
    assert db.committed_games == ["item-a"]


def test_temporary_mysql_commit_failure_rolls_back_game_and_receipt_before_retry() -> None:
    db = FakeSession()
    db.commit_errors.append(TemporaryMySQLError("commit connection lost"))
    transfer = _transfer()
    calls = 0
    delays: list[float] = []

    def importer(session: FakeSession, item: ConsumerItem) -> ItemImportResult:
        nonlocal calls
        calls += 1
        session.stage_game_write(item.item_key)
        return ItemImportResult.succeeded(game_id=32)

    coordinator = ConsumerProcessingCoordinator(
        callbacks=_callbacks(importer),
        retry_policy=RetryPolicy(
            max_attempts=2,
            initial_delay_seconds=0,
            multiplier=1,
            max_delay_seconds=0,
        ),
        sleep=delays.append,
    )
    result = coordinator.process(db, transfer, (_items()[0],))

    assert result.status == "processed"
    assert calls == 2
    assert db.rollback_count == 1
    assert db.committed_games == ["item-a"]
    assert [receipt.item_key for receipt in db.item_receipts] == ["item-a"]
    assert delays == [0]


def test_exhausted_temporary_minio_failure_restores_transfer_to_ready() -> None:
    db = FakeSession()
    transfer = _transfer()
    delays: list[float] = []

    def importer(_db: FakeSession, _item: ConsumerItem) -> ItemImportResult:
        raise TemporaryBusinessMinioError("business object store unavailable")

    coordinator = ConsumerProcessingCoordinator(
        callbacks=_callbacks(importer),
        retry_policy=RetryPolicy(
            max_attempts=3,
            initial_delay_seconds=0.5,
            multiplier=2,
            max_delay_seconds=5,
        ),
        sleep=delays.append,
    )

    result = coordinator.process(db, transfer, (_items()[0],))

    assert result.status == "ready"
    assert result.error_classification == "business_minio_unavailable"
    assert delays == [0.5, 1.0]
    assert transfer.status == "retry_wait"
    assert transfer.ready_at is not None
    assert transfer.claimed_by is None
    assert transfer.lease_until is None
    assert db.item_receipts == []
    assert db.transfer_receipts == []


def test_transfer_attempt_ceiling_dead_letters_repeated_temporary_failure() -> None:
    db = FakeSession()
    transfer = _transfer()
    transfer.attempt_count = 4

    def importer(_db: FakeSession, _item: ConsumerItem) -> ItemImportResult:
        raise TemporaryMySQLError(
            "mysql://user:top-secret@db/test?token=top-secret Authorization: Bearer top-secret"
        )

    coordinator = ConsumerProcessingCoordinator(
        callbacks=_callbacks(importer),
        retry_policy=RetryPolicy(
            max_attempts=2,
            max_transfer_attempts=4,
            initial_delay_seconds=0,
            max_delay_seconds=0,
        ),
        sleep=lambda _delay: None,
    )

    result = coordinator.process(db, transfer, (_items()[0],))

    assert result.status == "dead_letter"
    assert transfer.status == "dead_letter"
    assert db.transfer_receipts[0].status == "dead_letter"
    assert "top-secret" not in (db.transfer_receipts[0].error_summary or "")
    assert "attempt ceiling reached" in (db.transfer_receipts[0].error_summary or "")


@pytest.mark.parametrize(
    ("error", "classification"),
    [
        (SchemaValidationError("bad schema"), "schema"),
        (IdentityValidationError("identity mismatch"), "identity"),
        (SecurityValidationError("unsafe archive"), "security"),
    ],
)
def test_permanent_validation_errors_dead_letter(
    error: Exception,
    classification: str,
) -> None:
    db = FakeSession()
    transfer = _transfer()
    calls: list[str] = []

    def importer(_db: FakeSession, item: ConsumerItem) -> ItemImportResult:
        calls.append(item.item_key)
        raise error

    coordinator = ConsumerProcessingCoordinator(callbacks=_callbacks(importer))
    result = coordinator.process(db, transfer, _items())

    assert result.status == "dead_letter"
    assert result.error_classification == classification
    assert calls == ["item-a"]
    assert transfer.status == "dead_letter"
    assert db.item_receipts[0].status == "failed"
    assert db.transfer_receipts[0].status == "dead_letter"
    assert db.transfer_receipts[0].completed_item_count == 1


def test_discovery_conflict_is_isolated_and_does_not_block_other_items() -> None:
    db = FakeSession()
    transfer = _transfer()
    calls: list[str] = []

    def importer(session: FakeSession, item: ConsumerItem) -> ItemImportResult:
        calls.append(item.item_key)
        if item.item_key == "item-b":
            raise DiscoveryConflictError("normalized name is ambiguous")
        session.stage_game_write(item.item_key)
        return ItemImportResult.succeeded(game_id=44)

    items = _items() + (
        ConsumerItem(
            item_key="item-c",
            source="ninegame",
            source_item_id="source-c",
            mode="refresh",
            payload={"name": "C"},
        ),
    )
    coordinator = ConsumerProcessingCoordinator(callbacks=_callbacks(importer))
    result = coordinator.process(db, transfer, items)

    assert result.status == "processed"
    assert calls == ["item-a", "item-b", "item-c"]
    assert db.committed_games == ["item-a", "item-c"]
    assert [(item.item_key, item.status) for item in db.item_receipts] == [
        ("item-a", "succeeded"),
        ("item-b", "skipped"),
        ("item-c", "succeeded"),
    ]
    assert db.item_receipts[1].error_classification == "discovery_conflict"
    assert db.transfer_receipts[0].completed_item_count == 3


def test_transfer_receipt_is_created_only_after_every_item_is_terminal() -> None:
    db = FakeSession()
    transfer = _transfer()

    def importer(session: FakeSession, item: ConsumerItem) -> ItemImportResult:
        assert db.transfer_receipts == []
        session.stage_game_write(item.item_key)
        return ItemImportResult.succeeded(game_id=51)

    coordinator = ConsumerProcessingCoordinator(callbacks=_callbacks(importer))
    result = coordinator.process(db, transfer, _items())

    assert result.status == "processed"
    assert len(db.transfer_receipts) == 1
    assert db.transfer_receipts[0].item_count == 2
    assert db.transfer_receipts[0].completed_item_count == 2


def test_replay_after_all_items_commit_finalizes_without_reimporting() -> None:
    db = FakeSession()
    transfer = _transfer()
    calls: list[str] = []

    def importer(session: FakeSession, item: ConsumerItem) -> ItemImportResult:
        calls.append(item.item_key)
        session.stage_game_write(item.item_key)
        return ItemImportResult.succeeded(game_id=len(calls))

    def crash_before_finalize(
        _db: FakeSession,
        _transfer: SimpleNamespace,
    ) -> None:
        raise SimulatedCrash

    crashing = ConsumerProcessingCoordinator(
        callbacks=_callbacks(
            importer,
            before_transfer_finalize=crash_before_finalize,
        )
    )
    with pytest.raises(SimulatedCrash):
        crashing.process(db, transfer, _items())

    assert calls == ["item-a", "item-b"]
    assert len(db.item_receipts) == 2
    assert db.transfer_receipts == []
    assert transfer.status == "processing"

    replay = ConsumerProcessingCoordinator(callbacks=_callbacks(importer))
    result = replay.process(db, transfer, _items())
    repeated_poll = replay.process(db, transfer, _items())

    assert result.status == "processed"
    assert repeated_poll.status == "processed"
    assert calls == ["item-a", "item-b"]
    assert len(db.transfer_receipts) == 1


def test_retry_release_fencing_conflict_preserves_processing_state() -> None:
    db = FakeSession()
    db.execute_rowcounts.append(0)
    transfer = _transfer()

    def importer(_db: FakeSession, _item: ConsumerItem) -> ItemImportResult:
        raise TemporaryBusinessMinioError("business object store unavailable")

    coordinator = ConsumerProcessingCoordinator(
        callbacks=_callbacks(importer),
        retry_policy=RetryPolicy(
            max_attempts=1,
            initial_delay_seconds=0,
            max_delay_seconds=0,
        ),
    )

    with pytest.raises(ConsumerLeaseConflict, match="fencing token"):
        coordinator.process(db, transfer, (_items()[0],))

    assert transfer.status == "processing"
    assert transfer.claimed_by == "consumer-1"
    assert db.item_receipts == []
    assert db.transfer_receipts == []
    assert db.rollback_count == 2


def test_processed_finalization_fencing_conflict_does_not_write_transfer_receipt() -> None:
    db = FakeSession()
    db.execute_rowcounts.append(0)
    transfer = _transfer()

    def importer(session: FakeSession, item: ConsumerItem) -> ItemImportResult:
        session.stage_game_write(item.item_key)
        return ItemImportResult.succeeded(game_id=81)

    coordinator = ConsumerProcessingCoordinator(callbacks=_callbacks(importer))

    with pytest.raises(ConsumerLeaseConflict, match="fencing token"):
        coordinator.process(db, transfer, (_items()[0],))

    assert db.committed_games == ["item-a"]
    assert [receipt.item_key for receipt in db.item_receipts] == ["item-a"]
    assert db.transfer_receipts == []
    assert transfer.status == "processing"
    assert db.rollback_count == 1


def test_dead_letter_fencing_conflict_rolls_back_failed_receipts() -> None:
    db = FakeSession()
    db.execute_rowcounts.append(0)
    transfer = _transfer()

    def importer(_db: FakeSession, _item: ConsumerItem) -> ItemImportResult:
        raise SchemaValidationError("bad schema")

    coordinator = ConsumerProcessingCoordinator(callbacks=_callbacks(importer))

    with pytest.raises(ConsumerLeaseConflict, match="fencing token"):
        coordinator.process(db, transfer, (_items()[0],))

    assert db.item_receipts == []
    assert db.transfer_receipts == []
    assert transfer.status == "processing"
    assert db.rollback_count == 2
