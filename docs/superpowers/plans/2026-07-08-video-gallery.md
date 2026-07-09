# 视频库（只读画廊）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在网站新增「视频库」tab，展示由视频生成 loop 产出的配套视频（在线播放 + 下载 mp4/srt + 跳源文章）。

**Architecture:** 后端加一个 user-JWT 列表端点（SQL 层只查 `done`/`failed` 两终态，LEFT JOIN 文章标题，分页）；前端加一个懒加载 tab，卡片网格用原生 `<video>` 播放、`<a download>` 下载、复用已有 `/article/:id` 深链。现有公开文件服务不改，播放/下载零改造。

**Tech Stack:** FastAPI + SQLAlchemy（MySQL）+ Pydantic；React 19 + Vite + TypeScript + react-router + lucide。

## Global Constraints

- MySQL only；用户端点直接返回 Pydantic 模型（`{ok,data,error}` 外壳仅用于 MCP 端点）。
- service 层不抛裸 `ValueError`（本端点无自定义校验，Query 越界由 FastAPI 自动 422，无需命名异常）。
- 只显示 `done`+`failed`，由**后端 SQL 层**保证（`status IN ('done','failed')`），不靠前端过滤、不轮询。
- 全量共享、无属主：所有登录用户看同一份列表，不加 `user_id` 列、不写迁移。
- nav：标签「视频库」、图标 lucide `Film`、位置在「图片库」之后。
- 不做：缩略图生成、实时进度轮询、删除/重生成/编辑；不新增 MCP 工具、不改 `MCP_TOOLS_COUNT`、不改渲染管线 / `service.run_video_job` / `video_mcp_router` / `video_files_router`。
- CI 门禁：后端 `ruff check` + `ruff format --check`（钉版 `ruff==0.15.15`）+ `mypy server/app` + `pytest`；前端 `typecheck` + `build`（前端无单测框架）。
- 后端测试需 `@pytest.mark.mysql` + `GEO_TEST_DATABASE_URL`（DB 名含 "test"），用 `build_test_app(monkeypatch)`，`try/finally` 里 `test_app.cleanup()`。

---

## 文件结构

**新建：**
- `server/tests/test_video_list_api.py` — 列表端点的 API 测试（8 用例，直接插 `VideoJob` 行，不跑渲染）。
- `web/src/api/videos.ts` — 前端 api 客户端，一个 `listVideos()`。
- `web/src/features/videos/VideosWorkspace.tsx` — 视频库工作区（含内部 `VideoCard`）。

**修改：**
- `server/app/modules/video/schemas.py` — 加 `VideoJobSummary` + `VideoListResponse`。
- `server/app/modules/video/service.py` — 加 `list_video_jobs()` 查询函数。
- `server/app/modules/video/router.py` — 加 `video_list_router` + `_to_summary` + `list_videos` 端点。
- `server/app/main.py` — import 并挂载 `video_list_router`。
- `web/src/types.ts` — `NavKey` 加 `"videos"`、import `Film`、`navItems` 插入、加 `VideoJobSummary`/`VideoListResponse` 类型。
- `web/src/App.tsx` — `KNOWN_NAV` + `TAB_TITLES` 加 `videos`。
- `web/src/routes.tsx` — 懒加载 + 路由。

---

## Task 1: 后端视频列表端点

**Files:**
- Create: `server/tests/test_video_list_api.py`
- Modify: `server/app/modules/video/schemas.py`
- Modify: `server/app/modules/video/service.py`
- Modify: `server/app/modules/video/router.py`
- Modify: `server/app/main.py`

