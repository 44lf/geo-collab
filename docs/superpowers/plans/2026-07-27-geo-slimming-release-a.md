# GEO Slimming Release A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不删除历史表和数据的前提下，把共享能力从方案流解耦、把问题源管理迁到智能体、停止方案写入，并建立可观测的七日 Release B 准入窗口。

**Architecture:** 保留 `/api/generation/question-pools/*`、两个 engine URL、全部 MCP 名称和 Pipeline 节点，通过中立的 `runtime_templates.py`、`question_bank.py` 和拆分后的前端 API 承载共享能力。旧方案 GET 暂时只读保留，写请求统一 410；问题可用性先切到 `source_active`，让 Release B 只做结构删除而不再改变行为。

**Tech Stack:** Python 3.11、FastAPI、SQLAlchemy 2、Pydantic 2、pytest（MySQL only）、React 19、TypeScript、Vite、pnpm、MCP。

## Global Constraints

- 本发布不删除、重命名或迁移任何数据库表、列和历史数据。
- `/api/generation/question-pools/*`、`/api/generation/ai-engines`、`/api/generation/format-engines` 的路径和鉴权保持不变。
- MCP `list_question_pools`、`list_question_items`、`save_article` 和 `get_template_performance` 的 tool 名与公开参数保持不变。
- MCP 工具计数保持 `MCP_TOOLS_COUNT = 39`。
- `/api/mcp/loop-skill-bundle/*` 和 `/api/mcp/skills/*` 不改动。
- `source_active` 是问题可用性的唯一真值；兼容 DTO 固定输出 `status="pending"`、`article_id=null`。
- 未传 `status` 或传 `pending` 返回 active，`all` 返回全部，`consumed` 返回空，其他值返回 HTTP 400。
- `/ai` 显式重定向 `/agents`；旧方案源码在 Release A 仍保留。
- 方案 POST、PUT、PATCH、DELETE 和启动 run 统一返回 410，且不得写数据库或审计日志。
- Release A 上线后至少观察 7 个连续自然日；门禁未全部满足时禁止执行 Release B。
- MySQL 测试只能使用名称包含 `test` 的 `GEO_TEST_DATABASE_URL`，不得设置绕过变量指向生产。

---

## File Map

- `server/app/modules/ai_generation/runtime_templates.py`：唯一共享的运行时模板候选解析。
- `server/app/modules/ai_generation/question_bank.py`：问题 active 查询、问题类型聚合和兼容投影所需数据读取。
- `server/app/modules/ai_generation/router.py`：问题池、问题类型和 engine 共享 HTTP 契约。
- `web/src/api/question-pools.ts`：问题池、items、question-types 客户端。
- `web/src/api/generation-engines.ts`：写作和格式模型列表客户端。
- `web/src/features/pipelines/question-pools/QuestionPoolManagerModal.tsx`：智能体内的问题源管理 UI。
- `server/app/modules/performance/service.py`：真实模板表现聚合。
- `server/tests/test_geo_slimming_release_a.py`：Release A 共享契约和静态依赖门禁。
- `docs/runbooks/geo-slimming-release-a-observation.md`：七日观察证据模板和 Release B 准入结论。

### Task 1: Extract the Shared Runtime Template Selector

**Files:**
- Create: `server/app/modules/ai_generation/runtime_templates.py`
- Modify: `server/app/modules/pipelines/nodes/ai_compose.py`
- Modify: `server/app/modules/pipelines/nodes/ai_generate_node.py`
- Modify: `server/app/modules/ai_generation/scheme_executor.py`
- Modify: `server/tests/test_runtime_template_resolution.py`
- Create: `server/tests/test_geo_slimming_release_a.py`

**Interfaces:**
- Consumes: `get_runtime_prompt_template(db, template_id, user_id=user_id, scope="generation")`.
- Produces: `pick_valid_template(db: Session, template_ids: list[int], user_id: int, *, rng: random.Random | None = None) -> PromptTemplate | None`.
- Invariant: 输入 ID 去重但保持顺序；忽略不可见、scope 不符或 disabled 模板；没有候选返回 `None`。

- [ ] **Step 1: Move the existing behavioral tests to the public helper and add a dependency gate**

