"""Add durable Collector Gateway, transfer queue, and Consumer receipt tables."""

import sqlalchemy as sa

from alembic import op

revision: str = "0073_collector_gateway_model"
down_revision: str | None = "0072_planb_discovery_patrol"
branch_labels: str | None = None
depends_on: str | None = None


TABLE_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}


def upgrade() -> None:
    op.create_table(
        "collector_nodes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("collector_id", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("destination", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="enabled", nullable=False),
        sa.Column("platform", sa.String(length=64), nullable=True),
        sa.Column("agent_version", sa.String(length=64), nullable=True),
        sa.Column("enabled_sources", sa.JSON(), nullable=False),
        sa.Column("current_run_id", sa.String(length=64), nullable=True),
        sa.Column("current_stage", sa.String(length=64), nullable=True),
        sa.Column("spool_pending_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(), nullable=True),
        sa.Column("last_success_at", sa.DateTime(), nullable=True),
        sa.Column("last_error_at", sa.DateTime(), nullable=True),
        sa.Column("last_error_summary", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.CheckConstraint(
            "status in ('enabled','disabled','revoked')",
            name="ck_collector_nodes_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("collector_id", name="uq_collector_nodes_collector_id"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_collector_nodes_status_heartbeat",
        "collector_nodes",
        ["status", "last_heartbeat_at"],
        unique=False,
    )

    op.create_table(
        "collector_credentials",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("collector_id", sa.String(length=64), nullable=False),
        sa.Column("credential_id", sa.String(length=64), nullable=False),
        sa.Column("credential_hash", sa.String(length=255), nullable=False),
        sa.Column("hash_algorithm", sa.String(length=32), server_default="bcrypt", nullable=False),
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status in ('active','revoked','expired')",
            name="ck_collector_credentials_status",
        ),
        sa.ForeignKeyConstraint(
            ["collector_id"],
            ["collector_nodes.collector_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "credential_id",
            name="uq_collector_credentials_credential_id",
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_collector_credentials_collector_status",
        "collector_credentials",
        ["collector_id", "status"],
        unique=False,
    )

    op.create_table(
        "collector_config_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("collector_id", sa.String(length=64), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("snapshot_sha256", sa.String(length=64), nullable=False),
        sa.Column("max_staleness_seconds", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.CheckConstraint(
            "version_no > 0",
            name="ck_collector_config_versions_version_no",
        ),
        sa.CheckConstraint(
            "max_staleness_seconds >= 0",
            name="ck_collector_config_versions_staleness",
        ),
        sa.ForeignKeyConstraint(
            ["collector_id"],
            ["collector_nodes.collector_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "collector_id",
            "version_no",
            name="uq_collector_config_versions_collector_version",
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_collector_config_versions_collector_created",
        "collector_config_versions",
        ["collector_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "collector_jobs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("collector_id", sa.String(length=64), nullable=False),
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("claim_request_id", sa.String(length=64), nullable=False),
        sa.Column("config_version_id", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=False),
        sa.Column("destination", sa.String(length=64), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="available", nullable=False),
        sa.Column("schedule_occurrence_at", sa.DateTime(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.CheckConstraint(
            "mode in ('refresh','discovery')",
            name="ck_collector_jobs_mode",
        ),
        sa.CheckConstraint(
            "status in ('available','claimed','acknowledged','completed','failed','cancelled')",
            name="ck_collector_jobs_status",
        ),
        sa.ForeignKeyConstraint(
            ["collector_id"],
            ["collector_nodes.collector_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["config_version_id"],
            ["collector_config_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "collector_id",
            "job_id",
            name="uq_collector_jobs_collector_job",
        ),
        sa.UniqueConstraint(
            "collector_id",
            "claim_request_id",
            name="uq_collector_jobs_collector_claim_request",
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_collector_jobs_collector_status_created",
        "collector_jobs",
        ["collector_id", "status", "created_at"],
        unique=False,
    )

    op.create_table(
        "collector_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("collector_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("job_pk_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("current_stage", sa.String(length=64), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("error_classification", sa.String(length=64), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.CheckConstraint(
            "status in ('pending','running','succeeded','partial','failed','skipped')",
            name="ck_collector_runs_status",
        ),
        sa.ForeignKeyConstraint(
            ["collector_id"],
            ["collector_nodes.collector_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["job_pk_id"],
            ["collector_jobs.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "collector_id",
            "run_id",
            name="uq_collector_runs_collector_run",
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_collector_runs_collector_created",
        "collector_runs",
        ["collector_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "collector_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("collector_id", sa.String(length=64), nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("job_id", sa.String(length=64), nullable=True),
        sa.Column("transport_id", sa.String(length=64), nullable=True),
        sa.Column("bundle_id", sa.String(length=64), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=True),
        sa.Column("component", sa.String(length=32), nullable=False),
        sa.Column("component_version", sa.String(length=64), nullable=False),
        sa.Column("stage", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("level", sa.String(length=16), server_default="info", nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "level in ('debug','info','warning','error','critical')",
            name="ck_collector_events_level",
        ),
        sa.ForeignKeyConstraint(
            ["collector_id"],
            ["collector_nodes.collector_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "collector_id",
            "event_id",
            name="uq_collector_events_collector_event",
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_collector_events_collector_run_occurred",
        "collector_events",
        ["collector_id", "run_id", "occurred_at"],
        unique=False,
    )
    op.create_index(
        "ix_collector_events_received_at",
        "collector_events",
        ["received_at"],
        unique=False,
    )

    op.create_table(
        "collector_transfers",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("collector_id", sa.String(length=64), nullable=False),
        sa.Column("transport_id", sa.String(length=64), nullable=False),
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("bundle_id", sa.String(length=64), nullable=False),
        sa.Column("destination", sa.String(length=64), nullable=False),
        sa.Column("object_key", sa.String(length=512), nullable=False),
        sa.Column("archive_size", sa.BigInteger(), nullable=False),
        sa.Column("archive_sha256", sa.String(length=64), nullable=False),
        sa.Column("bundle_schema_version", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="created", nullable=False),
        sa.Column("ready_at", sa.DateTime(), nullable=True),
        sa.Column("claimed_by", sa.String(length=128), nullable=True),
        sa.Column("lease_until", sa.DateTime(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_classification", sa.String(length=64), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(), nullable=True),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.CheckConstraint(
            "status in "
            "('created','archive_uploaded','ready','processing','processed',"
            "'retry_wait','failed','dead_letter')",
            name="ck_collector_transfers_status",
        ),
        sa.CheckConstraint(
            "archive_size >= 0",
            name="ck_collector_transfers_archive_size",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_collector_transfers_attempt_count",
        ),
        sa.CheckConstraint(
            "(claimed_by is null and lease_until is null) or "
            "(claimed_by is not null and lease_until is not null)",
            name="ck_collector_transfers_claim_lease",
        ),
        sa.ForeignKeyConstraint(
            ["collector_id"],
            ["collector_nodes.collector_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "collector_id",
            "transport_id",
            name="uq_collector_transfers_collector_transport",
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_collector_transfers_queue",
        "collector_transfers",
        ["status", "ready_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_collector_transfers_lease",
        "collector_transfers",
        ["status", "lease_until"],
        unique=False,
    )

    op.create_table(
        "collector_transfer_receipts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("transfer_id", sa.BigInteger(), nullable=False),
        sa.Column("collector_id", sa.String(length=64), nullable=False),
        sa.Column("transport_id", sa.String(length=64), nullable=False),
        sa.Column("archive_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("completed_item_count", sa.Integer(), nullable=False),
        sa.Column("error_classification", sa.String(length=64), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("replay_evidence_ref", sa.String(length=512), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.CheckConstraint(
            "status in ('processed','failed','dead_letter')",
            name="ck_collector_transfer_receipts_status",
        ),
        sa.CheckConstraint(
            "item_count >= 0 and completed_item_count >= 0 and completed_item_count <= item_count",
            name="ck_collector_transfer_receipts_item_counts",
        ),
        sa.ForeignKeyConstraint(
            ["transfer_id"],
            ["collector_transfers.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "collector_id",
            "transport_id",
            name="uq_collector_transfer_receipts_identity",
        ),
        sa.UniqueConstraint(
            "transfer_id",
            name="uq_collector_transfer_receipts_transfer",
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_collector_transfer_receipts_status_completed",
        "collector_transfer_receipts",
        ["status", "completed_at"],
        unique=False,
    )

    op.create_table(
        "collector_item_receipts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("transfer_id", sa.BigInteger(), nullable=False),
        sa.Column("transport_id", sa.String(length=64), nullable=False),
        sa.Column("item_key", sa.String(length=255), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_item_id", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=True),
        sa.Column("outcome", sa.JSON(), nullable=True),
        sa.Column("error_classification", sa.String(length=64), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.CheckConstraint(
            "status in ('succeeded','skipped','failed')",
            name="ck_collector_item_receipts_status",
        ),
        sa.ForeignKeyConstraint(
            ["transfer_id"],
            ["collector_transfers.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "transfer_id",
            "item_key",
            name="uq_collector_item_receipts_transfer_item",
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_collector_item_receipts_transfer_status",
        "collector_item_receipts",
        ["transfer_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("collector_item_receipts")
    op.drop_table("collector_transfer_receipts")
    op.drop_table("collector_transfers")
    op.drop_table("collector_events")
    op.drop_table("collector_runs")
    op.drop_table("collector_jobs")
    op.drop_table("collector_config_versions")
    op.drop_table("collector_credentials")
    op.drop_table("collector_nodes")
