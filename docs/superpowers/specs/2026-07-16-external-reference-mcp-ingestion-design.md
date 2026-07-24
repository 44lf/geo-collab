# 爬取站外文章 → 异步入高质量库外部参考（MCP 入口）设计

- 日期：2026-07-16
- 状态：待审
- 关联模块：`quality_reference`、`image_library`（MinIO 封装复用）、`ai_generation/converter`、`server/mcp/tools`

## 1. 背景与问题

运营希望把**真·站外文章**（别人在其它平台写的真实内容，通过 Crawl4AI 等开源爬虫抓到）
批量沉淀进 **高质量库的"外部文章"池**（`quality_reference.origin="external"`），作为对抗判分的参考真品。

现状缺口：

- `import_external()` service 已存在且字段齐全，但**只有前端 user-JWT 端点** `POST /api/quality-reference/import`
  （见 `quality_reference/router.py` 顶部注释：本模块只服务前端页、不接 Claude Code Loop）。
- 现有 MCP 工具 `adopt_quality_reference` **显式禁止**录入站外内容，理由是"防 AI 把自产内容伪装成外部真品、
  毒化参考池"。本设计**有意识地放开这条护栏**（见 §9 安全）——因为灌入的是真实站外文章，正是参考池该有的真品。
- `markdown_to_tiptap()` **没有 `<img>` 分支**，markdown 路径会吞图，无法满足"图文结构基础保留"。
- 下载外站图 + 存储是慢/不稳的 I/O，同步调用会撞 MCP 客户端 30s 超时。

## 2. 目标 / 非目标

**目标**

- 新增一条 **MCP 入口**：把爬到的文章（markdown + 外链图）异步导入外部参考池，落库即 `is_active=True` 直接生效。
- 携带**问题类型（category）/ 问题词（question_texts）/ 平台（platform）/ 来源（source_url，必填）**。
- 图片**下载回传进 MinIO 专用桶**，正文用**站内代理内链**引用（不留外链），跨篇 **sha256 去重共享**。
- **图文结构基础保留**（标题 / 段落 / 图片位置），不吞图。
- **异步 job**：建 job 秒回 → 轮询状态，规避 MCP 超时；单图下载带超时、抓不到 best-effort 跳过。

**非目标**

- 不做爬取本身（Crawl4AI 等外部 MCP 负责）。
- 外部参考**只看不发布**，不进 articles、不进主 Asset 库、不参与任何平台发布链。
- 不做表格 / 深层嵌套的高保真（仅"标题+段落+图"基础结构）。
- 不改前端录入流程；不新增 MCP 编辑/下架能力（`patch` 仍走前端）。

## 3. 关键决策（已与运营对齐）

1. 落库**直接生效** `is_active=True`，无人工闸门。
2. `source_url` **必填**（保证每条可溯源，缓解护栏放开的风险）。
3. 图片**下载 → MinIO 专桶 → 站内内链**；**建表 + sha256 去重 + 跨篇共享 + 孤儿清理**；撞到已有图复用同内链，
   **不回改已入库文章**。
4. **异步 job**（仿 `compose_video`：thread + job 表 + 轮询），不引入 Celery / 消息队列。
5. 正文走 **markdown 路径** + 给 converter 补 `<img>` 分支（`save_article` 亦顺带受益）。

## 4. 端到端数据流

```
Claude Code 侧:
  Crawl4AI-MCP.scrape(url)              → markdown(含外链图) + 元信息
  import_external_reference(            → 返回 job_id（秒回）
      title, markdown, category,
      question_texts, platform, source_url)
  轮询 get_external_reference_status(job_id) 直到 done / failed

GEO 服务端（后台线程 worker）:
  status=running
  从 markdown 抠出图片 URL 列表
  逐图: 下载(超时+SSRF 校验+大小上限) → sha256
         → 去重命中? 复用已有 image 行 : 传 MinIO 专桶 + 建 image 行
         → 记 url → 内链 /api/quality-reference/images/<id>
         (下载失败 → 计入 skipped，剔除该图节点)
  改写 markdown 图片链接为内链
  markdown_to_tiptap(补丁后保图) + markdown_to_html + plain_text
  import_external(origin=external, is_active=True, content_json, ...,
                  category, question_texts, platform, source_url)
  写 quality_reference_image_link（reference ↔ image 关联，供孤儿清理）
  status=done, reference_id 落库, 图片统计落库
  任何异常 → status=failed, error 落库
```

