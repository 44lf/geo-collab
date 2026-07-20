"""游戏库服务：入库并集合并 + 检索聚合。"""

from __future__ import annotations

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


def upsert_game(db: Session, game: types.Game, *, max_screenshots: int = 6) -> Game:
    """按 name_normalized 跨源并集合并；不 commit（调用方按游戏 commit）。"""
    norm = _normalize_game_name(game.name) or game.name
    row = db.query(Game).filter(Game.name_normalized == norm).first()
    if row is None:
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
            db.add(GameTag(game_id=row.id, tag=tag, axis=None))
            existing_tags.add(tag)

    cat = get_or_create_companion_category(db, game.name)
    if cat is not None:
        row.stock_category_id = cat.id
        for url in (game.screenshot_urls or [])[:max_screenshots]:
            got = image_download.download_image(url)
            if got is None:
                continue
            data, mime = got
            store_image_bytes(db, cat, data, mime, source_url=url, commit=False)

    row.last_verified_at = utcnow()
    db.flush()
    return row
