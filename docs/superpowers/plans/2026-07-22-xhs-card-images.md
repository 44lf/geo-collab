# 小红书卡片配图 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** xhs-note-creator 加「配图」opt-in 选项；开了则每张卡按「原文图→游戏库→新 web 工具」三层兜底配图，图 embed 到文案下方、限高保持竖版。

**Architecture:** 新 MCP 工具 `search_web_image`（复用现有百度/千帆搜图 + rehost MinIO 返 URL）作第三层兜底；渲染器把卡片相对图 src 重写成绝对内网地址（让 Playwright 能拉）+ 注入 `.card-content img` 限高覆盖；skill 加选项+兜底链引导。前两层（原文图/游戏库）用已有工具、无需新代码。

**Tech Stack:** FastAPI + FastMCP + Playwright + MinIO + React(无改动)。

## Global Constraints

- 分支 `feat/xhs-card-images` 基于最新 `main`。`MCP_TOOLS_COUNT` 当前 **38** → 新增 1 个工具 → **39**（真值 `mcp_catalog/connect_router.py`）。
- MCP 端点：独立 sub-router、`Depends(require_mcp_token)`、未捕获异常走 `mcp_exception_response`。best-effort：搜不到/无 key → 返 `url=null`，不报错。
- 复用点（无需重构 ai_format）：`server.app.shared.baidu.search_landscape_images(keyword)` + `baidu.download_image(url)`；`image_library.service.store_image_bytes(db, category, data, mime, source_url=, width=, height=)`；`get_or_create_companion_category(db, name)`。
- 图可达：Playwright 从 `file://` 加载卡片 HTML，相对 `/api/…` 图拉不到 → 渲染把相对 src 重写成 `{GEO_INTERNAL_URL}/api/…`（默认 `http://127.0.0.1:8000`）。stock-images 端点公开，loopback 可达。
- 限高：注入公共 `<style>` 覆盖 `.card-content img { max-height: …; object-fit: contain; }`（**不改 8 个主题 CSS**）。
- 后端测试在容器（`GEO_TEST_DATABASE_URL`）；`build_test_app`。渲染真图人工验收进 dev 容器。前端无改动。
- 后端 lint：ruff + mypy。发版 `release-*`（需 `GEO_BAIDU_API_KEY` 第三层才用）。

## File Structure
- `server/app/modules/image_library/service.py` — 加 `search_and_store_web_image(db, keyword) -> str | None`
- `server/app/modules/image_library/mcp_router.py`（新）— `image_mcp_router` + `POST /search-web-image`
- `server/app/main.py` — 挂 `image_mcp_router`
- `server/mcp/tools/action.py` — `search_web_image` 工具
- `server/app/modules/mcp_catalog/connect_router.py` — count 39；`CLAUDE.md` 同步
- `server/app/modules/xhs_cards/render.py` — `rewrite_img_src` + 注入限高 style + 卡片 img-src 重写
- `.claude/skills/xhs-note-creator/SKILL.md` — 配图选项 + 兜底链
- Tests: `server/tests/test_xhs_card_images.py`（新）

---

### Task 1: `search_web_image` 后端 + MCP 工具

**Files:**
- Modify: `image_library/service.py`, `server/app/main.py`, `server/mcp/tools/action.py`, `mcp_catalog/connect_router.py`, `CLAUDE.md`
- Create: `image_library/mcp_router.py`
- Test: `server/tests/test_xhs_card_images.py`

**Interfaces:**
- Produces: `service.search_and_store_web_image(db, keyword) -> str | None`；`POST /api/mcp/search-web-image {keyword}` → `{ok,data:{url,stock_image_id},error}`；工具 `search_web_image(keyword) -> {...}`。

- [ ] **Step 1: service 函数**

`image_library/service.py` 末尾加（复用 `_web_fallback_fill_category` 的搜图/下载/存图逻辑，但用固定栏目、返 URL）：

```python
WEB_FALLBACK_CATEGORY_NAME = "小红书web兜底"


def search_and_store_web_image(db: Session, keyword: str) -> tuple[str, int] | None:
    """联网搜一张横版图 rehost MinIO，返回 (公开URL, stock_image_id)。搜不到/无 key/失败返 None（best-effort）。"""
    from server.app.shared import baidu

    keyword = (keyword or "").strip()
    if not keyword:
        return None
    category = get_or_create_companion_category(db, WEB_FALLBACK_CATEGORY_NAME)
    if category is None:
        return None
    try:
        for cand in baidu.search_landscape_images(keyword):
            downloaded = baidu.download_image(cand.url)
            if downloaded is None:
                continue
            data, mime = downloaded
            img = store_image_bytes(
                db, category, data, mime,
                source_url=cand.source_url, width=cand.width, height=cand.height,
            )
            if img is not None:
                return f"/api/stock-images/{img.id}/file", img.id
    except Exception:
        logger.exception("search_and_store_web_image failed: %s", keyword)
    return None
```

