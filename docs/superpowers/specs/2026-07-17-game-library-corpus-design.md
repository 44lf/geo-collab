# 游戏库（Game Library）· 生文取材语料底座 · 设计稿

- 日期：2026-07-17
- 状态：**需求已确认（含 L1 范围 + 数据建模拍板 2026-07-20），待写实现计划（writing-plans）**
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
- **游戏为一等公民 + 跨源合并（C）**：新建独立 `games` + `game_tags` 表；`games` 身份键=**归一化游戏名**（`name_normalized` UNIQUE），TapTap/百度同名游戏合并入同一行、截图汇同一栏目、用量不劈开。图片库（`StockCategory`/`StockImage`/MinIO）作为**媒体底座**被复用、**不合并、不删**——但 `stock_images` **加 4 列**（`source_url` 入库去重 + 3 列图片级用量），矩阵的主推/品牌桶原样保留。
- **标签驱动检索**：writer 按矩阵选「1-2 个相关标签 + 几个无关标签」，`query_games_by_tags` 做 DB 聚合先筛一批候选，再由 Claude 从候选里挑。**选哪些标签的策略写在 skill 里**；**可选什么标签**由 MCP 工具查。
- **v1 存扁平标签**：game-search 源只给扁平 tag，v1 就存扁平 tag，`game_tags.axis` **预留空列**（多轴留给日后富化，不为造多轴卡住 L1）。
- **语料保留原生 + 预留空列**：存 game-search 原生字段（名称/评分/平台/评论数/图标/截图 URL/官方简介）；另加 `highlight_comments` / `related_hotspots` 两个**可空列，v1 默认 null**，等日后别的脚本填。
- **入库=GEO 内置定时任务 + 手动 CLI**：抓取逻辑折叠进 GEO 后端新模块；由 `create_app()` 启动一个后台守护线程**定时刷新**（复用现有 `ai_generation/sync_scheduler.py` 模式：env 开关 + interval + 幂等 upsert + 单目标失败隔离），**不需要每次手动执行**；另留 `python -m server.scripts.ingest_games` CLI 供一次性 seed / backfill / 补抓。
- **截图 v1 入 MinIO**：真实截图下载入库，配图优先用真截图。
- **强制取材 = 库优先 + websearch 兜底**：候选 ≥ 阈值走库、彻底不搜；候选 < 阈值 writer 回退现有 WebSearch（安全网，不移除）。
- **配图命中机制不变 + 两处小改**：栏目「按游戏名精确命中」逻辑（`get_or_create_companion_category`）零改动；本次新增**软 LRU 选图**（桶内按 `last_used_at`/`use_count` 取最新鲜的、短期去重）+ **落图回写用量**两处小改。用量用**轻量列 + 软优先**（不加流水表、不硬排除，永不卡死）。

---

## 二、数据流总览

### 入库（内置定时任务，与生文解耦）

```
create_app() 启动后台守护线程 game_library.scheduler.start_game_ingest(session_factory)
   │   每隔 GEO_GAME_INGEST_INTERVAL_SECONDS 跑一轮 run_ingest_once（wait→run，幂等）
   │   （手动补抓：python -m server.scripts.ingest_games --source taptap --category 经营 --pages N，同一份 service）
   ▼
game_library.sources.{taptap,baidu}.search(...)   # 折叠自 game-search.zip，http_direct 出网、纯 stdlib、无登录态
   │  → list[Game]（name/score/tags/platforms/comment_count/icon_url/screenshot_urls/description）
   │  ⚠ taptap 列表【无截图 + 标签占位】→ 逐游戏补 sources.taptap.get_detail(game_id)（N+1）拿真截图/真标签；
   │     百度列表一把梭全有（gameOfficialPic 截图 + 真 gameTags），无需补详情
   ▼
game_library.service.upsert_game(db, game)  （幂等，game 须为已补详情的完整 Game）
   ① upsert games 行（UNIQUE(name_normalized) · 跨源【并集合并】：score/comment_count 取 max、description/icon 挑非空、sources/platforms/screenshot_urls 累积）
   ② 并集插入 game_tags（INSERT IGNORE，靠 UNIQUE(game_id,tag) 去重、【不 delete-all】→ 保留别源标签；axis=null）
   ③ get_or_create_companion_category(game.name)  → 拿/建陪衬栏目（复用 image_library）
   ④ 逐 screenshot_urls 下载 → store_image_bytes(...)  入 MinIO（按 (category_id, source_url) 去重）
   ⑤ 回写 games.stock_category_id = 栏目.id；stamp first_seen_at / last_verified_at
       （用量列 use_count/last_used_* 不在入库触碰，只在生文消费时回写）
```

