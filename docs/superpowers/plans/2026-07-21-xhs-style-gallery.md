# 小红书样式库（主题预览画廊）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在「提示词管理」下新增子 tab「小红书样式库」，画廊化展示 8 个 xhs 主题的封面+正文卡预览；预览懒生成后缓存 MinIO，全员可看可重生。

**Architecture:** 复用 `xhs_cards` 渲染核心（`render.render_markdown_to_card_bytes` + `store` MinIO），新增 `previews.py`（编排+缓存）+ 用户 JWT 的 `xhs_gallery_router`（list/serve/regenerate）+ 前端画廊组件（route-level 分支，不改 PromptsWorkspace 的 scope 模型）。无迁移、无新 MCP 工具。

**Tech Stack:** FastAPI + MinIO(minio-py) + Playwright(容器内) + React/TS。

## Global Constraints

- 依赖 `xhs_cards` 模块（`render.AVAILABLE_THEMES`、`render.render_markdown_to_card_bytes`、`store`）——本分支 `feat/xhs-style-gallery` 基于 `feat/xhs-note-creator`，这些已存在。
- 主题真源唯一：`render.AVAILABLE_THEMES`。`THEME_LABELS` 只是展示增强，缺失回落 code。
- 预览缓存：桶 `geo-xhs-cards`（复用），前缀 `theme-previews/{theme}/{cover|card}.png`。
- 端点鉴权：`Depends(get_current_user)`（样式库在认证页内，非 MCP token、非公开）。
- 重新生成**不碰 DB**：`threading.Thread(daemon=True)` + 进程内 `threading.Lock` 防并发，无需 `bg_session_factory`。
- 后端测试在容器跑：`docker compose exec -T app sh -c 'export GEO_TEST_DATABASE_URL="mysql+pymysql://$GEO_DB_USER:$GEO_DB_PASS@$GEO_DB_HOST:$GEO_DB_PORT/geo_test"; cd /app && python -m pytest <path> -q'`。测试用 `from server.tests.utils import build_test_app`（`test_app.client` / `.admin_id` / `.session_factory()` / `.cleanup()`；user JWT 已在 client 的 cookie 里，无需额外 header）。`server/` bind-mount，host 编辑即时生效。
- 前端在 host 跑：`pnpm --filter @geo/web typecheck` + `build`（无单测框架）。web/ 不在容器里。
- 后端 lint（容器）：`ruff check server/ && ruff format --check server/ && mypy server/app`。
- 部署：`release-*`（前后端都动）。

## File Structure

- `server/app/modules/image_library/store.py` — 加 `object_exists(bucket, key) -> bool`
- `server/app/modules/xhs_cards/store.py` — 加 `object_exists(key) -> bool` 包装
- `server/app/modules/xhs_cards/previews.py` — 新：预览编排 + 缓存（常量/keys/list/get/regenerate/spawn）
- `server/app/modules/xhs_cards/router.py` — 加 `xhs_gallery_router`
- `server/app/main.py` — 挂 `xhs_gallery_router`
- `server/tests/test_xhs_previews.py` — 新：previews 逻辑 + 路由测试
- `web/src/api/xhsThemes.ts` — 新：list/regenerate 客户端
- `web/src/features/prompt-templates/XhsStyleGallery.tsx` — 新：画廊组件
- `web/src/routes.tsx` — `PromptsRoute` 分支 `xhs_styles`
- `web/src/types.ts` — nav children 加「小红书样式库」

---

### Task 1: 后端预览核心（previews.py + store.object_exists）

**Files:**
- Modify: `server/app/modules/image_library/store.py`
- Modify: `server/app/modules/xhs_cards/store.py`
- Create: `server/app/modules/xhs_cards/previews.py`
- Test: `server/tests/test_xhs_previews.py`

