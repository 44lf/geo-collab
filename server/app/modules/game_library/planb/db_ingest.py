"""扩库落库编排：应用宝榜单发现 → service.upsert_game（新游戏自动建 companion 桶）。

镜像 `ingest_service.refresh_one_game` 的两段式：session 外联网 + 下图，短 session 内只写库，
per-game 隔离（一个失败不影响后续）。highlight_comments 在 upsert 返回的 row 上补写
（upsert_game 不写该字段）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from server.app.modules.articles.formatting.document import _normalize_game_name
from server.app.modules.game_library import ingest_service, landscape, service
from server.app.modules.game_library.models import Game
from server.app.modules.image_library.service import source_url_sha256
from server.app.shared import image_download

from . import adapter
from . import ingest as discovery_ingest
from .http_client import Client

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], Any]


def _download_shots(
    urls: list[str], max_shots: int, skip_hashes: set[str] | None = None
) -> list[tuple[str, bytes, str]]:
    """下载截图（session 外）。skip_hashes 里已入库的 source_url_hash 直接跳过，省重复下载 +
    重复付费竖转横（与巡检腿 refresh_one_game 同口径，评审 #2）。"""
    seen = set(skip_hashes or ())
    out: list[tuple[str, bytes, str]] = []
    for url in list(dict.fromkeys(urls or []))[:max_shots]:
        h = source_url_sha256(url)
        if h in seen:
            continue
        got = image_download.download_image(url)
        if got:
            data, mime = landscape.to_landscape_if_portrait(got[0], got[1])
            out.append((url, data, mime))
            seen.add(h)  # 本轮内同 URL 不重复下
    return out


def ingest_discovery(
    session_factory: SessionFactory,
    *,
    seed_paths: list[str] | None = None,
    with_detail: bool = True,
    detail_limit: int = 30,
    max_shots: int = 6,
    min_interval: float = 5.0,
    trigger: str = "manual",
) -> dict:
    """跑一轮应用宝榜单发现 → 落库。返回 summary(discovered/upserted/new/comments/failed)。"""
    client = Client(min_interval=min_interval)
    games = discovery_ingest.discover(
        client,
        paths=seed_paths,
        with_detail=with_detail,
        detail_limit=detail_limit,
        log=lambda m: logger.info("planb discovery: %s", m),
    )

    summary = {
        "discovered": len(games),
        "upserted": 0,
        "new": 0,
        "comments": 0,
        "failed": 0,
    }
    created_names: list[str] = []

    for pg in games:
        try:
            tg = adapter.to_types_game(pg)
            norm = _normalize_game_name(pg.name) or pg.name

            # ① 短读：定位既有行 + 复用其素材桶 + 取已入库截图 hash（省重复下载/竖转横）。
            # 撞已有行必须锚定该行 + 复用它的桶：绝不让 upsert 按"本次抓到的原始名"re-resolve 分类，
            # 把老游戏（含手工 main）的 stock_category 改指到新建空桶、孤立原截图（桶名精确匹配 ≠
            # name_normalized，去书名号/前缀后二者跨源撞名会分叉）。只取标量，行留到 ② 重新 get。
            db = session_factory()
            try:
                existing_row = db.query(Game).filter(Game.name_normalized == norm).first()
                existed = existing_row is not None
                existing_id = existing_row.id if existing_row else None
                category_id = existing_row.stock_category_id if existing_row else None
                skip_hashes = ingest_service._existing_shot_hashes(db, category_id)
            finally:
                db.close()

            # session 外：下载截图（跳过已入库）+ 图标。
            shots = _download_shots(pg.screenshot_urls, max_shots, skip_hashes=skip_hashes)
            pre_icon: tuple[str, bytes, str] | None = None
            if pg.icon_url:
                got = image_download.download_image(pg.icon_url)
                if got:
                    pre_icon = (pg.icon_url, got[0], got[1])

            # ② 短写：重新 get 既有行（不跨 session 传 detached ORM）+ 显式复用桶。
            db = session_factory()
            try:
                game_row = db.get(Game, existing_id) if existing_id else None
                row = service.upsert_game(
                    db,
                    tg,
                    max_screenshots=max_shots,
                    pre_downloaded=shots,
                    category_id=category_id,
                    game_row=game_row,
                    pre_downloaded_icon=pre_icon,
                )
                if pg.highlight_comments:
                    row.highlight_comments = pg.highlight_comments
                    summary["comments"] += 1
                db.commit()
                summary["upserted"] += 1
                if not existed:
                    summary["new"] += 1
                    created_names.append(row.name)
            finally:
                db.close()
        except Exception:
            summary["failed"] += 1
            logger.warning("planb ingest_discovery failed name=%s", pg.name, exc_info=True)

    _emit_discovery_log(session_factory, trigger=trigger, summary=summary, created=created_names)
    return summary


def _emit_discovery_log(
    session_factory: SessionFactory, *, trigger: str, summary: dict, created: list[str]
) -> None:
    if not summary.get("discovered"):
        return
    message = (
        f"应用宝扩库：发现 {summary['discovered']} / 落库 {summary['upserted']} / "
        f"新增 {summary['new']} / 精选评论 {summary['comments']} / 失败 {summary['failed']}"
    )
    ingest_service.record_ingest_run_event(
        session_factory,
        event_type="discovery_batch",
        message=message,
        payload={
            "trigger": trigger,
            "direction": "discovery",
            "counts": summary,
            "games": {"created": [{"id": None, "name": n} for n in created]},
        },
    )
