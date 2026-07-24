# 按标题搜索自有文章入库 + adopt 补 question_texts 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 MCP 加一个"按标题搜自有文章"的只读工具，让"搜到 id → adopt 入高质量库"成为顺畅两步；并给 `adopt_quality_reference` 补可选 `question_texts`，使按标题/按 ID 入库都能同时标类型+问题词。

**Architecture:** 复用已有的 `articles/services/feed.py` 加一个聚焦的 title-LIKE 搜索函数；在 `mcp_catalog/router.py` 加 `GET /api/mcp/articles/search` 端点（MCP token 鉴权，router 级已挂）；在 `server/mcp/tools/catalog.py` 加 async tool。adopt 增强只在 `adopt_article` 回落分支多传一个 `question_texts`，沿链路透传到 MCP tool。

**Tech Stack:** FastAPI + SQLAlchemy（MySQL only）、FastMCP（HTTP-mount，async tool + anyio.to_thread）、pytest（`@pytest.mark.mysql` + `build_test_app`）。

## Global Constraints

- **MySQL only**；DB 测试需 `GEO_TEST_DATABASE_URL`（库名含 `test`），本机跑用 `env` python 全路径（conda activate 在工具 shell 不生效）。示例：`GEO_TEST_DATABASE_URL=mysql+pymysql://geo_user:password@127.0.0.1:3306/geo_test pytest <file> -q`。
- **MCP 端点鉴权**：`mcp_catalog/router.py` 的 `router = APIRouter(dependencies=[Depends(require_mcp_token)])` 已在 router 级挂 MCP token，新端点无需再挂；测试须带 `X-MCP-Token` 头且 `GEO_MCP_TOKEN` 已设，未设 token = 全 401。
- **MCP tool 一律 `async def` + `anyio.to_thread.run_sync`**（自调用死锁规避，见 `catalog.py` 模块 docstring），沿用 `_aget` 辅助。
- **只搜标题**（`Article.title` 子串 LIKE，转义 `% _ \`），不搜 author/plain_text；**不改** `feed.list_articles` 现有语义。
- **计数真值**：`mcp_catalog/connect_router.py:MCP_TOOLS_COUNT`。加 tool 必须同一 commit 里 `+1` 并同步三处测试断言，否则 `test_mcp_connect` 的 `tools_count == MCP_TOOLS_COUNT` 断言失败（实时 `len(tools)` 与常量必须同步）。
- **`review_status` 放开用哨兵 `"all"`**（不是 `None`）：HTTP query 参数无法干净地传 `None`（省略即回落默认 `"approved"`），故端点约定 `review_status="all"` → 不过滤；tool 同款。这是相对 spec 的一处实现细化，语义（默认 approved、可放开全部）不变。
- **`category`/`question_texts` 是回落参数**：仅当文章无 `source_question_category` 时生效；有溯源则忽略二者、走溯源自动关联（保留既有语义）。
- 提交：本仓库在 `feat-high` 分支上开发，`Commit or push only when the user asks` —— 每个 Task 的 commit 步骤照写，但实际执行 commit 前遵循会话里的授权边界。

---

### Task 1: `search_by_title` 服务函数

**Files:**
- Modify: `server/app/modules/articles/services/feed.py`（在 `serialize_article_summaries` 之前、`list_articles` 之后新增函数）
- Modify: `server/app/modules/articles/service.py:35-39`（feed re-export 元组加 `search_by_title`）+ `:69-72`（`__all__` feed 段加 `"search_by_title"`）
- Modify: `server/app/modules/articles/__init__.py:11-35`（`from .service import (...)` 块加 `search_by_title`）
- Test: `server/tests/test_articles_search_mcp.py`（新建）

**Interfaces:**
- Produces: `search_by_title(db, *, title: str, review_status: str | None = None, limit: int = 20) -> list[Article]` —— 后续 Task 2 端点消费；`review_status=None` 表示不过滤。

> **re-export 链（已核实）**：`from server.app.modules.articles import search_by_title` 依赖三跳——`services/feed.py` 定义 → `service.py` 从 `services.feed` 导入并列进 `__all__` → `__init__.py` 从 `.service` 导入。三处都要加。

- [ ] **Step 1: 写失败测试**

新建 `server/tests/test_articles_search_mcp.py`：

```python
"""按标题搜自有文章的 MCP 端点 + 服务函数（高质量库入库便利化）。

GET /api/mcp/articles/search：默认只回 approved、already_adopted 标注、snippet 截断、
路由不撞 /articles/{id:int}、MCP token 鉴权。详见
docs/superpowers/specs/2026-07-16-search-articles-by-title-adopt-design.md。
"""