## 5. 数据模型（3 张新表，均在 `quality_reference/models.py`）

### 5.1 `quality_reference_image` — 去重共享的图片资源（不属于单篇）

| 列 | 类型 | 说明 |
|---|---|---|
| id | PK int | 内链取此 id |
| sha256 | str(64) **UNIQUE** | 去重键 |
| minio_key | str(500) | 专桶内对象 key（用 `sha256+ext`） |
| bucket | str(100) | 冗余记录桶名，便于将来迁桶 |
| mime_type | str(100) | |
| size / width / height | int / int? / int? | 宽高用 `store.guess_image_size` 复用 |
| created_at | datetime | |

- 桶名常量：`geo-qref-images`（`ensure_bucket` 幂等创建）。
- **不设 reference_id**：图片跨篇共享，归属由 §5.2 关联表表达。

### 5.2 `quality_reference_image_link` — reference ↔ image 关联（供孤儿清理）

| 列 | 类型 | 说明 |
|---|---|---|
| id | PK int | |
| reference_id | FK → quality_reference(id) ON DELETE CASCADE | |
| image_id | FK → quality_reference_image(id) | |
| — | UNIQUE(reference_id, image_id) | |

- 入库时按正文实际用到的图填充（去重后的 image_id 集合）。
- **孤儿定义**：某 image 无任何 active reference 关联 → 可删（MinIO 对象 + 行）。
  清理提供一个函数 `find_orphan_reference_images()`（简单 join，参照 `articles/store.find_orphan_asset_ids`），
  v1 由手动/周期调用触发，不做自动 GC。

### 5.3 `quality_reference_import_job` — 异步导入 job（仿 `VideoJob`）

| 列 | 类型 | 说明 |
|---|---|---|
| id | PK int | |
| job_id | str(32) UNIQUE | uuid4().hex，对外句柄 |
| status | str(20) | pending / running / done / failed |
| progress | float | 0.0~1.0 |
| title / markdown / platform / source_url | 输入快照 | |
| category | str? | |
| question_texts | JSON? | |
| reference_id | int? | 成功后回填（指向新建的 quality_reference） |
| images_total / images_rehosted / images_skipped | int | 图片统计 |
| error | text? | 失败原因（`type: msg[:500]`） |
| created_at / updated_at | datetime | |

## 6. 组件划分（每单元：职责 / 接口 / 依赖）

- **`quality_reference/image_store.py`（新）** — qref 图片的 MinIO 存取 + 去重建行。
  - `ensure_qref_bucket()`；`ingest_image(db, data, mime) -> QualityReferenceImage`（算 sha256 → 去重命中复用 / 未命中传 MinIO+建行）；`internal_url(image_id) -> str`。
  - 依赖：`image_library/store`（`ensure_bucket/upload_image/get_object_bytes`）、`articles/store.guess_image_size`。
- **`quality_reference/fetch.py`（新）** — 外链图下载 + SSRF 校验（见 §9）。
  - `download_image(url, *, timeout, max_bytes) -> (bytes, mime)`；失败抛命名异常，由 worker 记 skipped。
- **`quality_reference/import_job.py`（新）** — job 生命周期 + 后台 worker（镜像 `video/service.py`）。
  - `create_import_job(db, req) -> job`（校验 + 插 pending + commit，秒回）；`spawn_import_job(job_id)`（daemon thread）；`run_import_job(job_id, session_factory)`（worker，自开 session，异常兜底 failed）；`get_import_job(db, job_id)`。
  - 依赖：`fetch`、`image_store`、`converter`、`service.import_external`、`bg_session_factory`。
- **`quality_reference/service.py`（改）** — `import_external` 已可用，无需改；worker 直接调。
- **`quality_reference/routers/mcp.py`（新，MCP-token 认证）** — `POST /import`（建 job 返回 job_id）、`GET /jobs/{job_id}`（状态）。
- **`quality_reference/routers/images.py`（新，只读代理，无需 MCP-token）** — `GET /api/quality-reference/images/{id}`：读 image 行 → MinIO 取字节 → 返回（仿 `/api/stock-images/{id}/file`，供 reader 渲染内链）。
- **`ai_generation/converter.py`（改）** — `_TiptapBuilder` 增 `img` 处理（见 §7）。
- **`server/mcp/tools/action.py`（改）** — 新增 2 个工具：`import_external_reference(...)`、`get_external_reference_status(job_id)`，`_apost/_aget` 打上面两个端点。
- **alembic 迁移（新）** — 建 3 张表。