```python
from server.app.modules.ai_generation.runtime_templates import pick_valid_template


def test_pick_valid_template_deduplicates_and_preserves_candidate_order(db, user):
    rng = random.Random(7)
    result = pick_valid_template(db, [11, 11, 12], user_id=user.id, rng=rng)
    assert result is not None
    assert result.id in {11, 12}


def test_pipeline_does_not_import_scheme_modules():
    roots = [
        Path("server/app/modules/pipelines/nodes/ai_compose.py"),
        Path("server/app/modules/pipelines/nodes/ai_generate_node.py"),
    ]
    forbidden = {"scheme_router", "scheme_service", "scheme_executor"}
    for path in roots:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        assert not any(any(part in name for part in forbidden) for name in imported)
```

- [ ] **Step 2: Run the focused tests and verify the new module/import gate fails**

Run: `conda run -n geo_xzpt pytest server/tests/test_runtime_template_resolution.py server/tests/test_geo_slimming_release_a.py -q`

Expected: FAIL because `runtime_templates.py` does not exist and Pipeline still imports `scheme_executor`.

- [ ] **Step 3: Add the shared implementation and switch every consumer**

```python
def pick_valid_template(
    db: Session,
    template_ids: list[int],
    user_id: int,
    *,
    rng: random.Random | None = None,
) -> PromptTemplate | None:
    candidates: list[PromptTemplate] = []
    for template_id in dict.fromkeys(template_ids):
        template = get_runtime_prompt_template(
            db, template_id, user_id=user_id, scope="generation"
        )
        if template is not None and template.is_enabled:
            candidates.append(template)
    if not candidates:
        return None
    return (rng or random.Random()).choice(candidates)
```

Change both Pipeline nodes to import `pick_valid_template` from `runtime_templates`. Keep a temporary alias in `scheme_executor.py`:

```python
from .runtime_templates import pick_valid_template

_pick_valid_template = pick_valid_template
```

- [ ] **Step 4: Run focused and Pipeline registration tests**

Run: `conda run -n geo_xzpt pytest server/tests/test_runtime_template_resolution.py server/tests/test_pipeline_node_types.py server/tests/test_geo_slimming_release_a.py -q`

Expected: PASS; importing `server.app.modules.pipelines.nodes` still registers `ai_compose` and `ai_generate`.

- [ ] **Step 5: Commit**

```bash
git add server/app/modules/ai_generation/runtime_templates.py server/app/modules/ai_generation/scheme_executor.py server/app/modules/pipelines/nodes/ai_compose.py server/app/modules/pipelines/nodes/ai_generate_node.py server/tests/test_runtime_template_resolution.py server/tests/test_geo_slimming_release_a.py
git commit -m "refactor: extract generation runtime template selector"
```

### Task 2: Move Question Types and Engine Endpoints to the Shared Router

**Files:**
- Modify: `server/app/modules/ai_generation/question_bank.py`
- Modify: `server/app/modules/ai_generation/router.py`
- Modify: `server/app/modules/ai_generation/scheme_router.py`
- Modify: `server/app/modules/ai_generation/schemas.py`
- Modify: `server/tests/test_generation_schemes.py`
- Modify: `server/tests/test_ai_models_api.py`
- Modify: `server/tests/test_geo_slimming_release_a.py`

**Interfaces:**
- Consumes: `QuestionItem.pool_id`, `QuestionItem.source_active`, `QuestionItem.category`, `QuestionItem.id`.
- Produces: `question_types(db: Session, pool_id: int) -> list[tuple[str | None, list[QuestionItem]]]`.
- Preserves: `GET /api/generation/question-pools/{pool_id}/question-types`, `GET /api/generation/ai-engines`, `GET /api/generation/format-engines`, and `AiEngineRead {label: str, model: str}`.

- [ ] **Step 1: Add ownership and response-contract tests**

```python
def test_question_types_are_served_without_scheme_service(monkeypatch, client):
    monkeypatch.setitem(sys.modules, "server.app.modules.ai_generation.scheme_service", None)
    response = client.get("/api/generation/question-pools/1/question-types")
    assert response.status_code == 200


def test_shared_engine_urls_keep_public_shape(client):
    for path in ("/api/generation/ai-engines", "/api/generation/format-engines"):
        response = client.get(path)
        assert response.status_code == 200
        assert all(set(item) == {"label", "model"} for item in response.json())
```

- [ ] **Step 2: Run tests and verify the question-types isolation test fails**

Run: `conda run -n geo_xzpt pytest server/tests/test_generation_schemes.py server/tests/test_ai_models_api.py server/tests/test_geo_slimming_release_a.py -q`

