"""游戏库：图库栏目→游戏 幂等导入。"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from server.app.core.time import utcnow
from server.app.modules.articles.formatting.document import _normalize_game_name
from server.app.modules.game_library.models import Game
from server.app.modules.image_library.models import StockCategory, StockImage

logger = logging.getLogger(__name__)


def import_image_categories_as_games(
    db: Session, *, kind=None, only_with_images=False, limit=None
) -> dict:
    """将图库栏目导入为游戏记录，幂等处理。

    Args:
        db: 数据库会话。
        kind: 可选，栏目类型过滤（main/companion）。
        only_with_images: 可选，仅导入含图片的栏目。
        limit: 可选，限制导入数量。

    Returns:
        {"scanned": int, "created": int, "attached": int, "skipped": int}
    """
    q = db.query(StockCategory)
    if kind:
        q = q.filter(StockCategory.kind == kind)
    if only_with_images:
        with_img = select(StockImage.category_id).distinct().subquery()
        q = q.filter(StockCategory.id.in_(select(with_img.c.category_id)))
    if limit:
        q = q.limit(int(limit))
    scanned = created = attached = skipped = 0
    created_names: list[str] = []
    for cat in q.all():
        scanned += 1
        norm = _normalize_game_name(cat.name) or cat.name
        game = db.query(Game).filter(Game.name_normalized == norm).first()
        if game is None:
            nested = db.begin_nested()
            try:
                db.add(
                    Game(
                        name=cat.name,
                        name_normalized=norm,
                        stock_category_id=cat.id,
                        sources=[],
                        platforms=[],
                        screenshot_urls=[],
                        use_count=0,
                        is_active=True,
                        first_seen_at=utcnow(),
                    )
                )
                db.flush()
                nested.commit()
                created += 1
                created_names.append(cat.name)
            except IntegrityError:
                nested.rollback()
                skipped += 1
        elif game.stock_category_id is None:
            game.stock_category_id = cat.id
            attached += 1
        else:
            skipped += 1
    db.flush()
    # created_names 仅供「运行日志」事件用，HTTP 出参走 ImageCategoryImportResponse 会自动过滤掉。
    return {
        "scanned": scanned,
        "created": created,
        "attached": attached,
        "skipped": skipped,
        "created_names": created_names,
    }
