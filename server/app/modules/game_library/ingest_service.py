from __future__ import annotations

import re

from sqlalchemy.orm import Session

from server.app.modules.game_library.models import GameIngestConfig
from server.app.shared.errors import ValidationError

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_WRITABLE = {
    "enabled",
    "window_start",
    "window_end",
    "batch_size",
    "min_gap_seconds",
    "max_gap_seconds",
    "source_order",
    "max_shots",
}


def get_or_create_ingest_config(db: Session) -> GameIngestConfig:
    cfg = db.get(GameIngestConfig, 1)
    if cfg is None:
        cfg = GameIngestConfig(id=1)
        db.add(cfg)
        db.flush()
    return cfg


def update_ingest_config(db: Session, patch: dict) -> GameIngestConfig:
    cfg = get_or_create_ingest_config(db)
    data = {k: v for k, v in (patch or {}).items() if k in _WRITABLE}
    for key in ("window_start", "window_end"):
        if key in data and not _HHMM.match(str(data[key])):
            raise ValidationError(f"{key} 必须是 HH:MM")
    for key in ("batch_size", "min_gap_seconds", "max_gap_seconds", "max_shots"):
        if key in data and int(data[key]) <= 0:
            raise ValidationError(f"{key} 必须为正整数")
    lo = int(data.get("min_gap_seconds", cfg.min_gap_seconds))
    hi = int(data.get("max_gap_seconds", cfg.max_gap_seconds))
    if lo > hi:
        raise ValidationError("min_gap_seconds 不能大于 max_gap_seconds")
    for k, v in data.items():
        setattr(cfg, k, v)
    db.flush()
    return cfg


def _iso(dt):
    return dt.isoformat() if dt else None


def ingest_config_to_dict(cfg, *, running: bool) -> dict:
    return {
        "enabled": cfg.enabled,
        "window_start": cfg.window_start,
        "window_end": cfg.window_end,
        "batch_size": cfg.batch_size,
        "min_gap_seconds": cfg.min_gap_seconds,
        "max_gap_seconds": cfg.max_gap_seconds,
        "source_order": cfg.source_order,
        "max_shots": cfg.max_shots,
        "running": running,
        "last_run_started_at": _iso(cfg.last_run_started_at),
        "last_run_finished_at": _iso(cfg.last_run_finished_at),
        "last_run_summary": cfg.last_run_summary,
        "last_run_trigger": cfg.last_run_trigger,
    }