Expected: FAIL because `router.py` imports `scheme_service` at request time.

- [ ] **Step 3: Move aggregation and engine route bodies without changing output**

```python
def question_types(
    db: Session, pool_id: int
) -> list[tuple[str | None, list[QuestionItem]]]:
    items = (
        db.query(QuestionItem)
        .filter(
            QuestionItem.pool_id == pool_id,
            QuestionItem.source_active.is_(True),
        )
        .order_by(QuestionItem.id)
        .all()
    )
    grouped: dict[str | None, list[QuestionItem]] = {}
    for item in items:
        grouped.setdefault(item.category, []).append(item)
    return list(grouped.items())
```

Move the exact `/ai-engines` and `/format-engines` handlers from `scheme_router.py` into the existing shared `router` object. Remove only those handlers from `scheme_router.py`; keep `AiEngineRead` in `schemas.py`.

- [ ] **Step 4: Run route, auth and DTO tests**

Run: `conda run -n geo_xzpt pytest server/tests/test_generation_schemes.py server/tests/test_ai_models_api.py server/tests/test_geo_slimming_release_a.py -q`

Expected: PASS with unchanged URLs, auth behavior and `{label, model}` payloads.

- [ ] **Step 5: Commit**

```bash
git add server/app/modules/ai_generation/question_bank.py server/app/modules/ai_generation/router.py server/app/modules/ai_generation/scheme_router.py server/app/modules/ai_generation/schemas.py server/tests/test_generation_schemes.py server/tests/test_ai_models_api.py server/tests/test_geo_slimming_release_a.py
git commit -m "refactor: move shared generation endpoints out of schemes"
```

### Task 3: Switch Question HTTP and MCP Reads to Active Semantics

**Files:**
- Modify: `server/app/modules/ai_generation/question_bank.py`
- Modify: `server/app/modules/ai_generation/router.py`
- Modify: `server/app/modules/ai_generation/schemas.py`
- Modify: `server/app/modules/mcp_catalog/router.py`
- Modify: `server/tests/test_question_bank.py`
- Modify: `server/tests/test_mcp_catalog.py`
- Modify: `server/tests/test_save_article_mcp.py`

**Interfaces:**
- Produces: `list_items(db: Session, pool_id: int, *, availability: Literal["active", "all"] = "active") -> list[QuestionItem]`.
- Produces: `question_item_to_read(item: QuestionItem) -> QuestionItemRead`.
- Preserves MCP public signature: `list_question_items(pool_id: int, limit: int = 20, category: str | None = None)`.
- Compatibility projection: `QuestionItemRead.status == "pending"` and `QuestionItemRead.article_id is None`.

- [ ] **Step 1: Add the complete compatibility matrix**

```python
@pytest.mark.parametrize(
    ("status", "expected_ids"),
    [(None, [1, 2]), ("pending", [1, 2]), ("all", [1, 2, 3]), ("consumed", [])],
)
def test_question_items_compatibility(client, seeded_items, status, expected_ids):
    suffix = "" if status is None else f"?status={status}"
    response = client.get(f"/api/generation/question-pools/1/items{suffix}")
    assert response.status_code == 200
    assert [row["id"] for row in response.json()] == expected_ids
    assert all(row["status"] == "pending" for row in response.json())
    assert all(row["article_id"] is None for row in response.json())


def test_question_items_reject_unknown_status(client):
    response = client.get("/api/generation/question-pools/1/items?status=typo")
    assert response.status_code == 400


def test_pending_count_means_active_count(client):
    body = client.get("/api/generation/question-pools").json()
    assert body[0]["pending_count"] == 2
```

Add the same active filtering and projection assertions to `test_mcp_catalog.py`, and assert the MCP tool schema does not expose a `status` argument and still declares `limit` default `20`.

- [ ] **Step 2: Run tests and verify old status-column behavior fails**

Run: `conda run -n geo_xzpt pytest server/tests/test_question_bank.py server/tests/test_mcp_catalog.py server/tests/test_save_article_mcp.py -q`

Expected: FAIL because old reads filter `QuestionItem.status`, unknown values return 200, and ORM validation exposes consumed/article IDs.

- [ ] **Step 3: Implement a single active query and explicit DTO projection**

