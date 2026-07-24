# 游戏库页面重构并接管「图片库」tab — 设计稿

- 日期：2026-07-20
- 分支：`feat/game-library-corpus`
- 状态：设计已定，待写实现计划
- 关联：`demo.pen`（根目录设计稿，frame「游戏库桌面展示」`uAkUP` / 主内容 `FdaLS`）；游戏库语料底座设计 `docs/superpowers/specs/2026-07-17-game-library-corpus-design.md`

## 背景

`feat/game-library-corpus` 分支已落地游戏库语料底座：后端 `games` / `game_tags` 表 + 跨源入库 + MCP 检索端点 + 前台只读 web 接口（`router_web.py`：`GET /api/game-library/tags|games|games/{id}`），以及一个**新增的独立「游戏库」tab**（`web/src/features/game-library/GameLibraryWorkspace.tsx`）。

但这个已写出来的前端**与设计稿 `demo.pen` 对不上**：demo 是一套深色紫调、信息密度高的游戏库工作台，而现有前端是朴素的「顶部 header + 左列表右详情」。

同时，现有系统里还有一个独立的「图片库」tab（`ImageLibraryWorkspace.tsx`）——它按 `StockCategory.kind`（`main` 主推 / `companion` 陪衬）管理图片桶，每个栏目≈一个游戏的图片桶，支持栏目 CRUD、批量上传、改标签、删图、全局搜图、灯箱。而游戏库后端里，每个 `game` 通过 `stock_category_id` 挂到一个 `StockCategory`：入库时 `get_or_create_companion_category(game.name)` 为每个游戏建同名 companion 栏目，截图 rehost 进桶。

## 目标

1. 把游戏库页面**按 `demo.pen` 重建**（视觉 1:1）。
2. 让它**接管「图片库」tab 的槽位**，成为唯一入口——不再是「图片库 + 游戏库」两个并列 tab。
3. 把图片库的图片管理能力（上传/改标签/删图/搜图/灯箱）**并入游戏库页内**的「游戏素材」区。
4. **零后端改动**：只复用现有 `game-library` 只读接口 + `image-library` 接口。

## 非目标（本次不做）

- demo 顶部 `手动入库` / `同步任务` / `删除游戏` 三个按钮 + `陪衬游戏定时抓取浮层`——**这些后端无接口**，本次隐藏、不放出来（代码留 TODO 注释指向未来后端）。
- 不新增任何 `game_library` web 写接口、不碰 ingest scheduler、不改后端 service/schema。
- 不改 App 侧栏视觉（demo 图里左侧的应用导航是 App.tsx 外壳，已存在；demo 侧栏里的「热榜/分发引擎」等条目只是示意）。

## 关键决策（已与用户确认）

| # | 决策点 | 结论 |
|---|--------|------|
| 1 | tab 归并方式 | 游戏库为主，图片库能力并入游戏库页内；侧栏只留一个入口，落在图片库槽位 |
| 2 | 功能范围 | 视觉对齐 + 复用现有后端；无后端的 4 个动作隐藏 |
| 3 | 主轴 | **纯游戏为主轴（方案 A）**：本页只展示 games 语料 |
| 4 | 孤儿栏目 | **不在本页保留管理入口**——无 `games` 行的图片栏目（手工配图矩阵）在本页不可见（见「已知取舍」） |
| 5 | 导航标签 | 「游戏库」+ `Gamepad2` 图标（贴 demo），退场「图片库」字样 |
| 6 | 素材数据源 | `listImages(game.stock_category_id)` 拿 image-lib 桶里的 `stock_images`（可上传/改签/删） |

## 设计

### A. 路由与导航

- `web/src/types.ts` `navItems`：**删除单独的 `game-library` 项**；把 `image-library` 项的 `label` 改为「游戏库」、`icon` 改为 `Gamepad2`（key 仍为 `image-library`）。
- `web/src/routes.tsx`：
  - `/image-library` → 挂**重构后的游戏库 workspace**（下称 `GameLibraryWorkspace` v2）。
  - `/game-library` → `<Navigate to="/image-library" replace />`（避免旧链接死链）。
- `web/src/App.tsx`：`TAB_TITLES["image-library"]` 改为「游戏库」。`NavKey` union 中 `game-library` 保留（仅供重定向路由匹配），`KNOWN_NAV` 保留 `image-library`。
- **旧 `ImageLibraryWorkspace.tsx` 保留文件、不再被任何路由/导航引用（休眠）**。文章自动配图后端 hook 直接查 `StockCategory`/`StockImage`，不经此 UI，故配图功能不受影响；未来要恢复孤儿栏目管理，重新挂一条路由即可。

