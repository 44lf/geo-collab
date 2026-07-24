# Skill 安装/列举 + Category 分类 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Claude Code 能列出 GEO Skill 库里所有 skill（可按业务类别筛）并按 slug 安装任意整包（不再写死 goal），用 `category` 业务标签防功能错配。

**Architecture:** 承接已上线的多 skill×多版本库（表 `skill_library_skills` / `skill_library_versions`）。加一个 `category` 列（业务标签，打在包上）；在已存在的 `skills_mcp_router`（MCP token）上加一个 `GET /skills` 列举端点；新增 `list_skills` MCP 工具、给 `install_loop_skills` 加 slug 参数。保存机制（上传解析/files JSON 存储/MinIO seam/版本管理/download.zip）零改动。

**Tech Stack:** FastAPI + SQLAlchemy 2.0（Mapped/mapped_column）+ Alembic（MySQL only）+ FastMCP（stdio+HTTP mount）+ React 19 + Vite + TypeScript strict。

## Global Constraints

- **spec 依据**：`docs/superpowers/specs/2026-07-08-skill-install-list-category-design.md`，逐条落地。
- **整包原子**：一条 `Skill` 记录 = 一个包；阶段（orchestrator/writer/verifier）不拆、不单独寻址；units 仅只读展示。
- **保存机制零改动**：`upload.py` / `storage.py` / `service.py`(build_zip) / `models.py` 的 `files` 列 / 版本管理 / download.zip 全不碰逻辑（`models.py` 仅加 `category` 列）。
- **category 固定枚举**：`generation` / `distribute` / `video` / `general`，默认 `general`。非法值 → `ValidationError`（走全局 400，不裸 `ValueError`）。
- **表名/类名**：物理表名 `skill_library_skills`（勿撞休眠 `skills` 表）；类名 `Skill`/`SkillVersion` 不变。
- **MCP 端点鉴权**：挂 `skills_mcp_router`（已带 `Depends(require_mcp_token)`）；未捕获异常走 `core/mcp_errors.mcp_exception_response`。
- **MCP 工具纪律**：新工具 `async def` + 阻塞调用经 `anyio.to_thread.run_sync`（自调用防死锁）；`MCP_TOOLS_COUNT` 25→26；`_CURATED_ZH` 补 `list_skills`。
- **向后兼容**：`install_loop_skills()` 不传参仍装 goal（现有 4 个 loop 配方不破）。
- **迁移**：新 revision `0059_skill_category`，`down_revision="0058_skill_library"`。
- **测试环境**（后端 pytest 前 export；见 `.superpowers/sdd/backend-env.md` 惯例）：
  - `PY = C:/Users/Administrator/miniconda3/envs/geo_xzpt/python.exe`
  - `GEO_TEST_DATABASE_URL = mysql+pymysql://root:<GEO_DB_PASS>@172.25.20.57:3307/geo_test`
  - `GEO_JWT_SECRET = <任意非空>`
  - 后端测试标 `@pytest.mark.mysql`；`build_test_app(monkeypatch)` 提供 `app.client`（admin JWT cookie）、`create_extra_user(app, name)`；MCP token 测试用 `headers={"X-MCP-Token": "secret"}`。
- **前端**：无单测框架，门禁 = `pnpm --filter @geo/web typecheck` + `build`。命令用 `-C web` 或绝对路径（worktree cwd 会漂）。

---

### Task 1: `category` 列（模型 + 迁移 + service 校验 + seed + schema）

一次落地 category 的整个数据层——模型列、迁移（含就地修正生产 goal）、service 写入/校验/读出、seed 设官方类别、响应 schema。

**Files:**
- Modify: `server/app/modules/loop_skills/models.py`（`Skill` 加 `category`）
- Create: `server/alembic/versions/0059_skill_category.py`
- Modify: `server/app/modules/loop_skills/skill_service.py`（`create_version` 加 `category` 参数+校验、`SkillListItem` 加 `category`、`list_skills` 返回 `category`）
- Modify: `server/scripts/seed_skill_library.py`（建 goal 设 `category="generation"`）
- Modify: `server/app/modules/loop_skills/schemas.py`（`SkillMeta` 加 `category`）
- Test: `server/tests/test_skill_library_service.py`（新增 category 用例）、`server/tests/test_skill_library_seed.py`（goal category）

**Interfaces:**
- Produces:
  - `Skill.category: str`（列，默认 `"general"`）
  - `skill_service.VALID_SKILL_CATEGORIES: set[str] = {"generation","distribute","video","general"}`
  - `skill_service.create_version(session, *, entries, name, uploaded_by, is_admin=False, category="general") -> tuple[Skill, SkillVersion]`
  - `SkillListItem.category: str`（dataclass 字段）
  - `SkillMeta.category: str`（pydantic）

