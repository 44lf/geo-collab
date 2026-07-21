from __future__ import annotations

import logging
import re
import unicodedata

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
    "cull_after_misses",
    "cull_enabled",
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
    for key in (
        "batch_size",
        "min_gap_seconds",
        "max_gap_seconds",
        "max_shots",
        "cull_after_misses",
    ):
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
    # 与 main.py 全局 datetime 补丁一致：无时区(naive UTC) 补 "Z"，否则预先 isoformat 的字符串会
    # 绕过那个只认 datetime 对象的补丁 → 前端 new Date 把裸 UTC 当本地时区、差 8 小时。
    if dt is None:
        return None
    return dt.isoformat() + ("Z" if dt.tzinfo is None else "")


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
        "cull_after_misses": cfg.cull_after_misses,
        "cull_enabled": cfg.cull_enabled,
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


# 匹配用的独立归一化：比去重键 `_normalize_game_name`（仅 strip）更狠，但只用于"算不算命中"、
# 不碰 name_normalized（落库靠 game_row 锚定，不按名解析），故可放心加强、零去重风险。
# NFKC 折全/半角 + 小写 + 去所有内部空白 + 去常见标点。仍是"归一化后相等"，不做子串/模糊。
_MATCH_PUNCT = re.compile(r"[\s·・:：\-—_、,，.。!！?？'’\"“”()（）\[\]【】~～|/\\]+")


def _match_key(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").strip().lower()
    return _MATCH_PUNCT.sub("", s)


def _normalized_matcher(candidate, target) -> bool:
    if not candidate or not target:
        return False
    ck, tk = _match_key(candidate), _match_key(target)
    return bool(ck) and ck == tk


def _best_icon_url(hits: list) -> str | None:
    """命中里挑一个图标 URL 作封面来源：取最长的非空（与 upsert 的 _prefer_longer 口径一致）。"""
    return max((h.icon_url for h in hits if h.icon_url), key=len, default=None)


def _collect_from_all_sources(source_order: str, name: str) -> dict:
    """按 source_order 逐源查、**不命中即停**，收集所有命中并记录每源结果。

    返回 {"hits": [types.Game...], "per_source": {src: "hit"|"miss"|"error"}}。
    error（网络/异常）与 miss 严格区分：error 不构成"搜不到"的证据、不计入软删 streak。
    taptap 命中不带截图 → 补 get_detail。归一化 matcher 放宽格式差异。
    """
    from server.app.modules.game_library.sources import baidu, taptap

    srcs = {"baidu": baidu, "taptap": taptap}
    hits: list = []
    per_source: dict[str, str] = {}
    for key in [s.strip() for s in (source_order or "").split(",") if s.strip()]:
        mod = srcs.get(key)
        if mod is None:
            continue
        try:
            hit = mod.search_by_name(name, matcher=_normalized_matcher)
        except Exception:
            logger.warning("search_by_name failed source=%s name=%s", key, name, exc_info=True)
            per_source[key] = "error"
            continue
        if hit is None:
            per_source[key] = "miss"
            continue
        if key == "taptap" and not hit.screenshot_urls:
            try:
                hit = taptap.get_detail(hit.game_id)
            except Exception:
                logger.warning("taptap get_detail failed name=%s", name, exc_info=True)
        per_source[key] = "hit"
        hits.append(hit)
    return {"hits": hits, "per_source": per_source}


def _has_evidence(g: Game) -> bool:
    """有任何源级信息 = 从某个源成功匹配过 = 有存在证据。截图不算（桶里图可能错配）。"""
    if g.score is not None:
        return True
    if g.description and g.description.strip():
        return True
    if g.comment_count is not None:
        return True
    if g.sources:  # 非空 list
        return True
    return False


def _is_cull_exempt(db: Session, g: Game) -> bool:
    """自动软删豁免：有证据 / 被文章用过 / 人工背书 / 主推 main。"""
    if _has_evidence(g):
        return True
    if (g.use_count or 0) > 0:
        return True
    if g.manually_curated:
        return True
    if g.stock_category_id is not None:
        cat = db.get(StockCategory, g.stock_category_id)
        if cat is not None and cat.kind == "main":
            return True
    return False


def refresh_one_game(
    session_factory,
    game_id: int,
    *,
    source_order: str,
    max_shots: int,
    cull_after_misses: int = 3,
    cull_enabled: bool = True,
) -> dict:
    """巡检单个游戏：短读 name/桶 → session 外查全部源+下载 → 短写落库(并集合并)。

    并集：查全部源、合并所有命中（`upsert_game` 天然跨源合并，锚定到本 game_row、不按名重解析）。
    无证据软删：全源 miss(无 hit 无 error) → not_found_streak+1；达阈值且无证据且不豁免 → is_active=False。
    per-game 隔离：异常吞掉记日志。返回 {"outcome": refreshed|not_found|error|culled, "per_source": {...}}。
    """
    from server.app.modules.game_library import service
    from server.app.shared import image_download

    db = session_factory()
    try:
        game = db.get(Game, game_id)
        if game is None:
            return {"outcome": "error", "per_source": {}}
        name, category_id = game.name, game.stock_category_id
        icon_local = bool(game.icon_url and game.icon_url.startswith("/api/stock-images/"))
    finally:
        db.close()

    collected = _collect_from_all_sources(source_order, name)  # 无 session：联网
    hits = collected["hits"]
    per_source = collected["per_source"]

    # 每个 hit 各自下截图（session 外）
    hit_shots: list[tuple] = []
    for hit in hits:
        shots: list[tuple[str, bytes, str]] = []
        for url in list(dict.fromkeys(hit.screenshot_urls or []))[:max_shots]:
            got = image_download.download_image(url)
            if got:
                shots.append((url, got[0], got[1]))
        hit_shots.append((hit, shots))

    # 封面同样在 session 外下载：已是本地地址（转存过）就跳过，否则挑最优图标下一份。
    pre_icon: tuple[str, bytes, str] | None = None
    if not icon_local:
        icon_url = _best_icon_url(hits)
        if icon_url:
            got = image_download.download_image(icon_url)
            if got:
                pre_icon = (icon_url, got[0], got[1])

    db = session_factory()
    try:
        g = db.get(Game, game_id)
        if g is None:
            return {"outcome": "error", "per_source": per_source}

        if hits:
            for hit, shots in hit_shots:
                service.upsert_game(
                    db,
                    hit,
                    max_screenshots=max_shots,
                    pre_downloaded=shots,
                    category_id=category_id,
                    game_row=g,  # 锚定到本行：源标题≠库名时也不另建行
                    pre_downloaded_icon=pre_icon,
                )
            g.not_found_streak = 0
            db.commit()
            return {"outcome": "refreshed", "per_source": per_source}

        # 无命中：区分 miss / error
        has_error = any(v == "error" for v in per_source.values())
        all_miss = bool(per_source) and not has_error
        g.last_verified_at = utcnow()  # 推进轮转，避免饿死（沿用 I2）
        outcome = "error" if has_error else "not_found"
        if all_miss:
            g.not_found_streak = (g.not_found_streak or 0) + 1
            if (
                cull_enabled
                and g.not_found_streak >= cull_after_misses
                and not _is_cull_exempt(db, g)
            ):
                g.is_active = False
                outcome = "culled"
        db.commit()
        return {"outcome": outcome, "per_source": per_source}
    except Exception:
        db.rollback()
        logger.warning("refresh_one_game failed id=%s", game_id, exc_info=True)
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
        return {"outcome": "error", "per_source": per_source}
    finally:
        db.close()