### 取材（生文时，在 /goal writer 子代理内）

```
geo-article-writer 子代理（每篇全新上下文）:
   ① list_game_tags()                     # 知道可选什么标签（tag + game_count）
   ② skill 逻辑按矩阵选标签: relevant_tags(1-2 相关,准入) + diversity_tags(几个多样,只扩散)
   ③ query_games_by_tags(relevant_tags, diversity_tags?, exclude_tags?, min_score?, limit)  # relevant 准入 + 均衡排序
   ④ 候选数 ≥ 阈值(只数 relevant 命中池) → 从候选挑游戏、用 description/score/tags/(语料) 写正文、记下 game_id
      候选数 <  阈值 → 回退现有 WebSearch 验证路径（安全网）
   ⑤ report_event 打点区分「走库检索 / 回退 websearch」
   ⑥ save_article(..., selected_games=[{game_id,name}])  # 后端同事务 bump games 用量
      返回 game_positions（游戏名对齐陪衬栏目名）
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

### `games` — 游戏户口本（跨源合并：一个逻辑游戏一行）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| `id` | int PK | | |
| `name` | str(200) | not null | 规范中文名（对齐 `get_or_create_companion_category` 的精确名命中键） |
| `name_normalized` | str(200) | **UNIQUE** | 归一化名（去《》「」弯引号/`游戏N、`前缀）——**合并/去重身份键** |
| `sources` | JSON | nullable | 溯源数组 `[{source, source_game_id, url}]`，跨源累积（取代早稿的单列 `source`/`source_game_id`） |
| `score` | float | nullable | 代表评分（多源合并取 `max`，确定性、刷新不抖） |
| `comment_count` | int | nullable | 评论数 |
| `platforms` | JSON | nullable | 平台列表 |
| `icon_url` | str(1000) | nullable | 图标 URL |
| `screenshot_urls` | JSON | nullable | 原始截图 URL（溯源，即使已入 MinIO 也留；跨源累积） |
| `description` | Text | nullable | 官方简介 |
| `stock_category_id` | int FK→stock_categories.id | nullable, ondelete SET NULL | 该游戏的陪衬栏目（真实截图落这里） |
| `highlight_comments` | JSON | nullable, **默认 null** | 预留语料：精彩评论（日后脚本填） |
| `related_hotspots` | JSON | nullable, **默认 null** | 预留语料：相关热点（日后脚本填） |
| `use_count` | int | not null, **default 0** | **取材用量**：被写进过几篇文章 |
| `last_used_article_id` | int FK→articles.id | nullable, ondelete SET NULL | 最近采用它的文章 |
| `last_used_at` | datetime | nullable | 最近采用时间（取材均衡排序用，免 join 文章表） |
| `is_active` | bool | default True | 软禁用/下架 |
| `first_seen_at` | datetime | default utcnow | 首次入库 |
| `last_verified_at` | datetime | nullable | 最近一次刷新核验到 |
| `created_at` / `updated_at` | datetime | | |

- **UNIQUE(`name_normalized`)** — 跨源合并 + upsert 去重键。**同一逻辑游戏一行**：TapTap/百度同名游戏合并入同一行、截图汇入同一陪衬栏目，`use_count` 不被劈成两半（否则取材均衡算不准）。
- `name_normalized` 归一化**复用** `articles/formatting/document.py:_normalize_game_name`（与配图命中 / heading 匹配同一套，不另造）。
- **取材均衡**：`use_count`/`last_used_at` 供检索排序「少用 + 近期没用」优先（详见第五、七节的读写逻辑）。
- **决策修订（2026-07-20 建模讨论）**：身份键从早稿 `UNIQUE(source, source_game_id)` 改为 `name_normalized`（跨源合并 C）；单列 `source`/`source_game_id` 并入 `sources` JSON；新增 `use_count`/`last_used_article_id`/`last_used_at` 三列做取材均衡。

### `game_tags` — 归一化标签（供聚合）

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | int PK | | |
| `game_id` | int FK→games.id | index, ondelete CASCADE | |
| `tag` | str(100) | index | 扁平标签原文 |
| `axis` | str(20) | nullable, **默认 null** | 预留多轴（类型/题材/玩法/调性…），v1 不填 |

- **UNIQUE(game_id, tag)**。
- **跨源并集**：多源标签用 `INSERT IGNORE` 并入同一 game（**不 delete-all**），`UNIQUE(game_id, tag)` 天然去重——百度真标签 ∪ TapTap `get_detail` 真标签，命中面更大。合并策略详见第四节。
- 标签词表查询：`SELECT tag, COUNT(*) FROM game_tags GROUP BY tag`。
- 按标签查游戏（OR 命中）：`JOIN game_tags ... WHERE tag IN (:include) ... GROUP BY game_id`。

> **为什么独立表而非堆到 StockCategory**：游戏有 source/score/平台/去重键/多标签聚合需求，与「栏目」语义相距甚远；独立表让聚合查询干净、语义清晰，且截图仍落图片库 → 配图命中链路基本不动、真正「扩展图片库」。
>
> **X（并存）vs Y（吸收）——选 X**：讨论中比过两条路——X＝`games` 与 `stock_categories` 并存、经 `stock_category_id` 外键连（配图命中链零改动）；Y＝`games` 直接持有 bucket、`stock_images` 外键改挂 `games.id`（单一实体无冗余，但配图命中键要从「按名找 category」改成「按名找 game」、有回归风险）。**选 X**：以「一层 name↔name 的轻微冗余」换「配图命中机制零改动」，这套「按名精确命中」是之前踩坑换来的，不动它。

### `stock_images` — 现有表加 4 列（复用为游戏截图 + 用量账本）

图片库 `stock_images` **保留、原样承载游戏截图**（`games → stock_category → stock_images` 一条链：游戏经陪衬栏目**间接**持有多张截图，一对多由栏目中转，栏目扛 MinIO bucket）；在现有列上**加 4 列**：

| 列 | 类型 | 约束/默认 | 用途 |
|---|---|---|---|
| `source_url` | str(1000) | nullable（**不建索引**） | 溯源原始 URL（人读/调试）。VARCHAR(1000) utf8mb4 = 4000B 超 InnoDB 索引键上限 3072B，不能直接建索引 |
| `source_url_hash` | CHAR(64) | nullable | `sha256(source_url)` hex——去重键的可索引替身 |
| `use_count` | int | not null, **default 0** | **配图用量**：这张图被插进过几篇 |
| `last_used_at` | datetime | nullable | **短期去重**的时间窗判据 |
| `last_used_article_id` | int FK→articles.id | nullable, ondelete SET NULL | 最近落在哪篇 |

- **入库去重 = `UNIQUE(category_id, source_url_hash)`（二轮评审改正）**：普通索引挡不住并发重复，唯一约束才是硬去重闸；`source_url` 太长不能直接进唯一键，故 hash 一列做替身。仍补早稿「按 `minio_key` 去重」的洞（`minio_key` 每次随机 uuid、永不命中）。
- `stock_categories` **完全不动**（仍扛 MinIO bucket + 「按游戏名精确命中」）。
- `store_image_bytes` 已有 `source_url=` 入参（现塞在 description 文本里），改为写入 `source_url` + `source_url_hash` 两列；批量入库用**不主动 commit** 的底层写入变体（见第四节事务纪律），web_fallback 现有单图路径保持 commit-per-call 不变。

### 用量账本读写逻辑（游戏级 + 图片级双粒度）

- **写（消费那一刻）**：
  - 配图把某张截图插进文章 → 该 `stock_image` 的 `use_count += 1`、`last_used_at = now`、`last_used_article_id = 文章id`。
  - 游戏被采用进一篇文章 → 同理 bump `games` 的 `use_count`/`last_used_article_id`/`last_used_at`。
- **读（选图 / 选游戏）——软优先、不硬排**：
  - `ORDER BY last_used_at ASC（MySQL 里 NULL 最小 → 从没用过的天然排最前）, use_count ASC`，取最「新鲜」的；只剩旧图/旧游戏就退用最久没碰的那个 → **永不卡死**。
  - 「近 N 天回避」只作**排序权重**，不作铁闸（某游戏截图少、同周写多篇时仍能出图）。
- **决策修订（2026-07-20）**：去重强度选**轻量列 + 软 LRU**（不加 `image_usage` 流水表）；判据用 `last_used_at` 时间戳而非文章/task ID 距离（后者与总产量耦合、阈值随生成速度漂，且 task↔article 非同维度）。若日后要「严格保证窗口内零重复」或用量报表，再引入流水表（本 spec 范围外）。

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

> **前置·TapTap 补详情（N+1）**：TapTap 列表接口（by-tag）**不返回截图、标签是占位**（只填查询分类名）；真实截图 + 完整标签要**逐游戏调 `sources.taptap.get_detail(game_id)`**——仅对**将入库的** taptap 游戏调（别对整页候选调，太贵）。百度列表接口一把梭全有（`gameOfficialPic` 截图 + 真 `gameTags`），无需补详情。故 `upsert_game` 收到的 `game` 应是**已补详情的完整 Game**（screenshot_urls / tags 非空）。

> **跨源并集合并（C）**：同一逻辑游戏（`name_normalized` 相同）跨源**取并集**——集合字段真并、标量字段挑一：
> - **并集（去重）**：`game_tags`（百度真标签 ∪ TapTap detail 真标签）、截图（靠 `(category_id, source_url)` 去重后天然共存）、`platforms` / `sources` / `screenshot_urls` JSON（累积）。
> - **挑一（合并策略）**：`score = max`、`comment_count = max`（跨平台不相加，避免口径混）、`description` / `icon_url` 取更长/非空者。

1. 按 `name_normalized`（=`_normalize_game_name(game.name)`）查现有行：有则**并集合并更新**（标量按上表策略、JSON 字段累积去重），无则插入。**跨源同名游戏落同一行**。
2. **并集插入 `game_tags`（不 delete-all）**：`INSERT IGNORE`（靠 `UNIQUE(game_id, tag)` 去重）把本源标签**并进**已有集合，**不删别源标签**；`axis=null`。
   - 取舍：只加不删 → 某源日后撤标签会**滞留**（stale，低伤害：多一个匹配面、不影响正确性）。要「源撤即消」需给 `game_tags` 加 `source` 列做按源归属（更重，v1 不做）。
3. `cat = get_or_create_companion_category(db, game.name)`（复用 image_library service）。
4. 逐已下载的截图字节写入（**不主动 commit** 的底层变体）；**去重=并集**：按 `UNIQUE(category_id, source_url_hash)` 命中即跳过；两源截图 URL 不同 → **天然共存=并集**。每张限体积/类型/重定向（走通用下载器，见下）。
5. 回写 `games.stock_category_id = cat.id`、`last_verified_at = utcnow()`。
6. 出网/下载失败 best-effort：单图失败跳过、不整体失败（与 web_fallback 同风格）。

> **用量列不由入库触碰**：`use_count`/`last_used_*`（游戏级与图片级）只在**生文消费**时回写（第六、七节），入库/刷新只管语料与截图、不动用量，避免刷新把均衡状态冲掉。

> **事务与连接纪律（二轮评审）**：现有 `get_or_create_companion_category` / `store_image_bytes` **各自 `db.commit()`**——批量入库直接复用会把事务按图逐张切碎，且与「单目标失败 rollback」冲突。故：
> - 抽**不主动 commit** 的底层写入函数供 `upsert_game` / 批量入图用，**commit 边界 = 每个游戏一次**（游戏是幂等单元）；web_fallback 现有单图路径不动。
> - **HTTP 全在无 DB session 阶段做**：先 `sources.search` + `taptap.get_detail` + 下载截图字节到内存，**再**开 session 落库——避免慢网络长期占用连接池（GEO 踩过连接池耗尽）。
> - **通用截图下载器**：抽 `shared/` 下载函数，限**体积上限 / content-type 白名单 / 拒绝跨站重定向**（复用现有百度下载器的 magic-bytes + 20MB 上限逻辑），入库与 web_fallback 共用。

### 定时入库（`scheduler.py`，复用 `sync_scheduler.py` 模式）

- `run_ingest_once(session_factory) -> {"targets", "upserted", "failed"}`：**纯函数式扫一轮、可单测**——遍历「入库目标清单」（source + category），逐目标 `sources.search(...)` → 逐游戏 `upsert_game`；单目标失败隔离（rollback + 记日志，不影响其它目标），不休眠。测试直接调它并 monkeypatch `sources.*.search`，不跑真休眠、不真出网。
- `start_game_ingest(session_factory) -> bool`：按 `GEO_GAME_INGEST_SCHEDULER_ENABLED` 起 daemon 线程；循环 `wait(interval) → run_ingest_once`（先等再抓，避免一启动就打外站；停止事件可立即唤醒）。`interval = max(下限, GEO_GAME_INGEST_INTERVAL_SECONDS)`，默认建议 **6 小时**（游戏门户变化慢，一天几次足够）。
- `stop_game_ingest()`：测试 / 优雅关闭用。
- **启动位置**：`create_app()` 里与 `start_auto_sync` 并列启动（web 进程内，无需独立 worker；不依赖浏览器，与问题池同步同类）。
- **入库目标清单**（抓哪些 source×category）：v1 走**配置/env 驱动**（如 `GEO_GAME_INGEST_TARGETS` JSON 或模块常量种子清单，覆盖矩阵题材：经营/养成/国风等）。DB 驱动的可编辑目标清单留给 L2 策展。
  - **分类词表按源不同**（实测爬包 `CATEGORIES`）：百度 40 类（带 tagId 映射，有「经营/策略经营」但**无「国风」**）、TapTap 27 精选（分类名直接当 tag、**有「国风」**，非精选词语义也能命中、非法词 404）。目标清单**必须按源配各自认的词**，不能一个分类名两源通用；`养成` 两源都有、`经营` 百度有 TapTap 靠语义、`国风` 只 TapTap 有。
  - **N+1 与配额（二轮评审）**：TapTap 逐游戏 `get_detail` 是 N+1，目标配置必须带 **每目标页数上限 / 每目标游戏数上限 / 每游戏截图数上限**，并做**轮内 `(source, game_id)` 去重**（同轮别对同一游戏重复补详情 / 重复下图）。
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

### `query_games_by_tags(relevant_tags, diversity_tags=None, exclude_tags=None, min_score=None, limit=20) -> {games: [...]}`

> **二轮评审：拆 relevant / diversity，别全 OR**——早稿把「相关 + 无关」标签全塞一个 `include_tags` 做 OR，只要无关标签够热门，候选几乎必然 ≥ 阈值、于是永不 WebSearch，但候选可能**多是离题游戏**、稀释主题。故拆两组：

- `relevant_tags`：**准入过滤（必须命中至少一个）**——候选池 = 命中任一相关标签的游戏。**阈值判定只数这个池**，离题游戏不能把阈值凑够。
- `diversity_tags`（可选）：**只用于扩散/排序**，绝不放宽准入——让命中它的相关游戏排序靠前/带点多样，但从不让离题游戏入池。
- `exclude_tags`（可选）：命中任一即排除。`min_score`（可选）：分数下限。`limit`：返回上限。
- 排序（取材均衡）：`ORDER BY last_used_at ASC（近期没用过的优先；MySQL NULL 最小）, score DESC, comment_count DESC`；`diversity_tags` 命中作次级加权/打散（稳定可复现，不靠 SQL 随机）。
- 每项返回：`{game_id, name, score, tags, description, stock_category_id, icon_url, screenshot_urls, highlight_comments, related_hotspots, use_count, last_used_at}`（多回 `use_count`/`last_used_at`，让 skill 侧也能感知均衡）。

> `MCP_TOOLS_COUNT`：33 → **35**（`connect_router.py:MCP_TOOLS_COUNT` + CLAUDE.md catalog 清单同步 +2；`save_article` 只加可选参数、不新增工具）。
> **顺带修既有漂移（二轮评审）**：`test_mcp_status_count.py:17` 与 `test_mcp_tools_registration.py:22` 仍断言 `== 31`（真值早已 33、因 `backend-test` 禁用未爆红），本功能一并改到 **35**。

---

## 六、writer skill 契约（改 geo-goal 插件 `geo-article-writer`）

> **跨仓**：writer 是 **geo-goal 插件** marketplace 分发的 skill，**不在本仓**。本 spec 覆盖后端（模块/表/MCP 工具/CLI）；writer 契约在此写清，插件侧照契约改 + 重发版（另走插件仓流程）。

动笔前，把「自己 WebSearch 验游戏」替换为**库优先取材**：

1. `list_game_tags()` 拿可选标签。
2. skill 逻辑按矩阵选标签，**分两组**：`relevant_tags`＝**1-2 个相关标签**（矩阵题材，如餐厅养成记→经营/养成，准入）+ `diversity_tags`＝**几个多样性标签**（只扩散/打散，不放宽准入）。选标签策略是 skill 层规则、可迭代。
3. `query_games_by_tags(relevant_tags, diversity_tags?, exclude_tags?, min_score?, limit)` 拿候选真实游戏 + 语料。
4. **候选数 ≥ 阈值**（默认建议 `>=4`，skill 常量可调；**阈值只数 `relevant_tags` 命中池**）→ 从候选里挑游戏、用 `description/score/tags/(语料)` 写正文；**不 websearch**。**记下选中的 game_id**。
5. **候选数 < 阈值** → 回退现有 WebSearch 验证路径（安全网，不移除）。
6. `report_event` 打点，`event_type` 区分 `games_from_library` / `games_fallback_websearch`，payload 记候选数、选中标签、命中游戏。
7. `save_article(..., selected_games=[{game_id, name}, ...])`——**把选中游戏透传给后端**，后端在保存同事务里**原子 bump `games` 用量**（第七/十节）；正文结构判定与 `game_positions` 返回沿用现有约定（每款各占 `##` 小标题 → 收 game_positions；散文 → None）。库里没命中、回退 websearch 的游戏无 game_id，`selected_games` 相应留空。