from __future__ import annotations

import pytest

from server.tests.utils import build_test_app

pytestmark = pytest.mark.mysql


def _mk(db, *, title, review_status="approved", plain_text="正文内容"):
    from server.app.modules.articles.models import Article

    a = Article(
        user_id=1,
        title=title,
        content_json="{}",
        content_html="",
        plain_text=plain_text,
        word_count=len(plain_text),
        status="draft",
        review_status=review_status,
        is_deleted=False,
    )
    db.add(a)
    db.flush()
    return a


def test_search_by_title_matches_and_filters(monkeypatch):
    monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
    app_ctx = build_test_app(monkeypatch)
    db = app_ctx.session_factory()
    try:
        from server.app.modules.articles import search_by_title

        _mk(db, title="周年庆盘点", review_status="approved")
        _mk(db, title="周年庆前瞻", review_status="pending")
        _mk(db, title="无关文章", review_status="approved")
        db.commit()

        hits = search_by_title(db, title="周年庆")  # 不过滤状态
        assert {h.title for h in hits} == {"周年庆盘点", "周年庆前瞻"}

        approved = search_by_title(db, title="周年庆", review_status="approved")
        assert [h.title for h in approved] == ["周年庆盘点"]

        assert search_by_title(db, title="   ") == []  # 空标题 → []
    finally:
        db.close()
        app_ctx.cleanup()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest server/tests/test_articles_search_mcp.py::test_search_by_title_matches_and_filters -q`
Expected: FAIL —— `ImportError: cannot import name 'search_by_title'`。

- [ ] **Step 3: 实现 `search_by_title`**

在 `server/app/modules/articles/services/feed.py` 的 `list_articles` 函数之后加：

```python
def search_by_title(
    db: Session,
    *,
    title: str,
    review_status: str | None = None,
    limit: int = 20,
) -> list[Article]:
    """标题子串搜索（LIKE on articles.title，转义 % _ \\），按 updated_at 倒序。

    只搜标题（贴合"依据标题搜索"），不碰 author/plain_text，不改 list_articles 语义。
    title 为空/纯空白 → 返回 []（不退化成列全部文章）。load_only 摘要列 + plain_text 供 snippet。
    review_status=None 不过滤。
    """
    if not title or not title.strip():
        return []
    kw = title.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    stmt = (
        select(Article)
        .where(
            Article.is_deleted == False,  # noqa: E712
            Article.title.like(f"%{kw}%"),
        )
        .options(
            load_only(
                Article.title,
                Article.author,
                Article.review_status,
                Article.word_count,
                Article.plain_text,
                Article.created_at,
                Article.updated_at,
            ),
            lazyload(Article.tags),
        )
        .order_by(Article.updated_at.desc())
    )
    if review_status is not None:
        stmt = stmt.where(Article.review_status == review_status)
    stmt = stmt.limit(max(1, min(limit, 100)))
    return list(db.execute(stmt).scalars().all())
```

（`select` / `load_only` / `lazyload` / `Session` / `Article` 均已在 `feed.py` 顶部导入，无需新增 import。）

然后把 `search_by_title` 加进 re-export 链（三处）：

1. `server/app/modules/articles/service.py:35-39` 的 feed 导入元组：
   ```python
   from server.app.modules.articles.services.feed import (
       list_article_feed,
       list_articles,
       search_by_title,
       serialize_article_summaries,
   )
   ```
2. 同文件 `__all__` 的 feed 段（`service.py:69-72`）加一行：
   ```python
       # 列表 / 检索 / Feed（services/feed.py）
       "list_articles",
       "search_by_title",
       "serialize_article_summaries",
       "list_article_feed",
   ```
3. `server/app/modules/articles/__init__.py:11-35` 的 `from server.app.modules.articles.service import (...)` 块加一行 `    search_by_title,`（放在 `serialize_article_summaries,` 附近即可）。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest server/tests/test_articles_search_mcp.py::test_search_by_title_matches_and_filters -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add server/app/modules/articles/services/feed.py server/app/modules/articles/service.py server/app/modules/articles/__init__.py server/tests/test_articles_search_mcp.py
git commit -m "feat(adversarial): 文章按标题搜索服务函数 search_by_title"
```

