"""游戏库定时入库：run_ingest_once 纯函数 + 后台守护线程。"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from typing import Any

from server.app.core.config import get_settings
from server.app.modules.game_library import registry, service
from server.app.modules.game_library.sources import taptap

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], Any]

_thread: threading.Thread | None = None
_stop = threading.Event()

SEED_TARGETS = [
    {"source": "baidu", "category": "经营", "pages": 2, "max_games": 30, "max_shots": 6},
    {"source": "taptap", "category": "养成", "pages": 2, "max_games": 30, "max_shots": 6},
    {"source": "taptap", "category": "国风", "pages": 2, "max_games": 30, "max_shots": 6},
]


def _load_targets() -> list[dict]:
    raw = (get_settings().game_ingest_targets or "").strip()
    if not raw:
        return SEED_TARGETS
    try:
        loaded = json.loads(raw)
        return loaded if isinstance(loaded, list) else SEED_TARGETS
    except Exception:
        logger.exception("GEO_GAME_INGEST_TARGETS 解析失败，回落种子清单")
        return SEED_TARGETS


def run_ingest_once(session_factory: SessionFactory, *, targets: list[dict] | None = None) -> dict:
    """扫一轮入库目标。逐目标隔离（一个失败不影响其它）。"""
    active_targets = targets if targets is not None else _load_targets()
    upserted = 0
    failed = 0
    for target in active_targets:
        source = target["source"]
        category = target["category"]
        max_games = int(target.get("max_games", 30))
        max_shots = int(target.get("max_shots", 6))
        db = session_factory()
        try:
            pool = registry.collect_pool(source, category, max_games)
            seen: set[tuple[str, str]] = set()
            for game in pool:
                key = (game.source, game.game_id)
                if key in seen:
                    continue
                seen.add(key)
                if source == "taptap" and not game.screenshot_urls:
                    try:
                        game = taptap.get_detail(game.game_id)
                    except Exception:
                        logger.info("taptap get_detail 失败 game_id=%s", game.game_id)
                        continue
                service.upsert_game(db, game, max_screenshots=max_shots)
                db.commit()
                upserted += 1
        except Exception:
            failed += 1
            db.rollback()
            logger.exception("入库目标失败 source=%s category=%s", source, category)
        finally:
            db.close()
    return {"targets": len(active_targets), "upserted": upserted, "failed": failed}


def start_game_ingest(session_factory: SessionFactory) -> bool:
    global _thread
    if not get_settings().game_ingest_scheduler_enabled:
        return False
    if _thread is not None and _thread.is_alive():
        return False

    _stop.clear()

    def _loop() -> None:
        while not _stop.is_set():
            interval = max(300, get_settings().game_ingest_interval_seconds)
            if _stop.wait(interval):
                break
            try:
                logger.info("game-ingest round: %s", run_ingest_once(session_factory))
            except Exception:
                logger.exception("game-ingest round failed")

    _thread = threading.Thread(target=_loop, daemon=True, name="game-ingest")
    _thread.start()
    return True


def stop_game_ingest() -> None:
    _stop.set()
