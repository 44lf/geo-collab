# 小红书图文创作 skill（xhs-note-creator）设计

> 日期：2026-07-21　状态：待实现　作者：Glen + Claude

## 背景与目标

把开源项目 [Auto-Redbook-Skills](https://github.com/comeonzhj/Auto-Redbook-Skills) 的「小红书精美卡片渲染」能力接进 Geo 协作平台，形成一个可分发的 skill：

1. 读取**已审核库**（`review_status="approved"`）里的一篇文章；
2. 主对话按**可指定的提示词模板**（`list_prompt_templates`）把它精简成小红书风格内容；
3. 调用移植进后端的渲染器生成**小红书图片卡片**（封面 + 正文卡）；
4. 连同图片与文案**落库到未审核库**（`review_status="pending"`），文章卡片带「小红书图文」徽标；
5. **不做**自动发布到小红书。

产出是一个「后端渲染模块 + 2 个 MCP 工具 + `content_type` 徽标列 + 前端徽标 + 可安装/升级的 skill 包」的完整特性，分两阶段交付。

## 关键约束与既有可复用资产

- 本机（Windows 宿主）**无 Python/Playwright**，渲染只能在 geo Docker 后端跑；且「落库为图片资产」只有后端能做 → 渲染必须**服务端**执行。
- **`video` 模块**是完全对标的先例：`compose_video`/`get_video_status` 在 daemon 线程里做媒体合成 → MinIO → 回吐 URL，由 MCP 工具 + 可分发 skill 驱动。本设计照抄这套异步 job 模式。
- **多 skill 库**已存在（`skill_library_skills` 表 = `Skill` + `SkillVersion`，带 slug/version/category/is_official/上传/追加版本/回滚）。`install_loop_skills(slug=...)` 从此库取。→ 「他人安装 + 系统升级」直接复用，无需另造分发机制。
- **精简生成走主对话零配置**（与 `save_article` 一致）：Claude 主对话读提示词模板并自行精简，不调 `GEO_AI_API_KEY`、不加服务端 LLM 管线。
- `articles` 表现有 `metrics`(JSON)、`cover_asset_id`、`content_json`(Tiptap)、`review_status`(CHECK: pending/approved)，**无** `content_type` 列。

## 架构与数据流

一条主对话编排的流水线：

```
已审文章(approved)
  └─[主对话] get_article + 按指定 prompt_template 精简
        → 小红书 render-markdown（frontmatter: emoji/title/subtitle + 正文，--- 分页）
  └─[后端] compose_xhs_cards(render_md, theme, mode, ...) → job_id        ← Phase 1 核心
  └─[主对话] 轮询 get_xhs_status(job_id) → cover_url + card_urls[]（MinIO）
  └─[主对话] 拼 markdown（图片 ![](minio_url) + 小红书文案）
        → save_article(..., content_type="xhs_image_text")                ← Phase 2
        → 落未审核库(pending) + 前端「小红书图文」徽标
```

### 落库方式：方案 A（渲染返回 URL + 复用 save_article）

渲染 job 只负责「渲染 + 传 MinIO + 回吐 URL」，**不建文章**；由主对话拼 markdown 调 `save_article` 落库。理由：渲染与落库解耦，Phase 1 可单独验证渲染；`save_article` 保持为唯一落库入口。

（已否决：方案 B 渲染 job 直接建文章——更原子，但把两阶段绑死；方案 C 主对话本机渲染——本机无 Playwright，跑不了。）

## 分阶段交付

### Phase 1 —— 后端渲染模块 + MCP 工具（先本地验证图片效果）

**新模块 `server/app/modules/xhs_cards/`（对标 `server/app/modules/video/`）：**

- 移植 Auto-Redbook 的渲染资产：`assets/cover.html`、`assets/card.html`、`assets/styles.css`、`assets/themes/*.css`（8 套主题）；以及 `scripts/render_xhs.py` 的**渲染核心**（HTML 组装 + 分页切分 + `playwright.async_api` chromium 截图）。
  - **只移植渲染**：`publish_xhs.py`（自动发布）、`render_xhs_v2.*`（渐变风格备用）、xhs cookie/`.env` 一律**不移植**。
  - CLI 入口去掉，改为服务端可调用的函数；输出从「写本地 PNG 文件」改为「返回 PNG bytes」。
- `models.py`：`XhsRenderJob`（`id`/`status`(pending/running/done/failed)/`theme`/`mode`/`cover_url`/`card_urls`(JSON)/`error`/时间戳），对标 `VideoJob`。
- `service.py`：`create_render_job(...)` + `run_render_job(job_id, session_factory)` 在 `threading.Thread(daemon=True)` 里渲染 → 每张 PNG 传 MinIO（复用 `image_library` 的 MinIO store）→ 回写 URL 列表 + 置 `done`；异常置 `failed` 记 error。
- `store.py`：PNG bytes → MinIO 对象 + 可公开读取的 URL（复用或对齐 image_library / video 的公开文件服务）。
- `router.py`：`xhs_mcp_router`（`dependencies=[Depends(require_mcp_token)]`）挂 `compose` + `status`；未捕获异常走 `core/mcp_errors.mcp_exception_response`。

**新 MCP 工具（`server/mcp/tools/`，MCP token 鉴权）：**

- `compose_xhs_cards(render_markdown, theme?, mode?, width?=1080, dpr?=2)` → `{ok, data:{job_id, status}}`（202）
- `get_xhs_status(job_id)` → `{ok, data:{status, cover_url, card_urls[], error}}`
- 更新 `mcp_catalog/connect_router.py:MCP_TOOLS_COUNT` 33 → 35，并在 CLAUDE.md「Tool 三组」补两个。

**验证：** dev 容器内跑真 Playwright 出图，人工确认封面 + 卡片效果（多主题/多分页各抽验）。

### Phase 2 —— 落库 + 徽标 + skill 包

- **`content_type` 列**：`articles` 加 `content_type: str | None`（默认 null；`"xhs_image_text"` 标识小红书图文）。一次 Alembic 迁移。
- **`save_article` 加 `content_type` 可选参数**：透传到 `create_article`，写入该列。（`save-from-mcp` 端点 + MCP 工具 `save_article` 签名各加一个可选字段。）
- **落库形态**：主对话把结果拼成 geo 文章——**正文 = 封面图 + 各卡片图（顺序图片节点）+ 末尾「小红书文案区」（标题 / 正文 / SEO 标签，纯文本便于复制发布）**；封面卡设为文章 `cover_asset`。markdown 里图片用 `![](minio_url)`，经 `markdown_to_tiptap` 转图片节点入库。
  - 衔接点待 plan 核实：`markdown_to_tiptap` 遇外部/站内图片 URL 是直接引用还是 rehost 成 Asset——需保证图片长期可服务且 `cover_asset` 能被设定。
- **前端徽标**：内容列表卡片读 `content_type`，`xhs_image_text` 显示「小红书图文」标签（`web/src/features/content/`）。列表 API 需 select 出 `content_type`。
- **skill 包 `xhs-note-creator`**（category=`generation`）：SKILL.md 描述流程——选已审文章（`list_articles review_status=approved` / `get_article`）→ 选 prompt 模板（`list_prompt_templates`）精简 → **主对话每次询问用户选主题/分页**（不设强默认）→ `compose_xhs_cards` → 轮询 `get_xhs_status` → 拼 markdown → `save_article(content_type=...)`。上传进 Skill 库。
  - **安装**：`install_loop_skills(slug="xhs-note-creator")`。
  - **升级**：Skill 库追加新版本（web「Skill 库」上传或 seed），使用方重装取最新版。

## 测试策略

- 后端纯函数单测：render-markdown → HTML 组装、分页切分（separator/auto-fit/auto-split/dynamic）逻辑。
- `XhsRenderJob` 状态机测试，对标 `server/tests/test_video*`（mock 渲染器，验证 pending→running→done/failed + URL 回写）。
- dev 容器内真 Playwright 出图人工验收（不进 CI，CI 无 Xvfb/Chromium 的图形栈按需处理）。
- 迁移测试：`content_type` 列升/降级。
- 前端：`typecheck` + `build` 门禁（无单测框架）。

## 非目标（YAGNI）

- 不做自动发布到小红书（不移植 `publish_xhs.py`、不处理 XHS cookie）。
- 不移植 `render_xhs_v2`（渐变色彩备用风格）。
- 不做 web UI 触发入口（先只走 skill / MCP；后续如需再加）。
- 不做服务端 LLM 精简（走主对话零配置）。

## 杂项

- `Auto-Redbook-Skills-main/`（本地下载的参考副本）加进 `.gitignore`，**不提交**到本仓库。
- MCP 端点遵循平台约定：独立 sub-router、`require_mcp_token`、`mcp_exception_response` 包异常。
