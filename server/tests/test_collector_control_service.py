from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from server.app.modules.collector.control_service import (
    CollectorControlError,
    acknowledge_job,
    claim_job,
    effective_config_snapshot,
)
from server.app.modules.collector.models import (
    CollectorConfigVersion,
    CollectorJob,
    CollectorNode,
)

NOW = datetime(2026, 7, 29, 9, 0)


def _ingest_config(**changes):
    values = {
        "enabled": True,
        "window_start": "03:00",
        "window_end": "06:00",
        "batch_size": 2,
        "min_gap_seconds": 20,
        "max_gap_seconds": 90,
        "source_order": "baidu,ninegame,taptap",
        "max_shots": 6,
        "patrol_include_main": False,
        "discovery_enabled": True,
        "discovery_window_start": "04:00",
        "discovery_window_end": "06:00",
        "discovery_seed_paths": ["/hot-game-list"],
        "discovery_detail_limit": 15,
        "discovery_max_shots": 6,
        "discovery_min_gap_seconds": 20,
        "discovery_max_gap_seconds": 90,
        "last_run_started_at": NOW,
    }
    values.update(changes)
    return SimpleNamespace(**values)


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


def _config() -> CollectorConfigVersion:
    snapshot = effective_config_snapshot(_ingest_config())
    return CollectorConfigVersion(
        id=7,
        collector_id="collector-local-1",
        version_no=3,
        snapshot=snapshot,
        snapshot_sha256="a" * 64,
        max_staleness_seconds=3600,
    )


def test_effective_config_is_versionable_and_excludes_volatile_run_state():
    first = effective_config_snapshot(_ingest_config(last_run_started_at=NOW))
    second = effective_config_snapshot(
        _ingest_config(last_run_started_at=datetime(2026, 7, 30, 9, 0))
    )

    assert first == second
    assert first["refresh"]["source_order"] == ["baidu", "ninegame", "taptap"]
    assert first["discovery"]["paths"] == ["/hot-game-list"]
    assert "last_run_started_at" not in repr(first)


def test_refresh_claim_uses_geo_due_selection_and_returns_immutable_manifest(monkeypatch):
    db = MagicMock()
    db.scalar.side_effect = [_node(), None]
    games = {
        101: SimpleNamespace(id=101, name="Game A", stock_category_id=9),
        102: SimpleNamespace(id=102, name="Game B", stock_category_id=10),
    }
    db.get.side_effect = lambda model, identity: games.get(identity)
    monkeypatch.setattr(
        "server.app.modules.collector.control_service.sync_effective_configuration",
        lambda *args, **kwargs: _config(),
    )

    result = claim_job(
        db,
        collector_id="collector-local-1",
        destination="geo-production",
        claim_request_id="claim-1",
        mode="refresh",
        max_staleness_seconds=3600,
        now=NOW,
        due_selector=lambda db, **kwargs: [101, 102],
        id_factory=lambda: "job-fixed",
    )

    assert result.created is True
    assert result.job is not None
    assert result.job.claim_request_id == "claim-1"
    assert result.job.status == "claimed"
    assert result.job.manifest["job_id"] == "job-fixed"
    assert result.job.manifest["targets"] == [
        {"target_game_id": 101, "name": "Game A", "category_id": 9},
        {"target_game_id": 102, "name": "Game B", "category_id": 10},
    ]
    assert result.job.manifest["source_order"] == ["baidu", "ninegame", "taptap"]
    assert "database" not in repr(result.job.manifest).lower()
    db.add.assert_called_once_with(result.job)
    db.flush.assert_called_once()


def test_discovery_claim_is_bounded_and_repeated_claim_request_is_idempotent(
    monkeypatch,
):
    existing = CollectorJob(
        collector_id="collector-local-1",
        job_id="job-existing",
        claim_request_id="claim-1",
        config_version_id=7,
        mode="discovery",
        destination="geo-production",
        manifest={"immutable": True, "source_order": ["yingyongbao"]},
        manifest_sha256="b" * 64,
        status="claimed",
    )
    db = MagicMock()
    db.scalar.side_effect = [_node(), existing]
    due_selector = MagicMock()

    result = claim_job(
        db,
        collector_id="collector-local-1",
        destination="geo-production",
        claim_request_id="claim-1",
        mode="discovery",
        max_staleness_seconds=3600,
        due_selector=due_selector,
    )

    assert result.job is existing
    assert result.created is False
    due_selector.assert_not_called()
    db.add.assert_not_called()


