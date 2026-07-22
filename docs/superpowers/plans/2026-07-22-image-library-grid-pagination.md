# 图片库素材网格分页 + 调大卡片 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给图片库素材网格加纯前端页码分页（每页 12 张）并调大卡片，把"进栏目一次性拉全部原图"改成"每页只加载 12 张原图"，缓解带宽卡顿。

**Architecture:** 元数据仍一次 `listImages` 全量拉回（几十~一两百张、JSON 很小），前端用 `currentPage` + `PAGE_SIZE` 对 `images` 做切片，网格只渲染当前页；新增局部组件 `GridPagination` 渲染底部页码条；lightbox 继续基于完整 `images` 全局 index 跨页浏览。全部改动在 `web/`，不动后端、无迁移、无发版。

**Tech Stack:** React 19 + TypeScript（strict）+ 现有 CSS 变量系统（`web/src/styles.css`），图标用已 import 的 `lucide-react`（`ChevronLeft`/`ChevronRight`）。

## Global Constraints

- **纯前端**：只改 `web/src/features/image-library/ImageLibraryWorkspace.tsx` 和 `web/src/styles.css`。不动后端、无 DB 迁移、不发后端版。
- **前端无单测框架**（CLAUDE.md 明确：无 vitest/jest）。每个任务的"验证" = `pnpm --filter @geo/web typecheck` 通过 + 手动清单；全部完成后再跑一次 `pnpm --filter @geo/web build`。命令在仓库根 `e:\geo` 执行（当前分支 `feat/image-library-grid-pagination`，非 worktree）。
- `PAGE_SIZE = 12`（模块级常量，组件函数外）。
- 卡片网格目标尺寸：`repeat(auto-fill, minmax(min(100%, 480px), 560px))`。
- Lightbox 用**完整 `images` 数组的全局 index**（不受当前页限制），跨页浏览。
- CSS 只用已存在的主题变量：`--accent`、`--fg`、`--fg-2`、`--fg-3`、`--paper`、`--hair`、`--hair-2`、`--r-sm`。
- 所有面向用户文案用中文。

---

### Task 1: 分页状态 + 切片渲染 + `GridPagination` 组件 + 布局/样式

**Files:**
- Modify: `web/src/features/image-library/ImageLibraryWorkspace.tsx`
- Modify: `web/src/styles.css`

**Interfaces:**
- Produces（后续任务依赖）：
  - 模块级常量 `PAGE_SIZE: number`（= 12）。
  - state `currentPage: number`、setter `setCurrentPage: (p: number) => void`（1-indexed）。
  - 派生 `totalPages: number`、`pageStart: number`、`pageImages: StockImage[]`。
  - 局部组件 `GridPagination`、纯函数 `pageList(page, total): (number | "…")[]`。
  - 新 CSS class：`.imageLibraryContent`、`.imageLibraryPagination`、`.imageLibraryPageBtn`、`.imageLibraryPageGap`、`.imageLibraryPageInfo`。

- [ ] **Step 1: 加模块级常量 `PAGE_SIZE`**

在 `ImageLibraryWorkspace.tsx` 的 `type CategorySort = ...`（第 7 行）**上方**插入：

```tsx
const PAGE_SIZE = 12;
```

- [ ] **Step 2: 加 `currentPage` state**

在 `const [lightboxIndex, setLightboxIndex] = useState<number | null>(null);`（第 63 行）**下方**插入：

```tsx
const [currentPage, setCurrentPage] = useState(1);
```

- [ ] **Step 3: 加派生切片**

在 `const lightboxImage = lightboxIndex !== null ? ...`（第 421 行）**上方**插入：

```tsx
const totalPages = Math.max(1, Math.ceil(images.length / PAGE_SIZE));
const pageStart = (currentPage - 1) * PAGE_SIZE;
const pageImages = images.slice(pageStart, pageStart + PAGE_SIZE);
```

- [ ] **Step 4: 网格改渲染 `pageImages` + 全局 index + 包 content wrapper + 挂分页条**

把第 602–657 行整块（`<div className="imageLibraryGrid">` … 其闭合 `</div>`）替换为：

