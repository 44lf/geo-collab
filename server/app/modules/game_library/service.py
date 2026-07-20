"""游戏库服务：入库并集合并 + 检索聚合。"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from server.app.core.time import utcnow
from server.app.modules.articles.formatting.document import _normalize_game_name
from server.app.modules.game_library import types
from server.app.modules.game_library.models import Game, GameTag
from server.app.modules.image_library.service import (
    get_or_create_companion_category,
    store_image_bytes,
)
from server.app.shared import image_download


def _merge_scalar_max(cur, new):
    if new is None:
        return cur
    if cur is None:
        return new
    return max(cur, new)


def _prefer_longer(cur: str | None, new: str | None) -> str | None:
    if not new:
        return cur
    if not cur or len(new) > len(cur):
        return new
    return cur


def _download_screenshots(
    urls: list[str] | None,
    max_screenshots: int,
) -> list[tuple[str, bytes, str]]:
    out: list[tuple[str, bytes, str]] = []
    for url in list(dict.fromkeys(urls or []))[:max_screenshots]:
        got = image_download.download_image(url)
        if got is None:
            continue
        data, mime = got
        out.append((url, data, mime))
    return out


def _get_or_create_game_row(db: Session, game: types.Game, norm: str) -> Game:
    row = db.query(Game).filter(Game.name_normalized == norm).first()
    if row is not None:
        return row

    nested = db.begin_nested()
    try:
        row = Game(
            name=game.name,
            name_normalized=norm,
            sources=[],
            platforms=[],
            screenshot_urls=[],
            use_count=0,
            is_active=True,
            first_seen_at=utcnow(),
        )
        db.add(row)
        db.flush()
        nested.commit()
        return row
    except IntegrityError:
        nested.rollback()
        return db.query(Game).filter(Game.name_normalized == norm).one()


def _add_game_tag_if_missing(db: Session, game_id: int, tag: str) -> None:
    nested = db.begin_nested()
    try:
        db.add(GameTag(game_id=game_id, tag=tag, axis=None))
        db.flush()
        nested.commit()
    except IntegrityError:
        nested.rollback()


def upsert_game(db: Session, game: types.Game, *, max_screenshots: int = 6) -> Game:
    """按 name_normalized 跨源并集合并；不 commit（调用方按游戏 commit）。"""
    downloaded_screenshots = _download_screenshots(game.screenshot_urls, max_screenshots)

    norm = _normalize_game_name(game.name) or game.name
    cat = get_or_create_companion_category(db, game.name, commit=False)
    row = _get_or_create_game_row(db, game, norm)

    row.score = _merge_scalar_max(row.score, game.score)
    row.comment_count = _merge_scalar_max(row.comment_count, game.comment_count)
    row.description = _prefer_longer(row.description, game.description)
    row.icon_url = _prefer_longer(row.icon_url, game.icon_url)

    src_entry = {"source": game.source, "source_game_id": game.game_id}
    row.sources = list(row.sources or [])
    if src_entry not in row.sources:
        row.sources = row.sources + [src_entry]
    row.platforms = sorted(set((row.platforms or []) + list(game.platforms or [])))
    row.screenshot_urls = list(
        dict.fromkeys((row.screenshot_urls or []) + list(game.screenshot_urls or []))
    )

    existing_tags = {t.tag for t in db.query(GameTag).filter(GameTag.game_id == row.id)}
    for tag in game.tags or []:
        if tag and tag not in existing_tags:
            _add_game_tag_if_missing(db, row.id, tag)
            existing_tags.add(tag)

    if cat is not None:
        row.stock_category_id = cat.id
        for url, data, mime in downloaded_screenshots:
            store_image_bytes(db, cat, data, mime, source_url=url, commit=False)

    row.last_verified_at = utcnow()
    db.flush()
    return row


def list_game_tags(db: Session, limit: int = 200) -> list[dict]:
    count_expr = func.count(func.distinct(GameTag.game_id))
    stmt = (
        select(GameTag.tag, count_expr.label("game_count"))
        .join(Game, Game.id == GameTag.game_id)
        .where(Game.is_active.is_(True))
        .group_by(GameTag.tag)
        .order_by(count_expr.desc())
        .limit(max(1, min(1000, limit)))
    )
    return [{"tag": tag, "game_count": game_count} for tag, game_count in db.execute(stmt).all()]


def _game_to_dict(g: Game) -> dict:
    return {
        "game_id": g.id,
        "name": g.name,
        "score": g.score,
        "tags": [t.tag for t in g.tags],
        "description": g.description,
        "stock_category_id": g.stock_category_id,
        "icon_url": g.icon_url,
        "screenshot_urls": g.screenshot_urls or [],
        "highlight_comments": g.highlight_comments,
        "related_hotspots": g.related_hotspots,
        "use_count": g.use_count,
        "last_used_at": g.last_used_at.isoformat() if g.last_used_at else None,
    }


def query_games_by_tags(
    db: Session,
    relevant_tags: list[str],
    diversity_tags: list[str] | None = None,
    exclude_tags: list[str] | None = None,
    min_score: float | None = None,
    limit: int = 20,
) -> list[dict]:
    """relevant_tags=准入（命中任一）；diversity_tags 只排序不放宽准入。"""
    relevant_tags = [tag for tag in relevant_tags if tag]
    if not relevant_tags:
        return []

    admit = select(GameTag.game_id).where(GameTag.tag.in_(relevant_tags)).distinct()
    stmt = select(Game).where(Game.id.in_(admit), Game.is_active.is_(True))
    if exclude_tags:
        excluded = select(GameTag.game_id).where(GameTag.tag.in_(exclude_tags)).distinct()
        stmt = stmt.where(~Game.id.in_(excluded))
    if min_score is not None:
        stmt = stmt.where(Game.score >= min_score)

    stmt = stmt.order_by(
        Game.last_used_at.asc(),
        func.coalesce(Game.score, -1).desc(),
        func.coalesce(Game.comment_count, -1).desc(),
    ).limit(max(1, min(100, limit)))
    rows = list(db.execute(stmt).scalars().all())

    if diversity_tags:
        diversity_set = set(diversity_tags)
        rows.sort(key=lambda g: 0 if diversity_set & {tag.tag for tag in g.tags} else 1)

    return [_game_to_dict(g) for g in rows]


def bump_game_usage(db: Session, game_ids: list[int], article_id: int) -> None:
    """文章采用游戏后回写游戏级用量（不 commit，调用方同事务提交）。未知 id 天然跳过。"""
    ids = [int(game_id) for game_id in (game_ids or []) if game_id]
    if not ids:
        return
    db.query(Game).filter(Game.id.in_(ids)).update(
        {
            Game.use_count: Game.use_count + 1,
            Game.last_used_at: utcnow(),
            Game.last_used_article_id: article_id,
        },
        synchronize_session=False,
    )
