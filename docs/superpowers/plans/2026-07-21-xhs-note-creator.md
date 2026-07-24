# 小红书图文创作 skill（xhs-note-creator）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Auto-Redbook 的小红书卡片渲染器移植进 geo 后端，让主对话能把「已审核库」文章精简成小红书图文、渲染成卡片、落「未审核库」并打「小红书图文」徽标；封装成可安装/升级的 skill。

**Architecture:** 新后端模块 `xhs_cards/` 完全对标 `video/` 模块的异步 job 模式（daemon 线程渲染 → MinIO → 回吐 URL），由 `compose_xhs_cards`/`get_xhs_status` 两个 MCP 工具驱动。主对话拿到卡片 URL 后拼 markdown，经新增的 `save_xhs_note` 工具走（放宽后的）`/save-from-mcp` 端点落库，`content_type` 列驱动前端徽标。skill 进现有 Skill 库分发。

**Tech Stack:** FastAPI + SQLAlchemy/Alembic(MySQL) + Playwright(chromium，容器内) + MinIO + FastMCP + React/TS。

## Global Constraints

- **MySQL only**；迁移 `down_revision` 从当前 head `0064` 往后链（`0065` → `0066`）。
- **MCP 端点**：独立 sub-router、`dependencies=[Depends(require_mcp_token)]`、未捕获异常走 `core/mcp_errors.mcp_exception_response(exc, context=...)`。
- **异步 job 三态四值**：`status ∈ pending/running/done/failed`，对标 `VideoJob`。
- **渲染只在容器**：Playwright/chromium 仅 Docker 有；渲染集成测试进 dev 容器跑，不进 CI。真 Playwright 测试用 `@pytest.mark.mysql` 之外的显式跳过守卫（`GEO_XHS_RENDER_LIVE=1` 才跑）。
- **零配置生文**：精简由主对话完成，后端不调 LLM，不需要 `GEO_AI_API_KEY`。
- **不移植**：`publish_xhs.py`、`render_xhs_v2.*`、xhs cookie/`.env`（不做自动发布）。
- **只加一次 content_type 迁移**；`source_article_id` 溯源塞 `metrics` JSON，不加第二列。
- **保持 `/goal` 热路径不变**：`save_article` MCP 工具与其现有调用签名**零改动**；`/save-from-mcp` 放宽字段时，`question_item_id` 存在的旧路径行为必须字节级不变（测试锁定）。
- **`MCP_TOOLS_COUNT`** 真值在 `mcp_catalog/connect_router.py`，本计划新增 3 个工具：33 → 36。CLAUDE.md「Tool 三组」同步。
- 后端 lint 门禁：`ruff check server/` + `ruff format --check server/` + `mypy server/app`。前端门禁：`pnpm --filter @geo/web typecheck` + `build`。
- `Auto-Redbook-Skills-main/` 已在 `.gitignore`，不提交；移植是**复制文件内容**进 `server/app/modules/xhs_cards/assets/`，不引用仓外路径。

## File Structure

**Phase 1（渲染器 + MCP）**
- `server/app/modules/xhs_cards/__init__.py` — 模块标识
- `server/app/modules/xhs_cards/assets/{cover.html,card.html,styles.css,themes/*.css}` — 从 Auto-Redbook 复制的渲染模板/主题（8 套主题）
- `server/app/modules/xhs_cards/render.py` — 渲染核心（frontmatter 解析、分页、HTML 组装、Playwright 截图 → PNG bytes）
- `server/app/modules/xhs_cards/store.py` — MinIO 桶 `geo-xhs-cards` 存取
- `server/app/modules/xhs_cards/models.py` — `XhsRenderJob`
- `server/app/modules/xhs_cards/schemas.py` — `ComposeXhsRequest` + 校验
- `server/app/modules/xhs_cards/service.py` — `create_render_job` / `run_render_job` / `spawn_render_job` / `list_render_jobs`
- `server/app/modules/xhs_cards/router.py` — `xhs_mcp_router`（compose/status）+ `xhs_files_router`（公开图片服务）
- `server/mcp/tools/xhs.py` — `compose_xhs_cards` / `get_xhs_status` / `save_xhs_note` 三个 MCP 工具
- `server/alembic/versions/0065_xhs_render_jobs.py` — 建表
- `server/tests/test_xhs_render.py` / `test_xhs_service.py` / `test_xhs_api.py` — 测试

**Phase 2（落库 + 徽标 + skill）**
- `server/app/modules/articles/models.py` — 加 `content_type` 列
- `server/alembic/versions/0066_article_content_type.py` — 建列
- `server/app/modules/articles/routers/mcp.py` — 放宽 `SaveArticleFromMcpPayload` + 落库逻辑
- `web/src/features/content/**` + `web/src/api/**` — 徽标 + 列表字段
- `<skill bundle>/skills/xhs-note-creator/SKILL.md` — skill 定义（上传进 Skill 库）

**Wiring**
- `server/app/main.py` — import models、注入 `bg_session_factory`、挂 4 个 router
- `requirements.txt` — 加 `PyYAML`
- `server/app/modules/mcp_catalog/connect_router.py` — `MCP_TOOLS_COUNT` 33→36
- `CLAUDE.md` — 模块 + 工具清单同步

---

# Phase 1 — 后端渲染模块 + MCP 工具（先容器验证出图）

### Task 1: 移植渲染资产 + 纯函数渲染核心（无 Playwright）

**Files:**
- Create: `server/app/modules/xhs_cards/__init__.py`
- Create: `server/app/modules/xhs_cards/assets/cover.html`（复制自 `Auto-Redbook-Skills-main/assets/cover.html`）
- Create: `server/app/modules/xhs_cards/assets/card.html`（复制自同名）
- Create: `server/app/modules/xhs_cards/assets/styles.css`（复制自同名）
- Create: `server/app/modules/xhs_cards/assets/themes/{sketch,default,playful-geometric,neo-brutalism,botanical,professional,retro,terminal}.css`（复制自 `Auto-Redbook-Skills-main/assets/themes/`）
- Create: `server/app/modules/xhs_cards/render.py`
- Modify: `requirements.txt`（加 `PyYAML`）
- Test: `server/tests/test_xhs_render.py`

**Interfaces:**
- Produces（`render.py`，纯函数，供 Task 2 与 service 调用）：
  - `AVAILABLE_THEMES: list[str]`、`PAGING_MODES: list[str]`
  - `parse_markdown_string(md: str) -> dict` → `{"metadata": {"emoji","title","subtitle"}, "body": str}`
  - `split_content_by_separator(body: str) -> list[str]`
  - `convert_markdown_to_html(md: str) -> str`
  - `load_theme_css(theme: str) -> str`（未知主题回落 `default`）
  - `generate_cover_html(metadata: dict, theme: str, width: int, height: int) -> str`
  - `generate_card_html(content: str, theme: str, page_number: int, width: int, height: int) -> str`
  - 模块常量 `ASSETS_DIR = Path(__file__).parent / "assets"`、`THEMES_DIR = ASSETS_DIR / "themes"`

- [ ] **Step 1: 复制渲染资产**

把 `Auto-Redbook-Skills-main/assets/` 下的 `cover.html`、`card.html`、`styles.css`、`themes/*.css`（8 个）逐字复制到 `server/app/modules/xhs_cards/assets/` 对应位置（内容不改）。建空 `server/app/modules/xhs_cards/__init__.py`（内容：`"""小红书卡片渲染模块（移植自 Auto-Redbook-Skills，仅渲染，不含发布）。"""`）。

验证复制完整：

Run: `ls server/app/modules/xhs_cards/assets/themes/ | wc -l`
Expected: `8`

- [ ] **Step 2: 加 PyYAML 依赖**

在 `requirements.txt` 的 `markdown` 行下一行加：

```
PyYAML>=6.0
```

（dev 容器已装则 no-op；确保声明存在。）

- [ ] **Step 3: 写失败测试（纯函数）**

创建 `server/tests/test_xhs_render.py`：

