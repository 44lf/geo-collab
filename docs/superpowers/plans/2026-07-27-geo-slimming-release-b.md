# GEO Slimming Release B Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Release A 七日观察通过后，可恢复地归档并删除旧方案、session、Skill、消费队列、标签和 loop bundle 存储，同时证明所有受保护模块继续正常运行。

**Architecture:** Release B 先生成带 schema、行数、哈希和关系闭合证据的离线归档，并在三类非生产 MySQL 环境验证恢复；随后用两条只向前新增的 Alembic migration 删除结构。代码、ORM、路由和前端删除与迁移同步完成，AST、ORM metadata、路由、MCP、Article feed、skill seed 和全链路测试共同阻止悬空引用。

**Tech Stack:** Python 3.11、FastAPI、SQLAlchemy 2、Alembic、MySQL 8、pytest、React 19、TypeScript、Vite、PowerShell、JSONL、SHA-256。

## Global Constraints

- 只有 `docs/runbooks/geo-slimming-release-a-observation.md` 明确记录连续 7 个自然日且结论为 `Release B decision: GO` 才能执行。
- dev `alembic_version=0067_game_cull_and_manual` 不在仓库中；禁止对 dev 直接 `stamp` 或 `upgrade`。
- dev 的目标关系和数据是删除设计基线；生产只允许只读校验，正式发布前不得修改、迁移或重启生产。
- 不修改或重写任何历史 Alembic migration，只新增不超过 `alembic_version.version_num VARCHAR(32)` 上限的 revision `0073_geo_slim_legacy_gen` 和 `0074_geo_slim_tags_loop`。
- Migration 1 删除方案、session、旧 Skill、CategoryUsage 和 `question_items` 遗留列；Migration 2 删除文章标签和可选旧 loop bundle 表。
- 只有 `loop_skill_bundle_versions` 可用 `has_table()` 条件处理；其他目标缺失必须报错并阻断。
- 离线归档和一次性 MySQL 恢复验证必须先于任何结构删除。
- `/api/generation/question-pools/*`、engine URLs、MCP tool 名/参数、loop bundle 兼容路由、Article 行为和 image-library 能力保持不变。
- `MCP_TOOLS_COUNT` 保持 39；`get_template_performance` 保留真实实现。
- `skill_library_skills`、`skill_library_versions`、官方 `goal`、`QuestionPool`、`QuestionItem` 和 image-library 数据模型不得删除。
- downgrade 只重建空结构；数据回滚必须使用归档。
- MySQL 演练和测试库名称必须包含 `test`，不得把 `GEO_ALLOW_NON_TEST_DATABASE_FOR_TESTS=1` 指向生产。

---

## File Map

- `server/scripts/archive_geo_slimming.py`：只读导出 schema、JSONL 和 manifest。
- `server/scripts/restore_geo_slimming_archive.py`：只向一次性库恢复归档并核验。
- `server/tests/test_geo_slimming_archive.py`：归档、哈希、关系闭合和恢复测试。
- `server/tests/test_geo_slimming_boundaries.py`：禁用 import、ORM metadata、路由、MCP 和保留表门禁。
- `server/alembic/versions/0073_geo_slim_legacy_gen.py`：第一组旧生成结构删除。
- `server/alembic/versions/0074_geo_slim_tags_loop.py`：文章标签和可选 loop bundle 删除。
- `server/scripts/seed_skill_library.py`：不依赖旧 bundle 表的官方 `goal` bootstrap。

### Task 1: Enforce the Release B Preflight Gate

**Files:**
- Create: `server/scripts/check_geo_slimming_release_b.py`
- Create: `server/tests/test_geo_slimming_preflight.py`
- Modify: `docs/runbooks/geo-slimming-release-a-observation.md`

**Interfaces:**
- Produces: `check_release_b_preflight(db: Session, observation_path: Path) -> PreflightReport`.
- Produces: `parse_observation(text: str) -> tuple[Literal["GO", "NO-GO"], tuple[date, ...]]`, parsing the final decision and every daily UTC evidence heading.
- Produces: `validate_daily_check_sections(path: Path) -> bool`, requiring `scheme_counts`, `scheme_410_count`, `scheme_get_count`, `shared_endpoint_health`, `pipeline_smoke`, `mcp_smoke`, `goal_skill_smoke`, `article_smoke` and `runtime_error_scan` for every recorded date.
- `PreflightReport.allowed` is true only when observation says GO, duration is at least 7 days, and no active scheme run exists.
- Pending tasks whose parent run is already terminal are queried and recorded generically but do not count as active runs; the dev fixture specifically expects five under failed run 14.

- [ ] **Step 1: Write fail-closed preflight tests**

```python
@pytest.mark.parametrize(
    ("decision", "daily_dates", "active_runs", "allowed"),
    [
        ("NO-GO", consecutive_dates(8), 0, False),
        ("GO", consecutive_dates(6), 0, False),
        ("GO", consecutive_dates(8), 1, False),
        ("GO", consecutive_dates(8), 0, True),
        ("GO", consecutive_dates(8, omit_day=4), 0, False),
    ],
)
def test_release_b_preflight_is_fail_closed(
    db, observation_file, decision, daily_dates, active_runs, allowed
):
    write_observation(
        observation_file,
        decision=decision,
        daily_dates=daily_dates,
        include_required_checks=True,
    )
    seed_active_runs(db, active_runs)
    report = check_release_b_preflight(db, observation_file)
    assert report.allowed is allowed
```

- [ ] **Step 2: Run the tests and verify the checker is missing**

Run: `conda run -n geo_xzpt pytest server/tests/test_geo_slimming_preflight.py -q`

Expected: FAIL because `check_geo_slimming_release_b.py` does not exist.

- [ ] **Step 3: Implement the read-only checker**