---

## 七、配图链路（栏目命中机制不变；新增软 LRU 选图 + 用量回写）

- writer 返回 `game_positions` → orchestrator 调 `ai_illustrate_article(game_positions=..., web_fallback=True)`（现有主循环配图决策块不变）。
- 现有 `_web_fallback_decide` → `get_or_create_companion_category(游戏名)` **命中已预灌真实截图的陪衬栏目** → `pick_image_id` 取真截图，**不触 Baidu**。
- **选图改软 LRU（本次新增）**：「按名找 category」不变，但桶内选哪张图改为 `ORDER BY last_used_at ASC（MySQL NULL 最小 → 没用过的排最前）, use_count ASC` 取最新鲜的一张；桶里全是旧图就退用最久没碰的 → 文章间短期去重、永不卡死。
- **落图回写用量（本次新增）**：图插进文章后 bump 该 `stock_image` 的 `use_count`/`last_used_at`/`last_used_article_id`（文章 id 从配图上下文取）。
- 库里没有的游戏仍走 web_fallback 联网兜底（行为与现状一致）。
- **命中键零改动**：真实截图入库用的就是配图同一套栏目命中键（精确游戏名）与 `store_image_bytes`，天然对齐；只有**选图排序 + 落图回写**两处是本次新增的小改，栏目/桶机制不动。
- **游戏级用量走另一条路（不在配图里）**：配图这里只 bump **图片级**用量；**游戏级**用量由 `save_article(selected_games=...)` 在文章保存同事务里 bump（二轮评审）——因为候选 ≠ 选中（不能查询时 bump）、散文/未进配图的文章也没有 `game_positions`（不能从配图反推），只有 writer 自己知道真正选了哪些游戏，必须显式透传。