---

### Task 2: `GET /api/mcp/articles/search` 端点 + 路由 `:int` 修正

**Files:**
- Modify: `server/app/modules/mcp_catalog/router.py`（顶部加两个 import；`mcp_get_article` 路由加 `:int`；`mcp_list_articles` 之后新增搜索端点）
- Test: `server/tests/test_articles_search_mcp.py`（追加端点测试）

**Interfaces:**
- Consumes: `search_by_title(db, *, title, review_status, limit)`（Task 1）。
- Produces: `GET /api/mcp/articles/search?title=&review_status=&limit=` → `{"items": [{"id","title","review_status","word_count","already_adopted","snippet","created_at"}]}`；`review_status="all"` → 不过滤，默认 `"approved"`。

- [ ] **Step 1: 写失败测试**

追加到 `server/tests/test_articles_search_mcp.py`：

```python
def test_search_endpoint_defaults_approved_and_flags_adopted(monkeypatch):
    monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
    app_ctx = build_test_app(monkeypatch)
    db = app_ctx.session_factory()
    try:
        a1 = _mk(db, title="周年庆盘点", review_status="approved", plain_text="开篇正文很长很长很长")
        _mk(db, title="周年庆前瞻", review_status="pending")
        db.commit()

        from server.app.modules.quality_reference import service as qsvc

        qsvc.adopt_article(db, user_id=app_ctx.admin_id, article_id=a1.id)  # 采纳 a1 进库
        db.commit()

        h = {"X-MCP-Token": "secret"}
        r = app_ctx.client.get("/api/mcp/articles/search", params={"title": "周年庆"}, headers=h)
        assert r.status_code == 200, r.text
        items = r.json()["items"]
        assert [i["title"] for i in items] == ["周年庆盘点"]  # 默认只回 approved
        assert items[0]["already_adopted"] is True
        assert items[0]["snippet"].startswith("开篇正文")

        r2 = app_ctx.client.get(
            "/api/mcp/articles/search",
            params={"title": "周年庆", "review_status": "all"},
            headers=h,
        )
        assert {i["title"] for i in r2.json()["items"]} == {"周年庆盘点", "周年庆前瞻"}
    finally:
        db.close()
        app_ctx.cleanup()


def test_search_route_does_not_shadow_get_by_id(monkeypatch):
    monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
    app_ctx = build_test_app(monkeypatch)
    db = app_ctx.session_factory()
    try:
        a = _mk(db, title="独苗文章", review_status="approved")
        db.commit()
        h = {"X-MCP-Token": "secret"}
        r_search = app_ctx.client.get(
            "/api/mcp/articles/search", params={"title": "独苗"}, headers=h
        )
        assert r_search.status_code == 200  # /search 命中搜索，不被 {id:int} 当 422
        r_get = app_ctx.client.get(f"/api/mcp/articles/{a.id}", headers=h)
        assert r_get.status_code == 200 and r_get.json()["id"] == a.id  # 单篇仍正常
    finally:
        db.close()
        app_ctx.cleanup()


def test_search_requires_mcp_token(monkeypatch):
    app_ctx = build_test_app(monkeypatch)  # 未设 GEO_MCP_TOKEN → 全 401
    try:
        r = app_ctx.client.get("/api/mcp/articles/search", params={"title": "x"})
        assert r.status_code == 401
    finally:
        app_ctx.cleanup()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest server/tests/test_articles_search_mcp.py -q -k "endpoint or shadow or requires_mcp_token"`
Expected: FAIL —— `/api/mcp/articles/search` 404/422（端点未加）或 `already_adopted` KeyError。

- [ ] **Step 3: 实现端点 + 修 `:int`**