> ⚠️ 确认公开 URL 格式：`grep -rn "stock-images.*file\|/api/stock-images" server/app/modules/image_library/router.py server/app/modules/mcp_catalog/router.py` —— 用现有 stock 图公开 serve 的真实路径（若是 `/api/stock-images/{id}/file` 就用它；不同则对齐）。`store_image_bytes`/`get_or_create_companion_category` 已在本文件，直接调（无需 import）。

- [ ] **Step 2: 写失败测试**

创建 `server/tests/test_xhs_card_images.py`：

```python
import pytest

pytestmark = pytest.mark.mysql


def test_search_and_store_web_image(monkeypatch):
    from server.tests.utils import build_test_app

    test_app = build_test_app(monkeypatch)
    try:
        from server.app.modules.image_library import service
        from server.app.shared import baidu

        class Cand:
            url = "http://x/a.jpg"
            source_url = "http://x/a"
            width = 800
            height = 450

        monkeypatch.setattr(baidu, "search_landscape_images", lambda kw, **k: [Cand()])
        monkeypatch.setattr(baidu, "download_image", lambda url: (b"\x89PNGxxxx", "image/jpeg"))
        with test_app.session_factory() as db:
            res = service.search_and_store_web_image(db, "餐厅养成记")
        assert res is not None
        url, sid = res
        assert url.startswith("/api/stock-images/") and url.endswith("/file")

        # 搜不到 → None
        monkeypatch.setattr(baidu, "search_landscape_images", lambda kw, **k: [])
        with test_app.session_factory() as db:
            assert service.search_and_store_web_image(db, "无此游戏") is None
    finally:
        test_app.cleanup()


def test_search_web_image_endpoint_requires_token(monkeypatch):
    from server.tests.utils import build_test_app

    test_app = build_test_app(monkeypatch)
    try:
        r = test_app.client.post("/api/mcp/search-web-image", json={"keyword": "x"})
        assert r.status_code == 401
    finally:
        test_app.cleanup()


def test_search_web_image_endpoint(monkeypatch):
    from server.tests.utils import build_test_app

    test_app = build_test_app(monkeypatch)
    try:
        from server.app.modules.image_library import mcp_router
        monkeypatch.setattr(
            mcp_router, "search_and_store_web_image", lambda db, kw: ("/api/stock-images/5/file", 5)
        )
        r = test_app.client.post(
            "/api/mcp/search-web-image", json={"keyword": "餐厅养成记"},
            headers={"X-MCP-Token": "secret"},
        )
        assert r.status_code == 200
        assert r.json()["data"]["url"] == "/api/stock-images/5/file"
        # 搜不到 → url null
        monkeypatch.setattr(mcp_router, "search_and_store_web_image", lambda db, kw: None)
        r2 = test_app.client.post(
            "/api/mcp/search-web-image", json={"keyword": "x"},
            headers={"X-MCP-Token": "secret"},
        )
        assert r2.status_code == 200 and r2.json()["data"]["url"] is None
    finally:
        test_app.cleanup()
```

- [ ] **Step 3: 运行确认失败**

Run: `docker compose exec -T app sh -c 'export GEO_TEST_DATABASE_URL="mysql+pymysql://$GEO_DB_USER:$GEO_DB_PASS@$GEO_DB_HOST:$GEO_DB_PORT/geo_test"; cd /app && python -m pytest server/tests/test_xhs_card_images.py -q'`
Expected: FAIL（service/端点不存在）

- [ ] **Step 4: mcp_router**

创建 `server/app/modules/image_library/mcp_router.py`：

```python
"""图片库 MCP 端点：search-web-image（第三层配图兜底）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from server.app.core.mcp_auth import require_mcp_token
from server.app.core.mcp_errors import mcp_exception_response
from server.app.db.session import get_db
from server.app.modules.image_library.service import search_and_store_web_image

image_mcp_router = APIRouter(dependencies=[Depends(require_mcp_token)])


class SearchWebImageReq(BaseModel):
    keyword: str


@image_mcp_router.post("/search-web-image")
def search_web_image(req: SearchWebImageReq, db: Session = Depends(get_db)) -> dict:
    try:
        res = search_and_store_web_image(db, req.keyword)
    except HTTPException:
        raise
    except Exception as exc:
        raise mcp_exception_response(exc, context=f"search_web_image kw={req.keyword}") from exc
    if res is None:
        return {"ok": True, "data": {"url": None, "stock_image_id": None}, "error": None}
    url, sid = res
    return {"ok": True, "data": {"url": url, "stock_image_id": sid}, "error": None}
```

- [ ] **Step 5: main.py 挂载**