```python
@dataclass(frozen=True)
class PreflightReport:
    allowed: bool
    observed_days: int
    active_run_count: int
    terminal_parent_pending_task_count: int
    reasons: tuple[str, ...]


def check_release_b_preflight(db: Session, observation_path: Path) -> PreflightReport:
    decision, daily_dates = parse_observation(
        observation_path.read_text(encoding="utf-8")
    )
    observed_days = len(daily_dates)
    dates_are_consecutive = all(
        right - left == timedelta(days=1)
        for left, right in pairwise(daily_dates)
    )
    required_checks_present = validate_daily_check_sections(observation_path)
    active = db.scalar(
        text(
            "SELECT COUNT(*) FROM generation_scheme_runs "
            "WHERE status IN ('pending', 'running')"
        )
    )
    terminal_parent_pending_tasks = db.scalar(
        text(
            "SELECT COUNT(*) FROM generation_scheme_run_tasks t "
            "JOIN generation_scheme_runs r ON r.id = t.run_id "
            "WHERE t.status = 'pending' AND r.status IN ('done', 'failed')"
        )
    )
    reasons: list[str] = []
    if decision != "GO":
        reasons.append("observation decision is not GO")
    if observed_days < 7:
        reasons.append("observation period is shorter than seven days")
    if not dates_are_consecutive:
        reasons.append("daily evidence dates are not consecutive")
    if not required_checks_present:
        reasons.append("one or more daily health/count sections are missing")
    if active:
        reasons.append(f"{active} active scheme runs remain")
    allowed = (
        decision == "GO"
        and observed_days >= 7
        and dates_are_consecutive
        and required_checks_present
        and active == 0
    )
    return PreflightReport(
        allowed,
        observed_days,
        active,
        terminal_parent_pending_tasks,
        tuple(reasons),
    )
```

The CLI exits 0 only when `allowed` is true and prints the generic terminal-parent/pending-task count as a warning; only the dev-shaped rehearsal test asserts that this count is exactly five and belongs to failed run 14.

- [ ] **Step 4: Run tests and execute the checker against a non-production database**

Run: `conda run -n geo_xzpt pytest server/tests/test_geo_slimming_preflight.py -q`

Expected: PASS.

Run: `conda run -n geo_xzpt python -m server.scripts.check_geo_slimming_release_b --observation docs/runbooks/geo-slimming-release-a-observation.md`

Expected: exit 0 only after the real seven-day runbook is complete; otherwise stop Release B here.

- [ ] **Step 5: Commit**

```bash
git add server/scripts/check_geo_slimming_release_b.py server/tests/test_geo_slimming_preflight.py docs/runbooks/geo-slimming-release-a-observation.md
git commit -m "feat: add release b fail-closed preflight"
```

### Task 2: Build a Deterministic, Verifiable Offline Archive

**Files:**
- Create: `server/scripts/archive_geo_slimming.py`
- Create: `server/tests/test_geo_slimming_archive.py`
- Modify: `docs/DEPLOYMENT.md`

**Interfaces:**
- Produces: `archive_slimming_tables(engine: Engine, output_root: Path, *, deploy_commit: str, image_version: str) -> Path`.
- Archive directory: `${GEO_DATA_DIR}/exports/slimming/YYYYMMDDTHHMMSSZ/`, generated with `datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")`.
- Tables: `generation_schemes`, `generation_scheme_lines`, `generation_scheme_line_questions`, `generation_scheme_runs`, `generation_scheme_run_tasks`, `generation_sessions`, `skills`, `category_usages`, `tags`, `article_tags`, `loop_skill_bundle_versions`.

- [ ] **Step 1: Write archive shape, absent-table and hash tests**

```python
def test_archive_records_present_and_absent_tables(engine, tmp_path):
    archive = archive_slimming_tables(
        engine, tmp_path, deploy_commit="abc123", image_version="test"
    )
    manifest = json.loads((archive / "manifest.json").read_text())
    assert manifest["tables"]["generation_schemes"]["source_state"] == "present"
    assert manifest["tables"]["loop_skill_bundle_versions"]["source_state"] == "absent"
    assert (archive / "loop_skill_bundle_versions.jsonl").read_bytes() == b""
    for name, info in manifest["tables"].items():
        payload = (archive / f"{name}.jsonl").read_bytes()
        assert hashlib.sha256(payload).hexdigest() == info["sha256"]
        assert payload.count(b"\n") == info["row_count"]
```

Add assertions for DB name, Alembic revision, commit, image version, UTC timestamp, exporter version, byte count, min/max timestamp, archive-internal FK closure, external FKs into preserved parent tables and the five pending tasks whose parent run is failed. The manifest must distinguish `internal_fk_checks` from `external_fk_checks` so restore prerequisites are explicit.

- [ ] **Step 2: Run tests and verify the exporter is missing**

Run: `conda run -n geo_xzpt pytest server/tests/test_geo_slimming_archive.py -q`

Expected: FAIL because the archive script does not exist.

- [ ] **Step 3: Implement schema and JSONL export with native JSON values**

```python
TABLES = (
    "generation_schemes",
    "generation_scheme_lines",
    "generation_scheme_line_questions",
    "generation_scheme_runs",
    "generation_scheme_run_tasks",
    "generation_sessions",
    "skills",
    "category_usages",
    "tags",
    "article_tags",
    "loop_skill_bundle_versions",
)


def _json_value(value: Any, column_type: TypeEngine[Any]) -> Any:
    if isinstance(column_type, sa.JSON) and isinstance(value, str):
        return json.loads(value)
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC).isoformat()
    if isinstance(value, (dict, list, int, float, bool)) or value is None:
        return value
    return str(value)
```

Use SQLAlchemy inspection and parameter-free `SELECT *`. Build `ORDER BY` from every quoted name in `inspector.get_pk_constraint(table)["constrained_columns"]`, preserving inspector order and failing if the list is empty. Composite-key tables such as `category_usages` and `article_tags` must sort by every PK column for stable JSONL hashes. Pass each inspected column type into `_json_value()` so MySQL JSON text becomes a JSON object/list rather than a second JSON string. Write one compact UTF-8 JSON object plus newline per row. Export `SHOW CREATE TABLE` into `schema.sql`. Compute hashes after closing files. Never log row payloads.

- [ ] **Step 4: Run archive tests and a dev read-only dry run**

Run: `conda run -n geo_xzpt pytest server/tests/test_geo_slimming_archive.py -q`

Expected: PASS.

