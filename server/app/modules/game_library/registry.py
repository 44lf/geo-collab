"""数据源注册表:入库只需 search + 分页 collect_pool。"""

import logging

from . import types
from .sources import baidu

logger = logging.getLogger(__name__)

SOURCES = {types.SOURCE_BAIDU: baidu}


def search(source, category, **kwargs):
    if source not in SOURCES:
        raise ValueError(f"未知数据源: {source!r},可选: {list(SOURCES)}")
    return SOURCES[source].search(category, **kwargs)


def collect_pool(source, category, pool_size, *, page_size_cap=None, **kwargs):
    """按 pool_size 翻页收集(baidu 无单页上限)。数据源枯竭即停。"""
    cap = page_size_cap if page_size_cap is not None else pool_size
    if cap <= 0:
        return []

    games, page = [], 1
    while len(games) < pool_size:
        size = min(cap, pool_size - len(games))
        try:
            batch = search(source, category, page=page, page_size=size, **kwargs)
        except Exception:
            if not games:
                raise
            logger.warning(
                "game source page failed; returning partial pool source=%s category=%s page=%s",
                source,
                category,
                page,
                exc_info=True,
            )
            break
        if not batch:
            break
        games.extend(batch)
        if len(batch) < size:
            break
        page += 1
    return games