```python
"""xhs_cards.render 纯函数测试（不触 Playwright）。"""
from server.app.modules.xhs_cards import render


def test_parse_frontmatter_and_body():
    md = '---\nemoji: "🚀"\ntitle: "封面标题"\nsubtitle: "副标题"\n---\n\n正文第一段\n'
    out = render.parse_markdown_string(md)
    assert out["metadata"]["title"] == "封面标题"
    assert out["metadata"]["emoji"] == "🚀"
    assert out["metadata"]["subtitle"] == "副标题"
    assert "正文第一段" in out["body"]


def test_split_by_separator():
    body = "第一张\n\n---\n\n第二张\n\n---\n\n第三张"
    cards = render.split_content_by_separator(body)
    assert len(cards) == 3
    assert cards[0].strip() == "第一张"
    assert cards[2].strip() == "第三张"


def test_load_theme_css_fallback():
    assert "sketch" in render.AVAILABLE_THEMES
    css = render.load_theme_css("no-such-theme")
    assert css  # 回落 default，非空


def test_cover_html_contains_title():
    html = render.generate_cover_html(
        {"emoji": "🔥", "title": "标题X", "subtitle": "副X"}, "default", 1080, 1440
    )
    assert "标题X" in html and "<html" in html.lower()
```

- [ ] **Step 4: 运行确认失败**

Run: `pytest server/tests/test_xhs_render.py -q`
Expected: FAIL（`ModuleNotFoundError: xhs_cards.render` 或函数未定义）

- [ ] **Step 5: 实现 render.py 纯函数部分**

从 `Auto-Redbook-Skills-main/scripts/render_xhs.py` 移植以下函数到 `server/app/modules/xhs_cards/render.py`，改动点：
1. 路径常量指向模块内 assets（`ASSETS_DIR = Path(__file__).parent / "assets"`）。
2. `parse_markdown_file(path)` → 改名 `parse_markdown_string(md: str)`，直接吃字符串（用 `yaml.safe_load` 解析 `---...---` frontmatter，剩余为 body），返回 `{"metadata": {...}, "body": ...}`；metadata 缺省 `emoji/title/subtitle` 补空串。
3. 保留 `split_content_by_separator`、`convert_markdown_to_html`（`markdown.markdown(md, extensions=["extra"])`）、`load_theme_css`（读 `THEMES_DIR/{theme}.css`，不存在回落 `default.css`）、`generate_cover_html`、`generate_card_html`（照抄模板拼接逻辑，读 `ASSETS_DIR/cover.html`、`card.html`、`styles.css`）。
4. `AVAILABLE_THEMES`、`PAGING_MODES` 常量照抄。

> 参考原文件行号：`parse_markdown_file`@73、`split_content_by_separator`@98、`convert_markdown_to_html`@104、`load_theme_css`@130、`generate_cover_html`@145、`generate_card_html`@276。逐函数搬运并按上面改路径/签名。

- [ ] **Step 6: 运行确认通过**

Run: `pytest server/tests/test_xhs_render.py -q`
Expected: PASS（4 passed）

- [ ] **Step 7: lint + commit**

Run: `ruff check server/app/modules/xhs_cards/ server/tests/test_xhs_render.py && ruff format server/app/modules/xhs_cards/ server/tests/test_xhs_render.py`

```bash
git add server/app/modules/xhs_cards/ server/tests/test_xhs_render.py requirements.txt
git commit -m "feat(xhs): 移植小红书渲染资产 + 纯函数渲染核心"
```

---

### Task 2: Playwright 截图 → PNG bytes 全流程

**Files:**
- Modify: `server/app/modules/xhs_cards/render.py`
- Test: `server/tests/test_xhs_render.py`（加单测，monkeypatch 截图函数）

**Interfaces:**
- Consumes: Task 1 的纯函数。
- Produces:
  - `async def render_html_to_png_bytes(html: str, width: int, height: int, dpr: int) -> bytes`（Playwright 截图，返回 PNG bytes）
  - `async def render_markdown_to_card_bytes(md: str, *, theme: str, mode: str, width: int = 1080, height: int = 1440, dpr: int = 2) -> dict` → `{"cover": bytes, "cards": list[bytes]}`
  - `def resolve_screenshot_fn()`（返回当前截图实现，供测试 monkeypatch 编排层而不起浏览器）

- [ ] **Step 1: 写失败测试（编排层，patch 截图）**

在 `server/tests/test_xhs_render.py` 追加：

```python
import asyncio
import pytest
from server.app.modules.xhs_cards import render as R


def test_render_markdown_orchestration_separator(monkeypatch):
    # 用假的截图函数：返回可辨识的 bytes，避免起 chromium
    async def fake_shot(html, width, height, dpr):
        return b"PNG:" + (b"cover" if "cover" in html.lower() else b"card")

    monkeypatch.setattr(R, "render_html_to_png_bytes", fake_shot)
    md = '---\ntitle: "T"\nsubtitle: "S"\nemoji: "🔥"\n---\n\nA\n\n---\n\nB'
    out = asyncio.run(
        R.render_markdown_to_card_bytes(md, theme="default", mode="separator")
    )
    assert out["cover"].startswith(b"PNG:")
    assert len(out["cards"]) == 2  # A / B 两张
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest server/tests/test_xhs_render.py::test_render_markdown_orchestration_separator -q`
Expected: FAIL（`render_markdown_to_card_bytes` 未定义）

- [ ] **Step 3: 实现截图 + 编排**

在 `render.py` 加：

```python
import tempfile, os
from playwright.async_api import async_playwright


async def render_html_to_png_bytes(html: str, width: int, height: int, dpr: int) -> bytes:
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--no-sandbox"])
        page = await browser.new_page(
            viewport={"width": width, "height": height}, device_scale_factor=dpr
        )
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as f:
            f.write(html)
            path = f.name
        try:
            await page.goto(f"file://{path}")
            await page.wait_for_timeout(200)
            png = await page.screenshot(full_page=True)
        finally:
            await browser.close()
            os.unlink(path)
        return png


async def render_markdown_to_card_bytes(
    md: str, *, theme: str, mode: str, width: int = 1080, height: int = 1440, dpr: int = 2
) -> dict:
    if theme not in AVAILABLE_THEMES:
        theme = "sketch"
    if mode not in PAGING_MODES:
        mode = "auto-split"
    parsed = parse_markdown_string(md)
    metadata, body = parsed["metadata"], parsed["body"]

    cover_html = generate_cover_html(metadata, theme, width, height)
    cover_png = await render_html_to_png_bytes(cover_html, width, height, dpr)

    if mode == "separator":
        chunks = split_content_by_separator(body)
    else:
        # auto-split / auto-fit / dynamic：MVP 先按 separator 语义 + 无分隔时整体一张。
        # 后续如需精确 auto-split，移植原 auto_split_content（@529，依赖真实渲染高度）。
        chunks = split_content_by_separator(body) if "---" in body else [body]

    cards: list[bytes] = []
    for i, chunk in enumerate(chunks, start=1):
        card_html = generate_card_html(chunk, theme, i, width, height)
        cards.append(await render_html_to_png_bytes(card_html, width, height, dpr))
    return {"cover": cover_png, "cards": cards}
```

> 说明：`auto-split` 精确切分依赖「渲染后真实高度」（原 `auto_split_content`@529），移植成本高且非 MVP 必需。本期先退化为 separator 语义。skill 侧引导主对话用 `---` 手动分页，效果等价。此退化写进 SKILL.md（Task 11）。

- [ ] **Step 4: 运行确认通过**

Run: `pytest server/tests/test_xhs_render.py -q`
Expected: PASS（5 passed）

- [ ] **Step 5: 真 Playwright 冒烟测试（守卫，仅容器跑）**

追加（默认跳过，`GEO_XHS_RENDER_LIVE=1` 才跑）：

