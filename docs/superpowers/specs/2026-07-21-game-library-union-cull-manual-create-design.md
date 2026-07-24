# 游戏库抓取：并集刷新 + 无证据自动软删 + 手动新建 —— 设计稿

- 日期：2026-07-21
- 分支：`feat/game-library-corpus`
- 状态：设计已与用户逐段确认，待写实现计划（writing-plans）
- 关联现状：`docs/superpowers/specs/2026-07-21-game-library-ingest-management-design.md`（托管抓取基线）

## 背景与动机

游戏库的「陪衬抓取」是**按游戏名去各源精确重搜、刷新**（不是发现型爬虫）。当前实现有三个问题，本设计逐一处理：

1. **源没做并集**：`ingest_service._search_by_name` 按 `source_order`（默认 `taptap,baidu`）逐个源试、**命中即停**。baidu 排第二，taptap 命中就不问 baidu；且各源命中数据没有合并。（实测：baidu 源裸 HTTP 直连、**不需要 key**，`GEO_BAIDU_API_KEY` 是图库 AI 配图那条链用的、与此无关。）
2. **搜不到的空壳游戏一直沉淀**：从图库栏目名导入的游戏（`importer.import_image_categories_as_games`）只有名字、无任何源级信息；名字对不上源时永远 `not_found`，越积越多，稀释库质量。
3. **无手动建游戏入口**：后端没有 `POST /games`，新增游戏只能靠「从图片库导入」或 CLI；`refresh` 只更新已存在的游戏。

**用户确认的关键判断**：
- 「有信息（简介/评分等）才算存在证据 → 豁免；**只有截图不算**，因为桶里的图可能错配。」
- 「当前是基于清单按名核对，清单核完后会换更强的匹配方法，**现在先删**。」——即接受现阶段"名匹配失败即无证据、连续 N 轮软删"，可逆，后续再放宽。

## 范围

**做**：
1. 并集刷新：每次刷新查全部源、合并所有命中。
2. 无证据自动软删：连续 N 轮全源 miss 且无证据的游戏 → `is_active=False`。
3. 手动新建游戏接口 `POST /games` + 前端「新建游戏」按钮。

**不做（YAGNI / 明确留后）**：
- 模糊/编辑距离匹配（会误配，如"斗地主"↔"欢乐斗地主"灌错数据/图）——本轮只做**归一化后相等**。更强匹配是"清单核完后换的别的方法"，另立。
- 栏目→游戏自动同步（第 3 项用户只选了手动新建，导入按钮保留）。
- 硬删 / 宽限期物理删（本轮只软删，可逆）。
- 别名/白名单保护表。

## 组件与数据流

### 1. 并集刷新（第 1 项）

**改 `ingest_service`**：把 `_search_by_name(source_order, name)`（命中即停、返回单个 hit 或 None）替换为：

```
_collect_from_all_sources(source_order, name) -> CollectResult
  # 按 source_order 逐源查，不命中即停；每源产出明确状态
  CollectResult = {
    "hits": list[types.Game],          # 所有命中源的结果（taptap 命中补 get_detail）
    "per_source": {src: "hit"|"miss"|"error"},  # 每源结果，喂软删计数
  }
```

- 逐源调 `source.search_by_name(name, matcher=...)`（见"匹配"）。抛异常 → `per_source[src]="error"`、不进 hits；返回 None → `"miss"`；命中 → `"hit"` 且 taptap 无截图时补 `get_detail`。
- `error` 与 `miss` **必须区分**：error（网络/异常）不构成"搜不到"的证据。

