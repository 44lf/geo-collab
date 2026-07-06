# 单篇文章可分享的直达链接（Article Permalink Share）设计

> 日期：2026-07-06
> 状态：待实现
> 关联：为后期「飞书指定文章的跳转审核」打基础

## 1. 背景与目标

当前「内容管理」只有一个编辑工作台，文章按用户私有（后端属主校验），没有只读浏览页，也没有任何可复制、可分享的单篇链接。运营同事无法把「某一篇具体文章」以链接形式发给别人查看。

**目标**：每篇文章拥有一个稳定、可复制的直达链接。点开链接进入「内容管理」工作台并把该文章载入编辑器查看。收到链接的人用**自己的账号**登录后即可只读查看，无需是文章作者。此链接是后续「飞书指定文章跳转审核」的锚点。

**非目标（本期明确不做）**：
- ❌ 免登录公开分享（本期是登录门控，不引入公开 token / slug）。
- ❌ 审核人跨用户「通过审核」（approve/revoke 仍限作者 / admin；飞书审核的 approve 联动留下一期）。
- ❌ 文章列表跨用户可见（列表仍按 user_id 私有过滤，只放开「读单篇」）。
- ❌ 「开启分享」开关（本期任意登录用户即可读，暂不做每篇的可见性开关）。

## 2. 核心决策（已与用户确认）

| 维度 | 决策 |
|---|---|
| 访问方式 | **登录门控**：收链接的人用自己账号登录查看，非公开链接 |
| 可见范围 | **任意登录用户可只读**查看任意单篇；编辑 / 删除 / 审核仍限作者本人（admin 例外） |
| 入口形态 | 复用现有「内容管理」编辑工作台，把文章载入编辑器；非作者进入**只读降级** |
| URL 形态 | **顶级永久链接 `/article/:id`**，与 `content/pending`、`content/approved` 完全无关，避免与审核状态 tab 混淆 |

## 3. URL 与路由

### 3.1 永久链接形态
```
https://<域名>/article/123
```
- `123` = 文章数据库 id（列表里已经在显示 "ID 123"，天然稳定锚点）。
- 打开后渲染到「内容管理」工作台（`RootLayout` 内），左侧导航高亮「内容管理」。
- **打开链接是纯查看动作**：只把文章载入右侧编辑器，不改变文章审核状态，也不影响左侧列表停在哪一栏。

### 3.2 前端路由改动（`web/src/routes.tsx`）
新增一个顶级路由（作为 `/` 的 child，和 `content` 平级），**复用现有的 `ContentRoute` 元素**（不要新建独立组件）：
```
{ path: "article/:articleId", element: <ContentRoute /> }
```
**为什么复用 `ContentRoute` 而不是新建 `ArticlePermalinkRoute`（关键，见 §10 隐患）**：`/content/*` 与 `/article/*` 在同一个 `<Outlet/>` 位置渲染。若两条路由用**同一个组件类型**（都 `<ContentRoute/>`），React 会 reconcile、**不卸载重挂** `ContentWorkspace`（编辑器 / 草稿 / `savedStateRef` 保留）；若用不同组件（`ContentRoute` vs `ArticlePermalinkRoute`），React 会**卸载重挂**，导致作者经永久链接编辑时草稿丢失 + 离开拦截失效。

`ContentRoute` 改造：
```tsx
function ContentRoute() {
  const { status, articleId } = useParams();  // 两者互斥，永久链接下 status 为空
  const navigate = useNavigate();
  const isMobile = useIsMobile();
  const reviewTab = status === "approved" ? "approved" : "pending";
  return (
    <ContentWorkspace
      isActive
      reviewTab={reviewTab}
      isMobile={isMobile}
      deepLinkArticleId={articleId ? Number(articleId) : undefined}
      onReviewTabChange={(t) => navigate(`/content/${t}`)}
    />
  );
}
```
非法 / 非数字 `articleId` → `ContentWorkspace` 内 `getArticle` 失败 toast + 回落空态（或路由层重定向 `/content`）。

### 3.3 导航高亮（`web/src/App.tsx`）
`pathToNavKey()` 当前按 URL 首段映射，`/article/...` 首段是 `article` 不在 `KNOWN_NAV` 里，会回落到 `agents`。需加一条映射：首段为 `article` 时返回 `content`，让「内容管理」正确高亮。