```python
@pytest.mark.skipif(
    os.environ.get("GEO_XHS_RENDER_LIVE") != "1",
    reason="需容器内 chromium；设 GEO_XHS_RENDER_LIVE=1 启用",
)
def test_live_render_produces_png():
    md = '---\ntitle: "真渲染"\nsubtitle: "冒烟"\nemoji: "✅"\n---\n\n正文\n\n---\n\n第二张'
    out = asyncio.run(R.render_markdown_to_card_bytes(md, theme="sketch", mode="separator"))
    assert out["cover"][:8] == b"\x89PNG\r\n\x1a\n"
    assert all(c[:8] == b"\x89PNG\r\n\x1a\n" for c in out["cards"])
```

在 dev 容器跑一次人工验收：

Run（容器内）: `GEO_XHS_RENDER_LIVE=1 pytest server/tests/test_xhs_render.py::test_live_render_produces_png -q`
Expected: PASS（真出 PNG 魔数）

- [ ] **Step 6: lint + commit**

```bash
git add server/app/modules/xhs_cards/render.py server/tests/test_xhs_render.py
git commit -m "feat(xhs): Playwright 截图 + markdown→卡片 PNG 编排"
```

---

### Task 3: MinIO 存储 + XhsRenderJob 模型 + 迁移

**Files:**
- Create: `server/app/modules/xhs_cards/store.py`
- Create: `server/app/modules/xhs_cards/models.py`
- Create: `server/alembic/versions/0065_xhs_render_jobs.py`
- Modify: `server/app/main.py`（import model 注册表）
- Test: `server/tests/test_xhs_service.py`

**Interfaces:**
- Produces:
  - `store.XHS_BUCKET = "geo-xhs-cards"`、`store.ensure_bucket()`、`store.put_png(key: str, data: bytes)`、`store.get_object(key: str) -> bytes`
  - `models.XhsRenderJob`：列 `id/job_id(str32 uniq)/source_article_id(int|None)/status(str20)/theme/mode/cover_key(str500|None)/card_keys(JSON|None)/error(Text|None)/created_at/updated_at`

- [ ] **Step 1: 写 store.py**

```python
"""小红书卡片 MinIO 存储：复用 image_library.store 底层 client，独立 bucket。"""
from __future__ import annotations
from server.app.modules.image_library import store as minio_store

XHS_BUCKET = "geo-xhs-cards"


def ensure_bucket() -> None:
    minio_store.ensure_bucket(XHS_BUCKET)


def put_png(key: str, data: bytes) -> None:
    minio_store.upload_image(XHS_BUCKET, key, data, "image/png")


def get_object(key: str) -> bytes:
    return minio_store.get_object_bytes(XHS_BUCKET, key)
```

- [ ] **Step 2: 写 models.py**

```python
"""小红书卡片渲染任务 ORM。产物 PNG 以 MinIO 对象 key 记录。"""
from __future__ import annotations
from datetime import datetime
from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from server.app.core.time import utcnow
from server.app.db.base import Base


class XhsRenderJob(Base):
    __tablename__ = "xhs_render_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    source_article_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    theme: Mapped[str] = mapped_column(String(50), nullable=False, server_default="sketch")
    mode: Mapped[str] = mapped_column(String(20), nullable=False, server_default="separator")
    cover_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    card_keys: Mapped[list | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
```

- [ ] **Step 3: 迁移 0065**

创建 `server/alembic/versions/0065_xhs_render_jobs.py`（`down_revision = "0064"`）：

```python
"""xhs_render_jobs table

Revision ID: 0065_xhs_render_jobs
Revises: 0064_qref_external_ingestion
"""
from alembic import op
import sqlalchemy as sa

revision = "0065_xhs_render_jobs"
down_revision = "0064_qref_external_ingestion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "xhs_render_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.String(32), nullable=False),
        sa.Column("source_article_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("theme", sa.String(50), nullable=False, server_default="sketch"),
        sa.Column("mode", sa.String(20), nullable=False, server_default="separator"),
        sa.Column("cover_key", sa.String(500), nullable=True),
        sa.Column("card_keys", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_xhs_render_jobs_job_id", "xhs_render_jobs", ["job_id"], unique=True)
    op.create_index(
        "ix_xhs_render_jobs_source_article_id", "xhs_render_jobs", ["source_article_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_xhs_render_jobs_source_article_id", table_name="xhs_render_jobs")
    op.drop_index("ix_xhs_render_jobs_job_id", table_name="xhs_render_jobs")
    op.drop_table("xhs_render_jobs")
```

> 确认 `down_revision` 取真实 head：`ls -t server/alembic/versions/*.py | head -1` 若已非 0064 则改成实际最新 revision id。

- [ ] **Step 4: main.py 注册 model**

在 `server/app/main.py` 顶部 model import 区（`import server.app.modules.video.models  # noqa: F401` 那一带）加一行：

```python
import server.app.modules.xhs_cards.models  # noqa: F401  (register XhsRenderJob table)
```

- [ ] **Step 5: 写失败测试（模型 CRUD）**

创建 `server/tests/test_xhs_service.py`：

```python
"""xhs_cards service / model 测试。"""
import pytest
from server.app.modules.xhs_cards.models import XhsRenderJob

pytestmark = pytest.mark.mysql


def test_render_job_row(test_app):
    from server.app.db.session import SessionLocal
    db = SessionLocal()
    try:
        job = XhsRenderJob(job_id="abc123", status="pending", theme="sketch", mode="separator")
        db.add(job); db.commit(); db.refresh(job)
        assert job.id is not None
        got = db.query(XhsRenderJob).filter_by(job_id="abc123").one()
        assert got.status == "pending"
    finally:
        db.close()
```

> `test_app` fixture 见 `server/tests/conftest.py`（`build_test_app` 会建一次性 schema）。若无该 fixture，按现有测试用 `build_test_app(monkeypatch)` 模式改写；参考 `test_video*.py` 的建 app 方式。

- [ ] **Step 6: 运行确认失败 → 建库 → 通过**

Run: `GEO_TEST_DATABASE_URL=... pytest server/tests/test_xhs_service.py::test_render_job_row -q`
Expected: 先 FAIL（表不存在）→ `build_test_app` 会 `create_all`，若走 alembic 则先 `alembic upgrade head`；确认 PASS。

- [ ] **Step 7: lint + commit**

```bash
git add server/app/modules/xhs_cards/store.py server/app/modules/xhs_cards/models.py \
  server/alembic/versions/0065_xhs_render_jobs.py server/app/main.py server/tests/test_xhs_service.py
git commit -m "feat(xhs): XhsRenderJob 模型 + MinIO 存储 + 建表迁移"
```

---

### Task 4: service 层（建 job + 后台渲染线程）

**Files:**
- Create: `server/app/modules/xhs_cards/schemas.py`
- Create: `server/app/modules/xhs_cards/service.py`
- Modify: `server/app/main.py`（注入 `bg_session_factory`）
- Test: `server/tests/test_xhs_service.py`

**Interfaces:**
- Consumes: `render.render_markdown_to_card_bytes`、`store.*`、`models.XhsRenderJob`。
- Produces:
  - `schemas.ComposeXhsRequest`：`render_markdown: str`、`theme: str = "sketch"`、`mode: str = "separator"`、`width: int = 1080`、`dpr: int = 2`、`source_article_id: int | None = None`
  - `service.create_render_job(db, req) -> XhsRenderJob`
  - `service.run_render_job(job_id, session_factory) -> None`
  - `service.spawn_render_job(job_id) -> None`
  - `service.list_render_jobs(db, *, status, skip, limit) -> tuple[list[XhsRenderJob], int]`
  - 模块级 `bg_session_factory: Callable | None = None`

- [ ] **Step 1: 写 schemas.py**

```python
from __future__ import annotations
from pydantic import BaseModel, Field


class ComposeXhsRequest(BaseModel):
    render_markdown: str = Field(min_length=1)
    theme: str = "sketch"
    mode: str = "separator"
    width: int = Field(default=1080, ge=320, le=2160)
    dpr: int = Field(default=2, ge=1, le=3)
    source_article_id: int | None = None
```

