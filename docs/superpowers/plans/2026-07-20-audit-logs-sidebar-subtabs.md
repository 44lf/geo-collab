# 审计日志改侧边栏子 tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把「审计日志」顶级 tab 更名为「日志中心」，其两个子 tab（审计日志 / 打点日志）从页内按钮切换改为与「内容管理」一致的侧边栏可展开子项 + URL 驱动。

**Architecture:** 抽出本地 `NavGroup` 组件，让 `navItems.map`（内容/提示词）与硬编码的 admin `audit-logs` 项共用同一段父+子渲染；新增 `AuditLogsRoute` 用 `/audit-logs/:tab` 驱动受控化后的 `AuditLogsWorkspace`；移动端保留页内 `.reviewTabs` 标签条。

**Tech Stack:** React 19 + Vite + TypeScript(strict) + react-router-dom + lucide-react。

## Global Constraints

- 前端**无单测框架**（无 vitest/jest）——验证门禁是 `pnpm --filter @geo/web typecheck` + `pnpm --filter @geo/web build`；每个 Task 末尾跑 typecheck，最后一个 Task 跑 build。
- Vite dev server 必须跑 **5173** 端口（CORS 只放行 5173）。
- 子 tab 的 URL value：审计日志=`audit`，打点日志=`events`。
- 父项文案：**「日志中心」**；图标 `ScrollText`（沿用）。
- admin 门禁不变：`RequireAdmin` 路由守卫 + 侧栏 `user.role === "admin"` 条件保留。
- 不改动后端、两个子面板（`AuditLogPanel` / `ReportEventPanel`）内部逻辑、侧栏「用户管理」「AI 模型」两项。
- 遵循现有代码风格（中文注释、`className` 复用现有 CSS 类）。
- 含中文的新文件若为 `.ps1` 需 UTF-8 BOM（本计划不涉及 .ps1）。

---

### Task 1: `types.ts` 新增审计日志子项常量

**Files:**
- Modify: `web/src/types.ts`（在 `navItems` 定义之后、`NavChild` 类型已存在处附近追加导出常量）

**Interfaces:**
- Produces: `export const AUDIT_LOG_CHILDREN: NavChild[]` — 值为
  `[{ key: "audit-logs:audit", label: "审计日志", value: "audit" }, { key: "audit-logs:events", label: "打点日志", value: "events" }]`

- [ ] **Step 1: 追加常量**

在 `web/src/types.ts` 中 `navItems` 常量定义结束（`];` 之后）追加：

```ts
// 「日志中心」（audit-logs）的子项。audit-logs 是 admin-only 且在 App.tsx 中
// 独立于 navItems 硬编码渲染，故其子项单列于此，供侧栏 NavGroup 复用。
export const AUDIT_LOG_CHILDREN: NavChild[] = [
  { key: "audit-logs:audit", label: "审计日志", value: "audit" },
  { key: "audit-logs:events", label: "打点日志", value: "events" },
];
```

- [ ] **Step 2: typecheck**

Run: `pnpm --filter @geo/web typecheck`
Expected: PASS（`NavChild` 类型已在 types.ts 定义，无新报错）

- [ ] **Step 3: Commit**

```bash
git add web/src/types.ts
git commit -m "feat(nav): 新增日志中心子项常量 AUDIT_LOG_CHILDREN"
```

---

### Task 2: `App.tsx` 抽出 `NavGroup` 组件并接入 audit-logs

**Files:**
- Modify: `web/src/App.tsx`

**Interfaces:**
- Consumes: `AUDIT_LOG_CHILDREN`（Task 1）、`NavChild`、`NavKey`（types.ts）
- Produces: 本地组件 `NavGroup`（不导出，仅 App.tsx 内用）：

```ts
function NavGroup(props: {
  navKey: NavKey;
  label: string;
  icon: ComponentType<{ size?: number }>;
  children: NavChild[];
  activeNav: NavKey;
  isOpen: boolean;
  childValue: string;
  onParentClick: () => void;
  onToggle: () => void;
  onSelectChild: (value: string) => void;
}): JSX.Element
```

- [ ] **Step 1: 导入补充**

在 `App.tsx` 顶部 import 增补：`ScrollText` 已从 lucide-react 导入（保留）；从 `./types` 增补导入 `AUDIT_LOG_CHILDREN`，并确保 `NavChild` 类型可用。`ComponentType` 从 `react` 导入。

```ts
import { Suspense, useState, type ComponentType } from "react";
// ...
import { navItems, AUDIT_LOG_CHILDREN } from "./types";
import type { NavKey, NavChild } from "./types";
```

