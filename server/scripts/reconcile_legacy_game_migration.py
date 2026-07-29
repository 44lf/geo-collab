"""Safely reconcile the historical DEV game-library Alembic branch.

The shared ``geo_dev`` database can contain the historical revision
``0067_game_cull_and_manual``.  That revision represented the same game-library
DDL that now lives at revisions 0067-0069, but it branched directly from 0064
and therefore skipped the current 0065/0066 migrations.

This incident-specific tool:

1. verifies the historical game schema and rejects unknown drift;
2. repairs only the missing current 0065/0066 effects;
3. moves the version marker to the equivalent current 0069 revision; and
4. upgrades through the committed Collector target at 0073.

The default is a read-only dry run.  Applying requires both ``--apply`` and an
explicit ``--backup-confirmed`` acknowledgement because MySQL DDL is not
transactional.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from alembic.config import Config as AlembicConfig
from sqlalchemy.engine import Connection, Engine

from alembic import command

LEGACY_REVISION = "0067_game_cull_and_manual"
BRIDGE_REVISION = "0069_game_cull_and_manual"
TARGET_REVISION = "0073_collector_gateway_model"
EXPECTED_DATABASE = "geo_dev"

CURRENT_CHAIN_REVISIONS = {
    BRIDGE_REVISION,
    "0070_prompt_template_platform",
    "0071_game_ingest_baidu_only",
    "0072_planb_discovery_patrol",
    TARGET_REVISION,
}

COLLECTOR_TABLES = {
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

DISCOVERY_COLUMNS = {
    "discovery_enabled",
    "discovery_window_start",
    "discovery_window_end",
    "discovery_seed_paths",
    "discovery_detail_limit",
    "discovery_max_shots",
    "discovery_min_gap_seconds",
    "discovery_max_gap_seconds",
    "discovery_last_run_started_at",
    "discovery_last_run_finished_at",
    "discovery_last_run_summary",
    "discovery_last_run_trigger",
    "patrol_include_main",
}


class ReconciliationError(RuntimeError):
    """Raised when the database is not safe for this targeted reconciliation."""


@dataclass(frozen=True)
class ColumnRequirement:
    type_family: str
    nullable: bool
    length: int | None = None
    default: str | None = None


LEGACY_COLUMN_REQUIREMENTS: dict[str, dict[str, ColumnRequirement]] = {
    "games": {
        "name": ColumnRequirement("string", False, length=200),
        "name_normalized": ColumnRequirement("string", False, length=200),
        "stock_category_id": ColumnRequirement("integer", True),
        "use_count": ColumnRequirement("integer", False, default="0"),
        "last_used_article_id": ColumnRequirement("integer", True),
        "last_used_at": ColumnRequirement("datetime", True),
        "is_active": ColumnRequirement("integer", False, default="1"),
        "not_found_streak": ColumnRequirement("integer", False, default="0"),
        "manually_curated": ColumnRequirement("integer", False, default="0"),
    },
    "game_tags": {
        "game_id": ColumnRequirement("integer", False),
        "tag": ColumnRequirement("string", False, length=100),
        "axis": ColumnRequirement("string", True, length=20),
    },
    "stock_images": {
        "source_url": ColumnRequirement("string", True, length=1000),
        "source_url_hash": ColumnRequirement("string", True, length=64),
        "use_count": ColumnRequirement("integer", False, default="0"),
        "last_used_at": ColumnRequirement("datetime", True),
        "last_used_article_id": ColumnRequirement("integer", True),
    },
    "game_ingest_config": {
        "source_order": ColumnRequirement("string", False, length=50),
        "max_shots": ColumnRequirement("integer", False, default="6"),
        "cull_after_misses": ColumnRequirement("integer", False, default="3"),
        "cull_enabled": ColumnRequirement("integer", False, default="1"),
    },
}

XHS_COLUMN_REQUIREMENTS = {
    "id": ColumnRequirement("integer", False),
    "job_id": ColumnRequirement("string", False, length=32),
    "source_article_id": ColumnRequirement("integer", True),
    "status": ColumnRequirement("string", False, length=20, default="pending"),
    "theme": ColumnRequirement("string", False, length=50, default="sketch"),
    "mode": ColumnRequirement("string", False, length=20, default="separator"),
    "cover_key": ColumnRequirement("string", True, length=500),
    "card_keys": ColumnRequirement("json", True),
    "error": ColumnRequirement("text", True),
    "created_at": ColumnRequirement("datetime", True),
    "updated_at": ColumnRequirement("datetime", True),
}

CONTENT_TYPE_REQUIREMENT = ColumnRequirement("string", True, length=40)


def _normalize_default(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    while len(normalized) >= 2 and normalized[0] == normalized[-1] and normalized[0] in "'\"":
        normalized = normalized[1:-1]
    return normalized


def _matches_type(column_type: sa.types.TypeEngine[Any], requirement: ColumnRequirement) -> bool:
    family = requirement.type_family
    if family == "string":
        return isinstance(column_type, sa.String) and column_type.length == requirement.length
    if family == "integer":
        return isinstance(column_type, sa.Integer)
    if family == "datetime":
        return isinstance(column_type, sa.DateTime)
    if family == "json":
        return isinstance(column_type, sa.JSON)
    if family == "text":
        return isinstance(column_type, sa.Text)
    raise AssertionError(f"unsupported type family: {family}")


def _columns(inspector: sa.Inspector, table_name: str) -> dict[str, dict[str, Any]]:
    if not inspector.has_table(table_name):
        raise ReconciliationError(f"required table is missing: {table_name}")
    result: dict[str, dict[str, Any]] = {}
    for reflected_column in inspector.get_columns(table_name):
        column = dict(reflected_column)
        name = column.get("name")
        if not isinstance(name, str) or not name:
            raise ReconciliationError(f"{table_name} contains an unnamed reflected column")
        result[name] = column
    return result


def _require_columns(
    inspector: sa.Inspector,
    table_name: str,
    requirements: dict[str, ColumnRequirement],
) -> None:
    actual = _columns(inspector, table_name)
    missing = sorted(set(requirements) - set(actual))
    if missing:
        raise ReconciliationError(f"{table_name} is missing required columns: {missing}")

    for name, requirement in requirements.items():
        column = actual[name]
        if not _matches_type(column["type"], requirement):
            raise ReconciliationError(
                f"{table_name}.{name} has incompatible type {column['type']!s}"
            )
        if bool(column["nullable"]) is not requirement.nullable:
            raise ReconciliationError(
                f"{table_name}.{name} nullable={column['nullable']!r}, "
                f"expected {requirement.nullable!r}"
            )
        if requirement.default is not None:
            actual_default = _normalize_default(column.get("default"))
            if actual_default != requirement.default:
                raise ReconciliationError(
                    f"{table_name}.{name} default={actual_default!r}, "
                    f"expected {requirement.default!r}"
                )


def _index_map(inspector: sa.Inspector, table_name: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for reflected_index in inspector.get_indexes(table_name):
        index = dict(reflected_index)
        name = index.get("name")
        if isinstance(name, str) and name:
            result[name] = index
    return result


def _require_named_index(
    inspector: sa.Inspector,
    table_name: str,
    index_name: str,
    columns: tuple[str, ...],
    *,
    unique: bool,
) -> None:
    index = _index_map(inspector, table_name).get(index_name)
    if index is None:
        raise ReconciliationError(f"required index is missing: {table_name}.{index_name}")
    actual_columns = tuple(index.get("column_names") or ())
    if actual_columns != columns or bool(index.get("unique")) is not unique:
        raise ReconciliationError(
            f"{table_name}.{index_name} is incompatible: "
            f"columns={actual_columns!r}, unique={index.get('unique')!r}"
        )


def _require_unique_columns(
    inspector: sa.Inspector,
    table_name: str,
    columns: tuple[str, ...],
) -> None:
    actual = {
        tuple(constraint.get("column_names") or ())
        for constraint in inspector.get_unique_constraints(table_name)
    }
    if columns not in actual:
        raise ReconciliationError(f"{table_name} is missing unique constraint on {columns!r}")


def _require_foreign_key(
    inspector: sa.Inspector,
    table_name: str,
    columns: tuple[str, ...],
    referred_table: str,
    referred_columns: tuple[str, ...],
    *,
    ondelete: str,
) -> None:
    for foreign_key in inspector.get_foreign_keys(table_name):
        if tuple(foreign_key.get("constrained_columns") or ()) != columns:
            continue
        actual_ondelete = str((foreign_key.get("options") or {}).get("ondelete", "")).upper()
        if (
            foreign_key.get("referred_table") == referred_table
            and tuple(foreign_key.get("referred_columns") or ()) == referred_columns
            and actual_ondelete == ondelete
        ):
            return
    raise ReconciliationError(
        f"{table_name} is missing compatible foreign key "
        f"{columns!r} -> {referred_table}{referred_columns!r} ON DELETE {ondelete}"
    )


def _current_revision(connection: Connection) -> str:
    inspector = sa.inspect(connection)
    if not inspector.has_table("alembic_version"):
        raise ReconciliationError("alembic_version table is missing")
    revisions = list(
        connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalars()
    )
    if len(revisions) != 1:
        raise ReconciliationError(
            f"expected exactly one Alembic revision row, found {len(revisions)}"
        )
    return str(revisions[0])


def _validate_legacy_game_schema(
    inspector: sa.Inspector,
    *,
    source_order_default: str = "taptap,baidu",
) -> None:
    for table_name, requirements in LEGACY_COLUMN_REQUIREMENTS.items():
        _require_columns(inspector, table_name, requirements)

    source_order = _columns(inspector, "game_ingest_config")["source_order"]
    actual_source_order_default = _normalize_default(source_order.get("default"))
    if actual_source_order_default != source_order_default:
        raise ReconciliationError(
            "game_ingest_config.source_order "
            f"default={actual_source_order_default!r}, "
            f"expected {source_order_default!r}"
        )

    _require_named_index(
        inspector,
        "games",
        "ix_games_is_active",
        ("is_active",),
        unique=False,
    )
    _require_unique_columns(inspector, "games", ("name_normalized",))
    _require_foreign_key(
        inspector,
        "games",
        ("stock_category_id",),
        "stock_categories",
        ("id",),
        ondelete="SET NULL",
    )
    _require_foreign_key(
        inspector,
        "games",
        ("last_used_article_id",),
        "articles",
        ("id",),
        ondelete="SET NULL",
    )

    _require_named_index(
        inspector,
        "game_tags",
        "ix_game_tags_game_id",
        ("game_id",),
        unique=False,
    )
    _require_named_index(
        inspector,
        "game_tags",
        "ix_game_tags_tag",
        ("tag",),
        unique=False,
    )
    _require_unique_columns(inspector, "game_tags", ("game_id", "tag"))
    _require_foreign_key(
        inspector,
        "game_tags",
        ("game_id",),
        "games",
        ("id",),
        ondelete="CASCADE",
    )

    _require_unique_columns(
        inspector,
        "stock_images",
        ("category_id", "source_url_hash"),
    )
    _require_foreign_key(
        inspector,
        "stock_images",
        ("last_used_article_id",),
        "articles",
        ("id",),
        ondelete="SET NULL",
    )


def _reject_post_bridge_drift(inspector: sa.Inspector) -> None:
    prompt_columns = _columns(inspector, "prompt_templates")
    if "platform" in prompt_columns:
        raise ReconciliationError(
            "prompt_templates.platform already exists while the revision is still legacy"
        )

    config_columns = _columns(inspector, "game_ingest_config")
    unexpected_discovery = sorted(DISCOVERY_COLUMNS & set(config_columns))
    if unexpected_discovery:
        raise ReconciliationError(
            f"post-bridge discovery columns already exist: {unexpected_discovery}"
        )

    existing_collector_tables = sorted(COLLECTOR_TABLES & set(inspector.get_table_names()))
    if existing_collector_tables:
        raise ReconciliationError(
            f"Collector tables already exist at the legacy revision: {existing_collector_tables}"
        )


def _validate_xhs_table(inspector: sa.Inspector, *, require_indexes: bool) -> None:
    _require_columns(inspector, "xhs_render_jobs", XHS_COLUMN_REQUIREMENTS)
    if require_indexes:
        _require_named_index(
            inspector,
            "xhs_render_jobs",
            "ix_xhs_render_jobs_job_id",
            ("job_id",),
            unique=True,
        )
        _require_named_index(
            inspector,
            "xhs_render_jobs",
            "ix_xhs_render_jobs_source_article_id",
            ("source_article_id",),
            unique=False,
        )


def _validate_content_type(inspector: sa.Inspector, *, require_index: bool) -> None:
    _require_columns(
        inspector,
        "articles",
        {"content_type": CONTENT_TYPE_REQUIREMENT},
    )
    if require_index:
        _require_named_index(
            inspector,
            "articles",
            "ix_articles_content_type",
            ("content_type",),
            unique=False,
        )


def _bridge_actions(inspector: sa.Inspector) -> list[str]:
    actions: list[str] = []
    if not inspector.has_table("xhs_render_jobs"):
        actions.append("create xhs_render_jobs table and indexes (current 0065)")
    else:
        _validate_xhs_table(inspector, require_indexes=False)
        indexes = _index_map(inspector, "xhs_render_jobs")
        for name, columns, unique in (
            ("ix_xhs_render_jobs_job_id", ("job_id",), True),
            (
                "ix_xhs_render_jobs_source_article_id",
                ("source_article_id",),
                False,
            ),
        ):
            if name in indexes:
                _require_named_index(
                    inspector,
                    "xhs_render_jobs",
                    name,
                    columns,
                    unique=unique,
                )
            else:
                actions.append(f"create index {name}")

    article_columns = _columns(inspector, "articles")
    if "content_type" not in article_columns:
        actions.append("add articles.content_type VARCHAR(40) NULL (current 0066)")
    else:
        _validate_content_type(inspector, require_index=False)

    article_indexes = _index_map(inspector, "articles")
    if "ix_articles_content_type" in article_indexes:
        _require_named_index(
            inspector,
            "articles",
            "ix_articles_content_type",
            ("content_type",),
            unique=False,
        )
    else:
        actions.append("create index ix_articles_content_type")
    return actions


def _validate_bridge_schema(inspector: sa.Inspector) -> None:
    _validate_xhs_table(inspector, require_indexes=True)
    _validate_content_type(inspector, require_index=True)


def _validate_target_schema(connection: Connection) -> None:
    inspector = sa.inspect(connection)
    _validate_legacy_game_schema(inspector, source_order_default="baidu")
    _validate_bridge_schema(inspector)

    _require_columns(
        inspector,
        "prompt_templates",
        {"platform": ColumnRequirement("string", True, length=50)},
    )
    _require_named_index(
        inspector,
        "prompt_templates",
        "ix_prompt_templates_platform",
        ("platform",),
        unique=False,
    )

    _require_columns(
        inspector,
        "game_ingest_config",
        {
            name: ColumnRequirement("integer", False)
            for name in DISCOVERY_COLUMNS
            if name.endswith(("_enabled", "_limit", "_shots", "_seconds"))
        }
        | {
            "discovery_window_start": ColumnRequirement("string", False, length=5),
            "discovery_window_end": ColumnRequirement("string", False, length=5),
            "discovery_seed_paths": ColumnRequirement("json", True),
            "discovery_last_run_started_at": ColumnRequirement("datetime", True),
            "discovery_last_run_finished_at": ColumnRequirement("datetime", True),
            "discovery_last_run_summary": ColumnRequirement("json", True),
            "discovery_last_run_trigger": ColumnRequirement("string", True, length=12),
            "patrol_include_main": ColumnRequirement("integer", False),
        },
    )

    missing_collector_tables = sorted(COLLECTOR_TABLES - set(inspector.get_table_names()))
    if missing_collector_tables:
        raise ReconciliationError(
            f"target Collector tables are missing: {missing_collector_tables}"
        )


def _database_name(connection: Connection) -> str:
    database = connection.execute(sa.text("SELECT DATABASE()")).scalar_one_or_none()
    if not database:
        raise ReconciliationError("connection has no selected database")
    return str(database)


def _assert_connection_scope(connection: Connection, expected_database: str) -> str:
    if connection.dialect.name != "mysql":
        raise ReconciliationError(
            f"this reconciliation supports MySQL only, got {connection.dialect.name!r}"
        )
    database = _database_name(connection)
    if database != expected_database:
        raise ReconciliationError(f"refusing database {database!r}; expected {expected_database!r}")
    return database


def plan_reconciliation(
    connection: Connection,
    *,
    expected_database: str = EXPECTED_DATABASE,
) -> dict[str, Any]:
    database = _assert_connection_scope(connection, expected_database)
    revision = _current_revision(connection)
    inspector = sa.inspect(connection)
    actions: list[str] = []

    if revision == LEGACY_REVISION:
        _validate_legacy_game_schema(inspector)
        _reject_post_bridge_drift(inspector)
        actions.extend(_bridge_actions(inspector))
        actions.append(f"move alembic_version {LEGACY_REVISION} -> {BRIDGE_REVISION}")
        actions.append(f"run Alembic upgrade to {TARGET_REVISION}")
    elif revision in CURRENT_CHAIN_REVISIONS:
        if revision == BRIDGE_REVISION:
            _validate_legacy_game_schema(inspector)
            _validate_bridge_schema(inspector)
        if revision == TARGET_REVISION:
            _validate_target_schema(connection)
        else:
            actions.append(f"resume Alembic upgrade to {TARGET_REVISION}")
    else:
        raise ReconciliationError(
            f"unsupported current revision {revision!r}; no changes were made"
        )

    return {
        "ok": True,
        "mode": "dry-run",
        "database": database,
        "current_revision": revision,
        "bridge_revision": BRIDGE_REVISION,
        "target_revision": TARGET_REVISION,
        "planned_actions": actions,
        "warnings": [
            "MySQL DDL is non-transactional; take and verify a backup before --apply.",
            "The target is pinned to committed revision 0073; uncommitted 0074 is excluded.",
        ],
    }


def _create_xhs_table(connection: Connection) -> None:
    metadata = sa.MetaData()
    table = sa.Table(
        "xhs_render_jobs",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.String(32), nullable=False),
        sa.Column("source_article_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("theme", sa.String(50), nullable=False, server_default="sketch"),
        sa.Column("mode", sa.String(20), nullable=False, server_default="separator"),
        sa.Column("cover_key", sa.String(500), nullable=True),
        sa.Column("card_keys", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    sa.Index("ix_xhs_render_jobs_job_id", table.c.job_id, unique=True)
    sa.Index(
        "ix_xhs_render_jobs_source_article_id",
        table.c.source_article_id,
    )
    table.create(connection)


def _apply_bridge(connection: Connection, *, lock_timeout_seconds: int) -> None:
    if lock_timeout_seconds <= 0:
        raise ReconciliationError("lock timeout must be a positive integer")

    revision = _current_revision(connection)
    if revision != LEGACY_REVISION:
        return

    inspector = sa.inspect(connection)
    _validate_legacy_game_schema(inspector)
    _reject_post_bridge_drift(inspector)
    _bridge_actions(inspector)
    connection.commit()
    connection.exec_driver_sql(f"SET SESSION lock_wait_timeout = {int(lock_timeout_seconds)}")
    connection.commit()

    if not inspector.has_table("xhs_render_jobs"):
        _create_xhs_table(connection)
    else:
        indexes = _index_map(inspector, "xhs_render_jobs")
        if "ix_xhs_render_jobs_job_id" not in indexes:
            connection.exec_driver_sql(
                "CREATE UNIQUE INDEX ix_xhs_render_jobs_job_id ON xhs_render_jobs (job_id)"
            )
        if "ix_xhs_render_jobs_source_article_id" not in indexes:
            connection.exec_driver_sql(
                "CREATE INDEX ix_xhs_render_jobs_source_article_id "
                "ON xhs_render_jobs (source_article_id)"
            )

    inspector = sa.inspect(connection)
    article_columns = _columns(inspector, "articles")
    if "content_type" not in article_columns:
        connection.exec_driver_sql("ALTER TABLE articles ADD COLUMN content_type VARCHAR(40) NULL")

    inspector = sa.inspect(connection)
    article_indexes = _index_map(inspector, "articles")
    if "ix_articles_content_type" not in article_indexes:
        connection.exec_driver_sql(
            "CREATE INDEX ix_articles_content_type ON articles (content_type)"
        )

    inspector = sa.inspect(connection)
    _validate_legacy_game_schema(inspector)
    _validate_bridge_schema(inspector)

    result = connection.execute(
        sa.text("UPDATE alembic_version SET version_num = :bridge WHERE version_num = :legacy"),
        {"bridge": BRIDGE_REVISION, "legacy": LEGACY_REVISION},
    )
    if result.rowcount != 1:
        connection.rollback()
        raise ReconciliationError(
            "failed to move the Alembic version marker after bridge validation"
        )
    connection.commit()


@contextmanager
def _temporary_alembic_environment(
    database_url: str,
    *,
    lock_timeout_seconds: int,
) -> Iterator[None]:
    from server.app.core.config import get_settings

    previous_url = os.environ.get("GEO_DATABASE_URL")
    previous_timeout = os.environ.get("GEO_MIGRATE_LOCK_WAIT_TIMEOUT")
    os.environ["GEO_DATABASE_URL"] = database_url
    os.environ["GEO_MIGRATE_LOCK_WAIT_TIMEOUT"] = str(lock_timeout_seconds)
    get_settings.cache_clear()
    try:
        yield
    finally:
        if previous_url is None:
            os.environ.pop("GEO_DATABASE_URL", None)
        else:
            os.environ["GEO_DATABASE_URL"] = previous_url
        if previous_timeout is None:
            os.environ.pop("GEO_MIGRATE_LOCK_WAIT_TIMEOUT", None)
        else:
            os.environ["GEO_MIGRATE_LOCK_WAIT_TIMEOUT"] = previous_timeout
        get_settings.cache_clear()


def _upgrade_to_target(database_url: str, *, lock_timeout_seconds: int) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    config = AlembicConfig(str(repo_root / "alembic.ini"))
    config.set_main_option(
        "script_location",
        str(repo_root / "server" / "alembic"),
    )
    with _temporary_alembic_environment(
        database_url,
        lock_timeout_seconds=lock_timeout_seconds,
    ):
        command.upgrade(config, TARGET_REVISION)


def _new_engine(database_url: str) -> Engine:
    return sa.create_engine(database_url, pool_pre_ping=True)


def reconcile_database(
    database_url: str,
    *,
    apply: bool,
    backup_confirmed: bool = False,
    expected_database: str = EXPECTED_DATABASE,
    lock_timeout_seconds: int = 30,
) -> dict[str, Any]:
    if apply and not backup_confirmed:
        raise ReconciliationError(
            "--apply requires --backup-confirmed because MySQL DDL cannot be rolled back"
        )
    if lock_timeout_seconds <= 0:
        raise ReconciliationError("lock timeout must be a positive integer")

    engine = _new_engine(database_url)
    try:
        with engine.connect() as connection:
            plan = plan_reconciliation(
                connection,
                expected_database=expected_database,
            )
        if not apply:
            return plan

        initial_revision = str(plan["current_revision"])
        with engine.connect() as connection:
            _assert_connection_scope(connection, expected_database)
            _apply_bridge(
                connection,
                lock_timeout_seconds=lock_timeout_seconds,
            )
    finally:
        engine.dispose()

    _upgrade_to_target(
        database_url,
        lock_timeout_seconds=lock_timeout_seconds,
    )

    verification_engine = _new_engine(database_url)
    try:
        with verification_engine.connect() as connection:
            database = _assert_connection_scope(connection, expected_database)
            final_revision = _current_revision(connection)
            if final_revision != TARGET_REVISION:
                raise ReconciliationError(
                    f"postflight revision is {final_revision!r}, expected {TARGET_REVISION!r}"
                )
            _validate_target_schema(connection)
    finally:
        verification_engine.dispose()

    return {
        "ok": True,
        "mode": "apply",
        "database": database,
        "initial_revision": initial_revision,
        "final_revision": final_revision,
        "target_revision": TARGET_REVISION,
        "applied_actions": plan["planned_actions"],
        "postflight": "passed",
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="reconcile_legacy_game_migration",
        description=(
            "Dry-run or reconcile the historical geo_dev game-library Alembic branch "
            "through committed Collector revision 0073."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="apply the bridge and upgrade to the pinned 0073 target",
    )
    parser.add_argument(
        "--backup-confirmed",
        action="store_true",
        help="confirm that a restorable geo_dev backup was taken and verified",
    )
    parser.add_argument(
        "--lock-timeout-seconds",
        type=int,
        default=30,
        help="bounded MySQL metadata-lock wait used only during apply (default: 30)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.backup_confirmed and not args.apply:
            raise ReconciliationError("--backup-confirmed is meaningful only together with --apply")
        from server.app.core.paths import get_database_url

        result = reconcile_database(
            get_database_url(),
            apply=args.apply,
            backup_confirmed=args.backup_confirmed,
            lock_timeout_seconds=args.lock_timeout_seconds,
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "mode": "apply" if args.apply else "dry-run",
                    "error": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