## 7. converter `<img>` 补丁

`_TiptapBuilder` 增加对 `img` 的处理，产出 **顶层 `image` 节点**（不能嵌在 paragraph 里，因编辑器 `CustomImage` 基于
`@tiptap/extension-image` 是块级节点）：

- python-markdown 会把 `![alt](url)` 包成 `<p><img></p>`；补丁需把 image **提升到顶层**（遇 `img` 时，
  若当前在 paragraph 内则先收尾/旁路，image 落到 `_root`）。
- 节点形如 `{"type":"image","attrs":{"src": 内链, "alt": alt, "title":"", "width":"30%", "assetId": null}}`；
  worker 已把 src 改写成 `/api/quality-reference/images/<id>`，`width` 用编辑器默认 `"30%"`。
- 该补丁对 `save_article` 的 markdown 路径同样生效（此前会吞图），属正向兼容。
- 单元测试覆盖：单图成段、图文交错、多图，断言 image 均在顶层且顺序正确。

## 8. 异步 job 生命周期与超时

- **建 job 秒回**：`create_import_job` 只校验 + 插 pending 行 → 不受 MCP 30s 约束。
- **后台线程**：`threading.Thread(daemon=True)`，worker 自开 DB session（`bg_session_factory`），每图/每阶段
  `job.progress` 落库；成功写 done + reference_id + 统计；**任何异常兜底写 failed + error**（照抄 video）。
- **单图下载超时**：`fetch.download_image` 显式 `connect=5 / read=15s`，`max_bytes` 上限（如 20MB）；
  超时/超限/网络错 → 抛命名异常 → worker 计 `images_skipped`、剔除该图节点，**不卡死整个 job**。
- **僵尸 job（可选，v1 可不做）**：加"扫描 running 超过 N 分钟 → 标 failed"的兜底 sweep；与 video 现状一致，
  先不强做，列入未来工作。

## 9. 安全

- **护栏放开的补偿**：本设计有意开放 MCP 录入外部参考。补偿措施：
  (a) `source_url` 必填、服务端盖 `origin="external"`；(b) `content_html` 走 `nh3.clean`（`import_external` 已有，防存储型 XSS）；
  (c) sha256 去重防重复灌注；(d) 文档明确这是"灌真·站外文章"的运营动作，非 AI 自产。
- **SSRF**：worker 服务端下载任意图片 URL 是 SSRF 面。`fetch.download_image` 必须：
  仅允许 `http/https`；解析目标 IP，**拒绝私网 / 环回 / link-local / 云元数据（169.254.169.254）**；
  限制重定向跳数；`max_bytes` 上限；短超时。
- **内链只读**：图片代理端点只按 id 读 MinIO 字节，不接受任意 key，避免越权读桶。

## 10. 错误处理

- 建 job 阶段的校验失败（缺 source_url / title 空 / markdown 空）→ 端点同步返回 4xx（命名异常 → `mcp_exception_response`）。
- worker 阶段失败 → job.status=failed + error；MCP 侧轮询拿到 failed + error 文案。
- 图片部分失败（部分 skipped）**不判整体失败**：job 仍 done，`images_skipped>0` 由调用方据统计决定是否告警。
- `import_external` 的 content_hash 去重：撞已有参考 → 复活返回（`_insert_idempotent`），job 照样 done（幂等）。

## 11. 测试策略

- **converter**：img 顶层化 + 顺序（§7）。
- **image_store**：sha256 去重（同图第二次不新建行/不重传）、内链拼装。
- **fetch/SSRF**：私网/环回/元数据 URL 被拒；超时/超限被拒；正常图返回 bytes+mime。
- **import_job**：worker 全流程（mock download + mock MinIO）：pending→running→done、reference_id/统计落库、
  异常→failed；部分图 skipped 仍 done。
- **端点**：MCP-token 鉴权；建 job 返回 job_id；轮询返回状态；图片代理返回字节。
- **MCP 工具**：`import_external_reference` / `get_external_reference_status` 打通端点。
- **孤儿清理**：`find_orphan_reference_images` 只挑无 active 关联的图。

## 12. 未来工作（非本期）

- 僵尸 job 周期兜底 sweep。
- 自动孤儿图 GC（v1 手动/周期触发）。
- HTML 直传路径（当前仅 markdown）。
- 批量一次多篇 URL 的编排（当前一次一篇）。