- [ ] **Step 2: 定义 `NavGroup` 组件**

在 `App.tsx` 内（`RootLayout` 之外，文件下方或 `TabFallback` 附近）新增。整段渲染直接搬自现有 `navItems.map` 中「有 children」分支的 JSX（父按钮 + `.navSub/.navChildren/.navChildrenInner/.navChild`），把其中依赖的变量改为 props：

```tsx
function NavGroup({
  navKey, label, icon: Icon, children, activeNav, isOpen, childValue,
  onParentClick, onToggle, onSelectChild,
}: {
  navKey: NavKey;
  label: string;
  icon: ComponentType<{ size?: number }>;
  children: NavChild[];
  activeNav: NavKey;
  isOpen: boolean;
  childValue: string;
  onParentClick: () => void;
  onToggle: () => void;
  onSelectChild: (value: string) => void;
}) {
  return (
    <div className="navGroup">
      <button
        className={`navItem navParent ${activeNav === navKey ? "active" : ""}`}
        type="button"
        onClick={() => {
          if (activeNav === navKey) onToggle();
          else onParentClick();
        }}
      >
        <Icon size={17} />
        <span>{label}</span>
        <ChevronDown size={15} className={`navChevron${isOpen ? " open" : ""}`} />
      </button>
      <div className={`navSub ${isOpen ? "open" : ""}`}>
        <div className="navChildren">
          <div className="navChildrenInner">
            {children.map((child) => {
              const childActive = activeNav === navKey && childValue === child.value;
              return (
                <button
                  className={`navChild ${childActive ? "active" : ""}`}
                  key={child.key}
                  type="button"
                  onClick={() => onSelectChild(child.value)}
                >
                  {child.label}
                </button>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: `navItems.map` 的 children 分支改用 `NavGroup`**

把现有 `navItems.map` 里 `if (item.children && item.children.length > 0) { ... return (<div className="navGroup">...</div>) }` 整段替换为：

```tsx
if (item.children && item.children.length > 0) {
  return (
    <NavGroup
      key={item.key}
      navKey={item.key}
      label={item.label}
      icon={Icon}
      children={item.children}
      activeNav={activeNav}
      isOpen={openGroup === item.key}
      childValue={childValueFor(item.key)}
      onParentClick={() => { go(item.key); setOpenGroup(item.key); }}
      onToggle={() => toggleGroup(item.key)}
      onSelectChild={(value) => selectChild(item.key, value)}
    />
  );
}
```

> 说明：原 `onClick` 逻辑「若已激活则 toggle，否则 go+setOpenGroup」被拆到 `NavGroup` 内（`onToggle` vs `onParentClick`），行为等价。

- [ ] **Step 4: 硬编码 admin `audit-logs` 按钮改用 `NavGroup`**

把 `App.tsx` 中现有的 audit-logs admin 按钮块：

```tsx
{user.role === "admin" && (
  <button
    className={`navItem ${activeNav === "audit-logs" ? "active" : ""}`}
    type="button"
    onClick={() => go("audit-logs")}
  >
    <ScrollText size={17} />
    <span>审计日志</span>
    <span className="navDot" />
  </button>
)}
```

替换为：

```tsx
{user.role === "admin" && (
  <NavGroup
    navKey="audit-logs"
    label="日志中心"
    icon={ScrollText}
    children={AUDIT_LOG_CHILDREN}
    activeNav={activeNav}
    isOpen={openGroup === "audit-logs"}
    childValue={childValueFor("audit-logs")}
    onParentClick={() => { go("audit-logs"); setOpenGroup("audit-logs"); }}
    onToggle={() => toggleGroup("audit-logs")}
    onSelectChild={(value) => selectChild("audit-logs", value)}
  />
)}
```

（「用户管理」`admin` 与「AI 模型」`ai-models` 两个按钮保持不动。）

- [ ] **Step 5: `childValueFor` 识别 audit-logs**

把现有 `childValueFor` 扩展一行：

```tsx
function childValueFor(parentKey: NavKey): string {
  if (parentKey !== activeNav) return "";
  const seg = subSegment(location.pathname);
  if (parentKey === "content") return seg || "pending";
  if (parentKey === "prompts") return seg || "generation";
  if (parentKey === "audit-logs") return seg || "audit";
  return "";
}
```

- [ ] **Step 6: `openGroup` 初始化识别 audit-logs**

把 `openGroup` 的 `useState` 初始化，从「仅看 navItems」扩展到「含 audit-logs」：

```tsx
const [openGroup, setOpenGroup] = useState<NavKey | null>(() => {
  const k = pathToNavKey(location.pathname);
  const inNavItems = navItems.some((i) => i.key === k && (i.children?.length ?? 0) > 0);
  if (inNavItems || k === "audit-logs") return k;
  return null;
});
```

- [ ] **Step 7: typecheck**

Run: `pnpm --filter @geo/web typecheck`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add web/src/App.tsx
git commit -m "feat(nav): 抽出 NavGroup 组件，日志中心用侧栏子项渲染"
```

