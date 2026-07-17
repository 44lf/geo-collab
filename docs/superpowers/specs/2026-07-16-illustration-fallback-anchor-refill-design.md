# 配图随机兜底改为「按锚点回填」设计稿

- 日期：2026-07-16
- 状态：设计已确认，待实现（TDD）
- 范围：`/goal` 生文配图 + pipeline「AI配图」节点（二者共用 `illustrate_one`）

## 1. 背景与根因

近期生文频繁出现「图片插入位置错误 / 图片本身错误 / 图片数量错误」。以生产文章 2017 为样本，三方证据（`report_events` 写作事件 + `content_json` + `app.log`）闭合定位：

1. writer（skill）吐的 `game_positions` 清单**正确**（10 款、无 index 提示、与正文 10 个标题一一对应）。
2. 确定性锚定（`build_image_positions_from_game_list`）**正确**，10 款都命中各自标题。
3. 其中「创造与魔法」「爱莉的魔法学院」本地图库无图 → 触发联网兜底，但生产 `WARNING baidu 联网兜底跳过：未配置 GEO_BAIDU_API_KEY` → 这 2 款补不到图。日志 `ai_format applied 0 headings + 8 images`（该 10 只来 8）。
4. **随机兜底填图**触发：`apply_image_fallback` 见 anchored=10、实插=8、gap=2，调 `fill_random_images` → `_spread_positions` **在全文均匀撒 2 张不相关图**，落到 `[1]`（文首）和 `[69]`（游戏五正文中间）。

结论：**插图工具函数无辜**（`insert_images_at_positions` 从后往前插、无索引漂移，忠实执行）；错位来自「漏配位置信息在交接给兜底时被丢掉、兜底只拿到一个计数 `anchored`，于是自己用 `_spread_positions` 全文撒点」。真凶是**配置缺失（GEO_BAIDU_API_KEY）+ 兜底设计缺陷（随机撒点）**的叠加。

> 配置缺失是另一条线（补 `GEO_BAIDU_API_KEY` 回 geo-ci compose，属运维止血），本设计**只治兜底设计缺陷**：即便将来某款游戏真的补不到图，兜底也应「补在漏配的那个锚点、或干脆留空」，绝不再全文撒无关图。

## 2. 目标行为

主配图对 N 个锚点插图，其中 M 个成功、K=N−M 个漏配（精准 + 联网都取不到图）。兜底应当：

- 只在**漏配的那 K 个锚点**各补一张（从候选栏目池随机取，best-effort）；
- 补的图落在**该漏配锚点**处（该游戏标题后），不抢已配上的位置、不在全文另撒；
- 栏目池也取不到时该锚点**留空**（宁少勿错），绝不撒无关图；
- `anchored=0`（一个锚点都没有）→ 不补（保持 #1182 不变量）。

例：5 个锚点 1/2/3/4/5，主配图给 1/2/3/5 配上、4 漏了 → 兜底只在 **4** 补一张，1/2/3/5 一律不动。

## 3. 方案（A：把随机替补并进主插图、按锚点就地回填）

### 3.1 核心机制

在 `_maybe_insert_images`（server/app/modules/articles/ai_format.py，插图的唯一权威）的**同一趟位置扫描**里加一档「随机替补」：

当某锚点位置精准图（本地栏目）+ 联网补图都取不到 `image_id` 时，**若 `random_fill_missed=True`**，做最后一次尝试：`pick_image_id(ImageQuery(category_ids=list(valid_category_ids), excluded_ids=used_ids))`。

- 取到 → 当作 matched，把该 ref 与**同一个原始索引 `idx`** 记入 `matched_refs` / `matched_positions`，并计一笔 `random_filled`；`used_ids` 追加去重。
- 取不到 → 该锚点保持漏配（记 `missed`）。

扫描结束后，精准 + 联网 + 随机替补图**一次** `insert_images_at_positions(content, matched_refs, matched_positions)` 落盘。因为所有位置都是**原始索引**、一次性插入（内部从后往前），**索引位移天然一致、无需任何事后位移校正**——这是选 A 的关键理由。

优先级固定：精准（本地栏目）> 联网补图 > 随机替补 > 留空。

### 3.2 硬上限与不变量

- `max_images` 硬上限沿用现有逻辑：扫描顶部 `if max_images is not None and len(matched_refs) >= max_images: break`——随机替补计入 `matched_refs`，故总图数 ≤ `max_images` 自动成立。
- #1182 不变量天然保持：随机替补**只在已锚定（requested）但没配上的位置**发生；一个锚点都没有时没有任何 requested 位置可替补 → 不补。绝不出现「作者想配 N 张 → 灌 N 张随机图」。

### 3.3 随机替补图不附 url 段落（决策 1）

精准/联网图经 `insert_images_at_positions` 会在图后附来源 url 段落（`ref.official_url`）。随机替补是与该游戏无关的图，其来源 url 无意义。**随机替补只插 image 节点、不附 url 段落**。实现上：给 `insert_images_at_positions` 的替补 ref 传一个不带 `official_url` 的路径，或在替补分支单独 `build_image_node` 后自行插入（细节留给实现，语义=不附 url）。

## 4. 门控与透传