---

## 八、组件边界与依赖

| 单元 | 职责 | 依赖 | 可独立测试点 |
|---|---|---|---|
| `game_library/sources/*` + `registry`/`types` | 抓 TapTap/百度 → 统一 `Game` | 出网（http_direct） | 解析真实响应样例 → Game 字段 |
| `game_library/service.upsert_game` | 幂等 upsert + 标签并集 + 截图入 MinIO（不主动 commit）+ 回写栏目 | image_library（no-commit 变体）、MinIO、通用下载器 | upsert 幂等/并集、截图 hash 去重、批量事务回滚、栏目回写 |
| `game_library/service.query_*` / `list_game_tags` | 聚合检索（relevant 准入 + 取材均衡排序） | DB | relevant 准入、diversity 只排序、min_score、`last_used_at` 均衡排序 |
| image_library 选图/回写（配图侧小改） | 软 LRU 选图 + 落图 bump 图片级用量 | `stock_images` 用量列 | 桶内 LRU 取图、用量回写、不卡死 |
| `save_article` + 后端 `save-from-mcp` | 保存文章 + 原子 bump 游戏级用量 | `selected_games`、`games` 用量列 | selected_games 落 `use_count`/`last_used_*`、未知 game_id 跳过 |
| `game_library/router` | MCP-token 只读端点（+可选入库触发） | require_mcp_token | 鉴权、返回结构 |
| `server/scripts/ingest_games` | CLI 编排 | service、SessionLocal | 端到端灌一批（mysql mark） |
| MCP 工具 `list_game_tags`/`query_games_by_tags` | LLM-facing schema | router 端点 | 工具 schema/透传 |
| writer skill 契约 | 库优先取材 + 兜底 | MCP 工具 | 插件侧，人工/评审验证 |