---

### Task 3: `AuditLogsWorkspace` 受控化 + 移动端标签条

**Files:**
- Modify: `web/src/features/system/AuditLogsWorkspace.tsx`

**Interfaces:**
- Produces: `export function AuditLogsWorkspace(props: { tab: "audit" | "events"; onTabChange: (t: "audit" | "events") => void; isMobile?: boolean }): JSX.Element`
- Consumes: 已有 `AuditLogPanel`（本文件）、`ReportEventPanel`（`./ReportEventsWorkspace`）

- [ ] **Step 1: 替换组件签名与实现**

把现有 `AuditLogsWorkspace`（含 `useState<subTab>` 与顶部两按钮）替换为受控版。桌面端不渲染切换条；移动端渲染 `.reviewTabs` 标签条：

```tsx
export function AuditLogsWorkspace({
  tab,
  onTabChange,
  isMobile,
}: {
  tab: "audit" | "events";
  onTabChange: (t: "audit" | "events") => void;
  isMobile?: boolean;
}) {
  return (
    <>
      {isMobile && (
        <div className="reviewTabs">
          <button
            type="button"
            className={`reviewTabBtn ${tab === "audit" ? "active" : ""}`}
            onClick={() => onTabChange("audit")}
          >
            审计日志
          </button>
          <button
            type="button"
            className={`reviewTabBtn ${tab === "events" ? "active" : ""}`}
            onClick={() => onTabChange("events")}
          >
            打点日志
          </button>
        </div>
      )}
      {tab === "audit" ? <AuditLogPanel /> : <ReportEventPanel />}
    </>
  );
}
```

> 保留文件内 `AuditLogPanel` 及其所有辅助函数/样式常量不变；仅替换顶层 `AuditLogsWorkspace`。删除原先只为切换条使用的 `useState` import 若不再被其它代码引用（`AuditLogPanel` 仍用 `useState`，故 `useState` import 保留）。

- [ ] **Step 2: typecheck**

Run: `pnpm --filter @geo/web typecheck`
Expected: FAIL — `routes.tsx` 仍以无参形式使用 `<AuditLogsWorkspace />`，报缺少 `tab`/`onTabChange` props。此失败预期，由 Task 4 修复。

> 若希望本 Task 独立通过，可先跳至 Task 4 再回来跑；建议按顺序执行，在 Task 4 后统一 typecheck。

- [ ] **Step 3: Commit**

```bash
git add web/src/features/system/AuditLogsWorkspace.tsx
git commit -m "feat(audit-logs): AuditLogsWorkspace 受控化 + 移动端标签条"
```

---

### Task 4: `routes.tsx` 新增 `AuditLogsRoute` 与 `/audit-logs/:tab`

**Files:**
- Modify: `web/src/routes.tsx`

**Interfaces:**
- Consumes: `AuditLogsWorkspace`（Task 3 的受控签名）、`RequireAdmin`（本文件已有）、`useIsMobile`（已导入）
- Produces: `function AuditLogsRoute(): JSX.Element`

- [ ] **Step 1: 新增 `AuditLogsRoute` 组件**

在 `routes.tsx` 中（`PromptsRoute` 附近）新增，仿 `ContentRoute` 模式：

```tsx
// 「日志中心」子页（审计日志 / 打点日志）由 URL 段驱动：/audit-logs/:tab。
function AuditLogsRoute() {
  const { tab } = useParams();
  const navigate = useNavigate();
  const isMobile = useIsMobile();
  const active: "audit" | "events" = tab === "events" ? "events" : "audit";
  return (
    <AuditLogsWorkspace
      tab={active}
      isMobile={isMobile}
      onTabChange={(t) => navigate(`/audit-logs/${t}`)}
    />
  );
}
```

- [ ] **Step 2: 路由表接线**

把现有 audit-logs 路由项：

```tsx
{
  path: "audit-logs",
  element: (
    <RequireAdmin>
      <AuditLogsWorkspace />
    </RequireAdmin>
  ),
},
```

替换为两条（`/audit-logs` 默认 audit，不重定向；`/audit-logs/:tab`）：