在 `main.py` MCP 路由挂载区（如 `articles_mcp_router` 附近）加：

```python
from server.app.modules.image_library.mcp_router import image_mcp_router
...
app.include_router(image_mcp_router, prefix="/api/mcp", tags=["image-mcp"])
```

- [ ] **Step 6: MCP 工具**

`server/mcp/tools/action.py` 加（对齐该文件 `_apost` 风格）：

```python
@mcp.tool()
async def search_web_image(keyword: str) -> dict[str, Any]:
    """Web-search ONE landscape image for a keyword, rehost to MinIO, return its URL.

    Third-tier fallback for xhs card illustration (games/topics not in the stock library).
    Does NOT insert into any article. Returns {"url": "/api/stock-images/{id}/file"} or {"url": null}
    when no image found / GEO_BAIDU_API_KEY missing.

    Args:
        keyword: search term, e.g. a game name.
    Returns:
        {"ok": True, "data": {"url": str|null, "stock_image_id": int|null}, "error": None}
    """
    return await _apost("/api/mcp/search-web-image", json={"keyword": keyword})
```

- [ ] **Step 7: count + CLAUDE.md**

`connect_router.py`：`MCP_TOOLS_COUNT = 39`。CLAUDE.md action 组补 `search_web_image`，总数 38→39。若有 count 断言测试（`grep -rn "MCP_TOOLS_COUNT == \|_count_is_" server/tests`）同步。

- [ ] **Step 8: 运行确认通过**

Run: `docker compose exec -T app sh -c 'export GEO_TEST_DATABASE_URL="mysql+pymysql://$GEO_DB_USER:$GEO_DB_PASS@$GEO_DB_HOST:$GEO_DB_PORT/geo_test"; cd /app && python -m pytest server/tests/test_xhs_card_images.py server/tests/test_mcp_status_count.py server/tests/test_mcp_tools_registration.py -q'`
Expected: PASS

- [ ] **Step 9: lint + commit**

```bash
docker compose exec -T app sh -c 'cd /app && ruff check server/ && ruff format --check server/app/modules/image_library/ server/mcp/tools/action.py && mypy server/app/modules/image_library/ 2>&1 | tail -1'
git add server/app/modules/image_library/ server/app/main.py server/mcp/tools/action.py \
  server/app/modules/mcp_catalog/connect_router.py CLAUDE.md server/tests/test_xhs_card_images.py
git commit -m "feat(xhs): search_web_image MCP 工具（第三层配图兜底）"
```

---

### Task 2: 渲染器 img-src 重写 + 限高

**Files:**
- Modify: `server/app/modules/xhs_cards/render.py`
- Test: `server/tests/test_xhs_render.py`（追加纯函数测试）

**Interfaces:**
- Consumes: 无。
- Produces: `render.rewrite_img_src(html: str, base: str) -> str`；`generate_card_html` 输出含绝对图 src + 限高 style。

- [ ] **Step 1: 写失败测试**

在 `server/tests/test_xhs_render.py` 追加：

```python
def test_rewrite_img_src():
    from server.app.modules.xhs_cards import render as R

    base = "http://127.0.0.1:8000"
    html = '<p><img src="/api/stock-images/5/file" alt="x"></p>'
    out = R.rewrite_img_src(html, base)
    assert 'src="http://127.0.0.1:8000/api/stock-images/5/file"' in out
    # 已是绝对 URL 不动
    html2 = '<img src="http://cdn/x.jpg">'
    assert R.rewrite_img_src(html2, base) == html2
    # 非 /api 相对不动
    html3 = '<img src="foo.png">'
    assert R.rewrite_img_src(html3, base) == html3


def test_card_html_has_absolute_img_and_maxheight():
    from server.app.modules.xhs_cards import render as R

    html = R.generate_card_html("正文\n\n![](/api/stock-images/9/file)", "default", 1, 1080, 1440)
    assert "http://127.0.0.1:8000/api/stock-images/9/file" in html
    assert "max-height" in html and "object-fit" in html  # 限高 style 注入
```

- [ ] **Step 2: 运行确认失败**

Run: `docker compose exec -T app sh -c 'cd /app && python -m pytest server/tests/test_xhs_render.py -q -k "rewrite or maxheight"'`
Expected: FAIL

- [ ] **Step 3: 实现 rewrite_img_src + 接入 generate_card_html**

`render.py`：

```python
import os
import re

_INTERNAL_BASE = os.environ.get("GEO_INTERNAL_URL", "http://127.0.0.1:8000")
_IMG_SRC_RE = re.compile(r'(<img\b[^>]*\bsrc=")(/api/[^"]*)(")', re.IGNORECASE)


def rewrite_img_src(html: str, base: str = _INTERNAL_BASE) -> str:
    """把 <img src="/api/…"> 的相对 src 重写成 base + src，让 Playwright(file://) 能拉。
    已是绝对(http…)或非 /api 相对的不动。"""
    return _IMG_SRC_RE.sub(rf"\g<1>{base}\g<2>\g<3>", html)
```

