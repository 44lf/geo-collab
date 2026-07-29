"""OpenTelemetry-compatible, secret-safe logs for Gateway and Consumer."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from server.app.modules.collector.telemetry import sanitize_event_payload

_URL = re.compile(r"https?://[^\s\"']+", re.IGNORECASE)
_URI_PASSWORD = re.compile(r"(?i)(://[^:/\s]+:)[^@\s]+(@)")
_LEVELS = {"debug", "info", "warning", "error", "critical"}


def _safe_message(value: str) -> str:
    without_password = _URI_PASSWORD.sub(r"\1[REDACTED]\2", value)

    def strip_query(match: re.Match[str]) -> str:
        parsed = urlsplit(match.group(0))
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))

    payload = sanitize_event_payload({"message": _URL.sub(strip_query, without_password)})
    return str((payload or {}).get("message", "[REDACTED]"))


def error_fingerprint(error: BaseException) -> str:
    identity = f"{type(error).__module__}.{type(error).__qualname__}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def collector_log_record(
    *,
    component: str,
    component_version: str,
    stage: str,
    event_type: str,
    level: str,
    message: str,
    collector_id: str | None = None,
    run_id: str | None = None,
    job_id: str | None = None,
    transport_id: str | None = None,
    bundle_id: str | None = None,
    source: str | None = None,
    fields: dict[str, Any] | None = None,
    error: BaseException | None = None,
    occurred_at: datetime | None = None,
) -> dict[str, Any]:
    normalized_level = level.lower()
    if normalized_level not in _LEVELS:
        raise ValueError("unsupported collector log level")
    required = (component, component_version, stage, event_type)
    if any(not value or value != value.strip() for value in required):
        raise ValueError("collector log identity fields are required")
    when = occurred_at or datetime.now(UTC)
    if when.tzinfo is None or when.utcoffset() is None:
        raise ValueError("collector log timestamp must include a UTC offset")
    when = when.astimezone(UTC)
    attributes: dict[str, Any] = {
        "component": component,
        "component.version": component_version,
        "stage": stage,
        "event.type": event_type,
        "collector.id": collector_id,
        "run.id": run_id,
        "job.id": job_id,
        "transport.id": transport_id,
        "bundle.id": bundle_id,
        "source": source,
        **(fields or {}),
    }
    attributes = {key: value for key, value in attributes.items() if value is not None}
    if error is not None:
        attributes["error.type"] = type(error).__name__
        attributes["error.fingerprint"] = error_fingerprint(error)
    return {
        "timestamp": when.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "time_unix_nano": int(when.timestamp() * 1_000_000_000),
        "severity_text": normalized_level.upper(),
        "body": _safe_message(message),
        "attributes": sanitize_event_payload(attributes) or {},
    }