```tsx
        <div className="imageLibraryContent">
          <div className="imageLibraryGrid">
            {loading && Array.from({ length: PAGE_SIZE }).map((_, i) => (
              <div key={i} className="imageLibraryCardSkeleton" />
            ))}
            {!loading && images.length === 0 && selectedCategoryId !== null && (
              <div className="imageLibraryEmptyState">
                <Images size={40} strokeWidth={1.2} />
                <p className="imageLibraryEmptyTitle">这个栏目还没有图片</p>
                <p>点击右上角「上传图片」开始添加</p>
              </div>
            )}
            {!loading && pageImages.map((img, localIdx) => {
              const globalIdx = pageStart + localIdx;
              return (
                <div
                  key={img.id}
                  id={`il-card-${img.id}`}
                  className={`imageLibraryCard${highlightedImageId === img.id ? " imageLibraryCardHighlight" : ""}`}
                >
                  <div className="imageLibraryCardImg" onClick={() => setLightboxIndex(globalIdx)}>
                    <img src={img.url} alt={img.filename} loading="lazy" />
                    <div className="imageLibraryCardOverlay">
                      <span className="imageLibraryCardOverlayName">{img.filename}</span>
                    </div>
                  </div>
                  <div className="imageLibraryCardActions">
                    <button
                      type="button"
                      className="imageLibraryMenuBtn"
                      onClick={(e) => { e.stopPropagation(); setMenuOpenId(menuOpenId === img.id ? null : img.id); }}
                    >
                      <MoreHorizontal size={16} />
                    </button>
                    {menuOpenId === img.id && (
                      <div className="imageLibraryDropdown" ref={menuRef}>
                        <button type="button" onClick={() => openEdit(img)}>
                          <Pencil size={13} /> 编辑标签
                        </button>
                        <button type="button" className="danger" onClick={() => handleDelete(img)}>
                          <Trash2 size={13} /> 删除
                        </button>
                      </div>
                    )}
                  </div>
                  <div className="imageLibraryCardInfo">
                    <p className="imageLibraryCardName" title={img.filename}>{img.filename}</p>
                    {img.tags.length > 0 && (
                      <div className="imageLibraryCardTags">
                        {img.tags.map((tag) => (
                          <span key={tag} className="imageLibraryTag">{tag}</span>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
          {!loading && totalPages > 1 && (
            <GridPagination
              page={currentPage}
              totalPages={totalPages}
              totalCount={images.length}
              onChange={setCurrentPage}
            />
          )}
        </div>
```

> 注意：骨架数量从原来的 `8` 改成了 `PAGE_SIZE`；卡片打开 lightbox 用的是 `globalIdx`（= `pageStart + localIdx`），保证 lightbox 跨页正确。

- [ ] **Step 5: 在文件末尾加 `GridPagination` 组件与 `pageList` 纯函数**

在 `ImageLibraryWorkspace` 函数的闭合 `}`（第 890 行）**之后**、文件末尾追加：

```tsx

// 生成页码列表；超过 7 页时用省略号折叠：始终含首页、末页、当前页 ±1。
function pageList(page: number, total: number): (number | "…")[] {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
  const out: (number | "…")[] = [1];
  const left = Math.max(2, page - 1);
  const right = Math.min(total - 1, page + 1);
  if (left > 2) out.push("…");
  for (let p = left; p <= right; p++) out.push(p);
  if (right < total - 1) out.push("…");
  out.push(total);
  return out;
}

function GridPagination({
  page,
  totalPages,
  totalCount,
  onChange,
}: {
  page: number;
  totalPages: number;
  totalCount: number;
  onChange: (p: number) => void;
}) {
  return (
    <div className="imageLibraryPagination">
      <button
        type="button"
        className="imageLibraryPageBtn"
        disabled={page <= 1}
        onClick={() => onChange(page - 1)}
        aria-label="上一页"
      >
        <ChevronLeft size={16} />
      </button>
      {pageList(page, totalPages).map((p, i) =>
        p === "…" ? (
          <span key={`gap-${i}`} className="imageLibraryPageGap">…</span>
        ) : (
          <button
            key={p}
            type="button"
            className={`imageLibraryPageBtn${p === page ? " active" : ""}`}
            onClick={() => onChange(p)}
          >
            {p}
          </button>
        ),
      )}
      <button
        type="button"
        className="imageLibraryPageBtn"
        disabled={page >= totalPages}
        onClick={() => onChange(page + 1)}
        aria-label="下一页"
      >
        <ChevronRight size={16} />
      </button>
      <span className="imageLibraryPageInfo">共 {totalCount} 张 · 第 {page}/{totalPages} 页</span>
    </div>
  );
}
```

> `ChevronLeft` / `ChevronRight` / `Images` / `MoreHorizontal` / `Pencil` / `Trash2` 均已在第 2 行 import，无需新增。

- [ ] **Step 6: CSS — content wrapper 布局 + 分页条样式**

在 `web/src/styles.css` 的 `.imageLibraryGrid {`（第 2365 行）**上方**插入布局 wrapper：

```css
.imageLibraryContent { flex: 1; display: flex; flex-direction: column; min-height: 0; }
```

在 `.imageLibraryEmpty { ... }`（第 2378 行）**下方**插入分页条样式：

