# 提示词模板按平台区分 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 `PromptTemplate` 加正交的 `platform` 字段（xiaohongshu/toutiao/wechat_mp，null=通用），生成时能按平台筛选/识别提示词。

**Architecture:** 后端加列+迁移+service 过滤（platform 或通用都返回）；MCP `list_prompt_templates` 暴露 platform + 可选过滤参数；前端编辑加平台下拉+卡片徽标；xhs skill 引导选小红书模板。

**Tech Stack:** FastAPI + SQLAlchemy/Alembic(MySQL) + FastMCP + React/TS。

## Global Constraints

- 分支 `feat/prompt-template-platform` 基于最新 `main`。迁移 `down_revision="0069_game_cull_and_manual"`。
- 已知值 `VALID_PROMPT_PLATFORMS = {"xiaohongshu", "toutiao", "wechat_mp"}`；`null`=通用（不入集合）。非法值 → `ValidationError`（→400）。
- **过滤语义**：`platform` 非 None → `filter(or(platform==值, platform IS NULL))`（通用永远候选）；None → 不过滤。
- **update 直接应用 platform**（不走 scope/is_system 的 None=保持）：`template.platform = platform or None`（前端下拉恒传值，空=通用）。
- 后端测试在容器：`docker compose exec -T app sh -c 'export GEO_TEST_DATABASE_URL="mysql+pymysql://$GEO_DB_USER:$GEO_DB_PASS@$GEO_DB_HOST:$GEO_DB_PORT/geo_test"; cd /app && python -m pytest <path> -q'`。`build_test_app`。
- 前端 host：`pnpm --filter @geo/web typecheck` + `build`。**只 `npx prettier --write` 你改的文件，别跑 `pnpm format`（会重排全 src）。**
- 后端 lint：`ruff check server/` + `ruff format --check server/` + `mypy server/app`。
- 发版 `release-*`（迁移 + web）。

## File Structure
- `server/app/modules/prompt_templates/models.py` — 加 platform 列
- `server/alembic/versions/0070_prompt_template_platform.py` — 迁移
- `server/app/modules/prompt_templates/schemas.py` — Create/Update/Read 加 platform
- `server/app/modules/prompt_templates/service.py` — `_validate_platform` + create/update/_visible_query/list_prompt_templates/list_visible_prompts 加 platform
- `server/app/modules/prompt_templates/router.py` — PUT/POST 透传 platform（如需）
- `server/app/modules/mcp_catalog/router.py` — `/prompt-templates` 加 platform 查询参数
- `server/mcp/tools/catalog.py` — `list_prompt_templates` 加 platform 参数
- `<skill>/skills/xhs-note-creator/SKILL.md` — 引导按平台选
- `web/src/api/prompt-templates.ts` / `web/src/types.ts` / `web/src/features/prompt-templates/PromptsWorkspace.tsx` — 前端
- Test: `server/tests/test_prompt_templates*.py`（若无则新建 `test_prompt_template_platform.py`）

---

### Task 1: 后端 platform 列 + service 过滤 + 迁移 + MCP 端点

**Files:**
- Modify: `models.py`, `schemas.py`, `service.py`, `router.py`, `mcp_catalog/router.py`
- Create: `server/alembic/versions/0070_prompt_template_platform.py`
- Test: `server/tests/test_prompt_template_platform.py`

**Interfaces:**
- Produces：
  - `PromptTemplate.platform: str | None`
  - `service.VALID_PROMPT_PLATFORMS`、`service._validate_platform(platform)`
  - `create_prompt_template(..., platform=None)`、`update_prompt_template(..., platform=None)`
  - `_visible_query(..., platform=None)`、`list_prompt_templates(..., platform=None)`、`list_visible_prompts(..., platform=None)`
  - `PromptTemplateRead.platform`；`/api/mcp/prompt-templates?platform=` 过滤

- [ ] **Step 1: 写失败测试**

创建 `server/tests/test_prompt_template_platform.py`：