**Interfaces:**
- Consumes: 现有 `VideoJob` ORM（`server/app/modules/video/models.py`：`job_id/article_id/status/progress/storyboard/engine/video_key/srt_key/title/description/tags/error/created_at`）；`Article` ORM；`get_current_user`（`server/app/core/security`）；`get_db`（`server/app/db/session`）。
- Produces（Task 2 的 TS 类型必须逐字对齐）：
  - `GET /api/videos?status=&skip=&limit=` → `VideoListResponse`
  - `VideoJobSummary` 字段：`job_id: str`、`article_id: int`、`article_title: str|None`、`title: str|None`、`status: str`、`video_url: str|None`、`srt_url: str|None`、`tags: list[str]`、`engine: str|None`、`error: str|None`、`created_at: datetime`
  - `VideoListResponse` 字段：`items: list[VideoJobSummary]`、`total: int`
  - `service.list_video_jobs(db, *, status: str|None, skip: int, limit: int) -> tuple[list[tuple[VideoJob, str|None]], int]`

- [ ] **Step 1: 写失败测试**

创建 `server/tests/test_video_list_api.py`：

```python
"""视频库列表端点测试：GET /api/videos（user JWT，只回 done/failed）。"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from server.app.core.time import utcnow
from server.app.modules.video.models import VideoJob
from server.tests.utils import build_test_app


def _seed_article(db, admin_id: int, title: str = "文章标题") -> int:
    from server.app.modules.articles.models import Article

    article = Article(
        user_id=admin_id,
        title=title,
        content_json=json.dumps({"type": "doc", "content": []}),
        content_html="",
        plain_text="正文",
        word_count=0,
        status="draft",
        review_status="pending",
    )
    db.add(article)
    db.commit()
    db.refresh(article)
    return article.id


def _seed_job(
    db,
    *,
    article_id: int,
    status: str,
    job_id: str,
    video_key: str | None = None,
    srt_key: str | None = None,
    title: str | None = None,
    error: str | None = None,
    tags: list[str] | None = None,
    created_at=None,
) -> VideoJob:
    job = VideoJob(
        job_id=job_id,
        article_id=article_id,
        status=status,
        progress=1.0 if status == "done" else 0.0,
        storyboard={"title": title or "t", "shots": [{"subtitle": "a", "narration": "a"}]},
        title=title,
        video_key=video_key,
        srt_key=srt_key,
        error=error,
        tags=tags,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    if created_at is not None:
        job.created_at = created_at
        db.commit()
        db.refresh(job)
    return job


@pytest.mark.mysql
def test_default_lists_done_and_failed_excludes_pending_running(monkeypatch):
    test_app = build_test_app(monkeypatch)
    try:
        db = test_app.session_factory()
        try:
            aid = _seed_article(db, test_app.admin_id)
            _seed_job(db, article_id=aid, status="done", job_id="j-done", video_key="j-done.mp4")
            _seed_job(db, article_id=aid, status="failed", job_id="j-fail", error="boom")
            _seed_job(db, article_id=aid, status="pending", job_id="j-pend")
            _seed_job(db, article_id=aid, status="running", job_id="j-run")
        finally:
            db.close()

        resp = test_app.client.get("/api/videos")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        got = {i["job_id"] for i in body["items"]}
        assert got == {"j-done", "j-fail"}
        assert body["total"] == 2
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_ordered_by_created_at_desc(monkeypatch):
    test_app = build_test_app(monkeypatch)
    try:
        db = test_app.session_factory()
        try:
            aid = _seed_article(db, test_app.admin_id)
            now = utcnow()
            _seed_job(db, article_id=aid, status="done", job_id="old", created_at=now - timedelta(hours=2))
            _seed_job(db, article_id=aid, status="done", job_id="new", created_at=now)
        finally:
            db.close()

        body = test_app.client.get("/api/videos").json()
        assert [i["job_id"] for i in body["items"]] == ["new", "old"]
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_status_filter(monkeypatch):
    test_app = build_test_app(monkeypatch)
    try:
        db = test_app.session_factory()
        try:
            aid = _seed_article(db, test_app.admin_id)
            _seed_job(db, article_id=aid, status="done", job_id="d1", video_key="d1.mp4")
            _seed_job(db, article_id=aid, status="failed", job_id="f1", error="x")
        finally:
            db.close()

        done = test_app.client.get("/api/videos?status=done").json()
        assert {i["job_id"] for i in done["items"]} == {"d1"}
        assert done["total"] == 1

        failed = test_app.client.get("/api/videos?status=failed").json()
        assert {i["job_id"] for i in failed["items"]} == {"f1"}
        assert failed["total"] == 1
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_urls_present_when_keys_set_null_when_absent(monkeypatch):
    test_app = build_test_app(monkeypatch)
    try:
        db = test_app.session_factory()
        try:
            aid = _seed_article(db, test_app.admin_id)
            _seed_job(db, article_id=aid, status="done", job_id="withkeys", video_key="withkeys.mp4", srt_key="withkeys.srt")
            _seed_job(db, article_id=aid, status="done", job_id="nokeys")
        finally:
            db.close()

        items = {i["job_id"]: i for i in test_app.client.get("/api/videos").json()["items"]}
        assert items["withkeys"]["video_url"] == "/api/videos/file/withkeys"
        assert items["withkeys"]["srt_url"] == "/api/videos/srt/withkeys"
        assert items["nokeys"]["video_url"] is None
        assert items["nokeys"]["srt_url"] is None
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_article_title_joined(monkeypatch):
    test_app = build_test_app(monkeypatch)
    try:
        db = test_app.session_factory()
        try:
            aid = _seed_article(db, test_app.admin_id, title="独特标题ABC")
            _seed_job(db, article_id=aid, status="done", job_id="j1", video_key="j1.mp4")
        finally:
            db.close()

        item = test_app.client.get("/api/videos").json()["items"][0]
        assert item["article_title"] == "独特标题ABC"
        assert item["article_id"] == aid
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_pagination_skip_limit_total(monkeypatch):
    test_app = build_test_app(monkeypatch)
    try:
        db = test_app.session_factory()
        try:
            aid = _seed_article(db, test_app.admin_id)
            now = utcnow()
            for n in range(3):
                _seed_job(db, article_id=aid, status="done", job_id=f"j{n}", video_key=f"j{n}.mp4", created_at=now - timedelta(minutes=n))
        finally:
            db.close()

        page1 = test_app.client.get("/api/videos?skip=0&limit=2").json()
        assert len(page1["items"]) == 2
        assert page1["total"] == 3

        page2 = test_app.client.get("/api/videos?skip=2&limit=2").json()
        assert len(page2["items"]) == 1
        assert page2["total"] == 3
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_requires_auth(monkeypatch):
    test_app = build_test_app(monkeypatch)
    try:
        test_app.client.cookies.clear()
        resp = test_app.client.get("/api/videos")
        assert resp.status_code == 401, resp.text
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_invalid_status_422(monkeypatch):
    test_app = build_test_app(monkeypatch)
    try:
        resp = test_app.client.get("/api/videos?status=foo")
        assert resp.status_code == 422, resp.text
    finally:
        test_app.cleanup()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `GEO_TEST_DATABASE_URL=mysql+pymysql://geo_user:password@127.0.0.1:3306/geo_test pytest server/tests/test_video_list_api.py -q`
