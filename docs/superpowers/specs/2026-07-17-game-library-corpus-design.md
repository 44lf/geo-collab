# 游戏库（Game Library）· 生文取材语料底座 · 设计稿

- 日期：2026-07-17
- 状态：**需求已确认（含 L1 范围拍板），待写实现计划（writing-plans）**
- 范围：**L1（脊梁 + writer/配图）**——把「图片库」升级为以**游戏为一等公民**的「游戏库」：新增 `games` / `game_tags` 两张表 + 入库刷新（CLI/cron）+ 2 个检索 MCP 工具 + writer skill 取材契约改造 + 真实截图入 MinIO。目标是让 `/goal` 生文从**预灌的真实游戏库**按标签取材，替掉串行 websearch，提可控性与速度，并用真实截图配图。
- 思路来源：用户提供的 `Downloads/game-search.zip`（独立 game-search 抓取包，爬 TapTap/百度乐玩 → 统一 `Game` 结构）。
- **维度说明**：数据脊梁按**完整「游戏库」格局**设计（Game + 多轴标签 + 语料 + 溯源 + 媒体），面向未来；v1 只落地 L1 表面。L2（策展 UI）/ L3（喂选题、对抗评审验真、视频 storyboard、文章↔游戏反向引用分析）是脊梁日后可插的路线图，**不在本 spec**。

---

## 一、背景与目标

**现状**：`/goal` loop 的 writer 子代理（geo-goal 插件里的 `geo-article-writer`）被生文模板的「真实游戏库」要求驱动，动笔前**自己 WebSearch 逐个验证游戏真实性**。串行生文时每篇都搜、很慢；而且用哪些游戏不可控、每次结果漂。配图侧现有 `web_fallback`：库里没有的游戏，按需 `get_or_create_companion_category(游戏名)` 建陪衬栏目 + 百度千帆搜**一张随机图**补上——图未必贴题。

**问题**：
- 慢：websearch 卡在串行生文关键路径上。
- 不可控：游戏选择靠模型即兴，运营无法约束取材范围。
- 配图糙：web_fallback 是随机百度图，不是真实游戏截图。

**目标**：立一个**预灌的、以游戏为中心的知识底座**，让生文按标签**从库里取真实游戏 + 语料**：
- 快：取材变成一次 DB 聚合查询，不再串行 websearch。
- 可控：游戏来自离线灌好的库；运营可通过入库范围/（日后）策展约束取材宇宙。
- 增彩：每个游戏带评分/简介/（预留）精彩评论/热点等语料，供 writer 增彩。
- 配图真：真实截图预灌进游戏的陪衬栏目，配图直接命中真截图、不触随机图。

**关键约束（用户已确认）**：
- **游戏为一等公民**：新建独立 `games` + `game_tags` 表；图片库（`StockCategory`/`StockImage`/MinIO）作为**媒体底座**被复用、**不合并、不删**。矩阵的主推/品牌桶原样保留。
- **标签驱动检索**：writer 按矩阵选「1-2 个相关标签 + 几个无关标签」，`query_games_by_tags` 做 DB 聚合先筛一批候选，再由 Claude 从候选里挑。**选哪些标签的策略写在 skill 里**；**可选什么标签**由 MCP 工具查。
- **v1 存扁平标签**：game-search 源只给扁平 tag，v1 就存扁平 tag，`game_tags.axis` **预留空列**（多轴留给日后富化，不为造多轴卡住 L1）。
- **语料保留原生 + 预留空列**：存 game-search 原生字段（名称/评分/平台/评论数/图标/截图 URL/官方简介）；另加 `highlight_comments` / `related_hotspots` 两个**可空列，v1 默认 null**，等日后别的脚本填。
- **入库=GEO 内置定时任务 + 手动 CLI**：抓取逻辑折叠进 GEO 后端新模块；由 `create_app()` 启动一个后台守护线程**定时刷新**（复用现有 `ai_generation/sync_scheduler.py` 模式：env 开关 + interval + 幂等 upsert + 单目标失败隔离），**不需要每次手动执行**；另留 `python -m server.scripts.ingest_games` CLI 供一次性 seed / backfill / 补抓。
- **截图 v1 入 MinIO**：真实截图下载入库，配图优先用真截图。
- **强制取材 = 库优先 + websearch 兜底**：候选 ≥ 阈值走库、彻底不搜；候选 < 阈值 writer 回退现有 WebSearch（安全网，不移除）。
- **配图零改动**：复用现有 `get_or_create_companion_category` + 确定性落图，配图代码不改。

