"""Idempotent Collector transfer creation and Inbox completion boundaries."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol, cast

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from server.app.core.time import utcnow
from server.app.modules.collector.models import (
    CollectorJob,
    CollectorNode,
    CollectorRun,
    CollectorTransfer,
    CollectorTransferReceipt,
)

_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_TRANSFER_UPLOADABLE_STATES = {"created", "archive_uploaded", "retry_wait"}
_TRANSFER_READY_STATES = {"ready", "processing", "processed"}


class TransferServiceError(RuntimeError):
    """Base error for Collector transfer operations."""


class TransferDeclarationError(TransferServiceError):
    """The request is invalid or outside the Collector's scope."""


class TransferIdentityConflict(TransferServiceError):
    """A transport ID was reused for different immutable content."""


class TransferStateConflict(TransferServiceError):
    """The requested transition is invalid for the current transfer state."""


class TransferIntegrityError(TransferServiceError):
    """The uploaded Inbox object does not match the declaration."""


@dataclass(frozen=True, slots=True)
class TransferDeclaration:
    collector_id: str
    transport_id: str
    job_id: str
    run_id: str
    bundle_id: str
    destination: str
    archive_size: int
    archive_sha256: str
    bundle_schema_version: str


@dataclass(frozen=True, slots=True)
class CreateTransferResult:
    transfer: CollectorTransfer
    created: bool


@dataclass(frozen=True, slots=True)
class UploadAuthorization:
    object_key: str
    upload_url: str
    required_sha256: str
    required_size: int
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class InboxObjectMetadata:
    object_key: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class TransferStatus:
    collector_id: str
    transport_id: str
    object_key: str
    archive_sha256: str
    state: str
    receipt_status: str | None
    receipt_completed_at: datetime | None
    error_classification: str | None
    error_summary: str | None


class InboxUploadAuthorizer(Protocol):
    def authorize_put(
        self,
        *,
        object_key: str,
        size_bytes: int,
        sha256: str,
        expires_in: timedelta,
    ) -> str: ...


class InboxObjectInspector(Protocol):
    def stat_object(self, *, object_key: str) -> InboxObjectMetadata: ...


def _validate_id(field_name: str, value: str) -> None:
    if _SAFE_ID.fullmatch(value) is None:
        raise TransferDeclarationError(f"{field_name} is not a safe bounded identifier")


def _validate_declaration(declaration: TransferDeclaration, *, max_archive_bytes: int) -> None:
    for field_name in (
        "collector_id",
        "transport_id",
        "job_id",
        "run_id",
        "bundle_id",
    ):
        _validate_id(field_name, getattr(declaration, field_name))
    if not declaration.destination or declaration.destination != declaration.destination.strip():
        raise TransferDeclarationError("destination must be non-empty trimmed text")
    if declaration.archive_size <= 0 or declaration.archive_size > max_archive_bytes:
        raise TransferDeclarationError("archive_size is outside the allowed bounds")
    if _SHA256.fullmatch(declaration.archive_sha256) is None:
        raise TransferDeclarationError("archive_sha256 must be a lowercase SHA-256 digest")
    if declaration.bundle_schema_version != "2":
        raise TransferDeclarationError("unsupported Bundle schema version")


def fixed_inbox_object_key(collector_id: str, transport_id: str) -> str:
    _validate_id("collector_id", collector_id)
    _validate_id("transport_id", transport_id)
    return f"incoming/{collector_id}/{transport_id}.tar.zst"


def _immutable_fields(transfer: CollectorTransfer) -> tuple[object, ...]:
    return (
        transfer.job_id,
        transfer.run_id,
        transfer.bundle_id,
        transfer.destination,
        transfer.object_key,
        transfer.archive_size,
        transfer.archive_sha256,
        transfer.bundle_schema_version,
    )