（dev 容器内：`docker compose exec -e GEO_TEST_DATABASE_URL=... app pytest server/tests/test_video_list_api.py -q`）
Expected: FAIL —— `GET /api/videos` 未定义，返回 404 或路由不存在（多数用例 `assert 200` 失败）。

- [ ] **Step 3: 加响应 schema**

在 `server/app/modules/video/schemas.py` 顶部 import 区加 `from datetime import datetime`，文件末尾追加：

```python
class VideoJobSummary(BaseModel):
    job_id: str
    article_id: int
    article_title: str | None
    title: str | None
    status: str
    video_url: str | None
    srt_url: str | None
    tags: list[str]
    engine: str | None
    error: str | None
    created_at: datetime


class VideoListResponse(BaseModel):
    items: list[VideoJobSummary]
    total: int
```

- [ ] **Step 4: 加查询函数**

在 `server/app/modules/video/service.py` 末尾追加（`Article` 已在文件顶部 import）：

```python
def list_video_jobs(
    db: Session,
    *,
    status: str | None,
    skip: int,
    limit: int,
) -> tuple[list[tuple[VideoJob, str | None]], int]:
    """列出 done/failed 视频任务（LEFT JOIN 文章标题），created_at DESC，分页。

    返回 (rows, total)。rows 每项为 (VideoJob, article_title|None)；
    total 为满足筛选的总数（不受 skip/limit 影响）。
    """
    conditions = [VideoJob.status.in_(("done", "failed"))]
    if status is not None:
        conditions.append(VideoJob.status == status)
    total = db.query(VideoJob.id).filter(*conditions).count()
    rows = (
        db.query(VideoJob, Article.title)
        .outerjoin(Article, VideoJob.article_id == Article.id)
        .filter(*conditions)
        .order_by(VideoJob.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return [(row[0], row[1]) for row in rows], total
```