---

## 二、数据流总览

### 入库（内置定时任务，与生文解耦）

```
create_app() 启动后台守护线程 game_library.scheduler.start_game_ingest(session_factory)
   │   每隔 GEO_GAME_INGEST_INTERVAL_SECONDS 跑一轮 run_ingest_once（wait→run，幂等）
   │   （手动补抓：python -m server.scripts.ingest_games --source taptap --category 经营 --pages N，同一份 service）
   ▼
game_library.sources.{taptap,baidu}.search(...)   # 折叠自 game-search.zip，http_direct 出网
   │  → list[Game]（源生结构：name/score/tags/platforms/comment_count/icon_url/screenshot_urls/description）
   ▼
game_library.service.upsert_game(db, game)  （幂等）
   ① upsert games 行（UNIQUE(source, source_game_id) 去重键）
   ② 重建 game_tags（删旧 + 插新，扁平 tag，axis=null）
   ③ get_or_create_companion_category(game.name)  → 拿/建陪衬栏目（复用 image_library）
   ④ 逐 screenshot_urls 下载 → store_image_bytes(...)  入 MinIO（按 URL/minio_key 去重）
   ⑤ 回写 games.stock_category_id = 栏目.id；stamp first_seen_at / last_verified_at
```

### 取材（生文时，在 /goal writer 子代理内）

```
geo-article-writer 子代理（每篇全新上下文）:
   ① list_game_tags()                     # 知道可选什么标签（tag + game_count）
   ② skill 逻辑按矩阵选标签: 1-2 相关 + 几个无关 → include_tags
   ③ query_games_by_tags(include_tags, exclude_tags?, min_score?, limit)   # DB 聚合筛候选
   ④ 候选数 ≥ 阈值 → 从候选里挑游戏、用其 description/score/tags/(语料) 写正文
      候选数 <  阈值 → 回退现有 WebSearch 验证路径（安全网）
   ⑤ report_event 打点区分「走库检索 / 回退 websearch」
   ⑥ save_article(...)；返回 game_positions（游戏名对齐陪衬栏目名）
```

### 配图（零改动，复用现有链路）

```
orchestrator → ai_illustrate_article(game_positions=..., web_fallback=True)
   → 现有 get_or_create_companion_category(游戏名) 命中【已预灌真实截图】的陪衬栏目
   → pick_image_id 取真实截图（不触 Baidu）
   → 库里没有的游戏仍走 web_fallback 兜底（行为不变）
```

---

## 三、数据模型（新增 migration）

新模块 `server/app/modules/game_library/`，ORM 在 `models.py`。**媒体仍用 `image_library` 的 `StockCategory`/`StockImage`**。

