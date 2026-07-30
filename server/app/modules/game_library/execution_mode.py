"""Mutually exclusive startup boundary for in-process and external game ingestion."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from server.app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

Starter = Callable[[Any], Any]


def start_game_ingest_schedulers(
    session_factory: Any,
    *,
    settings: Settings | None = None,
    refresh_starter: Starter | None = None,
    discovery_starter: Starter | None = None,
) -> dict[str, str]:
    """Start legacy GEO schedulers only when ``in_process`` mode is selected.

    The explicit dependency parameters keep startup behavior testable without starting threads.
    Each legacy scheduler remains best-effort and failure-isolated, matching the previous
    ``create_app`` behavior.
    """

    effective = settings or get_settings()
    if effective.game_ingest_execution_mode == "external":
        logger.info("game ingest execution mode is external; in-process schedulers are disabled")
        return {"mode": "external", "refresh": "disabled", "discovery": "disabled"}

    if refresh_starter is None:
        from server.app.modules.game_library.scheduler import start_game_ingest

        refresh_starter = start_game_ingest
    if discovery_starter is None:
        from server.app.modules.game_library.planb.discovery_scheduler import start_game_discovery

        discovery_starter = start_game_discovery

    states = {"mode": "in_process", "refresh": "started", "discovery": "started"}
    try:
        refresh_starter(session_factory)
    except Exception:
        states["refresh"] = "failed"
        logger.exception("start_game_ingest failed")
    try:
        discovery_starter(session_factory)
    except Exception:
        states["discovery"] = "failed"
        logger.exception("start_game_discovery failed")
    return states