## 4. 后端改动（纯加法，风险面小）

### 4.1 放开「读单篇」的属主校验
`GET /api/articles/{id}`（`server/app/modules/articles/router.py:read_article`）：
- 现状：`_verify_article_ownership(get_article(db, id), current_user)` —— 非作者且非 admin 返回 404。
- 改为：仅校验存在（缺失 / 软删仍 404），**任意登录用户可读**。
- 这是**唯一**放开的隔离点。`get_article` service 本身**不带 user 过滤**（`service.py:79`，只按 id + `is_deleted`），故只删 router 层这一句校验即可，service 不动。
- **风险面比看上去更小**：MCP 侧 `mcp_get_article`（`mcp_catalog/router.py:94`）**本来就没有属主校验**——"按 id 读任意文章"在 service 层早已存在，本改动只是把 user-JWT 路径对齐它。
- **已核实无测试变红**：`server/tests/` 里对 `GET /api/articles/{id}` 断言 404 的只有「已软删」用例（`test_articles_api.py:185`、`test_delete_guards.py:118`），软删仍 404、不受影响；跨用户 404 断言全落在 **DELETE** 写路径（`test_security_boundaries.py`、`test_content_delete_permission.py`），本设计不动写路径。

### 4.2 新增 `can_edit` 字段驱动前端只读
`ArticleRead` schema（`schemas.py:120`）新增：
```python
can_edit: bool = True   # 默认 True，保证其它构造点无需改动
```
`to_article_read` 现签名是 `to_article_read(article, published_count: int = 0)`（`schemas.py:199`）。**新参数追加在 `published_count` 之后**，不要挤占位置：
```python
def to_article_read(article, published_count: int = 0, can_edit: bool = True) -> ArticleRead: ...
```
`read_article` 里计算并传入：
```python
can_edit = (article.user_id == current_user.id) or (current_user.role == "admin")
return to_article_read(article, can_edit=can_edit)
```
**已核实唯一构造点**：`ArticleRead` 仅在 `to_article_read` 内构造一次；所有调用点（`router.py` 的 create/read/update/cover/approve/revoke + `mcp_catalog/router.py:99` 的 `mcp_get_article`）均单参调用、落默认 `True`，且都在「作者本人操作」或 MCP 无 user 语境下，语义正确，**不破坏现有调用**。列表走的是 `ArticleListRead`（另一个 schema），完全不受影响。

### 4.3 写路径完全不动（双保险）
`PUT` / `DELETE` / `POST /{id}/cover` / `approve` / `revoke` 继续 `_verify_article_ownership`。即使非作者绕过前端直接调用，后端仍 404 兜底。

### 4.4 列表不动
`GET /api/articles` 仍按 `user_id` 私有过滤（admin 除外）。分享只影响「按 id 直达单篇」，不改列表可见性。

### 4.6 审计与限流（非阻断，实现时补一笔）
`read_article` 当前**不写 audit_log**（只有 create/update/delete/cover/approve/revoke 写）。做「看他人文章」而无「谁看了谁的文章」轨迹是常见合规缺口——如需可在放开读时补一条 `article.view` 审计。`read_article` 也无 `@limiter.limit`，放开后按 int id 枚举无限流。内部团队可接受，但记一笔。

### 4.5 资源（图片/封面）已天然共享 —— 无需改
`/api/assets/{id}`、`/api/assets/{id}/thumbnail` 虽要登录但**无属主校验**，任何登录用户都能按 id 取图。故非作者打开文章时正文图 / 封面照常显示，不会 403。（已核实 `router.py:read_asset_file` / `read_asset_thumbnail`。）

## 5. 前端改动（`web/src/features/content/ContentWorkspace.tsx`）