**Interfaces:**
- Produces:
  - `image_library.store.object_exists(bucket_name: str, key: str) -> bool`
  - `xhs_cards.store.object_exists(key: str) -> bool`（对 `XHS_BUCKET`）
  - `previews.PREVIEW_PREFIX`、`previews.PREVIEW_SAMPLE_MD`、`previews.THEME_LABELS`
  - `previews.preview_keys(theme: str) -> tuple[str, str]`（cover_key, card_key）
  - `previews.list_theme_previews() -> list[dict]`（每项 `{name,label,cover_url,card_url,cached}`）
  - `previews.get_preview_bytes(theme: str, kind: str) -> bytes | None`
  - `previews.regenerate_all_previews() -> None`
  - `previews.spawn_regenerate() -> bool`（True=已启动本轮，False=已有在跑）
  - `previews.is_generating() -> bool`

- [ ] **Step 1: image_library.store 加 object_exists**

在 `server/app/modules/image_library/store.py` 末尾加：

```python
def object_exists(bucket_name: str, key: str) -> bool:
    """对象是否存在。stat_object 命中 S3Error（NoSuchKey/NoSuchBucket）→ False。"""
    from minio.error import S3Error

    client = _client()
    try:
        client.stat_object(bucket_name, key)
        return True
    except S3Error:
        return False
```

- [ ] **Step 2: xhs_cards.store 加 object_exists 包装**

在 `server/app/modules/xhs_cards/store.py` 末尾加：

```python
def object_exists(key: str) -> bool:
    return minio_store.object_exists(XHS_BUCKET, key)
```

- [ ] **Step 3: 写失败测试**

创建 `server/tests/test_xhs_previews.py`：

```python
"""xhs_cards.previews 逻辑测试（不触 Playwright / MinIO 真实网络）。"""
from server.app.modules.xhs_cards import previews, store
from server.app.modules.xhs_cards import render


def test_preview_keys():
    cover, card = previews.preview_keys("sketch")
    assert cover == "theme-previews/sketch/cover.png"
    assert card == "theme-previews/sketch/card.png"


def test_list_theme_previews_cached_flag(monkeypatch):
    # 只有 sketch 有缓存
    def fake_exists(key):
        return key.startswith("theme-previews/sketch/")

    monkeypatch.setattr(store, "object_exists", fake_exists)
    rows = previews.list_theme_previews()
    assert {r["name"] for r in rows} == set(render.AVAILABLE_THEMES)
    by = {r["name"]: r for r in rows}
    assert by["sketch"]["cached"] is True
    assert by["default"]["cached"] is False
    assert by["sketch"]["cover_url"] == "/api/xhs-cards/themes/sketch/preview/cover"
    assert by["sketch"]["card_url"] == "/api/xhs-cards/themes/sketch/preview/card"
    assert by["sketch"]["label"]  # 非空


def test_regenerate_all_puts_cover_and_card(monkeypatch):
    puts = []

    async def fake_render(md, *, theme, mode, width=1080, height=1440, dpr=2):
        return {"cover": b"COVER", "cards": [b"CARD1", b"CARD2"]}

    monkeypatch.setattr(render, "render_markdown_to_card_bytes", fake_render)
    monkeypatch.setattr(store, "ensure_bucket", lambda: None)
    monkeypatch.setattr(store, "put_png", lambda k, d: puts.append((k, d)))
    previews.regenerate_all_previews()
    # 每主题 2 次 put（cover + card[0]）
    assert len(puts) == 2 * len(render.AVAILABLE_THEMES)
    assert ("theme-previews/sketch/cover.png", b"COVER") in puts
    assert ("theme-previews/sketch/card.png", b"CARD1") in puts


def test_regenerate_skips_failing_theme(monkeypatch):
    puts = []

    async def flaky(md, *, theme, mode, width=1080, height=1440, dpr=2):
        if theme == "sketch":
            raise RuntimeError("boom")
        return {"cover": b"C", "cards": [b"D"]}

    monkeypatch.setattr(render, "render_markdown_to_card_bytes", flaky)
    monkeypatch.setattr(store, "ensure_bucket", lambda: None)
    monkeypatch.setattr(store, "put_png", lambda k, d: puts.append(k))
    previews.regenerate_all_previews()  # 不抛
    assert not any(k.startswith("theme-previews/sketch/") for k in puts)
    assert any(k.startswith("theme-previews/default/") for k in puts)
```