def _declared_fields(declaration: TransferDeclaration) -> tuple[object, ...]:
    return (
        declaration.job_id,
        declaration.run_id,
        declaration.bundle_id,
        declaration.destination,
        fixed_inbox_object_key(declaration.collector_id, declaration.transport_id),
        declaration.archive_size,
        declaration.archive_sha256,
        declaration.bundle_schema_version,
    )


def _require_identical(
    transfer: CollectorTransfer, declaration: TransferDeclaration
) -> CollectorTransfer:
    if _immutable_fields(transfer) != _declared_fields(declaration):
        raise TransferIdentityConflict("transport_id already belongs to different immutable fields")
    return transfer


def _find_transfer(
    db: Session, *, collector_id: str, transport_id: str
) -> CollectorTransfer | None:
    return cast(
        CollectorTransfer | None,
        db.scalar(
            select(CollectorTransfer).where(
                CollectorTransfer.collector_id == collector_id,
                CollectorTransfer.transport_id == transport_id,
            )
        ),
    )


def find_owned_transfer(
    db: Session, *, collector_id: str, transport_id: str
) -> CollectorTransfer | None:
    """Load a transfer only inside the authenticated Collector identity scope."""

    _validate_id("collector_id", collector_id)
    _validate_id("transport_id", transport_id)
    return _find_transfer(
        db,
        collector_id=collector_id,
        transport_id=transport_id,
    )


def _authorize_declaration(db: Session, declaration: TransferDeclaration) -> None:
    node = cast(
        CollectorNode | None,
        db.scalar(
            select(CollectorNode).where(CollectorNode.collector_id == declaration.collector_id)
        ),
    )
    if node is None or node.status != "enabled" or node.destination != declaration.destination:
        raise TransferDeclarationError("collector is disabled or destination is denied")

    job = cast(
        CollectorJob | None,
        db.scalar(
            select(CollectorJob).where(
                CollectorJob.collector_id == declaration.collector_id,
                CollectorJob.job_id == declaration.job_id,
            )
        ),
    )
    if (
        job is None
        or job.collector_id != declaration.collector_id
        or job.destination != declaration.destination
        or job.status not in {"claimed", "acknowledged", "completed", "failed"}
    ):
        raise TransferDeclarationError("job is missing, out of scope, or not transferable")

    run = cast(
        CollectorRun | None,
        db.scalar(
            select(CollectorRun).where(
                CollectorRun.collector_id == declaration.collector_id,
                CollectorRun.run_id == declaration.run_id,
            )
        ),
    )
    if (
        run is None
        or run.collector_id != declaration.collector_id
        or run.job_id != declaration.job_id
        or run.status not in {"succeeded", "partial", "failed"}
    ):
        raise TransferDeclarationError("run is missing, out of scope, or not complete")


def create_or_get_transfer(
    db: Session,
    declaration: TransferDeclaration,
    *,
    max_archive_bytes: int,
) -> CreateTransferResult:
    """Create one immutable transfer or return the identical existing row."""

    _validate_declaration(declaration, max_archive_bytes=max_archive_bytes)
    existing = _find_transfer(
        db,
        collector_id=declaration.collector_id,
        transport_id=declaration.transport_id,
    )
    if existing is not None:
        return CreateTransferResult(
            transfer=_require_identical(existing, declaration),
            created=False,
        )

    _authorize_declaration(db, declaration)
    transfer = CollectorTransfer(
        collector_id=declaration.collector_id,
        transport_id=declaration.transport_id,
        job_id=declaration.job_id,
        run_id=declaration.run_id,
        bundle_id=declaration.bundle_id,
        destination=declaration.destination,
        object_key=fixed_inbox_object_key(declaration.collector_id, declaration.transport_id),
        archive_size=declaration.archive_size,
        archive_sha256=declaration.archive_sha256,
        bundle_schema_version=declaration.bundle_schema_version,
        status="created",
    )
    db.add(transfer)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        winner = _find_transfer(
            db,
            collector_id=declaration.collector_id,
            transport_id=declaration.transport_id,
        )
        if winner is None:
            raise
        return CreateTransferResult(
            transfer=_require_identical(winner, declaration),
            created=False,
        )
    return CreateTransferResult(transfer=transfer, created=True)