```css
.imageLibraryPagination {
  flex-shrink: 0;
  display: flex; align-items: center; justify-content: center; gap: 6px;
  padding: 10px 12px;
  border-top: 1px solid var(--hair);
}
.imageLibraryPageBtn {
  min-width: 30px; height: 30px; padding: 0 8px;
  display: inline-flex; align-items: center; justify-content: center;
  border: 1px solid var(--hair); border-radius: var(--r-sm);
  background: var(--paper); color: var(--fg-2); cursor: pointer;
  font-size: 13px; transition: background .1s, color .1s;
}
.imageLibraryPageBtn:hover:not(:disabled) { background: var(--hair); color: var(--fg); }
.imageLibraryPageBtn.active { background: var(--accent); color: #fff; border-color: var(--accent); }
.imageLibraryPageBtn:disabled { opacity: .4; cursor: default; }
.imageLibraryPageGap { padding: 0 4px; color: var(--fg-3); }
.imageLibraryPageInfo { margin-left: 10px; font-size: 12px; color: var(--fg-3); }
```

- [ ] **Step 7: typecheck**

Run: `pnpm --filter @geo/web typecheck`
Expected: 无报错（tsc -b 通过）。

- [ ] **Step 8: 手动验证**

启动前端 `pnpm --filter @geo/web dev`（或复用已运行的 5173）；进入图片库、选一个图较多的栏目：
- 网格一次只渲染 12 张卡片，底部出现页码条「共 N 张 · 第 1/M 页」。
- 点页码 / 上一页 / 下一页可切页；切页后浏览器 Network 面板只新拉该页图片（不是全部）。
- 图少于等于 12 张的栏目：不显示页码条。
- 点任一卡片打开大图，左右箭头能循环浏览（此任务未改 lightbox 逻辑，应仍可用）。

- [ ] **Step 9: Commit**

```bash
git add web/src/features/image-library/ImageLibraryWorkspace.tsx web/src/styles.css
git commit -m "feat(image-library): 素材网格页码分页 + GridPagination 组件"
```

---

### Task 2: 状态转换集成（切栏目/切 tab 重置、上传跳首页、页码越界钳制）

**Files:**
- Modify: `web/src/features/image-library/ImageLibraryWorkspace.tsx`

**Interfaces:**
- Consumes：`PAGE_SIZE`、`currentPage`、`setCurrentPage`、`images`、`selectedCategoryId`、`uploadCategoryId`（均来自 Task 1 及现有代码）。

- [ ] **Step 1: 切换栏目时重置到第 1 页**

在依赖 `selectedCategoryId` 的 effect（第 160 行起，`useEffect(() => { setLightboxIndex(null); ...`）里，把开头的 `setLightboxIndex(null);` 改成两行：

```tsx
    setLightboxIndex(null);
    setCurrentPage(1);
```

> 切主推/陪衬 tab 会改 `selectedCategoryId`（第 134 行 effect 重新选栏目），从而触发本 effect，一并重置页码 —— 无需再单独处理 `kindTab`。

- [ ] **Step 2: 加"页码越界自动钳制"effect（覆盖删除后回退）**

在 Step 1 的 effect **下方**新增一个 effect（放在第 171 行 `}, [selectedCategoryId]);` 之后）：

```tsx
  // 删除等导致总页数减少时，把 currentPage 钳回合法范围，避免停在空页。
  useEffect(() => {
    const total = Math.max(1, Math.ceil(images.length / PAGE_SIZE));
    if (currentPage > total) setCurrentPage(total);
  }, [images, currentPage]);
```

> 现有 `handleDelete`（第 312 行）已 `setImages(filter)`；本 effect 监听 `images` 变化自动回退，无需改 `handleDelete`。

- [ ] **Step 3: 上传成功后跳回第 1 页**

在 `handleUpload`（第 284 行）末尾，`showToast(\`上传完成…\`)` 之后追加：

```tsx
    if (uploadCategoryId === selectedCategoryId) setCurrentPage(1);
```

> 新图 `created_at` 最新、排在 `images` 最前（第 298 行 unshift），跳第 1 页即可看到；只在上传到当前正查看的栏目时跳。

- [ ] **Step 4: typecheck**

Run: `pnpm --filter @geo/web typecheck`
Expected: 无报错。

- [ ] **Step 5: 手动验证**

- 翻到第 2/3 页 → 切换到另一个栏目 → 回到第 1 页。
- 翻到第 2/3 页 → 切主推/陪衬 tab → 回到第 1 页。
- 停在最后一页且该页只剩 1 张 → 删除它 → 自动回退上一页（不停留在空页）。
- 上传新图到当前栏目 → 自动跳第 1 页并看到新图。

- [ ] **Step 6: Commit**

```bash
git add web/src/features/image-library/ImageLibraryWorkspace.tsx
git commit -m "feat(image-library): 切栏目/tab重置页码 + 删除回退 + 上传跳首页"
```

---

### Task 3: 搜索结果跳转适配分页（先翻到目标页再滚动高亮）

