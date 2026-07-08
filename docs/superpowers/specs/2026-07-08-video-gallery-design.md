# 视频库（只读画廊）设计

> 状态：已定稿，待转实现计划
> 日期：2026-07-08
> 关联：视频生成 loop（`docs/superpowers/specs/2026-07-07-article-to-video-loop-design.md`）、`server/app/modules/video/`

## 目标

在网站新增一个「视频库」模块（顶级 tab），供所有登录用户浏览由视频生成 loop（`compose_video` MCP 工具）产出的配套视频：在线播放预览、下载 mp4/srt、跳回源文章。定位是**只读画廊 MVP**——看质量、拿产物去手动上传到豆包等平台。

## 范围边界

**做：**
- 一个 user-JWT 的列表端点，分页返回视频任务。
- 一个前端 tab「视频库」：卡片网格 + 状态筛选 + 播放 + 下载 + 跳源文章。

**不做（明确排除，避免范围蔓延）：**
- 缩略图/封面帧生成（原生 `<video>` 首帧够用；封面帧作为将来快速跟进）。
- 实时进度轮询（用户明确未选「实时进度」方向）。
- 删除 / 重新生成 / 编辑标题标签（轻管理在 MVP 之外）。
- 不新增 MCP 工具、不改 `MCP_TOOLS_COUNT`、不改渲染管线 / `service.py` / `video_mcp_router` / `video_files_router`。
- 不加视频属主列、不做迁移。

## 关键决策与理由

1. **全量共享、无属主。** `VideoJob` 无 `user_id`，视频由 MCP loop（service token，无用户身份）产出，天然无从归属。所有登录用户看同一份全量列表，与问题池的共享逻辑一致。→ 不加列、不写迁移。
2. **只显示 `done` + `failed` 两终态，后端在 SQL 层保证。** 端点默认 `status IN ('done','failed')`，排除 `pending`/`running`。这样"不轮询、只看终态"是服务端不变量，不依赖前端过滤。用户明确未选实时进度方向。
3. **播放/下载零改造。** 现有 `video_files_router` 的 `/api/videos/file/{job_id}`、`/api/videos/srt/{job_id}` 已是公开文件服务，前端可直接 `<video src>` 播放、`<a download>` 下载，无需新增鉴权或代理。
4. **薄切优先。** 后端只加 1 个读端点，前端 1 个 tab，复用现有 tab 注册模式 / `ErrorBoundary` / `Toast` / `/article/:id` 深链。

## 架构

### 后端：列表端点

在 `server/app/modules/video/router.py` 新增**第三个 sub-router** `video_list_router`（现有两个：`video_mcp_router`/MCP token、`video_files_router`/公开）。

```
GET /api/videos?status=&skip=&limit=
  依赖: Depends(get_current_user)          # 任何登录用户（operator + admin）
  status: Literal["done", "failed"] | None  # None = 两终态都要
  skip:   int = Query(default=0, ge=0)
  limit:  int = Query(default=24, ge=1, le=100)
```

查询语义：

- 基础过滤 `VideoJob.status IN ('done', 'failed')`；`status` 非空时进一步收窄到单一值。
- 排序 `VideoJob.created_at DESC`（newest first）。
- `article_title` 通过 join `Article`（`VideoJob.article_id → Article.id`）取；文章被删的极端情况 → `null`。
- `total` = 满足筛选（含 `status` 收窄）的总行数，供前端分页。

响应模型（Pydantic，直接返回，不套 `{ok,data,error}`——该外壳仅用于 MCP 端点）：

```python
class VideoJobSummary(BaseModel):
    job_id: str
    article_id: int
    article_title: str | None      # join Article；文章被删则 None
    title: str | None              # storyboard 标题（可能异于文章标题）
    status: str                    # "done" | "failed"
    video_url: str | None          # f"/api/videos/file/{job_id}" if video_key else None
    srt_url: str | None            # f"/api/videos/srt/{job_id}" if srt_key else None
    tags: list[str]                # job.tags or []
    engine: str | None
    error: str | None              # failed 时的原因；done 为 None
    created_at: datetime

class VideoListResponse(BaseModel):
    items: list[VideoJobSummary]
    total: int
```

挂载（`server/app/main.py`，与现有两个 video router 并列）：

```python
app.include_router(video_list_router, prefix="/api/videos", tags=["videos"])
```

> 注意：`video_files_router` 也挂在 `/api/videos` 前缀下，路由不冲突（`/file/{job_id}`、`/srt/{job_id}` vs 根 `GET /`）。列表端点是 `GET /api/videos`（根路径）。

### 前端：新 tab「视频库」

**注册改动（4 处，与现有 tab 同构）：**

- `web/src/types.ts`
  - `NavKey` 联合类型加 `"videos"`。
  - lucide import 加 `Film`。
  - `navItems` 数组插入 `{ key: "videos", label: "视频库", icon: Film }`，位置在 `image-library`（图片库）之后、`media` 之前（素材库视觉归组）。
  - 新增类型 `VideoJobSummary`（字段对齐后端响应；`created_at: string`）与 `VideoListResponse`。
- `web/src/App.tsx`
  - `KNOWN_NAV` 数组加 `"videos"`。
  - `TAB_TITLES` 加 `videos: "视频库"`。
- `web/src/routes.tsx`
  - 懒加载 `VideosWorkspace`（`lazy(() => import("./features/videos/VideosWorkspace").then((m) => ({ default: m.VideosWorkspace })))`）。
  - 路由 `{ path: "videos", element: <VideosWorkspace isActive /> }`。
- 移动端底栏（`BOTTOM_KEYS`）不改——视频库归「更多」区，和图片库/系统状态一致。