```python
def list_items(
    db: Session,
    pool_id: int,
    *,
    availability: Literal["active", "all"] = "active",
) -> list[QuestionItem]:
    query = db.query(QuestionItem).filter(QuestionItem.pool_id == pool_id)
    if availability == "active":
        query = query.filter(QuestionItem.source_active.is_(True))
    return query.order_by(QuestionItem.id).all()


def question_item_to_read(item: QuestionItem) -> QuestionItemRead:
    return QuestionItemRead(
        id=item.id,
        record_id=item.record_id,
        fields=item.fields,
        question_text=question_text_of(item),
        category=item.category,
        source_active=item.source_active,
        status="pending",
        article_id=None,
    )
```

Map HTTP status values before querying:

```python
if status not in {"pending", "all", "consumed"}:
    raise HTTPException(status_code=400, detail="status must be pending, all, or consumed")
if status == "consumed":
    return []
availability = "all" if status == "all" else "active"
```

Use `question_item_to_read()` in both user HTTP and MCP catalog responses. Count active rows for `pending_count`. Do not alter the MCP tool declaration.

- [ ] **Step 4: Run all question-pool, MCP and Pipeline source tests**

Run: `conda run -n geo_xzpt pytest server/tests/test_question_bank.py server/tests/test_question_pool_sync.py server/tests/test_mcp_catalog.py server/tests/test_save_article_mcp.py server/tests/test_question_source_multiselect.py -q`

Expected: PASS; historical consumed-but-active fixtures appear in default active reads, inactive rows only appear for `all`.

- [ ] **Step 5: Commit**

```bash
git add server/app/modules/ai_generation/question_bank.py server/app/modules/ai_generation/router.py server/app/modules/ai_generation/schemas.py server/app/modules/mcp_catalog/router.py server/tests/test_question_bank.py server/tests/test_mcp_catalog.py server/tests/test_save_article_mcp.py
git commit -m "feat: align question reads with active source state"
```

### Task 4: Split Shared Frontend APIs and Complete Question Pool Types

**Files:**
- Create: `web/src/api/question-pools.ts`
- Create: `web/src/api/generation-engines.ts`
- Modify: `web/src/api/ai-generation.ts`
- Modify: `web/src/api/client.ts`
- Modify: `web/src/types.ts`
- Modify: `web/src/features/pipelines/PipelineEditor.tsx`
- Modify: `web/src/features/ai-generation/GenerateTab.tsx`
- Modify: `web/src/features/ai-generation/SchemeEditorModal.tsx`
- Modify: `web/src/features/ai-generation/PoolManagerModal.tsx`
- Modify: `server/app/modules/ai_generation/schemas.py`
- Modify: `server/tests/test_question_bank.py`

**Interfaces:**
- Produces frontend `QuestionPool` with `auto_sync_enabled: boolean`.
- Produces `listQuestionPools`, `createQuestionPool`, `updateQuestionPool`, `deleteQuestionPool`, `syncQuestionPool`, `listQuestionItems`, `listQuestionTypes`.
- Produces `listAiEngines`, `listFormatEngines`.
- `PipelineEditor` imports no symbol from `api/ai-generation.ts`.

- [ ] **Step 1: Add backend serialization coverage and a frontend import gate**

```python
def test_question_pool_read_includes_auto_sync_enabled(client):
    row = client.get("/api/generation/question-pools").json()[0]
    assert row["auto_sync_enabled"] is True
```

```powershell
$imports = rg -n "api/ai-generation" web/src/features/pipelines
if ($LASTEXITCODE -eq 0) { throw $imports }
```

- [ ] **Step 2: Run the backend test and import gate**

Run: `conda run -n geo_xzpt pytest server/tests/test_question_bank.py::test_question_pool_read_includes_auto_sync_enabled -q`

Expected: FAIL because `QuestionPoolRead` does not emit the field.

Run: `powershell -Command '$m = rg -n "api/ai-generation" web/src/features/pipelines; if ($LASTEXITCODE -eq 0) { throw $m }'`

Expected: FAIL and print the `PipelineEditor.tsx` import.

- [ ] **Step 3: Add the field and move only shared client functions**

```python
class QuestionPoolRead(BaseModel):
    id: int
    name: str
    pending_count: int
    feishu_app_token: str | None
    feishu_table_id: str | None
    last_synced_at: datetime | None
    created_at: datetime
    auto_sync_enabled: bool
```

```ts
export type QuestionPool = {
  id: number
  name: string
  pending_count: number
  feishu_app_token: string | null
  feishu_table_id: string | null
  last_synced_at: string | null
  created_at: string
  auto_sync_enabled: boolean
}
```