---

## 九、测试（server/tests，`@pytest.mark.mysql`）

- `run_ingest_once` 扫一轮：monkeypatch `sources.*.search` 返回假 Game，断言 upsert 计数、单目标失败隔离（一个目标抛错不影响其它目标）。
- `upsert_game` 幂等 + 跨源并集合并：同 `name_normalized` 重跑只更新不增重；**两个不同 source 的同名游戏合并入一行**、`score`/`comment_count` 取 `max`、`sources`/`screenshot_urls` 累积；**`game_tags` 取两源并集**（先 taptap 再 baidu，taptap 标签**不被删**、并集去重）。
- TapTap 补详情：断言 upsert 前对 taptap 游戏调 `get_detail` 拿到非空 `screenshot_urls`/真标签（可 mock `get_detail` 验证被调用、且 by-tag 占位标签被真标签取代）。
- 截图入库去重：同 `source_url` 重跑不重复灌图（`UNIQUE(category_id, source_url_hash)` 命中即跳过）；`stock_category_id` 正确回写。
- 批量入库事务（**commit 边界＝每游戏一次**）：单个游戏中途写失败 → **该游戏的部分写入（它的图）回滚**、跳过该游戏；**同目标已 commit 的前序游戏保留、别的目标不受影响**（game 级 + target 级双隔离）。
- `query_games_by_tags`：**`relevant_tags` 准入**（只命中 diversity 的离题游戏**不入池、不撑阈值**）、`diversity_tags` 只影响排序、`exclude_tags`/`min_score`/`is_active` 过滤，排序 `last_used_at ASC → score DESC`；返回含 `use_count`/`last_used_at`。
- `list_game_tags`：计数正确、按 game_count 降序。
- **用量回写**：配图落图后 `stock_image` 三列正确 bump；`save_article(selected_games=...)` 保存成功后 `games` 三列在**同事务**原子 bump、未知 game_id 跳过（散文/无配图文章也能记账）。
- **软 LRU 选图**：同桶多图，重复配图时优先取 `last_used_at` 最旧/`use_count` 最小的；桶内全用过时仍能出图（退用最久没碰的、不卡死）。
- MCP 工具数：`MCP_TOOLS_COUNT == 35`，`test_mcp_status_count` / `test_mcp_tools_registration` 断言同步改 35（修既有 31 漂移）。
- MCP 端点鉴权：无 token 401；有 token 返回结构正确。
- 配图命中：预灌某游戏截图后，走 `game_positions` 配图命中真截图、不触网（可 mock 千帆断言未调用）。
- MinIO：测试环境按现有 image_library 测试约定（若无 MinIO 则相应用例跳过/mock store）。

