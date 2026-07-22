# 游戏库远程补数：本地抓取 → 传输 → 生产入库

**日期**：2026-07-22
**状态**：设计已确认，待写实现计划
**topic**：game-library-remote-backfill

## 背景与问题

生产服务器的 TapTap 抓取源持续返回 `HTTP 405 Not Allowed`（`taptap.py:_bootstrap_xsrf` 换 XSRF token 的 GET 请求被挡）。根因是**生产出口 IP（阿里云）被 TapTap 风控**，不是接口变更——本地机器实测同一请求 `LOCAL XSRF OK`，能正常换票并抓取。

因此把抓取的网络出口从生产挪到本地即可绕开 405。生产游戏库现有 1584 个游戏，绝大多数 `last_verified_at` 为空（从未成功抓过）。

## 目标

- **一次性/偶发补数**：本地跑抓取，把结果回填到生产库（games/game_tags 元数据 + stock_images 截图）。
- **先小样验证**：先跑 ~20 个，验证「本地抓取 → 传输 → 生产入库」整条链路通，再扩全量。
- 复用现有 `game_library` 抓取与入库函数，不新造轮子。

## 非目标（YAGNI）

- 不做 MCP 工具（MCP 是给 Claude Code loop 调的单步原子能力；这是长时批处理，CLI 更合身）。
- 不做软删/cull（补数脚本只加不删，见「错误处理与安全」）。
- 不做 geo_dev→生产整库同步（对补一批过重）。
- 不做常态化定时调度（本次是手动补数）。

## 关键事实（已核实）

- 本地 `e:\geo` 有完整 `game_library` 模块 + `sources/{taptap,baidu}.py`，抓取代码可直接复用。
- 本地 `.env` 指向 LAN 开发服 `172.25.20.57`（geo_dev / MinIO），**不是生产**。
- 生产 MySQL/MinIO 端口**未 publish 到宿主**（只在 compose 内网 `mysql:3306` / `minio:9000` 可达）→ 隧道直连方案有容器 IP 漂移 + MinIO URL 一致性坑，故不采用。
- `refresh_one_game`（`ingest_service.py:226`）的拆分点干净：**抓取+下载字节（254-275，纯联网无 DB）** 与 **`upsert_game` 入库（283-293，纯 DB/MinIO）** 天然两段。
- 抓取产物 `Game`（`types.py`）是纯 dataclass（基本类型 + list + dict）→ `dataclasses.asdict()` 可直接 JSON 序列化，生产端 `Game(**d)` 还原。

## 方案 A：抓取端本地 + 入库端生产

入库在生产容器内跑，DB（`geo_collab`）与 MinIO（`minio:9000`）都是生产原生配置，**不存在把 `localhost:9000` 图片 URL 写进生产库的坑**——这是本方案优于 SSH 隧道直连的核心原因。

### 三个组件（职责隔离）

| 组件 | 跑在哪 | 职责 | 复用 |
|---|---|---|---|
| ① 清单导出 | 生产容器 | 查生产 games → `todo.json`（game_id / name / category_id / icon_local） | 一条 inline python，不建脚本 |
| ② 抓取端 `server/scripts/remote_backfill_scrape.py` | **本地** | 读 `todo.json` → 每游戏 `_collect_from_all_sources` 抓 + 下载截图/封面**字节** → 出包 | `_collect_from_all_sources` + `shared/image_download` |
| ③ 入库端 `server/scripts/remote_backfill_import.py` | **生产容器** | 读包 → `Game(**d)` 还原 → `upsert_game(pre_downloaded=字节, category_id, game_row)` | `game_library.service.upsert_game` |

### 数据流

```
生产 games ──①导出──▶ todo.json ──scp──▶ 本地
本地 ──②抓取+下字节──▶ 包/(scraped.json + images/) ──scp──▶ 生产
生产容器 ──③upsert──▶ geo_collab(MySQL) + minio:9000   ← 原生配置，无 URL 坑
```

### 包结构

```
backfill_pkg/
├── todo.json           # [{game_id, name, category_id, icon_local}]
├── scraped.json        # [{game_id, category_id, icon: {file,mime,url}|null,
│                       #   hits: [{game: <asdict(Game)>, shots: [{file,mime,url}]}],
│                       #   per_source: {taptap: hit|miss|error, baidu: ...}}]
└── images/<game_id>/   # 截图/封面字节文件（<idx>.<ext>）
```

`Game` 序列化字段（`types.py`）：`source, game_id, name, score, tags, platforms, comment_count, icon_url, screenshot_urls, android_package, description, raw`。