在 `server/app/modules/mcp_catalog/router.py` 顶部 import 区（与其它 `from server.app.modules...` 并列）加：

```python
from server.app.modules.articles import search_by_title as svc_search_by_title
from server.app.modules.quality_reference.models import QualityReference
```

把现有 `mcp_get_article` 的路由装饰器（`server/app/modules/mcp_catalog/router.py:93`）改成带 `:int` 转换器：

```python
@router.get("/articles/{article_id:int}", response_model=ArticleRead)
def mcp_get_article(article_id: int, db: Session = Depends(get_db)) -> ArticleRead:
```

在 `mcp_list_articles`（`.../articles` 那个）之后、`mcp_get_article` 之前新增（模块内已有常量区则放模块顶部；这里就近定义）：

```python
_SEARCH_SNIPPET_CHARS = 100


@router.get("/articles/search")
def mcp_search_articles_by_title(
    title: str = Query(...),
    review_status: str = Query(default="approved"),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """[MCP] 按标题搜自有文章，主要给 adopt_quality_reference 挑候选。

    默认只回 approved；review_status="all" 放开全部状态。返回带 already_adopted / snippet。
    """
    svc_rs = None if review_status == "all" else review_status
    articles = svc_search_by_title(db, title=title, review_status=svc_rs, limit=limit)
    if not articles:
        return {"items": []}
    ids = [a.id for a in articles]
    adopted = {
        aid
        for (aid,) in db.execute(
            select(QualityReference.article_id).where(QualityReference.article_id.in_(ids))
        ).all()
    }
    return {
        "items": [
            {
                "id": a.id,
                "title": a.title,
                "review_status": a.review_status,
                "word_count": a.word_count,
                "already_adopted": a.id in adopted,
                "snippet": (a.plain_text or "")[:_SEARCH_SNIPPET_CHARS],
                "created_at": a.created_at,
            }
            for a in articles
        ]
    }
```

（`Query` / `select` / `Any` / `ArticleRead` / `Session` / `Depends` / `get_db` 均已在文件顶部导入。`FastAPI` 会把 dict 里的 `datetime` 经 `jsonable_encoder` 序列化成 ISO 串。）

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest server/tests/test_articles_search_mcp.py -q`
Expected: PASS（Task 1 + Task 2 全部用例）。

- [ ] **Step 5: 提交**

```bash
git add server/app/modules/mcp_catalog/router.py server/tests/test_articles_search_mcp.py
git commit -m "feat(adversarial): MCP GET /articles/search 按标题搜文章 + get-by-id 加 :int"
```

---

### Task 3: MCP tool `search_articles_by_title` + 计数 30→31 + 文档

**Files:**
- Modify: `server/mcp/tools/catalog.py`（新增 async tool）
- Modify: `server/app/modules/mcp_catalog/connect_router.py:27`（`MCP_TOOLS_COUNT = 30` → `31`）
- Modify: `server/tests/test_mcp_status_count.py:3,14,17`（函数名/注释/断言 30 → 31）
- Modify: `server/tests/test_mcp_tools_registration.py:22`（`== 30` → `31`）+ 加注册断言
- Modify: `CLAUDE.md:111,162,164`（计数 + catalog 清单）

**Interfaces:**
- Consumes: `GET /api/mcp/articles/search`（Task 2）。
- Produces: MCP tool `search_articles_by_title(title, review_status="approved", limit=20)`，注册进 `mcp._tool_manager._tools`。

- [ ] **Step 1: 写失败测试**

在 `server/tests/test_mcp_tools_registration.py` 末尾加：

```python
def test_search_articles_by_title_tool_registered():
    import server.mcp.tools.catalog  # noqa: F401  触发注册
    from server.mcp.server import mcp

    assert "search_articles_by_title" in mcp._tool_manager._tools
```

并把该文件 `test_registered_count_meets_floor` 里的 `assert MCP_TOOLS_COUNT == 30` 改为 `== 31`。

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest server/tests/test_mcp_tools_registration.py -q`
Expected: FAIL —— `search_articles_by_title` 未注册；且 `MCP_TOOLS_COUNT == 31` 断言失败（常量仍 30）。