### 5.1 深链加载（含时序竞态处理）
`ContentWorkspace` 新增可选 prop `deepLinkArticleId?: number`：
- 抽出一个「按 id 载入」路径 `loadArticleById(id)`：跳过 `ArticleSummary` 预填，直接 `getArticle(id)` → `setSelectedArticle` → `setDraft` → `editor.commands.setContent(...)`。
- **必须 gate 在 editor 就绪、且只跑一次**：`useEditor` 首帧返回 `null`，若在未就绪时调 `loadArticleById` 会被 `editor?.` 静默跳过、编辑器停在空白且 `isDirty` 误判为脏。用 `useEffect(() => { if (editor && deepLinkArticleId && !loadedRef.current) { loadedRef.current = true; void loadArticleById(deepLinkArticleId); } }, [editor, deepLinkArticleId])`。
- **必须照抄 `savedStateRef` 设置顺序**（见 `loadArticle:645-654`）：先 `setContent` → 再 `editorBodyState(editor)` 算 `bodyState` → 写 `savedStateRef`。否则离开时误弹「未保存」。
- 不依赖列表里是否有这篇（跨用户文章不在列表也能开）；列表刷新只 set `articles/groups`、不动 `selectedArticle`，两者不打架。
- 加载后按文章 `review_status` 把左侧列表 tab 默认停在对应栏（体验一致，非必需）。

### 5.2 「复制链接」入口（两处）
- **列表每行**：加一个小按钮「复制链接」（照现有 `inlineMiniButton`「加入分组」样式），点击写入剪贴板 `${location.origin}/article/${article.id}` —— 满足「每篇文章都有自己可复制的链接」。
- **编辑器顶部工具栏**：有选中文章时显示「复制链接」按钮，复制当前文章的永久链接。

> 说明：列表点选文章**仍停在 `/content/...`**、不跳到 `/article/:id`。永久链接作为「入站深链 / 复制目标」即可满足分享需求，无需劫持正常编辑流的地址栏。

**`useBlocker` 谓词必须同步放开 `/article`（关键）**：现谓词 `!nextLocation.pathname.startsWith("/content")`（`ContentWorkspace.tsx:433`）假设工作台只挂在 `/content`。因 `/article/:id` 复用同一 `ContentRoute` 元素（§3.2），`ContentWorkspace` 不重挂、草稿保留，但谓词要改成「离开工作台挂载点才拦」：
```tsx
const inWorkspace = (p: string) => p.startsWith("/content") || p.startsWith("/article");
const blocker = useBlocker(({ nextLocation }) => isDirty() && !inWorkspace(nextLocation.pathname));
```
这样：作者经永久链接编辑 → 在 `/article`↔`/content` 间移动不误拦、草稿不丢；真正离开到 `/agents` 等仍正常拦截。

### 5.2b 载入新文章前的「未保存守卫」（顺手修既有 gap）
**问题**：现状下在列表里点另一篇文章时，`loadArticle`（`ContentWorkspace.tsx:618`，由 `:1142` `onSelect` 触发）**不检查 `isDirty()`** → 直接 `setContent` 冲掉当前未保存内容，无任何确认。本功能的深链加载（`loadArticleById`）与「应用内跳到另一篇 `/article/Y`」会走同一个"替换编辑器内容"动作，若不守卫会放大这个丢数据面。

**方案**：抽一个统一入口 `loadArticleGuarded(loader)`：调用前若 `isDirty()` 为真，弹确认「当前文章有未保存内容，确定切换吗？」——取消则不载入。`loadArticle`（列表点选）与 `loadArticleById`（深链）都经它。这样：
- 「复制链接」按钮只写剪贴板、**不导航**，永不触发（正常分享流零风险）。
- 整页打开 / 刷新 / 关标签由既有 `beforeunload`（`:452`）原生拦截。
- 应用内切换文章（列表点选 **或** 深链换 id）统一走确认，**连带修好现有列表点选的老 gap**。

> 注意深链首帧加载：`loadArticleById` 首次由 `/article/:id` 进入时编辑器是空的、`isDirty()` 为 false，不会弹确认；只有"已在编辑器里改了东西、再去载入另一篇"才弹。

### 5.3 只读降级（非作者）—— `setEditable(false)` 只是基线，必须逐项屏蔽
当载入文章的 `can_edit === false`：

> ⚠️ **Tiptap 的 `editor.setEditable(false)` 只阻止用户经 DOM 直接输入；`editor.chain()...run()` 编程写入仍生效。** 所以下列写入口必须**额外**显式屏蔽，缺一处就会漏：

