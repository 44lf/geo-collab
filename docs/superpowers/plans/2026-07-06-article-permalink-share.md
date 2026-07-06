# 单篇文章可分享直达链接（Article Permalink Share）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给每篇文章一个稳定可复制的 `/article/:id` 直达链接，任意登录用户可只读查看任意单篇，编辑/删除/审核仍限作者本人（admin 例外）。

**Architecture:** 后端只放开「读单篇」的属主校验并新增 `can_edit` 字段驱动前端只读；前端新增顶级路由 `/article/:articleId`，**复用现有 `ContentRoute` 元素**避免 `ContentWorkspace` 重挂，深链把文章载入现有图文工作台，非作者进入逐项只读降级。纯加法：不动列表私有过滤、不动写路径属主校验、不动资源服务。

**Tech Stack:** 后端 FastAPI + SQLAlchemy + Pydantic（MySQL only，pytest）；前端 React 19 + Vite + TypeScript(strict) + Tiptap + react-router-dom v6。

**关联设计文档：** `docs/superpowers/specs/2026-07-06-article-permalink-share-design.md`（本计划逐条落实它，§编号引用该文档）。

## Global Constraints

- **工作目录**：本任务在 worktree `E:\geo\.claude\worktrees\article-permalink-share`（分支 `worktree-article-permalink-share`）。所有命令从该目录根运行；worktree 里 Bash cwd 会漂回主树，凡跑命令先 `cd` 到 worktree 根或用 `-C`/绝对路径，跑完 `pwd` 确认。
- **后端门禁**：`ruff check server/`、`ruff format --check server/`、`mypy server/app`、`pytest` 全绿。行长 100，忽略 E501/B008。
- **后端测试需要 MySQL**：设 `GEO_TEST_DATABASE_URL`（DB 名必须含 `"test"`），否则 `@pytest.mark.mysql` 用例自动跳过（跳过 ≠ 通过，必须真跑到绿）。conda activate 在工具 shell 里不生效，用 `python` / `pytest` 全路径或已激活 `geo_xzpt` 的终端。
- **前端门禁**：`pnpm --filter @geo/web typecheck` + `pnpm --filter @geo/web build` 均通过（前端无单测框架，这两个就是 CI 硬门禁；eslint 为非阻塞）。
- **service 层异常**：抛命名异常（`ClientError`/`ConflictError` 等），不抛裸 `ValueError`。本计划后端只用已存在的 `HTTPException`。
- **只加不改的边界（双保险）**：写路径（PUT/DELETE/cover/approve/revoke-approval）继续 `_verify_article_ownership`；列表 `GET /api/articles` 继续按 `user_id` 私有过滤。唯一放开点是 `GET /api/articles/{id}`。
- **提交**：仅在计划各 Task 末尾按步骤 commit；commit message 用中文/英文均可，遵循现有风格。不 push（除非用户另行要求）。

## 文件结构（本计划涉及的文件与职责）

**后端**
- `server/app/modules/articles/schemas.py` — `ArticleRead` 增 `can_edit` 字段；`to_article_read` 增可选 `can_edit` 参数（唯一构造点）。
- `server/app/modules/articles/router.py` — `read_article` 放开属主校验、计算并传入 `can_edit`。
- `server/tests/test_articles_api.py` — 跨用户读 / 写隔离 / 列表私有回归用例。

**前端**
- `web/src/types.ts` — `Article` 增可选 `can_edit?: boolean`。
- `web/src/features/content/ContentWorkspace.tsx` — `deepLinkArticleId` prop + `loadArticleById` + 统一 `loadArticleGuarded` 未保存守卫 + `useBlocker` 谓词放开 `/article` + 只读降级逐项屏蔽 + 复制链接按钮。
- `web/src/routes.tsx` — 新增 `article/:articleId` 路由复用 `ContentRoute`；`ContentRoute` 读 `articleId` 传 `deepLinkArticleId`。
- `web/src/App.tsx` — `pathToNavKey` 把 `article` 段映射到 `content`（高亮「内容管理」）。

**说明**：`web/src/api/articles.ts` 的 `getArticle` 已返回 `Promise<Article>`，`Article` 类型加了 `can_edit?` 后自动带上，**无需改 articles.ts**。

---

## Task 1: 后端放开「读单篇」属主校验 + `can_edit` 字段

放开 `GET /api/articles/{id}` 的跨用户读，新增 `can_edit` 驱动前端只读；写路径与列表不动。这是后端唯一改动，作为一个可独立评审、独立测试的单元。