- [ ] **Step 5: 加路由 + 挂载**

在 `server/app/modules/video/router.py`：

顶部 import 调整/新增：
```python
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query  # Query 为新增

from server.app.core.security import get_current_user  # 新增
from server.app.modules.video.schemas import (  # 扩充这一行 import
    ComposeVideoRequest,
    VideoJobSummary,
    VideoListResponse,
)
from server.app.modules.video.service import (  # 扩充这一行 import
    create_video_job,
    list_video_jobs,
    spawn_video_job,
)
```

在 `video_files_router = APIRouter()` 那行之后加：
```python
video_list_router = APIRouter(dependencies=[Depends(get_current_user)])  # 视频库：任何登录用户
```

在文件末尾追加：
```python
def _to_summary(job: VideoJob, article_title: str | None) -> VideoJobSummary:
    return VideoJobSummary(
        job_id=job.job_id,
        article_id=job.article_id,
        article_title=article_title,
        title=job.title,
        status=job.status,
        video_url=f"/api/videos/file/{job.job_id}" if job.video_key else None,
        srt_url=f"/api/videos/srt/{job.job_id}" if job.srt_key else None,
        tags=job.tags or [],
        engine=job.engine,
        error=job.error,
        created_at=job.created_at,
    )


@video_list_router.get("", response_model=VideoListResponse)
def list_videos(
    status: Literal["done", "failed"] | None = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=24, ge=1, le=100),
    db: Session = Depends(get_db),
) -> VideoListResponse:
    """[web] 视频库列表：只回 done/failed，created_at DESC，分页。"""
    rows, total = list_video_jobs(db, status=status, skip=skip, limit=limit)
    return VideoListResponse(items=[_to_summary(j, t) for j, t in rows], total=total)
```

在 `server/app/main.py`：
- 把 `from server.app.modules.video.router import video_files_router, video_mcp_router` 改为
  `from server.app.modules.video.router import video_files_router, video_list_router, video_mcp_router`
- 在挂载 `video_files_router` 那行（`app.include_router(video_files_router, prefix="/api/videos", tags=["video-files"])`）之后加一行：
  ```python
  app.include_router(video_list_router, prefix="/api/videos", tags=["videos"])
  ```
  （三个 router 同前缀但路径互不冲突：list=`GET ""`、files=`/file|/srt`、mcp=`/compose|/status`。）

- [ ] **Step 6: 跑测试确认通过**

Run: `GEO_TEST_DATABASE_URL=mysql+pymysql://geo_user:password@127.0.0.1:3306/geo_test pytest server/tests/test_video_list_api.py -q`
Expected: PASS（8 passed）。

- [ ] **Step 7: lint + 类型检查**

Run（钉版）:
```bash
pip install ruff==0.15.15 >/dev/null 2>&1
ruff format server/app/modules/video/ server/tests/test_video_list_api.py server/app/main.py
ruff check server/app/modules/video/ server/tests/test_video_list_api.py server/app/main.py
ruff format --check server/
mypy server/app
```
Expected: ruff check「All checks passed!」、`ruff format --check` 无 diff、mypy「Success」。

