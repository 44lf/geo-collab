# 小红书样式库（主题预览画廊）设计

> 日期：2026-07-21　状态：待实现　作者：Glen + Claude
> 依赖：`xhs_cards` 渲染模块（分支 `feat/xhs-note-creator` / MR !51）

## 背景与目标

`xhs-note-creator` skill 的主题选择目前是**纯文本**（列 8 个主题名），运营选主题前看不到长啥样。补一个**主题预览画廊**：在「提示词管理」下新增子 tab「小红书样式库」，可视化展示每个主题的封面 + 正文卡样式。

本次只解决「能看」；**不做主题 CRUD**（加主题仍走「代码加 CSS + 发版」），维护收敛是独立方向、另议。

## 关键决策（已确认）

- 预览生成：**懒生成 + MinIO 缓存**（首次空 → 点击生成；生成后读缓存）。
- 粒度：每主题**封面 + 1 张正文卡**。
- 权限：**全员可看、全员可触发重新生成**。
- 示例文案：**固定内置**一段（不按主题变化）。
- 预览图端点：**用户 JWT 认证**（样式库在登录页内，图不必公开）。
- 放置：**提示词管理**的子 tab 行，与「AI生文提示词」等平行。

## 现状事实（代码）

- 主题真源：`server/app/modules/xhs_cards/render.py:AVAILABLE_THEMES`（8 个）。
- 渲染入口：`render.render_markdown_to_card_bytes(md, *, theme, mode, width, height, dpr) -> {"cover": bytes, "cards": [bytes]}`。
- MinIO：`xhs_cards/store.py`（桶 `geo-xhs-cards`，`ensure_bucket/put_png/get_object`；底层复用 `image_library.store`）。
- 前端子 tab：`web/src/features/prompt-templates/PromptsWorkspace.tsx` 的 `scopeTabs`（当前每项是一个 `PromptScope`）。

## 架构与数据流

```
[样式库 tab 打开]
  └─ GET /api/xhs-cards/themes  → [{name,label,cover_url,card_url,cached}]（cached=MinIO 对象是否存在）
  └─ 若有 cached：<img> 直接加载 cover_url/card_url（读 MinIO 缓存）
  └─ 若全空：显示「点击生成预览」
[点「重新生成预览」]
  └─ POST /api/xhs-cards/themes/regenerate  → 起后台线程：8 主题 × (封面+正文卡) 渲染 → 写 MinIO
       （进程内 lock 防并发双跑；固定示例 markdown，mode=separator）
  └─ 前端轮询 GET /api/xhs-cards/themes，每主题 cached 翻 true 即点亮，全好即停
```

### 后端

**新文件 `server/app/modules/xhs_cards/previews.py`**（预览编排 + 缓存，与渲染核心解耦）：

- 常量：`PREVIEW_PREFIX = "theme-previews"`；`PREVIEW_SAMPLE_MD`（固定示例，含 frontmatter + 2 张卡的正文，`---` 分隔）。
- `preview_keys(theme) -> (cover_key, card_key)`：`theme-previews/{theme}/cover.png`、`.../card.png`。
- `list_theme_previews() -> list[dict]`：遍历 `render.AVAILABLE_THEMES`，每主题查 MinIO 对象是否存在（`cached`），拼 `cover_url`/`card_url`（形如 `/api/xhs-cards/themes/{name}/preview/cover`）。
- `get_preview_bytes(theme, kind) -> bytes | None`：读 MinIO；不存在返回 None（路由转 404）。
- `regenerate_all_previews()`：对每主题 `render_markdown_to_card_bytes(PREVIEW_SAMPLE_MD, theme=…, mode="separator")` → `put_png(cover_key, cover)` + `put_png(card_key, cards[0])`；渲染失败的主题跳过并记日志、不整体失败。
- `spawn_regenerate()`：进程内 `threading.Lock` + `_generating` 标志防并发；已在跑则直接返回（幂等）。后台 `threading.Thread(daemon=True)` 执行 `regenerate_all_previews`。**不碰 DB**（预览纯靠 MinIO + render），所以不需要 `bg_session_factory` / session。