**Files:**
- Modify: `server/app/modules/articles/schemas.py`（`ArticleRead` ~120-142；`to_article_read` ~199-233）
- Modify: `server/app/modules/articles/router.py`（`read_article` 249-257）
- Test: `server/tests/test_articles_api.py`（文件末尾追加用例，复用 `create_extra_user`）

**Interfaces:**
- Produces（前端 Task 2 依赖）：`GET /api/articles/{id}` 响应体新增布尔字段 `can_edit`：属主本人或 admin 为 `true`，其他登录用户为 `false`。
- Produces：`to_article_read(article, published_count: int = 0, can_edit: bool = True) -> ArticleRead`（新参数追加在末位，既有单参调用行为不变）。

- [ ] **Step 1: 写失败测试（跨用户可读 + can_edit=false）**

在 `server/tests/test_articles_api.py` 顶部 import 处补上 `create_extra_user`（现有 import 是 `from server.tests.utils import build_test_app`，改成下面这行）：

```python
from server.tests.utils import build_test_app, create_extra_user
```

在文件**末尾**追加：

```python
@pytest.mark.mysql
def test_non_owner_can_read_article_but_cannot_edit(monkeypatch):
    """任意登录用户可只读任意单篇：非作者 GET → 200 且 can_edit=False。"""
    test_app = build_test_app(monkeypatch)
    owner = test_app.client  # 默认 admin，作为文章作者
    try:
        resp = owner.post(
            "/api/articles",
            json={"title": "分享文章", "content_json": {"type": "doc", "content": []}},
        )
        assert resp.status_code == 200, resp.text
        article_id = resp.json()["id"]

        _uid, reader = create_extra_user(test_app, "reader_op", role="operator")
        got = reader.get(f"/api/articles/{article_id}")
        assert got.status_code == 200, got.text
        body = got.json()
        assert body["id"] == article_id
        assert body["title"] == "分享文章"
        assert body["can_edit"] is False
    finally:
        test_app.cleanup()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest server/tests/test_articles_api.py -q -k non_owner_can_read`
Expected: FAIL —— 现状 `read_article` 走 `_verify_article_ownership`，非作者返回 404（`assert got.status_code == 200` 失败）。

- [ ] **Step 3: schema 增 `can_edit` 字段**

`server/app/modules/articles/schemas.py`，在 `ArticleRead` 里 `source_template_id` 与 `created_at` 之间插入（这一对相邻行是 `ArticleRead` 独有，`ArticleListRead` 的 `source_template_id` 后面跟的是 `auto_review_score`）：

```python
    source_template_id: int | None = None
    can_edit: bool = True  # 属主/admin 为 True；他人只读分享时 False（驱动前端只读降级）
    created_at: datetime
```

- [ ] **Step 4: `to_article_read` 增可选参数并写入字段**

同文件 `to_article_read`，改签名（第 199 行）：

```python
def to_article_read(article: "Article", published_count: int = 0, can_edit: bool = True) -> ArticleRead:
```

并在 `ArticleRead(...)` 构造里，`source_template_id=...` 之后、右括号之前加一行：

```python
        source_template_id=article.source_template_id,
        can_edit=can_edit,
    )
```

- [ ] **Step 5: `read_article` 放开属主校验并计算 `can_edit`**

`server/app/modules/articles/router.py`，把 `read_article` 函数体（第 255-257 行）：

```python
    article = _verify_article_ownership(get_article(db, article_id), current_user)
    _clear_ai_lock_if_expired(db, article)
    return to_article_read(article)
```

替换为：

```python
    article = get_article(db, article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="文章不存在")
    _clear_ai_lock_if_expired(db, article)
    can_edit = (article.user_id == current_user.id) or (current_user.role == "admin")
    return to_article_read(article, can_edit=can_edit)
```

（`get_article` service 本身只按 id + `is_deleted` 过滤、无 user 过滤，故缺失/软删仍 404；`HTTPException` 已在本文件导入。写路径与列表完全不动。）

- [ ] **Step 6: 跑测试确认通过**

Run: `pytest server/tests/test_articles_api.py -q -k non_owner_can_read`
Expected: PASS

- [ ] **Step 7: 补齐属主/admin/写隔离/列表私有回归用例**

在 `server/tests/test_articles_api.py` 末尾继续追加：

