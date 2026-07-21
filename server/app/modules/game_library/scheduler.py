"""游戏库入库：手动种子(genre) run_ingest_once + config-driven 托管抓取(按名刷新迁移集)。

两条互不干扰的路径：
- **手动种子(genre-crawl)**：`run_ingest_once` / `_load_targets` / `SEED_TARGETS` —— 按体裁关键词
  发现新游戏，只被 `server/scripts/ingest_games.py` CLI 手动调用，不再被定时线程读取(退役休眠)。
- **config-driven 托管抓取(按名刷新)**：`start_game_ingest` 启动的后台守护线程，镜像
  `accounts/keepalive.py` 的窗口 + 软 LRU + 有界随机 gap 结构。DB 单例表 `game_ingest_config`
  (`ingest_service.get_or_create_ingest_config`)控制 enabled / 时间窗 / 每晚数量 / gap /
  数据源顺序 / 截图上限；`ingest_service.select_due_games` 挑最久没刷的 companion 游戏，
  `ingest_service.refresh_one_game` 按名重查 + 合并落库。另有 `start_configured_ingest` 供
  「立即抓一批」手动触发(绕过时间窗，与定时 tick 共享进程内锁互斥)。

  **仅建议单 web 进程跑**：窗口 / 软 LRU / 进程内锁都不跨进程去重，多实例会各起一份线程、
  重复爬取(`upsert_game`/`refresh_one_game` 幂等无害，但浪费请求)，与 `sync_scheduler.py`
  同样的约束(CLAUDE.md 已记录)。
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import random
import threading
import time
from collections.abc import Callable
from typing import Any
from zoneinfo import ZoneInfo

from server.app.core.config import get_settings
from server.app.core.time import utcnow
from server.app.modules.game_library import ingest_service, registry, service
from server.app.modules.game_library.sources import taptap

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], Any]

_thread: threading.Thread | None = None
_stop = threading.Event()
_TAPTAP_DETAIL_THROTTLE_SECONDS = 0.3

# 尝试上限 = batch_size × 此倍数。搜不到的游戏不占名额（只有 refreshed 计入 batch_size），
# 但整池都搜不到时用它兜底，避免一晚把全库刷一遍、猛打源站。
_ATTEMPT_MULTIPLIER = 4

# 进程内锁：定时 tick 与手动「立即抓一批」互斥，同一时刻只允许一条抓取活动在跑。
_run_lock = threading.Lock()
_running = False

SEED_TARGETS = [
    {"source": "baidu", "category": "经营", "max_games": 30, "max_shots": 6},
    {"source": "taptap", "category": "养成", "max_games": 30, "max_shots": 6},
    {"source": "taptap", "category": "国风", "max_games": 30, "max_shots": 6},
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
    """扫一轮入库目标(按体裁发现，手动种子路径)。target/game 双层隔离，一个失败不影响后续。"""
    active_targets = targets if targets is not None else _load_targets()
    upserted = 0
    failed = 0
    for target in active_targets:
        try:
            source = target["source"]
            category = target["category"]
            max_games = int(target.get("max_games", 30))
            max_shots = int(target.get("max_shots", 6))
        except Exception:
            failed += 1
            logger.exception("入库目标配置失败 target=%s", target)
            continue

        db = session_factory()
        try:
            pool = registry.collect_pool(source, category, max_games)
        except Exception:
            failed += 1
            db.rollback()
            logger.exception("入库目标失败 source=%s category=%s", source, category)
            db.close()
            continue

        seen: set[tuple[str, str]] = set()
        detail_requests = 0
        try:
            for game in pool:
                key = (game.source, game.game_id)
                if key in seen:
                    continue
                seen.add(key)
                try:
                    if source == "taptap" and not game.screenshot_urls:
                        if detail_requests > 0:
                            time.sleep(_TAPTAP_DETAIL_THROTTLE_SECONDS)
                        detail_requests += 1
                        game = taptap.get_detail(game.game_id)
                    service.upsert_game(db, game, max_screenshots=max_shots)
                    db.commit()
                    upserted += 1
                except Exception:
                    failed += 1
                    db.rollback()
                    logger.exception(
                        "入库游戏失败 source=%s category=%s game_id=%s",
                        source,
                        category,
                        game.game_id,
                    )
        finally:
            db.close()
    return {"targets": len(active_targets), "upserted": upserted, "failed": failed}


# ---------------------------------------------------------------------------
# 窗口 / 有界随机间隔纯函数 —— 照搬 accounts/keepalive.py 同构，改名贴合游戏库语境。
# ---------------------------------------------------------------------------


def parse_hhmm(value: str) -> dt.time:
    hh, mm = value.split(":")
    return dt.time(hour=int(hh), minute=int(mm))


def _to_utc_naive(local_dt: dt.datetime) -> dt.datetime:
    return local_dt.astimezone(dt.UTC).replace(tzinfo=None)


def in_window(start: dt.time, end: dt.time, now: dt.datetime) -> bool:
    """now 落在 [start, end] 内（end<start 视为跨午夜）。"""
    t = now.timetz().replace(tzinfo=None)
    if start <= end:
        return start <= t <= end
    return t >= start or t <= end  # 跨午夜


def window_start_instant(start: dt.time, now_local: dt.datetime) -> dt.datetime:
    """本窗口起点：<= now 的最近一次 start 出现时刻（今天或昨天），返回 UTC-naive。"""
    candidate = now_local.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
    if candidate > now_local:
        candidate -= dt.timedelta(days=1)
    return _to_utc_naive(candidate)


def window_end_instant(end: dt.time, now_local: dt.datetime) -> dt.datetime:
    """本窗口止点：> now 的最近一次 end 出现时刻（今天或明天），返回 UTC-naive。"""
    candidate = now_local.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
    if candidate <= now_local:
        candidate += dt.timedelta(days=1)
    return _to_utc_naive(candidate)


def compute_next_gap(
    remaining_window_s: float,
    remaining_due: int,
    min_gap: float,
    max_gap: float,
    rng: random.Random,
) -> float:
    """窗口内下一个游戏前的随机间隔（从上一个巡检完成后计时）。

    cap = 剩余窗口 / 剩余待刷数：游戏多→cap 小→上界压缩→当晚刷完；游戏少→上界放到 max_gap。
    cap < min_gap（窗口收尾 / 数量过多 / 剩余窗口为负）时退化为恒定 min_gap，连刷。
    """
    cap = max(0.0, remaining_window_s) / max(1, remaining_due)
    hi = min(max_gap, max(min_gap, cap))
    return rng.uniform(min_gap, hi)


# ---------------------------------------------------------------------------
# 进程内锁：定时 tick 与手动批次互斥。
# ---------------------------------------------------------------------------


def is_configured_ingest_running() -> bool:
    return _running


def _try_acquire_running() -> bool:
    global _running
    with _run_lock:
        if _running:
            return False
        _running = True
        return True


def _release_running() -> None:
    global _running
    with _run_lock:
        _running = False


# ---------------------------------------------------------------------------
# 运行日志：一轮抓取(手动批次 / 定时窗)结果落一条 report_events(source_module=game_ingest)，
# payload 带按结果分组的游戏名列表，供前端「运行日志」展示 —— 每轮一条，明细进 payload。
# ---------------------------------------------------------------------------

_LOG_BUCKETS = ("refreshed", "not_found", "error", "culled")


def _blank_log() -> dict[str, list]:
    return {b: [] for b in _LOG_BUCKETS}


def _append_log(log: dict[str, list], result: dict) -> None:
    outcome = result.get("outcome")
    bucket = outcome if outcome in _LOG_BUCKETS else "error"
    log[bucket].append({"id": result.get("game_id"), "name": result.get("name")})


def _emit_run_log(
    session_factory: SessionFactory,
    *,
    trigger: str,
    event_type: str,
    kind_label: str,
    log: dict[str, list],
    attempts: int,
    cap_reached: bool,
) -> None:
    """一轮结果非空才写一条 report_event(空轮不写)。刷新=录入、跳过=not_found、删除=culled。"""
    if not any(log.values()):
        return
    counts = {b: len(log[b]) for b in _LOG_BUCKETS}
    counts["attempts"] = attempts
    counts["cap_reached"] = cap_reached
    tail = "，已达尝试上限" if cap_reached else ""
    message = (
        f"{kind_label}：刷新 {counts['refreshed']} / 跳过 {counts['not_found']} / "
        f"删除 {counts['culled']} / 错误 {counts['error']}（尝试 {attempts}{tail}）"
    )
    ingest_service.record_ingest_run_event(
        session_factory,
        event_type=event_type,
        message=message,
        payload={"trigger": trigger, "counts": counts, "games": log},
    )


# ---------------------------------------------------------------------------
# config-driven 托管抓取：按名刷新迁移集，软 LRU + 时间窗 + 有界随机 gap。
# ---------------------------------------------------------------------------


def _recover_stuck_run(session_factory: SessionFactory) -> None:
    """启动时把上次崩溃残留的 last_run_started_at(无对应 finished)复位为已中断。

    纯展示用途，不影响进程内锁（`_running` 本就是全新进程的初始值 False）。
    """
    db = session_factory()
    try:
        cfg = ingest_service.get_or_create_ingest_config(db)
        if cfg.last_run_started_at is not None and cfg.last_run_finished_at is None:
            cfg.last_run_finished_at = utcnow()
            summary = dict(cfg.last_run_summary or {})
            summary["interrupted"] = True
            cfg.last_run_summary = summary
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("game ingest stuck-run recovery failed")
    finally:
        db.close()


def run_configured_ingest_once(
    session_factory: SessionFactory,
    now_local: dt.datetime,
    state: dict[str, Any],
    rng: random.Random,
) -> dict[str, Any]:
    """一轮 config-driven 巡检：关闭 / 窗外 / 本窗已满 → 不处理；否则刷最旧的 1 个待刷游戏。

    `state` 由调用方跨轮持有（`{"window_start": ..., "processed_this_window": ...}`），
    进入新窗口（`window_start_instant` 变化）时重置计数，把每晚 `batch_size` 次抓取错峰铺开。
    不处理进程内锁——由调用方（`_loop` / `start_configured_ingest`）负责互斥。
    """
    db = session_factory()
    try:
        cfg = ingest_service.get_or_create_ingest_config(db)
        enabled = cfg.enabled
        start = parse_hhmm(cfg.window_start)
        end = parse_hhmm(cfg.window_end)
        batch_size = cfg.batch_size
        source_order = cfg.source_order
        max_shots = cfg.max_shots
        min_gap = cfg.min_gap_seconds
        max_gap = cfg.max_gap_seconds
        cull_after_misses = cfg.cull_after_misses
        cull_enabled = cfg.cull_enabled
    finally:
        db.close()

    if not enabled:
        return {"processed": False, "reason": "disabled"}
    if not in_window(start, end, now_local):
        return {"processed": False, "reason": "outside_window"}

    win_start = window_start_instant(start, now_local)
    if state.get("window_start") != win_start:
        # 进入新窗口：上一窗若攒了游戏却没在配额/触顶时 emit 过(收尾窗口不足 batch_size)，补发一条。
        if state.get("log_games") and not state.get("log_emitted", False):
            _emit_run_log(
                session_factory,
                trigger="scheduled",
                event_type="ingest_window",
                kind_label="定时抓取窗口",
                log=state["log_games"],
                attempts=state.get("attempts_this_window", 0),
                cap_reached=False,
            )
        state["window_start"] = win_start
        state["refreshed_this_window"] = 0
        state["attempts_this_window"] = 0
        state["log_games"] = _blank_log()
        state["log_emitted"] = False

    # 名额=batch_size，只数刷新成功；搜不到/错误不占。尝试上限兜底，防整池搜不到时猛刷。
    refreshed = state.get("refreshed_this_window", 0)
    attempts = state.get("attempts_this_window", 0)
    if refreshed >= batch_size:
        return {"processed": False, "reason": "batch_size_reached"}
    if attempts >= batch_size * _ATTEMPT_MULTIPLIER:
        return {"processed": False, "reason": "attempt_cap_reached"}

    db = session_factory()
    try:
        due = ingest_service.select_due_games(db, limit=1)
    finally:
        db.close()

    if not due:
        return {"processed": False, "reason": "no_due_games"}

    game_id = due[0]
    result = ingest_service.refresh_one_game(
        session_factory,
        game_id,
        source_order=source_order,
        max_shots=max_shots,
        cull_after_misses=cull_after_misses,
        cull_enabled=cull_enabled,
    )
    state["attempts_this_window"] = attempts + 1
    if result.get("outcome") == "refreshed":
        state["refreshed_this_window"] = refreshed + 1
    _append_log(state.setdefault("log_games", _blank_log()), result)

    # 本窗配额跑满 / 触顶 → 立刻 emit 一条(不等下一窗)，本窗其余 tick 靠 log_emitted 不重复。
    done_quota = state["refreshed_this_window"] >= batch_size
    done_cap = state["attempts_this_window"] >= batch_size * _ATTEMPT_MULTIPLIER
    if (done_quota or done_cap) and not state.get("log_emitted", False):
        _emit_run_log(
            session_factory,
            trigger="scheduled",
            event_type="ingest_window",
            kind_label="定时抓取窗口",
            log=state["log_games"],
            attempts=state["attempts_this_window"],
            cap_reached=done_cap and not done_quota,
        )
        state["log_emitted"] = True

    win_end = window_end_instant(end, now_local)
    remaining_window_s = (win_end - _to_utc_naive(now_local)).total_seconds()
    remaining_due = max(1, batch_size - state["refreshed_this_window"])
    gap = compute_next_gap(remaining_window_s, remaining_due, min_gap, max_gap, rng)
    return {
        "processed": True,
        "game_id": game_id,
        "result": result,
        "next_gap_seconds": gap,
    }


def _run_configured_batch(session_factory: SessionFactory, *, trigger: str) -> None:
    """手动「立即抓一批」：绕过时间窗，一次跑 `cfg.batch_size` 个，游戏间有界随机 gap。

    调用方（`start_configured_ingest`）已经把 `_running` 置 True，这里只负责在 `finally` 释放。
    """
    rng = random.Random()
    try:
        db = session_factory()
        try:
            cfg = ingest_service.get_or_create_ingest_config(db)
            cfg.last_run_started_at = utcnow()
            cfg.last_run_trigger = trigger
            db.commit()
            batch_size = cfg.batch_size
            source_order = cfg.source_order
            max_shots = cfg.max_shots
            min_gap = cfg.min_gap_seconds
            max_gap = cfg.max_gap_seconds
            cull_after_misses = cfg.cull_after_misses
            cull_enabled = cfg.cull_enabled
            attempt_cap = batch_size * _ATTEMPT_MULTIPLIER
            due = ingest_service.select_due_games(db, limit=attempt_cap)
        finally:
            db.close()

        # 名额=batch_size，只数 refreshed；搜不到/错误不占，继续往下取，直到攒够或触顶。
        summary: dict[str, int] = {
            "batch": batch_size,
            "attempts": 0,
            "refreshed": 0,
            "not_found": 0,
            "error": 0,
            "culled": 0,
        }
        cap_reached = False
        log = _blank_log()
        for game_id in due:
            result = ingest_service.refresh_one_game(
                session_factory,
                game_id,
                source_order=source_order,
                max_shots=max_shots,
                cull_after_misses=cull_after_misses,
                cull_enabled=cull_enabled,
            )
            outcome = result["outcome"]
            key = outcome if outcome in summary else "error"
            summary[key] += 1
            summary["attempts"] += 1
            _append_log(log, result)
            if summary["refreshed"] >= batch_size:
                break
            if summary["attempts"] < len(due):
                time.sleep(rng.uniform(min_gap, max_gap))
        else:
            # for 未 break：整个候选池跑完仍没攒够 → 只有真到尝试上限才算触顶（否则只是池子不够）。
            cap_reached = summary["refreshed"] < batch_size and len(due) >= attempt_cap
        summary["cap_reached"] = cap_reached

        db = session_factory()
        try:
            cfg = ingest_service.get_or_create_ingest_config(db)
            cfg.last_run_finished_at = utcnow()
            cfg.last_run_summary = summary
            db.commit()
        finally:
            db.close()

        _emit_run_log(
            session_factory,
            trigger=trigger,
            event_type="ingest_batch",
            kind_label="立即抓一批" if trigger == "manual" else "抓取一批",
            log=log,
            attempts=summary["attempts"],
            cap_reached=cap_reached,
        )
    except Exception:
        logger.exception("game ingest manual batch failed")
    finally:
        _release_running()


def start_configured_ingest(session_factory: SessionFactory, *, trigger: str = "manual") -> bool:
    """立即抓一批（`cfg.batch_size` 个，绕过时间窗），异步后台线程跑。

    忙（定时 tick 或另一个手动批次正在跑）→ 不重入，返回 False。
    """
    if not _try_acquire_running():
        return False
    threading.Thread(
        target=_run_configured_batch,
        args=(session_factory,),
        kwargs={"trigger": trigger},
        daemon=True,
        name="game-ingest-manual",
    ).start()
    return True


def start_game_ingest(session_factory: SessionFactory) -> bool:
    """按 DB 配置启动 config-driven 托管抓取后台守护线程。

    env 总闸 `GEO_GAME_INGEST_SCHEDULER_ENABLED` 决定是否起线程；线程起来之后每 tick 都重新读
    DB `game_ingest_config`（enabled / 时间窗 / batch_size / gap / source_order / max_shots），
    细粒度控制全在 DB、不需要重启进程。已在跑 → 返回 False（幂等）。
    """
    global _thread
    if not get_settings().game_ingest_scheduler_enabled:
        return False
    if _thread is not None and _thread.is_alive():
        return False

    _recover_stuck_run(session_factory)
    _stop.clear()

    def _loop() -> None:
        state: dict[str, Any] = {}
        rng = random.Random()
        while not _stop.is_set():
            tz = ZoneInfo(get_settings().scheduler_tz)
            if not _try_acquire_running():
                r: dict[str, Any] = {"processed": False, "reason": "busy"}
            else:
                try:
                    r = run_configured_ingest_once(session_factory, dt.datetime.now(tz), state, rng)
                except Exception:
                    logger.exception("game ingest scheduled tick failed")
                    r = {"processed": False}
                finally:
                    _release_running()
            if r.get("processed"):
                sleep_s = float(r.get("next_gap_seconds") or 0.0)
            else:
                sleep_s = float(get_settings().game_ingest_poll_seconds)
            if _stop.wait(max(1.0, sleep_s)):
                break

    _thread = threading.Thread(target=_loop, daemon=True, name="game-ingest")
    _thread.start()
    return True


def stop_game_ingest() -> None:
    _stop.set()