```tsx
{
  path: "audit-logs",
  element: (
    <RequireAdmin>
      <AuditLogsRoute />
    </RequireAdmin>
  ),
},
{
  path: "audit-logs/:tab",
  element: (
    <RequireAdmin>
      <AuditLogsRoute />
    </RequireAdmin>
  ),
},
```

- [ ] **Step 3: typecheck**

Run: `pnpm --filter @geo/web typecheck`
Expected: PASS（Task 3 的失败此处修复；`AuditLogsWorkspace` 现以受控 props 调用）

- [ ] **Step 4: Commit**

```bash
git add web/src/routes.tsx
git commit -m "feat(audit-logs): 新增 AuditLogsRoute + /audit-logs/:tab 路由"
```

---

### Task 5: `MobileMorePage` label 改「日志中心」

**Files:**
- Modify: `web/src/components/MobileMorePage.tsx`

**Interfaces:**
- 无新接口；仅改「管理」分组中 audit-logs 行的 `label`。

- [ ] **Step 1: 改 label**

把 `MobileMorePage` 中：

```tsx
{ key: "audit-logs" as NavKey, label: "审计日志", icon: ScrollText },
```

改为：

```tsx
{ key: "audit-logs" as NavKey, label: "日志中心", icon: ScrollText },
```

（`key`、`icon` 不变。）

- [ ] **Step 2: typecheck**

Run: `pnpm --filter @geo/web typecheck`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add web/src/components/MobileMorePage.tsx
git commit -m "feat(audit-logs): 移动端「更多」入口改名日志中心"
```

---

### Task 6: 集成验证（build + 手动核对）

**Files:** 无代码改动（仅验证；若发现问题回到对应 Task 修复）。

- [ ] **Step 1: 前端构建**

Run: `pnpm --filter @geo/web build`
Expected: 构建成功、无 type/编译错误。

- [ ] **Step 2: 启动 dev 手动核对**

Run: `pnpm --filter @geo/web dev`（5173）。以 admin 登录，逐项核对：
- 桌面侧栏出现「日志中心」，可展开出「审计日志」「打点日志」；点击子项切换面板、子项高亮、URL 变为 `/audit-logs/audit`｜`/audit-logs/events`。
- 直接地址栏访问 `/audit-logs/events`：侧栏「日志中心」已展开且「打点日志」高亮，右侧显示打点日志面板。
- 访问 `/audit-logs`（无子段）：默认落「审计日志」面板，侧栏「审计日志」子项高亮。
- 桌面端页面顶部**不再出现**原来的 `primaryButton/secondaryButton` 切换条。
- 非 admin 登录：侧栏无「日志中心」；直接访问 `/audit-logs/events` 被 `RequireAdmin` 重定向到 `/agents`。
- 移动端（窄视口 / 移动模拟）：底栏「更多 → 日志中心」进入后，页内出现 `.reviewTabs` 标签条，可切到「打点日志」。

- [ ] **Step 3: 收尾**

无需额外 commit（前几个 Task 已分别提交）。若手动核对触发修复，改动归入对应 Task 的文件并补一次 commit。

---

## Self-Review

**Spec coverage:**
- 侧栏子项陈列（方案 B / NavGroup 复用）→ Task 2 ✅
- 父项更名「日志中心」→ Task 2（侧栏）+ Task 5（移动端）✅
- 子项 value `audit`/`events` → Task 1 常量 + Task 4 路由 ✅
- URL 驱动 `/audit-logs/:tab`、`/audit-logs` 默认 audit 不重定向 → Task 4 ✅
- `childValueFor`/`openGroup` 识别 audit-logs → Task 2 Step 5/6 ✅
- `AuditLogsWorkspace` 受控化 + 去页内按钮 → Task 3 ✅
- 移动端页内标签条 → Task 3 ✅
- `MobileMorePage` 改名 → Task 5 ✅
- admin 门禁保留（RequireAdmin + 侧栏 role 条件）→ Task 2 Step 4 / Task 4 Step 2 ✅
- 不动 admin/ai-models、后端、子面板 → 各 Task 明确保留 ✅

**Placeholder scan:** 无 TBD/TODO；所有代码步骤均给出完整代码。

**Type consistency:** `tab: "audit" | "events"`、`onTabChange`、`AUDIT_LOG_CHILDREN`（`NavChild[]`）、`NavGroup` props 在 Task 1/2/3/4 间一致；子项 value `audit`/`events` 全链路一致。

**已知的跨 Task 中间失败：** Task 3 单独 typecheck 会因 routes.tsx 尚未更新而失败，Task 4 修复——已在 Task 3 Step 2 标注为预期。