Copy the exact existing request paths and payloads into the two focused API files and remove those definitions from `ai-generation.ts`. Update `api/client.ts` to export both new modules. Update `PipelineEditor.tsx`, `GenerateTab.tsx`, `SchemeEditorModal.tsx` and the old `PoolManagerModal.tsx` to import shared functions directly from the new modules; keep their scheme-only imports in `ai-generation.ts`. This is required because Release A keeps the unreachable old source in the TypeScript compilation. Update `_pool_to_read()` to pass `auto_sync_enabled=pool.auto_sync_enabled`; otherwise FastAPI response validation will fail even though the ORM column exists.

- [ ] **Step 4: Run backend test and frontend gates**

Run: `conda run -n geo_xzpt pytest server/tests/test_question_bank.py -q`

Expected: PASS.

Run: `pnpm --filter @geo/web typecheck && pnpm --filter @geo/web build`

Expected: PASS; no Pipeline import from `api/ai-generation.ts`.

- [ ] **Step 5: Commit**

```bash
git add server/app/modules/ai_generation/schemas.py server/tests/test_question_bank.py web/src/api/question-pools.ts web/src/api/generation-engines.ts web/src/api/ai-generation.ts web/src/api/client.ts web/src/types.ts web/src/features/pipelines/PipelineEditor.tsx web/src/features/ai-generation/GenerateTab.tsx web/src/features/ai-generation/SchemeEditorModal.tsx web/src/features/ai-generation/PoolManagerModal.tsx
git commit -m "refactor: split shared generation frontend clients"
```

### Task 5: Move Question Source Management into Agents and Retire the AI Route

**Files:**
- Create: `web/src/features/pipelines/question-pools/QuestionPoolManagerModal.tsx`
- Create: `scripts/check-release-a-frontend.ps1`
- Modify: `web/src/features/pipelines/AgentManagementWorkspace.tsx`
- Modify: `web/src/features/pipelines/PipelineEditor.tsx`
- Modify: `web/src/features/ai-generation/PoolManagerModal.tsx`
- Modify: `web/src/routes.tsx`
- Modify: `web/src/App.tsx`
- Modify: `web/src/components/MobileNav.tsx`
- Modify: `web/src/types.ts`
- Modify: `web/src/styles.css`

**Interfaces:**
- Produces: `QuestionPoolManagerModal({open, onClose, onChanged}: Props)` plus a temporary named `PoolManagerModal` compatibility wrapper for the unreachable Release A source.
- UI covers create, Feishu rebind, rename, manual sync, `auto_sync_enabled` toggle and admin-only delete.
- `/ai` redirects to `/agents`; desktop and mobile navigation no longer expose `ai`.

- [ ] **Step 1: Add a static route/navigation assertion script**

```powershell
$route = Get-Content -Raw web/src/routes.tsx
$aiRedirectPattern = '(?s)\{\s*path:\s*["'']/?ai["'']\s*,\s*element:\s*<Navigate\s+to=["'']/agents["'']\s+replace\s*/>\s*\}'
if ($route -notmatch $aiRedirectPattern) { throw "missing /ai redirect" }
$navFiles = @("web/src/App.tsx", "web/src/components/MobileNav.tsx")
foreach ($file in $navFiles) {
  if ((Get-Content -Raw $file) -match '["'']ai["'']') { throw "ai navigation remains in $file" }
}
```

- [ ] **Step 2: Run the script and verify it fails**

Run: `powershell -File scripts/check-release-a-frontend.ps1`

Expected: FAIL because `/ai` still lazy-loads the old workspace and navigation still contains `ai`. Create `scripts/check-release-a-frontend.ps1` from the assertion above as part of this task.

- [ ] **Step 3: Move and extend the modal, then wire both agent entry points**

```tsx
export type QuestionPoolManagerModalProps = {
  open: boolean
  onClose: () => void
  onChanged: () => Promise<void> | void
}
```

Use `updateQuestionPool(id, {name, feishu_app_token, feishu_table_id, auto_sync_enabled})` for edits. Add “问题源管理” to `AgentManagementWorkspace` and “管理问题源 / 立即同步” beside the `question_source` node controls. Render delete only when the current user is admin. Keep the old modal file as a named compatibility wrapper during Release A because `GenerateTab.tsx` remains in the TypeScript compilation:

```tsx
import { QuestionPoolManagerModal } from "../pipelines/question-pools/QuestionPoolManagerModal"

export function PoolManagerModal({
  pools: _pools,
  onClose,
  onChanged,
}: {
  pools: QuestionPool[]
  onClose: () => void
  onChanged: () => void
}) {
  return (
    <QuestionPoolManagerModal open onClose={onClose} onChanged={onChanged} />
  )
}
```

Replace the old route with `<Navigate to="/agents" replace />`; remove AI navigation/title/type entries. Preserve every CSS selector still consumed by Pipeline or the moved modal.

- [ ] **Step 4: Run static, type, formatting and production-build gates**

Run: `powershell -File scripts/check-release-a-frontend.ps1`

Expected: PASS.

Run: `pnpm --filter @geo/web lint && pnpm --filter @geo/web typecheck && pnpm --filter @geo/web format:check && pnpm --filter @geo/web build`

Expected: PASS; `/ai` is unreachable except for the redirect.

- [ ] **Step 5: Commit**

```bash
git add scripts/check-release-a-frontend.ps1 web/src/features/pipelines/question-pools/QuestionPoolManagerModal.tsx web/src/features/pipelines/AgentManagementWorkspace.tsx web/src/features/pipelines/PipelineEditor.tsx web/src/features/ai-generation/PoolManagerModal.tsx web/src/routes.tsx web/src/App.tsx web/src/components/MobileNav.tsx web/src/types.ts web/src/styles.css
git commit -m "feat: move question source management into agents"
```

### Task 6: Make Every Scheme Mutation a Side-Effect-Free 410

**Files:**
- Modify: `server/app/modules/ai_generation/scheme_router.py`
- Modify: `server/app/modules/ai_generation/router.py`
- Modify: `server/tests/test_generation_schemes.py`
- Modify: `server/tests/test_scheme_runs.py`
- Modify: `server/tests/test_geo_slimming_release_a.py`

**Interfaces:**
- Produces: `_scheme_retired() -> NoReturn`, raising `HTTPException(410, "方案生文已停用，请使用智能体工作流")`.
- Read-only scheme list/detail/run history GET routes remain mounted.

- [ ] **Step 1: Parameterize all retired mutations and assert zero side effects**

```python
@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("post", "/api/generation/schemes", VALID_SCHEME),
        ("put", "/api/generation/schemes/1", VALID_SCHEME),
        ("patch", "/api/generation/schemes/1", {"name": "changed"}),
        ("delete", "/api/generation/schemes/1", None),
        ("post", "/api/generation/schemes/1/runs", {}),
    ],
)
def test_scheme_mutations_are_gone_without_writes(
    client, db, method, path, payload
):
    before = snapshot_scheme_counts_and_audits(db)
    response = getattr(client, method)(path, json=payload)
    assert response.status_code == 410
    assert "智能体" in response.json()["detail"]
    assert snapshot_scheme_counts_and_audits(db) == before
```

Also assert old `POST /api/generation/sessions` returns 410 text pointing to `/agents` or “智能体”, not “方案”.

- [ ] **Step 2: Run mutation tests and verify current CRUD/run execution fails the contract**

Run: `conda run -n geo_xzpt pytest server/tests/test_generation_schemes.py server/tests/test_scheme_runs.py server/tests/test_geo_slimming_release_a.py -q`

Expected: FAIL because the existing endpoints write and start background work.

- [ ] **Step 3: Replace mutation bodies with one immediate 410**

```python
def _scheme_retired() -> NoReturn:
    raise HTTPException(
        status_code=410,
        detail="方案生文已停用，请使用智能体工作流",
    )
```

Call `_scheme_retired()` before ownership lookup, audit logging, commit, thread creation or executor calls in every mutation handler. Update the old session 410 copy to the same destination.

- [ ] **Step 4: Run scheme tests and an app import smoke test**

Run: `conda run -n geo_xzpt pytest server/tests/test_generation_schemes.py server/tests/test_scheme_runs.py server/tests/test_geo_slimming_release_a.py -q`

Expected: PASS for 410 and read-only GET coverage.

Run: `conda run -n geo_xzpt python -c "from server.app.main import create_app; print('import-ok')"`

Expected: prints `import-ok`.

- [ ] **Step 5: Commit**