- [ ] **Step 2: 写失败测试（状态机，patch 渲染器）**

在 `server/tests/test_xhs_service.py` 追加：

```python
def test_run_render_job_success(test_app, monkeypatch):
    from server.app.db.session import SessionLocal
    from server.app.modules.xhs_cards import service, render, store
    from server.app.modules.xhs_cards.schemas import ComposeXhsRequest

    async def fake_render(md, *, theme, mode, width=1080, height=1440, dpr=2):
        return {"cover": b"\x89PNGcover", "cards": [b"\x89PNGa", b"\x89PNGb"]}

    monkeypatch.setattr(render, "render_markdown_to_card_bytes", fake_render)
    monkeypatch.setattr(store, "ensure_bucket", lambda: None)
    monkeypatch.setattr(store, "put_png", lambda k, d: None)

    db = SessionLocal()
    try:
        job = service.create_render_job(db, ComposeXhsRequest(render_markdown="---\ntitle: T\n---\nA\n---\nB"))
        jid = job.job_id
    finally:
        db.close()

    service.run_render_job(jid, SessionLocal)

    db = SessionLocal()
    try:
        from server.app.modules.xhs_cards.models import XhsRenderJob
        got = db.query(XhsRenderJob).filter_by(job_id=jid).one()
        assert got.status == "done"
        assert got.cover_key and len(got.card_keys) == 2
    finally:
        db.close()


def test_run_render_job_failure(test_app, monkeypatch):
    from server.app.db.session import SessionLocal
    from server.app.modules.xhs_cards import service, render, store
    from server.app.modules.xhs_cards.schemas import ComposeXhsRequest
    from server.app.modules.xhs_cards.models import XhsRenderJob

    async def boom(*a, **k):
        raise RuntimeError("render boom")

    monkeypatch.setattr(store, "ensure_bucket", lambda: None)
    monkeypatch.setattr(render, "render_markdown_to_card_bytes", boom)

    db = SessionLocal()
    try:
        job = service.create_render_job(db, ComposeXhsRequest(render_markdown="x"))
        jid = job.job_id
    finally:
        db.close()
    service.run_render_job(jid, SessionLocal)
    db = SessionLocal()
    try:
        got = db.query(XhsRenderJob).filter_by(job_id=jid).one()
        assert got.status == "failed" and "render boom" in got.error
    finally:
        db.close()
```

- [ ] **Step 3: 运行确认失败**

Run: `GEO_TEST_DATABASE_URL=... pytest server/tests/test_xhs_service.py -q`
Expected: FAIL（`service` 未定义 create_render_job/run_render_job）

- [ ] **Step 4: 实现 service.py**

对标 `server/app/modules/video/service.py`：

```python
"""小红书卡片渲染 service：render-markdown → Playwright → MinIO → XhsRenderJob。

后台线程执行（bg_session_factory），全程确定性、不调 LLM。
"""
from __future__ import annotations
import asyncio
import logging
import threading
import uuid
from collections.abc import Callable
from typing import Any
from sqlalchemy.orm import Session
from server.app.modules.xhs_cards import render, store
from server.app.modules.xhs_cards.models import XhsRenderJob
from server.app.modules.xhs_cards.schemas import ComposeXhsRequest
from server.app.shared.errors import ClientError

logger = logging.getLogger(__name__)

bg_session_factory: Callable[[], Any] | None = None


def create_render_job(db: Session, req: ComposeXhsRequest) -> XhsRenderJob:
    job = XhsRenderJob(
        job_id=uuid.uuid4().hex,
        source_article_id=req.source_article_id,
        status="pending",
        theme=req.theme,
        mode=req.mode,
    )
    db.add(job); db.commit(); db.refresh(job)
    return job


def run_render_job(job_id: str, session_factory) -> None:
    db = session_factory()
    try:
        job = db.query(XhsRenderJob).filter(XhsRenderJob.job_id == job_id).one()
        job.status = "running"; db.commit()
        store.ensure_bucket()
        result = asyncio.run(
            render.render_markdown_to_card_bytes(
                job.render_markdown_or_body(), theme=job.theme, mode=job.mode
            )
        ) if hasattr(job, "render_markdown_or_body") else None
        # render_markdown 不落库（避免大字段进表）：改从 req 传入。见下方说明。
        raise NotImplementedError  # 占位，Step 4b 修正
    except Exception as exc:  # noqa: BLE001
        logger.exception("xhs 渲染失败: job_id=%s", job_id)
        db.rollback()
        try:
            job = db.query(XhsRenderJob).filter(XhsRenderJob.job_id == job_id).one()
            job.status = "failed"; job.error = f"{type(exc).__name__}: {str(exc)[:500]}"
            db.commit()
        except Exception:
            logger.exception("写 failed 也失败: job_id=%s", job_id)
    finally:
        db.close()
```

- [ ] **Step 4b: 修正 render_markdown 传递方式**

`render_markdown` 是大文本、不宜落 job 表。改为：`create_render_job` 之后、`spawn_render_job` 之前，把 markdown 暂存进进程内 dict（`_PENDING_MD: dict[str, str]`，key=job_id），`run_render_job` 取用后删除。重写 service.py 的运行部分：

```python
_PENDING_MD: dict[str, str] = {}


def create_render_job(db: Session, req: ComposeXhsRequest) -> XhsRenderJob:
    job = XhsRenderJob(
        job_id=uuid.uuid4().hex, source_article_id=req.source_article_id,
        status="pending", theme=req.theme, mode=req.mode,
    )
    db.add(job); db.commit(); db.refresh(job)
    _PENDING_MD[job.job_id] = req.render_markdown
    _RENDER_PARAMS[job.job_id] = (req.width, req.dpr)
    return job


_RENDER_PARAMS: dict[str, tuple[int, int]] = {}


def run_render_job(job_id: str, session_factory) -> None:
    db = session_factory()
    md = _PENDING_MD.pop(job_id, "")
    width, dpr = _RENDER_PARAMS.pop(job_id, (1080, 2))
    try:
        job = db.query(XhsRenderJob).filter(XhsRenderJob.job_id == job_id).one()
        job.status = "running"; db.commit()
        store.ensure_bucket()
        result = asyncio.run(
            render.render_markdown_to_card_bytes(
                md, theme=job.theme, mode=job.mode, width=width, dpr=dpr
            )
        )
        cover_key = f"{job.job_id}/cover.png"
        store.put_png(cover_key, result["cover"])
        card_keys: list[str] = []
        for i, png in enumerate(result["cards"], start=1):
            k = f"{job.job_id}/card_{i}.png"
            store.put_png(k, png); card_keys.append(k)
        job.cover_key = cover_key; job.card_keys = card_keys
        job.status = "done"; db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.exception("xhs 渲染失败: job_id=%s", job_id)
        db.rollback()
        try:
            job = db.query(XhsRenderJob).filter(XhsRenderJob.job_id == job_id).one()
            job.status = "failed"; job.error = f"{type(exc).__name__}: {str(exc)[:500]}"
            db.commit()
        except Exception:
            logger.exception("写 failed 也失败: job_id=%s", job_id)
    finally:
        db.close()


def spawn_render_job(job_id: str) -> None:
    if bg_session_factory is None:
        raise ClientError("bg_session_factory 未注入（create_app 未执行？）")
    threading.Thread(target=run_render_job, args=(job_id, bg_session_factory), daemon=True).start()


def list_render_jobs(db, *, status=None, skip=0, limit=24):
    q = db.query(XhsRenderJob)
    if status:
        q = q.filter(XhsRenderJob.status == status)
    total = q.count()
    rows = q.order_by(XhsRenderJob.created_at.desc()).offset(skip).limit(limit).all()
    return rows, total
```

> 进程内 dict 传大文本仅在同进程 spawn 场景成立（与 pipelines/generation 一致：无独立 worker，`bg_session_factory=SessionLocal`）。测试直接调 `run_render_job` 前需保证 `create_render_job` 已填 `_PENDING_MD`（同进程，成立）。

