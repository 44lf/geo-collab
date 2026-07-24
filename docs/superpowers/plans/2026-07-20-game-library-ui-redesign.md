# 游戏库页面重构并接管图片库 tab — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 `demo.pen` 重建游戏库页面，纯游戏为主轴，接管「图片库」tab 槽位成为唯一入口，图片管理能力并入素材区，零后端改动。

**Architecture:** 前端 React 19 组件重构。容器 `GameLibraryWorkspace` 拥有全部数据编排（游戏列表 / 主推·陪衬栏目集 / 选中详情 / 素材），拆出纯展示子组件（Header / GroupSwitch / List / DetailCard / MaterialPanel / SourceTable）。分组靠 `game.stock_category_id` 与 `listCategories("main"|"companion")` 交叉引用得出，纯前端。素材区复用 image-library 接口（`listImages`/`uploadImage`/`updateImage`/`deleteImage`）。

**Tech Stack:** TypeScript（strict）、React 19、Vite、lucide-react、现有 `web/src/api/*` fetch 客户端、`web/src/styles.css`。

## Global Constraints

- 设计真值：`demo.pen` frame「游戏库桌面展示」`uAkUP` / 主内容 `FdaLS`。用 `mcp__pencil__get_screenshot(filePath:"/e:/geo/demo.pen", nodeId:"FdaLS")` 取视觉真值；用 `mcp__pencil__batch_get` 查具体节点尺寸/色值。
- 调色板（demo 变量）：bg `#0D1020`、sidebar `#0B0E19`、panel `#151827`、panel2 `#1B1E30`、accent `#8B5CF6`、accent2 `#A99BFF`、border `#2A2E45`、border2 `#343A56`、text `#F5F7FF`、muted `#8D94AE`、faint `#596179`、danger `#F87171`。字体：正文 Inter；数字/字段名 mono（等宽，用现有 `.mono` 类或 `font-family: ui-monospace, "Geist Mono", monospace`）。
- 紫色强调（选中/主推）：底 `#2B2650`、描边 `#6B5CE7`。
- **零后端改动**：只用现有 `web/src/api/game-library.ts` + `web/src/api/image-library.ts`。不新增/不改后端。
- **隐藏项**（无后端）：`手动入库` / `同步任务` / `删除游戏` 按钮、`陪衬游戏定时抓取浮层`——不渲染，代码处留 `// TODO(game-web-write): ...` 注释锚点。
- **纯游戏为主轴**：本页只展示 games；无 games 行的孤儿栏目不在本页出现。
- 新 CSS 一律用 `gl*` 命名前缀，集中在 `web/src/styles.css`；不复用旧 `gameLib*`/`gameRow*`/`gameCard*` 类（旧规则在 Task 6 清理）。
- **无前端单测框架**：每个 UI 任务的验证门禁 = `pnpm --filter @geo/web typecheck` 必过 + 阶段性 `pnpm --filter @geo/web build` 必过 + 对照 demo 截图核视觉。命令一律从仓库根 `E:\geo` 跑（用 `--filter @geo/web`，不要 `cd web` 以免 cwd 漂移）。
- 提交纪律：每个 Task 末尾一次 commit；commit message 末尾附
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`。

---

## 文件结构

**修改：**
- `web/src/types.ts` — navItems：删 `game-library` 项、`image-library` 项改标签「游戏库」+ 图标 `Gamepad2`；移除未用的 `Images` 导入。
- `web/src/routes.tsx` — `/image-library`→新 `GameLibraryWorkspace`；`/game-library`→重定向；移除 `ImageLibraryWorkspace` 懒加载。
- `web/src/App.tsx` — `TAB_TITLES["image-library"]` 改「游戏库」。
- `web/src/styles.css` — 新增 `gl*` 规则；Task 6 删旧 `gameLib*` 死规则。

**重建：**
- `web/src/features/game-library/GameLibraryWorkspace.tsx` — 容器（数据编排 + 组合子组件；导出 `Seg`/`SortKey` 类型）。

**新建（子组件，均在 `web/src/features/game-library/`）：**
- `GameLibraryHeader.tsx`、`GameGroupSwitch.tsx`、`GameList.tsx`、`GameDetailCard.tsx`、`GameMaterialPanel.tsx`、`GameSourceTable.tsx`。

**保留休眠（不再被引用）：**
- `web/src/features/image-library/ImageLibraryWorkspace.tsx`。

**复用不改：**
- `web/src/api/game-library.ts`、`web/src/api/image-library.ts`、`web/src/components/Toast.tsx`。

---

## Task 1: 导航与路由归并（单入口落图片库槽位）

**Files:**
- Modify: `web/src/types.ts:1`（import 行）、`web/src/types.ts:663-664`（navItems）
- Modify: `web/src/routes.tsx:18-23`（懒加载）、`web/src/routes.tsx:131-132`（路由）
- Modify: `web/src/App.tsx:27`（TAB_TITLES）

**Interfaces:**
- Consumes: 无（结构改动）。
- Produces: 侧栏单一「游戏库」入口，`/image-library` 挂 `GameLibraryWorkspace`，`/game-library` 重定向到 `/image-library`。

- [ ] **Step 1: 改 navItems（types.ts）**

把 `web/src/types.ts` 第 663-664 两行：
```ts
  { key: "image-library", label: "图片库", icon: Images },
  { key: "game-library", label: "游戏库", icon: Gamepad2 },
```
改为一行：
```ts
  { key: "image-library", label: "游戏库", icon: Gamepad2 },
```

- [ ] **Step 2: 移除未用的 `Images` 导入（types.ts:1）**

第 1 行 import 里删掉 `Images,`（`Gamepad2` 保留，仍被用）。改后：
```ts
import { Bot, FileText, Film, Gamepad2, Gem, MessagesSquare, MonitorCog, Plug, RadioTower, Send, Sparkles } from "lucide-react";
```

- [ ] **Step 3: 改路由（routes.tsx）**

第 18-23 行，删除 `ImageLibraryWorkspace` 懒加载定义（整段 4 行），保留 `GameLibraryWorkspace` 懒加载。删除后该区只剩：
```ts
const GameLibraryWorkspace = lazy(() =>
  import("./features/game-library/GameLibraryWorkspace").then((m) => ({ default: m.GameLibraryWorkspace })),
);
```
第 131-132 行两条路由：
```ts
      { path: "image-library", element: <ImageLibraryWorkspace /> },
      { path: "game-library", element: <GameLibraryWorkspace /> },