### `games` — 游戏户口本

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| `id` | int PK | | |
| `source` | str(20) | not null | `taptap` / `baidu` |
| `source_game_id` | str(100) | not null | 源站游戏 id |
| `name` | str(200) | not null | 规范中文名（对齐 `get_or_create_companion_category` 的精确名命中键） |
| `name_normalized` | str(200) | index | 归一化名（去《》「」弯引号/前缀），去重/匹配用 |
| `score` | float | nullable | 评分 |
| `comment_count` | int | nullable | 评论数 |
| `platforms` | JSON | nullable | 平台列表 |
| `icon_url` | str(1000) | nullable | 图标 URL |
| `screenshot_urls` | JSON | nullable | 原始截图 URL（溯源，即使已入 MinIO 也留） |
| `description` | Text | nullable | 官方简介 |
| `stock_category_id` | int FK→stock_categories.id | nullable, ondelete SET NULL | 该游戏的陪衬栏目（真实截图落这里） |
| `highlight_comments` | JSON | nullable, **默认 null** | 预留语料：精彩评论（日后脚本填） |
| `related_hotspots` | JSON | nullable, **默认 null** | 预留语料：相关热点（日后脚本填） |
| `is_active` | bool | default True | 软禁用/下架 |
| `first_seen_at` | datetime | default utcnow | 首次入库 |
| `last_verified_at` | datetime | nullable | 最近一次刷新核验到 |
| `created_at` / `updated_at` | datetime | | |

- **UNIQUE(source, source_game_id)** — upsert 去重键。
- `name_normalized` 归一化函数**复用** `ai_format._normalize_game_name`（保证与配图命中键一致，不另造一套）。

### `game_tags` — 归一化标签（供聚合）

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | int PK | | |
| `game_id` | int FK→games.id | index, ondelete CASCADE | |
| `tag` | str(100) | index | 扁平标签原文 |
| `axis` | str(20) | nullable, **默认 null** | 预留多轴（类型/题材/玩法/调性…），v1 不填 |

- **UNIQUE(game_id, tag)**。
- 标签词表查询：`SELECT tag, COUNT(*) FROM game_tags GROUP BY tag`。
- 按标签查游戏（OR 命中）：`JOIN game_tags ... WHERE tag IN (:include) ... GROUP BY game_id`。

> **为什么独立表而非堆到 StockCategory**：游戏有 source/score/平台/去重键/多标签聚合需求，与「栏目」语义相距甚远；独立表让聚合查询干净、语义清晰，且截图仍落图片库 → 配图链路零改动、真正「扩展图片库」。

---

## 四、入库模块 + 刷新

### 目录结构（新增）

```
server/app/modules/game_library/
  __init__.py
  models.py        # Game / GameTag ORM
  schemas.py       # 入库/检索 Pydantic
  service.py       # upsert_game / query_games_by_tags / list_game_tags 的服务实现
  scheduler.py     # 定时入库后台线程：run_ingest_once / start_game_ingest / stop_game_ingest
  router.py        # MCP-token 只读检索端点（给 MCP 工具用）+ 可选入库触发端点
  types.py         # 折叠自 game-search: Game dataclass + 常量
  registry.py      # 折叠自 game-search: SOURCES 注册表
  sources/
    __init__.py
    baidu.py       # 折叠自 game-search
    taptap.py      # 折叠自 game-search
server/scripts/ingest_games.py   # CLI 入口
```

### `service.upsert_game(db, game: Game) -> Game(ORM)`（幂等）

1. 按 `(source, source_game_id)` 查现有行：有则更新字段，无则插入。
2. 重建 `game_tags`：删该 game_id 旧标签 + 按 `game.tags` 插新（去重）；`axis=null`。
3. `cat = get_or_create_companion_category(db, game.name)`（复用 image_library service）。
4. 逐 `game.screenshot_urls` 下载 → `store_image_bytes(db, cat, data, content_type, source_url=url, width, height)`；**去重**：该栏目下已有同 `source_url`/`minio_key` 则跳过（避免重复刷新灌重复图）。
5. 回写 `games.stock_category_id = cat.id`、`last_verified_at = utcnow()`。
6. 出网/下载失败 best-effort：单图失败跳过、不整体失败（与 web_fallback 同风格）。

> **出网**：sources 是 `http_direct`（urllib/requests），与现有 `shared/baidu.py` 千帆出网同类。运行环境为 GEO 后端（conda `geo_xzpt` + GEO settings 提供 DB/MinIO）。截图下载走短超时 + best-effort。