**改 `refresh_one_game`**（`session_factory, game_id, *, source_order, max_shots`）：
- 短读 session 取 `name / stock_category_id / 现有 info 字段`。
- session 外：`_collect_from_all_sources` → 对每个 hit `image_download.download_image` 下好截图。
- 短写 session：对每个 hit 调 `service.upsert_game(db, hit, pre_downloaded=..., category_id=...)`。`upsert_game` **本就做跨源合并**（评分/评论取 max、标签并集、截图去重、sources 追加），多次 upsert 到同一 game 天然并集。
- 之后在**同一写 session** 内做 streak 更新 + 软删判定（见第 2 项）。
- 返回值升级为结构化 outcome：`{"outcome": "refreshed"|"not_found"|"error"|"culled", "per_source": {...}}`。
- per-game 隔离不变：任何异常吞掉记日志，不拖垮整批。

**匹配质量**：现在两源内部按标题**完全相等**过滤（taptap `app["title"] == name`、baidu `item["gameName"] == name`）。改为**归一化后相等**，归一化**复用现有 `_normalize_game_name`**（`articles.formatting.document`，即 `name_normalized` 去重用的同一套），保证"匹配口径 == 去重口径"，不自造新规则；若其归一化对某类差异（如全/半角）覆盖不足，在 plan 阶段**最小增补该函数**、两处同时受益。
- 为**不破坏 `sources/*` 的纯 stdlib 无 app 依赖**，归一化不写进源模块：`search_by_name(name, *, matcher=lambda a, b: a == b)` 增加可注入的 `matcher` 参数，源内部遍历候选、用 `matcher(候选标题, name)` 判定；`ingest_service` 调用时注入基于 `_normalize_game_name` 的归一化 matcher。默认参数保持完全相等（不影响 registry 等其它调用点）。
- **只做归一化相等，不做子串/模糊**：能救"格式差异"型 miss，不会误配。故"斗地主↔欢乐斗地主"仍 miss——用户已确认此类**按规则软删**。

### 2. 无证据自动软删（第 2 项）

**`games` 加列**：`not_found_streak INT NOT NULL DEFAULT 0`。

**`refresh_one_game` 写 session 内的判定（每游戏一次）**：
- 所有源都 `miss`（`per_source` 非空且无 hit 无 error）→ `not_found_streak += 1`。
- 任一源 `hit` → `not_found_streak = 0`（并已被 upsert 刷新）。
- 任一源 `error` → `not_found_streak` **不动**（错误不算"搜不到"证据）。
- 判软删：`cull_enabled` 且 `not_found_streak >= cull_after_misses` 且 **无证据** 且 **不豁免** → `is_active=False`，outcome=`culled`。

**"无证据"定义**（= 从没被任何源成功匹配过）：`score is None` **且** `description is None`（空串视为无）**且** `comment_count is None` **且** `sources` 为空列表。**截图不计入证据**（桶里图可能错配）。

**豁免（永不自动软删，任一命中即豁免）**：
- 有任何源级信息（上面"无证据"取反）。
- `use_count > 0`（被文章采用过）。
- `manually_curated = True`（人工新建或编辑过）。
- 主推 main（`stock_category.kind == 'main'`）——天然不进 `select_due_games`，永远不累积 streak；为稳妥仍在判定里显式排除。

**配置**（`game_ingest_config` 加列）：
- `cull_after_misses INT NOT NULL DEFAULT 3`：连续几轮全源 miss 才软删。
- `cull_enabled TINYINT(1) NOT NULL DEFAULT 1`：自动软删总开关，可一键关。
- 两者纳入 `ingest_service._WRITABLE` + `GameIngestConfigRead/Patch` schema + `ingest_config_to_dict`，前端「陪衬抓取」配置弹窗可改（前端改动可选，本轮至少后端可配）。

**批次 summary**：`_run_configured_batch` 的 summary 增加 `culled` 计数（`{"batch","refreshed","not_found","error","culled"}`）。

### 3. 手动新建游戏（第 3 项）