- [ ] **Step 4: 运行确认失败**

Run: `docker compose exec -T app sh -c 'export GEO_TEST_DATABASE_URL="mysql+pymysql://$GEO_DB_USER:$GEO_DB_PASS@$GEO_DB_HOST:$GEO_DB_PORT/geo_test"; cd /app && python -m pytest server/tests/test_xhs_previews.py -q'`
Expected: FAIL（`previews` 未定义）

- [ ] **Step 5: 实现 previews.py**

创建 `server/app/modules/xhs_cards/previews.py`：

```python
"""小红书主题预览：固定示例渲染 8 主题封面+正文卡，缓存 MinIO；懒生成。

不碰 DB，不调 LLM。主题真源 = render.AVAILABLE_THEMES。
"""
from __future__ import annotations

import asyncio
import logging
import threading

from server.app.modules.xhs_cards import render, store

logger = logging.getLogger(__name__)

PREVIEW_PREFIX = "theme-previews"

# 固定示例文案（frontmatter 出封面；正文出一张卡）。
PREVIEW_SAMPLE_MD = """---
emoji: "🍜"
title: "3步搞定红烧肉"
subtitle: "新手也能零失败"
---

# 选肉有讲究 🥩

五花肉三层分明最好，切成麻将块大小，肥瘦相间才够香。

#红烧肉 #家常菜 #新手下厨
"""

THEME_LABELS = {
    "sketch": "手绘素描",
    "default": "默认简约",
    "playful-geometric": "活泼几何",
    "neo-brutalism": "新粗野主义",
    "botanical": "植物园自然",
    "professional": "专业商务",
    "retro": "复古怀旧",
    "terminal": "终端命令行",
}

_lock = threading.Lock()
_generating = False


def preview_keys(theme: str) -> tuple[str, str]:
    return f"{PREVIEW_PREFIX}/{theme}/cover.png", f"{PREVIEW_PREFIX}/{theme}/card.png"


def _preview_urls(theme: str) -> tuple[str, str]:
    return (
        f"/api/xhs-cards/themes/{theme}/preview/cover",
        f"/api/xhs-cards/themes/{theme}/preview/card",
    )


def list_theme_previews() -> list[dict]:
    rows: list[dict] = []
    for theme in render.AVAILABLE_THEMES:
        cover_key, card_key = preview_keys(theme)
        cached = store.object_exists(cover_key) and store.object_exists(card_key)
        cover_url, card_url = _preview_urls(theme)
        rows.append(
            {
                "name": theme,
                "label": THEME_LABELS.get(theme, theme),
                "cover_url": cover_url,
                "card_url": card_url,
                "cached": cached,
            }
        )
    return rows


def get_preview_bytes(theme: str, kind: str) -> bytes | None:
    if theme not in render.AVAILABLE_THEMES or kind not in ("cover", "card"):
        return None
    cover_key, card_key = preview_keys(theme)
    key = cover_key if kind == "cover" else card_key
    if not store.object_exists(key):
        return None
    return store.get_object(key)


def regenerate_all_previews() -> None:
    """同步渲染全部主题并写 MinIO。单个主题失败跳过、不整体失败。"""
    store.ensure_bucket()
    for theme in render.AVAILABLE_THEMES:
        try:
            result = asyncio.run(
                render.render_markdown_to_card_bytes(
                    PREVIEW_SAMPLE_MD, theme=theme, mode="separator"
                )
            )
            cover_key, card_key = preview_keys(theme)
            store.put_png(cover_key, result["cover"])
            if result["cards"]:
                store.put_png(card_key, result["cards"][0])
        except Exception:  # noqa: BLE001 — 单主题失败不拖累其它
            logger.exception("主题预览渲染失败: theme=%s", theme)


def is_generating() -> bool:
    return _generating


def _run_regenerate() -> None:
    global _generating
    try:
        regenerate_all_previews()
    finally:
        with _lock:
            _generating = False


def spawn_regenerate() -> bool:
    """启动一轮后台重生。已有在跑 → 返回 False（幂等）。"""
    global _generating
    with _lock:
        if _generating:
            return False
        _generating = True
    threading.Thread(target=_run_regenerate, daemon=True).start()
    return True
```