### 定时入库（`scheduler.py`，复用 `sync_scheduler.py` 模式）

- `run_ingest_once(session_factory) -> {"targets", "upserted", "failed"}`：**纯函数式扫一轮、可单测**——遍历「入库目标清单」（source + category），逐目标 `sources.search(...)` → 逐游戏 `upsert_game`；单目标失败隔离（rollback + 记日志，不影响其它目标），不休眠。测试直接调它并 monkeypatch `sources.*.search`，不跑真休眠、不真出网。
- `start_game_ingest(session_factory) -> bool`：按 `GEO_GAME_INGEST_SCHEDULER_ENABLED` 起 daemon 线程；循环 `wait(interval) → run_ingest_once`（先等再抓，避免一启动就打外站；停止事件可立即唤醒）。`interval = max(下限, GEO_GAME_INGEST_INTERVAL_SECONDS)`，默认建议 **6 小时**（游戏门户变化慢，一天几次足够）。
- `stop_game_ingest()`：测试 / 优雅关闭用。
- **启动位置**：`create_app()` 里与 `start_auto_sync` 并列启动（web 进程内，无需独立 worker；不依赖浏览器，与问题池同步同类）。
- **入库目标清单**（抓哪些 source×category）：v1 走**配置/env 驱动**（如 `GEO_GAME_INGEST_TARGETS` JSON 或模块常量种子清单，覆盖矩阵题材：经营/养成/国风等）。DB 驱动的可编辑目标清单留给 L2 策展。
- **多进程 caveat**（照抄现有模式）：每个 web 进程各起一个入库线程，`upsert_game` 幂等、重复无害但浪费；生产建议单进程跑 web，或后续加进程级租约（与 `sync_scheduler` 同注意事项）。

### CLI `python -m server.scripts.ingest_games`（手动 seed / backfill）

- 定时任务之外的**手动补抓**：首次种库、临时抓某个新分类、backfill。
- 参数：`--source {taptap,baidu}`、`--category <中文分类名>`、`--pages N`、`--limit N`、`--min-score`（可选）。
- 用 `SessionLocal` 短 session（in-process，参照其它 `server/scripts/*`），调**同一份** `service.upsert_game`，与定时任务共用核心；打印/汇总 upsert/入图计数。
- 幂等：与定时任务同源，重复运行只更新不增重。

### 可选：MCP-token HTTP 触发端点

- `POST /api/mcp/game-library/ingest`（MCP-token 保护，异步起后台 job 返回 job_id），供远端/无 shell 手动触发一轮。**非 FastMCP 工具，不计入 `MCP_TOOLS_COUNT`**。有了内置定时任务后**优先级下降**，v1 可后置。

---

## 五、检索 MCP 工具（新增 2 个 catalog 只读）

两个都是 FastMCP `@mcp.tool`，后端走 `game_library/router.py` 的 MCP-token 只读端点（与其它 catalog 工具同构）。

### `list_game_tags(limit: int = 200) -> {tags: [{tag, game_count}]}`

- `SELECT tag, COUNT(DISTINCT game_id) FROM game_tags JOIN games ON ... WHERE games.is_active GROUP BY tag ORDER BY game_count DESC LIMIT :limit`
- 用途：writer 知道**可选什么标签**（不凭空编标签）。

### `query_games_by_tags(include_tags, exclude_tags=None, min_score=None, limit=20) -> {games: [...]}`

- `include_tags`：**OR 命中**——游戏含任一即入池。
- `exclude_tags`（可选）：游戏含任一即排除。
- `min_score`（可选）：分数下限。
- `limit`：返回上限。
- 排序：先按 `score desc` 再按 `comment_count desc`（稳定、可复现；随机多样性由 writer 选标签时的「无关标签」带来，不靠 SQL 随机）。
- 每项返回：`{game_id, name, score, tags, description, stock_category_id, icon_url, screenshot_urls, highlight_comments, related_hotspots}`。
- 「1-2 相关 + 几个无关」= skill 把这些标签一起塞进 `include_tags` 凑一池，Claude 再从池里挑。