- [ ] **Step 1: 写失败测试（service 层 category）**

追加到 `server/tests/test_skill_library_service.py`：

```python
def test_create_version_category_default_and_custom(monkeypatch):
    import io, zipfile
    from server.tests.utils import build_test_app
    from server.app.db.session import SessionLocal
    from server.app.modules.loop_skills import skill_service as svc

    def _zip(files):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for k, v in files.items():
                zf.writestr(k, v)
        return buf.getvalue()

    app = build_test_app(monkeypatch)
    try:
        db = SessionLocal()
        try:
            sk_def, _ = svc.create_version(
                db, entries=[("b.zip", _zip({"SKILL.md": b"x"}))],
                name="cat-default", uploaded_by=None,
            )
            assert sk_def.category == "general"

            sk_gen, _ = svc.create_version(
                db, entries=[("b.zip", _zip({"SKILL.md": b"x"}))],
                name="cat-gen", uploaded_by=None, category="generation",
            )
            assert sk_gen.category == "generation"
            db.commit()

            items = {it.name: it for it in svc.list_skills(db)}
            assert items["cat-gen"].category == "generation"
        finally:
            db.close()
    finally:
        app.cleanup()


def test_create_version_rejects_bad_category(monkeypatch):
    import io, zipfile
    import pytest
    from server.tests.utils import build_test_app
    from server.app.db.session import SessionLocal
    from server.app.modules.loop_skills import skill_service as svc
    from server.app.shared.errors import ValidationError

    def _zip(files):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for k, v in files.items():
                zf.writestr(k, v)
        return buf.getvalue()

    app = build_test_app(monkeypatch)
    try:
        db = SessionLocal()
        try:
            with pytest.raises(ValidationError):
                svc.create_version(
                    db, entries=[("b.zip", _zip({"SKILL.md": b"x"}))],
                    name="cat-bad", uploaded_by=None, category="nonsense",
                )
        finally:
            db.close()
    finally:
        app.cleanup()
```

同时给测试文件顶部确保有 `import pytest` 与 `pytestmark = pytest.mark.mysql`（若已存在则跳过）。

- [ ] **Step 2: 运行测试确认失败**

Run: `$PY -m pytest server/tests/test_skill_library_service.py -q -k "category"`
Expected: FAIL（`create_version()` 不接受 `category` → TypeError，或 `Skill` 无 `category` 属性）

- [ ] **Step 3: 模型加 `category` 列**

`server/app/modules/loop_skills/models.py`，在 `Skill` 类的 `is_official` 之后加一行：

```python
    is_official: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    category: Mapped[str] = mapped_column(String(32), default="general", nullable=False)
```

- [ ] **Step 4: service 加校验/参数/读出**

`server/app/modules/loop_skills/skill_service.py`：

顶部（`import re` 之后合适位置）加枚举常量：

```python
VALID_SKILL_CATEGORIES = {"generation", "distribute", "video", "general"}
```

`SkillListItem` dataclass 加字段（放在 `uploaded_by` 之后）：

```python
    uploaded_by: int | None
    category: str
```

`create_version` 签名加参数 + 开头校验；新建 `Skill` 时带 category：

```python
def create_version(
    session: Session,
    *,
    entries: list[tuple[str, bytes]],
    name: str,
    uploaded_by: int | None,
    is_admin: bool = False,
    category: str = "general",
) -> tuple[Skill, SkillVersion]:
    name = (name or "").strip()
    if not name:
        raise ValidationError("skill 名不能为空")
    if category not in VALID_SKILL_CATEGORIES:
        raise ValidationError(
            f"非法 category: {category}（可选 {sorted(VALID_SKILL_CATEGORIES)}）"
        )
    raw = upload.parse_upload(entries)
    ...
```

在 `create_version` 里新建 skill 的分支加 `category=category`：

```python
            if skill is None:
                skill = Skill(
                    name=name,
                    slug=slugify(name, session),
                    is_official=False,
                    created_by=uploaded_by,
                    category=category,
                )
```

> 注意：category 仅在**首次新建** skill 时写入；对已存在 skill 追加版本时不改其 category（category 是包属性，不随版本变）。

`list_skills` 里构造 `SkillListItem` 时补 `category=sk.category`：