### 抓取端（②）逻辑

镜像 `refresh_one_game` 的上半段，但输入来自 `todo.json`（不依赖本地 DB）：

1. 读 `todo.json`。
2. 每游戏：`_collect_from_all_sources("taptap,baidu", name)` → hits + per_source。
3. 每 hit：下载 `screenshot_urls[:max_shots]` 字节（`image_download.download_image`，失败跳过单图）；封面仅当 `icon_local=False`（生产尚未本地化）时才下载最优封面字节，`icon_local=True` 跳过——与 `refresh_one_game` 的 `icon_local` 判断一致。`max_shots` 沿用生产 config 值（当前 3）。
4. 字节落 `images/<game_id>/`，元数据 `asdict(hit)` + 文件引用写 `scraped.json`。
5. per-game try/except 隔离：单个游戏抓失败不中断整批。

### 入库端（③）逻辑

镜像 `refresh_one_game` 的下半段，在生产容器内跑：

1. 读 `scraped.json`。
2. 每游戏：`game_row = db.get(Game, game_id)`（锚定生产行，不按名另建）；对每个 hit：`Game(**d)` 还原 + 从 `images/` 读字节还原 `pre_downloaded=[(url,bytes,mime)]` / `pre_icon` → `upsert_game(db, hit, max_screenshots, pre_downloaded=shots, category_id=category_id, game_row=g, pre_downloaded_icon=pre_icon)`。
3. **每游戏单独 commit**。
4. `--dry-run` 开关：只打印将要 upsert 的内容，不写库。

## 错误处理与安全

- **per-game 隔离**：抓取端/入库端单个游戏失败 → 记录跳过，不中断整批。
- **每游戏单独 commit**：入库端一个坏行不回滚整批。
- **幂等**：`upsert_game` 跨源并集合并（tags 并集 / score 取 max / 截图按 `source_url_hash` 去重）；包可重复导入、重跑安全。
- **⚠️ 不走软删（cull）**：入库端**只 upsert 命中的游戏**，绝不因「本地某游戏没抓到」把生产游戏置 `is_active=False`。这是与 `refresh_one_game` 的关键差异——补数脚本不能带删除语义，否则本地漏抓会误伤生产。
- **单图下载失败**：`download_image` 返回 None 即跳过该图，不影响其余。

## 发版隔离（已核实）

`deploy.sh` 目标分 `all | server | web`。**只发前端（`web`）时 `SERVER_VERSION` 保持不变**，`app`/`worker`（`geo-collab-server:${SERVER_VERSION}`）镜像引用不变 → `docker compose up -d` 不 recreate app/worker，只重建 `nginx`。

- ① 本地抓取：与生产完全解耦，任何发版零影响。
- ③ 生产入库：纯前端发版不重启 app 容器 → 不受影响。
- 唯一风险：入库时恰好发**后端/`all`** → app 被 recreate 杀掉 import 进程；但幂等 + 每游戏 commit → 数据不损坏、重跑补齐；`mysql`/`minio` 不随发版重启。
- 建议：跑「③ 入库」那几分钟避开后端发版即可；撞上也只是重跑一次。

## 20 个小样验证步骤

1. ① 导出 `todo.json`：`last_verified_at` 为空的前 ~17 个 **+ 故意混 2-3 个 TapTap 铁定有的知名游戏（原神 / 王者荣耀）**，确保验证到「抓到并成功入库」的正路径。
2. ② 本地抓 → 检查 `scraped.json`：几个抓到、每个几张图、per_source 分布。
3. scp 包 → 生产。
4. ③ `--dry-run` 过一遍 → 再正式入库。
5. 验证：这 20 个 `last_verified_at` 更新？截图进 MinIO？前端游戏库能看到图？
6. 通过 → 把 ① 的过滤/`LIMIT` 改成全量范围，同一套脚本跑全量。

## 决策记录

- 小样选取：方案 (b)——空白前 ~17 + 混 2-3 知名游戏。
- 抓取源顺序：沿用生产 `taptap,baidu`。
- 不走软删：确认，入库端只加不删。
- 形态：CLI 脚本（不做 MCP 工具）。

## 脚本落点

- `server/scripts/remote_backfill_scrape.py`（本地跑）
- `server/scripts/remote_backfill_import.py`（生产容器跑）
- 清单导出：一条 `docker exec geo-ci-app-1 python -c` inline 只读查询，输出 `todo.json`，不单独建脚本。
