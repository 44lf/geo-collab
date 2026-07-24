# 游戏库入库托管化 + 图片库栏目导入 · 设计稿

> 分支：`feat/game-library-corpus`　日期：2026-07-21
> 前置：`docs/superpowers/specs/2026-07-17-game-library-corpus-design.md`（语料底座 L1）
> 关联：`docs/superpowers/plans/2026-07-20-game-library-corpus-review-fixes.md`（本稿并入其 C2/M1/M2/M3）

## 1. 背景与动机

游戏库语料底座已落地：`games` / `game_tags` 表 + 复用 `stock_categories` / `stock_images` 存截图，
`upsert_game` 跨源并集合并，MCP `query_games_by_tags` 供 `/goal` 生文取材。但入库侧还缺两块：

1. **抓取任务不可管理**：现有 `game_library/scheduler.py` 是 env 驱动的隐形后台线程
   （`GEO_GAME_INGEST_SCHEDULER_ENABLED` 默认关、固定间隔、目标来自 `GEO_GAME_INGEST_TARGETS`
   / `SEED_TARGETS`），而且**按体裁关键词**抓 top-N（"经营"→前 N 个游戏），不是一份可控清单。
   运营无法设时间段、无法手动触发、看不到跑没跑。
2. **存量图片库没进游戏库**：原图片库有大量图，以栏目（`stock_categories`）区分，只有图、无游戏信息。
   希望把栏目"丝滑"折进游戏库——栏目名当游戏名，图片带过来，其余字段暂 null。

本稿把入库改造成**可管理的托管抓取**，并把图片库栏目作为托管抓取的**种子清单**。

## 2. 目标 / 非目标

**目标**
- 一次性、幂等地把 `stock_categories` 导入为 `games`（栏目名→游戏名，归一化去重，图片零拷贝复用）。
- 托管抓取：运营可在 UI 设每日时间窗 + 每轮数量 + 间隔，后台按软 LRU 轮转刷新全库；可手动"立即抓一批"。
- 抓取语义从"按体裁发现"切换为"**按名刷新迁移集**"（wire 现有 `search_by_name`）。
- 把 `upsert_game` 的"下载搬出事务"重构一并做掉（关闭评审清单 C2/M1/M2/M3）。

**非目标**
- 不做"按体裁发现新游戏"的定时调度（旧 `SEED_TARGETS` 路径退役休眠，保留为手动 CLI 种子）。
- 不改 MCP 检索侧（`list_game_tags` / `query_games_by_tags` 不动）。
- 不引入多命名调度、多 web 实例去重（沿用单 web 进程约束，见 §11）。
- 不做游戏名与栏目名的双向同步（见 §6 的 FK-为唯一关联决策）。

## 3. 已定决策（四岔口 + 两修正）

| 决策点 | 结论 |
|---|---|
| 抓取节奏 | **每日时间窗 + 每轮限量轮转**（软 LRU，镜像 `accounts/keepalive.py`） |
| 导入触发 | **幂等 CLI 一次性数据迁移**（可重跑，覆盖后续新栏目） |
| 与旧逻辑关系 | **取代**：只按名刷新迁移集；按体裁发现退役休眠 |
| 手动触发范围 | **一批 K 个**（软 LRU 优先最久没刷）、异步后台、绕过时间窗 |
| 关联方式（修正 1） | **FK `stock_category_id` 为唯一关联**，不依赖"名字相等" |
| `screenshot_urls` 定位（修正 2） | **原始 URL 台账（溯源用）**，展示以 `stock_images` 为准 |
| `upsert_game` 重构 | **并进本 feature**：下载搬出事务 + 短事务写库 |

## 4. 数据流

```
① 导入 CLI（一次性 / 可重跑，纯 DB 无联网）
   stock_categories ──栏目名→归一化去重──▶ games(name + stock_category_id，其余 null)
                          图片零拷贝：靠 stock_category_id 复用现有 StockImage

② 托管抓取（DB 配置 + 后台线程，镜像 keepalive）
   每日时间窗内 ── 软 LRU 取最久没刷的游戏（每晚≤K）──▶ search_by_name(taptap→baidu)
        命中 ─▶ 下载截图(session 外) ─▶ 短事务 upsert_game 合并 + last_verified_at=now
        未命中 ─▶ 仅 last_verified_at=now（不再每轮重锤）
   手动"立即抓一批" ──▶ 同一批逻辑，绕过时间窗，异步后台跑

③ 按体裁发现（旧 SEED_TARGETS/env）── 退役休眠，保留为手动 CLI 种子
```

