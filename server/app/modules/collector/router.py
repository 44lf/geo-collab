"""Collector-authenticated Gateway transfer endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from server.app.core.config import get_settings
from server.app.db.session import get_db
from server.app.modules.collector.auth import (
    CollectorAuthorizationError,
    CollectorPrincipal,
    require_collector_principal,
)
from server.app.modules.collector.control_service import (
    CollectorControlError,
    acknowledge_job,
    claim_job,
    read_configuration,
)
from server.app.modules.collector.inbox import MinioCollectorInbox, get_collector_inbox
from server.app.modules.collector.models import CollectorTransfer
from server.app.modules.collector.run_service import (
    RunCompletion,
    RunCompletionConflict,
    RunCompletionError,
    record_completed_run,
)
from server.app.modules.collector.telemetry import (
    CollectorEventInput,
    HeartbeatInput,
    TelemetryValidationError,
    ingest_event_batch,
    record_heartbeat,
)
from server.app.modules.collector.transfer_service import (
    TransferDeclaration,
    TransferDeclarationError,
    TransferIdentityConflict,
    TransferIntegrityError,
    TransferServiceError,
    TransferStateConflict,
    complete_upload,
    create_or_get_transfer,
    find_owned_transfer,
    get_transfer_status,
    issue_upload_authorization,
)

collector_gateway_router = APIRouter()


class TransferCreateRequest(BaseModel):
    transport_id: str = Field(min_length=1, max_length=64)
    job_id: str = Field(min_length=1, max_length=64)
    run_id: str = Field(min_length=1, max_length=64)
    bundle_id: str = Field(min_length=1, max_length=64)
    destination: str = Field(min_length=1, max_length=64)
    archive_size: int = Field(gt=0)
    archive_sha256: str = Field(min_length=64, max_length=64)
    bundle_schema_version: str = Field(min_length=1, max_length=32)


class UploadAuthorizationResponse(BaseModel):
    object_key: str
    upload_url: str
    required_sha256: str
    required_size: int
    expires_at: datetime
    required_headers: dict[str, str]


class TransferCreateResponse(BaseModel):
    created: bool
    state: str
    object_key: str
    archive_sha256: str
    upload: UploadAuthorizationResponse | None


class TransferStateResponse(BaseModel):
    collector_id: str
    transport_id: str
    state: str
    object_key: str
    archive_sha256: str
    receipt_status: str | None = None
    receipt_completed_at: datetime | None = None
    error_classification: str | None = None
    error_summary: str | None = None


class HeartbeatRequest(BaseModel):
    platform: str = Field(min_length=1, max_length=64)
    agent_version: str = Field(min_length=1, max_length=64)
    enabled_sources: list[str] = Field(max_length=4)
    current_run_id: str | None = Field(default=None, max_length=64)
    current_stage: str = Field(min_length=1, max_length=64)
    spool_pending_count: int = Field(ge=0)


class HeartbeatResponse(BaseModel):
    collector_id: str
    received_at: datetime


class CollectorEventRequest(BaseModel):
    event_id: str = Field(min_length=1, max_length=64)
    run_id: str | None = Field(default=None, max_length=64)
    job_id: str | None = Field(default=None, max_length=64)
    transport_id: str | None = Field(default=None, max_length=64)
    bundle_id: str | None = Field(default=None, max_length=64)
    source: str | None = Field(default=None, max_length=32)
    component: str = Field(min_length=1, max_length=32)
    component_version: str = Field(min_length=1, max_length=64)
    stage: str = Field(min_length=1, max_length=64)
    event_type: str = Field(min_length=1, max_length=64)
    level: str = Field(min_length=1, max_length=16)
    payload: dict | None = None
    occurred_at: datetime


class EventBatchRequest(BaseModel):
    events: list[CollectorEventRequest] = Field(max_length=100)


class EventBatchResponse(BaseModel):
    accepted_event_ids: list[str]
    duplicate_event_ids: list[str]


class ConfigurationResponse(BaseModel):
    version: int
    unchanged: bool
    snapshot_sha256: str
    max_staleness_seconds: int
    snapshot: dict | None


class JobClaimRequest(BaseModel):
    claim_request_id: str = Field(min_length=1, max_length=64)
    mode: str = Field(pattern="^(refresh|discovery)$")


class JobResponse(BaseModel):
    job_id: str
    claim_request_id: str
    mode: str
    destination: str
    manifest_sha256: str
    manifest: dict
    status: str


class JobClaimResponse(BaseModel):
    created: bool
    job: JobResponse | None


class JobAckRequest(BaseModel):
    manifest_sha256: str = Field(min_length=64, max_length=64)


class RunCompleteRequest(BaseModel):
    run_id: str = Field(min_length=1, max_length=64)
    job_id: str = Field(min_length=1, max_length=64)
    source: str | None = Field(default=None, max_length=32)
    status: str = Field(pattern="^(succeeded|partial|failed)$")
    summary: dict = Field(default_factory=dict)
    error_classification: str | None = Field(default=None, max_length=64)
    error_summary: str | None = Field(default=None, max_length=1000)
    started_at: datetime
    finished_at: datetime


class RunCompleteResponse(BaseModel):
    created: bool
    collector_id: str
    run_id: str
    job_id: str
    status: str


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, CollectorAuthorizationError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if isinstance(exc, TransferIdentityConflict | TransferStateConflict):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, TransferIntegrityError):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))
    if isinstance(exc, TransferDeclarationError):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="transfer error")


def _upload_response(
    transfer: CollectorTransfer,
    inbox: MinioCollectorInbox,
) -> UploadAuthorizationResponse | None:
    if transfer.status not in {"created", "archive_uploaded", "retry_wait"}:
        return None
    authorization = issue_upload_authorization(
        transfer,
        inbox,
        expires_in=timedelta(seconds=get_settings().collector_upload_authorization_seconds),
    )
    return UploadAuthorizationResponse(
        object_key=authorization.object_key,
        upload_url=authorization.upload_url,
        required_sha256=authorization.required_sha256,
        required_size=authorization.required_size,
        expires_at=authorization.expires_at,
        # minio-py presigned PUT URLs sign only the host header. Sending an
        # additional unsigned x-amz-meta-* header is rejected by MinIO. The
        # completion boundary independently streams and hashes the private
        # Inbox object when signed checksum metadata is unavailable.
        required_headers={},
    )


@collector_gateway_router.post(
    "/heartbeat",
    response_model=HeartbeatResponse,
)
def heartbeat(
    payload: HeartbeatRequest,
    principal: CollectorPrincipal = Depends(require_collector_principal),
    db: Session = Depends(get_db),
) -> HeartbeatResponse:
    try:
        node = record_heartbeat(
            db,
            collector_id=principal.collector_id,
            destination=principal.destination,
            heartbeat=HeartbeatInput(
                platform=payload.platform,
                agent_version=payload.agent_version,
                enabled_sources=tuple(payload.enabled_sources),
                current_run_id=payload.current_run_id,
                current_stage=payload.current_stage,
                spool_pending_count=payload.spool_pending_count,
            ),
        )
    except TelemetryValidationError as exc:
        code = (
            status.HTTP_403_FORBIDDEN
            if "outside collector scope" in str(exc)
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    if node.last_heartbeat_at is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="heartbeat receive timestamp missing",
        )
    return HeartbeatResponse(
        collector_id=node.collector_id,
        received_at=node.last_heartbeat_at,
    )


@collector_gateway_router.post(
    "/events",
    response_model=EventBatchResponse,
)
def events(
    payload: EventBatchRequest,
    principal: CollectorPrincipal = Depends(require_collector_principal),
    db: Session = Depends(get_db),
) -> EventBatchResponse:
    settings = get_settings()
    try:
        result = ingest_event_batch(
            db,
            collector_id=principal.collector_id,
            events=[
                CollectorEventInput(
                    event_id=event.event_id,
                    run_id=event.run_id,
                    job_id=event.job_id,
                    transport_id=event.transport_id,
                    bundle_id=event.bundle_id,
                    source=event.source,
                    component=event.component,
                    component_version=event.component_version,
                    stage=event.stage,
                    event_type=event.event_type,
                    level=event.level,
                    payload=event.payload,
                    occurred_at=event.occurred_at,
                )
                for event in payload.events
            ],
            max_events=settings.collector_event_batch_size,
            max_payload_bytes=settings.collector_event_payload_bytes,
        )
    except TelemetryValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return EventBatchResponse(
        accepted_event_ids=list(result.accepted_event_ids),
        duplicate_event_ids=list(result.duplicate_event_ids),
    )


@collector_gateway_router.get(
    "/configuration",
    response_model=ConfigurationResponse,
)
def configuration(
    current_version: int | None = None,
    principal: CollectorPrincipal = Depends(require_collector_principal),
    db: Session = Depends(get_db),
) -> ConfigurationResponse:
    try:
        result = read_configuration(
            db,
            collector_id=principal.collector_id,
            destination=principal.destination,
            current_version=current_version,
            max_staleness_seconds=get_settings().collector_config_max_staleness_seconds,
        )
    except CollectorControlError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    config = result.config
    return ConfigurationResponse(
        version=config.version_no,
        unchanged=result.unchanged,
        snapshot_sha256=config.snapshot_sha256,
        max_staleness_seconds=config.max_staleness_seconds,
        snapshot=None if result.unchanged else config.snapshot,
    )


@collector_gateway_router.post(
    "/jobs/claim",
    response_model=JobClaimResponse,
)
def claim(
    payload: JobClaimRequest,
    principal: CollectorPrincipal = Depends(require_collector_principal),
    db: Session = Depends(get_db),
) -> JobClaimResponse:
    try:
        result = claim_job(
            db,
            collector_id=principal.collector_id,
            destination=principal.destination,
            claim_request_id=payload.claim_request_id,
            mode=payload.mode,
            max_staleness_seconds=get_settings().collector_config_max_staleness_seconds,
        )
    except CollectorControlError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    job = result.job
    return JobClaimResponse(
        created=result.created,
        job=(
            JobResponse(
                job_id=job.job_id,
                claim_request_id=job.claim_request_id,
                mode=job.mode,
                destination=job.destination,
                manifest_sha256=job.manifest_sha256,
                manifest=job.manifest,
                status=job.status,
            )
            if job is not None
            else None
        ),
    )


@collector_gateway_router.post(
    "/jobs/{job_id}/ack",
    response_model=JobResponse,
)
def acknowledge(
    job_id: str,
    payload: JobAckRequest,
    principal: CollectorPrincipal = Depends(require_collector_principal),
    db: Session = Depends(get_db),
) -> JobResponse:
    try:
        job = acknowledge_job(
            db,
            collector_id=principal.collector_id,
            job_id=job_id,
            manifest_sha256=payload.manifest_sha256,
        )
    except CollectorControlError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    return JobResponse(
        job_id=job.job_id,
        claim_request_id=job.claim_request_id,
        mode=job.mode,
        destination=job.destination,
        manifest_sha256=job.manifest_sha256,
        manifest=job.manifest,
        status=job.status,
    )


@collector_gateway_router.post(
    "/runs/complete",
    response_model=RunCompleteResponse,
)
def complete_run(
    payload: RunCompleteRequest,
    principal: CollectorPrincipal = Depends(require_collector_principal),
    db: Session = Depends(get_db),
) -> RunCompleteResponse:
    try:
        result = record_completed_run(
            db,
            collector_id=principal.collector_id,
            destination=principal.destination,
            completion=RunCompletion(
                run_id=payload.run_id,
                job_id=payload.job_id,
                source=payload.source,
                status=payload.status,
                summary=payload.summary,
                error_classification=payload.error_classification,
                error_summary=payload.error_summary,
                started_at=payload.started_at,
                finished_at=payload.finished_at,
            ),
        )
    except RunCompletionConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except RunCompletionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    run = result.run
    return RunCompleteResponse(
        created=result.created,
        collector_id=run.collector_id,
        run_id=run.run_id,
        job_id=run.job_id,
        status=run.status,
    )


@collector_gateway_router.post(
    "/transfers",
    response_model=TransferCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_transfer(
    payload: TransferCreateRequest,
    principal: CollectorPrincipal = Depends(require_collector_principal),
    db: Session = Depends(get_db),
    inbox: MinioCollectorInbox = Depends(get_collector_inbox),
) -> TransferCreateResponse:
    try:
        principal.require_scope(
            collector_id=principal.collector_id,
            destination=payload.destination,
        )
        result = create_or_get_transfer(
            db,
            TransferDeclaration(
                collector_id=principal.collector_id,
                transport_id=payload.transport_id,
                job_id=payload.job_id,
                run_id=payload.run_id,
                bundle_id=payload.bundle_id,
                destination=payload.destination,
                archive_size=payload.archive_size,
                archive_sha256=payload.archive_sha256,
                bundle_schema_version=payload.bundle_schema_version,
            ),
            max_archive_bytes=get_settings().collector_max_archive_bytes,
        )
        return TransferCreateResponse(
            created=result.created,
            state=result.transfer.status,
            object_key=result.transfer.object_key,
            archive_sha256=result.transfer.archive_sha256,
            upload=_upload_response(result.transfer, inbox),
        )
    except (CollectorAuthorizationError, TransferServiceError) as exc:
        raise _http_error(exc) from exc


@collector_gateway_router.post(
    "/transfers/{transport_id}/upload-authorization",
    response_model=UploadAuthorizationResponse,
)
def renew_upload_authorization(
    transport_id: str,
    principal: CollectorPrincipal = Depends(require_collector_principal),
    db: Session = Depends(get_db),
    inbox: MinioCollectorInbox = Depends(get_collector_inbox),
) -> UploadAuthorizationResponse:
    transfer = find_owned_transfer(
        db,
        collector_id=principal.collector_id,
        transport_id=transport_id,
    )
    if transfer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="transfer not found")
    try:
        response = _upload_response(transfer, inbox)
    except TransferServiceError as exc:
        raise _http_error(exc) from exc
    if response is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"transfer in state {transfer.status} cannot receive upload authorization",
        )
    return response


@collector_gateway_router.post(
    "/transfers/{transport_id}/complete",
    response_model=TransferStateResponse,
)
def mark_upload_complete(
    transport_id: str,
    principal: CollectorPrincipal = Depends(require_collector_principal),
    db: Session = Depends(get_db),
    inbox: MinioCollectorInbox = Depends(get_collector_inbox),
) -> TransferStateResponse:
    transfer = find_owned_transfer(
        db,
        collector_id=principal.collector_id,
        transport_id=transport_id,
    )
    if transfer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="transfer not found")
    try:
        completed = complete_upload(db, transfer, inbox)
    except TransferServiceError as exc:
        raise _http_error(exc) from exc
    return TransferStateResponse(
        collector_id=completed.collector_id,
        transport_id=completed.transport_id,
        state=completed.status,
        object_key=completed.object_key,
        archive_sha256=completed.archive_sha256,
    )


@collector_gateway_router.get(
    "/transfers/{transport_id}",
    response_model=TransferStateResponse,
)
def read_transfer_status(
    transport_id: str,
    principal: CollectorPrincipal = Depends(require_collector_principal),
    db: Session = Depends(get_db),
) -> TransferStateResponse:
    try:
        transfer_status = get_transfer_status(
            db,
            collector_id=principal.collector_id,
            transport_id=transport_id,
        )
    except TransferServiceError as exc:
        raise _http_error(exc) from exc
    if transfer_status is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="transfer not found")
    return TransferStateResponse(
        collector_id=transfer_status.collector_id,
        transport_id=transfer_status.transport_id,
        state=transfer_status.state,
        object_key=transfer_status.object_key,
        archive_sha256=transfer_status.archive_sha256,
        receipt_status=transfer_status.receipt_status,
        receipt_completed_at=transfer_status.receipt_completed_at,
        error_classification=transfer_status.error_classification,
        error_summary=transfer_status.error_summary,
    )