| 写入口 | 位置 | 只读处理 |
|---|---|---|
| 编辑器本体 | `useEditor` | `editor.setEditable(false)`（基线） |
| **整条工具栏 `EditorToolbar`** | `ContentWorkspace.tsx:1399` | **隐藏/禁用整条**（所有按钮都是 programmatic `chain().run()`，editable 拦不住） |
| 粘贴图片 `handlePaste` | `:397` | 开头 `if (!editor?.isEditable) return false;` |
| 图片 resize 手柄 `startResize` | `ImageResizeView:270` | 手柄渲染加 `editor.isEditable &&` 门控 |
| 标题 / 作者 / 状态输入 | `:1328/:1332/:1336` | `disabled={!canEdit}`（受控 input，不受 editable 约束，不禁会污染 `isDirty`） |
| 封面上传 `<input type=file>` + `handleCoverUpload` | `:1352/:737` | `disabled` / 隐藏（`handleCoverUpload` 会直接写后端） |
| 顶部 保存 / 删除 / 新建 | `:1068/:1072/:1076` | 隐藏 |
| reviewStrip 通过审核/撤销/分发 | `:1370-1392` | 隐藏（现仅由 `selectedArticle` 门控、未看 can_edit） |
| 列表行 通过审核 / 加入分组 | `:1146-1165` | 只读会话可保留（作用于自己的列表项，不作用于分享文章）；但对**当前深链打开的他人文章**不应出现审核入口 |

- 顶部显示醒目提示条：「你正在查看他人文章，仅可阅读」。
- 作者本人（`can_edit === true`）打开自己文章链接时，行为与今天完全一致（可编辑），且受 §5.2 blocker 保护。
- 写路径后端有 404 兜底（未放开），前端屏蔽是为体验，不是唯一防线。

### 5.4 API 客户端
`web/src/api/articles.ts` 的 `getArticle` 返回类型补 `can_edit`；`web/src/types.ts` 的 `Article`（`types.ts:225`，= `ArticleSummary & {...}`）加 **`can_edit?: boolean`（写成可选，缺省视作 true）**。设为可选最稳：`applySavedArticle` / `setArticles(...展开...)` 会把 `Article` 铺进 `ArticleSummary[]`，可选字段无害；必填则需确认无手写 Article 字面量（核查未发现，均来自 API 响应）。

## 6. 数据流

```
【作者复制链接】
列表行 / 编辑器顶部「复制链接」 → clipboard 写入 ${origin}/article/{id}

【别人打开链接】
GET /article/123
  → RootLayout（要求登录，未登录走 LoginPage）
  → ArticlePermalinkRoute 取 articleId=123
  → ContentWorkspace(deepLinkArticleId=123)
  → getArticle(123)  ← 后端已放开：任意登录用户可读
  → can_edit=false（非作者）→ 编辑器只读 + 顶部提示条
  → 正文图 / 封面经 /api/assets/{id} 正常加载（无属主校验）
```

## 7. 测试策略

### 后端（`server/tests/test_articles_api.py` 增补）
- 非作者 `GET /api/articles/{id}` → **200**，能读到内容 + `can_edit=false`。
- 作者本人 `GET` → 200 + `can_edit=true`；admin `GET` 他人文章 → 200 + `can_edit=true`。
- 非作者 `PUT` / `DELETE` / `cover` / `approve` → 仍 **404**（写隔离不变）。
- 缺失 / 软删文章 `GET` → 404。
- `GET /api/articles`（列表）非作者看不到他人文章（列表私有不变）。

### 前端
无单测框架，门禁 = `pnpm --filter @geo/web typecheck` + `build`。手动验证：
1. 复制链接按钮写入正确 `/article/:id`。
2. 用另一账号打开链接 → 只读展示、图片正常、**所有写入口都消失/禁用**（工具栏、标题/作者/状态、封面上传、保存/删除/审核/分发、粘贴图片无效、图片手柄不出现）、提示条出现。
3. 作者本人打开自己链接 → 正常可编辑；编辑后在 `/article`↔`/content` 间移动**草稿不丢**、离开到 `/agents` 弹未保存确认。
4. 未登录打开链接 → 落到登录页，登录后回到文章。
5. 导航高亮「内容管理」；移动端（`useIsMobile`）布局正常、review-tabs 出现。
6. `/content/*` ↔ `/article/*` 切换时 `ContentWorkspace` 不重挂（可在编辑器打字后切换观察内容是否保留）。
7. **未保存守卫**：编辑器改了字未保存 → 点列表另一篇 / 深链换一篇 → 弹确认；取消则内容保留。深链首次进入（编辑器本空）不弹。