- [ ] **Step 3: 实现 tool + 改计数 + 改其余断言**

在 `server/mcp/tools/catalog.py` 末尾（`pick_quality_references` 之后）加：

```python
@mcp.tool()
async def search_articles_by_title(
    title: str,
    review_status: str = "approved",
    limit: int = 20,
) -> dict[str, Any]:
    """按标题关键词搜自有（站内）文章，主要用于挑一篇已审文章去 adopt_quality_reference 入高质量库。

    Args:
        title: 标题关键词（子串匹配，只搜标题）。
        review_status: 默认 "approved"（只回能直接采纳的候选）；"pending" / "draft" 搜对应状态，
            "all" 搜全部状态。
        limit: 1–100，默认 20。

    Returns:
        {"ok": True, "data": {"items": [
            {"id", "title", "review_status", "word_count",
             "already_adopted": bool, "snippet": str, "created_at": str}
        ]}, "error": None}

    典型用法：先本工具拿到 id → 再 adopt_quality_reference(article_id=id) 入高质量库。
    already_adopted=True 表示该文已在库、无需重复采纳。
    """
    params: dict[str, Any] = {
        "title": title,
        "review_status": review_status,
        "limit": max(1, min(100, limit)),
    }
    return await _aget("/api/mcp/articles/search", params=params)
```

把 `server/app/modules/mcp_catalog/connect_router.py:27` 的 `MCP_TOOLS_COUNT = 30` 改成 `MCP_TOOLS_COUNT = 31`。

把 `server/tests/test_mcp_status_count.py` 改：
- 第 3 行注释 `test_mcp_tools_count_is_30：` → `test_mcp_tools_count_is_31：`
- 第 14 行 `def test_mcp_tools_count_is_30():` → `def test_mcp_tools_count_is_31():`
- 第 17 行 `assert MCP_TOOLS_COUNT == 30` → `assert MCP_TOOLS_COUNT == 31`

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest server/tests/test_mcp_tools_registration.py server/tests/test_mcp_status_count.py server/tests/test_mcp_connect.py -q`
Expected: PASS（注册断言过、计数 31、`tools_count == MCP_TOOLS_COUNT` 同步）。

- [ ] **Step 5: 更新 CLAUDE.md**

- 第 111 行 `**MCP 工具总数的唯一真值在 `connect_router.py:MCP_TOOLS_COUNT`（当前 30）**` → `（当前 31）`
- 第 162 行 `### Tool 三组（共 30 个，真值在 ...）` → `（共 31 个，真值在 ...）`
- 第 164 行 catalog 那行：`- **catalog**（只读 13 个）：` → `（只读 14 个）：`，并在列表末尾 ` / pick_quality_references` 后追加 ` / search_articles_by_title`

（第 127 行 `~30 个 atomic tools` 是约数，可保留不动。action 组计数不变——`adopt_quality_reference` 只加参数、非新 tool。）

- [ ] **Step 6: 提交**

```bash
git add server/mcp/tools/catalog.py server/app/modules/mcp_catalog/connect_router.py server/tests/test_mcp_status_count.py server/tests/test_mcp_tools_registration.py CLAUDE.md
git commit -m "feat(adversarial): 注册 search_articles_by_title MCP 工具 + 计数 30→31"
```

---

### Task 4: `adopt_quality_reference` 补可选 `question_texts`

**Files:**
- Modify: `server/app/modules/quality_reference/service.py:98-139`（`adopt_article` 加 `fallback_question_texts`，回落分支透传）
- Modify: `server/app/modules/quality_reference/mcp_router.py:50-95`（`AdoptFromMcpPayload` 加 `question_texts`；`adopt_from_mcp` 透传）
- Modify: `server/mcp/tools/action.py:445-467`（tool 加 `question_texts` 参数 + docstring）
- Test: `server/tests/test_quality_reference_mcp.py`（追加两条）

**Interfaces:**
- Consumes: 现有 `set_reference_categories(db, ref_id, items)`（items 每项 `{"category","question_texts"}`）。
- Produces: `adopt_article(db, *, user_id, article_id, fallback_category=None, fallback_question_texts=None)`；MCP `POST /api/quality-reference/adopt-from-mcp` body 多接受 `question_texts: list | None`；tool `adopt_quality_reference(article_id, category=None, question_texts=None)`。

