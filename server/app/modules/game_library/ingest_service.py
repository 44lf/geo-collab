from __future__ import annotations

from sqlalchemy.orm import Session

from server.app.modules.game_library.models import GameIngestConfig


def get_or_create_ingest_config(db: Session) -> GameIngestConfig:
    cfg = db.get(GameIngestConfig, 1)
    if cfg is None:
        cfg = GameIngestConfig(id=1)
        db.add(cfg)
        db.flush()
    return cfg