### B. 组件分解（`web/src/features/game-library/`）

重建 `GameLibraryWorkspace.tsx` 为容器（状态 + 数据编排），拆出以下子组件，各自单一职责、便于独立理解与测试：

- `GameLibraryWorkspace`（容器）：拥有全部 state 与数据拉取，组合下列子组件。
- `GameLibraryHeader`：面包屑 + 大标题 + 记录数徽标 + 搜索框 + 排序下拉。
- `GameGroupSwitch`：主推/陪衬分组切换（segmented）。
- `GameList` / `GameListRow`：左侧游戏列表 + 单行。
- `GameDetailCard`：右上富详情卡（评分/指标/状态条/描述/底部元信息）。
- `GameMaterialPanel`：截图素材网格 + 「上传图片」+ 灯箱（复用 image-lib 上传/改签/删图逻辑）。
- `GameSourceTable`：来源与标签 key-value 表。
- 上传/编辑标签/删除/灯箱等 modal 复用现有 image-lib 交互模式（可从 `ImageLibraryWorkspace` 抽取共享，或在本模块内实现精简版；实现计划里定）。

### C. 页面结构（对齐 demo.pen `FdaLS` 主内容）

主内容：竖排，`padding [48,52,40,52]`，`gap 28`，bg `#0D1020`。

1. **页面头部**（`rKgfo`，space_between）
   - 左「标题区」：面包屑「素材 / 游戏语料」；标题行 = 大标题「游戏库」（52px / 800）+ 记录数徽标「N 款游戏」（mono）。
   - 右「头部操作」：搜索框（380 宽，占位「搜索游戏名…」）+ 排序下拉。
   - **隐藏**：`手动入库` / `同步任务` / `删除游戏` 按钮、`陪衬游戏定时抓取浮层`。
2. **页面主体**（`v6W7d9`，横排 `gap 18`，撑满高度）
   - **左检索面板**（`mcJoY`，245 宽，竖排）
     - `游戏分组切换`（`jbAUO`）：`主推游戏` / `陪衬游戏` 两段。
     - `游戏列表`（`ECL8d`，`panel` 底，滚动）：每行（`70` 高）= 38×38 图标 + 名称（13/800）+ `score X.X`（mono）+ 标签（`经营 · 美食 · 模拟`）+ `use N`（mono，faint）。选中行紫底 `#2B2650` / 紫描边 `#6B5CE7`，其余透明底 `#22283C` 描边。
   - **详情与素材**（`RIa2h`，自适应宽，竖排 `gap 18`）
     - `游戏详情卡`（`oHrzJ`，`panel` 底）：
       - 顶部 space_between：左=64×64 圆角图标 + 名称（下架时挂「已下架」徽标）+ 标签 chip 行；右=64px 大评分 + 「综合评分 · score」+ 「评论 X万+ / 平台 N」。
       - `生文取材状态条`（`ekLsW`）：紫调条，左「生文取材详情：主游戏候选 · 可绑定截图素材 · 保存文章后回写 use_count」，右 `selected_games.game_id = N`（mono）。
       - `库字段指标`（`kPZSG`）：5 个指标块（入库来源 sources / 支持平台 platforms / 引用次数 use_count / 素材栏目 #stock_category_id / 最近校验 last_verified_at），每块 = 字段名 + 值（16/800）+ 说明。
       - 描述段（`games.description`，`lineHeight 1.5`）。
       - 底部元信息（`Q4O3Yv`，上描边）：`被引用 N 次` pill + `last_used_at` + `first_seen_at`（左）；`source_game_id: taptap/… · baidu/…`（右，mono）。
     - `素材与来源`（`AESdf`，横排 `gap 18`）
       - `游戏截图素材`（`PMsQE`，自适应）：标题行（「游戏素材」+ 数量徽标 + `上传图片` 紫钮）+ 缩略图网格（每卡 148 宽 = 70 高图 + mono 文件名）。
       - `来源与标签表`（`uGr3x`，420 宽）：表头「入库来源」+ 字段行 `source` / `name_normalized` / `game_tags` / `screenshot_urls` / `last_used_article_id`（每行左字段名单元 145 宽 + 右值单元自适应）。

### D. 数据流 / 接线（全部复用，零后端）