**新建 feature `web/src/features/videos/`：**

- `web/src/api/videos.ts`
  - `listVideos(params: { status?: "done" | "failed"; skip: number; limit: number }): Promise<VideoListResponse>` —— 打 `GET /api/videos`，走现有 api 请求封装（同 `web/src/api/` 其它客户端）。
- `web/src/features/videos/VideosWorkspace.tsx`
  - Props：`{ isActive: boolean }`（与其它 workspace 一致，供懒挂载）。
  - 顶部**状态筛选**分段控件：`全部 / 已完成 / 失败` → 映射 `status` 为 `undefined / "done" / "failed"`；切换时重置 `skip=0` 重新拉。
  - **卡片网格**，每张卡按 `status` 分支：
    - `done`：`<video preload="metadata" controls>`（`src=video_url`，首帧当封面）+ storyboard `title` + 源文章标题（`article_title`，点击跳 `/article/{article_id}`）+ `created_at` + `tags` + `已完成` 徽章 + **下载 mp4**（`<a download href={video_url}>`）+ **下载 srt**（`href={srt_url}`，`srt_url` 为 `null` 时禁用）。
    - `done` 但 `video_url` 为 `null`（脏数据兜底）：不给播放器，只显示标题 + 跳源文章 + 一个"产物缺失"提示。
    - `failed`：无播放器，`失败` 徽章 + `error` 原因文本 + 仍可跳源文章。
  - **分页**：`加载更多` 按钮，点击 `skip += limit` 并把新一页 `items` 追加到列表；`items.length >= total` 时隐藏按钮。
  - **空态**：列表为空显示"还没有生成的视频"。
  - **加载 / 错误**：首次加载显示 loading；`listVideos` 失败 → 复用现有 `Toast` 报错 + 提供重试。整个 workspace 由 App 层已包好的 `ErrorBoundary`（`key={activeNav}`）兜住。

## 数据流

```
用户点「视频库」tab
  → routes.tsx 懒加载 VideosWorkspace
  → listVideos({ status, skip:0, limit:24 })
  → GET /api/videos  (get_current_user 鉴权)
  → service 查 VideoJob (status IN done/failed，join Article，DESC，分页)
  → 返回 { items: VideoJobSummary[], total }
  → 卡片网格渲染
      done 卡: <video src="/api/videos/file/{job_id}">  ← video_files_router 公开服务，直连
      下载:    <a download href="/api/videos/file|srt/{job_id}">
      跳文章:  navigate(`/article/{article_id}`)         ← 已有深链
  → 「加载更多」→ listVideos({ status, skip:24, limit:24 }) → 追加
```

## 错误处理

| 场景 | 处理 |
|------|------|
| 非法 `status`（非 done/failed/空） | `Literal` 约束 → FastAPI 自动 `422` |
| `skip`/`limit` 越界 | `Query(ge/le)` 兜 → `422` |
| 未登录 | `get_current_user` → `401` |
| 源文章已删 | `article_title` = `null`，卡片正常渲染（标题回退 storyboard title） |
| `done` 但 `video_key` 缺失 | `video_url` = `null`，前端该卡降级为"产物缺失"，不给播放器 |
| 列表请求失败 | 前端 `Toast` 报错 + 重试；`ErrorBoundary` 兜底 |
| 单个 `<video>` 加载失败 | 浏览器原生行为，不额外拦 |

## 测试

新增 `server/tests/test_video_list_api.py`（`@pytest.mark.mysql`，`build_test_app`，`try/finally` cleanup）。测试数据直接插 `VideoJob` 行，不跑真实 ffmpeg/MinIO 渲染——纯查询层测试，快。

用例：

1. 默认列表只返回 `done`+`failed`，**排除** `pending`/`running`。
2. 结果按 `created_at DESC` 排序。
3. `status=done` 只返回 done；`status=failed` 只返回 failed。
4. `video_url`/`srt_url`：有 `video_key`/`srt_key` 时成形为 `/api/videos/file|srt/{job_id}`，无 key 时为 `null`。
5. `article_title` 正确 join 带出；源文章不存在时为 `null`。
6. `skip`/`limit` 分页正确 + `total` 计数正确（`total` 反映筛选后总数，不受 `limit` 截断）。
7. 未登录（清 cookie）→ `401`。
8. 非法 `status=foo` → `422`。

前端无单测框架 → `pnpm --filter @geo/web typecheck` + `build` 为门禁。

## 文件清单

**新建：**
- `server/tests/test_video_list_api.py`
- `web/src/api/videos.ts`
- `web/src/features/videos/VideosWorkspace.tsx`

**修改：**
- `server/app/modules/video/router.py`（加 `video_list_router` + `VideoJobSummary`/`VideoListResponse` schema，schema 也可放 `schemas.py`）
- `server/app/modules/video/service.py` 或新增查询函数（列表查询逻辑；service 层不抛裸 `ValueError`）
- `server/app/main.py`（挂 `video_list_router`）
- `web/src/types.ts`（NavKey / navItems / Film import / VideoJobSummary 类型）
- `web/src/App.tsx`（KNOWN_NAV / TAB_TITLES）
- `web/src/routes.tsx`（懒加载 + 路由）

## CI 门禁提醒

- 后端：`ruff check` + `ruff format --check`（用钉版 `ruff==0.15.15`）+ `mypy server/app` + `pytest`。三条 lint 都是硬门禁。
- mypy 陷阱：模块级注入句柄写 `Callable[[], Any] | None = None`（video 模块已踩过）。本端点若不引入模块级可注入句柄则不涉及。
- 前端：`typecheck` + `build`。