**MinIO 复用**：`store.ensure_bucket()` + `store.put_png/get_object`（桶 `geo-xhs-cards`，预览与卡片同桶不同前缀）。对象存在性检查：给 `store` 加 `object_exists(key) -> bool`（底层 `image_library.store` 的 stat/head；若无则 try get 捕异常）。

**新路由 `xhs_gallery_router`（`Depends(get_current_user)`）挂 `/api/xhs-cards`**：

- `GET /themes` → `{"ok":true,"data":[{name,label,cover_url,card_url,cached}], "error":null}`
- `GET /themes/{name}/preview/{kind}`（kind ∈ cover|card）→ `Response(PNG)`；未生成 404；未知主题 404。
- `POST /themes/regenerate` → 起后台线程，返回 `{"ok":true,"data":{"status":"generating"},"error":null}`（202）。
- `main.py` 挂载（与其它 xhs 路由同区）。**注意路由顺序**：`/themes/*` 与既有 `/file/{job_id}/*`、`/compose`、`/status` 无冲突（路径前缀不同）。

**主题 label**：`AVAILABLE_THEMES` 只有 code；给个 `THEME_LABELS: dict[str,str]`（如 `sketch→手绘素描`）放 `previews.py`，缺失回落 code 本身。单一真源仍是 `AVAILABLE_THEMES`（label 只是展示增强，缺了不影响功能）。

### 前端

- **子 tab 模型改造**（`PromptsWorkspace.tsx`）：把子 tab 从纯 `PromptScope` 扩成可含一个非 scope 项。方案：新增一个联合类型的「视图」状态 `activeView: PromptScope | "xhs_styles"`；tab 行在 `scopeTabs` 后追加 `{ view: "xhs_styles", label: "小红书样式库" }`；内容区 `activeView === "xhs_styles"` 渲染 `<XhsStyleGallery/>`，否则渲染现有提示词列表。`onScopeChange`/`propScope`（App 侧栏驱动）也要容纳这个值——App 的子 tab 列表同步加一项。
- **新组件 `web/src/features/prompt-templates/XhsStyleGallery.tsx`**：
  - 挂载即 `GET /themes`；渲染 8 主题卡片(网格)，每卡：封面 `<img>` + 正文卡 `<img>` + 主题名(等宽字体、可复制) + label。
  - 顶部「重新生成预览」按钮 → `POST /regenerate` → 进入生成中态、轮询 `GET /themes`（间隔 ~2s），全部 `cached` 或超时(如 60s)停。
  - 空态(全未 cached)：显著「点击生成预览」引导。图片 `cached=false` 时占位。
  - 复用现有 `.badge`/卡片样式，不新造设计系统。
- **API 客户端**（`web/src/api/` 对应文件，或新 `xhsThemes.ts`）：`listXhsThemes()`、`regenerateXhsThemePreviews()`；预览图直接用返回的 URL 作 `<img src>`（同源带 cookie，走用户 JWT）。
- **类型**：`XhsThemePreview = { name; label; cover_url; card_url; cached }`。

## 测试

- 后端：`previews.py` 纯逻辑单测（`preview_keys`、`list_theme_previews` 的 cached 判定用 monkeypatch `store.object_exists`）；`regenerate_all_previews` 用 monkeypatch 假 `render_markdown_to_card_bytes` + 假 `put_png` 验证每主题两次 put、失败主题跳过。路由测试（user JWT）：`/themes` 结构、`/preview/{kind}` 404、`/regenerate` 202 + lock 幂等。对标 `test_xhs_api.py` 用 `build_test_app`。
- 真渲染出图人工验收在 dev 容器（chromium）。
- 前端：`typecheck` + `build` 门禁。

## 非目标（YAGNI）

- 主题 CRUD / 上传新主题 / 每主题独立示例 / 实时渲染 / 静态烤图。
- 预览示例可配置（固定内置即可）。

## 交付 & 部署

纯前端 + 后端 3 端点 + 复用 render/MinIO。**无迁移、无新 MCP 工具**。发版 `release-*`（前后端都动）。依赖 `xhs_cards` 模块（需 MR !51 已合或本分支基于其上）。