- [ ] **Step 5: main.py 注入 bg_session_factory**

在 `create_app()` 里，与 `video.service.bg_session_factory = SessionLocal` 同处，加：

```python
import server.app.modules.xhs_cards.service as _xhs_service
_xhs_service.bg_session_factory = SessionLocal
```

（若 video 的注入写法是 `from ... import service as video_service; video_service.bg_session_factory = SessionLocal`，照抄该风格。）

- [ ] **Step 6: 运行确认通过**

Run: `GEO_TEST_DATABASE_URL=... pytest server/tests/test_xhs_service.py -q`
Expected: PASS（success + failure + row 三测）

- [ ] **Step 7: lint + commit**

```bash
git add server/app/modules/xhs_cards/schemas.py server/app/modules/xhs_cards/service.py \
  server/app/main.py server/tests/test_xhs_service.py
git commit -m "feat(xhs): 渲染 job service + 后台线程状态机"
```

---

### Task 5: router（compose/status MCP + 公开图片服务）+ 挂载

**Files:**
- Create: `server/app/modules/xhs_cards/router.py`
- Modify: `server/app/main.py`（挂 `xhs_mcp_router` + `xhs_files_router`）
- Test: `server/tests/test_xhs_api.py`

**Interfaces:**
- Consumes: `service.*`、`store.get_object`、`models.XhsRenderJob`。
- Produces:
  - `xhs_mcp_router`：`POST /compose`（202）、`GET /status/{job_id}`（挂 `/api/xhs-cards`）
  - `xhs_files_router`：`GET /file/{job_id}/cover`、`GET /file/{job_id}/card/{idx}`（公开，返回 PNG）
  - status 返回体含 `cover_url` / `card_urls[]`（形如 `/api/xhs-cards/file/{job_id}/cover`、`.../card/{i}`）

- [ ] **Step 1: 写失败测试**

创建 `server/tests/test_xhs_api.py`：

```python
import pytest
pytestmark = pytest.mark.mysql


def test_compose_requires_mcp_token(test_app):
    # 无 X-MCP-Token → 401
    resp = test_app.client.post("/api/xhs-cards/compose", json={"render_markdown": "x"})
    assert resp.status_code == 401


def test_compose_and_status(test_app, monkeypatch):
    from server.app.modules.xhs_cards import service
    monkeypatch.setattr(service, "spawn_render_job", lambda jid: None)  # 不真起线程
    headers = {"X-MCP-Token": test_app.mcp_token}
    r = test_app.client.post(
        "/api/xhs-cards/compose",
        json={"render_markdown": "---\ntitle: T\n---\nA", "theme": "sketch", "mode": "separator"},
        headers=headers,
    )
    assert r.status_code == 202
    jid = r.json()["data"]["job_id"]
    s = test_app.client.get(f"/api/xhs-cards/status/{jid}", headers=headers)
    assert s.status_code == 200 and s.json()["data"]["status"] == "pending"
```

> `test_app.mcp_token` / `test_app.client`：按 `build_test_app` 的既有属性名对齐（参考 `test_video*` 或其它 MCP 端点测试怎么取 token / client）。若命名不同照该文件改。

- [ ] **Step 2: 运行确认失败**

Run: `GEO_TEST_DATABASE_URL=... pytest server/tests/test_xhs_api.py -q`
Expected: FAIL（路由不存在 → 404）

- [ ] **Step 3: 实现 router.py**（对标 `video/router.py`）

```python
"""xhs_cards 路由：MCP token 的 compose/status + 公开的图片服务。"""
from __future__ import annotations
import logging
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session
from server.app.core.mcp_auth import require_mcp_token
from server.app.core.mcp_errors import mcp_exception_response
from server.app.db.session import get_db
from server.app.modules.xhs_cards import store
from server.app.modules.xhs_cards.models import XhsRenderJob
from server.app.modules.xhs_cards.schemas import ComposeXhsRequest
from server.app.modules.xhs_cards.service import create_render_job, spawn_render_job
from server.app.shared.errors import ClientError, ConflictError, ValidationError

logger = logging.getLogger(__name__)
xhs_mcp_router = APIRouter(dependencies=[Depends(require_mcp_token)])
xhs_files_router = APIRouter()  # 公开：卡片 PNG 供入库 markdown 引用


def _to_status(job: XhsRenderJob) -> dict:
    n = len(job.card_keys or [])
    return {
        "job_id": job.job_id,
        "source_article_id": job.source_article_id,
        "status": job.status,
        "theme": job.theme,
        "mode": job.mode,
        "cover_url": f"/api/xhs-cards/file/{job.job_id}/cover" if job.cover_key else None,
        "card_urls": [f"/api/xhs-cards/file/{job.job_id}/card/{i}" for i in range(1, n + 1)],
        "error": job.error,
    }


@xhs_mcp_router.post("/compose", status_code=202)
def compose(req: ComposeXhsRequest, db: Session = Depends(get_db)) -> dict:
    try:
        job = create_render_job(db, req)
    except (ValidationError, ClientError, ConflictError):
        raise
    except HTTPException:
        raise
    except Exception as exc:
        raise mcp_exception_response(exc, context="create_render_job") from exc
    spawn_render_job(job.job_id)
    return {"ok": True, "data": _to_status(job), "error": None}


@xhs_mcp_router.get("/status/{job_id}")
def status(job_id: str, db: Session = Depends(get_db)) -> dict:
    job = db.query(XhsRenderJob).filter(XhsRenderJob.job_id == job_id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="渲染任务不存在")
    return {"ok": True, "data": _to_status(job), "error": None}


def _serve(job_id: str, key: str | None) -> Response:
    if not key:
        raise HTTPException(status_code=404, detail="产物尚未生成")
    try:
        data = store.get_object(key)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"MinIO 读取失败: {exc}") from exc
    return Response(content=data, media_type="image/png")


@xhs_files_router.get("/file/{job_id}/cover")
def serve_cover(job_id: str, db: Session = Depends(get_db)) -> Response:
    job = db.query(XhsRenderJob).filter(XhsRenderJob.job_id == job_id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="渲染任务不存在")
    return _serve(job_id, job.cover_key)


@xhs_files_router.get("/file/{job_id}/card/{idx}")
def serve_card(job_id: str, idx: int, db: Session = Depends(get_db)) -> Response:
    job = db.query(XhsRenderJob).filter(XhsRenderJob.job_id == job_id).first()
    if job is None or not job.card_keys or idx < 1 or idx > len(job.card_keys):
        raise HTTPException(status_code=404, detail="卡片不存在")
    return _serve(job_id, job.card_keys[idx - 1])
```

- [ ] **Step 4: main.py 挂载**

在 video 的 include_router 附近加（MCP router 带前缀 `/api/xhs-cards`，files router 同前缀且公开）：

```python
from server.app.modules.xhs_cards.router import xhs_files_router, xhs_mcp_router
...
app.include_router(xhs_mcp_router, prefix="/api/xhs-cards", tags=["xhs-mcp"])
app.include_router(xhs_files_router, prefix="/api/xhs-cards", tags=["xhs-files"])
```

> 注意路由顺序：files_router 的 `/file/{job_id}/cover` 与 mcp_router 无路径冲突（一个在 `/compose`、`/status`，一个在 `/file/*`），先后无所谓；但两者同 prefix，确保都 include。

- [ ] **Step 5: 运行确认通过**

Run: `GEO_TEST_DATABASE_URL=... pytest server/tests/test_xhs_api.py -q`
Expected: PASS（401 守卫 + compose/status）

- [ ] **Step 6: lint + commit**

```bash
git add server/app/modules/xhs_cards/router.py server/app/main.py server/tests/test_xhs_api.py
git commit -m "feat(xhs): compose/status MCP 端点 + 公开卡片图片服务"
```

---

### Task 6: MCP 工具 compose_xhs_cards + get_xhs_status

