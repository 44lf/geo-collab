from __future__ import annotations

import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.app.core.time import utcnow
from server.app.modules.game_library.models import Game, GameIngestConfig
from server.app.modules.image_library.models import StockCategory
from server.app.shared.errors import ValidationError

logger = logging.getLogger(__name__)

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


def select_due_games(db: Session, *, limit: int) -> list[int]:
    """选出待巡检的 companion-only 游戏 id，按 last_verified_at 升序（未巡检的 NULL 优先）。

    只挂在 kind='companion' 栏目下的游戏才自动巡检；main 栏目手工维护，不进这条自动化。
    注意：不用 `.nullsfirst()`——MySQL（含 8.0.46）不支持 `ORDER BY ... NULLS FIRST` 语法
    （会报 1064 语法错误）。MySQL 对 ASC 排序里 NULL 的默认语义就是排最前，因此裸 `.asc()`
    已经等价于 NULLS FIRST，不需要（也不能用）显式子句。
    """
    comp = select(StockCategory.id).where(StockCategory.kind == "companion")
    stmt = (
        select(Game.id)
        .where(Game.is_active.is_(True), Game.stock_category_id.in_(comp))
        .order_by(Game.last_verified_at.asc())
        .limit(max(1, int(limit)))
    )
    return list(db.execute(stmt).scalars().all())


def _search_by_name(source_order: str, name: str):
    """按 source_order（逗号分隔，如 "taptap,baidu"）依次尝试，命中即停。"""
    from server.app.modules.game_library.sources import baidu, taptap

    srcs = {"baidu": baidu, "taptap": taptap}
    for key in [s.strip() for s in (source_order or "").split(",") if s.strip()]:
        mod = srcs.get(key)
        if mod is None:
            continue
        try:
            hit = mod.search_by_name(name)
        except Exception:
            logger.warning("search_by_name failed source=%s name=%s", key, name, exc_info=True)
            hit = None
        if hit is not None:
            # taptap 的 search_by_name 命中时不带截图（brand 搜索接口没有该字段），
            # 需要再调 get_detail 补齐 screenshot_urls。
            if key == "taptap" and not hit.screenshot_urls:
                try:
                    hit = taptap.get_detail(hit.game_id)
                except Exception:
                    logger.warning("taptap get_detail failed name=%s", name, exc_info=True)
            return hit
    return None


def refresh_one_game(session_factory, game_id: int, *, source_order: str, max_shots: int) -> str:
    """巡检单个游戏：短读 session 取名字/桶 → session 外联网+下载 → 短写 session 落库。

    per-game 隔离：任何异常都吞掉记日志，返回 'refreshed' / 'not_found' / 'error'，
    绝不向上抛——一个游戏巡检失败不能拖垮整批。
    """
    from server.app.modules.game_library import service
    from server.app.shared import image_download

    db = session_factory()
    try:
        game = db.get(Game, game_id)
        if game is None:
            return "error"
        name, category_id = game.name, game.stock_category_id
    finally:
        db.close()

    hit = _search_by_name(source_order, name)  # 无 session：联网 + 下载
    shots: list[tuple[str, bytes, str]] = []
    if hit is not None:
        for url in list(dict.fromkeys(hit.screenshot_urls or []))[:max_shots]:
            got = image_download.download_image(url)
            if got:
                shots.append((url, got[0], got[1]))

    db = session_factory()
    try:
        if hit is None:
            g = db.get(Game, game_id)
            if g is not None:
                g.last_verified_at = utcnow()
            db.commit()
            return "not_found"
        service.upsert_game(
            db, hit, max_screenshots=max_shots, pre_downloaded=shots, category_id=category_id
        )
        db.commit()
        return "refreshed"
    except Exception:
        db.rollback()
        logger.warning("refresh_one_game failed id=%s", game_id, exc_info=True)
        # 即便失败也推进 last_verified_at：否则该游戏在 last_verified_at ASC 里永远排最前、
        # 每个 tick 都重选，饿死整批轮转（最终 review I2）。失败游戏被推到队尾、下一整轮才重试。
        try:
            g = db.get(Game, game_id)
            if g is not None:
                g.last_verified_at = utcnow()
                db.commit()
        except Exception:
            db.rollback()
            logger.warning(
                "refresh_one_game: bump last_verified_at failed id=%s", game_id, exc_info=True
            )
        return "error"
    finally:
        db.close()