---

## 十、迁移与文档

- **Alembic**：新增迁移 ① 建 `games`（`name_normalized` **UNIQUE**）/ `game_tags`（`UNIQUE(game_id, tag)`）；② `ALTER stock_images` 加 **5 列**（`source_url` **不索引**、`source_url_hash` CHAR(64)、`use_count` not null default 0、`last_used_at`、`last_used_article_id`）+ **`UNIQUE(category_id, source_url_hash)`**（跟随当前迁移头，不写死版本号）。FK：`games.stock_category_id`→`stock_categories` SET NULL、`games.last_used_article_id` / `stock_images.last_used_article_id`→`articles` SET NULL、`game_tags.game_id`→`games` CASCADE。
- **image_library 改动**：`store_image_bytes` 写 `source_url`+`source_url_hash`；抽**不主动 commit** 的批量写入变体（web_fallback 单图路径不变）；`pick_image_id` 选图排序改软 LRU + 落图回写图片级用量（第七节）。
- **通用截图下载器**：抽 `shared/` 下载函数（体积上限 / content-type 白名单 / 拒跨站重定向，复用现有百度下载器 magic-bytes+20MB 逻辑），入库与 web_fallback 共用。
- **`save_article` + 后端 `save-from-mcp`**：新增可选 `selected_games`（`SaveArticleFromMcpPayload` 加字段），保存成功在**同事务**原子 bump `games` 用量（未知 game_id 跳过）。MCP 工具签名 +1 可选参数，**不新增工具**（工具数仍 +2=35）。
- **修既有测试漂移**：`test_mcp_status_count.py` / `test_mcp_tools_registration.py` 的 `== 31` 改 `== 35`。
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