**Files:**
- Create: `server/mcp/tools/xhs.py`
- Modify: `server/mcp/tools/__init__.py`（触发注册，若走显式 import）
- Modify: `server/mcp/server.py`（若在此 `from server.mcp.tools import ...`）
- Modify: `server/app/modules/mcp_catalog/connect_router.py`（`MCP_TOOLS_COUNT` 33→35，本 Task 先 +2）
- Modify: `CLAUDE.md`（Tool 三组补 compose_xhs_cards / get_xhs_status）
- Test: `server/tests/test_mcp_tools_count.py`（若存在）

**Interfaces:**
- Consumes: `/api/xhs-cards/compose`、`/api/xhs-cards/status/{job_id}`。
- Produces MCP 工具：`compose_xhs_cards(render_markdown, theme?, mode?, width?, dpr?, source_article_id?)`、`get_xhs_status(job_id)`。

- [ ] **Step 1: 写 xhs.py（对标 video.py 的 _apost/_aget 封装）**

```python
"""小红书卡片渲染 MCP 工具（zero-config：主对话写 render-markdown，GEO 后端 Playwright 渲染）。"""
from __future__ import annotations
from typing import Any
import anyio
from server.mcp.config import get_config
from server.mcp.http_client import ApiError, GeoApiClient
from server.mcp.server import mcp


def _client() -> GeoApiClient:
    cfg = get_config()
    return GeoApiClient(base_url=cfg.internal_api_url, token=cfg.token, timeout=cfg.timeout_seconds)


def _ok(d): return {"ok": True, "data": d, "error": None}
def _fail(e): return {"ok": False, "data": None, "error": e}


async def _apost(path, *, json):
    def _impl():
        try: return _ok(_client().post(path, json=json))
        except ApiError as exc: return _fail(str(exc))
    return await anyio.to_thread.run_sync(_impl)


async def _aget(path):
    def _impl():
        try: return _ok(_client().get(path))
        except ApiError as exc: return _fail(str(exc))
    return await anyio.to_thread.run_sync(_impl)


@mcp.tool()
async def compose_xhs_cards(
    render_markdown: str,
    theme: str | None = None,
    mode: str | None = None,
    width: int | None = None,
    dpr: int | None = None,
    source_article_id: int | None = None,
) -> dict[str, Any]:
    """Render Xiaohongshu (Redbook) image cards from a render-markdown YOU author.

    Zero-config, mirroring compose_video: you write the render-markdown (YAML frontmatter
    `emoji/title/subtitle` + body, `---` to split cards), GEO renders cover + card PNGs
    via headless Chromium and stores them to MinIO. NO LLM call.

    Workflow:
        1. get_article(source_article_id) to read the approved article.
        2. Condense it into Xiaohongshu style per the chosen prompt template.
        3. Build render-markdown: frontmatter (emoji/title<=15/subtitle<=15) + body,
           use `---` between cards.
        4. compose_xhs_cards(render_markdown, theme, mode, source_article_id) -> job_id.
        5. Poll get_xhs_status(job_id) until status == "done", then read cover_url + card_urls.
        6. Assemble article markdown embedding those image URLs and call save_xhs_note.

    Args:
        render_markdown: frontmatter + body markdown (see above).
        theme: one of sketch/default/playful-geometric/neo-brutalism/botanical/
            professional/retro/terminal. None -> sketch.
        mode: separator/auto-split/auto-fit/dynamic. None -> separator. (MVP renders
            separator semantics; use `---` to control paging.)
        width: card width px (default 1080). dpr: 1-3 (default 2).
        source_article_id: the approved article this is derived from (traceability).

    Returns:
        {"ok": True, "data": {"job_id": str, "status": "pending", ...}, "error": None}
    """
    body: dict[str, Any] = {"render_markdown": render_markdown}
    if theme: body["theme"] = theme
    if mode: body["mode"] = mode
    if width: body["width"] = width
    if dpr: body["dpr"] = dpr
    if source_article_id: body["source_article_id"] = source_article_id
    return await _apost("/api/xhs-cards/compose", json=body)


@mcp.tool()
async def get_xhs_status(job_id: str) -> dict[str, Any]:
    """Poll an xhs render job; when done returns cover_url + card_urls (MinIO-backed).

    Returns:
        {"ok": True, "data": {"job_id": str, "status": "pending|running|done|failed",
         "cover_url": str|null, "card_urls": [str], "error": str|null}, "error": None}
    """
    return await _aget(f"/api/xhs-cards/status/{job_id}")
```

- [ ] **Step 2: 注册触发**

确认 `server/mcp/server.py` 或 `server/mcp/tools/__init__.py` 有 `from server.mcp.tools import xhs`（对标 `import video`）。若是 `from server.mcp.tools import (video, ...)` 形式，加 `xhs`。

Run: `grep -rn "import video\|from server.mcp.tools import" server/mcp/`
据结果加对应一行。

- [ ] **Step 3: 更新计数 + CLAUDE.md**

`connect_router.py`：`MCP_TOOLS_COUNT = 35`（本 Task 从 33 到 35）。
CLAUDE.md「action」组补 `compose_xhs_cards` / `get_xhs_status`，并把「共 33 个」相关字样改 35（Task 9 再到 36）。

- [ ] **Step 4: 验证工具注册**

在 dev 容器重启后端 → Claude Code `/mcp` 应看到 `compose_xhs_cards`、`get_xhs_status`。（无自动化测试；若仓库有 `test_mcp_tools_count.py` 之类断言总数，同步改。）

Run: `grep -rn "MCP_TOOLS_COUNT" server/ | grep -v connect_router` → 若测试硬编码 33 则改。

- [ ] **Step 5: lint + commit**

```bash
git add server/mcp/tools/xhs.py server/mcp/ server/app/modules/mcp_catalog/connect_router.py CLAUDE.md
git commit -m "feat(xhs): compose_xhs_cards / get_xhs_status MCP 工具"
```

---

**Phase 1 验收（dev 容器）**：重启后端 → `/mcp` 见两工具 → 手动 `compose_xhs_cards(render_markdown=...)` → 轮询 `get_xhs_status` 到 done → 浏览器打开 `cover_url` / `card_urls` 看到真实卡片 PNG。**图片效果满意后再进 Phase 2。**

---

# Phase 2 — 落库 + 徽标 + skill 包

### Task 7: articles.content_type 列 + 迁移

**Files:**
- Modify: `server/app/modules/articles/models.py`（加 `content_type`）
- Create: `server/alembic/versions/0066_article_content_type.py`
- Test: `server/tests/test_xhs_service.py`（加列存在断言）

**Interfaces:**
- Produces: `Article.content_type: Mapped[str | None]`（默认 null；值 `"xhs_image_text"` 标识小红书图文）。

- [ ] **Step 1: 加列**

`server/app/modules/articles/models.py` 的 `Article` 类里（`metrics` 列附近）加：

```python
content_type: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
```

（确认文件已 `from sqlalchemy import String`；已用则无需加 import。）

- [ ] **Step 2: 迁移 0066**

创建 `server/alembic/versions/0066_article_content_type.py`（`down_revision = "0065_xhs_render_jobs"`）：

```python
"""articles.content_type

Revision ID: 0066_article_content_type
Revises: 0065_xhs_render_jobs
"""
from alembic import op
import sqlalchemy as sa

revision = "0066_article_content_type"
down_revision = "0065_xhs_render_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("articles", sa.Column("content_type", sa.String(40), nullable=True))
    op.create_index("ix_articles_content_type", "articles", ["content_type"])


def downgrade() -> None:
    op.drop_index("ix_articles_content_type", table_name="articles")
    op.drop_column("articles", "content_type")
```

- [ ] **Step 3: 写失败测试**

在 `server/tests/test_xhs_service.py` 追加：

```python
def test_article_content_type_column(test_app):
    from server.app.db.session import SessionLocal
    from server.app.modules.articles.models import Article
    db = SessionLocal()
    try:
        a = Article.__table__.c
        assert "content_type" in a
    finally:
        db.close()
```