Run: `conda run -n geo_xzpt python -m server.scripts.archive_geo_slimming --output-root "$env:GEO_DATA_DIR/exports/slimming" --deploy-commit "$(git rev-parse HEAD)" --image-version "dev-audit"`

Expected: writes one archive directory; dev manifest contains counts `13/143/464/17/154/7/6/0/0/0`, and marks loop table absent.

- [ ] **Step 5: Commit**

```bash
git add server/scripts/archive_geo_slimming.py server/tests/test_geo_slimming_archive.py docs/DEPLOYMENT.md
git commit -m "feat: archive legacy generation data before removal"
```

### Task 3: Restore and Reconcile an Archive in a Disposable MySQL Database

**Files:**
- Create: `server/scripts/restore_geo_slimming_archive.py`
- Modify: `server/tests/test_geo_slimming_archive.py`
- Modify: `docs/DEPLOYMENT.md`

**Interfaces:**
- Produces: `restore_slimming_archive(engine: Engine, archive_dir: Path, *, allow_production_restore: bool = False, expected_database: str | None = None, expected_manifest_sha256: str | None = None) -> RestoreReport`.
- Defaults to refusing non-test database names and refuses a checksum, row-count, schema or FK mismatch.
- Production restore additionally requires `allow_production_restore=True`, an exact expected database name and the expected manifest SHA-256; the runbook requires two-person approval before using this mode.

- [ ] **Step 1: Add round-trip and tamper tests**

```python
def test_archive_restores_rows_json_and_relationships(source_engine, target_engine, tmp_path):
    archive = archive_slimming_tables(
        source_engine, tmp_path, deploy_commit="abc123", image_version="test"
    )
    report = restore_slimming_archive(target_engine, archive)
    assert report.row_counts == load_manifest(archive)["row_counts"]
    assert report.fk_closure_ok is True
    assert report.json_values_match is True


def test_restore_rejects_tampered_jsonl(target_engine, archive):
    with (archive / "skills.jsonl").open("ab") as handle:
        handle.write(b"{}\n")
    with pytest.raises(ArchiveIntegrityError, match="sha256"):
        restore_slimming_archive(target_engine, archive)


def test_restore_rejects_production_without_explicit_proof():
    with pytest.raises(ArchiveIntegrityError, match="production restore approval"):
        _validate_restore_target(
            FakeEngine("geo_collab"),
            allow_production_restore=False,
            expected_database=None,
            expected_manifest_sha256=None,
            actual_manifest_sha256="a" * 64,
        )


def test_restore_accepts_downgrade_created_empty_tables(
    source_engine, downgraded_target_engine, tmp_path
):
    archive = archive_slimming_tables(
        source_engine, tmp_path, deploy_commit="abc123", image_version="test"
    )
    report = restore_slimming_archive(downgraded_target_engine, archive)
    assert report.row_counts_match is True


def test_restore_rejects_mixed_table_presence(mixed_target_engine, archive):
    with pytest.raises(ArchiveIntegrityError, match="mixed target table state"):
        restore_slimming_archive(mixed_target_engine, archive)
```

- [ ] **Step 2: Run tests and verify restore support is missing**

Run: `conda run -n geo_xzpt pytest server/tests/test_geo_slimming_archive.py -q`

Expected: FAIL because `restore_geo_slimming_archive.py` does not exist.

- [ ] **Step 3: Implement fail-closed restore**

```python
def _validate_restore_target(
    engine: Engine,
    *,
    allow_production_restore: bool,
    expected_database: str | None,
    expected_manifest_sha256: str | None,
    actual_manifest_sha256: str,
) -> None:
    db_name = engine.url.database or ""
    if "test" in db_name.lower():
        return
    if not (
        allow_production_restore
        and expected_database == db_name
        and expected_manifest_sha256 == actual_manifest_sha256
    ):
        raise ArchiveIntegrityError("production restore approval proof is incomplete")
```

Verify every file hash and count before executing DDL. The target must already contain the protected parent schema and matching parent rows (`users`, `prompt_templates`, `question_pools`, `question_items`, `articles`); for rehearsal, use a non-production clone upgraded to `0074`, not a truly empty database. Support exactly two target states: all archived tables absent, in which case create them from `schema.sql`; or all expected archived tables present but empty after downgrade, in which case compare normalized `SHOW CREATE TABLE` output before inserting. Reject a mixed state, nonempty target table or schema mismatch. Insert in parent-before-child order, preserve JSON objects/lists as JSON, and rerun both archive-internal FK checks and external-FK-to-protected-table checks. Return exact source/target counts.

- [ ] **Step 4: Run tests and perform a disposable restore**

Run: `conda run -n geo_xzpt pytest server/tests/test_geo_slimming_archive.py -q`

Expected: PASS.

Run: `conda run -n geo_xzpt python -m server.scripts.restore_geo_slimming_archive --archive "$env:GEO_SLIMMING_ARCHIVE_DIR" --database-url "$env:GEO_SLIMMING_RESTORE_TEST_DATABASE_URL"`

Expected: against a protected-parent clone upgraded to `0074`, prints `row_counts_match=true`, `sha256_match=true`, `internal_fk_closure=true`, `external_fk_closure=true`, `json_values_match=true`.

- [ ] **Step 5: Commit**

```bash
git add server/scripts/restore_geo_slimming_archive.py server/tests/test_geo_slimming_archive.py docs/DEPLOYMENT.md
git commit -m "test: verify legacy archive restore"
```

### Task 4: Remove the Retired Frontend and Preserve Shared Consumers

**Files:**
- Delete: `web/src/features/ai-generation/AiGenerationWorkspace.tsx`
- Delete: `web/src/features/ai-generation/GenerateTab.tsx`
- Delete: `web/src/features/ai-generation/SchemeEditorModal.tsx`
- Delete: `web/src/features/ai-generation/RunDetailModal.tsx`
- Delete: `web/src/features/ai-generation/PoolManagerModal.tsx`
- Delete: `web/src/features/image-library/ImageLibraryWorkspace.tsx`
- Delete: `web/src/api/ai-generation.ts`
- Modify: `web/src/api/client.ts`
- Modify: `web/src/types.ts`
- Modify: `web/src/routes.tsx`
- Modify: `web/src/styles.css`
- Create: `scripts/check-geo-slimming-frontend.ps1`