> `MCP_TOOLS_COUNT`：33 → **35**（`connect_router.py:MCP_TOOLS_COUNT` + CLAUDE.md 的 catalog 清单同步 +2）。

---

## 六、writer skill 契约（改 geo-goal 插件 `geo-article-writer`）

> **跨仓**：writer 是 **geo-goal 插件** marketplace 分发的 skill，**不在本仓**。本 spec 覆盖后端（模块/表/MCP 工具/CLI）；writer 契约在此写清，插件侧照契约改 + 重发版（另走插件仓流程）。

动笔前，把「自己 WebSearch 验游戏」替换为**库优先取材**：

1. `list_game_tags()` 拿可选标签。
2. skill 逻辑按矩阵选标签：**1-2 个相关标签**（矩阵题材，如餐厅养成记→经营/养成）+ **几个多样性标签**，合成 `include_tags`。选标签策略是 skill 层规则、可迭代。
3. `query_games_by_tags(include_tags, exclude_tags?, min_score?, limit)` 拿候选真实游戏 + 语料。
4. **候选数 ≥ 阈值**（默认建议 `>=4`，skill 常量可调）→ 从候选里挑游戏、用 `description/score/tags/(highlight_comments 等语料)` 写正文；**不 websearch**。
5. **候选数 < 阈值** → 回退现有 WebSearch 验证路径（安全网，不移除）。
6. `report_event` 打点，`event_type` 区分 `games_from_library` / `games_fallback_websearch`，payload 记候选数、选中标签、命中游戏 —— 让「这篇走了库还是回退了搜」成为可查事实。
7. `save_article(...)`；正文结构判定与 `game_positions` 返回**沿用现有约定**（每款各占 `##` 小标题 → 收 game_positions，游戏名与陪衬栏目名对齐；散文 → None）。

---

## 七、配图链路（零改动）

- writer 返回 `game_positions` → orchestrator 调 `ai_illustrate_article(game_positions=..., web_fallback=True)`（现有主循环配图决策块不变）。
- 现有 `_web_fallback_decide` → `get_or_create_companion_category(游戏名)` **命中已预灌真实截图的陪衬栏目** → `pick_image_id` 取真截图，**不触 Baidu**。
- 库里没有的游戏仍走 web_fallback 联网兜底（行为与现状一致）。
- **不改配图代码**：真实截图入库时用的就是配图同一套栏目命中键（精确游戏名）与 `store_image_bytes`，天然对齐。

---

## 八、组件边界与依赖

| 单元 | 职责 | 依赖 | 可独立测试点 |
|---|---|---|---|
| `game_library/sources/*` + `registry`/`types` | 抓 TapTap/百度 → 统一 `Game` | 出网（http_direct） | 解析真实响应样例 → Game 字段 |
| `game_library/service.upsert_game` | 幂等 upsert + 标签重建 + 截图入 MinIO + 回写栏目 | image_library service、MinIO | upsert 幂等/去重、截图去重、栏目回写 |
| `game_library/service.query_*` / `list_game_tags` | 聚合检索 | DB | OR 命中、exclude、min_score、排序 |
| `game_library/router` | MCP-token 只读端点（+可选入库触发） | require_mcp_token | 鉴权、返回结构 |
| `server/scripts/ingest_games` | CLI 编排 | service、SessionLocal | 端到端灌一批（mysql mark） |
| MCP 工具 `list_game_tags`/`query_games_by_tags` | LLM-facing schema | router 端点 | 工具 schema/透传 |
| writer skill 契约 | 库优先取材 + 兜底 | MCP 工具 | 插件侧，人工/评审验证 |

---

## 九、测试（server/tests，`@pytest.mark.mysql`）