```python
        items.append(
            SkillListItem(
                id=sk.id,
                name=sk.name,
                slug=sk.slug,
                is_official=sk.is_official,
                current_version_label=cur.version_label if cur else None,
                file_count=cur.file_count if cur else 0,
                total_bytes=cur.total_bytes if cur else 0,
                updated_at=sk.updated_at,
                uploaded_by=cur.uploaded_by if cur else None,
                category=sk.category,
            )
        )
```

- [ ] **Step 5: schema 加字段**

`server/app/modules/loop_skills/schemas.py`，`SkillMeta` 加 `category`：

```python
class SkillMeta(BaseModel):
    id: int
    name: str
    slug: str
    is_official: bool
    current_version_label: str | None
    file_count: int
    total_bytes: int
    updated_at: datetime
    uploaded_by: int | None
    category: str
```

- [ ] **Step 6: 写迁移**

Create `server/alembic/versions/0059_skill_category.py`：

```python
"""skill_library_skills 加 category 业务标签列。

已存在的官方 goal 就地修正为 generation（seed 是"存在即 skip"，不能靠它补）。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0059_skill_category"
down_revision: str | None = "0058_skill_library"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "skill_library_skills",
        sa.Column("category", sa.String(length=32), nullable=False, server_default="general"),
    )
    op.execute(
        "UPDATE skill_library_skills SET category='generation' "
        "WHERE slug='goal' AND is_official=1"
    )


def downgrade() -> None:
    op.drop_column("skill_library_skills", "category")
```

- [ ] **Step 7: seed 建 goal 设 category**

`server/scripts/seed_skill_library.py`，建 `Skill` 处加 `category="generation"`：

```python
    skill = Skill(
        name=OFFICIAL_NAME, slug=OFFICIAL_SLUG, is_official=True,
        created_by=None, category="generation",
    )
```

- [ ] **Step 8: 写 seed 测试**

追加到 `server/tests/test_skill_library_seed.py`（沿用该文件已有的 build_test_app + seed 调用模式；若文件已 seed 一次，读现有用例仿写）：

```python
def test_seed_sets_goal_category_generation(monkeypatch):
    from server.tests.utils import build_test_app
    from server.app.db.session import SessionLocal
    from server.app.scripts_shim import _noop  # 若无则删本行
    from server.scripts.seed_skill_library import seed_skill_library
    from server.app.modules.loop_skills import skill_service as svc

    app = build_test_app(monkeypatch)
    try:
        db = SessionLocal()
        try:
            seed_skill_library(db)
            db.commit()
            goal = next(it for it in svc.list_skills(db) if it.slug == "goal")
            assert goal.category == "generation"
        finally:
            db.close()
    finally:
        app.cleanup()
```

> 若 `build_test_app` 已自动 seed goal（读 `server/tests/utils.py` 确认），则去掉重复 `seed_skill_library` 调用，直接断言 `list_skills` 里 goal 的 category。删掉 `scripts_shim` 那行（它是占位，不存在就别 import）。

- [ ] **Step 9: 运行测试确认通过**

Run: `$PY -m pytest server/tests/test_skill_library_service.py server/tests/test_skill_library_seed.py server/tests/test_skill_library_models.py -q`
Expected: PASS（含新增 category 用例 + 原有回归）

- [ ] **Step 10: lint + commit**

Run: `ruff check server/app/modules/loop_skills/ server/scripts/seed_skill_library.py server/alembic/versions/0059_skill_category.py && ruff format server/app/modules/loop_skills/ server/scripts/seed_skill_library.py server/alembic/versions/0059_skill_category.py`

```bash
git add server/app/modules/loop_skills/models.py server/app/modules/loop_skills/skill_service.py server/app/modules/loop_skills/schemas.py server/scripts/seed_skill_library.py server/alembic/versions/0059_skill_category.py server/tests/test_skill_library_service.py server/tests/test_skill_library_seed.py
git commit -m "feat(skill): Skill 加 category 业务标签列（模型+迁移+service+seed）"
```

---

### Task 2: 上传端点带 category

`POST /api/mcp/skills/upload` 加一个 `category` 表单参数，透传给 `create_version`。

**Files:**
- Modify: `server/app/modules/loop_skills/skill_router.py`（`upload_skill` 加 `category` Form 参数）
- Test: `server/tests/test_skill_library_api.py`（新增 category 上传用例）

**Interfaces:**
- Consumes: `create_version(..., category=...)`（Task 1）
- Produces: `POST /api/mcp/skills/upload` 接受 `category` 表单字段（默认 `general`）

- [ ] **Step 1: 写失败测试**

追加到 `server/tests/test_skill_library_api.py`（复用文件顶部的 `_zip` helper）：