### 已知限制（L1 明确接受，二轮评审登记）

- **`name_normalized` 不是实体消歧**：现归一化只去书名号/引号/`游戏N、`前缀，**同名不同游戏会误合并、改名或版本后缀会拆行**。L1 接受此近似；真消歧（别名表/人工合并）留 L2。
- **一游戏一 MinIO bucket 的规模上限**：几百游戏可沿用；**数千~数万**时 bucket 数会成运维负担，届时改共享 bucket + key 前缀分区（L2）。
- **第三方截图＝公开发布（产品已定 2026-07-20）**：游戏截图与现有图片库**同用途 / 同风险姿态**——插入文章、随文公开发布。运营接受与现图片库一致的版权姿态；与自有 stock 的唯一区别是来源为第三方官方宣传图，用法无差、不作额外合规闸。

---

## 十二、开放项 / 待实现计划细化

- 入库目标清单（source×category）默认放哪：`GEO_GAME_INGEST_TARGETS` env（JSON） vs 模块内种子常量 —— writing-plans 阶段定，倾向「env 覆盖、缺省回落常量」。
- 定时任务默认 interval（建议 6h）与下限值 —— 落地可调。
- writer 阈值默认值（建议 `>=4`）与「多样性标签」条数（建议 1-2 相关 + 2-3 无关）——skill 常量，落地可调。
- 截图入库的尺寸/数量上限（每游戏最多存几张、单图压缩策略）——参照 image_library 现有约定。
- 可选 HTTP 入库触发端点是否 v1 就做（有定时任务后优先级下降，默认后置）。
- 用量均衡权重公式（`last_used_at` 时间窗 vs `use_count` 谁主谁次、是否加「近 N 天」软阈值）——落地可调；`ORDER BY last_used_at ASC, use_count ASC` 是默认起点。
- 取材均衡由谁执行：`query_games_by_tags` SQL 侧已按 `last_used_at` 排序打底，skill 侧是否再依 `use_count`/`last_used_at` 二次挑——skill 常量，落地定。
- **版权 / 站点条款（产品已拍板 2026-07-20）**：截图定为**公开可随文发布**，与现有图片库同用途 / 同风险姿态——**不再作上线前的额外合规闸**。（来源为第三方官方宣传图，姿态与自有 stock 略异，运营已接受。）