def test_discovery_claim_uses_only_approved_geo_paths_without_database_credentials(
    monkeypatch,
):
    db = MagicMock()
    db.scalar.side_effect = [_node(), None]
    monkeypatch.setattr(
        "server.app.modules.collector.control_service.sync_effective_configuration",
        lambda *args, **kwargs: _config(),
    )

    result = claim_job(
        db,
        collector_id="collector-local-1",
        destination="geo-production",
        claim_request_id="claim-discovery-1",
        mode="discovery",
        max_staleness_seconds=3600,
        now=NOW,
        due_selector=MagicMock(),
        id_factory=lambda: "job-discovery-fixed",
    )

    assert result.created is True
    assert result.job is not None
    assert result.job.manifest["discovery_paths"] == [
        {"source": "yingyongbao", "path": "/hot-game-list"}
    ]
    rendered = repr(result.job.manifest).lower()
    assert "mysql" not in rendered
    assert "password" not in rendered
    assert "minio_access" not in rendered


def test_job_claim_rejects_sources_outside_node_scope(monkeypatch):
    db = MagicMock()
    db.scalar.side_effect = [
        _node(enabled_sources=["baidu"]),
        None,
    ]
    monkeypatch.setattr(
        "server.app.modules.collector.control_service.sync_effective_configuration",
        lambda *args, **kwargs: _config(),
    )

    with pytest.raises(CollectorControlError, match="sources are outside"):
        claim_job(
            db,
            collector_id="collector-local-1",
            destination="geo-production",
            claim_request_id="claim-1",
            mode="refresh",
            max_staleness_seconds=3600,
            now=NOW,
        )

    db.add.assert_not_called()


def test_same_claim_request_cannot_change_mode_or_destination():
    existing = CollectorJob(
        collector_id="collector-local-1",
        job_id="job-existing",
        claim_request_id="claim-1",
        config_version_id=7,
        mode="refresh",
        destination="geo-production",
        manifest={},
        manifest_sha256="b" * 64,
        status="claimed",
    )
    db = MagicMock()
    db.scalar.side_effect = [_node(), existing]

    with pytest.raises(CollectorControlError, match="identity conflict"):
        claim_job(
            db,
            collector_id="collector-local-1",
            destination="geo-production",
            claim_request_id="claim-1",
            mode="discovery",
            max_staleness_seconds=3600,
        )


def test_job_ack_is_hash_bound_and_idempotent():
    job = CollectorJob(
        collector_id="collector-local-1",
        job_id="job-1",
        claim_request_id="claim-1",
        config_version_id=7,
        mode="refresh",
        destination="geo-production",
        manifest={},
        manifest_sha256="c" * 64,
        status="claimed",
    )
    db = MagicMock()
    db.scalar.return_value = job

    acknowledged = acknowledge_job(
        db,
        collector_id="collector-local-1",
        job_id="job-1",
        manifest_sha256="c" * 64,
        now=NOW,
    )
    repeated = acknowledge_job(
        db,
        collector_id="collector-local-1",
        job_id="job-1",
        manifest_sha256="c" * 64,
        now=NOW,
    )

    assert acknowledged is job
    assert repeated is job
    assert job.status == "acknowledged"
    assert job.acknowledged_at == NOW
    db.flush.assert_called_once()

    with pytest.raises(CollectorControlError, match="manifest identity"):
        acknowledge_job(
            db,
            collector_id="collector-local-1",
            job_id="job-1",
            manifest_sha256="d" * 64,
            now=NOW,
        )


@pytest.mark.parametrize("terminal_status", ["completed", "failed"])
def test_terminal_job_ack_replay_is_a_noop(terminal_status):
    job = CollectorJob(
        collector_id="collector-local-1",
        job_id="job-1",
        claim_request_id="claim-1",
        config_version_id=7,
        mode="refresh",
        destination="geo-production",
        manifest={},
        manifest_sha256="c" * 64,
        status=terminal_status,
    )
    db = MagicMock()
    db.scalar.return_value = job

    replay = acknowledge_job(
        db,
        collector_id="collector-local-1",
        job_id="job-1",
        manifest_sha256="c" * 64,
        now=NOW,
    )

    assert replay is job
    db.flush.assert_not_called()