**Interfaces:**
- Preserves `api/question-pools.ts`, `api/generation-engines.ts`, `api/image-library.ts`.
- Preserves `QuestionPool`, `QuestionType`, `AiEngine`, `StockCategory`, `StockImage`.
- Preserves all CSS selectors referenced by Pipeline and `QuestionPoolManagerModal`.

- [ ] **Step 1: Add a static reference and protected-file gate**

```powershell
$forbidden = @(
  "AiGenerationWorkspace",
  "GenerateTab",
  "SchemeEditorModal",
  "RunDetailModal",
  "api/ai-generation"
)
foreach ($name in $forbidden) {
  $hit = rg -n $name web/src
  if ($LASTEXITCODE -eq 0) { throw "retired frontend reference: $hit" }
}
$retiredFiles = @(
  "web/src/features/ai-generation/PoolManagerModal.tsx",
  "web/src/features/image-library/ImageLibraryWorkspace.tsx"
)
foreach ($path in $retiredFiles) {
  if (Test-Path $path) { throw "retired file still exists: $path" }
}
$required = @(
  "web/src/api/question-pools.ts",
  "web/src/api/generation-engines.ts",
  "web/src/api/image-library.ts",
  "web/src/features/pipelines/question-pools/QuestionPoolManagerModal.tsx"
)
foreach ($path in $required) {
  if (-not (Test-Path $path)) { throw "protected file missing: $path" }
}
```

- [ ] **Step 2: Run the gate and verify retired references are found**

Run: `powershell -File scripts/check-geo-slimming-frontend.ps1`

Expected: FAIL and list the old files/references.

- [ ] **Step 3: Delete only retired files, types and exclusive CSS**

Remove scheme/session types and API exports. Remove only CSS selectors whose repository-wide reference count becomes zero. Keep `schemeEmpty`, `schemeLine*`, `schemeChip*`, `schemeLink`, `schemePanel*`, `schemeCard*` and shared `ai*` selectors used outside the retired page. Keep the `/ai` redirect.

- [ ] **Step 4: Run static, lint, type and build gates**

Run: `powershell -File scripts/check-geo-slimming-frontend.ps1`

Expected: PASS.

Run: `pnpm --filter @geo/web lint && pnpm --filter @geo/web typecheck && pnpm --filter @geo/web format:check && pnpm --filter @geo/web build`

Expected: PASS; Pipeline editor, question source management, editor image save and game materials compile.

- [ ] **Step 5: Commit**

```bash
git add -A web/src scripts/check-geo-slimming-frontend.ps1
git commit -m "refactor: remove retired generation frontend"
```

### Task 5: Remove Scheme, Session and Old Skill Runtime Code

**Files:**
- Delete: `server/app/modules/ai_generation/scheme_router.py`
- Delete: `server/app/modules/ai_generation/scheme_service.py`
- Delete: `server/app/modules/ai_generation/scheme_executor.py`
- Delete: `server/app/modules/ai_generation/pipeline.py`
- Delete: `server/app/modules/ai_generation/service.py`
- Delete: `server/app/modules/skills/`
- Modify: `server/app/main.py`
- Modify: `server/app/modules/ai_generation/models.py`
- Modify: `server/app/modules/ai_generation/question_bank.py`
- Modify: `server/app/modules/ai_generation/router.py`
- Modify: `server/app/modules/ai_generation/schemas.py`
- Modify: `server/app/core/config.py`
- Modify: `server/alembic/env.py`
- Modify: `server/scripts/seed_users.py`
- Modify: `server/scripts/encrypt_secrets.py`
- Modify: `server/scripts/repair_article_escaped_quotes.py`
- Modify: `server/tests/utils.py`
- Create: `server/tests/test_geo_slimming_boundaries.py`
- Delete or trim: scheme/session-only tests under `server/tests/`

**Interfaces:**
- Preserves `runtime_templates.pick_valid_template`, question-pool router, sync scheduler, article writer, converter and all Pipeline node registration.
- Removes old `/api/generation/schemes*`, `/scheme-runs*`, `/sessions*` routes.

- [ ] **Step 1: Add the full forbidden-import and route contract gate**

```python
FORBIDDEN = {
    "server.app.modules.ai_generation.scheme_router",
    "server.app.modules.ai_generation.scheme_service",
    "server.app.modules.ai_generation.scheme_executor",
    "server.app.modules.ai_generation.pipeline",
    "server.app.modules.ai_generation.service",
    "server.app.modules.skills",
    "server.app.modules.loop_skills.versions_service",
}


def scan_python_imports(roots: list[Path], *, exclude: Path) -> set[str]:
    imported: set[str] = set()
    for root in roots:
        paths = root.rglob("*.py") if root.is_dir() else [root]
        for path in paths:
            if exclude == path or exclude in path.parents:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imported.add(node.module or "")
    return imported


def test_current_code_has_no_retired_imports():
    roots = [
        Path("server/app"),
        Path("server/mcp"),
        Path("server/scripts"),
        Path("server/worker"),
        Path("server/alembic/env.py"),
        Path("server/tests/utils.py"),
    ]
    imported = scan_python_imports(roots, exclude=Path("server/alembic/versions"))
    hits = {
        name
        for name in imported
        if any(name == retired or name.startswith(f"{retired}.") for retired in FORBIDDEN)
    }
    assert hits == set()


def test_retired_routes_are_absent_and_shared_routes_remain(app):
    paths = {route.path for route in app.routes}
    assert not any("/schemes" in path or "/scheme-runs" in path or "/sessions" in path for path in paths)
    assert {
        "/api/generation/question-pools",
        "/api/generation/ai-engines",
        "/api/generation/format-engines",
    } <= paths
```

- [ ] **Step 2: Run the gate and verify current imports/routes fail**

Run: `conda run -n geo_xzpt pytest server/tests/test_geo_slimming_boundaries.py -q`

Expected: FAIL with imports from `main.py`, Pipeline, scripts/test setup and mounted old routes.