```
改为：
```ts
      { path: "image-library", element: <GameLibraryWorkspace /> },
      { path: "game-library", element: <Navigate to="/image-library" replace /> },
```
（`Navigate` 已在第 3 行 import，无需新增。）

- [ ] **Step 4: 改 TAB_TITLES（App.tsx:27）**

第 27 行把 `"image-library": "图片库"` 改成 `"image-library": "游戏库"`（同行 `"game-library": "游戏库"` 保留，供重定向匹配，无害）。

- [ ] **Step 5: 验证 typecheck + build**

Run: `pnpm --filter @geo/web typecheck`
Expected: 无报错（`ImageLibraryWorkspace` 未再被 routes 引用；`Images` 已从 types.ts 移除；无 unused 导入报错）。

Run: `pnpm --filter @geo/web build`
Expected: 构建成功。

> 若 typecheck 报 `ImageLibraryWorkspace` 相关 unused：确认 routes.tsx 已彻底删掉其 import 段。若报 `Images` unused：确认 types.ts import 已删。

- [ ] **Step 6: Commit**

```bash
git add web/src/types.ts web/src/routes.tsx web/src/App.tsx
git commit -m "refactor(game-library): 侧栏合并为单一游戏库入口、接管图片库槽位

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: 容器数据层 + 页面头部 + 页面外壳

**Files:**
- Rebuild: `web/src/features/game-library/GameLibraryWorkspace.tsx`
- Create: `web/src/features/game-library/GameLibraryHeader.tsx`
- Modify: `web/src/styles.css`（追加 `gl*` 页面/头部规则）

**Interfaces:**
- Consumes: `listGames`/`getGame`（`../../api/game-library`）、`listCategories`（`../../api/image-library`）、`useToast`（`../../components/Toast`）、类型 `GameListItem`/`GameDetail`/`StockCategory`（`../../types`）。
- Produces（后续任务依赖）：
  - `export type Seg = "main" | "companion"`
  - `export type SortKey = "score" | "least_used" | "recent"`
  - 容器把这些 state 下发给子组件：`total:number`、`q:string`/`setQ`、`sort:SortKey`/`setSort`、`seg:Seg`/`setSeg`、分组后排序的 `games:GameListItem[]`、`mainCount:number`/`companionCount:number`、`selectedId:number|null`/`openDetail(id)`、`detail:GameDetail|null`、`detailLoading:boolean`、`loaded:boolean`。
  - `GameLibraryHeader` 组件，props：`{ total:number; q:string; onQ:(v:string)=>void; sort:SortKey; onSort:(s:SortKey)=>void }`。

- [ ] **Step 1: 写 `GameLibraryHeader.tsx`**

```tsx
import { Search } from "lucide-react";
import type { SortKey } from "./GameLibraryWorkspace";

type Props = {
  total: number;
  q: string;
  onQ: (v: string) => void;
  sort: SortKey;
  onSort: (s: SortKey) => void;
};

export function GameLibraryHeader({ total, q, onQ, sort, onSort }: Props) {
  return (
    <header className="glHeader">
      <div className="glTitleZone">
        <p className="glCrumb">素材 / 游戏语料</p>
        <div className="glTitleRow">
          <h1 className="glTitle">游戏库</h1>
          <span className="glCount">
            <b className="mono">{total.toLocaleString("zh-CN")}</b> 款游戏
          </span>
        </div>
      </div>
      <div className="glActions">
        <label className="glSearch">
          <Search size={15} aria-hidden />
          <input
            type="text"
            value={q}
            onChange={(e) => onQ(e.target.value)}
            placeholder="搜索游戏名…"
            aria-label="搜索游戏名"
          />
        </label>
        <select
          className="glSortSelect"
          value={sort}
          onChange={(e) => onSort(e.target.value as SortKey)}
          aria-label="排序"
        >
          <option value="score">评分优先</option>
          <option value="least_used">取材最少</option>
          <option value="recent">最近取材</option>
        </select>
        {/* TODO(game-web-write): 手动入库 / 同步任务 / 删除游戏 按钮 —— 待后端 game_library web 写接口 */}
      </div>
    </header>
  );
}
```

- [ ] **Step 2: 重建 `GameLibraryWorkspace.tsx`（容器 + 数据编排，body 先放占位）**