## 5. 组件 1 · 图片库→游戏库 导入（幂等 CLI）

**新增**：`server/app/modules/game_library/importer.py`（服务）+ `server/scripts/import_image_library_games.py`（CLI）。

`import_categories_as_games(db) -> dict`：
- 遍历所有 `StockCategory`；对每个栏目 `norm = _normalize_game_name(cat.name) or cat.name`
  （复用 `service.py` 现用的同一归一化函数，保证与抓取侧身份键一致）。
- 按 `name_normalized == norm` 查 `Game`：
  - **不存在** → 建 `Game(name=cat.name, name_normalized=norm, stock_category_id=cat.id,
    sources=[], platforms=[], screenshot_urls=[], use_count=0, is_active=True,
    first_seen_at=now)`；`last_verified_at` 留 **null**（→ §7 软 LRU 首批优先）。其余字段 null。
  - **存在且 `stock_category_id` 为空** → 挂上 `cat.id`（attach）。
  - **存在且已挂栏目** → 跳过（去重）。
- 纯 DB、无联网、无 MinIO：图片已是栏目内 `StockImage` 行，游戏靠 FK 复用。
- **幂等**：重跑只补新栏目 / 未挂栏目的游戏，跳过已建。天然覆盖 AI 配图 web-fallback 自建的陪衬栏目。
- 竞态：`Game` 创建 / attach 用 `begin_nested` + `except IntegrityError` 回退查询（对齐 §8 M2 兜底）。
- 返回 `{scanned, created, attached, skipped}`；CLI 打印汇总，`created==0 and attached==0` 也退 0（幂等重跑正常）。

**归一化撞名边缘**：两个栏目归一化到同名 → 先到者建 game，后到者仅在 game 无栏目时 attach，否则跳过并 `logger.info`。

## 6. 修正 1 · FK 为唯一关联（消除更新异常）

迁移后 `games.name` 与 `stock_categories.name` 会长期相等，若只改一处会产生更新异常。规避：

- **迁移阶段**用名字 match/建 game（一次性），建完把 `stock_category_id` **钉死**。
- **抓取刷新阶段**一律走 `game.stock_category_id` 直接拿桶存图，**不再按名字 re-resolve 栏目**
  （即 `upsert_game` 对"已有 FK 的游戏"跳过 `get_or_create_companion_category` 的按名解析）。
- 游戏权威身份 = `name_normalized` + FK；栏目名退化成纯桶标签，两名字漂移也无害。

> 兼容：仅当游戏 `stock_category_id` 为 **null**（如纯 websearch 无栏目的游戏）时，`upsert_game`
> 才回落到 `get_or_create_companion_category(name)` 按名建桶——保持现有 AI 配图路径不变。

## 7. 组件 2 · 托管抓取配置（DB 单例表 + 调度线程）

### 7.1 新表 `game_ingest_config`（Alembic `0066`，down_revision=`0065_game_library`，种一行 id=1 默认值）

| 字段 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `id` | int PK | 1 | 单例 |
| `enabled` | bool | false | 定时抓取总开关（UI 可切） |
| `window_start` | str(5) | "02:00" | 每日窗起 "HH:MM"（本地 `GEO_SCHEDULER_TZ`） |
| `window_end` | str(5) | "05:00" | 每日窗止（`end<start` 视为跨午夜） |
| `batch_size` | int | 30 | 每晚最多刷新几个（软 LRU 取最旧 K） |
| `min_gap_seconds` | int | 20 | 游戏间有界随机间隔下界 |
| `max_gap_seconds` | int | 90 | 上界 |
| `source_order` | str(50) | "taptap,baidu" | 按名查数据源顺序（首个命中即止） |
| `max_shots` | int | 6 | 每游戏截图上限 |
| `last_run_started_at` | datetime? | null | 状态展示 |
| `last_run_finished_at` | datetime? | null | 状态展示 |
| `last_run_summary` | JSON? | null | `{refreshed, not_found, failed, batch}` |
| `last_run_trigger` | str(12)? | null | "scheduled" / "manual" |
| `updated_at` | datetime | now | onupdate |

模型放 `game_library/models.py`（`GameIngestConfig`）。迁移种入 id=1 那行，读写走服务函数（get 单例恒命中该行）。

### 7.2 调度线程（镜像 `accounts/keepalive.py`）