- [ ] **Step 3: Remove the complete runtime dependency chain**

Delete old module files and scheme/session schemas. From `question_bank.py`, remove `mark_consumed`, `mark_items_consumed`, `list_categories_for_auto`, `auto_pick_groups`, `mark_category_used`, `format_question_group` and `group_items_by_category` in the same commit so no removed ORM class remains imported. From `ai_generation/models.py`, remove `GenerationSession`, `CategoryUsage`, `GenerationScheme`, `GenerationSchemeLine`, `GenerationSchemeLineQuestion`, `GenerationSchemeRun` and `GenerationSchemeRunTask`, while preserving `QuestionPool` and `QuestionItem`. Remove `main.py` top-level scheme import, startup recovery, router mount and background session injection. Remove scheme concurrency config and inaccurate comments. Remove every old Skill model-registration import listed in Files. Delete only tests exclusively exercising retired behavior; migrate shared assertions to `test_geo_slimming_boundaries.py`, `test_runtime_template_resolution.py`, `test_ai_models_api.py` or `test_question_bank.py`.

- [ ] **Step 4: Run import, route, Pipeline and script smoke tests**

Run: `conda run -n geo_xzpt pytest server/tests/test_geo_slimming_boundaries.py server/tests/test_runtime_template_resolution.py server/tests/test_ai_models_api.py server/tests/test_pipeline_node_types.py server/tests/test_question_bank.py -q`

Expected: PASS.

Run: `conda run -n geo_xzpt python -c "import server.app.main, server.scripts.seed_users, server.scripts.encrypt_secrets, server.scripts.repair_article_escaped_quotes; print('imports-ok')"`

Expected: prints `imports-ok`.

Run: `conda run -n geo_xzpt alembic heads`

Expected: Alembic loads `env.py` in its supported context and reports exactly one head.

- [ ] **Step 5: Commit**

```bash
git add -A server/app server/alembic/env.py server/scripts server/tests
git commit -m "refactor: remove retired generation runtime"
```

### Task 6: Remove Old Question Consumption Columns from Current Code

**Files:**
- Modify: `server/app/modules/ai_generation/models.py`
- Modify: `server/app/modules/ai_generation/question_bank.py`
- Modify: `server/app/modules/ai_generation/schemas.py`
- Modify: `server/tests/test_question_bank.py`
- Modify: `server/tests/test_save_article_mcp.py`
- Modify: `server/tests/test_geo_slimming_boundaries.py`

**Interfaces:**
- Preserves `QuestionItem`, `source_active`, category/text/source record fields and fixed compatibility DTO.
- Removes ORM columns `status`, `article_id`, check/index declarations and old consumption helpers.

- [ ] **Step 1: Add ORM metadata and API compatibility assertions**

```python
def test_question_item_metadata_has_only_active_availability():
    table = Base.metadata.tables["question_items"]
    assert "status" not in table.c
    assert "article_id" not in table.c
    assert "source_active" in table.c
    assert "ix_question_items_source_active" in {idx.name for idx in table.indexes}


def test_question_item_dto_keeps_compatibility_fields(client):
    row = client.get("/api/generation/question-pools/1/items").json()[0]
    assert row["status"] == "pending"
    assert row["article_id"] is None
```

- [ ] **Step 2: Run tests and verify metadata still exposes legacy columns**

Run: `conda run -n geo_xzpt pytest server/tests/test_question_bank.py server/tests/test_geo_slimming_boundaries.py -q`

Expected: FAIL because ORM metadata contains `status` and `article_id`.

- [ ] **Step 3: Remove columns and session-only queue functions**

Remove the two columns and their ORM check/index declarations. The old consumption helpers were already deleted atomically with their ORM classes in Task 5. Keep explicit `QuestionItemRead` projection fields so external DTO remains compatible.

- [ ] **Step 4: Run question-pool, Pipeline question source and MCP save tests**

Run: `conda run -n geo_xzpt pytest server/tests/test_question_bank.py server/tests/test_question_pool_sync.py server/tests/test_question_source_multiselect.py server/tests/test_mcp_catalog.py server/tests/test_save_article_mcp.py server/tests/test_geo_slimming_boundaries.py -q`

Expected: PASS without physical legacy columns in ORM metadata.

- [ ] **Step 5: Commit**

```bash
git add server/app/modules/ai_generation/models.py server/app/modules/ai_generation/question_bank.py server/app/modules/ai_generation/schemas.py server/tests/test_question_bank.py server/tests/test_save_article_mcp.py server/tests/test_geo_slimming_boundaries.py
git commit -m "refactor: remove legacy question consumption state"
```

### Task 7: Remove Article Tags and Old Loop Bundle Storage Safely

**Files:**
- Modify: `server/app/modules/articles/models.py`
- Modify: `server/app/modules/articles/services/feed.py`
- Modify: `server/app/modules/loop_skills/models.py`
- Delete: `server/app/modules/loop_skills/versions_service.py`
- Modify: `server/scripts/seed_skill_library.py`
- Modify: `server/tests/test_skill_library_seed.py`
- Delete or trim: `server/tests/test_loop_skill_bundle_versions.py`
- Modify: `server/tests/test_skill_library_compat.py`
- Modify: `server/tests/test_geo_slimming_boundaries.py`

**Interfaces:**
- Preserves `Skill`, `SkillVersion`, `skill_service`, `skill_router` and compatibility `loop_skills/router.py`.
- `seed_skill_library(session: Session) -> None` seeds official `goal` directly from `build_bundle()` even when `loop_skill_bundle_versions` does not exist.
- Article feed does not access `Article.tags`.

- [ ] **Step 1: Add metadata, seed-without-old-table and feed tests**

