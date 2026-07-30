from __future__ import annotations

import shutil
import tempfile
import uuid
from pathlib import Path

import pytest
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from alembic import command
from server.app.core.config import get_settings
from server.scripts.reconcile_legacy_game_migration import (
    LEGACY_REVISION,
    TARGET_REVISION,
    ReconciliationError,
    _create_xhs_table,
    reconcile_database,
)
from server.tests.utils import get_test_database_url, reset_test_database

pytestmark = pytest.mark.mysql


def _new_temp_data_dir() -> Path:
    return Path(tempfile.gettempdir()) / "geo-test-data" / uuid.uuid4().hex


def _simulate_legacy_branch(engine) -> None:
    with engine.connect() as connection:
        connection.exec_driver_sql("DROP INDEX ix_articles_content_type ON articles")
        connection.exec_driver_sql("ALTER TABLE articles DROP COLUMN content_type")
        connection.exec_driver_sql("DROP TABLE xhs_render_jobs")
        result = connection.execute(
            text(
                "UPDATE alembic_version "
                "SET version_num = :legacy "
                "WHERE version_num = '0069_game_cull_and_manual'"
            ),
            {"legacy": LEGACY_REVISION},
        )
        assert result.rowcount == 1
        connection.commit()


def _simulate_completed_bridge_ddl_without_version_update(engine) -> None:
    with engine.connect() as connection:
        _create_xhs_table(connection)
        connection.exec_driver_sql("ALTER TABLE articles ADD COLUMN content_type VARCHAR(40) NULL")
        connection.exec_driver_sql(
            "CREATE INDEX ix_articles_content_type ON articles (content_type)"
        )


def test_apply_requires_explicit_backup_confirmation():
    with pytest.raises(ReconciliationError, match="backup-confirmed"):
        reconcile_database(
            "mysql+pymysql://unused.invalid/geo_dev",
            apply=True,
        )


def test_reconciles_legacy_branch_then_upgrades_to_pinned_target(monkeypatch):
    database_url = get_test_database_url()
    database_name = make_url(database_url).database
    assert database_name is not None

    data_dir = _new_temp_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    engine = create_engine(database_url, pool_pre_ping=True)
    reset_test_database(engine, create_schema=False)
    monkeypatch.setenv("GEO_DATA_DIR", str(data_dir))
    monkeypatch.setenv("GEO_DATABASE_URL", database_url)
    get_settings.cache_clear()

    config = AlembicConfig("alembic.ini")
    try:
        command.upgrade(config, "0069_game_cull_and_manual")
        _simulate_legacy_branch(engine)

        dry_run = reconcile_database(
            database_url,
            apply=False,
            expected_database=database_name,
        )
        assert dry_run["mode"] == "dry-run"
        assert dry_run["current_revision"] == LEGACY_REVISION
        assert dry_run["target_revision"] == TARGET_REVISION
        assert dry_run["planned_actions"] == [
            "create xhs_render_jobs table and indexes (current 0065)",
            "add articles.content_type VARCHAR(40) NULL (current 0066)",
            "create index ix_articles_content_type",
            ("move alembic_version 0067_game_cull_and_manual -> 0069_game_cull_and_manual"),
            "run Alembic upgrade to 0073_collector_gateway_model",
        ]

        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == LEGACY_REVISION
            )
            current_inspector = inspect(connection)
            assert not current_inspector.has_table("xhs_render_jobs")
            assert "content_type" not in {
                column["name"] for column in current_inspector.get_columns("articles")
            }

        # Simulate interruption after MySQL's non-transactional bridge DDL committed but before
        # alembic_version changed. The guarded apply must validate and resume rather than duplicate
        # the table, column, or indexes.
        _simulate_completed_bridge_ddl_without_version_update(engine)

        applied = reconcile_database(
            database_url,
            apply=True,
            backup_confirmed=True,
            expected_database=database_name,
            lock_timeout_seconds=10,
        )
        assert applied["mode"] == "apply"
        assert applied["initial_revision"] == LEGACY_REVISION
        assert applied["final_revision"] == TARGET_REVISION
        assert applied["postflight"] == "passed"

        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == TARGET_REVISION
            )
            final_inspector = inspect(connection)
            assert final_inspector.has_table("xhs_render_jobs")
            assert "content_type" in {
                column["name"] for column in final_inspector.get_columns("articles")
            }
            assert final_inspector.has_table("collector_transfers")

        rerun = reconcile_database(
            database_url,
            apply=False,
            expected_database=database_name,
        )
        assert rerun["current_revision"] == TARGET_REVISION
        assert rerun["planned_actions"] == []
    finally:
        reset_test_database(engine, create_schema=False)
        engine.dispose()
        shutil.rmtree(data_dir, ignore_errors=True)
        get_settings.cache_clear()