def issue_upload_authorization(
    transfer: CollectorTransfer,
    authorizer: InboxUploadAuthorizer,
    *,
    now: datetime | None = None,
    expires_in: timedelta = timedelta(minutes=10),
) -> UploadAuthorization:
    """Issue an ephemeral fixed-key upload grant without persisting its URL."""

    if transfer.status not in _TRANSFER_UPLOADABLE_STATES:
        raise TransferStateConflict(
            f"transfer in state {transfer.status} cannot receive upload authorization"
        )
    if not timedelta(minutes=1) <= expires_in <= timedelta(minutes=15):
        raise ValueError("upload authorization lifetime must be between 1 and 15 minutes")
    issued_at = now or utcnow()
    upload_url = authorizer.authorize_put(
        object_key=transfer.object_key,
        size_bytes=transfer.archive_size,
        sha256=transfer.archive_sha256,
        expires_in=expires_in,
    )
    return UploadAuthorization(
        object_key=transfer.object_key,
        upload_url=upload_url,
        required_sha256=transfer.archive_sha256,
        required_size=transfer.archive_size,
        expires_at=issued_at + expires_in,
    )


def complete_upload(
    db: Session,
    transfer: CollectorTransfer,
    inspector: InboxObjectInspector,
    *,
    now: datetime | None = None,
) -> CollectorTransfer:
    """Verify the remote immutable object before making the queue row READY."""

    if transfer.status in _TRANSFER_READY_STATES:
        return transfer
    if transfer.status not in _TRANSFER_UPLOADABLE_STATES:
        raise TransferStateConflict(f"transfer in state {transfer.status} cannot be completed")
    metadata = inspector.stat_object(object_key=transfer.object_key)
    if metadata.object_key != transfer.object_key:
        raise TransferIntegrityError("Inbox object key differs from declaration")
    if metadata.size_bytes != transfer.archive_size:
        raise TransferIntegrityError("Inbox object size differs from declaration")
    if metadata.sha256 != transfer.archive_sha256:
        raise TransferIntegrityError("Inbox object SHA-256 differs from declaration")

    completed_at = now or utcnow()
    transfer.status = "ready"
    transfer.uploaded_at = completed_at
    transfer.ready_at = completed_at
    transfer.claimed_by = None
    transfer.lease_until = None
    transfer.error_classification = None
    transfer.error_summary = None
    db.flush()
    return transfer


def get_transfer_status(
    db: Session, *, collector_id: str, transport_id: str
) -> TransferStatus | None:
    """Return only the owning Collector's state and cleanup receipt fields."""

    _validate_id("collector_id", collector_id)
    _validate_id("transport_id", transport_id)
    transfer = _find_transfer(db, collector_id=collector_id, transport_id=transport_id)
    if transfer is None:
        return None
    receipt = cast(
        CollectorTransferReceipt | None,
        db.scalar(
            select(CollectorTransferReceipt).where(
                CollectorTransferReceipt.transfer_id == transfer.id,
                CollectorTransferReceipt.collector_id == collector_id,
                CollectorTransferReceipt.transport_id == transport_id,
            )
        ),
    )
    if receipt is not None and receipt.archive_sha256 != transfer.archive_sha256:
        raise TransferIntegrityError("stored receipt does not match transfer archive")
    return TransferStatus(
        collector_id=collector_id,
        transport_id=transport_id,
        object_key=transfer.object_key,
        archive_sha256=transfer.archive_sha256,
        state=transfer.status,
        receipt_status=receipt.status if receipt is not None else None,
        receipt_completed_at=receipt.completed_at if receipt is not None else None,
        error_classification=(
            receipt.error_classification if receipt is not None else transfer.error_classification
        ),
        error_summary=(receipt.error_summary if receipt is not None else transfer.error_summary),
    )