```python
def test_upload_with_category(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        c = app.client
        r = c.post(
            "/api/mcp/skills/upload",
            files={"files": ("b.zip", _zip({"SKILL.md": b"one"}), "application/zip")},
            data={"name": "cat-writer", "category": "distribute"},
        )
        assert r.status_code == 200, r.text
        skills = c.get("/api/mcp/skills").json()["skills"]
        row = next(s for s in skills if s["slug"] == r.json()["slug"])
        assert row["category"] == "distribute"

        # 不传 category → 默认 general
        r2 = c.post(
            "/api/mcp/skills/upload",
            files={"files": ("b.zip", _zip({"SKILL.md": b"x"}), "application/zip")},
            data={"name": "cat-default-api"},
        )
        skills2 = c.get("/api/mcp/skills").json()["skills"]
        row2 = next(s for s in skills2 if s["slug"] == r2.json()["slug"])
        assert row2["category"] == "general"
    finally:
        app.cleanup()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `$PY -m pytest server/tests/test_skill_library_api.py -q -k category`
Expected: FAIL（`category` 未透传 → 返回的 skill `category` 恒为 `general`，`distribute` 断言失败）

- [ ] **Step 3: 端点加 category 参数**

`server/app/modules/loop_skills/skill_router.py` 的 `upload_skill`，加 `category` Form 参数并透传：

```python
@skills_user_router.post("/skills/upload", response_model=UploadResult)
async def upload_skill(
    files: list[UploadFile] = File(...),
    name: str = Form(...),
    category: str = Form("general"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UploadResult:
    entries = [(f.filename or "file", await f.read()) for f in files]
    try:
        skill, version = svc.create_version(
            db,
            entries=entries,
            name=name,
            uploaded_by=current_user.id,
            is_admin=(current_user.role == "admin"),
            category=category,
        )
    except (ConflictError, ValidationError):
        raise
    except ClientError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    ...
```

> 非法 category 由 `create_version` 抛 `ValidationError` → 现有 `except (ConflictError, ValidationError): raise` 放行 → 全局 400。无需在端点额外处理。

- [ ] **Step 4: 运行测试确认通过**

Run: `$PY -m pytest server/tests/test_skill_library_api.py -q`
Expected: PASS（新 category 用例 + 原有上传/回滚/删除回归全绿）

- [ ] **Step 5: lint + commit**

Run: `ruff check server/app/modules/loop_skills/skill_router.py && ruff format server/app/modules/loop_skills/skill_router.py`

```bash
git add server/app/modules/loop_skills/skill_router.py server/tests/test_skill_library_api.py
git commit -m "feat(skill): 上传端点接受 category 表单参数"
```

---

### Task 3: MCP list 端点 + units 提取

在 `skills_mcp_router`（MCP token）加 `GET /skills?category=`，返回每个包的元信息 + `units`（包内 skill 单元清单）。

**Files:**
- Modify: `server/app/modules/loop_skills/skill_service.py`（加 `extract_unit_names` + `unit_names_for_skill`）
- Modify: `server/app/modules/loop_skills/skill_router.py`（`skills_mcp_router` 加 `GET /skills`）
- Test: `server/tests/test_skill_library_api.py`（MCP list 端点：鉴权 + category 筛 + units）

**Interfaces:**
- Consumes: `svc.list_skills(db)`（含 category，Task 1）、`storage.load_version_files(row)`
- Produces:
  - `skill_service.extract_unit_names(files: list[SkillFile], *, fallback_slug: str) -> list[str]`
  - `skill_service.unit_names_for_skill(session, skill_id: int, slug: str) -> list[str]`
  - `GET /api/mcp/skills?category=<opt>` → `{"ok": True, "data": {"skills": [{id, slug, name, category, is_official, current_version_label, file_count, total_bytes, units}]}, "error": None}`

- [ ] **Step 1: 写失败测试（units 提取纯函数）**

追加到 `server/tests/test_skill_library_service.py`：

```python
def test_extract_unit_names():
    from server.app.modules.loop_skills.skill_service import extract_unit_names
    from server.app.modules.loop_skills.service import SkillFile

    multi = [
        SkillFile(path="README.md", size=1, sha256="a", content="x"),
        SkillFile(path="commands/goal.md", size=1, sha256="b", content="x"),
        SkillFile(path="skills/geo-goal-orchestrator/SKILL.md", size=1, sha256="c", content="x"),
        SkillFile(path="skills/geo-article-writer/SKILL.md", size=1, sha256="d", content="x"),
    ]
    assert extract_unit_names(multi, fallback_slug="goal") == [
        "geo-goal-orchestrator", "geo-article-writer",
    ]

    single = [SkillFile(path="SKILL.md", size=1, sha256="e", content="x")]
    assert extract_unit_names(single, fallback_slug="my-writer") == ["my-writer"]
```

- [ ] **Step 2: 写失败测试（MCP 端点）**

> ⚠️ 路径冲突规避：`skills_user_router` 已占用 `GET /api/mcp/skills`（user JWT list）。MCP 版**不能同路径**（两个 router 同 path + 不同鉴权会互相遮蔽），所以本任务的 MCP 列举端点用 **`GET /api/mcp/skills/catalog`**。

追加到 `server/tests/test_skill_library_api.py`：

```python
def test_mcp_list_skills_catalog(monkeypatch):
    from server.tests.utils import build_test_app

    HDR = {"X-MCP-Token": "secret"}
    app = build_test_app(monkeypatch)
    try:
        c = app.client
        # 无 MCP token → 401
        assert c.get("/api/mcp/skills/catalog").status_code == 401

        c.post("/api/mcp/skills/upload",
               files={"files": ("b.zip", _zip({"skills/w1/SKILL.md": b"x",
                                               "skills/w2/SKILL.md": b"y"}), "application/zip")},
               data={"name": "gen-pkg", "category": "generation"})
        c.post("/api/mcp/skills/upload",
               files={"files": ("b.zip", _zip({"SKILL.md": b"x"}), "application/zip")},
               data={"name": "dist-pkg", "category": "distribute"})

        # 带 token → 200，返回全部
        r = c.get("/api/mcp/skills/catalog", headers=HDR)
        assert r.status_code == 200, r.text
        by_slug = {s["slug"]: s for s in r.json()["data"]["skills"]}
        assert "gen-pkg" in by_slug and "dist-pkg" in by_slug
        # units 提取
        assert sorted(by_slug["gen-pkg"]["units"]) == ["w1", "w2"]
        assert by_slug["dist-pkg"]["units"] == ["dist-pkg"]  # 单文件包回落 slug

        # category 筛
        r2 = c.get("/api/mcp/skills/catalog?category=distribute", headers=HDR)
        slugs = {s["slug"] for s in r2.json()["data"]["skills"]}
        assert "dist-pkg" in slugs and "gen-pkg" not in slugs
    finally:
        app.cleanup()
```

- [ ] **Step 3: 运行测试确认失败**

Run: `$PY -m pytest server/tests/test_skill_library_service.py::test_extract_unit_names server/tests/test_skill_library_api.py::test_mcp_list_skills_catalog -q`
Expected: FAIL（`extract_unit_names` 未定义 / `GET /api/mcp/skills/catalog` 404）

- [ ] **Step 4: service 加 units 提取**

`server/app/modules/loop_skills/skill_service.py`，加纯函数 + DB helper（放在 `list_skills` 之后）：

```python
import re as _re  # 若文件顶部已 import re，复用即可，勿重复 import

_UNIT_RE = re.compile(r"^skills/([^/]+)/SKILL\.md$")


def extract_unit_names(files, *, fallback_slug: str) -> list[str]:
    """从包内文件提取 skill 单元名（只读展示）。

    skills/<name>/SKILL.md → <name>（保序）；无子目录单元但有顶层 SKILL.md
    （单文件包）→ 回落 [fallback_slug]。
    """
    units: list[str] = []
    for f in files:
        m = _UNIT_RE.match(f.path)
        if m:
            units.append(m.group(1))
    if not units and any(f.path == "SKILL.md" for f in files):
        units = [fallback_slug]
    return units


def unit_names_for_skill(session: Session, skill_id: int, slug: str) -> list[str]:
    sk = session.get(Skill, skill_id)
    if sk is None or sk.current_version_id is None:
        return []
    row = session.get(SkillVersion, sk.current_version_id)
    if row is None:
        return []
    files = storage.load_version_files(row)
    return extract_unit_names(files, fallback_slug=slug)
```

> `re` 已在文件顶部 import（`import re`），直接用 `re.compile`，删掉上面 `import re as _re` 那行——它只是提醒，不要真加重复 import。

- [ ] **Step 5: 加 MCP list 端点**

`server/app/modules/loop_skills/skill_router.py`，在 `install_payload` 之后（同属 `skills_mcp_router`）加：

```python
@skills_mcp_router.get("/skills/catalog")
def mcp_list_skills(category: str | None = None, db: Session = Depends(get_db)) -> dict:
    try:
        items = svc.list_skills(db)
        if category:
            items = [it for it in items if it.category == category]
        skills = [
            {
                "id": it.id,
                "slug": it.slug,
                "name": it.name,
                "category": it.category,
                "is_official": it.is_official,
                "current_version_label": it.current_version_label,
                "file_count": it.file_count,
                "total_bytes": it.total_bytes,
                "units": svc.unit_names_for_skill(db, it.id, it.slug),
            }
            for it in items
        ]
        return {"ok": True, "data": {"skills": skills}, "error": None}
    except Exception as exc:
        raise mcp_exception_response(exc, context=f"mcp_list_skills category={category}") from exc
```

> `mcp_exception_response` 已在文件顶部 import。`svc` 别名即 `skill_service`。

- [ ] **Step 6: 运行测试确认通过**

Run: `$PY -m pytest server/tests/test_skill_library_service.py server/tests/test_skill_library_api.py -q`
Expected: PASS（units 纯函数 + MCP 端点 + 原有回归全绿）

- [ ] **Step 7: lint + commit**

Run: `ruff check server/app/modules/loop_skills/ && ruff format server/app/modules/loop_skills/`

```bash
git add server/app/modules/loop_skills/skill_service.py server/app/modules/loop_skills/skill_router.py server/tests/test_skill_library_service.py server/tests/test_skill_library_api.py
git commit -m "feat(skill): MCP GET /skills/catalog 列举端点 + units 提取"
```

---

### Task 4: MCP 工具（list_skills + install 收 slug）+ MCP_TOOLS_COUNT

新增 `list_skills` 工具打 catalog 端点；`install_loop_skills` 加 slug 参数打任意包；bump 常量 + 中文表。

**Files:**
- Modify: `server/mcp/tools/catalog.py`（加 `list_skills`）
- Modify: `server/mcp/tools/action.py`（`install_loop_skills` 加 `slug`）
- Modify: `server/app/modules/mcp_catalog/connect_router.py`（`MCP_TOOLS_COUNT` 26 + `_CURATED_ZH` 补 `list_skills`）
- Test: `server/tests/test_mcp_tools_registration.py`（新建：注册表含 list_skills + 数量下限）

**Interfaces:**
- Consumes: `GET /api/mcp/skills/catalog`（Task 3）、`GET /api/mcp/skills/{slug}/install-payload`（已存在）
- Produces:
  - MCP 工具 `list_skills(category: str | None = None)`
  - MCP 工具 `install_loop_skills(slug: str | None = None, version: str | None = None)`（slug=None→goal）

- [ ] **Step 1: 写失败测试（工具注册）**

Create `server/tests/test_mcp_tools_registration.py`：

```python
"""MCP 工具注册守卫：list_skills 已注册 + 注册总数 ≥ MCP_TOOLS_COUNT。"""


def test_list_skills_tool_registered():
    import server.mcp.tools.catalog  # noqa: F401  触发注册
    import server.mcp.tools.action  # noqa: F401
    from server.mcp.server import mcp

    names = set(mcp._tool_manager._tools.keys())
    assert "list_skills" in names
    assert "install_loop_skills" in names


def test_registered_count_meets_floor():
    import server.mcp.tools.action  # noqa: F401
    import server.mcp.tools.catalog  # noqa: F401
    import server.mcp.tools.meta  # noqa: F401
    from server.app.modules.mcp_catalog.connect_router import MCP_TOOLS_COUNT
    from server.mcp.server import mcp

    assert len(mcp._tool_manager._tools) >= MCP_TOOLS_COUNT
    assert MCP_TOOLS_COUNT == 26
```

- [ ] **Step 2: 运行测试确认失败**

Run: `$PY -m pytest server/tests/test_mcp_tools_registration.py -q`
Expected: FAIL（`list_skills` 未注册；`MCP_TOOLS_COUNT == 26` 断言失败，当前是 25）

- [ ] **Step 3: 加 list_skills 工具**

`server/mcp/tools/catalog.py` 末尾追加：

```python
@mcp.tool()
async def list_skills(category: str | None = None) -> dict[str, Any]:
    """List installable skill packages in GEO's Skill library.

    Each entry is one installable package (a Skill record). Use its `slug`
    with install_loop_skills(slug=...) to install it. `units` lists the
    SKILL.md sub-skills the package expands into under .claude/skills/.

    Args:
        category: Optional business-category filter — one of
            "generation" / "distribute" / "video" / "general".

    Returns:
        {"ok": True, "data": {"skills": [{id, slug, name, category,
         is_official, current_version_label, file_count, total_bytes,
         units:[str]}]}, "error": None}
    """
    params: dict[str, Any] = {}
    if category:
        params["category"] = category
    return await _aget("/api/mcp/skills/catalog", params=params or None)
```

> `_aget` 已在 catalog.py 顶部定义，直接复用。

- [ ] **Step 4: install_loop_skills 加 slug**

`server/mcp/tools/action.py` 的 `install_loop_skills`，改签名 + 用 slug 构造路径：

```python
@mcp.tool()
async def install_loop_skills(slug: str | None = None, version: str | None = None) -> dict[str, Any]:
    """Fetch a skill package's current version so Claude Code can install it locally.

    Reads a skill from GEO's multi-skill library
    (`/api/mcp/skills/{slug}/install-payload`) and returns its files.

    Args:
        slug: Which skill package to install. None → the official "goal" pack
            (backward compatible with existing loop recipes). Use list_skills()
            to discover available slugs.
        version: Deprecated / ignored. Kept for backward compatibility. Always
            resolves to the target skill's current version; to switch versions,
            change the current version in GEO's web "Skill 库", then re-call.

    Returns:
        {"ok": True, "data": {"version": str, "bundle_sha256": str,
         "install_hint": str, "files": [{path, content, sha256, size}]}, "error": None}
    """
    target = slug or "goal"
    raw = await _aget(f"/api/mcp/skills/{target}/install-payload")
    if not raw.get("ok"):
        return raw
    inner = raw.get("data") or {}
    if isinstance(inner, dict) and "ok" in inner and "data" in inner:
        return inner
    return raw
```

- [ ] **Step 5: bump 常量 + 中文表**

`server/app/modules/mcp_catalog/connect_router.py`：

```python
MCP_TOOLS_COUNT = 26
```

`_CURATED_ZH` 的 catalog 段加一行（放在 `list_stock_categories` 附近）：

```python
    "list_stock_categories": "列出图片库栏目（配图选 main_category_id 用）",
    "list_skills": "列出 Skill 库里可安装的 skill 包（可按 category 过滤）",
```

`install_loop_skills` 的中文条目已存在，无需改。

- [ ] **Step 6: 运行测试确认通过**

Run: `$PY -m pytest server/tests/test_mcp_tools_registration.py server/tests/test_mcp_tools_async.py -q`
Expected: PASS（list_skills 已注册、数量 ≥26、常量 ==26；async 守卫覆盖新工具）

- [ ] **Step 7: lint + commit**

Run: `ruff check server/mcp/ server/app/modules/mcp_catalog/connect_router.py && ruff format server/mcp/ server/app/modules/mcp_catalog/connect_router.py`

```bash
git add server/mcp/tools/catalog.py server/mcp/tools/action.py server/app/modules/mcp_catalog/connect_router.py server/tests/test_mcp_tools_registration.py
git commit -m "feat(skill): list_skills MCP 工具 + install_loop_skills 收 slug + count 26"
```

---

### Task 5: 前端 category（选择器 + 徽章 + 客户端）

上传时选 category、卡片展示 category 徽章、API 客户端带上 category。

**Files:**
- Modify: `web/src/api/skills.ts`（`Skill` interface 加 `category`；`uploadSkill` 加 `category` 参数）
- Modify: `web/src/features/mcp/skill-library/UploadZone.tsx`（加 category 下拉，传给 uploadSkill）
- Modify: `web/src/features/mcp/skill-library/SkillCard.tsx`（加 category 徽章）
- Test: 前端门禁 `typecheck` + `build`

**Interfaces:**
- Consumes: 后端 `SkillMeta.category`（Task 1）、`POST /skills/upload` 的 `category` 表单字段（Task 2）
- Produces: 上传携带 category；卡片显示 category

- [ ] **Step 1: API 客户端加 category**

`web/src/api/skills.ts`：

`Skill` interface 加字段：

```typescript
export interface Skill {
  id: number;
  name: string;
  slug: string;
  is_official: boolean;
  current_version_label: string | null;
  file_count: number;
  total_bytes: number;
  updated_at: string;
  uploaded_by: number | null;
  category: string;
}
```

`uploadSkill` 加 `category` 参数并写入 FormData：

```typescript
export function uploadSkill(
  name: string,
  files: File[],
  category: string = "general",
): Promise<{ skill_id: number; slug: string; version_label: string }> {
  const form = new FormData();
  form.append("name", name);
  form.append("category", category);
  for (const file of files) {
    const relativePath = (file as File & { webkitRelativePath?: string }).webkitRelativePath;
    form.append("files", file, relativePath || file.name);
  }
  return api<{ skill_id: number; slug: string; version_label: string }>("/api/mcp/skills/upload", {
    method: "POST",
    body: form,
  });
}
```

- [ ] **Step 2: UploadZone 加 category 下拉**

`web/src/features/mcp/skill-library/UploadZone.tsx`：

顶部加常量与 state：

```typescript
const CATEGORY_OPTIONS: { value: string; label: string }[] = [
  { value: "generation", label: "生文" },
  { value: "distribute", label: "发文" },
  { value: "video", label: "视频" },
  { value: "general", label: "通用" },
];
```

在 `UploadZone` 组件里加 state（与其它 useState 并列）：

```typescript
  const [category, setCategory] = useState<string>("general");
```

`handleFiles` 里把 category 传给 uploadSkill：

```typescript
      const r = await uploadSkill(name, files, category);
```

在拖拽区上方（`return` 里 idle 分支的拖拽框之前）加下拉，放进 idle 视图：

```tsx
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
            <span style={{ fontSize: 12.5, color: "var(--fg-2)" }}>业务类别</span>
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              style={{
                height: 30,
                padding: "0 8px",
                borderRadius: "var(--r-sm)",
                border: "1px solid var(--hair)",
                background: "var(--surface-2)",
                color: "var(--fg)",
                fontSize: 12.5,
              }}
            >
              {CATEGORY_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </div>
```

> 放在 idle 分支最外层 `<div>` 内、拖拽 `<div onDragOver...>` 之前，使其只在待上传时可见。

- [ ] **Step 3: SkillCard 加 category 徽章**

`web/src/features/mcp/skill-library/SkillCard.tsx`：

加一个 category 中文映射 + 徽章组件（放在 `CustomBadge` 之后）：

```tsx
const CATEGORY_ZH: Record<string, string> = {
  generation: "生文",
  distribute: "发文",
  video: "视频",
  general: "通用",
};

function CategoryBadge({ category }: { category: string }) {
  return (
    <span style={badgeStyle("var(--cream-2)", "var(--fg-2)", "var(--hair)")}>
      {CATEGORY_ZH[category] ?? category}
    </span>
  );
}
```

在卡片头部 official/custom 徽章之后渲染：

```tsx
        {skill.is_official ? <OfficialBadge /> : <CustomBadge />}
        <CategoryBadge category={skill.category} />
```

- [ ] **Step 4: typecheck + build**

Run: `pnpm --filter @geo/web typecheck && pnpm --filter @geo/web build`
Expected: 两者均绿（无 TS 报错、build 成功）

- [ ] **Step 5: lint + commit**

Run: `pnpm --filter @geo/web lint`（非阻塞，报错记录即可）

```bash
git add web/src/api/skills.ts web/src/features/mcp/skill-library/UploadZone.tsx web/src/features/mcp/skill-library/SkillCard.tsx
git commit -m "feat(skill): 前端上传选 category + 卡片展示类别徽章"
```

---

## Self-Review

**1. Spec coverage（逐条对 spec §5 / §7 / §10）:**
- §5.1 category 列 + 迁移 + data migration + seed → Task 1 ✅
- §5.2 MCP list 端点 + units + 前端不拉 files（前端仍走 `svc.list_skills`，不含 units）→ Task 3 ✅
- §5.3 list_skills 工具 + install slug + MCP_TOOLS_COUNT 26 + _CURATED_ZH → Task 4 ✅
- §5.4 前端 category 选择器 + 徽章 + 客户端 → Task 5 ✅
- §7 上传端点 +category → Task 2；install-payload 不改 ✅
- §10 测试：迁移默认值/seed(T1)、category 上传(T2)、MCP 鉴权+筛+units(T3)、count 26+注册(T4)、typecheck+build(T5) ✅
- §8 向后兼容 install() → goal：Task 4 Step 4 `target = slug or "goal"` ✅

**2. Placeholder scan:** Task 3 Step 2 有一处刻意标注的路径冲突纠正（`GET /api/mcp/skills` 撞 user list → 改 `/skills/catalog`），已给出最终测试并说明删除占位版本；Task 1 Step 8 `scripts_shim` 明确标注为「不存在就删」。无残留 TBD。

**3. Type consistency:** `create_version(..., category="general")`（T1）↔ `upload_skill` 透传（T2）↔ `SkillListItem.category`（T1）↔ `SkillMeta.category`（T1）↔ 前端 `Skill.category`（T5）一致；MCP 端点路径 `/api/mcp/skills/catalog`（T3）↔ `list_skills` 工具打同路径（T4）一致；`extract_unit_names(files, *, fallback_slug)` 签名（T3 Step 1 测试 ↔ Step 4 实现）一致。

**路径冲突已解决：** MCP 列举端点用 `/skills/catalog`（不与 `skills_user_router` 的 `GET /skills` 撞）。工具面向 LLM 命名仍是 `list_skills`。