纯函数（可单测、不休眠、不联网）：
- `in_window(start, end, now)` / `window_start_instant` / `window_end_instant` / `compute_next_gap`
  ——直接照搬 keepalive 语义（跨午夜、cap=剩余窗/剩余数）。
- `select_due_games(db, limit=K) -> list[int]`：`is_active` + `ORDER BY last_verified_at ASC NULLS FIRST`，limit K。

薄 loop（`start_game_ingest` 重写）：
- 每 tick：`enabled` 且 `in_window` 且**本窗已处理 < batch_size** → 取 1 个最旧 due 游戏 → `refresh_one_game`
  → 增计数 → 睡 `compute_next_gap`；否则睡 poll 间隔。
- **本窗计数**：进入新窗口（`window_start_instant` 变化）时重置，把 K 次抓取错峰铺在窗口内。
- 并发守卫：进程内 `threading.Lock`（定时轮与手动触发互斥）。
- 启动时把 `last_run_started_at` 无对应 `finished` 的残留态复位（崩溃恢复，纯展示）。
- 环境总闸沿用 `GEO_GAME_INGEST_SCHEDULER_ENABLED`（是否起线程）；细控全走 DB `enabled`。
- 线程 docstring 补"多实例部署会重复爬、建议单 web 进程跑"告警（对齐 `sync_scheduler.py`）。

## 8. 组件 3 · 按名刷新执行器（wire `search_by_name` + upsert 重构）

### 8.1 `refresh_one_game(session_factory, game_id, *, source_order, max_shots) -> str`（永不抛，单游戏隔离）
1. 短事务读出 `game.name` / `game.stock_category_id`。
2. **联网阶段（session 外）**：按 `source_order` 逐源 `search_by_name(name)`，首个命中即止；
   taptap 命中缺图时补 `get_detail`；再 `download_image` 把截图字节全下好成 `list[(url, data, mime)]`。
3. **短事务写库**：`upsert_game(db, crawled, pre_downloaded=..., category_id=game.stock_category_id)`
   合并 score/标签/截图 + `last_verified_at=now`，一次 commit。
4. 未命中所有源 → 短事务只 `last_verified_at=now`（字段留 null，计 `not_found`，避免每轮重锤）。
5. 异常 → rollback、计 `failed`、返回 `error`；调用方继续下一个（逐游戏 + 逐轮双隔离，落实评审 M3）。
- 每个游戏之间由 loop 的随机 gap 限速（同时解掉 taptap `get_detail` 背靠背无节流的 Minor）。

### 8.2 `upsert_game` 重构（并入，关闭 C2/M1/M2/M3）
- **下载搬出事务**：截图字节在**无 session** 阶段下好（`download_image` 无状态），再开短事务批量
  `store_image_bytes(..., commit=False)` + 一次 flush，杜绝"持连接期间跑 HTTP+MinIO"（C2/M1）。
- 签名加 `pre_downloaded: list[(url,bytes,mime)] | None` 与 `category_id: int | None`：
  有 `category_id`（迁移集游戏）→ 直接用该桶，**不按名 re-resolve**（落实 §6）；无则回落按名建桶。
- `Game` 行创建 / `GameTag` 插入 / `store_image_bytes` 的 `flush/commit` 都补
  `except IntegrityError: rollback + 回退查询已有行`（M2 竞态兜底，参照 `get_or_create_companion_category`）。
- `StockImage` ORM 补 `UniqueConstraint("category_id","source_url_hash", name="uq_stock_images_category_source_hash")`
  （M4：测试库走 `create_all`，补声明防 schema 漂移）。
- `query_games_by_tags` 已在底座补 `selectinload(Game.tags)`（M5 已修，核对即可）。

> **`screenshot_urls` 定位**：`upsert_game` 仍把抓到的原始 URL 并进 `games.screenshot_urls`（台账/溯源），
> 但前端 `GameMaterialPanel` 展示一律走 `listImages({category_id})` 读 `stock_images`（现状即如此，无需改）。

## 9. 组件 4 · API + 前端

**API（user JWT，挂 `router_web.py`，与 MCP-token `router.py` 隔离）**
- `GET /api/game-library/ingest/config` → 单例配置 + `last_run_*` 状态。
- `PUT /api/game-library/ingest/config` → 改配置（校验 HH:MM、`min≤max`、`batch_size/max_shots>0`）。
- `POST /api/game-library/ingest/run` → 手动触发一批：进程内锁忙则 **409 + 当前状态**；
  否则 spawn 后台线程跑 `batch_size` 个（绕过时间窗），返回 **202 + 起始状态**。