- [ ] **Step 6: 运行确认通过**

Run: `docker compose exec -T app sh -c 'export GEO_TEST_DATABASE_URL="mysql+pymysql://$GEO_DB_USER:$GEO_DB_PASS@$GEO_DB_HOST:$GEO_DB_PORT/geo_test"; cd /app && python -m pytest server/tests/test_xhs_previews.py -q'`
Expected: PASS（4 passed）

- [ ] **Step 7: lint + commit**

Run: `docker compose exec -T app sh -c 'cd /app && ruff check server/app/modules/xhs_cards/ server/app/modules/image_library/store.py server/tests/test_xhs_previews.py && ruff format server/app/modules/xhs_cards/ server/app/modules/image_library/store.py server/tests/test_xhs_previews.py'`

```bash
git add server/app/modules/xhs_cards/previews.py server/app/modules/xhs_cards/store.py \
  server/app/modules/image_library/store.py server/tests/test_xhs_previews.py
git commit -m "feat(xhs): 主题预览核心 previews.py + store.object_exists"
```

---

### Task 2: 后端画廊路由（xhs_gallery_router + 挂载）

**Files:**
- Modify: `server/app/modules/xhs_cards/router.py`
- Modify: `server/app/main.py`
- Test: `server/tests/test_xhs_previews.py`（追加路由测试）

**Interfaces:**
- Consumes: `previews.list_theme_previews / get_preview_bytes / spawn_regenerate`。
- Produces: `xhs_gallery_router`（`Depends(get_current_user)`）挂 `/api/xhs-cards`：
  - `GET /themes` → `{ok,data:[...],error}`
  - `GET /themes/{name}/preview/{kind}` → PNG（404 未生成/未知）
  - `POST /themes/regenerate` → `{ok,data:{status},error}`（202）

- [ ] **Step 1: 追加路由测试**

在 `server/tests/test_xhs_previews.py` 追加：

```python
import pytest

pytestmark = pytest.mark.mysql


def test_themes_requires_login(monkeypatch):
    from server.tests.utils import build_test_app

    test_app = build_test_app(monkeypatch)
    try:
        # 未登录 client：清 cookie 后请求应 401
        c = test_app.client
        c.cookies.clear()
        r = c.get("/api/xhs-cards/themes")
        assert r.status_code == 401
    finally:
        test_app.cleanup()


def test_themes_list_and_regenerate(monkeypatch):
    from server.tests.utils import build_test_app
    from server.app.modules.xhs_cards import previews

    test_app = build_test_app(monkeypatch)
    try:
        monkeypatch.setattr(previews, "spawn_regenerate", lambda: True)  # 不真起线程
        r = test_app.client.get("/api/xhs-cards/themes")
        assert r.status_code == 200
        data = r.json()["data"]
        assert len(data) >= 8 and "cached" in data[0] and "cover_url" in data[0]

        g = test_app.client.post("/api/xhs-cards/themes/regenerate")
        assert g.status_code == 202

        # 未生成的预览图 → 404
        p = test_app.client.get("/api/xhs-cards/themes/sketch/preview/cover")
        assert p.status_code == 404
        # 未知主题 → 404
        p2 = test_app.client.get("/api/xhs-cards/themes/nope/preview/cover")
        assert p2.status_code == 404
    finally:
        test_app.cleanup()
```

> `build_test_app` 的 `client` 默认已带 admin 的 JWT cookie（见现有 `test_articles_api.py` 用法）。若清 cookie 的属性名不同（如 `c.cookies` 不可 clear），改用「新建一个无 cookie 的 TestClient(app.client.app)」——参考 `test_save_article_mcp.py:test_save_from_mcp_requires_token` 的无 token 请求写法。