- [ ] **Step 8: 提交**

```bash
git add server/app/modules/video/schemas.py server/app/modules/video/service.py server/app/modules/video/router.py server/app/main.py server/tests/test_video_list_api.py
git commit -m "feat(video): 视频库列表端点 GET /api/videos（user JWT，只回 done/failed）"
```

---

## Task 2: 前端「视频库」tab

**Files:**
- Modify: `web/src/types.ts`
- Create: `web/src/api/videos.ts`
- Modify: `web/src/App.tsx`
- Modify: `web/src/routes.tsx`
- Create: `web/src/features/videos/VideosWorkspace.tsx`

**Interfaces:**
- Consumes: Task 1 的 `GET /api/videos` + `VideoListResponse`/`VideoJobSummary` 字段（逐字对齐）；现有 `api`（`web/src/api/core.ts`）；`useToast`（`web/src/components/Toast.tsx`，返回 `{ toast: showToast }`，`showToast(message, "error"|"success")`）；`/article/:id` 深链路由。
- Produces: 顶级 tab `/videos` 渲染 `VideosWorkspace`。

- [ ] **Step 1: 加 TS 类型 + nav 注册**

在 `web/src/types.ts`：

1) 第 1 行 lucide import 加 `Film`（放 `FileText` 与 `Images` 之间）：
```typescript
import { Bot, FileText, Film, Images, MessagesSquare, MonitorCog, Plug, RadioTower, Send, Sparkles } from "lucide-react";
```

2) 第 4 行 `NavKey` 联合类型加 `"videos"`（放 `"image-library"` 之后）：
```typescript
export type NavKey = "agents" | "ai" | "content" | "prompts" | "image-library" | "videos" | "media" | "tasks" | "system" | "mcp-connect" | "admin" | "audit-logs" | "ai-models";
```

3) `navItems` 数组里 `{ key: "image-library", label: "图片库", icon: Images },` 那行之后插入：
```typescript
  { key: "videos", label: "视频库", icon: Film },
```

4) 文件末尾追加类型：
```typescript
export type VideoJobSummary = {
  job_id: string;
  article_id: number;
  article_title: string | null;
  title: string | null;
  status: "done" | "failed";
  video_url: string | null;
  srt_url: string | null;
  tags: string[];
  engine: string | null;
  error: string | null;
  created_at: string;
};

export type VideoListResponse = {
  items: VideoJobSummary[];
  total: number;
};
```

- [ ] **Step 2: 加 api 客户端**

创建 `web/src/api/videos.ts`：
```typescript
import { api } from "./core";
import type { VideoListResponse } from "../types";

export function listVideos(params: {
  status?: "done" | "failed";
  skip: number;
  limit: number;
}): Promise<VideoListResponse> {
  const qs = new URLSearchParams();
  if (params.status) qs.set("status", params.status);
  qs.set("skip", String(params.skip));
  qs.set("limit", String(params.limit));
  return api<VideoListResponse>(`/api/videos?${qs.toString()}`);
}
```

- [ ] **Step 3: App.tsx 注册 KNOWN_NAV + TAB_TITLES**

在 `web/src/App.tsx`：

`KNOWN_NAV` 数组（约 18-21 行）加 `"videos"`（放 `"image-library"` 之后）：
```typescript
const KNOWN_NAV: NavKey[] = [
  "agents", "ai", "content", "prompts", "image-library", "videos", "media", "tasks",
  "system", "mcp-connect", "admin", "audit-logs", "ai-models",
];
```

`TAB_TITLES`（约 24-29 行）加一项：
```typescript
  "image-library": "图片库", videos: "视频库", media: "媒体矩阵", tasks: "分发引擎", system: "系统状态",
```
（即在 `"image-library": "图片库",` 之后插入 `videos: "视频库",`。）

- [ ] **Step 4: routes.tsx 懒加载 + 路由**

在 `web/src/routes.tsx`：