- [ ] **Step 4: 运行确认通过**

Run: `GEO_TEST_DATABASE_URL=... pytest server/tests/test_xhs_service.py::test_article_content_type_column -q`
Expected: PASS（`build_test_app` 建 schema 含新列；若走 alembic 需 `alembic upgrade head`）

- [ ] **Step 5: lint + commit**

```bash
git add server/app/modules/articles/models.py server/alembic/versions/0066_article_content_type.py server/tests/test_xhs_service.py
git commit -m "feat(articles): content_type 列（标识小红书图文）"
```

---

### Task 8: 放宽 /save-from-mcp（可选 question + content_type + source_article_id）

**Files:**
- Modify: `server/app/modules/articles/routers/mcp.py`（`SaveArticleFromMcpPayload` + handler）
- Test: `server/tests/test_articles_api.py`（新增用例；文件已存在）

**Interfaces:**
- Consumes: 现有 `create_article`、`markdown_to_tiptap/html`。
- Produces: `/api/articles/save-from-mcp` 接受可选 `question_item_id`、可选 `content_type`（白名单 `{"xhs_image_text"}`）、可选 `source_article_id`（存 `metrics["source_article_id"]`）。**question_item_id 存在时行为与现状一致（测试锁定）。**

- [ ] **Step 1: 写失败测试（两条：旧路径不变 + 新 xhs 路径）**

在 `server/tests/test_articles_api.py` 追加（对齐该文件既有 MCP 调用/建数据方式）：

```python
def test_save_from_mcp_xhs_no_question(test_app):
    # 无 question_item_id，带 content_type=xhs_image_text → 建 pending 文章
    tpl_id = _make_enabled_template(test_app)  # 复用文件内既有 helper；无则内联建模板
    headers = {"X-MCP-Token": test_app.mcp_token}
    body = {
        "prompt_template_id": tpl_id,
        "user_id": test_app.operator_user_id,
        "title": "小红书图文A",
        "markdown_content": "![封面](/api/xhs-cards/file/abc/cover)\n\n正文文案\n\n#标签",
        "content_type": "xhs_image_text",
        "source_article_id": 123,
    }
    r = test_app.client.post("/api/articles/save-from-mcp", json=body, headers=headers)
    assert r.status_code == 200
    aid = r.json()["article_id"]
    from server.app.db.session import SessionLocal
    from server.app.modules.articles.models import Article
    db = SessionLocal()
    try:
        art = db.get(Article, aid)
        assert art.review_status == "pending"
        assert art.content_type == "xhs_image_text"
        assert (art.metrics or {}).get("source_article_id") == 123
    finally:
        db.close()
```

> 保留/确认既有 `test_save_from_mcp_*`（带 question_item_id 的正路径）用例仍绿——它们就是「旧路径不变」的锁定测试。若文件里没有，新增一条带 `question_item_id` 的成功用例做锁定。

- [ ] **Step 2: 运行确认失败**

Run: `GEO_TEST_DATABASE_URL=... pytest server/tests/test_articles_api.py -q -k save_from_mcp`
Expected: 新用例 FAIL（`content_type` 未被接受/未写入；无 question_item_id 时 422 或 404）

- [ ] **Step 3: 改 payload schema**

`SaveArticleFromMcpPayload` 改为：

```python
class SaveArticleFromMcpPayload(BaseModel):
    question_item_id: int | None = None          # 放宽：xhs 图文源自文章、无问题
    prompt_template_id: int
    user_id: int
    title: str = Field(min_length=1, max_length=300)
    markdown_content: str = Field(min_length=1)
    model_label: str | None = Field(default=None, max_length=120)
    content_type: str | None = Field(default=None, max_length=40)
    source_article_id: int | None = None
```

- [ ] **Step 4: 改 handler**

在 `save_article_from_mcp` 里：
1. question 校验改为「present 才查」：

```python
item = None
if payload.question_item_id is not None:
    item = db.query(QuestionItem).filter(QuestionItem.id == payload.question_item_id).first()
    if item is None:
        raise HTTPException(status_code=404, detail=f"question_item not found: id={payload.question_item_id}")
```

2. 溯源字段按 item 是否存在分别写（原来无条件写 `item.category` 会 NPE）：

```python
article.source_question_category = item.category if item else None
article.source_question_texts = ([item.question_text] if item and item.question_text else None)
```

3. content_type 白名单 + 落列；source_article_id 落 metrics（在 model_label 合并块附近）：

```python
if payload.content_type is not None:
    if payload.content_type not in {"xhs_image_text"}:
        raise HTTPException(status_code=400, detail=f"unsupported content_type: {payload.content_type}")
    article.content_type = payload.content_type
if payload.source_article_id is not None:
    existing = dict(article.metrics or {})
    existing["source_article_id"] = payload.source_article_id
    article.metrics = existing
```

> 其余（tpl 校验、`source_agent_name="loop"`、`source_template_*`、commit/异常处理）保持不变。

- [ ] **Step 5: 运行确认通过（新 + 旧路径）**

Run: `GEO_TEST_DATABASE_URL=... pytest server/tests/test_articles_api.py -q -k save_from_mcp`
Expected: PASS（新 xhs 用例 + 既有 question 用例都绿）

- [ ] **Step 6: lint + commit**

```bash
git add server/app/modules/articles/routers/mcp.py server/tests/test_articles_api.py
git commit -m "feat(articles): save-from-mcp 支持无 question 的 xhs 图文落库 + content_type"
```

---

### Task 9: save_xhs_note MCP 工具

**Files:**
- Modify: `server/mcp/tools/xhs.py`（加 `save_xhs_note`）
- Modify: `server/app/modules/mcp_catalog/connect_router.py`（35→36）
- Modify: `CLAUDE.md`（action 组补 save_xhs_note，总数 36）
- Test: 无（thin wrapper）

**Interfaces:**
- Consumes: `/api/articles/save-from-mcp`。
- Produces: `save_xhs_note(source_article_id, prompt_template_id, title, markdown_content, model_label?)` → 落 pending + `content_type="xhs_image_text"`。

- [ ] **Step 1: 加工具**

在 `server/mcp/tools/xhs.py` 追加（`_OPERATOR_USER_ID` 取值方式对齐 `server/mcp/tools/action.py` 里 save_article 的同名常量来源）：

```python
from server.mcp.tools.action import _OPERATOR_USER_ID  # 复用同一 operator 归属


@mcp.tool()
async def save_xhs_note(
    source_article_id: int,
    prompt_template_id: int,
    title: str,
    markdown_content: str,
    model_label: str | None = None,
) -> dict[str, Any]:
    """Persist a rendered Xiaohongshu image-text note into GEO's review queue (pending).

    Use AFTER get_xhs_status returns done. Assemble markdown_content as:
    cover image + each card image (as ![](/api/xhs-cards/file/...) links) + the
    Xiaohongshu copy text (title / body / SEO #tags) at the end. Lands review_status=pending
    with content_type="xhs_image_text" so the content list badges it as 小红书图文.

    Args:
        source_article_id: the approved article this note derives from.
        prompt_template_id: the template used to condense (list_prompt_templates).
        title: note title (<=300 chars).
        markdown_content: image links + copy text (see above).
        model_label: optional writer label.

    Returns:
        {"ok": True, "data": {"article_id": N}, "error": None}
    """
    body: dict[str, Any] = {
        "prompt_template_id": prompt_template_id,
        "user_id": _OPERATOR_USER_ID,
        "title": title,
        "markdown_content": markdown_content,
        "content_type": "xhs_image_text",
        "source_article_id": source_article_id,
    }
    if model_label:
        body["model_label"] = model_label
    return await _apost("/api/articles/save-from-mcp", json=body)
```

> 若 `_OPERATOR_USER_ID` 不便跨 module import，按 action.py 的定义方式在 xhs.py 内同样取（同一 env/config 来源）。

- [ ] **Step 2: 计数 + CLAUDE.md**