- [ ] **Step 2: 运行确认失败**

Run: `docker compose exec -T app sh -c 'export GEO_TEST_DATABASE_URL="mysql+pymysql://$GEO_DB_USER:$GEO_DB_PASS@$GEO_DB_HOST:$GEO_DB_PORT/geo_test"; cd /app && python -m pytest server/tests/test_xhs_previews.py -q -k "themes"'`
Expected: FAIL（路由 404）

- [ ] **Step 3: 实现 router**

在 `server/app/modules/xhs_cards/router.py` 顶部 import 区加 `get_current_user` 与 `previews`：

```python
from server.app.core.security import get_current_user
from server.app.modules.xhs_cards import previews
```

在文件末尾加：

```python
xhs_gallery_router = APIRouter(dependencies=[Depends(get_current_user)])  # 样式库：任何登录用户


@xhs_gallery_router.get("/themes")
def list_themes() -> dict:
    return {"ok": True, "data": previews.list_theme_previews(), "error": None}


@xhs_gallery_router.get("/themes/{name}/preview/{kind}")
def theme_preview(name: str, kind: str) -> Response:
    data = previews.get_preview_bytes(name, kind)
    if data is None:
        raise HTTPException(status_code=404, detail="预览未生成或主题不存在")
    return Response(content=data, media_type="image/png")


@xhs_gallery_router.post("/themes/regenerate", status_code=202)
def regenerate_themes() -> dict:
    started = previews.spawn_regenerate()
    return {"ok": True, "data": {"status": "generating" if started else "already_running"}, "error": None}
```

> `Response`/`APIRouter`/`Depends`/`HTTPException` 该文件已 import（compose/status 用过）；确认 `get_current_user` 未重复 import。

- [ ] **Step 4: main.py 挂载**

在 `server/app/main.py` 挂 xhs 路由处（`xhs_mcp_router`/`xhs_files_router` 附近）加：

```python
from server.app.modules.xhs_cards.router import xhs_files_router, xhs_gallery_router, xhs_mcp_router
...
app.include_router(xhs_gallery_router, prefix="/api/xhs-cards", tags=["xhs-gallery"])
```

> `/themes*` 与既有 `/compose`、`/status/*`、`/file/*` 无路径冲突。

- [ ] **Step 5: 运行确认通过**

Run: `docker compose exec -T app sh -c 'export GEO_TEST_DATABASE_URL="mysql+pymysql://$GEO_DB_USER:$GEO_DB_PASS@$GEO_DB_HOST:$GEO_DB_PORT/geo_test"; cd /app && python -m pytest server/tests/test_xhs_previews.py -q'`
Expected: PASS（全绿）

- [ ] **Step 6: 真渲染人工验收（dev 容器，可选但推荐）**

Run: `docker compose exec -T app sh -c 'cd /app && python -c "from server.app.modules.xhs_cards import previews; previews.regenerate_all_previews(); print(\"done\")"'`
然后浏览器（带登录 cookie）打开 `http://127.0.0.1:8000/api/xhs-cards/themes/sketch/preview/cover` 看图。（需 chromium：`docker compose exec app python -m playwright install chromium` 若未装。）

- [ ] **Step 7: lint + commit**

```bash
git add server/app/modules/xhs_cards/router.py server/app/main.py server/tests/test_xhs_previews.py
git commit -m "feat(xhs): 样式库路由 themes/preview/regenerate（用户 JWT）"
```

---

### Task 3: 前端小红书样式库 tab

**Files:**
- Create: `web/src/api/xhsThemes.ts`
- Create: `web/src/features/prompt-templates/XhsStyleGallery.tsx`
- Modify: `web/src/routes.tsx`
- Modify: `web/src/types.ts`
- Test: `pnpm --filter @geo/web typecheck` + `build`（host）