```tsx
import { useEffect, useMemo, useRef, useState } from "react";
import { listCategories } from "../../api/image-library";
import { getGame, listGames } from "../../api/game-library";
import type { GameDetail, GameListItem } from "../../types";
import { useToast } from "../../components/Toast";
import { GameLibraryHeader } from "./GameLibraryHeader";

export type Seg = "main" | "companion";
export type SortKey = "score" | "least_used" | "recent";

export function GameLibraryWorkspace() {
  const { toast } = useToast();
  const [raw, setRaw] = useState<GameListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [mainCatIds, setMainCatIds] = useState<Set<number>>(new Set());
  const [q, setQ] = useState("");
  const [seg, setSeg] = useState<Seg>("main");
  const [sort, setSort] = useState<SortKey>("score");
  const [loaded, setLoaded] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<GameDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const seqRef = useRef(0);
  const detailSeq = useRef(0);

  // 主推栏目 id 集合（用于把 game 分到 主推/陪衬）。
  useEffect(() => {
    listCategories("main")
      .then((cats) => setMainCatIds(new Set(cats.map((c) => c.id))))
      .catch(() => {});
  }, []);

  // 游戏列表：搜索变化时（服务端 name like）重拉，带竞态防护。
  useEffect(() => {
    const seq = ++seqRef.current;
    const timer = setTimeout(
      () => {
        listGames({ q: q.trim() || undefined, limit: 200 })
          .then((res) => {
            if (seq !== seqRef.current) return;
            setRaw(res.items);
            setTotal(res.total);
            setLoaded(true);
          })
          .catch((e) => {
            if (seq !== seqRef.current) return;
            toast(e instanceof Error ? e.message : "加载游戏失败", "error");
          });
      },
      q ? 250 : 0,
    );
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);

  // 主推 = 有桶且桶在 main 集；陪衬 = 其余（含无桶），保证没有游戏消失。
  const isMain = (g: GameListItem) =>
    g.stock_category_id != null && mainCatIds.has(g.stock_category_id);
  const mainCount = useMemo(() => raw.filter(isMain).length, [raw, mainCatIds]);
  const companionCount = raw.length - mainCount;

  const games = useMemo(() => {
    let list = raw.filter((g) => (seg === "main" ? isMain(g) : !isMain(g)));
    list = [...list];
    if (sort === "score") list.sort((a, b) => (b.score ?? -1) - (a.score ?? -1));
    else if (sort === "least_used") list.sort((a, b) => a.use_count - b.use_count);
    else if (sort === "recent")
      list.sort((a, b) => (b.last_used_at ?? "").localeCompare(a.last_used_at ?? ""));
    return list;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [raw, seg, sort, mainCatIds]);

  async function openDetail(id: number) {
    setSelectedId(id);
    const seq = ++detailSeq.current;
    setDetailLoading(true);
    try {
      const d = await getGame(id);
      if (seq === detailSeq.current) setDetail(d);
    } catch (e) {
      toast(e instanceof Error ? e.message : "加载游戏详情失败", "error");
    } finally {
      if (seq === detailSeq.current) setDetailLoading(false);
    }
  }

  // 列表变化后自动选中首项（或当前选中已不在列表时）。
  useEffect(() => {
    if (games.length === 0) {
      setSelectedId(null);
      setDetail(null);
      return;
    }
    if (selectedId == null || !games.some((g) => g.game_id === selectedId)) {
      void openDetail(games[0].game_id);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [games]);

  return (
    <div className="glPage">
      <GameLibraryHeader total={total} q={q} onQ={setQ} sort={sort} onSort={setSort} />
      <div className="glBody">
        <aside className="glLeft">{/* Task 3: GroupSwitch + List */}</aside>
        <section className="glRight">
          {detailLoading && !detail && <div className="glDetailLoading">加载中…</div>}
          {loaded && games.length === 0 && (
            <div className="glDetailLoading">没有匹配的游戏；库为空时先跑 ingest_games</div>
          )}
          {/* Task 4/5: DetailCard + MaterialPanel + SourceTable */}
        </section>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: 追加页面/头部 CSS（styles.css 末尾）**

```css
/* ── 游戏库 v2（gl* 命名，对齐 demo.pen）───────────────────────────── */
.glPage {
  display: flex;
  flex-direction: column;
  gap: 28px;
  min-height: 100%;
  padding: 48px 52px 40px;
  background: #0D1020;
  color: #F5F7FF;
}
.glHeader { display: flex; justify-content: space-between; align-items: flex-end; gap: 24px; }
.glTitleZone { display: flex; flex-direction: column; gap: 14px; }
.glCrumb { margin: 0; font-size: 13px; color: #8D94AE; }
.glTitleRow { display: flex; align-items: center; gap: 14px; }
.glTitle { margin: 0; font-size: 52px; font-weight: 800; line-height: 1; color: #F5F7FF; }
.glCount {
  display: inline-flex; align-items: center; gap: 6px;
  height: 28px; padding: 0 10px; border-radius: 6px;
  background: #24283B; border: 1px solid #2A2E45;
  font-size: 12px; color: #8D94AE;
}
.glCount b { font-size: 13px; font-weight: 700; color: #A99BFF; }
.glActions { display: flex; align-items: center; gap: 10px; }
.glSearch {
  display: flex; align-items: center; gap: 9px;
  width: 380px; height: 46px; padding: 0 16px;
  background: #141827; border: 1px solid #2A2E45; border-radius: 8px;
  color: #8D94AE;
}
.glSearch input {
  flex: 1; min-width: 0; border: none; background: transparent;
  color: #F5F7FF; font-size: 14px; outline: none;
}
.glSearch input::placeholder { color: #596179; }
.glSortSelect {
  height: 46px; padding: 0 16px; border-radius: 8px;
  background: #161A29; border: 1px solid #2A2E45; color: #F5F7FF;
  font-size: 13px; cursor: pointer;
}
.glBody { display: flex; gap: 18px; flex: 1; min-height: 0; }
.glLeft { display: flex; flex-direction: column; gap: 14px; width: 245px; flex-shrink: 0; }
.glRight { display: flex; flex-direction: column; gap: 18px; flex: 1; min-width: 0; }
.glDetailLoading {
  display: flex; align-items: center; justify-content: center;
  padding: 40px; border-radius: 10px;
  background: #151827; border: 1px solid #2A2E45; color: #8D94AE; font-size: 14px;
}
```

- [ ] **Step 4: 验证 typecheck + build + 视觉**

Run: `pnpm --filter @geo/web typecheck` → Expected: 通过。
Run: `pnpm --filter @geo/web build` → Expected: 通过。
视觉：起 `pnpm --filter @geo/web dev`，浏览 `/image-library`；对照 `mcp__pencil__get_screenshot(nodeId:"rKgfo")`（页面头部），核对大标题 52px、记录数徽标、搜索框宽度/描边、排序下拉。头部与 demo 一致即可（body 尚空）。

- [ ] **Step 5: Commit**

```bash
git add web/src/features/game-library/GameLibraryWorkspace.tsx web/src/features/game-library/GameLibraryHeader.tsx web/src/styles.css
git commit -m "feat(game-library): v2 容器数据层 + 页面头部（对齐 demo）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: 左检索面板（主推/陪衬切换 + 游戏列表）

**Files:**
- Create: `web/src/features/game-library/GameGroupSwitch.tsx`
- Create: `web/src/features/game-library/GameList.tsx`
- Modify: `web/src/features/game-library/GameLibraryWorkspace.tsx`（`.glLeft` 内挂两组件）
- Modify: `web/src/styles.css`（追加左面板 CSS）

**Interfaces:**
- Consumes: 容器下发的 `seg`/`setSeg`、`mainCount`/`companionCount`、`games`/`selectedId`/`openDetail`/`loaded`；类型 `Seg`（`./GameLibraryWorkspace`）、`GameListItem`（`../../types`）。
- Produces:
  - `GameGroupSwitch` props：`{ seg:Seg; onSeg:(s:Seg)=>void; mainCount:number; companionCount:number }`。
  - `GameList` props：`{ games:GameListItem[]; selectedId:number|null; onSelect:(id:number)=>void; loaded:boolean }`。

- [ ] **Step 1: 写 `GameGroupSwitch.tsx`**

```tsx
import type { Seg } from "./GameLibraryWorkspace";

type Props = {
  seg: Seg;
  onSeg: (s: Seg) => void;
  mainCount: number;
  companionCount: number;
};

export function GameGroupSwitch({ seg, onSeg, mainCount, companionCount }: Props) {
  const items: { key: Seg; label: string; count: number }[] = [
    { key: "main", label: "主推游戏", count: mainCount },
    { key: "companion", label: "陪衬游戏", count: companionCount },
  ];
  return (
    <div className="glGroupSwitch">
      {items.map((it) => (
        <button
          key={it.key}
          type="button"
          className={`glSegBtn${seg === it.key ? " active" : ""}`}
          onClick={() => onSeg(it.key)}
        >
          {it.label}
          <span className="glSegCount">{it.count}</span>
        </button>
      ))}
    </div>
  );
}
```

- [ ] **Step 2: 写 `GameList.tsx`**

```tsx
import { Gamepad2 } from "lucide-react";
import type { GameListItem } from "../../types";

type Props = {
  games: GameListItem[];
  selectedId: number | null;
  onSelect: (id: number) => void;
  loaded: boolean;
};

export function GameList({ games, selectedId, onSelect, loaded }: Props) {
  return (
    <div className="glList">
      {loaded && games.length === 0 && (
        <div className="glListEmpty">
          <Gamepad2 size={30} strokeWidth={1.3} />
          <p>没有匹配的游戏</p>
          <span>换个分组/搜索词；库为空时先跑 ingest_games</span>
        </div>
      )}
      {games.map((g) => (
        <button
          key={g.game_id}
          type="button"
          className={`glRow${selectedId === g.game_id ? " active" : ""}`}
          onClick={() => onSelect(g.game_id)}
        >
          {g.icon_url ? (
            <img className="glRowIcon" src={g.icon_url} alt="" loading="lazy" />
          ) : (
            <div className="glRowIcon glRowIconFallback">{g.name.slice(0, 1)}</div>
          )}
          <div className="glRowMain">
            <div className="glRowTop">
              <span className="glRowName" title={g.name}>{g.name}</span>
              {g.score != null && <span className="glRowScore mono">score {g.score.toFixed(1)}</span>}
            </div>
            <div className="glRowBottom">
              <span className="glRowTags" title={g.tags.join(" · ")}>
                {g.tags.slice(0, 3).join(" · ") || "无标签"}
              </span>
              <span className="glRowUse mono">use {g.use_count}</span>
            </div>
          </div>
        </button>
      ))}
    </div>
  );
}
```

- [ ] **Step 3: 在容器 `.glLeft` 里挂载两组件**

`GameLibraryWorkspace.tsx` 顶部补 import：
```tsx
import { GameGroupSwitch } from "./GameGroupSwitch";
import { GameList } from "./GameList";
```
把 `<aside className="glLeft">{/* Task 3 */}</aside>` 替换为：
```tsx
        <aside className="glLeft">
          <GameGroupSwitch
            seg={seg}
            onSeg={setSeg}
            mainCount={mainCount}
            companionCount={companionCount}
          />
          <GameList games={games} selectedId={selectedId} onSelect={openDetail} loaded={loaded} />
        </aside>
```

- [ ] **Step 4: 追加左面板 CSS（styles.css）**

```css
.glGroupSwitch {
  display: flex; gap: 6px; height: 44px; padding: 4px; flex-shrink: 0;
  background: #151827; border: 1px solid #2A2E45; border-radius: 10px;
}
.glSegBtn {
  flex: 1; display: flex; align-items: center; justify-content: center; gap: 6px;
  border: 1px solid #242A40; border-radius: 7px; background: #111523;
  color: #9FA8C7; font-size: 13px; font-weight: 700; cursor: pointer;
}
.glSegBtn.active { background: #2B2650; border-color: #6B5CE7; color: #F5F7FF; }
.glSegCount { font-size: 11px; opacity: 0.8; }
.glList {
  display: flex; flex-direction: column; gap: 2px;
  flex: 1; min-height: 0; overflow-y: auto;
  padding: 12px; border-radius: 10px;
  background: #151827; border: 1px solid #2A2E45;
}
.glRow {
  display: flex; align-items: center; gap: 10px;
  padding: 9px 10px; border-radius: 8px; cursor: pointer; text-align: left;
  background: transparent; border: 1px solid #22283C;
}
.glRow.active { background: #2B2650; border-color: #6B5CE7; }
.glRowIcon {
  width: 38px; height: 38px; flex-shrink: 0; border-radius: 8px;
  object-fit: cover; border: 1px solid #39405B; background: #252A3E;
}
.glRowIconFallback {
  display: flex; align-items: center; justify-content: center;
  color: #F5F7FF; font-size: 16px; font-weight: 700;
}
.glRowMain { display: flex; flex-direction: column; gap: 6px; flex: 1; min-width: 0; }
.glRowTop { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.glRowName {
  font-size: 13px; font-weight: 800; color: #F5F7FF;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.glRowScore { font-size: 10px; color: #A99BFF; flex-shrink: 0; }
.glRowBottom { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.glRowTags {
  font-size: 11px; color: #8D94AE;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.glRowUse { font-size: 10px; color: #596179; flex-shrink: 0; }
.glListEmpty {
  display: flex; flex-direction: column; align-items: center; gap: 8px;
  padding: 40px 12px; color: #8D94AE; text-align: center;
}
.glListEmpty p { margin: 0; font-size: 14px; }
.glListEmpty span { font-size: 12px; color: #596179; }
```

- [ ] **Step 5: 验证 typecheck + build + 视觉**

Run: `pnpm --filter @geo/web typecheck` → 通过。
Run: `pnpm --filter @geo/web build` → 通过。
视觉：对照 `mcp__pencil__get_screenshot(nodeId:"mcJoY")`（左检索面板）。核对分组切换主推高亮紫、列表行选中态、图标 38、评分/标签/用量排布。切主推/陪衬能过滤、点行能切选中。

- [ ] **Step 6: Commit**

```bash
git add web/src/features/game-library/GameGroupSwitch.tsx web/src/features/game-library/GameList.tsx web/src/features/game-library/GameLibraryWorkspace.tsx web/src/styles.css
git commit -m "feat(game-library): v2 左检索面板（主推/陪衬切换 + 游戏列表）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: 游戏详情卡

**Files:**
- Create: `web/src/features/game-library/GameDetailCard.tsx`
- Modify: `web/src/features/game-library/GameLibraryWorkspace.tsx`（`.glRight` 挂详情卡）
- Modify: `web/src/styles.css`（追加详情卡 CSS）

**Interfaces:**
- Consumes: 容器 `detail:GameDetail|null`；类型 `GameDetail`（`../../types`）。
- Produces: `GameDetailCard` props：`{ detail:GameDetail }`（仅在 detail 非空时渲染）。内部纯展示，无回调。

- [ ] **Step 1: 写 `GameDetailCard.tsx`**

```tsx
import { BookmarkCheck, PenLine } from "lucide-react";
import type { GameDetail } from "../../types";

function fmtDateTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString("zh-CN");
}
function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleDateString("zh-CN");
}
function fmtCount(n: number | null): string {
  if (n == null) return "—";
  if (n >= 10000) return `${(n / 10000).toFixed(n % 10000 === 0 ? 0 : 1)}万+`;
  return n.toLocaleString("zh-CN");
}

export function GameDetailCard({ detail: d }: { detail: GameDetail }) {
  const sourceNames = [...new Set(d.sources.map((s) => s.source).filter(Boolean))].join(" · ");
  const tiles: { label: string; value: string; sub: string }[] = [
    { label: "入库来源", value: sourceNames || "—", sub: "跨源合并" },
    { label: "支持平台", value: d.platforms.join(" / ") || "—", sub: "平台覆盖" },
    { label: "引用次数", value: String(d.use_count), sub: "取材回写" },
    { label: "素材栏目", value: d.stock_category_id ? `#${d.stock_category_id}` : "—", sub: "截图归档" },
    { label: "最近校验", value: fmtDateTime(d.last_verified_at), sub: "新鲜度" },
  ];
  const sourceGameIds =
    d.sources.map((s) => `${s.source ?? "—"}/${s.source_game_id ?? "—"}`).join(" · ") || "—";
  return (
    <div className="glCard">
      <div className="glCardTop">
        <div className="glCardId">
          {d.icon_url ? (
            <img className="glCardIcon" src={d.icon_url} alt="" />
          ) : (
            <div className="glCardIcon glRowIconFallback">{d.name.slice(0, 1)}</div>
          )}
          <div className="glCardIdText">
            <div className="glCardNameRow">
              <h2>{d.name}</h2>
              {!d.is_active && <span className="glBadgeInactive">已下架</span>}
            </div>
            <div className="glCardTags">
              {d.tags.map((t) => (
                <span key={t} className="glChip">{t}</span>
              ))}
            </div>
          </div>
        </div>
        <div className="glCardScore">
          <span className="glBigScore mono">{d.score != null ? d.score.toFixed(1) : "—"}</span>
          <span className="glScoreCap">综合评分 · score</span>
          <div className="glScoreMetrics mono">
            <span>评论 {fmtCount(d.comment_count)}</span>
            <span>平台 {d.platforms.length}</span>
          </div>
        </div>
      </div>

      <div className="glStatusBar">
        <span className="glStatusLeft">
          <PenLine size={15} />
          生文取材详情：主游戏候选 · 可绑定截图素材 · 保存文章后回写 use_count
        </span>
        <span className="glStatusRight mono">selected_games.game_id = {d.game_id}</span>
      </div>

      <div className="glMetricRow">
        {tiles.map((t) => (
          <div key={t.label} className="glMetricTile">
            <span className="glMetricLabel">{t.label}</span>
            <span className="glMetricValue" title={t.value}>{t.value}</span>
            <span className="glMetricSub">{t.sub}</span>
          </div>
        ))}
      </div>

      {d.description && <p className="glDesc">{d.description}</p>}

      <div className="glCardBottom">
        <div className="glCardBottomLeft">
          <span className="glPill"><BookmarkCheck size={14} /> 被引用 {d.use_count} 次</span>
          <span className="mono glMeta">last_used_at：{fmtDateTime(d.last_used_at)}</span>
          <span className="mono glMeta">first_seen_at：{fmtDate(d.first_seen_at)}</span>
        </div>
        <span className="mono glMeta">source_game_id: {sourceGameIds}</span>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: 容器 `.glRight` 挂详情卡**

`GameLibraryWorkspace.tsx` 补 import：
```tsx
import { GameDetailCard } from "./GameDetailCard";
```
在 `.glRight` 内的占位注释 `{/* Task 4/5 */}` 处加：
```tsx
          {detail && <GameDetailCard detail={detail} />}
```

- [ ] **Step 3: 追加详情卡 CSS（styles.css）**

```css
.glCard {
  display: flex; flex-direction: column; gap: 18px;
  padding: 22px; border-radius: 10px;
  background: #151827; border: 1px solid #2A2E45;
}
.glCardTop { display: flex; justify-content: space-between; gap: 16px; }
.glCardId { display: flex; gap: 16px; min-width: 0; }
.glCardIcon {
  width: 64px; height: 64px; flex-shrink: 0; border-radius: 12px;
  object-fit: cover; border: 1px solid #424864; background: #252A3E; font-size: 24px;
}
.glCardIdText { display: flex; flex-direction: column; gap: 9px; min-width: 0; }
.glCardNameRow { display: flex; align-items: center; gap: 8px; }
.glCardNameRow h2 { margin: 0; font-size: 22px; font-weight: 800; color: #F5F7FF; }
.glBadgeInactive {
  padding: 2px 8px; border-radius: 6px; font-size: 11px;
  background: #3A1C2A; border: 1px solid #7F2D46; color: #F87171;
}
.glCardTags { display: flex; flex-wrap: wrap; gap: 6px; }
.glChip {
  padding: 3px 10px; border-radius: 6px; font-size: 11px;
  background: #222638; color: #B9C0D8;
}
.glCardScore { display: flex; flex-direction: column; align-items: flex-end; gap: 6px; flex-shrink: 0; }
.glBigScore { font-size: 64px; font-weight: 700; line-height: 1; color: #A99BFF; }
.glScoreCap { font-size: 12px; color: #8D94AE; }
.glScoreMetrics { display: flex; gap: 14px; font-size: 11px; color: #8D94AE; }
.glStatusBar {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  height: 38px; padding: 0 12px; border-radius: 8px;
  background: #11172A; border: 1px solid #303854;
}
.glStatusLeft { display: flex; align-items: center; gap: 8px; font-size: 12px; font-weight: 700; color: #D9D4FF; }
.glStatusLeft svg { color: #A99BFF; flex-shrink: 0; }
.glStatusRight { font-size: 11px; color: #596179; }
.glMetricRow { display: flex; gap: 10px; }
.glMetricTile {
  flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 8px;
  padding: 12px; border-radius: 8px;
  background: #191D2D; border: 1px solid #2A2E45;
}
.glMetricLabel { font-size: 12px; font-weight: 700; color: #AEB7D4; }
.glMetricValue {
  font-size: 16px; font-weight: 800; color: #F5F7FF;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.glMetricSub { font-size: 11px; color: #596179; }
.glDesc { margin: 0; font-size: 14px; line-height: 1.5; color: #C3CAE1; }
.glCardBottom {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding-top: 14px; border-top: 1px solid #252B40;
}
.glCardBottomLeft { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
.glPill {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 4px 10px; border-radius: 6px; font-size: 12px; font-weight: 700;
  background: #201D3B; border: 1px solid #6556D9; color: #DED9FF;
}
.glMeta { font-size: 11px; color: #596179; }
```

- [ ] **Step 4: 验证 typecheck + build + 视觉**

Run: `pnpm --filter @geo/web typecheck` → 通过。
Run: `pnpm --filter @geo/web build` → 通过。
视觉：对照 `mcp__pencil__get_screenshot(nodeId:"oHrzJ")`（游戏详情卡）。核对 64px 大评分、状态条紫调、5 指标块、描述、底部元信息（被引用 pill / last_used / first_seen / source_game_id）。

- [ ] **Step 5: Commit**

```bash
git add web/src/features/game-library/GameDetailCard.tsx web/src/features/game-library/GameLibraryWorkspace.tsx web/src/styles.css
git commit -m "feat(game-library): v2 游戏详情卡（评分/指标/状态条/元信息）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: 素材区（截图素材 + 上传 + 灯箱）+ 来源表

**Files:**
- Create: `web/src/features/game-library/GameMaterialPanel.tsx`
- Create: `web/src/features/game-library/GameSourceTable.tsx`
- Modify: `web/src/features/game-library/GameLibraryWorkspace.tsx`（`.glRight` 挂素材行）
- Modify: `web/src/styles.css`（追加素材/来源表 CSS）

**Interfaces:**
- Consumes: `listImages`/`uploadImage`/`updateImage`/`deleteImage`（`../../api/image-library`）、`useToast`；类型 `StockImage`/`GameDetail`（`../../types`）。
- Produces:
  - `GameMaterialPanel` props：`{ categoryId:number|null; screenshotUrlCount:number }`（自管素材 state，按 categoryId 拉 `listImages`）。
  - `GameSourceTable` props：`{ detail:GameDetail }`。

- [ ] **Step 1: 写 `GameSourceTable.tsx`**

```tsx
import type { GameDetail } from "../../types";

export function GameSourceTable({ detail: d }: { detail: GameDetail }) {
  const sourceNames = [...new Set(d.sources.map((s) => s.source).filter(Boolean))].join(", ");
  const rows: { k: string; v: string }[] = [
    { k: "source", v: sourceNames || "—" },
    { k: "name_normalized", v: d.name_normalized },
    { k: "game_tags", v: d.tags.join(" / ") || "—" },
    { k: "screenshot_urls", v: `${d.screenshot_urls.length} 条，已去重` },
    { k: "last_used_article_id", v: d.last_used_article_id ? `#${d.last_used_article_id}` : "—" },
  ];
  return (
    <div className="glSourceTable">
      <div className="glSourceHead">入库来源</div>
      {rows.map((r) => (
        <div key={r.k} className="glSourceRow">
          <span className="glSourceKey mono">{r.k}</span>
          <span className="glSourceVal" title={r.v}>{r.v}</span>
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 2: 写 `GameMaterialPanel.tsx`（素材网格 + 上传 + 删除 + 灯箱）**

```tsx
import { useEffect, useRef, useState } from "react";
import { ImageIcon, Trash2, Upload, X } from "lucide-react";
import { deleteImage, listImages, uploadImage } from "../../api/image-library";
import type { StockImage } from "../../types";
import { useToast } from "../../components/Toast";

export function GameMaterialPanel({
  categoryId,
  screenshotUrlCount,
}: {
  categoryId: number | null;
  screenshotUrlCount: number;
}) {
  const { toast } = useToast();
  const [images, setImages] = useState<StockImage[]>([]);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [lightbox, setLightbox] = useState<StockImage | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const seqRef = useRef(0);

  useEffect(() => {
    if (categoryId == null) {
      setImages([]);
      return;
    }
    const seq = ++seqRef.current;
    setLoading(true);
    listImages({ category_id: categoryId })
      .then((imgs) => {
        if (seq === seqRef.current) setImages(imgs);
      })
      .catch(() => {
        if (seq === seqRef.current) toast("加载素材失败", "error");
      })
      .finally(() => {
        if (seq === seqRef.current) setLoading(false);
      });
  }, [categoryId]); // eslint-disable-line react-hooks/exhaustive-deps

  async function onPick(files: FileList | null) {
    if (!files || files.length === 0 || categoryId == null) return;
    setUploading(true);
    let ok = 0;
    for (let i = 0; i < files.length; i++) {
      try {
        const img = await uploadImage({ category_id: categoryId, file: files[i] });
        setImages((prev) => [img, ...prev]);
        ok++;
      } catch {
        toast(`第 ${i + 1} 张上传失败`, "error");
      }
    }
    setUploading(false);
    if (fileRef.current) fileRef.current.value = "";
    toast(`上传完成：${ok}/${files.length} 张`, ok === files.length ? "success" : "error");
  }

  async function onDelete(img: StockImage) {
    if (!window.confirm(`删除素材「${img.filename}」？`)) return;
    try {
      await deleteImage(img.id);
      setImages((prev) => prev.filter((i) => i.id !== img.id));
      toast("已删除", "success");
    } catch (e) {
      toast(e instanceof Error ? e.message : "删除失败", "error");
    }
  }

  const count = categoryId == null ? screenshotUrlCount : images.length;

  return (
    <div className="glShots">
      <div className="glShotsHead">
        <div className="glShotsTitle">
          <span>游戏素材</span>
          <span className="glShotsBadge">{count}</span>
        </div>
        <button
          type="button"
          className="glUploadBtn"
          disabled={categoryId == null || uploading}
          onClick={() => fileRef.current?.click()}
        >
          <Upload size={15} /> {uploading ? "上传中…" : "上传图片"}
        </button>
        <input
          ref={fileRef}
          type="file"
          multiple
          accept="image/jpeg,image/png,image/webp,image/gif"
          style={{ display: "none" }}
          onChange={(e) => void onPick(e.target.files)}
        />
      </div>

      {categoryId == null ? (
        <div className="glShotsEmpty">
          <ImageIcon size={26} strokeWidth={1.3} />
          <span>该游戏暂无素材桶（stock_category_id 为空）</span>
        </div>
      ) : loading ? (
        <div className="glShotsEmpty"><span>加载素材中…</span></div>
      ) : images.length === 0 ? (
        <div className="glShotsEmpty">
          <ImageIcon size={26} strokeWidth={1.3} />
          <span>暂无截图素材，点「上传图片」添加</span>
        </div>
      ) : (
        <div className="glShotsGrid">
          {images.map((img) => (
            <div key={img.id} className="glShotCard">
              <div className="glShotThumb" onClick={() => setLightbox(img)}>
                <img src={img.url} alt={img.filename} loading="lazy" />
                <button
                  type="button"
                  className="glShotDel"
                  title="删除"
                  onClick={(e) => { e.stopPropagation(); void onDelete(img); }}
                >
                  <Trash2 size={13} />
                </button>
              </div>
              <span className="glShotName mono" title={img.filename}>{img.filename}</span>
            </div>
          ))}
        </div>
      )}

      {lightbox && (
        <div className="glLightbox" onClick={() => setLightbox(null)}>
          <button type="button" className="glLightboxClose" onClick={() => setLightbox(null)}>
            <X size={20} />
          </button>
          <img className="glLightboxImg" src={lightbox.url} alt={lightbox.filename} onClick={(e) => e.stopPropagation()} />
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 3: 容器 `.glRight` 挂素材行（详情卡下方）**

`GameLibraryWorkspace.tsx` 补 import：
```tsx
import { GameMaterialPanel } from "./GameMaterialPanel";
import { GameSourceTable } from "./GameSourceTable";
```
在 `{detail && <GameDetailCard detail={detail} />}` 之后加：
```tsx
          {detail && (
            <div className="glMaterialRow">
              <GameMaterialPanel
                categoryId={detail.stock_category_id}
                screenshotUrlCount={detail.screenshot_urls.length}
              />
              <GameSourceTable detail={detail} />
            </div>
          )}
```

- [ ] **Step 4: 追加素材/来源表 CSS（styles.css）**

```css
.glMaterialRow { display: flex; gap: 18px; align-items: stretch; }
.glShots {
  flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 14px;
  padding: 18px; border-radius: 10px; background: #151827; border: 1px solid #2A2E45;
}
.glShotsHead { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.glShotsTitle { display: flex; align-items: center; gap: 8px; font-size: 15px; font-weight: 800; color: #F5F7FF; }
.glShotsBadge { padding: 1px 8px; border-radius: 6px; font-size: 11px; background: #222638; color: #B9C0D8; }
.glUploadBtn {
  display: inline-flex; align-items: center; gap: 8px;
  height: 38px; padding: 0 14px; border-radius: 8px; border: none;
  background: #8B5CF6; color: #F5F7FF; font-size: 13px; font-weight: 700; cursor: pointer;
}
.glUploadBtn:disabled { opacity: 0.5; cursor: not-allowed; }
.glShotsGrid { display: flex; flex-wrap: wrap; gap: 12px; }
.glShotCard {
  display: flex; flex-direction: column; gap: 8px;
  width: 148px; padding: 8px; border-radius: 8px;
  background: #191D2D; border: 1px solid #2A2E45;
}
.glShotThumb { position: relative; height: 70px; border-radius: 6px; overflow: hidden; cursor: pointer; }
.glShotThumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
.glShotDel {
  position: absolute; top: 4px; right: 4px;
  display: flex; align-items: center; justify-content: center;
  width: 22px; height: 22px; border-radius: 6px; border: none;
  background: rgba(10,12,20,0.7); color: #F87171; cursor: pointer;
}
.glShotName {
  font-size: 10px; color: #8D94AE;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.glShotsEmpty {
  display: flex; flex-direction: column; align-items: center; gap: 8px;
  padding: 30px; color: #8D94AE; text-align: center; font-size: 12px;
}
.glSourceTable {
  width: 420px; flex-shrink: 0; display: flex; flex-direction: column;
  border-radius: 10px; overflow: hidden; background: #151827; border: 1px solid #2A2E45;
}
.glSourceHead { height: 44px; display: flex; align-items: center; padding: 0 14px; background: #1A1E2E; font-size: 14px; font-weight: 800; color: #F5F7FF; }
.glSourceRow { display: flex; align-items: center; min-height: 38px; padding: 8px 14px; gap: 10px; border-top: 1px solid #252B40; }
.glSourceKey { width: 145px; flex-shrink: 0; font-size: 11px; color: #596179; }
.glSourceVal { flex: 1; min-width: 0; font-size: 12px; color: #CDD4EE; word-break: break-all; }
.glLightbox {
  position: fixed; inset: 0; z-index: 1000;
  display: flex; align-items: center; justify-content: center;
  background: rgba(6,8,16,0.86); padding: 40px;
}
.glLightboxImg { max-width: 90vw; max-height: 90vh; border-radius: 8px; }
.glLightboxClose {
  position: absolute; top: 24px; right: 24px;
  display: flex; align-items: center; justify-content: center;
  width: 40px; height: 40px; border-radius: 8px; border: none;
  background: rgba(255,255,255,0.08); color: #F5F7FF; cursor: pointer;
}
```

- [ ] **Step 5: 验证 typecheck + build + 视觉 + 接线**

Run: `pnpm --filter @geo/web typecheck` → 通过。
Run: `pnpm --filter @geo/web build` → 通过。
视觉：对照 `mcp__pencil__get_screenshot(nodeId:"AESdf")`（素材与来源）。核对缩略图网格 148 卡、上传紫钮、来源 key-value 表 420 宽。
接线（dev server）：选一款有桶的游戏 → 素材网格出图；点上传选一张图 → 网格头部出现；删一张 → 消失；点缩略图 → 灯箱；无桶的游戏 → 素材空态 + 上传禁用。

- [ ] **Step 6: Commit**

```bash
git add web/src/features/game-library/GameMaterialPanel.tsx web/src/features/game-library/GameSourceTable.tsx web/src/features/game-library/GameLibraryWorkspace.tsx web/src/styles.css
git commit -m "feat(game-library): v2 素材区（上传/删除/灯箱复用 image-lib）+ 来源表

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: 清理旧 CSS + 全页视觉终检

**Files:**
- Modify: `web/src/styles.css`（删旧 `gameLib*`/`gameRow*`/`gameCard*`/… 死规则）
- Verify: 全部 game-library 组件

**Interfaces:**
- Consumes: 无。
- Produces: 无死 CSS；全页对齐 demo。

- [ ] **Step 1: 找出旧游戏库 CSS 死规则范围**

Run: `grep -n "\.gameLib\|\.gameRow\|\.gameCard\|\.gameChip\|\.gameShot\|\.gameSource\|\.gameMetric\|\.gameStatus\|\.gameDesc\|\.gamePill\|\.gameBadge\|\.gameBig\|\.gameScore\|\.gameSection\|\.gameMaterialRow" web/src/styles.css`
Expected: 列出旧游戏库工作区遗留的选择器行号（约 54 处）。**不要动 `.mono`（全局共享）。**

- [ ] **Step 2: 删除这些旧规则块**

用 Grep 定位到旧规则的连续区块（原 `GameLibraryWorkspace` 用的 `gameLib*` 命名整段），整段删除。确认删除后：
Run: `grep -c "\.gameLib\|\.gameRow\b\|\.gameCard" web/src/styles.css`
Expected: `0`（新组件全用 `gl*`，无残留引用）。

- [ ] **Step 3: 确认旧 ImageLibraryWorkspace 已无引用**

Run: `grep -rn "ImageLibraryWorkspace" web/src`
Expected: 只在 `web/src/features/image-library/ImageLibraryWorkspace.tsx` 自身定义处出现（休眠），routes/App/types 均无引用。

- [ ] **Step 4: 全量 typecheck + build**

Run: `pnpm --filter @geo/web typecheck` → 通过。
Run: `pnpm --filter @geo/web build` → 通过。

- [ ] **Step 5: 全页视觉终检**

起 `pnpm --filter @geo/web dev`，浏览 `/image-library`，与 `mcp__pencil__get_screenshot(nodeId:"FdaLS")`（整块主内容）逐区对照：头部 / 分组切换 / 列表 / 详情卡 / 素材区 / 来源表。确认 `/game-library` 会重定向到 `/image-library`，侧栏只剩一个「游戏库」入口。逐条勾核视觉差异并微调（间距/色值以 demo 为准）。

- [ ] **Step 6: Commit**

```bash
git add web/src/styles.css
git commit -m "chore(game-library): 清理旧 gameLib* 死样式、全页视觉终检

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review 记录（写计划后自查）

- **Spec 覆盖**：① 路由归并→Task 1；② 页面头部→Task 2；③ 主推/陪衬分组（`game→category→kind` 交叉引用）→Task 2 数据层 + Task 3 UI；④ 富详情卡→Task 4；⑤ 素材区并入 image-lib 能力→Task 5；⑥ 来源表→Task 5；⑦ 隐藏无后端动作→Task 2 TODO 注释；⑧ 旧样式清理 + 休眠旧图片库→Task 6。全覆盖。
- **占位符扫描**：无「TBD/稍后实现」；每步给了完整 JSX/CSS；视觉微调是明确的「对照 demo 截图」验证步，非占位。
- **类型一致性**：`Seg`/`SortKey` 在 Task 2 容器定义并导出，Task 3 子组件 import 使用；子组件 props 类型与容器下发字段逐一对齐（`GameListItem`/`GameDetail`/`StockImage` 均来自全局 `../../types`）；`listImages`/`uploadImage`/`deleteImage`/`listCategories`/`listGames`/`getGame` 签名与 §API 客户端一致。
- **已知取舍**：孤儿栏目不在本页可见（spec 已记）；旧 `ImageLibraryWorkspace` 休眠不删。