```python
@pytest.mark.mysql
def test_owner_and_admin_read_can_edit_true(monkeypatch):
    """作者本人读自己文章 can_edit=True；admin 读他人文章 can_edit=True。"""
    test_app = build_test_app(monkeypatch)
    admin = test_app.client
    try:
        _uid, op = create_extra_user(test_app, "author_op", role="operator")
        resp = op.post(
            "/api/articles",
            json={"title": "operator 的文章", "content_json": {"type": "doc", "content": []}},
        )
        assert resp.status_code == 200, resp.text
        article_id = resp.json()["id"]

        # 作者本人读 → can_edit True
        own = op.get(f"/api/articles/{article_id}")
        assert own.status_code == 200
        assert own.json()["can_edit"] is True

        # admin 读他人文章 → 200 + can_edit True（admin 例外）
        as_admin = admin.get(f"/api/articles/{article_id}")
        assert as_admin.status_code == 200
        assert as_admin.json()["can_edit"] is True
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_non_owner_write_paths_still_404(monkeypatch):
    """写隔离不变：非作者 PUT / DELETE / cover / approve 仍 404。"""
    test_app = build_test_app(monkeypatch)
    owner = test_app.client
    try:
        resp = owner.post(
            "/api/articles",
            json={"title": "只读", "content_json": {"type": "doc", "content": []}},
        )
        article_id = resp.json()["id"]
        _uid, other = create_extra_user(test_app, "intruder_op", role="operator")

        assert other.put(f"/api/articles/{article_id}", json={"title": "改", "version": 1}).status_code == 404
        assert other.post(f"/api/articles/{article_id}/cover", json={"cover_asset_id": None, "version": 1}).status_code == 404
        assert other.post(f"/api/articles/{article_id}/approve").status_code == 404
        assert other.delete(f"/api/articles/{article_id}").status_code == 404
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_read_missing_article_404(monkeypatch):
    """缺失文章仍 404（放开跨用户读不影响不存在的 id）。"""
    test_app = build_test_app(monkeypatch)
    try:
        _uid, op = create_extra_user(test_app, "reader404_op", role="operator")
        assert op.get("/api/articles/99999999").status_code == 404
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_list_stays_private_after_read_relax(monkeypatch):
    """列表仍私有：非作者的 GET /api/articles 看不到他人文章。"""
    test_app = build_test_app(monkeypatch)
    owner = test_app.client
    try:
        resp = owner.post(
            "/api/articles",
            json={"title": "私有列表项", "content_json": {"type": "doc", "content": []}},
        )
        owner_article_id = resp.json()["id"]
        _uid, op = create_extra_user(test_app, "listprivacy_op", role="operator")

        listing = op.get("/api/articles")
        assert listing.status_code == 200
        ids = [row["id"] for row in listing.json()]
        assert owner_article_id not in ids
    finally:
        test_app.cleanup()
```

- [ ] **Step 8: 跑本文件全部用例确认通过**

Run: `pytest server/tests/test_articles_api.py -q`
Expected: PASS（新增 5 个用例全绿，既有用例不回归；软删 404 用例仍绿）。

- [ ] **Step 9: ruff + mypy**

Run:
```bash
ruff check server/app/modules/articles/ server/tests/test_articles_api.py
ruff format --check server/app/modules/articles/ server/tests/test_articles_api.py
mypy server/app/modules/articles/schemas.py server/app/modules/articles/router.py
```
Expected: 无报错（format 若提示需改写，去掉 `--check` 跑一遍再复查）。

- [ ] **Step 10: Commit**

```bash
git add server/app/modules/articles/schemas.py server/app/modules/articles/router.py server/tests/test_articles_api.py
git commit -m "feat(articles): 放开单篇跨用户只读 + can_edit 字段，写路径/列表不变"
```

---

## Task 2: 前端 `Article` 类型增 `can_edit?`

补类型契约，供后续只读降级读取 `selectedArticle.can_edit`。写成可选、缺省视作可编辑，最稳（`Article` 会被铺进 `ArticleSummary[]`，可选字段无害）。

**Files:**
- Modify: `web/src/types.ts`（`Article` type，225-235）

**Interfaces:**
- Consumes：Task 1 的 `GET /api/articles/{id}` 响应 `can_edit`。
- Produces（Task 4 依赖）：`Article.can_edit?: boolean`。

- [ ] **Step 1: 加字段**

`web/src/types.ts`，`Article` type 里 `ai_format_error` 之后、右花括号之前加：

```typescript
  ai_checking: boolean;
  ai_format_error: string | null;
  /** 当前登录用户是否可编辑该文章：属主/admin 为 true；他人只读分享时 false。缺省（列表铺入等）视作 true */
  can_edit?: boolean;
};
```

- [ ] **Step 2: typecheck**

Run（worktree 根目录）: `pnpm --filter @geo/web typecheck`
Expected: 通过（纯类型追加，无用点，零报错）。

