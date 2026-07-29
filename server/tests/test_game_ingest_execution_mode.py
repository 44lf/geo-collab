from __future__ import annotations

import pytest
from pydantic import ValidationError

from server.app.core.config import Settings
from server.app.modules.game_library.execution_mode import start_game_ingest_schedulers


def test_game_ingest_execution_mode_defaults_to_in_process():
    assert Settings().game_ingest_execution_mode == "in_process"


def test_game_ingest_execution_mode_rejects_unknown_value():
    with pytest.raises(ValidationError):
        Settings(game_ingest_execution_mode="both")


def test_external_mode_starts_no_in_process_scheduler():
    calls: list[str] = []

    result = start_game_ingest_schedulers(
        object(),
        settings=Settings(game_ingest_execution_mode="external"),
        refresh_starter=lambda _factory: calls.append("refresh"),
        discovery_starter=lambda _factory: calls.append("discovery"),
    )

    assert calls == []
    assert result == {"mode": "external", "refresh": "disabled", "discovery": "disabled"}


def test_in_process_mode_preserves_both_existing_schedulers():
    calls: list[str] = []

    result = start_game_ingest_schedulers(
        object(),
        settings=Settings(game_ingest_execution_mode="in_process"),
        refresh_starter=lambda _factory: calls.append("refresh"),
        discovery_starter=lambda _factory: calls.append("discovery"),
    )

    assert calls == ["refresh", "discovery"]
    assert result == {"mode": "in_process", "refresh": "started", "discovery": "started"}


def test_in_process_scheduler_failures_are_isolated(caplog):
    calls: list[str] = []

    def fail_refresh(_factory):
        calls.append("refresh")
        raise RuntimeError("refresh failed")

    result = start_game_ingest_schedulers(
        object(),
        settings=Settings(),
        refresh_starter=fail_refresh,
        discovery_starter=lambda _factory: calls.append("discovery"),
    )

    assert calls == ["refresh", "discovery"]
    assert result == {"mode": "in_process", "refresh": "failed", "discovery": "started"}
    assert "start_game_ingest failed" in caplog.text
