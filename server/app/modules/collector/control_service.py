"""Versioned Collector configuration and bounded immutable job claiming."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from server.app.core.time import utcnow
from server.app.modules.collector.models import (
    CollectorConfigVersion,
    CollectorJob,
    CollectorNode,
)
from server.app.modules.game_library.models import Game

_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_SOURCES = {"baidu", "ninegame", "yingyongbao", "taptap"}


class CollectorControlError(RuntimeError):
    """Configuration or job request is invalid or outside the node scope."""


@dataclass(frozen=True, slots=True)
class ConfigurationResult:
    unchanged: bool
    config: CollectorConfigVersion


@dataclass(frozen=True, slots=True)
class JobClaimResult:
    job: CollectorJob | None
    created: bool


def _canonical_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sources(source_order: str) -> list[str]:
    sources = [source.strip() for source in (source_order or "").split(",") if source.strip()]
    if (
        not sources
        or len(sources) != len(set(sources))
        or any(source not in _SOURCES for source in sources)
    ):
        raise CollectorControlError("GEO source_order is invalid")
    return sources


def effective_config_snapshot(config: Any) -> dict[str, Any]:
    """Freeze only effective acquisition settings, excluding volatile run timestamps."""

    from server.app.modules.game_library.planb.yingyongbao import LIST_PATHS

    discovery_paths = list(config.discovery_seed_paths or LIST_PATHS)
    return {
        "schema_version": 1,
        "refresh": {
            "enabled": bool(config.enabled),
            "window_start": config.window_start,
            "window_end": config.window_end,
            "batch_size": int(config.batch_size),
            "min_gap_seconds": int(config.min_gap_seconds),
            "max_gap_seconds": int(config.max_gap_seconds),
            "source_order": _sources(config.source_order),
            "max_shots": int(config.max_shots),
            "patrol_include_main": bool(config.patrol_include_main),
        },
        "discovery": {
            "enabled": bool(config.discovery_enabled),
            "window_start": config.discovery_window_start,
            "window_end": config.discovery_window_end,
            "paths": discovery_paths,
            "detail_limit": int(config.discovery_detail_limit),
            "max_shots": int(config.discovery_max_shots),
            "min_gap_seconds": int(config.discovery_min_gap_seconds),
            "max_gap_seconds": int(config.discovery_max_gap_seconds),
            "source_order": ["yingyongbao"],
        },
    }


def _node(db: Session, *, collector_id: str, destination: str) -> CollectorNode:
    node = cast(
        CollectorNode | None,
        db.scalar(select(CollectorNode).where(CollectorNode.collector_id == collector_id)),
    )
    if node is None or node.status != "enabled" or node.destination != destination:
        raise CollectorControlError("resource is outside collector scope")
    return node


def _require_source_scope(node: CollectorNode, sources: object) -> None:
    if not isinstance(sources, list) or not set(str(source) for source in sources).issubset(
        set(str(source) for source in node.enabled_sources)
    ):
        raise CollectorControlError("job sources are outside collector scope")


def current_config_version(
    db: Session,
    *,
    collector_id: str,
) -> CollectorConfigVersion | None:
    return cast(
        CollectorConfigVersion | None,
        db.scalar(
            select(CollectorConfigVersion)
            .where(CollectorConfigVersion.collector_id == collector_id)
            .order_by(CollectorConfigVersion.version_no.desc())
            .limit(1)
        ),
    )


def sync_effective_configuration(
    db: Session,
    *,
    collector_id: str,
    destination: str,
    max_staleness_seconds: int,
) -> CollectorConfigVersion:
    node = _node(db, collector_id=collector_id, destination=destination)
    if max_staleness_seconds <= 0:
        raise ValueError("max_staleness_seconds must be positive")
    from server.app.modules.game_library.ingest_service import (
        get_or_create_ingest_config,
    )

    snapshot = effective_config_snapshot(get_or_create_ingest_config(db))
    for mode in ("refresh", "discovery"):
        settings = snapshot[mode]
        if settings["enabled"]:
            _require_source_scope(node, settings["source_order"])
    digest = _canonical_sha256(snapshot)
    latest = current_config_version(db, collector_id=collector_id)
    if latest is not None and latest.snapshot_sha256 == digest:
        return latest
    created = CollectorConfigVersion(
        collector_id=collector_id,
        version_no=(latest.version_no + 1 if latest is not None else 1),
        snapshot=snapshot,
        snapshot_sha256=digest,
        max_staleness_seconds=max_staleness_seconds,
    )
    db.add(created)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        winner = current_config_version(db, collector_id=collector_id)
        if winner is None or winner.snapshot_sha256 != digest:
            raise
        return winner
    return created


def read_configuration(
    db: Session,
    *,
    collector_id: str,
    destination: str,
    current_version: int | None,
    max_staleness_seconds: int,
) -> ConfigurationResult:
    config = sync_effective_configuration(
        db,
        collector_id=collector_id,
        destination=destination,
        max_staleness_seconds=max_staleness_seconds,
    )
    return ConfigurationResult(
        unchanged=current_version == config.version_no,
        config=config,
    )


def _load_refresh_targets(
    db: Session,
    *,
    limit: int,
    include_main: bool,
    due_selector: Callable[..., list[int]],
) -> list[dict[str, Any]]:
    due_ids = due_selector(db, limit=limit, include_main=include_main)
    targets: list[dict[str, Any]] = []
    for game_id in due_ids:
        row = db.get(Game, game_id)
        if row is None or row.stock_category_id is None:
            continue
        targets.append(
            {
                "target_game_id": row.id,
                "name": row.name,
                "category_id": row.stock_category_id,
            }
        )
    return targets


def _manifest(
    *,
    job_id: str,
    mode: str,
    destination: str,
    config: CollectorConfigVersion,
    db: Session,
    due_selector: Callable[..., list[int]],
    scheduled_for: datetime,
) -> dict[str, Any] | None:
    snapshot = config.snapshot
    if mode == "refresh":
        settings = snapshot["refresh"]
        if not settings["enabled"]:
            return None
        targets = _load_refresh_targets(
            db,
            limit=settings["batch_size"],
            include_main=settings["patrol_include_main"],
            due_selector=due_selector,
        )
        if not targets:
            return None
        paths: list[dict[str, str]] = []
        source_order = list(settings["source_order"])
        max_media = settings["max_shots"]
        max_targets = settings["batch_size"]
        max_items = max(1, len(targets) * len(source_order))
    else:
        settings = snapshot["discovery"]
        if not settings["enabled"]:
            return None
        targets = []
        paths = [{"source": "yingyongbao", "path": path} for path in settings["paths"]]
        source_order = ["yingyongbao"]
        max_media = settings["max_shots"]
        max_targets = 1
        max_items = max(1, len(paths) * int(settings["detail_limit"]))
    return {
        "schema_version": 1,
        "job_id": job_id,
        "policy_version": f"collector-config-{config.version_no}",
        "mode": mode,
        "source_order": source_order,
        "targets": targets,
        "discovery_paths": paths,
        "limits": {
            "max_targets": max_targets,
            "max_discovery_paths": max(1, len(paths)),
            "max_items": max_items,
            "max_requests": max(1, (len(targets) or len(paths)) * len(source_order) * 4),
            "max_media_per_item": max_media,
            "max_media_bytes": 512 * 1024 * 1024,
        },
        "scheduled_for": scheduled_for.isoformat() + "Z",
        "destination": destination,
    }


def claim_job(
    db: Session,
    *,
    collector_id: str,
    destination: str,
    claim_request_id: str,
    mode: str,
    max_staleness_seconds: int,
    now: datetime | None = None,
    due_selector: Callable[..., list[int]] | None = None,
    id_factory: Callable[[], str] | None = None,
) -> JobClaimResult:
    if _SAFE_ID.fullmatch(claim_request_id) is None:
        raise CollectorControlError("claim_request_id is invalid")
    if mode not in {"refresh", "discovery"}:
        raise CollectorControlError("job mode is invalid")
    node = _node(db, collector_id=collector_id, destination=destination)
    existing = cast(
        CollectorJob | None,
        db.scalar(
            select(CollectorJob).where(
                CollectorJob.collector_id == collector_id,
                CollectorJob.claim_request_id == claim_request_id,
            )
        ),
    )
    if existing is not None:
        if existing.mode != mode or existing.destination != destination:
            raise CollectorControlError("claim request identity conflict")
        _require_source_scope(node, existing.manifest.get("source_order"))
        return JobClaimResult(job=existing, created=False)

    config = sync_effective_configuration(
        db,
        collector_id=collector_id,
        destination=destination,
        max_staleness_seconds=max_staleness_seconds,
    )
    _require_source_scope(
        node,
        config.snapshot.get(mode, {}).get("source_order"),
    )
    if due_selector is None:
        from server.app.modules.game_library.ingest_service import select_due_games

        due_selector = select_due_games
    job_id = (id_factory or (lambda: f"job-{uuid.uuid4().hex}"))()
    scheduled_for = now or utcnow()
    manifest = _manifest(
        job_id=job_id,
        mode=mode,
        destination=destination,
        config=config,
        db=db,
        due_selector=due_selector,
        scheduled_for=scheduled_for,
    )
    if manifest is None:
        return JobClaimResult(job=None, created=False)
    job = CollectorJob(
        collector_id=collector_id,
        job_id=job_id,
        claim_request_id=claim_request_id,
        config_version_id=config.id,
        mode=mode,
        destination=destination,
        manifest=manifest,
        manifest_sha256=_canonical_sha256(manifest),
        status="claimed",
        schedule_occurrence_at=scheduled_for,
        claimed_at=scheduled_for,
    )
    db.add(job)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        winner = cast(
            CollectorJob | None,
            db.scalar(
                select(CollectorJob).where(
                    CollectorJob.collector_id == collector_id,
                    CollectorJob.claim_request_id == claim_request_id,
                )
            ),
        )
        if winner is None or winner.mode != mode or winner.destination != destination:
            raise
        return JobClaimResult(job=winner, created=False)
    return JobClaimResult(job=job, created=True)


def acknowledge_job(
    db: Session,
    *,
    collector_id: str,
    job_id: str,
    manifest_sha256: str,
    now: datetime | None = None,
) -> CollectorJob | None:
    job = cast(
        CollectorJob | None,
        db.scalar(
            select(CollectorJob).where(
                CollectorJob.collector_id == collector_id,
                CollectorJob.job_id == job_id,
            )
        ),
    )
    if job is None:
        return None
    if job.manifest_sha256 != manifest_sha256:
        raise CollectorControlError("job manifest identity conflict")
    if job.status == "claimed":
        job.status = "acknowledged"
        job.acknowledged_at = now or utcnow()
        db.flush()
    elif job.status not in {"acknowledged", "completed", "failed"}:
        raise CollectorControlError(f"job in state {job.status} cannot be acknowledged")
    return job
