from __future__ import annotations

import shutil
import tempfile
import uuid
from pathlib import Path

import pytest
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine, inspect

import server.app.modules.collector.models  # noqa: F401
from alembic import command
from server.app.core.config import get_settings
from server.tests.utils import get_test_database_url, reset_test_database

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


def _new_temp_data_dir() -> Path:
    return Path(tempfile.gettempdir()) / "geo-test-data" / uuid.uuid4().hex


def _unique_columns(inspector, table_name: str) -> set[tuple[str, ...]]:
    return {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints(table_name)
    }


@pytest.mark.mysql
def test_collector_migration_upgrades_downgrades_and_restores_constraints(monkeypatch):
    data_dir = _new_temp_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    engine = create_engine(get_test_database_url(), pool_pre_ping=True)
    reset_test_database(engine, create_schema=False)
    monkeypatch.setenv("GEO_DATA_DIR", str(data_dir))
    monkeypatch.setenv("GEO_DATABASE_URL", get_test_database_url())
    get_settings.cache_clear()

    try:
        cfg = AlembicConfig("alembic.ini")
        command.upgrade(cfg, "head")

        inspector = inspect(engine)
        assert COLLECTOR_TABLES.issubset(inspector.get_table_names())
        assert ("collector_id", "event_id") in _unique_columns(inspector, "collector_events")
        assert ("collector_id", "transport_id") in _unique_columns(inspector, "collector_transfers")
        assert ("transfer_id",) in _unique_columns(inspector, "collector_transfer_receipts")
        assert ("transfer_id", "item_key") in _unique_columns(inspector, "collector_item_receipts")

        credential_columns = {
            column["name"] for column in inspector.get_columns("collector_credentials")
        }
        assert "credential_hash" in credential_columns
        assert {
            "credential",
            "secret",
            "token",
            "api_key",
            "credential_plaintext",
        }.isdisjoint(credential_columns)

        transfer_columns = {
            column["name"] for column in inspector.get_columns("collector_transfers")
        }
        assert {
            "archive_sha256",
            "archive_size",
            "status",
            "ready_at",
            "claimed_by",
            "lease_until",
            "attempt_count",
        }.issubset(transfer_columns)

        command.downgrade(cfg, "0072_planb_discovery_patrol")
        assert COLLECTOR_TABLES.isdisjoint(inspect(engine).get_table_names())

        command.upgrade(cfg, "head")
        assert COLLECTOR_TABLES.issubset(inspect(engine).get_table_names())
    finally:
        reset_test_database(engine, create_schema=False)
        engine.dispose()
        shutil.rmtree(data_dir, ignore_errors=True)
        get_settings.cache_clear()