```python
def test_retired_tag_and_bundle_metadata_is_absent():
    assert {"tags", "article_tags", "loop_skill_bundle_versions"}.isdisjoint(
        Base.metadata.tables
    )
    assert "tags" not in inspect(Article).relationships
    assert {"skill_library_skills", "skill_library_versions"} <= Base.metadata.tables.keys()


def test_fresh_database_seeds_goal_without_old_bundle_table(db):
    assert not inspect(db.bind).has_table("loop_skill_bundle_versions")
    seed_skill_library(db)
    goal = db.scalar(
        select(Skill).where(
            Skill.slug == "goal",
            Skill.is_official.is_(True),
            Skill.is_deleted.is_(False),
        )
    )
    assert goal is not None
    assert goal.current_version_id is not None


def test_seed_recreates_goal_when_only_soft_deleted_row_exists(db):
    seed_soft_deleted_goal(db)
    seed_skill_library(db)
    active = db.scalars(
        select(Skill).where(
            Skill.slug == "goal",
            Skill.is_official.is_(True),
            Skill.is_deleted.is_(False),
        )
    ).all()
    assert len(active) == 1
    assert active[0].current_version_id is not None


def test_article_feed_runs_without_tag_tables(client):
    response = client.get("/api/articles")
    assert response.status_code == 200
```

- [ ] **Step 2: Run tests and verify current metadata/seed/feed references fail**

Run: `conda run -n geo_xzpt pytest server/tests/test_skill_library_seed.py server/tests/test_skill_library_compat.py server/tests/test_articles_api.py server/tests/test_geo_slimming_boundaries.py -q`

Expected: FAIL because old ORM objects and seed imports still exist.

- [ ] **Step 3: Remove only obsolete objects and seed from the canonical bundle**

```python
def seed_skill_library(session: Session) -> None:
    existing = session.scalar(
        select(Skill).where(
            Skill.slug == "goal",
            Skill.is_official.is_(True),
            Skill.is_deleted.is_(False),
        )
    )
    if existing is not None:
        return
    bundle = build_bundle()
    skill = Skill(
        name=OFFICIAL_NAME,
        slug=OFFICIAL_SLUG,
        is_official=True,
        created_by=None,
        category="generation",
    )
    session.add(skill)
    session.flush()
    version = SkillVersion(
        skill_id=skill.id,
        version_label=LOOP_SKILL_BUNDLE_VERSION,
        bundle_sha256=bundle.bundle_sha256,
        file_count=len(bundle.files),
        total_bytes=sum(item.size for item in bundle.files),
        storage_backend="db",
        files=[
            {
                "path": item.path,
                "content": item.content,
                "sha256": item.sha256,
                "size": item.size,
            }
            for item in bundle.files
        ],
        storage_key=None,
        uploaded_by=None,
    )
    session.add(version)
    session.flush()
    skill.current_version_id = version.id
    session.flush()
```

Delete `Tag`, `ArticleTag`, `Article.tags` and all three `lazyload(Article.tags)` options. Delete only `LoopSkillBundleVersion` from the mixed loop model file. Keep compatibility router tests and update them to seed/read the new skill library.

- [ ] **Step 4: Run Article, skill, compatibility and MCP tests**

Run: `conda run -n geo_xzpt pytest server/tests/test_articles_api.py server/tests/test_articles_feed.py server/tests/test_skill_library_seed.py server/tests/test_skill_library_compat.py server/tests/test_loop_skill_bundle.py server/tests/test_mcp_tools_registration.py server/tests/test_geo_slimming_boundaries.py -q`

Expected: PASS; compatibility bundle routes return goal data from the new library.

- [ ] **Step 5: Commit**

```bash
git add -A server/app/modules/articles server/app/modules/loop_skills server/scripts/seed_skill_library.py server/tests
git commit -m "refactor: remove obsolete tag and loop bundle models"
```

### Task 8: Add Migration 1 for Legacy Generation Structures

**Files:**
- Create: `server/alembic/versions/0073_geo_slim_legacy_gen.py`
- Create: `server/tests/test_geo_slimming_migrations.py`

**Interfaces:**
- Revision: `0073_geo_slim_legacy_gen`.
- Down revision: `0072_planb_discovery_patrol`.
- Drops tables in exact child-first order and resolves the `question_items.article_id` FK by inspected constrained columns.

- [ ] **Step 1: Add fresh-chain and target-fixture migration assertions**

```python
def test_0073_drops_legacy_generation_and_preserves_question_data(mysql_migration_db):
    upgrade_to(mysql_migration_db, "0072_planb_discovery_patrol")
    seed_dev_shaped_legacy_rows(mysql_migration_db)
    before = snapshot_protected_question_rows(mysql_migration_db)
    upgrade_to(mysql_migration_db, "0073_geo_slim_legacy_gen")
    inspector = sa.inspect(mysql_migration_db)
    assert LEGACY_GENERATION_TABLES.isdisjoint(inspector.get_table_names())
    columns = {c["name"] for c in inspector.get_columns("question_items")}
    assert {"status", "article_id"}.isdisjoint(columns)
    assert snapshot_protected_question_rows(mysql_migration_db) == before
```

Add a downgrade assertion that empty structures are recreated, without claiming data restoration:

```python
def test_0073_downgrade_recreates_constraints_in_dependency_order(mysql_migration_db):
    upgrade_to(mysql_migration_db, "0073_geo_slim_legacy_gen")
    downgrade_to(mysql_migration_db, "0072_planb_discovery_patrol")
    inspector = sa.inspect(mysql_migration_db)
    assert LEGACY_GENERATION_TABLES <= set(inspector.get_table_names())
    columns = {column["name"] for column in inspector.get_columns("question_items")}
    assert {"status", "article_id"} <= columns
    assert any(
        fk["constrained_columns"] == ["article_id"]
        and fk["referred_table"] == "articles"
        for fk in inspector.get_foreign_keys("question_items")
    )
    assert any(
        fk["constrained_columns"] == ["skill_id"]
        and fk["referred_table"] == "skills"
        for fk in inspector.get_foreign_keys("generation_sessions")
    )
```

- [ ] **Step 2: Run migration test and verify revision is missing**

Run: `conda run -n geo_xzpt pytest server/tests/test_geo_slimming_migrations.py -q`

Expected: FAIL because revision `0073_geo_slim_legacy_gen` does not exist.

- [ ] **Step 3: Implement strict upgrade and empty-structure downgrade**

