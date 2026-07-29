"""Durable Collector Gateway, transfer queue, and Consumer receipt models."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from server.app.core.time import utcnow
from server.app.db.base import Base


class CollectorNode(Base):
    __tablename__ = "collector_nodes"
    __table_args__ = (
        UniqueConstraint("collector_id", name="uq_collector_nodes_collector_id"),
        CheckConstraint(
            "status in ('enabled','disabled','revoked')",
            name="ck_collector_nodes_status",
        ),
        Index("ix_collector_nodes_status_heartbeat", "status", "last_heartbeat_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    collector_id: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    destination: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="enabled", server_default="enabled"
    )
    platform: Mapped[str | None] = mapped_column(String(64), nullable=True)
    agent_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    enabled_sources: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    current_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    current_stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    spool_pending_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
    )


class CollectorCredential(Base):
    __tablename__ = "collector_credentials"
    __table_args__ = (
        UniqueConstraint("credential_id", name="uq_collector_credentials_credential_id"),
        CheckConstraint(
            "status in ('active','revoked','expired')",
            name="ck_collector_credentials_status",
        ),
        Index(
            "ix_collector_credentials_collector_status",
            "collector_id",
            "status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    collector_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("collector_nodes.collector_id", ondelete="RESTRICT"),
        nullable=False,
    )
    credential_id: Mapped[str] = mapped_column(String(64), nullable=False)
    credential_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    hash_algorithm: Mapped[str] = mapped_column(
        String(32), nullable=False, default="bcrypt", server_default="bcrypt"
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default="active"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class CollectorConfigVersion(Base):
    __tablename__ = "collector_config_versions"
    __table_args__ = (
        UniqueConstraint(
            "collector_id",
            "version_no",
            name="uq_collector_config_versions_collector_version",
        ),
        CheckConstraint(
            "version_no > 0",
            name="ck_collector_config_versions_version_no",
        ),
        CheckConstraint(
            "max_staleness_seconds >= 0",
            name="ck_collector_config_versions_staleness",
        ),
        Index(
            "ix_collector_config_versions_collector_created",
            "collector_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    collector_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("collector_nodes.collector_id", ondelete="RESTRICT"),
        nullable=False,
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    max_staleness_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, server_default=func.now()
    )


class CollectorJob(Base):
    __tablename__ = "collector_jobs"
    __table_args__ = (
        UniqueConstraint(
            "collector_id",
            "job_id",
            name="uq_collector_jobs_collector_job",
        ),
        UniqueConstraint(
            "collector_id",
            "claim_request_id",
            name="uq_collector_jobs_collector_claim_request",
        ),
        CheckConstraint(
            "mode in ('refresh','discovery')",
            name="ck_collector_jobs_mode",
        ),
        CheckConstraint(
            "status in ('available','claimed','acknowledged','completed','failed','cancelled')",
            name="ck_collector_jobs_status",
        ),
        Index(
            "ix_collector_jobs_collector_status_created",
            "collector_id",
            "status",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    collector_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("collector_nodes.collector_id", ondelete="RESTRICT"),
        nullable=False,
    )
    job_id: Mapped[str] = mapped_column(String(64), nullable=False)
    claim_request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    config_version_id: Mapped[int] = mapped_column(
        ForeignKey("collector_config_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    destination: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest: Mapped[dict] = mapped_column(JSON, nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="available", server_default="available"
    )
    schedule_occurrence_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, server_default=func.now()
    )


class CollectorRun(Base):
    __tablename__ = "collector_runs"
    __table_args__ = (
        UniqueConstraint(
            "collector_id",
            "run_id",
            name="uq_collector_runs_collector_run",
        ),
        CheckConstraint(
            "status in ('pending','running','succeeded','partial','failed','skipped')",
            name="ck_collector_runs_status",
        ),
        Index(
            "ix_collector_runs_collector_created",
            "collector_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    collector_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("collector_nodes.collector_id", ondelete="RESTRICT"),
        nullable=False,
    )
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    job_pk_id: Mapped[int] = mapped_column(
        ForeignKey("collector_jobs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    job_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    current_stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_classification: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, server_default=func.now()
    )


class CollectorEvent(Base):
    __tablename__ = "collector_events"
    __table_args__ = (
        UniqueConstraint(
            "collector_id",
            "event_id",
            name="uq_collector_events_collector_event",
        ),
        CheckConstraint(
            "level in ('debug','info','warning','error','critical')",
            name="ck_collector_events_level",
        ),
        Index(
            "ix_collector_events_collector_run_occurred",
            "collector_id",
            "run_id",
            "occurred_at",
        ),
        Index("ix_collector_events_received_at", "received_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    collector_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("collector_nodes.collector_id", ondelete="RESTRICT"),
        nullable=False,
    )
    event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    transport_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bundle_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    component: Mapped[str] = mapped_column(String(32), nullable=False)
    component_version: Mapped[str] = mapped_column(String(64), nullable=False)
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    level: Mapped[str] = mapped_column(
        String(16), nullable=False, default="info", server_default="info"
    )
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, server_default=func.now()
    )


class CollectorTransfer(Base):
    __tablename__ = "collector_transfers"
    __table_args__ = (
        UniqueConstraint(
            "collector_id",
            "transport_id",
            name="uq_collector_transfers_collector_transport",
        ),
        CheckConstraint(
            "status in "
            "('created','archive_uploaded','ready','processing','processed',"
            "'retry_wait','failed','dead_letter')",
            name="ck_collector_transfers_status",
        ),
        CheckConstraint(
            "archive_size >= 0",
            name="ck_collector_transfers_archive_size",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_collector_transfers_attempt_count",
        ),
        CheckConstraint(
            "(claimed_by is null and lease_until is null) or "
            "(claimed_by is not null and lease_until is not null)",
            name="ck_collector_transfers_claim_lease",
        ),
        Index("ix_collector_transfers_queue", "status", "ready_at", "id"),
        Index("ix_collector_transfers_lease", "status", "lease_until"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    collector_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("collector_nodes.collector_id", ondelete="RESTRICT"),
        nullable=False,
    )
    transport_id: Mapped[str] = mapped_column(String(64), nullable=False)
    job_id: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    bundle_id: Mapped[str] = mapped_column(String(64), nullable=False)
    destination: Mapped[str] = mapped_column(String(64), nullable=False)
    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    archive_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    archive_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    bundle_schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="created", server_default="created"
    )
    ready_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    claimed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    error_classification: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
    )


class CollectorTransferReceipt(Base):
    __tablename__ = "collector_transfer_receipts"
    __table_args__ = (
        UniqueConstraint(
            "transfer_id",
            name="uq_collector_transfer_receipts_transfer",
        ),
        UniqueConstraint(
            "collector_id",
            "transport_id",
            name="uq_collector_transfer_receipts_identity",
        ),
        CheckConstraint(
            "status in ('processed','failed','dead_letter')",
            name="ck_collector_transfer_receipts_status",
        ),
        CheckConstraint(
            "item_count >= 0 and completed_item_count >= 0 and completed_item_count <= item_count",
            name="ck_collector_transfer_receipts_item_counts",
        ),
        Index("ix_collector_transfer_receipts_status_completed", "status", "completed_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    transfer_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("collector_transfers.id", ondelete="RESTRICT"),
        nullable=False,
    )
    collector_id: Mapped[str] = mapped_column(String(64), nullable=False)
    transport_id: Mapped[str] = mapped_column(String(64), nullable=False)
    archive_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    completed_item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    error_classification: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    replay_evidence_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, server_default=func.now()
    )


class CollectorItemReceipt(Base):
    __tablename__ = "collector_item_receipts"
    __table_args__ = (
        UniqueConstraint(
            "transfer_id",
            "item_key",
            name="uq_collector_item_receipts_transfer_item",
        ),
        CheckConstraint(
            "status in ('succeeded','skipped','failed')",
            name="ck_collector_item_receipts_status",
        ),
        Index("ix_collector_item_receipts_transfer_status", "transfer_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    transfer_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("collector_transfers.id", ondelete="RESTRICT"),
        nullable=False,
    )
    transport_id: Mapped[str] = mapped_column(String(64), nullable=False)
    item_key: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_item_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    game_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    outcome: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_classification: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, server_default=func.now()
    )