```python
"""提示词模板 platform 字段 + 过滤语义。"""
import pytest

pytestmark = pytest.mark.mysql


def _make(db, **kw):
    from server.app.modules.prompt_templates.models import PromptTemplate

    t = PromptTemplate(
        name=kw.get("name", "t"),
        content="c",
        scope="generation",
        is_system=True,
        platform=kw.get("platform"),
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def test_platform_filter_returns_target_and_generic(test_app_module):
    from server.app.db.session import SessionLocal
    from server.app.modules.prompt_templates.service import list_prompt_templates

    with SessionLocal() as db:
        _make(db, name="xhs", platform="xiaohongshu")
        _make(db, name="generic", platform=None)
        _make(db, name="toutiao", platform="toutiao")
        rows = list_prompt_templates(db, scope="generation", platform="xiaohongshu")
        names = {r.name for r in rows}
        assert "xhs" in names and "generic" in names  # 专属 + 通用
        assert "toutiao" not in names  # 别的平台专属不返回
        # 不传 platform → 全部
        allrows = list_prompt_templates(db, scope="generation")
        assert {"xhs", "generic", "toutiao"} <= {r.name for r in allrows}


def test_validate_platform(test_app_module):
    from server.app.modules.prompt_templates.service import _validate_platform
    from server.app.shared.errors import ValidationError

    _validate_platform(None)  # 通用，OK
    _validate_platform("xiaohongshu")  # OK
    with pytest.raises(ValidationError):
        _validate_platform("bogus")
```

> `test_app_module`：按 `build_test_app` 实际提供的 fixture/用法对齐（参考现有 `test_prompt_templates*` 或用显式 `build_test_app(monkeypatch)` + `test_app.cleanup()`）。若无现成 module fixture，改成每测 `build_test_app(monkeypatch)` 模式。

- [ ] **Step 2: 运行确认失败**

Run: `docker compose exec -T app sh -c 'export GEO_TEST_DATABASE_URL="mysql+pymysql://$GEO_DB_USER:$GEO_DB_PASS@$GEO_DB_HOST:$GEO_DB_PORT/geo_test"; cd /app && python -m pytest server/tests/test_prompt_template_platform.py -q'`
Expected: FAIL（platform 列/参数不存在）

- [ ] **Step 3: 模型加列**

`models.py` 的 `PromptTemplate` 里（`scope` 附近）加：

```python
    platform: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
```

- [ ] **Step 4: 迁移 0070**

`server/alembic/versions/0070_prompt_template_platform.py`：

```python
"""prompt_templates.platform

Revision ID: 0070_prompt_template_platform
Revises: 0069_game_cull_and_manual
"""
from alembic import op
import sqlalchemy as sa

revision = "0070_prompt_template_platform"
down_revision = "0069_game_cull_and_manual"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("prompt_templates", sa.Column("platform", sa.String(50), nullable=True))
    op.create_index("ix_prompt_templates_platform", "prompt_templates", ["platform"])


def downgrade() -> None:
    op.drop_index("ix_prompt_templates_platform", table_name="prompt_templates")
    op.drop_column("prompt_templates", "platform")
```

- [ ] **Step 5: schemas 加 platform**

`schemas.py`：
- `PromptTemplateCreate` 加 `platform: str | None = None`
- `PromptTemplateUpdate` 加 `platform: str | None = None`
- `PromptTemplateRead` 加 `platform: str | None`（`from_attributes=True` 自动读）
- `PromptTemplatePatch` 加 `platform: str | None = None`（可选，前端 PATCH 不改平台可不加；加了无害）

- [ ] **Step 6: service 加 platform**

`service.py`：
1. 顶部（`VALID_PROMPT_SCOPES` 附近）加：

```python
VALID_PROMPT_PLATFORMS = {"xiaohongshu", "toutiao", "wechat_mp"}


def _validate_platform(platform: str | None) -> None:
    if platform is not None and platform not in VALID_PROMPT_PLATFORMS:
        raise ValidationError(f"Invalid prompt platform: {platform}")
```

2. `_visible_query(db, *, user_id, scope=None, platform=None)` 末尾（return 前）加：

```python
    if platform is not None:
        query = query.filter(
            or_(PromptTemplate.platform == platform, PromptTemplate.platform.is_(None))
        )
```

3. `list_prompt_templates(db, *, scope=None, enabled_only=False, platform=None)`：加 `_validate_platform(platform)`（在 `_validate_scope` 后）+ 同款 platform 过滤（scope 过滤那段之后）。

4. `list_visible_prompts(db, *, user_id, scope=None, platform=None)`：把 platform 透传给 `_visible_query`。

5. `create_prompt_template(..., platform: str | None = None)`：`_validate_platform(platform)` + `PromptTemplate(..., platform=platform)`。

6. `update_prompt_template(..., platform: str | None = None)`：`_validate_platform(platform)` + `template.platform = platform or None`（**直接应用**，空→null=通用）。