| 交互 | 接口 | 说明 |
|------|------|------|
| ~~标签集~~ | ~~`listGameTags()`~~ | **本次不用**：demo 头部只有搜索 + 排序、无标签下拉；接口保留可用，v2 不调 |
| 分组集 | `listCategories("main")` + `listCategories("companion")` | 得到 main / companion 两组 `StockCategory.id` 集合 |
| 游戏列表 | `listGames({ q, limit: 200 })` | 服务端按 `name like` 搜索；总数入头部徽标 |
| 分组归属 | 前端计算 | `game.stock_category_id ∈ main 集` → 主推；`∈ companion 集` → 陪衬；无桶兜底进陪衬 |
| 排序 | 前端 | 评分优先 / 取材最少（`use_count` 升）/ 最近取材（`last_used_at` 降），接排序下拉 |
| 选中详情 | `getGame(id)` | 富详情卡 + 来源表全部字段来自 `GameDetail` |
| 素材网格 | `listImages(game.stock_category_id)` | image-lib 桶里的 `stock_images`；`stock_category_id` 为空 → 显示「暂无素材桶」、上传禁用 |
| 上传 | `uploadImage({ category_id: game.stock_category_id, file })` | 复用 image-lib；上传后并入当前素材网格 |
| 改标签 / 删图 / 灯箱 | `updateImage` / `deleteImage` + 灯箱组件 | 复用 image-lib 交互 |

- 竞态防护沿用现有前端写法：列表用 `seqRef` 递增序号丢弃过期响应；详情用 `detailSeq`。
- 分组切换 / 搜索变化后，若当前选中游戏已不在新列表，自动选中列表首项（沿用现有 `GameLibraryWorkspace` 的自动选中逻辑）。

### E. API 客户端

- `web/src/api/game-library.ts`：已有 `listGameTags` / `listGames` / `getGame`，**无需新增**。
- `web/src/api/image-library.ts`：复用 `listCategories` / `listImages` / `uploadImage` / `updateImage` / `deleteImage`（如需搜图再引 `searchImages`）。**无需新增**。

### F. 样式

- 现有约 54 条 `gameLib*` CSS 规则**按 demo 调色板重做**：bg `#0D1020`、sidebar `#0B0E19`、panel `#151827`、panel2 `#1B1E30`、accent `#8B5CF6`、accent2 `#A99BFF`、border `#2A2E45`、border2 `#343A56`、text `#F5F7FF`、muted `#8D94AE`、faint `#596179`；正文字体 Inter、数字/字段名用 mono（Geist Mono）。选中/主推强调用紫（`#2B2650` 底 / `#6B5CE7` 描边）。
- 全部集中在 `web/src/styles.css` 的 `gameLib*` 命名空间，不外溢污染其它 tab。

## 已知取舍 / 风险

- **孤儿栏目失去 UI 管理入口**：没有 `games` 行的 `StockCategory`（尤其手工维护的 `main` 配图矩阵，如「餐厅养成记」）在本页不可见。旧 `ImageLibraryWorkspace` 休眠（不删），文章自动配图后端不受影响；未来若需恢复孤儿栏目管理，重挂旧路由或新增管理面即可。用户已确认接受。
- **无后端的 demo 动作暂缺**：`手动入库 / 同步任务 / 删除游戏 / 定时抓取配置` 本次不放；未来补 `game_library` web 写接口时再启用（代码留 TODO 锚点）。
- **`stock_category_id` 为空的游戏**：素材区退化为空态、上传禁用（理论上 ingest 必建桶，属边界兜底）。

## 测试

- 前端无单测框架，CI 门禁 = `pnpm --filter @geo/web typecheck` + `pnpm --filter @geo/web build`，两者必须绿。
- 起 Vite dev server（5173），逐块用截图对照 `demo.pen`（头部 / 列表 / 详情卡 / 素材区 / 来源表）核视觉保真。
- 手动验证接线：切主推/陪衬分组、搜索、排序、选中不同游戏刷新详情、上传一张图进某游戏桶后网格更新、删图、灯箱。

## 影响的文件（预估）

- 改：`web/src/types.ts`（navItems）、`web/src/routes.tsx`（路由重定向 + 挂载）、`web/src/App.tsx`（TAB_TITLES）、`web/src/styles.css`（gameLib* 重做）。
- 改/重建：`web/src/features/game-library/GameLibraryWorkspace.tsx` + 新增拆分子组件文件。
- 保留休眠：`web/src/features/image-library/ImageLibraryWorkspace.tsx`（不再引用）。
- 复用不改：`web/src/api/game-library.ts`、`web/src/api/image-library.ts`。
- 后端：**无改动**。