- [ ] **Step 3: Commit**

```bash
git add web/src/types.ts
git commit -m "feat(web): Article 类型增可选 can_edit"
```

---

## Task 3: ContentWorkspace 深链加载 + 统一未保存守卫 + blocker 放开 /article

在 `ContentWorkspace` 内加深链能力：新 prop `deepLinkArticleId`、按 id 载入 `loadArticleById`、统一未保存守卫 `loadArticleGuarded`（列表点选与深链共用，顺带修好现有列表点选直接冲掉未保存内容的老 gap，§5.2b），并把 `useBlocker` 谓词放开 `/article`（§5.2）。此 Task 只改 `ContentWorkspace.tsx`，`deepLinkArticleId` 可选、暂无外部传入，typecheck-green。

**Files:**
- Modify: `web/src/features/content/ContentWorkspace.tsx`（`Props` 318-323；组件解构 325-330；blocker 433-435；loadArticle 之后 661；onSelect 1142）

**Interfaces:**
- Produces（Task 5 依赖）：`ContentWorkspace` 接受可选 prop `deepLinkArticleId?: number`；进入时按该 id 载入文章到编辑器。
- Produces（Task 4 复用）：`loadArticleById(id)` 会 `setSelectedArticle(detail)`，其中 `detail.can_edit` 供只读降级判定。

- [ ] **Step 1: `Props` 与组件解构加 `deepLinkArticleId`**

`Props` interface（318-323）改为：

```typescript
interface Props {
  isActive?: boolean;
  reviewTab?: ReviewStatus;
  onReviewTabChange?: (t: ReviewStatus) => void;
  isMobile?: boolean;
  deepLinkArticleId?: number;
}
```

组件解构（325-330）改为：

```typescript
export function ContentWorkspace({
  isActive,
  reviewTab: reviewTabProp,
  onReviewTabChange,
  isMobile,
  deepLinkArticleId,
}: Props = {}) {
```

- [ ] **Step 2: blocker 谓词放开 `/article`**

把（433-435）：

```typescript
  const blocker = useBlocker(
    ({ nextLocation }) => isDirty() && !nextLocation.pathname.startsWith("/content"),
  );
```

替换为：

```typescript
  const inWorkspace = (p: string) => p.startsWith("/content") || p.startsWith("/article");
  const blocker = useBlocker(
    ({ nextLocation }) => isDirty() && !inWorkspace(nextLocation.pathname),
  );
```

- [ ] **Step 3: 加 `loadArticleGuarded` 与 `loadArticleById`**

在 `loadArticle` 函数结束（第 661 行 `}` 之后）插入两个函数：

```typescript
  // 统一「载入另一篇文章」入口：当前有未保存改动先确认，取消则不载入。
  // 列表点选与深链换 id 都经此守卫（顺带修好现有列表点选直接冲掉未保存内容的老 gap）。
  async function loadArticleGuarded(loader: () => Promise<void>) {
    if (isDirty() && !window.confirm("当前文章有未保存内容，确定切换吗？未保存的修改将丢失。")) {
      return;
    }
    await loader();
  }

  // 按 id 直接载入（深链用）：不依赖列表 summary 预填，直接拉详情灌入编辑器。
  // savedStateRef 设置顺序照抄 loadArticle：先 setContent → 再算 bodyState → 写 ref，否则离开误弹「未保存」。
  async function loadArticleById(id: number) {
    setPendingCoverUrl((url) => { if (url) URL.revokeObjectURL(url); return null; });
    setLoading(true);
    setStatusText("加载中");
    try {
      const detail = await getArticle(id);
      setSelectedArticle(detail);
      setDraft({
        id: detail.id,
        title: detail.title,
        author: detail.author ?? "",
        cover_asset_id: detail.cover_asset_id,
        status: detail.status,
        version: detail.version,
        stock_category_ids: detail.stock_category_ids ?? [],
      });
      const displayDoc = normalizeEditorDocument(detail.content_json || emptyDoc, "display");
      editor?.commands.setContent(displayDoc);
      const bodyState = editor
        ? editorBodyState(editor)
        : stableStringify(normalizeEditorDocument(detail.content_json || emptyDoc, "save"));
      savedStateRef.current = {
        title: detail.title?.trim() ?? "",
        author: detail.author?.trim() ?? "",
        cover_asset_id: detail.cover_asset_id,
        bodyState,
      };
    } catch (error) {
      toast(error instanceof Error ? error.message : "加载文章失败", "error");
    } finally {
      setLoading(false);
      setStatusText("");
    }
  }
```

