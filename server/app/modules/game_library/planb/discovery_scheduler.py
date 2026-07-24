"""扩库（应用宝榜单发现）定时 + 手动调度。

与补全巡检（`scheduler.py`）平行、独立进程内锁：补全一晚逐个游戏刷；扩库是**整批**操作
（一次 discover() 爬完所有种子页+详情），故语义是**每个时间窗跑一批**（当天窗内只跑一次）。

env 总闸复用 `GEO_GAME_INGEST_SCHEDULER_ENABLED`（与补全同一开关起线程）；DB
`game_ingest_config.discovery_enabled` 每 tick 重读，细控是否真干活。窗口纯函数复用 `scheduler`。
**仅建议单 web 进程跑**（窗口/进程内锁不跨进程去重，多实例会重复爬取、浪费请求）。
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
from collections.abc import Callable
from typing import Any
from zoneinfo import ZoneInfo

from server.app.core.config import get_settings
from server.app.core.time import utcnow
from server.app.modules.game_library import ingest_service, scheduler
from server.app.modules.game_library.planb import db_ingest

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], Any]

_thread: threading.Thread | None = None
_stop = threading.Event()

_run_lock = threading.Lock()
_running = False


def is_discovery_running() -> bool:
    return _running


def _try_acquire() -> bool:
    global _running
    with _run_lock:
        if _running:
            return False
        _running = True
        return True


def _release() -> None:
    global _running
    with _run_lock:
        _running = False


def _run_discovery_batch(session_factory: SessionFactory, *, trigger: str) -> None:
    """跑一整批扩库。调用方已置 `_running=True`，这里只在 finally 释放。"""
    try:
        db = session_factory()
        try:
            cfg = ingest_service.get_or_create_ingest_config(db)
            cfg.discovery_last_run_started_at = utcnow()
            cfg.discovery_last_run_trigger = trigger
            cfg.discovery_last_run_finished_at = None
            db.commit()
            seed_paths = cfg.discovery_seed_paths or None
            detail_limit = cfg.discovery_detail_limit
            max_shots = cfg.discovery_max_shots
            min_interval = float(cfg.discovery_min_gap_seconds)
        finally:
            db.close()

        summary = db_ingest.ingest_discovery(
            session_factory,
            seed_paths=seed_paths,
            with_detail=True,
            detail_limit=detail_limit,
            max_shots=max_shots,
            min_interval=min_interval,
            trigger=trigger,
        )

        db = session_factory()
        try:
            cfg = ingest_service.get_or_create_ingest_config(db)
            cfg.discovery_last_run_finished_at = utcnow()
            cfg.discovery_last_run_summary = summary
            db.commit()
        finally:
            db.close()
    except Exception:
        logger.exception("planb discovery batch failed")
    finally:
        _release()


def start_configured_discovery(session_factory: SessionFactory, *, trigger: str = "manual") -> bool:
    """立即扩库一批，异步后台线程跑。忙（定时或另一手动批次在跑）→ 不重入，返回 False。"""
    if not _try_acquire():
        return False
    threading.Thread(
        target=_run_discovery_batch,
        args=(session_factory,),
        kwargs={"trigger": trigger},
        daemon=True,
        name="game-discovery-manual",
    ).start()
    return True


def start_game_discovery(session_factory: SessionFactory) -> bool:
    """按 env 总闸启动扩库定时守护线程；每 tick 重读 DB discovery_enabled + 窗口。已在跑 → False。"""
    global _thread
    if not get_settings().game_ingest_scheduler_enabled:
        return False
    if _thread is not None and _thread.is_alive():
        return False
    _stop.clear()

    def _loop() -> None:
        last_window: dt.datetime | None = None
        while not _stop.is_set():
            try:
                tz = ZoneInfo(get_settings().scheduler_tz)
                now_local = dt.datetime.now(tz)
                db = session_factory()
                try:
                    cfg = ingest_service.get_or_create_ingest_config(db)
                    enabled = cfg.discovery_enabled
                    start = scheduler.parse_hhmm(cfg.discovery_window_start)
                    end = scheduler.parse_hhmm(cfg.discovery_window_end)
                finally:
                    db.close()
                if enabled and scheduler.in_window(start, end, now_local):
                    win = scheduler.window_start_instant(start, now_local)
                    if win != last_window and _try_acquire():
                        # 本 tick 已抢到锁；_run_discovery_batch 自身 finally 会 _release。
                        _run_discovery_batch(session_factory, trigger="scheduled")
                        last_window = win
            except Exception:
                logger.exception("planb discovery tick failed")
            if _stop.wait(float(get_settings().game_ingest_poll_seconds)):
                break

    _thread = threading.Thread(target=_loop, daemon=True, name="game-discovery")
    _thread.start()
    return True


def stop_game_discovery() -> None:
    _stop.set()