## 7b. 并发正确性（与 loop / pipeline 入库的关系）

分享功能**只读不写**，与 pipeline / scheme / MCP 的文章入库**不存在写写争用**：

- 链接是 `/article/<自增主键 id>`。文章的链接**只有 commit 入库后才存在**（id 由插入分配），且链接从"已存在文章"的复制按钮获得，天然已入库。
- `getArticle(id)` = 按主键 SELECT：读到**已 commit 的完整行**或 404。事务原子性保证**读不到半入库的脏行**——未 commit 的行对其它连接不可见。
- 读不加行锁、不阻塞插入：pipeline 插**新 id**，读者读**已存在 id**，不同行、无争用。恰好在 commit 前一刻访问某 id → 404；commit 后 → 正常。流式入库（每篇单独 commit）时读到的是"读那一刻的已提交快照"。
- **既有 nuance（非本功能新增）**：`read_article` 的 `_clear_ai_lock_if_expired`（`router.py:256`）在 AI 排版锁**超时**时会 commit 清锁；健康进行中的 ai_format（未超时）被读时它**什么都不做**、不打断排版。放开跨用户读只是让更多人能触发这个幂等、超时保护的惰性解锁，可接受。

## 8. 权衡与风险

- **复用编辑工作台的割裂感**：非作者打开链接时，左侧列表是他自己的文章（不含刚打开的那篇），右侧是分享来的只读文章 —— 有一点「列表与内容对不上」的割裂。用户已明确选择工作台而非独立只读页，用醒目提示条弥补。
- **任意登录用户可读 = 内部信任模型**：适合当前小团队。若将来出现敏感文章需隔离，再引入每篇「开启分享」开关（当前非目标）。
- **信息泄露面（已核实，比初稿担心的小）**：`ArticleRead`（`schemas.py:120-142`）**不含 `metrics`、不含 `user_id`**。跨用户真正可见的「溯源类」字段只有 `source_agent_name` / `source_template_name` / `source_template_id` / `ai_format_error`（哪个智能体/模板生成、模型报错串），对内部团队敏感度低。按 int id 枚举可探测他人文章存在性（200 vs 404），且 MCP 路径早已如此，可接受。
- **改动为加法**：不动列表私有、不动写路径属主校验、不动资源服务，回归风险集中在 `read_article` 一处 + 前端只读降级，易于测试与回滚。

## 9. 变更清单（实现时对照）

**后端**
- `server/app/modules/articles/router.py` — `read_article` 放开属主校验、计算并传入 `can_edit`。
- `server/app/modules/articles/schemas.py` — `ArticleRead.can_edit`。
- `server/app/modules/articles/service.py`（或 router 内 `to_article_read`）— 加可选 `can_edit` 参数。
- `server/tests/test_articles_api.py` — 跨用户读 / 写隔离用例。

**前端**
- `web/src/routes.tsx` — 新增 `article/:articleId` 路由，**复用 `ContentRoute` 元素**（防重挂，§3.2）；`ContentRoute` 读 `articleId` 并传 `deepLinkArticleId` + `isMobile`。
- `web/src/App.tsx` — `pathToNavKey` 把 `article` 段映射到 `content`（子 tab 不高亮属 cosmetic，可接受）。
- `web/src/features/content/ContentWorkspace.tsx` —
  - `deepLinkArticleId` prop + `loadArticleById`（gate on editor 就绪、只跑一次、照抄 `savedStateRef` 顺序，§5.1）；
  - **`loadArticleGuarded` 统一未保存守卫**（列表点选 + 深链共用，顺带修既有 gap，§5.2b）；
  - `useBlocker` 谓词放开 `/article`（§5.2）；
  - 只读降级**逐项**屏蔽（工具栏 / 元信息输入 / 封面 / 保存·删除·新建·审核·分发 / handlePaste / resize 手柄，§5.3 表）；
  - 「复制链接」按钮（列表行 + 顶部）。
- `web/src/api/articles.ts` / `web/src/types.ts` — `Article.can_edit?`（可选，§5.4）。

**验证时重点确认**：`/content/*` ↔ `/article/*` 导航时 `ContentWorkspace` **确实未重挂**（React 对同类型元素 reconcile）。若实测仍重挂，退路是把工作台状态上提或加稳定 key —— 但复用同一元素通常即可。