- [ ] **Step 4: 加深链加载 effect（gate 在 editor 就绪、id 变化才载入，经守卫）**

紧接 `beforeunload` 的 `useEffect`（结束于第 461 行）之后插入：

```typescript
  // 深链进入 /article/:id：等 editor 就绪后按 id 载入；仅在 id 变化时触发（换 id 经未保存守卫）。
  // useEditor 首帧返回 null，未就绪时调 loadArticleById 会被 editor?. 静默跳过、停在空白，故必须 gate 在 editor。
  const lastDeepLinkRef = useRef<number | null>(null);
  useEffect(() => {
    if (!editor || !deepLinkArticleId) return;
    if (lastDeepLinkRef.current === deepLinkArticleId) return;
    lastDeepLinkRef.current = deepLinkArticleId;
    void loadArticleGuarded(() => loadArticleById(deepLinkArticleId));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editor, deepLinkArticleId]);
```

（首帧 editor 空、`isDirty()` 为 false，守卫不弹确认；只有「已在编辑器改了东西、再换一篇」才弹。）

- [ ] **Step 5: 列表点选走统一守卫（修既有 gap）**

把第 1142 行：

```tsx
                      onSelect={(article) => void loadArticle(article)}
```

改为：

```tsx
                      onSelect={(article) => void loadArticleGuarded(() => loadArticle(article))}
```

- [ ] **Step 6: typecheck + build**

Run（worktree 根目录）:
```bash
pnpm --filter @geo/web typecheck
pnpm --filter @geo/web build
```
Expected: 均通过。

- [ ] **Step 7: Commit**

```bash
git add web/src/features/content/ContentWorkspace.tsx
git commit -m "feat(web): ContentWorkspace 支持深链载入 + 统一未保存守卫 + blocker 放开 /article"
```

---

## Task 4: ContentWorkspace 只读降级（非作者）

`can_edit === false` 时逐项屏蔽所有写入口（§5.3）。`setEditable(false)` 只拦 DOM 输入，programmatic `chain().run()` 仍生效，故工具栏/粘贴/resize/元信息/封面/保存·删除·审核·分发必须**额外**显式屏蔽。

**Files:**
- Modify: `web/src/features/content/ContentWorkspace.tsx`（ImageResizeView 193/270；handlePaste 397；editor 就绪后加 setEditable effect；currentReviewStatus 1050；formRow 输入 1328/1332/1336；封面 1349-1353；顶部按钮 1068-1079；reviewStrip 1357；只读提示条）

**Interfaces:**
- Consumes：`selectedArticle.can_edit`（Task 2 类型 + Task 3 载入）。

- [ ] **Step 1: ImageResizeView resize 手柄 gate 在 editable**

`ImageResizeView` 签名（193）加 `editor`：

```tsx
function ImageResizeView({ node, updateAttributes, selected, editor }: NodeViewProps) {
```

手柄渲染（270）改为：

```tsx
      {selected && editor.isEditable && <div className="imgResizeHandle" onMouseDown={startResize} />}
```

- [ ] **Step 2: handlePaste 只读早退**

`handlePaste`（397）第一行加 editable 判断（用 view 参数的 `.editable`，此处 `editor` 闭包在 useEditor 初始化期为 null 不可靠）：

```tsx
      handlePaste(view, event) {
        if (!view.editable) return false;
        const items = Array.from(event.clipboardData?.items ?? []);
```

（注意把原来的 `handlePaste(_, event)` 的 `_` 改成 `view`。）

- [ ] **Step 3: 计算 `canEdit` 并同步 editor 可编辑态**

> ⚠️ 顺序要求：`canEdit` 是 `const`（有 TDZ，不像 function 声明会 hoist），而下面的 `setEditable` effect 会读它并放进依赖数组。所以 **`canEdit` 必须定义在该 effect 之前**。它只依赖 `selectedArticle`（第 334 行已声明），故定义在顶部即可。

先在 `savedStateRef` 声明（第 413 行）之后、`isDirty` 的 `useCallback`（第 416 行）之前，加一行 `canEdit`：

```tsx
  const savedStateRef = useRef<{ title: string; author: string; cover_asset_id: number | string | null; bodyState: string } | null>(null);
  // 只读判定：无选中(新建草稿)/属主/admin 皆可编辑；仅他人分享时 can_edit===false → 只读
  const canEdit = selectedArticle?.can_edit !== false;
```

再在组件 effect 区（紧接 Task 3 Step 4 的深链 effect 之后，此处 `canEdit` 已在作用域内）加同步 editable 的 effect：