```python
def _fk_name_for_columns(inspector, table: str, columns: list[str]) -> str:
    matches = [
        fk["name"]
        for fk in inspector.get_foreign_keys(table)
        if fk["constrained_columns"] == columns
    ]
    if len(matches) != 1 or matches[0] is None:
        raise RuntimeError(f"expected exactly one FK on {table}{columns}, got {matches}")
    return matches[0]


def _index_names_for_columns(inspector, table: str, columns: list[str]) -> list[str]:
    return [
        index["name"]
        for index in inspector.get_indexes(table)
        if index["column_names"] == columns
    ]
```

Drop, in order: `generation_scheme_run_tasks`, `generation_scheme_runs`, `generation_scheme_line_questions`, `generation_scheme_lines`, `generation_schemes`, `generation_sessions`, `skills`, `category_usages`. Then drop `ck_question_items_status`, `ix_question_items_status`, the inspected `article_id` FK, every inspected single-column `article_id` index, `status` and `article_id`. Require exactly one FK; do not assume MySQL's generated FK/index names and do not use conditional table deletion.

Implement downgrade in parent-first order: restore `question_items.status/article_id` plus check/index/FK; create `skills`, then `generation_sessions`; create `generation_schemes`, then lines and runs, then line-questions and run-tasks; create `category_usages` only after confirming the preserved `question_pools` parent exists. The downgrade test must inspect all recreated FK, check and index definitions, not only table names.

- [ ] **Step 4: Run the migration test from zero, upgrade and downgrade**

Run: `conda run -n geo_xzpt pytest server/tests/test_geo_slimming_migrations.py -q -k 0073`

Expected: PASS on MySQL 8.

Run: `conda run -n geo_xzpt alembic heads`

Expected: exactly one head, `0073_geo_slim_legacy_gen`.

- [ ] **Step 5: Commit**

```bash
git add server/alembic/versions/0073_geo_slim_legacy_gen.py server/tests/test_geo_slimming_migrations.py
git commit -m "db: remove legacy generation structures"
```

### Task 9: Add Migration 2 for Tags and Optional Loop Bundle

**Files:**
- Create: `server/alembic/versions/0074_geo_slim_tags_loop.py`
- Modify: `server/tests/test_geo_slimming_migrations.py`

**Interfaces:**
- Revision: `0074_geo_slim_tags_loop`.
- Down revision: `0073_geo_slim_legacy_gen`.
- `loop_skill_bundle_versions` is the only optional table.

- [ ] **Step 1: Add present/absent loop-table branches and Article feed integration**

```python
@pytest.mark.parametrize("old_loop_table_exists", [False, True])
def test_0074_drops_tags_and_optional_loop_table(
    mysql_migration_db, old_loop_table_exists
):
    upgrade_to(mysql_migration_db, "0073_geo_slim_legacy_gen")
    create_tag_fixtures(mysql_migration_db)
    set_loop_table_presence(mysql_migration_db, old_loop_table_exists)
    upgrade_to(mysql_migration_db, "0074_geo_slim_tags_loop")
    tables = set(sa.inspect(mysql_migration_db).get_table_names())
    assert {"article_tags", "tags", "loop_skill_bundle_versions"}.isdisjoint(tables)
    assert {"articles", "skill_library_skills", "skill_library_versions"} <= tables
```

- [ ] **Step 2: Run migration tests and verify revision is missing**

Run: `conda run -n geo_xzpt pytest server/tests/test_geo_slimming_migrations.py -q -k 0074`

Expected: FAIL because revision `0074_geo_slim_tags_loop` does not exist.

- [ ] **Step 3: Implement strict tag deletion and optional loop deletion**

```python
def upgrade() -> None:
    op.drop_table("article_tags")
    op.drop_table("tags")
    bind = op.get_bind()
    if sa.inspect(bind).has_table("loop_skill_bundle_versions"):
        op.drop_table("loop_skill_bundle_versions")
```

Downgrade recreates empty `tags`, `article_tags` and `loop_skill_bundle_versions` structures using the exact former column definitions and indexes. It does not restore rows.

- [ ] **Step 4: Run full migration chain and protected-module tests**

Run: `conda run -n geo_xzpt pytest server/tests/test_geo_slimming_migrations.py server/tests/test_geo_slimming_boundaries.py server/tests/test_articles_api.py server/tests/test_skill_library_compat.py -q`

Expected: PASS in both optional-table branches.

Run: `conda run -n geo_xzpt alembic heads`

Expected: exactly one head, `0074_geo_slim_tags_loop`.

- [ ] **Step 5: Commit**

```bash
git add server/alembic/versions/0074_geo_slim_tags_loop.py server/tests/test_geo_slimming_migrations.py
git commit -m "db: remove obsolete article tags and loop bundle"
```

### Task 10: Rehearse on Three Non-Production MySQL Shapes

**Files:**
- Create: `docs/runbooks/geo-slimming-release-b-rehearsal.md`
- Modify: `server/tests/test_geo_slimming_migrations.py`
- Modify: `docs/DEPLOYMENT.md`

**Interfaces:**
- Environment 1: fresh MySQL from migration zero to `0074`.
- Environment 2: target fixtures shaped like dev, including 13/143/464/17/154/7/6 rows and failed run 14 with five pending tasks.
- Environment 3: non-production clone of production schema plus target-table desensitized data and the minimum matching protected parent rows required by external FKs.

- [ ] **Step 1: Add a scriptable rehearsal command matrix**

```powershell
conda run -n geo_xzpt python -m server.scripts.archive_geo_slimming --output-root $env:GEO_SLIMMING_ARCHIVE_ROOT --deploy-commit (git rev-parse HEAD) --image-version rehearsal
conda run -n geo_xzpt alembic upgrade head
conda run -n geo_xzpt alembic downgrade 0072_planb_discovery_patrol
conda run -n geo_xzpt alembic upgrade head
conda run -n geo_xzpt python -m server.scripts.restore_geo_slimming_archive --archive $env:GEO_SLIMMING_ARCHIVE_DIR --database-url $env:GEO_SLIMMING_RESTORE_TEST_DATABASE_URL
```

