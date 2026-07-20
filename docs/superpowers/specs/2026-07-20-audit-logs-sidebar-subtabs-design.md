# 审计日志改为侧边栏子 tab 陈列 — 设计

- 日期：2026-07-20
- 范围：前端（`web/`）纯 UI/导航结构调整，不动后端
- 目标：让「审计日志」的两个子 tab（`审计日志` / `打点日志`）与「内容管理」「提示词管理」保持一致的**侧边栏可展开子项**陈列与交互，去掉页内 `primaryButton/secondaryButton` 切换条。

## 背景 / 现状

- 「内容管理」「提示词管理」在 `types.ts:navItems` 中带 `children`，由 `App.tsx` 的 `navItems.map` 统一渲染成 `.navGroup / .navSub / .navChild`（父项点击展开、子项 URL 驱动切换，`/content/:status`、`/prompts/:scope`）。
- 「审计日志」不同：`audit-logs` **不在** `navItems` 中，而是 `App.tsx` 里与「用户管理」「AI 模型」一起、在 `navItems.map` **之后**硬编码的 admin-only 按钮；它没有 `children`。子 tab 切换由 `AuditLogsWorkspace` 内部 `useState<subTab>` + 顶部两个 `primaryButton/secondaryButton` 完成，交互与其他 tab 不一致。
- 两个子面板 `AuditLogPanel`、`ReportEventPanel` 已各自是独立组件（`ReportEventPanel` 从 `ReportEventsWorkspace.tsx` 导出），父组件只做切换。
- 复用资产已就绪：CSS `.navGroup/.navSub/.navChild`（侧栏子项）与 `.reviewTabs/.reviewTabBtn`（移动端页内标签条）均已存在。

## 决策（已与用户确认）

1. 陈列方式：**侧边栏可展开子项**，与「内容管理」完全一致；移除页内切换按钮。URL 驱动。
2. 父项更名 **「日志中心」**（避免父项与子项「审计日志」重名），子项为 `审计日志` / `打点日志`。
3. 实现路径：**抽出本地 `NavGroup` 组件**（方案 B）——`navItems.map` 与硬编码的 admin `audit-logs` 项**共用同一段父+子渲染**，audit-logs 仍留在原处、仍受 admin 门禁；不改动「用户管理」「AI 模型」两项，侧栏顺序不变。

> 放弃的备选：A 就地内联复制 navGroup JSX（产生重复代码）；C 给 navItems 加 `adminOnly` 并把三个 admin 项全搬进去（顺带改动 admin/ai-models，越出本次范围）。

## 详细设计

### 1. 导航与命名

- `audit-logs` 顶级项更名「日志中心」，图标沿用 `ScrollText`。
- 子项定义（value 即 URL 子段）：
  - `审计日志` → `audit`
  - `打点日志` → `events`
- 子项来源：在 `types.ts` 增加一个导出常量（如 `AUDIT_LOG_CHILDREN: NavChild[]`），供 `App.tsx` 渲染，避免把标签散落在组件里。

### 2. `App.tsx`：抽出 `NavGroup` 组件并复用

- 新增本地组件 `NavGroup`，封装现有 `navItems.map` 中「有 children 分支」的整段渲染（父按钮 `navParent` + `ChevronDown` + `.navSub/.navChildren/.navChild`），入参：
  - `navKey`、`label`、`icon`、`children: NavChild[]`
  - `activeNav`、`isOpen`、当前子值 `childValue`
  - 回调 `onParentClick`（= 现 `go`+`setOpenGroup` 逻辑）、`onToggle`、`onSelectChild`
- `navItems.map` 的 children 分支改为调用 `<NavGroup .../>`。
- 硬编码的 admin `audit-logs` 按钮改为：`user.role === "admin"` 时渲染 `<NavGroup navKey="audit-logs" label="日志中心" icon={ScrollText} children={AUDIT_LOG_CHILDREN} ... />`（其余两个 admin 按钮「用户管理」「AI 模型」保持原样）。
- `childValueFor` 扩展：`parentKey === "audit-logs"` 时返回 `seg || "audit"`（对齐 content 的 `seg || "pending"`）。
- `selectChild` 已通用（`navigate('/'+parent+'/'+value)`），无需为 audit-logs 特判。
- `openGroup` 初始化：现逻辑只看 `navItems`；扩展为「当前是有子项的父项（含 audit-logs）」时初始展开，保证直接打开 `/audit-logs/*` 时侧栏子项已展开。

### 3. 路由：`routes.tsx` 新增 `AuditLogsRoute`

- 仿 `ContentRoute` 新增 `AuditLogsRoute`：从 `useParams().tab` 解析，非法/缺省回落 `audit`；`useIsMobile()` 取移动端标志；把 `tab`、`onTabChange={(t)=>navigate('/audit-logs/'+t)}`、`isMobile` 传给 `AuditLogsWorkspace`，外层仍包 `RequireAdmin`。
- 路由表：`/audit-logs` 与 `/audit-logs/:tab` 均指向 `AuditLogsRoute`（`/audit-logs` 默认 audit，不做重定向，语义与 content 一致）。

### 4. `AuditLogsWorkspace` 受控化

- 删除内部 `useState<subTab>` 与顶部 `primaryButton/secondaryButton` 切换条。
- 新 props：`tab: "audit" | "events"`、`onTabChange: (t) => void`、`isMobile?: boolean`。
- 渲染：`tab === "audit" ? <AuditLogPanel/> : <ReportEventPanel/>`。
- 移动端（`isMobile`）：侧栏不可见，故在页内渲染一条 `.reviewTabs` 风格标签条（`审计日志/打点日志`，点了调 `onTabChange`），与「内容管理」移动端做法一致；桌面端不渲染此条。

### 5. `MobileMorePage`

- 「管理」分组里该行 label 由「审计日志」改为「日志中心」；`key` 仍为 `audit-logs`（导航到 `/audit-logs` → 默认 audit），图标不变。

### 不改动项

- 后端、`AuditLogPanel` / `ReportEventPanel` 的内部实现与数据请求。
- admin 门禁：`RequireAdmin` 路由守卫、侧栏 `role === "admin"` 条件均保留。
- 侧栏「用户管理」「AI 模型」两项及整体顺序。

## 影响文件

- `web/src/App.tsx` — 抽 `NavGroup`、audit-logs 用 NavGroup 渲染、`childValueFor`/`openGroup` 识别 audit-logs
- `web/src/routes.tsx` — 新增 `AuditLogsRoute` 与 `/audit-logs/:tab`
- `web/src/features/system/AuditLogsWorkspace.tsx` — 受控化 + 移动端页内标签条
- `web/src/types.ts` — 新增 `AUDIT_LOG_CHILDREN` 常量（父项更名文案）
- `web/src/components/MobileMorePage.tsx` — label 改「日志中心」

## 验证

- `pnpm --filter @geo/web typecheck` + `pnpm --filter @geo/web build`（前端 CI 门禁，无单测框架）。
- 手动核对：
  - 桌面 admin：侧栏「日志中心」可展开出「审计日志/打点日志」，点击切换、高亮、URL 变化正确；直接访问 `/audit-logs/events` 时子项已展开且高亮。
  - `/audit-logs` 默认落「审计日志」。
  - 非 admin：侧栏无「日志中心」，直接访问 `/audit-logs/*` 被 `RequireAdmin` 重定向。
  - 移动端：从「更多 → 日志中心」进入后，页内标签条可切到「打点日志」。

## 非目标 / YAGNI

- 不引入 `adminOnly` 通用机制、不迁移其他 admin 项。
- 不改子面板功能、筛选、分页、后端接口。