```tsx
  // 只读降级基线：非作者打开分享文章时禁用编辑器 DOM 输入（programmatic 写入另由下方逐项屏蔽）。
  useEffect(() => {
    editor?.setEditable(canEdit);
  }, [editor, canEdit]);
```

（`const currentReviewStatus`（第 1050 行）保持原样，不在此处改动；Step 8 会用到 `canEdit`，届时它已在作用域内。）

- [ ] **Step 4: 只读提示条**

在 `editorPane` 的 `<div className="formRow split">`（第 1325 行）之前插入：

```tsx
          {selectedArticle && !canEdit && (
            <div style={{ margin: "0 0 12px", padding: "8px 12px", borderRadius: 8, background: "#fff7ed", color: "#9a3412", fontSize: 13, border: "1px solid #fed7aa" }}>
              你正在查看他人分享的文章，仅可阅读，无法编辑或审核。
            </div>
          )}
```

- [ ] **Step 5: 标题/作者/状态输入禁用**

标题 input（1328）：

```tsx
              <input value={draft.title} disabled={!canEdit} onChange={(event) => setDraft({ ...draft, title: event.target.value })} />
```

作者 input（1332）：

```tsx
              <input value={draft.author} disabled={!canEdit} onChange={(event) => setDraft({ ...draft, author: event.target.value })} />
```

状态 select（1336）：

```tsx
              <select value={draft.status} disabled={!canEdit} onChange={(event) => setDraft({ ...draft, status: event.target.value })}>
```

- [ ] **Step 6: 封面上传只读时隐藏**

把封面上传 label（1349-1353）：

```tsx
              <label className="fileButton">
                <Upload size={16} />
                上传封面
                <input accept="image/*" type="file" onChange={(event) => { void handleCoverUpload(event.target.files?.[0] ?? null); event.currentTarget.value = ""; }} />
              </label>
```

包成条件渲染：

```tsx
              {canEdit && (
                <label className="fileButton">
                  <Upload size={16} />
                  上传封面
                  <input accept="image/*" type="file" onChange={(event) => { void handleCoverUpload(event.target.files?.[0] ?? null); event.currentTarget.value = ""; }} />
                </label>
              )}
```

- [ ] **Step 7: 顶部 删除/保存/新建 只读时隐藏**

把顶部三个按钮（1068-1079，`删除`/`保存`/`新建`；`刷新` 按钮 1064-1067 保留）：

```tsx
          <button className="dangerButton" disabled={!draft.id || loading} type="button" onClick={() => setConfirmDeleteArticle(true)}>
            <Trash2 size={16} />
            删除
          </button>
          <button className="primaryButton" disabled={loading || imageUploading > 0} type="button" onClick={() => void saveArticle()}>
            <Save size={16} />
            保存
          </button>
          <button className="secondaryButton" disabled={loading} type="button" onClick={() => { if (isDirty()) { setConfirmUnsavedNew(true); } else { resetDraft(); } }}>
            <Plus size={16} />
            新建
          </button>
```

包成：

```tsx
          {canEdit && (
            <>
              <button className="dangerButton" disabled={!draft.id || loading} type="button" onClick={() => setConfirmDeleteArticle(true)}>
                <Trash2 size={16} />
                删除
              </button>
              <button className="primaryButton" disabled={loading || imageUploading > 0} type="button" onClick={() => void saveArticle()}>
                <Save size={16} />
                保存
              </button>
              <button className="secondaryButton" disabled={loading} type="button" onClick={() => { if (isDirty()) { setConfirmUnsavedNew(true); } else { resetDraft(); } }}>
                <Plus size={16} />
                新建
              </button>
            </>
          )}
```

- [ ] **Step 8: reviewStrip（通过审核/撤销/分发）只读时隐藏**

把（1357）：

```tsx
            {selectedArticle ? (
```

改为：

```tsx
            {selectedArticle && canEdit ? (
```

- [ ] **Step 9: 工具栏只读时隐藏**

把 `EditorToolbar`（1399-1408 整块）包成条件渲染：

```tsx
          {canEdit && (
            <EditorToolbar
              editor={editor}
              onImageUpload={handleBodyImageUpload}
              imageSelected={!!editor?.isActive("image")}
              onSaveImage={() => {
                const src = editor?.getAttributes("image").src as string | undefined;
                if (src) setSaveImageSrc(src);
                else toast("请先选中正文中的图片", "error");
              }}
            />
          )}
```

- [ ] **Step 10: typecheck + build**

Run（worktree 根目录）:
```bash
pnpm --filter @geo/web typecheck
pnpm --filter @geo/web build
```
Expected: 均通过。