The Alembic commands use the rehearsal source/clone database. `GEO_SLIMMING_RESTORE_TEST_DATABASE_URL` must point to a different disposable clone already upgraded to `0074` and containing the same protected parent rows, so archived external FKs resolve and restore never collides with downgrade-created empty tables.

- [ ] **Step 2: Run the fresh and fixture database rehearsals**

Run: `conda run -n geo_xzpt pytest server/tests/test_geo_slimming_migrations.py server/tests/test_geo_slimming_archive.py -q`

Expected: PASS with one Alembic head, exact protected row counts, successful downgrade structure recreation and archive restore.

- [ ] **Step 3: Run the production-clone rehearsal without production writes**

Run the same upgrade/downgrade/restore sequence against a database whose name contains `test` and whose source is a schema-only plus desensitized target-data clone. Record connection target, starting revision `0072_planb_discovery_patrol`, ending revision `0074_geo_slim_tags_loop`, timings and exact counts. Do not set any database URL to `geo_collab`.

- [ ] **Step 4: Record all evidence and a fail-closed decision**

The runbook must include SHA-256 of the archive, exact source/restored counts, FK closure, JSON equivalence, migration duration, downgrade result, protected table counts, Article feed result, goal list/ZIP/SHA/install result and one line: `Production migration decision: GO` or `Production migration decision: NO-GO`.

- [ ] **Step 5: Commit**

```bash
git add docs/runbooks/geo-slimming-release-b-rehearsal.md docs/DEPLOYMENT.md server/tests/test_geo_slimming_migrations.py
git commit -m "docs: record slimming migration rehearsal"
```

### Task 11: Update Truth Documents and Run the Full Protected-Module Matrix

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/AI_GENERATION.md`
- Modify: `docs/project/02-product-design.md`
- Modify: `docs/project/04-database-design.md`
- Modify: `docs/project/05-api-reference.md`
- Modify: `server/tests/test_geo_slimming_boundaries.py`

**Interfaces:**
- Current documentation contains no claim that schemes, sessions, old skills, tags or old bundle storage still exist.
- Historical migration/spec/plan documents remain untouched.

- [ ] **Step 1: Extend the protected-module matrix**

```python
def test_mcp_contract_after_slimming():
    from server.mcp.server import mcp

    tools = set(mcp._tool_manager._tools)
    assert MCP_TOOLS_COUNT == 39
    assert {
        "list_question_pools",
        "list_question_items",
        "save_article",
        "get_template_performance",
        "get_account_performance",
        "record_publish_metrics",
        "install_loop_skills",
    } <= tools
```

Add assertions for app import/router mount, Pipeline node registry, question-pool CRUD/status matrix, Article feed/detail/MCP save, goal compatibility routes, image-library routes, game/video/XHS routers, worker import and operational scripts.

- [ ] **Step 2: Run the focused full matrix**

Run: `conda run -n geo_xzpt pytest server/tests/test_geo_slimming_boundaries.py server/tests/test_question_bank.py server/tests/test_question_pool_sync.py server/tests/test_mcp_catalog.py server/tests/test_save_article_mcp.py server/tests/test_performance.py server/tests/test_skill_library_seed.py server/tests/test_skill_library_compat.py server/tests/test_articles_api.py server/tests/test_pipeline_node_types.py server/tests/test_game_mcp.py server/tests/test_video_wiring.py -q`

Expected: PASS.

- [ ] **Step 3: Update all current truth documents**

Document Pipeline as the only in-product generation runtime, active question semantics, removed physical structures, retained compatibility routes, real performance metrics, current migration head and archive-based rollback. Do not edit historical files under `server/alembic/versions/` or dated archived plans/specs.

- [ ] **Step 4: Run repository gates**

Run: `conda run -n geo_xzpt ruff check server/ && conda run -n geo_xzpt ruff format --check server/ && conda run -n geo_xzpt mypy server/app`

Expected: PASS.

Run: `pnpm --filter @geo/web lint && pnpm --filter @geo/web typecheck && pnpm --filter @geo/web format:check && pnpm --filter @geo/web build`

Expected: PASS.

Run: `conda run -n geo_xzpt pytest server/tests/ -q`

Expected: no new failure relative to the recorded baseline; all slimming, migration and protected-module tests pass.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md docs/AI_GENERATION.md docs/project/02-product-design.md docs/project/04-database-design.md docs/project/05-api-reference.md server/tests/test_geo_slimming_boundaries.py
git commit -m "docs: align architecture with slimmed generation runtime"
```

## Release B Production Gate

- [ ] Confirm `Release B decision: GO` and `Production migration decision: GO`.
- [ ] Create the final production archive before migration; copy it to storage independent of the database host and recompute every SHA-256.
- [ ] Verify production still has no active scheme run and record exact pre-migration counts.
- [ ] Deploy code and run the two rehearsed migrations through the normal release process; do not improvise SQL.
- [ ] Confirm `alembic current` is `0074_geo_slim_tags_loop`.
- [ ] Confirm target tables/columns are absent and protected table/row counts match the preflight report.
- [ ] Run one real Pipeline from question source through generation, illustration, review and distribution.
- [ ] Verify MCP question selection/save, 39 tools, all three performance tools and weekly report.
- [ ] Verify goal list, version, ZIP entries, per-file SHA-256, bundle SHA and install payload through both new and compatibility routes.
- [ ] Verify Article feed/detail/MCP get-save/illustration/grouping/publish and confirm logs contain no `article_tags` or `tags` query.
- [ ] Verify image-library consumers, game/XHS/video/Feishu/WeChat APIs, worker, scheduler and account keepalive health.
- [ ] Before rollout, have two operators dry-run and record the production recovery command: `python -m server.scripts.restore_geo_slimming_archive --archive "$env:GEO_SLIMMING_ARCHIVE_DIR" --database-url "$env:GEO_DATABASE_URL" --allow-production-restore --expected-database geo_collab --expected-manifest-sha256 "$env:GEO_SLIMMING_MANIFEST_SHA256"`.
- [ ] If any check fails, stop writes, use the retained Release B admin image to downgrade the schema and run the recorded hash/database-bound restore command after two-person approval, then roll the application image back to Release A and rerun the protected-module matrix.