**接口** `POST /api/game-library/games`（user JWT，挂在 `router_web.game_library_web_router`）：
- body：`name: str`（必填，1–200）+ 可选 `score: float | None`、`description: str | None`、`tags: list[str]`、`kind: "main"|"companion"`（默认 `companion`）。
- 行为：`_normalize_game_name(name)` → 若 `name_normalized` 已存在 → **409 ConflictError**（不静默覆盖）。否则：
  - 建/挂对应 `kind` 的素材桶：companion 复用 `get_or_create_companion_category`；main 同理建 main 桶（或建后置 kind='main'）。
  - 建 `Game`：填 name/score/description、tags 落 `GameTag`、`stock_category_id=桶.id`、**`manually_curated=True`**、`sources=[]`、`first_seen_at=utcnow()`。
- 返回 `GameDetail`（复用 `service.get_game`）。
- 新增 `service.create_game(db, payload) -> Game`，`schemas.GameCreateRequest`。

**人工背书标记** `games.manually_curated`：手动新建置 True；`service.update_game`（`PATCH /games/{id}`）也置 True。驱动第 2 项豁免。

**前端**：`GameLibraryHeader` 加「新建游戏」按钮 + 弹窗（name + 可选字段 + kind 选择），复用现有弹窗样式；`api/game-library.ts` 加 `createGame`。**「从图片库导入」按钮保留**。

### 4. 数据模型 + 迁移

新迁移 `0067_game_cull_and_manual`（down_revision = `0066_game_ingest_config`）：
- `games` 加 `not_found_streak INT NOT NULL DEFAULT 0`、`manually_curated TINYINT(1) NOT NULL DEFAULT 0`。
- `game_ingest_config` 加 `cull_after_misses INT NOT NULL DEFAULT 3`、`cull_enabled TINYINT(1) NOT NULL DEFAULT 1`。
- 模型 `models.Game` / `models.GameIngestConfig` 同步加列（`server_default` 与迁移一致，存量行安全回填）。

## 错误处理

- 源异常 → `per_source[src]="error"`，不进 hits、**不累积 streak**（避免网络抖动误删）；`_collect_from_all_sources` 逐源 try/except，一个源挂不影响其它源。
- `refresh_one_game` 整体仍 per-game try/except 兜底：写库异常 → rollback + 仍推进 `last_verified_at`（避免饿死轮转，沿用现有 I2 修复逻辑），outcome=`error`，**此时不软删**。
- 软删只在"干净的全源 miss + 无证据 + 不豁免"路径触发，异常路径一律不删。
- `POST /games` 重名 → 409（`ConflictError`，全局 handler 转 409）；非法入参 → schema 422。

## 测试（全程 mock 源，不打真网络）

`server/tests/test_game_library_*.py` 扩充：
1. `_collect_from_all_sources`：taptap hit / baidu hit / 双命中合并 / 双 miss / 一源 error 一源 miss —— hits 与 per_source 正确。
2. 归一化匹配：带空格、大小写、全/半角能命中；子串（斗地主↔欢乐斗地主）**不误配**。
3. streak：全源 miss→+1；任一 hit→归 0；含 error→不动。
4. 软删判定：`streak>=N` 且无证据→`is_active=False`；有 score/description/sources、`use_count>0`、`manually_curated`、main → **永不删**；`cull_enabled=0` → 不删。
5. `POST /games`：正常建（含建桶、`manually_curated=True`、tags 落库）；重名→409；`PATCH /games/{id}` 置 `manually_curated=True`。
6. 迁移：`0067` upgrade/downgrade（沿用 `test_fts_and_migrations` 风格断言列存在/回退）。

## 部署 / 迁移注意

- 本地 geo_dev 当前 head=`0066`，本设计落地后需 `alembic upgrade head` 至 `0067`。
- 存量 663 个无信息 companion 空壳：上线后随抓取轮转，连续 `cull_after_misses` 轮全源 miss 才逐步软删；`cull_enabled` 可先关观察、确认匹配率后再开。
- 软删可逆（`is_active=False`），误删可查列表 + 置回 True 恢复。

## 未来（明确留后，不在本轮）

- "清单核完后换的别的方法"：更强匹配（别名库 / 模糊 + 人工确认 / 第三方 ID 对齐）。
- 栏目→游戏自动同步、导入按钮退化。
- 软删满宽限期后自动物理删。