- [ ] **Step 11: Commit**

```bash
git add web/src/features/content/ContentWorkspace.tsx
git commit -m "feat(web): 非作者只读降级（工具栏/元信息/封面/保存删除审核/粘贴/resize 全屏蔽）"
```

---

## Task 5: 路由 `/article/:articleId` + 导航高亮

新增顶级路由，**复用 `ContentRoute` 元素**（防 `ContentWorkspace` 重挂，§3.2），`ContentRoute` 读 `articleId` 传 `deepLinkArticleId`；`App.tsx` 把 `article` 首段映射到 `content` 高亮「内容管理」。落地后 `/article/:id` 端到端可达。

**Files:**
- Modify: `web/src/routes.tsx`（`ContentRoute` 57-70；router children 105-106）
- Modify: `web/src/App.tsx`（`pathToNavKey` 34-37）

**Interfaces:**
- Consumes：Task 3 的 `ContentWorkspace` prop `deepLinkArticleId?: number`。

- [ ] **Step 1: `ContentRoute` 读 `articleId` 并透传**

`web/src/routes.tsx` 的 `ContentRoute`（57-70）改为：

```tsx
// 「内容管理」子页（未审核 / 已审核）由 URL 段驱动：/content/:status。
// 同一组件也承接永久链接 /article/:articleId —— 复用同一元素让 React reconcile 而非重挂，
// 保住编辑器草稿 / savedStateRef（详见设计 §3.2）。
function ContentRoute() {
  const { status, articleId } = useParams();
  const navigate = useNavigate();
  const isMobile = useIsMobile();
  const reviewTab: ReviewStatus = status === "approved" ? "approved" : "pending";
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

- [ ] **Step 2: 注册 `article/:articleId` 路由（复用 `ContentRoute` 元素）**

在 router children 里 `content/:status`（106 行）之后加一行：

```tsx
      { path: "content", element: <ContentRoute /> },
      { path: "content/:status", element: <ContentRoute /> },
      { path: "article/:articleId", element: <ContentRoute /> },
```

- [ ] **Step 3: `pathToNavKey` 把 `article` 映射到 `content`**

`web/src/App.tsx` 的 `pathToNavKey`（34-37）改为：

```tsx
function pathToNavKey(pathname: string): NavKey {
  const seg = pathname.split("/").filter(Boolean)[0];
  if (seg === "article") return "content"; // 永久链接 /article/:id 归入「内容管理」高亮
  return (KNOWN_NAV as string[]).includes(seg) ? (seg as NavKey) : "agents";
}
```

- [ ] **Step 4: typecheck + build**

Run（worktree 根目录）:
```bash
pnpm --filter @geo/web typecheck
pnpm --filter @geo/web build
```
Expected: 均通过。

- [ ] **Step 5: 手动验证（dev server）**

启动前端 dev（worktree 根）：`pnpm --filter @geo/web dev`（需后端 8000 在跑）。用两个账号验证 §7 清单要点：
1. 用账号 A 打开自己文章、复制其 id，浏览器地址栏访问 `/article/<id>` → 进入图文工作台、载入该文、可编辑、左侧「内容管理」高亮。
2. 用账号 B（另一登录）访问同一 `/article/<id>` → 200 只读展示；正文图/封面正常；工具栏/标题/作者/状态/封面上传/保存·删除·新建/审核·分发**全部消失或禁用**；粘贴图片无效；选中图片无 resize 手柄；顶部出现只读提示条。
3. 账号 A 在 `/article/<id>` 编辑器打字后，在 `/article/<id>` ↔ `/content/pending` 间切换 → 内容**不丢**（无重挂）；再点侧栏「智能体管理」→ 弹未保存确认。
4. 未登录直接访问 `/article/<id>` → 落登录页，登录后回到该文章。
5. 编辑器改字未保存 → 点列表另一篇 → 弹确认；取消则内容保留；深链首次进入（编辑器本空）不弹。

（此为人工验证步骤，不阻断 commit；发现问题回到对应 Task 修。）

- [ ] **Step 6: Commit**

```bash
git add web/src/routes.tsx web/src/App.tsx
git commit -m "feat(web): 新增 /article/:id 永久链接路由（复用 ContentRoute）+ 导航高亮"
```

---

## Task 6: 「复制链接」入口（列表行 + 编辑器顶部）

给「每篇文章都有自己可复制的链接」提供入口（§5.2）。只写剪贴板、**不导航**，正常分享流零风险（永不触发未保存守卫）。

**Files:**
- Modify: `web/src/features/content/ContentWorkspace.tsx`（新增 `copyArticleLink` 辅助；列表行动作区 1144-1166；顶部 topActions 1059-1067）

- [ ] **Step 1: 加 `copyArticleLink` 辅助**

在 `loadArticleById` 之后（或任意组件内函数区）加：

```tsx
  async function copyArticleLink(id: number) {
    const url = `${window.location.origin}/article/${id}`;
    try {
      await navigator.clipboard.writeText(url);
      toast("链接已复制", "success");
    } catch {
      toast(`复制失败，链接：${url}`, "info"); // 无剪贴板权限时把链接显示出来供手动复制
    }
  }
