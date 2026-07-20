# Game Library Corpus Writer Handoff

日期：2026-07-20

范围：本仓只完成 GEO 后端、MCP 工具与文档同步。`geo-article-writer` 属于 geo-goal 插件 skill，不在本仓修改；插件侧需要按本文改造后重新发版。

## Writer 改造契约

`geo-article-writer` 在动笔前先走游戏库取材：

1. 调 `list_game_tags(limit=200)`，只从返回的真实标签里选择取材标签，不凭空造标签。
2. 从选题和文章角度挑 `relevant_tags` 1-2 个，作为准入标签；再挑若干 `diversity_tags` 只用于排序多样化。
3. 调 `query_games_by_tags(relevant_tags, diversity_tags?, exclude_tags?, min_score?, limit?)`。
4. 候选数量达到阈值时走库，默认阈值 `>=4`。计数只看 relevant 准入池；`diversity_tags` 不能让离题游戏进入候选。
5. 候选不足时回退 WebSearch。回退搜到的游戏没有库内 `game_id`，不要写进 `selected_games`。
6. 写入文章时调用 `save_article(..., selected_games=[{"game_id": <query 返回 id>, "name": <query 返回 name>}])`。只提交实际被文章使用、且来自库内候选的游戏。

## 打点

用 `report_event` 区分取材路径：

- `games_from_library`：命中库内候选并用于文章。
- `games_fallback_websearch`：库内候选不足，回退 WebSearch。

建议 payload 至少包含：

```json
{
  "relevant_tags": ["经营", "养成"],
  "diversity_tags": ["餐厅", "模拟"],
  "candidate_count": 8,
  "threshold": 4,
  "selected_games": [{"game_id": 123, "name": "餐厅养成记"}]
}
```

回退路径的 `selected_games` 为空数组，并记录 WebSearch 关键词或摘要，避免后续误以为已回写游戏库用量。

## 注意事项

- `query_games_by_tags` 的 `relevant_tags` 是准入门槛，至少传 1 个。
- `diversity_tags` 只影响排序，不作为准入条件。
- `selected_games.game_id` 使用 `query_games_by_tags` 返回的 `id`，不是爬虫源站的 source game id。
- 回写用量由 GEO 后端 `save_article` 同事务完成；插件侧不要直接调数据库或额外调用用量接口。