- 后台线程复用 `bg_session_factory`（懒导入避循环依赖，对齐现有约定）。

**前端（游戏库 tab，`GameLibraryHeader` 加控件 + `web/src/api/game-library.ts` 补客户端）**
- "抓取设置"面板/弹窗：开关、时间窗、每轮数量、间隔、数据源顺序、截图上限。
- "立即抓取一批"按钮 + 运行指示 + 上次汇总（"上次：刷新 8 / 未命中 2 / 失败 0 · 3 分钟前"）。
- `web/src/types.ts` 补 `GameIngestConfig` 类型。

## 10. 组件 5 · 旧路径处置 / 测试

**退役休眠**
- `run_ingest_once`（按体裁）+ `server/scripts/ingest_games.py` 保留为**手动种子 CLI**，注释标注"非定时"。
- `main.py:500` 的定时线程改跑新批量 loop；`SEED_TARGETS` / `GEO_GAME_INGEST_TARGETS` 留着但不再被定时读。
- CLAUDE.md `game_library/` 段同步：调度从"按体裁定时"改为"按名刷新迁移集 + DB 配置 + 手动触发"。

**测试**
- `test_import_image_library_games.py`：建 / attach / 去重 / 幂等重跑 / 归一化撞名。
- `test_game_ingest_batch.py`：软 LRU 顺序（null `last_verified` 优先）、逐游戏隔离（坏游戏不腰斩批次）、
  未命中 bump `last_verified`、源回退顺序、窗口门控（纯函数）、本窗 K 计数与重置。
- `test_game_ingest_config.py`：配置 CRUD 校验、手动触发 202 + 运行守卫（忙则不重入）。
- `test_game_sources.py`：给 `search_by_name` 补源映射用例（当前死代码，见 §11 风险）。
- `test_game_upsert.py`：补重构后"下载在 session 外"、`category_id` 传入不 re-resolve、`IntegrityError` 兜底。
- `test_image_store_dedup.py`：`UNIQUE(category_id, source_url_hash)` 声明生效。

## 11. 风险 / 依赖 / 注意

- **`search_by_name` 是死代码**：taptap 侧走 `_bootstrap_xsrf` / `_multipart_encode` / `_to_game_from_brand`
  （CSRF + multipart），wire 时可能暴露其脆弱性；baidu 侧较简单。计划应先写源用例跑通、必要时加固，
  再接入刷新执行器。若 taptap 按名查不稳，`source_order` 可退化为 "baidu" 优先。
- **多 web 实例**：窗口 + LRU + 进程内锁不跨进程去重，多实例会重复爬。沿用单 web 进程约束（CLAUDE.md 已警示）。
- **与评审清单 plan 的耦合**：本稿并入 C2/M1/M2/M3/M4/M5（M5 已修）+ 部分 Minor（taptap 节流）。
  若评审 plan 已在另窗口动 `upsert_game`，两边需协调避免重复/冲突。
- **`_normalize_game_name` 私有耦合**（评审 Minor）：导入与抓取都当身份键用；本稿沿用现状，
  提升为公开 API 可选、非阻塞。
- **范式说明**（回应评审提问）：新增 `games`/`game_tags` 不违反范式（拆新实体=规范化）；
  `stock_images.source_url_hash` 派生自 `source_url` 是**故意的合理反规范化**（长 URL 无法直接建索引，
  哈希撑起 `UNIQUE` 去重）；JSON 多值列破的是 1NF 非 3NF，全项目务实惯例；`screenshot_urls` 与
  `stock_images` 的重复靠 §3 修正 2 划清职责（台账 vs 展示）消解。

## 12. 交付顺序建议（供 writing-plans 展开）

1. 迁移 `0066` + `GameIngestConfig` 模型 + 配置服务/校验。
2. `upsert_game` 重构（下载搬出事务 + `category_id`/`pre_downloaded` + IntegrityError 兜底 + `StockImage` UNIQUE 声明）。
3. `search_by_name` 源用例跑通 / 加固。
4. `refresh_one_game` + 纯函数窗口/LRU + `start_game_ingest` 重写（含手动触发路径）。
5. 导入 `importer.py` + CLI。
6. API 端点（config CRUD + run）。
7. 前端设置面板 + 手动按钮 + 类型/客户端。
8. 退役旧路径注释 + CLAUDE.md 同步 + 全量测试。