- `run_ingest_once` 扫一轮：monkeypatch `sources.*.search` 返回假 Game，断言 upsert 计数、单目标失败隔离（一个目标抛错不影响其它目标）。
- `upsert_game` 幂等：同 `(source, source_game_id)` 重跑只更新不增重；标签重建正确。
- 截图入库去重：同 URL 重跑不重复灌图；`stock_category_id` 正确回写。
- `query_games_by_tags`：OR 命中、`exclude_tags`、`min_score`、排序、`is_active` 过滤。
- `list_game_tags`：计数正确、按 game_count 降序。
- MCP 端点鉴权：无 token 401；有 token 返回结构正确。
- 配图命中：预灌某游戏截图后，走 `game_positions` 配图命中真截图、不触网（可 mock 千帆断言未调用）。
- MinIO：测试环境按现有 image_library 测试约定（若无 MinIO 则相应用例跳过/mock store）。

---

## 十、迁移与文档

- **Alembic**：新增迁移建 `games` / `game_tags`（跟随当前迁移头，不写死版本号）。`stock_category_id` FK ondelete SET NULL、`game_id` FK ondelete CASCADE。
- **新增 env（`GEO_` 前缀，pydantic-settings）**：
  - `GEO_GAME_INGEST_SCHEDULER_ENABLED`（默认 `false`，与其它调度器同风格：默认关，生产显式开）
  - `GEO_GAME_INGEST_INTERVAL_SECONDS`（默认 `21600` = 6h，代码 `max()` 下限保护）
  - `GEO_GAME_INGEST_TARGETS`（入库目标清单，JSON；缺省回落模块内种子清单）
  - 时区沿用现有 `GEO_SCHEDULER_TZ`
- **CLAUDE.md**：
  - `create_app()` 启动的后台线程清单里加「定时入库」（与问题池同步、pipeline 调度并列），并写明**多进程 caveat**（别跑多实例 web，或后续加租约）。
  - Domain Modules 段加 `game_library/` 一节。
  - MCP「Tool 三组」catalog 列表 +2（`list_game_tags` / `query_games_by_tags`），`MCP_TOOLS_COUNT` 33→35。
  - 路由清单 `/api/mcp/game-library/*`（若加入库触发端点）。
- **`server/mcp/tools/`**：新增 2 个只读工具函数（catalog 组），`server.py` 触发注册。
- **`connect_router.py:MCP_TOOLS_COUNT`** 改 35。

---

## 十一、范围外（本 spec 不做）

- **L2 策展 UI**：图片库 tab 升级成游戏库工作台（浏览/改标签/改语料/拉黑/标矩阵偏好）。
- **L3 下游复用**：喂选题生成、对抗评审验真、视频 storyboard、文章↔游戏反向引用分析。
- **多轴标签富化**：`game_tags.axis` 的实际分类填充（v1 只留空列）。
- **语料富化脚本**：`highlight_comments` / `related_hotspots` 的抓取填充（v1 默认 null）。
- **DB 驱动的可编辑入库目标清单 + 每目标独立节奏 / 进程级租约**：v1 定时任务走 env/常量种子清单 + 单进程，目标清单的运营可编辑化与多进程租约留后。

以上均为脊梁日后可插的路线图，不影响 L1 落地。

---

## 十二、开放项 / 待实现计划细化

- 入库目标清单（source×category）默认放哪：`GEO_GAME_INGEST_TARGETS` env（JSON） vs 模块内种子常量 —— writing-plans 阶段定，倾向「env 覆盖、缺省回落常量」。
- 定时任务默认 interval（建议 6h）与下限值 —— 落地可调。
- writer 阈值默认值（建议 `>=4`）与「多样性标签」条数（建议 1-2 相关 + 2-3 无关）——skill 常量，落地可调。
- 截图入库的尺寸/数量上限（每游戏最多存几张、单图压缩策略）——参照 image_library 现有约定。
- 可选 HTTP 入库触发端点是否 v1 就做（有定时任务后优先级下降，默认后置）。