**Interfaces:**
- Consumes: `GET /api/xhs-cards/themes`、`POST /api/xhs-cards/themes/regenerate`。
- Produces: nav 新增 `prompts:xhs_styles`；`/prompts/xhs_styles` 渲染 `<XhsStyleGallery/>`。

- [ ] **Step 1: API 客户端**

创建 `web/src/api/xhsThemes.ts`（参考同目录 `videos.ts` / `prompt-templates.ts` 的 `apiFetch`/`client` 用法——打开一个现有文件对齐 import 与请求封装）：

```ts
import { apiGet, apiPost } from "./client"; // ← 用该文件实际导出的请求助手名对齐

export interface XhsThemePreview {
  name: string;
  label: string;
  cover_url: string;
  card_url: string;
  cached: boolean;
}

export async function listXhsThemes(): Promise<XhsThemePreview[]> {
  const res = await apiGet<{ data: XhsThemePreview[] }>("/api/xhs-cards/themes");
  return res.data;
}

export async function regenerateXhsThemePreviews(): Promise<void> {
  await apiPost("/api/xhs-cards/themes/regenerate", {});
}
```

> ⚠️ `web/src/api/client.ts` 的实际导出名可能不是 `apiGet/apiPost`。先 `grep -n "export" web/src/api/client.ts` 和看 `videos.ts` 怎么调，用真实的助手函数（如 `client.get`/`request`）替换。响应包一层 `{ok,data,error}` → 取 `.data`。

- [ ] **Step 2: 画廊组件**

创建 `web/src/features/prompt-templates/XhsStyleGallery.tsx`：

```tsx
import { useEffect, useRef, useState } from "react";
import { listXhsThemes, regenerateXhsThemePreviews, type XhsThemePreview } from "../../api/xhsThemes";
import { useToast } from "../../components/…"; // ← 用项目里实际的 toast hook 路径（对齐 PromptsWorkspace 的 import）

export function XhsStyleGallery() {
  const { toast } = useToast();
  const [themes, setThemes] = useState<XhsThemePreview[]>([]);
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const pollRef = useRef<number | null>(null);

  async function load() {
    setLoading(true);
    try {
      setThemes(await listXhsThemes());
    } catch (e) {
      toast(e instanceof Error ? e.message : "加载失败", "error");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current);
    };
  }, []);

  async function regenerate() {
    setGenerating(true);
    try {
      await regenerateXhsThemePreviews();
      const started = Date.now();
      pollRef.current = window.setInterval(async () => {
        const rows = await listXhsThemes();
        setThemes(rows);
        if (rows.every((t) => t.cached) || Date.now() - started > 60000) {
          if (pollRef.current) window.clearInterval(pollRef.current);
          pollRef.current = null;
          setGenerating(false);
        }
      }, 2000);
    } catch (e) {
      toast(e instanceof Error ? e.message : "生成失败", "error");
      setGenerating(false);
    }
  }

  const noneCached = themes.length > 0 && themes.every((t) => !t.cached);

  return (
    <div className="promptsWorkspace">
      <header className="topbar">
        <div>
          <p className="eyebrow">内容资产</p>
          <h1>小红书样式库</h1>
        </div>
        <div className="topActions">
          <button className="secondaryButton" type="button" disabled={loading} onClick={() => void load()}>
            刷新
          </button>
          <button className="primaryButton" type="button" disabled={generating} onClick={() => void regenerate()}>
            {generating ? "生成中…" : "重新生成预览"}
          </button>
        </div>
      </header>

      {noneCached && !generating && (
        <div className="aiEmptyState">
          <p className="aiEmptyText">预览还没生成，点「重新生成预览」渲染各主题样卡</p>
        </div>
      )}

      <div className="xhsThemeGrid">
        {themes.map((t) => (
          <article key={t.name} className="promptTemplateCard">
            <div className="promptTemplateHeader">
              <div>
                <div className="aiCardName">{t.label}</div>
                <div className="promptTemplateMeta">
                  <span className="badge" style={{ fontFamily: "var(--mono, monospace)" }}>{t.name}</span>
                  <span className={`badge ${t.cached ? "succeeded" : "pending"}`}>
                    {t.cached ? "已生成" : "待生成"}
                  </span>
                </div>
              </div>
            </div>
            {t.cached ? (
              <div className="xhsThemePreviews">
                <img src={t.cover_url} alt={`${t.label} 封面`} loading="lazy" />
                <img src={t.card_url} alt={`${t.label} 正文卡`} loading="lazy" />
              </div>
            ) : (
              <div className="aiEmptyText" style={{ padding: "24px 0" }}>未生成</div>
            )}
          </article>
        ))}
      </div>
    </div>
  );
}
```