- [ ] **Step 7: router 透传 platform**

`server/app/modules/prompt_templates/router.py`：POST（create）/ PUT（update）handler 把 `payload.platform` 传给 service。（grep `create_prompt_template(` / `update_prompt_template(` 在 router 里的调用，加 `platform=payload.platform`。）

- [ ] **Step 8: MCP catalog 端点加 platform 参数**

`server/app/modules/mcp_catalog/router.py` 的 `mcp_list_prompt_templates`（~L191）：函数签名加 `platform: str | None = None`（Query），传给 `svc_list_templates(..., platform=platform)`。

- [ ] **Step 9: 运行确认通过 + 迁移 round-trip**

Run: `docker compose exec -T app sh -c 'export GEO_TEST_DATABASE_URL="mysql+pymysql://$GEO_DB_USER:$GEO_DB_PASS@$GEO_DB_HOST:$GEO_DB_PORT/geo_test"; cd /app && python -m pytest server/tests/test_prompt_template_platform.py server/tests/test_prompt_templates*.py -q'`
Expected: PASS（新用例 + 既有模板测试都绿）

Run（迁移）: `docker compose exec -T app sh -c 'cd /app && alembic upgrade head && alembic downgrade -1 && alembic upgrade head'`
Expected: 无错。

- [ ] **Step 10: lint + commit**

Run: `docker compose exec -T app sh -c 'cd /app && ruff check server/ && ruff format --check server/app/modules/prompt_templates/ server/app/modules/mcp_catalog/router.py && mypy server/app/modules/prompt_templates/ 2>&1 | tail -2'`

```bash
git add server/app/modules/prompt_templates/ server/app/modules/mcp_catalog/router.py \
  server/alembic/versions/0070_prompt_template_platform.py server/tests/test_prompt_template_platform.py
git commit -m "feat(prompt): PromptTemplate 加 platform 字段 + 平台过滤 + MCP 暴露"
```

---

### Task 2: MCP 工具 platform 参数 + xhs skill 引导

**Files:**
- Modify: `server/mcp/tools/catalog.py`
- Modify: xhs-note-creator SKILL.md（仓库内 `.claude/skills/xhs-note-creator/SKILL.md` 若在；否则 skill 库源）
- Test: 无（thin wrapper）

**Interfaces:**
- Consumes: `/api/mcp/prompt-templates?scope=&platform=`。
- Produces: `list_prompt_templates(scope="generation", platform=None)`。

- [ ] **Step 1: 工具加 platform 参数**

`server/mcp/tools/catalog.py` 的 `list_prompt_templates`（~L117）改：

```python
@mcp.tool()
async def list_prompt_templates(scope: str = "generation", platform: str | None = None) -> dict[str, Any]:
    """List prompt templates filtered by scope, optionally by platform.

    Args:
        scope: "generation" / "ai_format" / "image_search" / "image_companion".
        platform: 平台标识，传了会返回「该平台专属 + 通用(platform=null)」；不传返回全部。
            已知值: xiaohongshu / toutiao / wechat_mp。返回体每条含 platform 字段供识别。
    """
    params: dict[str, Any] = {"scope": scope}
    if platform:
        params["platform"] = platform
    return await _aget("/api/mcp/prompt-templates", params=params)
```

（对齐该文件既有 `_aget`/docstring 风格；上面是示意。）

- [ ] **Step 2: 更新 xhs SKILL.md**

在 xhs-note-creator SKILL.md 的「选精简提示词」步骤，改成：

```markdown
2. 选精简提示词：`list_prompt_templates(scope="generation", platform="xiaohongshu")`
   （返回小红书专属 + 通用模板）；让用户指定，优先选小红书专属、没有则用通用。
```

（SKILL.md 位置：`grep -rl "xhs-note-creator" .claude/skills server/app/modules/loop_skills` 找到实际文件；仓库内 `.claude/skills/xhs-note-creator/SKILL.md` 用 `git add -f`。）

- [ ] **Step 3: 验证**

Run: `docker compose exec -T app sh -c 'cd /app && ruff check server/mcp/tools/catalog.py && python -c "import server.mcp.server; from server.mcp.tools import catalog; print(\"ok\")"'`
Expected: ok（无 import 错）。dev 容器重启后 `/mcp` 可见 `list_prompt_templates` 带 platform 参数。

- [ ] **Step 4: commit**