- [ ] **Step 1: 写失败测试**

追加到 `server/tests/test_quality_reference_mcp.py`：

```python
def test_adopt_from_mcp_sets_fallback_question_texts(monkeypatch):
    """无溯源文章：回落 category + question_texts 都落到子表关联行。"""
    monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
    app_ctx = build_test_app(monkeypatch)
    db = app_ctx.session_factory()
    try:
        from server.app.modules.quality_reference.models import QualityReferenceCategory

        a = _make_article(db, review_status="approved")  # 无 source_question_category
        db.commit()
        h = {"X-MCP-Token": "secret"}
        r = app_ctx.client.post(
            "/api/quality-reference/adopt-from-mcp",
            json={"article_id": a.id, "category": "攻略", "question_texts": ["怎么开局", "新手推荐"]},
            headers=h,
        )
        assert r.status_code == 200, r.text
        ref_id = r.json()["id"]
        rows = db.query(QualityReferenceCategory).filter_by(reference_id=ref_id).all()
        assert len(rows) == 1
        assert rows[0].category == "攻略"
        assert rows[0].question_texts == ["怎么开局", "新手推荐"]
    finally:
        db.close()
        app_ctx.cleanup()


def test_adopt_from_mcp_provenance_ignores_fallback(monkeypatch):
    """有溯源文章：忽略入参 category/question_texts，走 source_question_* 自动关联。"""
    monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
    app_ctx = build_test_app(monkeypatch)
    db = app_ctx.session_factory()
    try:
        from server.app.modules.quality_reference.models import QualityReferenceCategory

        a = _make_article(db, review_status="approved")
        a.source_question_category = "剧情"
        a.source_question_texts = ["主线剧情如何"]
        db.commit()
        h = {"X-MCP-Token": "secret"}
        r = app_ctx.client.post(
            "/api/quality-reference/adopt-from-mcp",
            json={"article_id": a.id, "category": "攻略", "question_texts": ["无关问题"]},
            headers=h,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["categories"] == ["剧情"]  # 溯源优先
        rows = db.query(QualityReferenceCategory).filter_by(reference_id=body["id"]).all()
        assert rows[0].question_texts == ["主线剧情如何"]
    finally:
        db.close()
        app_ctx.cleanup()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest server/tests/test_quality_reference_mcp.py -q -k "fallback_question_texts or provenance_ignores"`
Expected: FAIL —— `question_texts` 被 payload 丢弃（`AdoptFromMcpPayload` 无此字段），子表 `question_texts` 为 `None`。

- [ ] **Step 3: 沿链路加 `question_texts`**

`server/app/modules/quality_reference/service.py` —— `adopt_article` 签名与回落分支：

```python
def adopt_article(
    db, *, user_id: int, article_id: int,
    fallback_category: str | None = None,
    fallback_question_texts: list | None = None,
) -> QualityReference:
```

把回落分支（现 `service.py:135-138`）改成：

```python
        elif fallback_category:
            set_reference_categories(
                db,
                ref.id,
                [{"category": fallback_category, "question_texts": fallback_question_texts}],
            )
```

`server/app/modules/quality_reference/mcp_router.py` —— `AdoptFromMcpPayload` 加字段：

```python
class AdoptFromMcpPayload(BaseModel):
    article_id: int
    category: str | None = Field(default=None, max_length=200)  # 无溯源类目时回落关联
    question_texts: list | None = None  # 与回落 category 配对；文章有溯源时忽略
    user_id: int = 1  # operator（Loop 身份）；tool 传 _OPERATOR_USER_ID，缺省回落 admin(1)
```

`adopt_from_mcp` 里的 `svc.adopt_article(...)` 调用加透传：

```python
        ref = svc.adopt_article(
            db,
            user_id=payload.user_id,
            article_id=payload.article_id,
            fallback_category=payload.category,
            fallback_question_texts=payload.question_texts,
        )
```

`server/mcp/tools/action.py` —— `adopt_quality_reference` 加参数 + docstring 补说明 + body：

