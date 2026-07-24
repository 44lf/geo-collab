"""planb.model.Game → game_library.types.Game，喂 service.upsert_game。

category（应用宝 cate_name_new）并入 tags（与 baidu 把 gameTypes 落 tags 同口径）。
highlight_comments 不进 types.Game（upsert_game 不写该字段），由 db_ingest 在返回 row 上补写。
"""

from __future__ import annotations

from ..types import SOURCE_YINGYONGBAO
from ..types import Game as TypesGame
from .model import Game as PlanbGame


def to_types_game(pg: PlanbGame) -> TypesGame:
    tags = list(pg.tags or [])
    if pg.category and pg.category not in tags:
        tags.append(pg.category)
    return TypesGame(
        source=SOURCE_YINGYONGBAO,
        game_id=str(pg.source_id) if pg.source_id else pg.name,
        name=pg.name,
        score=pg.score,
        tags=tags,
        platforms=list(pg.platforms or []),
        comment_count=pg.comment_count,
        icon_url=pg.icon_url,
        screenshot_urls=list(pg.screenshot_urls or []),
        description=pg.description,
        raw={"source_url": pg.source_url, "category": pg.category},
    )