```bash
git add server/mcp/tools/catalog.py .claude/skills/xhs-note-creator/SKILL.md
git commit -m "feat(mcp): list_prompt_templates 加 platform 参数 + xhs skill 按平台选"
```

---

### Task 3: 前端平台下拉 + 徽标

**Files:**
- Modify: `web/src/api/prompt-templates.ts`, `web/src/types.ts`, `web/src/features/prompt-templates/PromptsWorkspace.tsx`
- Test: `pnpm --filter @geo/web typecheck` + `build`

**Interfaces:**
- Consumes: create/update/list API（加 platform）。
- Produces: 编辑弹窗平台下拉 + 卡片平台徽标。

- [ ] **Step 1: types + api client**

`web/src/types.ts`：`PromptTemplate` 类型加 `platform: string | null;`。
`web/src/api/prompt-templates.ts`：
- `createPromptTemplate` payload 加 `platform?: string | null`
- `updatePromptTemplate` payload 加 `platform?: string | null`
- （`listPromptTemplates` 可选加 platform 参数，前端列表本期不按平台筛，可不加）

- [ ] **Step 2: 平台常量 + 徽标 label**

`PromptsWorkspace.tsx` 顶部加：

```tsx
const PROMPT_PLATFORMS: { value: string; label: string }[] = [
  { value: "", label: "通用" },
  { value: "xiaohongshu", label: "小红书" },
  { value: "toutiao", label: "头条" },
  { value: "wechat_mp", label: "公众号" },
];
const platformLabel = (p: string | null) =>
  PROMPT_PLATFORMS.find((x) => x.value === (p ?? ""))?.label ?? p ?? "通用";
```

- [ ] **Step 3: PromptModal 加平台下拉**

`PromptModal`：加 `const [platform, setPlatform] = useState(initial?.platform ?? "");`，在表单里（scope 附近）加：

```tsx
<select value={platform} onChange={(e) => setPlatform(e.target.value)}>
  {PROMPT_PLATFORMS.map((p) => (
    <option key={p.value} value={p.value}>{p.label}</option>
  ))}
</select>
```

`onSave` 回调签名从 `(name, content, isSystem)` 扩成 `(name, content, isSystem, platform)`；`handleSave` 里传 `platform`。外层 `PromptsWorkspace` 的 `onSave` 实现（调 createPromptTemplate/updatePromptTemplate 处）把 `platform: platform || null` 传进 payload。

> 打开 `PromptsWorkspace.tsx` 对齐 `onSave` 的实际定义与调用（现签名 `(name, content, isSystem) => Promise<void>`，见 L76）。

- [ ] **Step 4: 卡片平台徽标**

在模板卡片 meta 区（scope/is_system 徽标旁）加：

```tsx
<span className="badge">{platformLabel(prompt.platform)}</span>
```

- [ ] **Step 5: 门禁**

Run: `npx prettier --write web/src/api/prompt-templates.ts web/src/types.ts web/src/features/prompt-templates/PromptsWorkspace.tsx`（**只格式化这三个文件**）
Run: `pnpm --filter @geo/web typecheck && pnpm --filter @geo/web build`
Expected: 均通过。

- [ ] **Step 6: commit**

```bash
git add web/src/api/prompt-templates.ts web/src/types.ts web/src/features/prompt-templates/PromptsWorkspace.tsx
git commit -m "feat(web): 提示词编辑加平台下拉 + 卡片平台徽标"
```

---

## Self-Review 结论
- **Spec 覆盖**：①platform 列+迁移→T1;②已知值+校验→T1 `_validate_platform`;③过滤语义(platform 或 null)→T1 `_visible_query`/`list_prompt_templates`;④MCP 暴露+过滤→T1 端点 + T2 工具;⑤前端下拉+徽标→T3;⑥skill 引导→T2;update 直接应用→T1 Step6。均落任务。
- **Placeholder 扫描**：测试 fixture、onSave 签名、SKILL.md 路径标「对齐现有」——实现指令非 TBD。代码完整。
- **类型一致**：`platform: str|None` 在 model/schema/service/Read/前端类型一致;过滤用 `or_(==, is_(None))` 在 _visible_query 与 list_prompt_templates 一致;`VALID_PROMPT_PLATFORMS` 单一真源(service)。
- **风险**：`update_prompt_template` 改成「直接应用 platform」与 scope/is_system 的「None=保持」不同——T1 Step6 显式说明;router 须始终传 payload.platform(前端恒传)。
