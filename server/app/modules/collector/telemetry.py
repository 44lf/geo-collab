"""Bounded, idempotent, secret-safe Collector heartbeat and event intake."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.app.core.time import utcnow
from server.app.modules.collector.models import CollectorEvent, CollectorNode

_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_SOURCES = {"baidu", "ninegame", "yingyongbao", "taptap"}
_LEVELS = {"debug", "info", "warning", "error", "critical"}
_SENSITIVE_KEYS = (
    "authorization",
    "credential",
    "secret",
    "token",
    "cookie",
    "xsrf",
    "presigned",
    "upload_url",
    "raw_body",
    "request_body",
    "response_body",
)
_BEARER = re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;]+")
_URI_USERINFO = re.compile(r"(?i)([a-z][a-z0-9+.-]*://)([^/@\s]+@)")
_HTTP_URL = re.compile(r"https?://[^\s]+", re.IGNORECASE)


class TelemetryValidationError(RuntimeError):
    """Telemetry input is invalid, out of scope, or exceeds intake bounds."""


def _bounded_text(label: str, value: str, *, maximum: int) -> None:
    if not value or value != value.strip() or len(value) > maximum:
        raise TelemetryValidationError(f"{label} must be bounded non-empty trimmed text")


def _optional_id(label: str, value: str | None) -> None:
    if value is not None and _SAFE_ID.fullmatch(value) is None:
        raise TelemetryValidationError(f"{label} is not a safe bounded identifier")


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise TelemetryValidationError("event timestamp must include a UTC offset")
    return value.astimezone(UTC).replace(tzinfo=None)


@dataclass(frozen=True, slots=True)
class HeartbeatInput:
    platform: str
    agent_version: str
    enabled_sources: tuple[str, ...]
    current_run_id: str | None
    current_stage: str
    spool_pending_count: int

    def __post_init__(self) -> None:
        _bounded_text("platform", self.platform, maximum=64)
        _bounded_text("agent_version", self.agent_version, maximum=64)
        _bounded_text("current_stage", self.current_stage, maximum=64)
        _optional_id("current_run_id", self.current_run_id)
        if (
            len(self.enabled_sources) > len(_SOURCES)
            or len(self.enabled_sources) != len(set(self.enabled_sources))
            or any(source not in _SOURCES for source in self.enabled_sources)
        ):
            raise TelemetryValidationError("enabled_sources are invalid")
        if self.spool_pending_count < 0:
            raise TelemetryValidationError("spool_pending_count must be non-negative")


@dataclass(frozen=True, slots=True)
class CollectorEventInput:
    event_id: str
    run_id: str | None
    job_id: str | None
    transport_id: str | None
    bundle_id: str | None
    source: str | None
    component: str
    component_version: str
    stage: str
    event_type: str
    level: str
    payload: dict[str, Any] | None
    occurred_at: datetime

    def __post_init__(self) -> None:
        _optional_id("event_id", self.event_id)
        if not self.event_id:
            raise TelemetryValidationError("event_id is required")
        for label, value in (
            ("run_id", self.run_id),
            ("job_id", self.job_id),
            ("transport_id", self.transport_id),
            ("bundle_id", self.bundle_id),
        ):
            _optional_id(label, value)
        if self.source is not None and self.source not in _SOURCES:
            raise TelemetryValidationError("event source is invalid")
        for label, value, maximum in (
            ("component", self.component, 32),
            ("component_version", self.component_version, 64),
            ("stage", self.stage, 64),
            ("event_type", self.event_type, 64),
        ):
            _bounded_text(label, value, maximum=maximum)
        if self.level not in _LEVELS:
            raise TelemetryValidationError("event level is invalid")
        _naive_utc(self.occurred_at)


@dataclass(frozen=True, slots=True)
class EventBatchResult:
    accepted_event_ids: tuple[str, ...]
    duplicate_event_ids: tuple[str, ...]


def _strip_query(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "[REDACTED]"
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return _BEARER.sub(r"\1[REDACTED]", value)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def sanitize_log_text(value: str) -> str:
    """Remove bearer tokens, URI credentials, and URL query secrets from text."""
    clean = _BEARER.sub(r"\1[REDACTED]", value)
    clean = _URI_USERINFO.sub(r"\1[REDACTED]@", clean)

    def strip_match(match: re.Match[str]) -> str:
        raw = match.group(0)
        trailing = ""
        while raw and raw[-1] in ".,;:)]}":
            trailing = raw[-1] + trailing
            raw = raw[:-1]
        return _strip_query(raw) + trailing

    return _HTTP_URL.sub(strip_match, clean)


def _sanitize(value: Any, *, key: str | None = None, depth: int = 0) -> Any:
    if depth > 8:
        return "[TRUNCATED]"
    lowered = (key or "").lower()
    if any(part in lowered for part in _SENSITIVE_KEYS):
        return None
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for raw_key, item in value.items():
            item_key = str(raw_key)
            clean = _sanitize(item, key=item_key, depth=depth + 1)
            if clean is not None:
                sanitized[item_key] = clean
        return sanitized
    if isinstance(value, (list, tuple)):
        return [clean for item in value if (clean := _sanitize(item, depth=depth + 1)) is not None]
    if isinstance(value, str):
        if lowered.endswith("url"):
            return _strip_query(value)
        return sanitize_log_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return f"[{type(value).__name__}]"


def sanitize_event_payload(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if payload is None:
        return None
    sanitized = _sanitize(payload)
    if not isinstance(sanitized, dict):
        raise TelemetryValidationError("event payload must be an object")
    return sanitized


def record_heartbeat(
    db: Session,
    *,
    collector_id: str,
    destination: str,
    heartbeat: HeartbeatInput,
    received_at: datetime | None = None,
) -> CollectorNode:
    node = cast(
        CollectorNode | None,
        db.scalar(select(CollectorNode).where(CollectorNode.collector_id == collector_id)),
    )
    if node is None or node.status != "enabled" or node.destination != destination:
        raise TelemetryValidationError("resource is outside collector scope")
    now = received_at or utcnow()
    node.platform = heartbeat.platform
    node.agent_version = heartbeat.agent_version
    node.enabled_sources = list(heartbeat.enabled_sources)
    node.current_run_id = heartbeat.current_run_id
    node.current_stage = heartbeat.current_stage
    node.spool_pending_count = heartbeat.spool_pending_count
    node.last_heartbeat_at = now
    db.flush()
    return node


def ingest_event_batch(
    db: Session,
    *,
    collector_id: str,
    events: list[CollectorEventInput],
    received_at: datetime | None = None,
    max_events: int,
    max_payload_bytes: int,
) -> EventBatchResult:
    if not 1 <= max_events <= 100 or len(events) > max_events:
        raise TelemetryValidationError("event batch limit exceeded")
    if max_payload_bytes <= 0:
        raise ValueError("max_payload_bytes must be positive")
    if not events:
        return EventBatchResult(accepted_event_ids=(), duplicate_event_ids=())

    event_ids = [event.event_id for event in events]
    existing = set(
        db.scalars(
            select(CollectorEvent.event_id).where(
                CollectorEvent.collector_id == collector_id,
                CollectorEvent.event_id.in_(event_ids),
            )
        ).all()
    )
    seen = set(existing)
    accepted: list[str] = []
    duplicates: list[str] = []
    now = received_at or utcnow()
    for event in events:
        if event.event_id in seen:
            if event.event_id not in duplicates:
                duplicates.append(event.event_id)
            continue
        payload = sanitize_event_payload(event.payload)
        payload_bytes = len(
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        if payload_bytes > max_payload_bytes:
            raise TelemetryValidationError("event payload exceeds byte limit")
        db.add(
            CollectorEvent(
                collector_id=collector_id,
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
                payload=payload,
                occurred_at=_naive_utc(event.occurred_at),
                received_at=now,
            )
        )
        seen.add(event.event_id)
        accepted.append(event.event_id)
    db.flush()
    return EventBatchResult(
        accepted_event_ids=tuple(accepted),
        duplicate_event_ids=tuple(duplicates),
    )


def node_freshness(
    node: CollectorNode,
    *,
    now: datetime,
    stale_after: timedelta,
) -> str:
    if stale_after.total_seconds() <= 0:
        raise ValueError("stale_after must be positive")
    if node.last_heartbeat_at is None:
        return "never_seen"
    return "online" if node.last_heartbeat_at + stale_after > now else "stale"