在 `AiModelsWorkspace` 懒加载定义（约 45-47 行）之后加：
```typescript
const VideosWorkspace = lazy(() =>
  import("./features/videos/VideosWorkspace").then((m) => ({ default: m.VideosWorkspace })),
);
```

在路由 `{ path: "image-library", element: <ImageLibraryWorkspace /> },`（约 119 行）之后加：
```typescript
      { path: "videos", element: <VideosWorkspace /> },
```

- [ ] **Step 5: 写 VideosWorkspace 组件**

创建 `web/src/features/videos/VideosWorkspace.tsx`：
```tsx
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Film } from "lucide-react";
import { listVideos } from "../../api/videos";
import type { VideoJobSummary } from "../../types";
import { useToast } from "../../components/Toast";

const PAGE = 24;
type Filter = "all" | "done" | "failed";

const FILTERS: { key: Filter; label: string }[] = [
  { key: "all", label: "全部" },
  { key: "done", label: "已完成" },
  { key: "failed", label: "失败" },
];

export function VideosWorkspace() {
  const [filter, setFilter] = useState<Filter>("all");
  const [items, setItems] = useState<VideoJobSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const navigate = useNavigate();
  const { toast: showToast } = useToast();

  async function fetchPage(reset: boolean, curLen: number) {
    setLoading(true);
    try {
      const res = await listVideos({
        status: filter === "all" ? undefined : filter,
        skip: reset ? 0 : curLen,
        limit: PAGE,
      });
      setTotal(res.total);
      setItems((prev) => (reset ? res.items : [...prev, ...res.items]));
      setLoaded(true);
    } catch (e) {
      showToast(e instanceof Error ? e.message : "加载视频失败", "error");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void fetchPage(true, 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter]);

  const hasMore = items.length < total;

  return (
    <div style={{ padding: 24 }}>
      <header style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 20 }}>
        <Film size={22} />
        <h2 style={{ margin: 0, fontSize: 20 }}>视频库</h2>
        <span style={{ color: "#888", fontSize: 13 }}>共 {total} 个</span>
        <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
          {FILTERS.map((f) => (
            <button
              key={f.key}
              type="button"
              onClick={() => setFilter(f.key)}
              style={{
                padding: "6px 14px",
                borderRadius: 8,
                border: "1px solid #ddd",
                background: filter === f.key ? "#2563eb" : "#fff",
                color: filter === f.key ? "#fff" : "#333",
                cursor: "pointer",
              }}
            >
              {f.label}
            </button>
          ))}
        </div>
      </header>

      {loaded && items.length === 0 && !loading && (
        <p className="emptyText" style={{ padding: 24 }}>
          还没有生成的视频
        </p>
      )}

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))",
          gap: 16,
        }}
      >
        {items.map((v) => (
          <VideoCard key={v.job_id} video={v} onOpenArticle={(id) => navigate(`/article/${id}`)} />
        ))}
      </div>

      <div style={{ textAlign: "center", marginTop: 20 }}>
        {loading && <span style={{ color: "#888" }}>加载中…</span>}
        {!loading && hasMore && (
          <button
            type="button"
            onClick={() => void fetchPage(false, items.length)}
            style={{ padding: "8px 20px", borderRadius: 8, border: "1px solid #ddd", background: "#fff", cursor: "pointer" }}
          >
            加载更多
          </button>
        )}
      </div>
    </div>
  );
}

function VideoCard({
  video,
  onOpenArticle,
}: {
  video: VideoJobSummary;
  onOpenArticle: (articleId: number) => void;
}) {
  const title = video.title || video.article_title || `视频 ${video.job_id.slice(0, 8)}`;
  return (
    <div style={{ border: "1px solid #eee", borderRadius: 12, overflow: "hidden", background: "#fff", display: "flex", flexDirection: "column" }}>
      {video.status === "done" && video.video_url ? (
        <video
          src={video.video_url}
          controls
          preload="metadata"
          style={{ width: "100%", aspectRatio: "9 / 16", objectFit: "cover", background: "#000" }}
        />
      ) : (
        <div
          style={{
            width: "100%",
            aspectRatio: "9 / 16",
            background: "#f6f6f6",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "#c0392b",
            fontSize: 13,
            padding: 12,
            textAlign: "center",
          }}
        >
          {video.status === "failed" ? `渲染失败：${video.error ?? "未知错误"}` : "产物缺失"}
        </div>
      )}
      <div style={{ padding: 12, display: "flex", flexDirection: "column", gap: 8 }}>
        <div style={{ fontWeight: 600, fontSize: 14, lineHeight: 1.3 }}>{title}</div>
        <button
          type="button"
          onClick={() => onOpenArticle(video.article_id)}
          style={{ alignSelf: "flex-start", padding: 0, border: "none", background: "none", color: "#2563eb", cursor: "pointer", fontSize: 12 }}
        >
          {video.article_title ? `源文章：${video.article_title}` : `源文章 #${video.article_id}`}
        </button>
        {video.tags.length > 0 && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
            {video.tags.map((t) => (
              <span key={t} style={{ fontSize: 11, background: "#f0f0f0", borderRadius: 4, padding: "2px 6px", color: "#666" }}>
                {t}
              </span>
            ))}
          </div>
        )}
        <div style={{ fontSize: 11, color: "#999" }}>{new Date(video.created_at).toLocaleString("zh-CN")}</div>
        {video.status === "done" && (
          <div style={{ display: "flex", gap: 12 }}>
            {video.video_url && (
              <a href={video.video_url} download style={{ fontSize: 12, color: "#2563eb" }}>
                下载 mp4
              </a>
            )}
            {video.srt_url && (
              <a href={video.srt_url} download style={{ fontSize: 12, color: "#2563eb" }}>
                下载 srt
              </a>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 6: typecheck**

Run: `pnpm --filter @geo/web typecheck`
Expected: 无 error（tsc 通过）。

- [ ] **Step 7: build**

Run: `pnpm --filter @geo/web build`
Expected: build 成功产出 `web/dist/`。

- [ ] **Step 8: 提交**

```bash
git add web/src/types.ts web/src/api/videos.ts web/src/App.tsx web/src/routes.tsx web/src/features/videos/VideosWorkspace.tsx
git commit -m "feat(web): 新增「视频库」tab 展示 loop 产出的配套视频"
```

---

## Self-Review

**1. Spec coverage：**
- 只读画廊（列表+播放+下载+跳文章）→ Task 2 卡片。✅
- user-JWT 列表端点、SQL 层只回 done/failed → Task 1 Step 4-5。✅
- 全量共享无属主 → 端点无 user 过滤（`dependencies=[Depends(get_current_user)]` 仅鉴权）。✅
- 不轮询、不缩略图、不管理 → Task 2 无 setInterval、原生 `<video>` 首帧、无删除/重生成按钮。✅
- nav 视频库/Film/图片库之后 → Task 2 Step 1/3/4。✅
- 8 个测试用例 → Task 1 Step 1 全覆盖（默认过滤/排序/状态筛选/URL/join/分页/401/422）。✅
- 「源文章已删→article_title null」：FK RESTRICT 下 orphan 不可构造（删文章会被引用它的 job 挡住），故 LEFT JOIN 为防御性、无对应测试用例；`article_title: str|None` 保留可空。已在此说明，非遗漏。

**2. Placeholder 扫描：** 无 TBD/TODO；每个改动步骤都给了完整代码与确切锚点。✅

**3. 类型一致性：** 后端 `VideoJobSummary`（Task 1）与前端 `VideoJobSummary`（Task 2）字段逐字对齐（`created_at` 后端 datetime → JSON ISO string → 前端 `string`）；`video_url`/`srt_url` 生成规则 `/api/videos/file|srt/{job_id}` 与前端直接消费一致；`listVideos` 参数 `{status?,skip,limit}` 与端点 Query 对齐。✅