新增布尔参数 `random_fill_missed: bool = False`，自 `illustrate_one` 向下逐层转发：

```
illustrate_one (random_fill_missed=True)
  ├─ run_ai_format_from_game_list(..., random_fill_missed)
  │     └─ _web_fallback_collect_and_write_back(..., random_fill_missed)
  │           └─ _maybe_insert_images(..., random_fill_missed)
  └─ run_ai_format(..., random_fill_missed)
        ├─ _run_ai_format_web_fallback(..., random_fill_missed)
        │     └─ _web_fallback_collect_and_write_back(..., random_fill_missed)
        │           └─ _maybe_insert_images(..., random_fill_missed)
        └─ _ai_format_write_back(..., random_fill_missed)
              └─ _maybe_insert_images(..., random_fill_missed)
```

各中间层只是转发一个 bool，不含逻辑。

**门控效果（=范围 goal + pipeline）**：

| 调用方 | 是否传 True | 结果 |
|---|---|---|
| `illustrate_one`（/goal MCP `ai_illustrate_article` + pipeline「AI配图」节点，两者共用它） | 是 | 漏配锚点随机替补 |
| `scheme_executor`（方案运行，直接调 `run_ai_format_from_game_list`） | 否（默认 False） | 行为不变（本就无随机填） |
| 手动 AI 排版 / 其它 `run_ai_format` 调用方 | 否（默认 False） | 行为不变 |

随机池复用 `_maybe_insert_images` 内已有的 `valid_category_ids`（主推 + 陪衬栏目 id），与旧 `apply_image_fallback` 的 `category_ids` 同池。

## 5. 退役随机撒点 & 诊断

### 5.1 删除

- `illustrate_one`（ai_illustrate_svc.py:211-227）里对 `apply_image_fallback` 的调用块。
- `image_library/fallback.py` 的 `_spread_positions` / `fill_random_images` / `apply_image_fallback`。
- `count_body_images` / `collect_used_stock_image_ids` 实测只在 `fallback.py` 内自用，随之清理；若清理后 `fallback.py` 空壳则整文件删除、去掉 `ai_illustrate_svc.py` 顶部 import。

### 5.2 诊断（决策 2）

`out_diagnostics` 语义调整：

- 新增 `random_filled: int`——随机替补落图的张数。
- `missed` 收窄为「连随机替补都没补到的锚点数」（栏目池也空）。
- `inserted` 仍为该锚点配上图的总数（精准 + 联网 + 随机替补）。

好处：orchestrator 的 `collect_illustration_warnings` 的 `partial_images` 警告更准（`missed` = 真没图）；`random_filled > 0` 可另做一条软提示，让飞书看到「本篇有 N 张是随机替补（位置对、图可能泛）」。`_resolve_illustration_outcome`（ai_illustrate_svc.py）相应读 `random_filled` 生成软提示，不当 error/不进重试。

## 6. 范围与非目标

- **范围**：goal + pipeline（共用 `illustrate_one`）。
- **非目标（本次不动，决策 3）**：
  - 方案运行（`scheme_executor`）保持零随机填。
  - 不修 `GEO_BAIDU_API_KEY` 缺失（运维止血，独立处理）。
  - 不改 writer/orchestrator skill（清单本就正确）。
  - 不改联网搜图质量 / 千帆 QPS（另线）。

## 7. 测试计划（TDD，全本地、不碰生产）

先红后绿。核心新单测挂 `_maybe_insert_images`（可无 DB 用 stub `pick_image_id` / `fetch_image_by_id`）：

1. **5 锚点缺 1 → 替补精确落在缺的那个锚点、其余 4 个不动**（对齐目标行为例子）。
2. `random_fill_missed=True` 但 `anchored=0` → 不补（#1182）。
3. `max_images` 上限：替补计入总数、不超上限。
4. 随机池空 → 该锚点留空、不崩、返回诊断 `missed` 计入。
5. 优先级：本地栏目有图时用本地、不触发随机替补；联网补到时不触发随机替补。
6. 替补图**不附 url 段落**（断言插入的只有 image 节点）。
7. `random_fill_missed=False`（默认）→ 零随机替补（回归护栏，覆盖 scheme / 手动路径）。

集成/端到端：

8. `illustrate_one` game_list 路径：某游戏栏目空、web_fallback 关 → 替补落在该游戏标题后（2017 的修正版）。
9. pipeline「AI配图」节点走同 `illustrate_one` → 同样行为（共享实现，加一条 smoke）。

迁移：删/改 `server/tests/test_illustration_fallback.py` 中针对 `_spread_positions` / `fill_random_images` / `apply_image_fallback` 的用例，能力迁到新机制的单测。

## 8. 风险

- `_maybe_insert_images` 是插图唯一权威、被多路径共用；改动必须靠默认 `False` 保证非目标路径逐字节等价。回归护栏（测试 7）是硬门禁。
- 透传层多（6 个函数），机械转发易漏；实现时逐函数核对签名。
- 随机替补图仍可能「图不对文」——本设计只保证「位置对、不抢位、可留空」，图本身相关性靠补 `GEO_BAIDU_API_KEY` 恢复联网精准补图解决（非本设计目标）。