`MCP_TOOLS_COUNT = 36`；CLAUDE.md action 组加 `save_xhs_note`，总数字样改 36。

- [ ] **Step 3: 验证**

dev 容器重启 → `/mcp` 见 `save_xhs_note`。

- [ ] **Step 4: commit**

```bash
git add server/mcp/tools/xhs.py server/app/modules/mcp_catalog/connect_router.py CLAUDE.md
git commit -m "feat(xhs): save_xhs_note MCP 工具（渲染结果落未审核库）"
```

---

### Task 10: 前端「小红书图文」徽标

**Files:**
- Modify: `web/src/api/`（文章类型加 `content_type`；对应 `articles.ts` 或 content feature 的类型定义）
- Modify: `web/src/features/content/`（列表卡片渲染徽标）
- Test: `pnpm --filter @geo/web typecheck` + `build`

**Interfaces:**
- Consumes: 列表 API 返回的 `content_type`。
- Produces: `content_type === "xhs_image_text"` 的文章卡片显示「小红书图文」标签。

- [ ] **Step 1: 确认列表 API 下发 content_type**

Run: `grep -rn "content_type\|source_template_name\|class ArticleListItem\|ArticleSummary" server/app/modules/articles/schemas.py`
若列表响应 schema（如 `ArticleListItem`/`ArticleSummary`）未含 `content_type`，加 `content_type: str | None = None` 字段（Pydantic 从 ORM 读）。加后端一行即可。

- [ ] **Step 2: 前端类型加字段**

在 content feature 的文章类型（`grep -rn "source_template_name" web/src` 找到同一 interface）加：

```ts
content_type?: string | null;
```

- [ ] **Step 3: 卡片渲染徽标**

在内容列表卡片组件（`grep -rn "source_template_name\|智能体\|徽标\|badge\|Tag" web/src/features/content` 定位渲染处）加：

```tsx
{item.content_type === "xhs_image_text" && (
  <span className="badge badge-xhs">小红书图文</span>
)}
```

样式沿用该文件既有 badge/tag 类名（不新造设计系统；跟现有「智能体/模板」标签同款视觉）。

- [ ] **Step 4: 门禁**

Run: `pnpm --filter @geo/web typecheck && pnpm --filter @geo/web build`
Expected: 均通过

- [ ] **Step 5: commit**

```bash
git add web/src server/app/modules/articles/schemas.py
git commit -m "feat(web): 内容列表小红书图文徽标"
```

---

### Task 11: skill 包 xhs-note-creator + 分发

**Files:**
- Create: `<bundle>/skills/xhs-note-creator/SKILL.md`（打包上传进 Skill 库）
- Test: 手动 / bundle 校验

**Interfaces:**
- Consumes: `list_articles(review_status=approved)` / `get_article` / `list_prompt_templates` / `compose_xhs_cards` / `get_xhs_status` / `save_xhs_note`。
- Produces: 一个 category=`generation` 的 Skill 库包，`install_loop_skills(slug="xhs-note-creator")` 可装。

- [ ] **Step 1: 写 SKILL.md**

内容要点（结构照 `server/app/modules/loop_skills/templates/skills/geo-article-writer/SKILL.md` 风格）：

```markdown
---
name: xhs-note-creator
description: 把已审核库文章精简成小红书图文并渲染卡片、落未审核库。当用户要「把某篇已审文章做成小红书图文/卡片」时用。
---

# 小红书图文创作

## 流程
1. 选源文章：list_articles(review_status="approved") → get_article(id) 读全文。
2. 选精简提示词：list_prompt_templates(scope="generation")，让用户指定；按该提示词把正文精简成小红书风格。
3. 问用户选主题 + 分页（不设强默认）：
   - 主题：sketch/default/playful-geometric/neo-brutalism/botanical/professional/retro/terminal
   - 分页：separator（推荐，用 --- 手动分页）/ auto-split / auto-fit / dynamic
4. 组 render-markdown：frontmatter(emoji/title≤15/subtitle≤15) + 正文，卡片间用 `---` 分隔。
5. compose_xhs_cards(render_markdown, theme, mode, source_article_id=源文章id) → job_id。
6. 轮询 get_xhs_status(job_id) 到 status=="done"，取 cover_url + card_urls。
7. 拼落库 markdown：封面图 + 各卡片图（![](url) 顺序）+ 末尾小红书文案（标题/正文/5-10 个 SEO #标签，纯文本便于复制）。
8. save_xhs_note(source_article_id, prompt_template_id, title, markdown_content) → 落未审核库(pending)，卡片带「小红书图文」徽标。

## 注意
- 不做自动发布到小红书（本 skill 只产素材 + 落库）。
- 分页当前按 separator 语义渲染：务必用 `---` 手动控分页，别指望 auto-split 精确切分。
- 标题≤20 字、每段 1-2 个 Emoji、结尾 5-10 个 SEO 标签（小红书风格）。
```

- [ ] **Step 2: 上传进 Skill 库**

通过 web「Skill 库」上传该 skill 包（category=generation），或用 seed/上传脚本。确认 `list_skills(category="generation")` 能看到 slug `xhs-note-creator`。

- [ ] **Step 3: 安装验证**

在一台接入 MCP 的 Claude Code：`install_loop_skills(slug="xhs-note-creator")` → skill 落 `.claude/skills/` → 跑一遍全流程（选已审文章 → 渲染 → 落库 → 内容列表看到「小红书图文」徽标 pending 文章）。

- [ ] **Step 4: commit（若 skill 源文件进仓库模板目录）**

若团队约定把官方 skill 源也存仓库（如 `server/app/modules/loop_skills/templates/skills/`），提交：

```bash
git add server/app/modules/loop_skills/templates/skills/xhs-note-creator/
git commit -m "feat(xhs): xhs-note-creator skill 包"
```

> 若 skill 仅存 DB Skill 库、不入仓库模板目录，本步跳过（分发靠 Skill 库版本管理，升级=库里追加新版本）。

---

## 升级路径

- **后端/渲染升级**：改 `xhs_cards` 模块 → 走 geo-release（tag main 触发 CI 构建部署）。
- **skill 升级**：Skill 库对 `xhs-note-creator` 追加新版本（web 上传或脚本），使用方 `install_loop_skills(slug="xhs-note-creator")` 重装取最新版。
- **加主题/分页精度**：新增主题 = 往 `assets/themes/` 加 CSS + `AVAILABLE_THEMES`；精确 auto-split = 移植原 `auto_split_content`（依赖真实渲染高度，本期退化说明见 Task 2 Step 3）。

## Self-Review 结论

- **Spec 覆盖**：①架构→全 Task；②渲染模块→T1-T5；③MCP 工具→T6/T9；④落库+徽标(方案A)→T7/T8/T10；⑤skill 分发→T11；⑥测试→各 Task TDD + 容器冒烟；非目标（不发布/不移植 v2）→Global Constraints 明列。无遗漏。
- **新增设计决策**（spec 未细化、计划中定）：`save-from-mcp` 放宽 `question_item_id` 为可选以承接「源自文章而非问题」的 xhs 落库，旧路径测试锁定不变；`source_article_id` 存 `metrics` 免第二次迁移；分页 MVP 退化为 separator 语义（SKILL.md 明示）；cover_asset 不从 URL 反建 Asset（body 首图即封面卡，列表缩略图靠徽标而非 cover——如需真 cover 缩略图为后续增强，不阻塞）。
- **Placeholder 扫描**：无 TBD/TODO；Task 4 Step 4 的占位实现已在 Step 4b 明确重写替代。
- **类型一致性**：`render_markdown_to_card_bytes` 返回 `{"cover","cards"}` 在 render/service/测试三处一致；`XhsRenderJob` 列名（cover_key/card_keys）在 model/service/router 一致；status 返回体 `cover_url/card_urls` 在 router/工具 doc/skill 一致；`content_type="xhs_image_text"` 在 model/handler/工具/前端四处一致。
```