```bash
git add server/app/modules/ai_generation/scheme_router.py server/app/modules/ai_generation/router.py server/tests/test_generation_schemes.py server/tests/test_scheme_runs.py server/tests/test_geo_slimming_release_a.py
git commit -m "feat: retire generation scheme mutations"
```

### Task 7: Replace Template Performance Stub with Real Aggregation

**Files:**
- Modify: `server/app/modules/performance/service.py`
- Modify: `server/tests/test_performance.py`
- Modify: `server/tests/test_mcp_tools_registration.py`
- Modify: `server/tests/test_mcp_status_count.py`
- Modify: `claude-loops/weekly-report-loop.md`

**Interfaces:**
- Preserves: `get_template_performance(db: Session, template_id: int, window_days: int = 7) -> dict[str, Any]`.
- Aggregate source: `Article.source_template_id`, `Article.created_at`, `Article.metrics`, `Article.review_status`.
- Return fields remain unchanged; no rows yield `avg_views=None`, `avg_likes=None`, `approval_rate=None`.

- [ ] **Step 1: Add deterministic aggregate tests**

```python
def test_template_performance_aggregates_articles(db, template, utcnow):
    seed_article(db, template.id, utcnow, {"views": 100, "likes": 8}, "approved")
    seed_article(db, template.id, utcnow, {"views": 50}, "pending")
    seed_article(db, template.id, utcnow - timedelta(days=8), {"views": 900}, "approved")
    result = get_template_performance(db, template.id, window_days=7)
    assert result["article_count"] == 2
    assert result["avg_views"] == 75
    assert result["avg_likes"] == 8
    assert result["approval_rate"] == 0.5


def test_template_performance_empty_window_returns_null_averages(db, template):
    result = get_template_performance(db, template.id, window_days=7)
    assert result["article_count"] == 0
    assert result["avg_views"] is None
    assert result["avg_likes"] is None
    assert result["approval_rate"] is None
```

- [ ] **Step 2: Run tests and verify the stub fails**

Run: `conda run -n geo_xzpt pytest server/tests/test_performance.py -q`

Expected: FAIL because the service returns a POC stub.

- [ ] **Step 3: Implement the fixed aggregation definition**

```python
cutoff = utcnow() - timedelta(days=window_days)
articles = (
    db.query(Article)
    .filter(
        Article.source_template_id == template_id,
        Article.created_at >= cutoff,
    )
    .all()
)
views = [a.metrics["views"] for a in articles if a.metrics and a.metrics.get("views") is not None]
likes = [a.metrics["likes"] for a in articles if a.metrics and a.metrics.get("likes") is not None]
approval_rate = (
    sum(a.review_status == "approved" for a in articles) / len(articles)
    if articles
    else None
)
```

Return the existing response keys, using `sum(values) / len(values)` or `None`. Keep the HTTP handler, MCP tool, Chinese catalog item and weekly report calls unchanged. Assert `MCP_TOOLS_COUNT == 39` and registration count remains at least 39.

- [ ] **Step 4: Run performance, HTTP/MCP and weekly-report reference tests**

Run: `conda run -n geo_xzpt pytest server/tests/test_performance.py server/tests/test_mcp_tools_registration.py server/tests/test_mcp_status_count.py server/tests/test_mcp_connect.py -q`

Expected: PASS; `get_template_performance` remains registered.

Run: `rg -n "get_template_performance" server/mcp claude-loops/weekly-report-loop.md`

Expected: MCP registration and both weekly-report uses remain present.

- [ ] **Step 5: Commit**

```bash
git add server/app/modules/performance/service.py server/tests/test_performance.py server/tests/test_mcp_tools_registration.py server/tests/test_mcp_status_count.py claude-loops/weekly-report-loop.md
git commit -m "feat: implement template performance aggregation"
```

### Task 8: Add Release A Cross-Module Gates and Update Current Documentation

**Files:**
- Modify: `server/tests/test_geo_slimming_release_a.py`
- Modify: `CLAUDE.md`
- Modify: `docs/AI_GENERATION.md`
- Modify: `docs/project/02-product-design.md`
- Modify: `docs/project/05-api-reference.md`
- Create: `docs/runbooks/geo-slimming-release-a-observation.md`

**Interfaces:**
- Produces a repeatable test proving shared contracts survive before the observation period starts.
- Produces an observation record with daily UTC counts and an explicit `GO`/`NO-GO` conclusion.

- [ ] **Step 1: Add a protected-contract smoke matrix**

