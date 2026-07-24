"""榜单发现编排：遍历种子页 → 按 source_id 去重合并 → 可选详情补全。

去重合并策略与并入生产后 upsert_game 口径一致：prefer 更全，不覆盖已有非空。
"""

from . import yingyongbao as yyb


def _merge_detail(base, extra):
    """详情结果补进已有卡片：只填空 / 取更全，不破坏列表页已拿到的字段。"""
    if extra.description and not base.description:
        base.description = extra.description
    if len(extra.screenshot_urls) > len(base.screenshot_urls):
        base.screenshot_urls = extra.screenshot_urls
    if len(extra.tags) > len(base.tags):
        base.tags = extra.tags
    if extra.highlight_comments:
        base.highlight_comments = extra.highlight_comments
    if base.score is None and extra.score is not None:
        base.score = extra.score
    base.detail_fetched = True


def discover(client, paths=None, with_detail=True, detail_limit=15, log=print):
    """返回去重后的 list[Game]。with_detail 时对缺 description 的前 detail_limit 款补详情。"""
    paths = paths if paths is not None else yyb.LIST_PATHS
    by_id = {}
    for path in paths:
        games, status = yyb.list_page(client, path)
        log(f"  list {path}: status={status} games={len(games)}")
        for g in games:
            if g.source_id and g.source_id not in by_id:
                by_id[g.source_id] = g
    games = list(by_id.values())
    log(f"去重后共 {len(games)} 款游戏")

    if with_detail:
        need = [g for g in games if not g.description][:detail_limit]
        log(f"补详情：{len(need)} 款（缺 description 的前 {detail_limit}）")
        for i, g in enumerate(need, 1):
            d = yyb.detail(client, g.source_id)
            if d:
                _merge_detail(g, d)
            log(f"  detail {i}/{len(need)} {g.source_id}: {'ok' if d else 'miss'}")
    return games
