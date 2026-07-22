# 图片库素材网格分页 + 调大卡片 — 设计

- 日期：2026-07-22
- 状态：待评审（brainstorming 产出）
- 影响面：**纯前端**（`web/`），不动后端、不发后端版

## 背景 / 问题

图片库页面（`/image-library`）在进入某个游戏栏目时，`listImages({ category_id })` 一次性拉回该栏目**全部**图片，前端 `images.map()` 一次性渲染所有卡片（`ImageLibraryWorkspace.tsx:167` 拉取、`:613` 渲染）。

真正的带宽瓶颈是：每张卡片的 `img.url` 指向后端 `serve_image_file`（`server/app/modules/image_library/router.py:527`），**代理的是 MinIO 原图全尺寸，没有缩略图**。当一个栏目图多且单图质量大时，网格里几十上百张 `<img>` 同时拉原图，`loading="lazy"` 也挡不住滚动时的全量拉取，浏览器传输带宽被打满、页面卡顿。

## 目标 / 非目标

**目标**
- 素材网格加**底部页码分页**，每页只渲染固定数量的卡片，浏览器一次只拉当前页这些原图。
- 卡片展示比例调大，保证观感。
- 改动尽量小、可当天上线，不牵动后端与发版。

**非目标（本次不做，YAGNI）**
- 不做后端分页（`limit/offset`）—— 单栏目量级为几十到一两百张，元数据 JSON 一次拉仅几十 KB，不是瓶颈。
- 不做缩略图生成/存储 —— 那是根治带宽的更大工程（要动后端、MinIO、可能迁移），本次先用分页把"同时加载的原图数"降下来。
- 不做每页数量选择器、不把页码写进 URL。

## 现状（代码位置）

- 前端组件：`web/src/features/image-library/ImageLibraryWorkspace.tsx`
  - 拉图：`:160-171` 的 effect，依赖 `selectedCategoryId`。
  - 渲染网格：`:602-656`，`images.map(...)`。
  - Lightbox 左右切换：`:110-119`（键盘）、`:862-885`（按钮），基于完整 `images` 数组 + 全局 index 循环。
  - 搜索跳转 pendingJump：`:174-188`，`scrollIntoView` + 高亮目标卡片。
  - 上传插入：`:298`（新图 unshift 到 `images`）。
  - 删除：`:317`（从 `images` 过滤）。
  - 侧栏栏目角标 `images.length`：`:590-592`（总数，分页后仍显示总数）。
- 前端 API：`web/src/api/image-library.ts:49` `listImages`（不变）。
- 卡片网格 CSS：`web/src/styles.css:2365-2373` `.imageLibraryGrid`，当前 `repeat(auto-fill, minmax(160px, 1fr))`（卡片偏小、`overflow-y:auto` 内部滚动）；容器 `.imageLibraryLayout`（flex row，:2347）+ `.imageLibrarySidebar`（180px，:2349）。
- 后端 `GET /images`：`server/app/modules/image_library/router.py:511`，`.all()` 全量、按 `created_at desc` 排序 —— **本次不改**。

## 设计

### 1. 分页状态（纯前端切片）

- `images` 仍存该栏目全量元数据（一次 `listImages` 拉回），不变。
- 新增：
  - 常量 `PAGE_SIZE = 12`。
  - state `currentPage`（1-indexed）。
- 派生：
  - `totalPages = Math.max(1, Math.ceil(images.length / PAGE_SIZE))`。
  - `pageImages = images.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE)`。
- 网格渲染改为遍历 `pageImages`（而非 `images`）。卡片 `onClick` 打开 lightbox 时传入的是**全局 index**（`(currentPage-1)*PAGE_SIZE + idx`），保证 lightbox 跨页浏览正确（见 §4）。

### 2. 底部页码控件（新增局部组件）

- 在网格下方渲染分页条：`‹ 上一页  1 … M-1 [M] M+1 … N  下一页 ›`，加文案「共 X 张 · 第 M/N 页」。
- 页码过多时用省略号折叠（首页、末页、当前页 ±1 常显）。
- `totalPages <= 1` 时整条隐藏。
- 抽成同文件内的局部组件 `GridPagination`（props：`page`、`totalPages`、`totalCount`、`onChange`），职责单一、便于独立理解；不引第三方分页库。
- 布局：新增 `.imageLibraryContent`（flex column）包住 `.imageLibraryGrid`（滚动区）+ 分页条（固定底部），分页条不随网格滚动。

### 3. 与现有逻辑的集成（逐条）

| 场景 | 处理 |
|---|---|
| 切换栏目 / 切主推·陪衬 tab | `currentPage` 重置为 1（并入现有 `selectedCategoryId` / `kindTab` 的 effect） |
| 上传新图 | 新图 `created_at` 最新、排最前 → 上传成功后 `setCurrentPage(1)`，用户立刻在首页看到 |
| 删除图 | 删除后若当前页变空且 `currentPage > 1` → `setCurrentPage(p => min(p, newTotalPages))` 回退，避免停在空页 |
| 搜索结果跳转 | 在 pendingJump 定位到目标图后，先算它在 `images` 中的下标 → `targetPage = floor(index / PAGE_SIZE) + 1` → `setCurrentPage(targetPage)`，再 `scrollIntoView` + 高亮。现有 `:174-188` effect 需先确保目标页已切换（目标卡片渲染出来后才能 scroll） |

### 4. Lightbox（决策：跨全部图浏览）

- Lightbox 继续基于**完整 `images` 数组 + 全局 index**，左右键/箭头可浏览本栏目全部图，不受当前页限制。
- 依据：数据已在内存，lightbox 一次只显示一张原图，跨页切换不额外增加带宽；体验最顺。
- 关闭 lightbox 后停留在原分页页码（lightbox 内切换不改 `currentPage`）。

### 5. 卡片尺寸调大

- `.imageLibraryGrid`：`minmax(160px, 1fr)` → `minmax(min(100%, 480px), 560px)`（每行约 2–3 列，随容器宽度自适应）。
- 加载骨架数量从 8 调到 `PAGE_SIZE`（12），与每页量对齐。

## 验证

前端无单测框架，门禁 = `pnpm --filter @geo/web typecheck` + `build`。外加手动验证清单：

1. 进入图多的栏目：网格只渲染 12 张，底部出现页码；切页后浏览器只新拉该页图片。
2. 翻到中间页 → 切换栏目 / 切 tab → 回到第 1 页。
3. 上传新图 → 自动跳第 1 页并看到新图。
4. 停在末页且该页只剩 1 张 → 删除它 → 自动回退上一页（不停空页）。
5. 用搜索跳到一张在第 N 页的图 → 自动翻到第 N 页并高亮该图。
6. 打开 lightbox → 左右键可跨页浏览全部图；关闭后仍停在原页码。
7. 只有 1 页（图少）时不显示页码条。

## 影响范围

- `web/src/features/image-library/ImageLibraryWorkspace.tsx`（分页状态、切片渲染、集成点、新增 `GridPagination`）。
- `web/src/styles.css`（`.imageLibraryGrid` minmax + 可能的分页条样式）。
- 无后端改动、无迁移、无发版依赖。