```

- [ ] **Step 2: 列表每行加「复制链接」按钮**

在列表行动作区，「加入分组」按钮（1156-1165）之后、`</div>`（articleRowActions 收尾，1166）之前加：

```tsx
                      <button
                        className="inlineMiniButton"
                        type="button"
                        onClick={() => void copyArticleLink(item.article.id)}
                      >
                        复制链接
                      </button>
```

- [ ] **Step 3: 编辑器顶部加「复制链接」按钮（有选中文章时）**

在顶部 `topActions` 的「刷新」按钮（1064-1067）之前加：

```tsx
          {draft.id ? (
            <button className="secondaryButton" type="button" onClick={() => void copyArticleLink(draft.id!)}>
              复制链接
            </button>
          ) : null}
```

- [ ] **Step 4: typecheck + build**

Run（worktree 根目录）:
```bash
pnpm --filter @geo/web typecheck
pnpm --filter @geo/web build
```
Expected: 均通过。

- [ ] **Step 5: 手动验证**

- 列表每行点「复制链接」→ 剪贴板得 `<origin>/article/<该行 id>`，toast「链接已复制」。
- 打开某文章后顶部「复制链接」→ 得当前文章链接。
- 粘贴到地址栏能正确直达对应文章（含用另一账号）。

- [ ] **Step 6: Commit**

```bash
git add web/src/features/content/ContentWorkspace.tsx
git commit -m "feat(web): 列表行与编辑器顶部新增复制链接按钮"
```

---

## 收尾核对（全部 Task 完成后）

- [ ] 后端：`pytest server/tests/test_articles_api.py -q` 全绿；`ruff check server/`、`ruff format --check server/`、`mypy server/app` 无报错。
- [ ] 前端：`pnpm --filter @geo/web typecheck` + `pnpm --filter @geo/web build` 通过。
- [ ] 设计 §7 手动清单 1-7 全部人工过一遍（尤其 §7 第 6 条：`/content/*` ↔ `/article/*` 切换 `ContentWorkspace` **确实未重挂**——编辑器打字后切换观察内容保留；若实测重挂，退路是给工作台加稳定 key 或状态上提，但复用同元素通常即可）。
- [ ] 变更集中在 `read_article` 一处 + 前端只读降级 + 路由/复制入口，回归面可控。

## Self-Review 记录（对照设计 §9 变更清单）

- §4.1 放开读属主校验 → Task 1 Step 5 ✅
- §4.2 `can_edit` 字段 + `to_article_read` 参数 → Task 1 Step 3/4 ✅
- §4.3 写路径不动（双保险）→ Task 1 Step 7 `test_non_owner_write_paths_still_404` 守护 ✅
- §4.4 列表私有不动 → Task 1 Step 7 `test_list_stays_private_after_read_relax` 守护 ✅
- §4.5 资源已共享无需改 → 不产生改动（设计已核实）✅
- §5.1 深链加载 + editor 就绪 gate + savedStateRef 顺序 → Task 3 Step 3/4 ✅
- §5.2 blocker 放开 `/article` → Task 3 Step 2 ✅
- §5.2b 统一未保存守卫（修既有 gap）→ Task 3 Step 3/5 ✅
- §5.3 只读降级逐项（工具栏/粘贴/resize/元信息/封面/保存删除新建/审核分发）→ Task 4 全 ✅
- §5.4 `Article.can_edit?` 可选 → Task 2 ✅（`getArticle` 已返回 `Article`，articles.ts 无需改）
- §3.2 复用 `ContentRoute` 防重挂 → Task 5 Step 2 ✅
- §3.3 `pathToNavKey` 映射 → Task 5 Step 3 ✅
- §5.2 复制链接双入口 → Task 6 ✅

**说明**：设计 §4.6（`article.view` 审计 + 读限流）标注为「非阻断，实现时补一笔」，本期按设计**不实现**（内部信任模型可接受），如后续需要另开小改动。
