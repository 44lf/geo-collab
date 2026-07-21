"""游戏库服务：入库并集合并 + 检索聚合。"""

from __future__ import annotations

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from server.app.core.time import utcnow
from server.app.modules.articles.formatting.document import _normalize_game_name
from server.app.modules.game_library import types
from server.app.modules.game_library.models import Game, GameTag
from server.app.modules.image_library.models import StockCategory
from server.app.modules.image_library.service import (
    get_or_create_companion_category,
    store_image_bytes,
)
from server.app.shared import image_download
from server.app.shared.errors import ConflictError


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


def upsert_game(
    db: Session,
    game: types.Game,
    *,
    max_screenshots: int = 6,
    pre_downloaded: list | None = None,
    category_id: int | None = None,
) -> Game:
    """跨源并集合并；下载已在 session 外做好经 pre_downloaded 传入，session 内只写库。
    category_id 非空 = 直接用该桶，不按名 re-resolve(迁移集游戏)。不 commit（调用方按游戏 commit）。"""
    shots = (
        pre_downloaded
        if pre_downloaded is not None
        else _download_screenshots(game.screenshot_urls, max_screenshots)
    )

    norm = _normalize_game_name(game.name) or game.name
    if category_id is not None:
        cat = db.get(StockCategory, category_id)
    else:
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
        for url, data, mime in shots:
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
    stmt = (
        select(Game)
        .options(selectinload(Game.tags))
        .where(
            Game.id.in_(admit),
            Game.is_active.is_(True),
        )
    )
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


def _iso(dt):
    return dt.isoformat() if dt else None


def _source_names(sources) -> list[str]:
    names: list[str] = []
    for entry in sources or []:
        name = entry.get("source") if isinstance(entry, dict) else None
        if name and name not in names:
            names.append(name)
    return sorted(names)


def _kind_of(db: Session, stock_category_id: int | None) -> str | None:
    if stock_category_id is None:
        return None
    cat = db.get(StockCategory, stock_category_id)
    return cat.kind if cat is not None else None


def _game_to_list_item(db: Session, g: Game) -> dict:
    return {
        "game_id": g.id,
        "name": g.name,
        "score": g.score,
        "tags": [t.tag for t in g.tags],
        "icon_url": g.icon_url,
        "screenshot_count": len(g.screenshot_urls or []),
        "use_count": g.use_count,
        "last_used_at": _iso(g.last_used_at),
        "stock_category_id": g.stock_category_id,
        "sources": _source_names(g.sources),
        "kind": _kind_of(db, g.stock_category_id),
    }


def _game_to_detail(db: Session, g: Game) -> dict:
    return {
        "game_id": g.id,
        "name": g.name,
        "name_normalized": g.name_normalized,
        "score": g.score,
        "comment_count": g.comment_count,
        "tags": [t.tag for t in g.tags],
        "platforms": g.platforms or [],
        "sources": g.sources or [],
        "icon_url": g.icon_url,
        "screenshot_urls": g.screenshot_urls or [],
        "description": g.description,
        "stock_category_id": g.stock_category_id,
        "kind": _kind_of(db, g.stock_category_id),
        "use_count": g.use_count,
        "last_used_at": _iso(g.last_used_at),
        "last_used_article_id": g.last_used_article_id,
        "first_seen_at": _iso(g.first_seen_at),
        "last_verified_at": _iso(g.last_verified_at),
        "highlight_comments": g.highlight_comments,
        "related_hotspots": g.related_hotspots,
        "is_active": g.is_active,
    }


def list_games(
    db,
    *,
    tag=None,
    min_score=None,
    q=None,
    kind=None,
    is_active=True,
    limit=50,
    offset=0,
) -> dict:
    conds: list[ColumnElement[bool]] = []
    if is_active:
        conds.append(Game.is_active.is_(True))
    if tag:
        conds.append(Game.id.in_(select(GameTag.game_id).where(GameTag.tag == tag)))
    if min_score is not None:
        conds.append(Game.score >= min_score)
    if q:
        conds.append(Game.name.like(f"%{q}%"))
    if kind:
        conds.append(
            Game.stock_category_id.in_(select(StockCategory.id).where(StockCategory.kind == kind))
        )
    count_stmt = select(func.count()).select_from(Game)
    if conds:
        count_stmt = count_stmt.where(*conds)
    total = int(db.execute(count_stmt).scalar_one())
    stmt = select(Game).options(selectinload(Game.tags))
    if conds:
        stmt = stmt.where(*conds)
    stmt = (
        stmt.order_by(func.coalesce(Game.score, -1).desc(), Game.id.asc())
        .limit(max(1, min(200, limit)))
        .offset(max(0, offset))
    )
    rows = list(db.execute(stmt).scalars().all())
    return {"items": [_game_to_list_item(db, g) for g in rows], "total": total}


def get_game(db, game_id: int) -> dict | None:
    g = db.execute(
        select(Game).options(selectinload(Game.tags)).where(Game.id == game_id)
    ).scalar_one_or_none()
    return _game_to_detail(db, g) if g is not None else None


def update_game(db: Session, game_id: int, patch: dict) -> Game | None:
    """手动编辑游戏。只对 patch 里显式给出的非 None 字段生效；tags 给了就整体替换。
    不 commit（调用方按需提交）。"""
    game = db.get(Game, game_id)
    if game is None:
        return None

    name = patch.get("name")
    if name is not None:
        game.name = name
        game.name_normalized = _normalize_game_name(name) or name

    score = patch.get("score")
    if score is not None:
        game.score = score

    description = patch.get("description")
    if description is not None:
        game.description = description

    tags = patch.get("tags")
    if tags is not None:
        deduped = list(dict.fromkeys(tag.strip() for tag in tags if tag and tag.strip()))
        # 差量替换：只删掉不再需要的、只追加新增的。不能整体重赋值 game.tags——
        # unit-of-work 对同一 mapper 先 INSERT 后 DELETE，新旧 tag 重叠时会撞
        # uq_game_tags_game_tag（最终 review C1）。差量后同一 (game_id,tag) 绝不同时删+插。
        desired = set(deduped)
        existing = {gt.tag: gt for gt in game.tags}
        for tag_val, gt in list(existing.items()):
            if tag_val not in desired:
                game.tags.remove(gt)  # delete-orphan 负责删行
        for tag_val in deduped:
            if tag_val not in existing:
                game.tags.append(GameTag(tag=tag_val))

    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("游戏名已存在") from exc
    return game


def soft_delete_game(db: Session, game_id: int) -> bool:
    """软删：置 is_active=False，可逆；浏览列表已按 is_active=True 过滤自动隐去。
    不 commit（调用方按需提交）。"""
    game = db.get(Game, game_id)
    if game is None:
        return False
    game.is_active = False
    db.flush()
    return True


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