**Files:**
- Modify: `web/src/features/image-library/ImageLibraryWorkspace.tsx`

**Interfaces:**
- Consumes：`PAGE_SIZE`、`currentPage`、`setCurrentPage`、`pendingJump`、`images`、`loading`（Task 1 + 现有）。

- [ ] **Step 1: 改写 pendingJump 的 scroll+highlight effect**

把第 173–188 行的 effect（`// After images load, if there's a pending jump ...` 到其 `}, [images, pendingJump, loading]);`）整块替换为：

```tsx
  // 点搜索结果跳转：目标图可能不在当前页，先翻到它所在页，再滚动 + 高亮。
  useEffect(() => {
    if (!pendingJump || loading) return;
    const idx = images.findIndex((img) => img.id === pendingJump.imageId);
    if (idx === -1) return;

    const targetPage = Math.floor(idx / PAGE_SIZE) + 1;
    if (currentPage !== targetPage) {
      setCurrentPage(targetPage);
      return; // 切页后本 effect 会因 currentPage 变化再跑一次，届时卡片已渲染
    }

    const el = document.getElementById(`il-card-${pendingJump.imageId}`);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      setHighlightedImageId(pendingJump.imageId);
      setTimeout(() => {
        setHighlightedImageId(null);
      }, 1500);
    }
    setPendingJump(null);
  }, [images, pendingJump, loading, currentPage]);
```

> 关键变化：用 `findIndex` 拿到全局下标算出 `targetPage`；不在目标页时先 `setCurrentPage` 并 `return`，等重渲染后 effect 靠新增的 `currentPage` 依赖再次触发，此时目标卡片已在 DOM 里才 `scrollIntoView`。到达目标页后无条件 `setPendingJump(null)`，避免残留反复触发。

- [ ] **Step 2: typecheck**

Run: `pnpm --filter @geo/web typecheck`
Expected: 无报错。

- [ ] **Step 3: 手动验证**

- 在顶部搜索框搜一个关键词 → 点一条结果，其目标图位于第 N 页（N>1）→ 页面自动翻到第 N 页，目标卡片滚动到视野中央并短暂高亮。
- 搜索结果目标图属于另一个 tab（主推/陪衬）→ 先切 tab 再切栏目再翻页高亮，链路仍通。

- [ ] **Step 4: Commit**

```bash
git add web/src/features/image-library/ImageLibraryWorkspace.tsx
git commit -m "feat(image-library): 搜索跳转适配分页(先翻到目标页再高亮)"
```

---

### Task 4: 卡片调大（CSS）

**Files:**
- Modify: `web/src/styles.css`

**Interfaces:**
- Consumes：无（独立视觉改动）。

- [ ] **Step 1: 调大网格列宽**

把 `.imageLibraryGrid`（第 2365 行）里的：

```css
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
```

改为：

```css
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 480px), 560px));
```

- [ ] **Step 2: typecheck + build**

Run: `pnpm --filter @geo/web typecheck && pnpm --filter @geo/web build`
Expected: 均通过（build 产出 `web/dist`）。

- [ ] **Step 3: 手动验证**

- 网格卡片明显变大（典型宽度下每行约 2–3 列，随窗口宽度自适应）。
- 窄窗口下卡片不溢出（`min(100%, 480px)` 保证不超过容器宽度）。
- 分页条在网格底部、随主题（暗/亮）正确变色，激活页码为品牌紫色。

- [ ] **Step 4: Commit**

```bash
git add web/src/styles.css
git commit -m "style(image-library): 素材卡片调大至 480-560px"
```

---

## Self-Review

**1. Spec coverage：**
- §1 分页状态切片 → Task 1 Step 1–3。
- §2 底部页码控件 + 布局 wrapper → Task 1 Step 4–6。
- §3 集成点（切栏目/tab 重置、上传跳首页、删除回退）→ Task 2；搜索跳转 → Task 3。
- §4 Lightbox 跨全部图 → Task 1 用 `globalIdx` 传入，lightbox 现有逻辑基于完整 `images` 未改，满足。
- §5 卡片调大 + 骨架数 → Task 4（minmax）+ Task 1 Step 4（骨架 8→PAGE_SIZE）。
- §"验证"手动清单 → 各 Task 的手动验证步骤覆盖。

**2. Placeholder scan：** 无 TBD/TODO；所有代码步骤给出完整代码；CSS 变量均为真实存在的主题变量。

**3. Type consistency：** `PAGE_SIZE`/`currentPage`/`setCurrentPage`/`pageStart`/`totalPages`/`pageImages` 命名跨 Task 一致；`GridPagination` props（`page`/`totalPages`/`totalCount`/`onChange`）与 Task 1 Step 4 的调用一致；`pageList` 签名 `(page, total) => (number | "…")[]` 与使用一致。