```python
@mcp.tool()
async def adopt_quality_reference(
    article_id: int,
    category: str | None = None,
    question_texts: list[str] | None = None,
) -> dict[str, Any]:
    """采纳一篇【已过人审(approved)】站内文章进高质量库，作对抗判分的参考真品。

    - 只接受 review_status="approved" 的站内文章（复用平台审核门禁）；未审 / 软删 / 不存在
      → 报错（400）。
    - 幂等：同一篇重复采纳返回已有那条，不重复建。
    - **不能录入站外内容**：外部真品录入是前端人工动作（防 AI 把自产内容伪装成外部真品、
      毒化参考池、架空对抗判分）。想要外部参考请让人在 Web「高质量库 → 录入外部文章」加。

    Args:
        article_id: 目标文章（须已 approved）。
        category: 可选。文章无溯源类目(source_question_category)时，用它作回落关联类目；
            文章有溯源类目时后端忽略本参数。
        question_texts: 可选。与回落 category 配对使用的问题词列表；同样仅在文章无溯源类目时
            生效（有溯源走 source_question_texts）。只传 question_texts 不传 category 时无类目
            可挂、被忽略。

    Returns:
        {"ok": True, "data": {"id": int, "origin": "own", "article_id": int, "title": str,
         "is_active": bool, "categories": [str]}, "error": None}
    """
    body: dict[str, Any] = {"article_id": article_id, "user_id": _OPERATOR_USER_ID}
    if category:
        body["category"] = category
    if question_texts:
        body["question_texts"] = question_texts
    return await _apost("/api/quality-reference/adopt-from-mcp", json=body)
```

（前端 `router.py:adopt` 与 `AdoptRequest` **不动**：`adopt_article` 新参数带默认值，前端调用点无需改，问题词经既有多类型 PATCH 编辑。）

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest server/tests/test_quality_reference_mcp.py -q`
Expected: PASS（含既有 adopt/幂等/拒未审用例 + 两条新用例，回归无破坏）。

- [ ] **Step 5: 提交**

```bash
git add server/app/modules/quality_reference/service.py server/app/modules/quality_reference/mcp_router.py server/mcp/tools/action.py server/tests/test_quality_reference_mcp.py
git commit -m "feat(adversarial): adopt_quality_reference 补可选 question_texts 回落"
```

---

## 收尾校验（全部 Task 后）

- [ ] 全量相关测试：`pytest server/tests/test_articles_search_mcp.py server/tests/test_quality_reference_mcp.py server/tests/test_mcp_status_count.py server/tests/test_mcp_tools_registration.py server/tests/test_mcp_connect.py -q` → 全绿。
- [ ] Lint：`ruff check server/ && ruff format --check server/`（新代码符合 E/F/I/B/UP、line-length=100）。
- [ ] 契约自检：`search_articles_by_title` tool 的返回字段 = 端点 `items` 字段 = 测试断言字段，三处一致。
- [ ] （可选，需真实 MCP 环境）重启后端 + Claude Code，`/mcp` 看到 `search_articles_by_title`；跑一遍 `search_articles_by_title(title=…)` → `adopt_quality_reference(id, question_texts=[…])`。

## Self-Review 记录

- **Spec 覆盖**：搜索工具（§4.1）→ Task 3；端点 + `:int`（§4.2）→ Task 2；服务函数（§4.3）→ Task 1；adopt 补 question_texts（§4.4）→ Task 4；计数/文档（§五）→ Task 3；测试（§六）→ 各 Task 内 TDD。全覆盖。
- **占位符**：无 TBD/TODO；每个改动步骤都给了完整代码。
- **类型一致**：`search_by_title(review_status: str | None)` 服务层用 None 表不过滤；端点/tool 面向调用方用哨兵 `"all"`（端点 `svc_rs = None if review_status == "all" else review_status` 做转换）——分层一致、已在 Global Constraints 与 Task 2/3 显式说明。`fallback_question_texts` / `question_texts` / 子表 `question_texts` 命名贯穿一致。
- **相对 spec 的细化**：`review_status` 放开由 `None` 改为哨兵 `"all"`（HTTP query 传不了干净的 None），语义不变，已在 Global Constraints 记录。