```python
def test_release_a_protected_routes(app):
    paths = {route.path for route in app.routes}
    required = {
        "/api/generation/question-pools",
        "/api/generation/ai-engines",
        "/api/generation/format-engines",
        "/api/mcp/loop-skill-bundle/info",
        "/api/prompt-templates/{template_id}/performance",
    }
    assert required <= paths


def test_release_a_mcp_tools_stay_registered():
    from server.mcp.server import mcp

    names = set(mcp._tool_manager._tools)
    assert {
        "list_question_pools",
        "list_question_items",
        "save_article",
        "get_template_performance",
        "get_account_performance",
        "record_publish_metrics",
        "install_loop_skills",
    } <= names
```

- [ ] **Step 2: Run the cross-module smoke tests**

Run: `conda run -n geo_xzpt pytest server/tests/test_geo_slimming_release_a.py server/tests/test_skill_library_compat.py server/tests/test_mcp_tools_registration.py server/tests/test_articles_api.py -q`

Expected: PASS. Any missing protected route/tool is a Release A blocker.

- [ ] **Step 3: Update truth documents and write the exact observation query set**

Document Pipeline as the sole in-product generation path, MCP as the external path, `/ai` as redirect-only, active question semantics, and template performance as implemented.

The runbook must record daily:

```sql
SELECT action, COUNT(*), MAX(created_at)
FROM audit_logs
WHERE action LIKE 'generation_scheme.%'
  AND created_at >= UTC_TIMESTAMP() - INTERVAL 7 DAY
GROUP BY action;

SELECT status, COUNT(*) FROM generation_scheme_runs GROUP BY status;
SELECT status, COUNT(*) FROM generation_scheme_run_tasks GROUP BY status;
SELECT COUNT(*), MAX(updated_at) FROM generation_schemes;
```

Each day uses the machine-parseable heading regex `^## Observation \d{4}-\d{2}-\d{2} UTC$` and contains these exact labels: `scheme_counts`, `scheme_410_count`, `scheme_get_count`, `shared_endpoint_health`, `pipeline_smoke`, `mcp_smoke`, `goal_skill_smoke`, `article_smoke`, `runtime_error_scan`. It must also record nginx counts for scheme GET/410 writes, engine endpoints, question pools and Pipeline; one manual Pipeline run; MCP question read/save; weekly report; goal ZIP/SHA/install; Article feed/detail/publish; and app/worker/scheduler error scans. End with exactly one decision line: `Release B decision: GO` or `Release B decision: NO-GO`.

- [ ] **Step 4: Run full static and build verification**

Run: `conda run -n geo_xzpt ruff check server/ && conda run -n geo_xzpt ruff format --check server/ && conda run -n geo_xzpt mypy server/app`

Expected: PASS.

Run: `pnpm --filter @geo/web lint && pnpm --filter @geo/web typecheck && pnpm --filter @geo/web format:check && pnpm --filter @geo/web build`

Expected: PASS.

Run: `conda run -n geo_xzpt pytest server/tests/test_geo_slimming_release_a.py server/tests/test_runtime_template_resolution.py server/tests/test_question_bank.py server/tests/test_question_pool_sync.py server/tests/test_mcp_catalog.py server/tests/test_performance.py server/tests/test_skill_library_compat.py server/tests/test_pipeline_node_types.py -q`

Expected: PASS. If the repository-wide suite has an existing unrelated failure, attach before/after output proving this change introduces no new failure.

- [ ] **Step 5: Commit**

```bash
git add server/tests/test_geo_slimming_release_a.py CLAUDE.md docs/AI_GENERATION.md docs/project/02-product-design.md docs/project/05-api-reference.md docs/runbooks/geo-slimming-release-a-observation.md
git commit -m "docs: define release a slimming observation gate"
```

## Release A Completion Gate

- [ ] Deploy Release A through the normal `geo-release` flow; do not run Release B migrations.
- [ ] Observe at least 7 consecutive natural days after the deployment timestamp.
- [ ] Confirm no pending/running scheme run exists; the five historical pending tasks under failed run 14 remain historical and are not rewritten.
- [ ] Confirm scheme mutation requests return 410 and create no rows/audits.
- [ ] Confirm non-manual scheme GET traffic stops after `/ai` is hidden.
- [ ] Confirm engine, question-pool, Pipeline, MCP, Article, skill-library and weekly-report checks pass.
- [ ] Record `Release B decision: GO`; any missing evidence or regression requires `NO-GO`.