> import 路径（toast/组件）以 `PromptsWorkspace.tsx` 里实际用的为准（打开对齐）。`.xhsThemeGrid` / `.xhsThemePreviews` 若无对应 CSS，用最简 inline style 或复用现有网格类；不新造设计系统，样式跟现有卡片一致即可。

- [ ] **Step 3: 路由分支**

在 `web/src/routes.tsx` 顶部 import：

```tsx
const XhsStyleGallery = lazy(() =>
  import("./features/prompt-templates/XhsStyleGallery").then((m) => ({ default: m.XhsStyleGallery })),
);
```

改 `PromptsRoute`：

```tsx
function PromptsRoute() {
  const { scope } = useParams();
  const navigate = useNavigate();
  const isMobile = useIsMobile();
  if (scope === "xhs_styles") return <XhsStyleGallery />;
  const active: PromptScope = PROMPT_SCOPES.includes(scope as PromptScope)
    ? (scope as PromptScope)
    : "generation";
  return (
    <PromptsWorkspace
      scope={active}
      isMobile={isMobile}
      onScopeChange={(s) => navigate(`/prompts/${s}`)}
    />
  );
}
```

（`prompts/:scope` 路由已存在，`xhs_styles` 作为一个 `:scope` 值走同一路由，无需新增 route。）

- [ ] **Step 4: nav 加子项**

在 `web/src/types.ts` 的 `prompts` children 末尾加：

```ts
{ key: "prompts:xhs_styles", label: "小红书样式库", value: "xhs_styles" },
```

> 确认 nav child 点击是用 `value` 拼 `/prompts/${value}`（与现有 4 项一致）；若点击逻辑读 `key`，按现有模式对齐即可。

- [ ] **Step 5: 门禁**

Run（host）: `pnpm --filter @geo/web typecheck && pnpm --filter @geo/web build`
Expected: 均通过

- [ ] **Step 6: commit**

```bash
git add web/src/api/xhsThemes.ts web/src/features/prompt-templates/XhsStyleGallery.tsx web/src/routes.tsx web/src/types.ts
git commit -m "feat(web): 提示词管理新增「小红书样式库」子 tab（主题预览画廊）"
```

---

## Self-Review 结论

- **Spec 覆盖**：①后端 previews+缓存→T1；②3 端点(list/serve/regenerate,user JWT)→T2；③前端子tab+画廊+懒生成轮询+全员可重生→T3；主题真源 AVAILABLE_THEMES、封面+1卡、固定示例、进程内 lock 防并发——均落到任务。无迁移/无 MCP 工具——符合 spec。
- **Placeholder 扫描**：前端 import 助手名（apiGet/apiPost/useToast 路径）标注为「对齐现有文件」——因前端无法在容器验证、需实测导出名，故给出对齐指引而非写死错误名；这是明确的实现指令不是 TBD。其余代码完整。
- **类型一致**：`XhsThemePreview` 字段（name/label/cover_url/card_url/cached）在后端 `list_theme_previews` 返回、前端 interface、组件渲染三处一致；URL 形态 `/api/xhs-cards/themes/{name}/preview/{cover|card}` 在 previews._preview_urls、router、组件三处一致；`spawn_regenerate` 返回 bool、路由据此回 generating/already_running 一致。
- **范围**：单一小功能，3 任务，聚焦。