`generate_card_html`：`html_content = convert_markdown_to_html(content)` 后加 `html_content = rewrite_img_src(html_content)`。并在拼 HTML 的 `<style>` 块**末尾**（theme_css 之后，保证覆盖）加一条：

```css
.card-content img { max-height: 640px; object-fit: contain; }
```

（640 ≈ 1440 高的 ~45%；用固定 px 即可。注入到 generate_card_html 的 `<style>` f-string 里 theme_css 后面。）

> 若 plan 阶段实测发现原文图是**需鉴权的** `/api/assets/*`（loopback 401），则改「抓字节内联 data URI」：新增 `inline_img_as_data_uri(html)` 在渲染前把 `/api/…` 图 fetch 成 base64 data URI。先按重写实现，人工验收（Step 4）若图空白再切内联。

- [ ] **Step 4: 运行确认通过 + 真图验收**

Run: `docker compose exec -T app sh -c 'cd /app && python -m pytest server/tests/test_xhs_render.py -q'`
Expected: PASS

真图验收（dev 容器，需 chromium）：渲一张带 `![](/api/stock-images/<真实id>/file)` 的卡片，确认图显示在文案下方、限高、不遮挡。（`GEO_XHS_RENDER_LIVE=1` 或临时脚本。）

- [ ] **Step 5: lint + commit**

```bash
docker compose exec -T app sh -c 'cd /app && ruff check server/app/modules/xhs_cards/render.py server/tests/test_xhs_render.py && ruff format --check server/app/modules/xhs_cards/render.py'
git add server/app/modules/xhs_cards/render.py server/tests/test_xhs_render.py
git commit -m "feat(xhs): 卡片图 src 重写为绝对内网地址 + 限高保持竖版"
```

---

### Task 3: skill 配图选项 + 兜底链引导

**Files:**
- Modify: `.claude/skills/xhs-note-creator/SKILL.md`
- Test: 无（校验工具名存在）

**Interfaces:**
- Consumes: `get_article` / `list_stock_categories` / `list_stock_images` / `search_web_image`。

- [ ] **Step 1: 更新 SKILL.md**

在 xhs-note-creator SKILL.md 加/改：
1. 主题/分页选择后，新增一步「**是否给卡片配图**」（问用户，默认可关）。
2. 配图开时的兜底链引导（每张卡）：
   - 先用**原文对应 body 图**：`get_article` 读源文章 content_json，取该卡对应游戏/主题的 image 节点 src。
   - 没有 → **游戏库**：`list_stock_categories` 找匹配栏目 → `list_stock_images` 取一张 url。
   - 还没有 → **`search_web_image(该卡关键词)`** 取一张（返 url=null 就跳过）。
   - 拿到 url 就把 `![](url)` 放该卡 render-markdown **文案末尾**（渲染即在文字下方、限高）；三层都空该卡不配。
   - 关闭配图=现状纯文案卡（不 embed 任何图）。

- [ ] **Step 2: 校验工具名**

Run: `grep -rn "def search_web_image\|def list_stock_categories\|def list_stock_images\|def get_article" server/mcp/tools/` —— 4 个都在。SKILL.md 只引用存在的工具。

- [ ] **Step 3: commit**

```bash
git add -f .claude/skills/xhs-note-creator/SKILL.md
git commit -m "feat(xhs): xhs-note-creator 加配图选项 + 三层兜底链引导"
```

---

## Self-Review 结论
- **Spec 覆盖**：①skill 配图选项→T3;②三层兜底(原文/游戏库现有工具 + 新 search_web_image)→T1+T3;③新工具复用 baidu+store_image_bytes 返 URL→T1;④渲染图可达(src 重写)+限高→T2;count 38→39→T1。均落任务。
- **Placeholder 扫描**：T1 Step1 的 stock URL 格式、T2 Step3 的重写-vs-内联决策标「plan/实测定」——是明确实现指令 + 兜底切换条件，非 TBD。代码完整。
- **类型一致**：`search_and_store_web_image` 返 `(url, id) | None` 在 service/endpoint/test 一致；端点回 `{url, stock_image_id}` 与工具 doc 一致；`rewrite_img_src(html, base)` 在 render/test 一致。
- **风险**：`baidu.search_landscape_images/download_image` 依赖 `GEO_BAIDU_API_KEY`——缺时 service 返 None、端点返 url=null（best-effort，测试用 monkeypatch 不触真网）。img-src 重写假设图是公开 stock-images；若遇鉴权 asset 图，切 data-URI 内联（T2 Step3 备选，人工验收把关）。
