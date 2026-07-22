# 小红书图文预览（内容管理专用预览形态）设计

> 日期：2026-07-22　状态：待实现　类型：前端为主 + 后端一处小改
> 依赖：`content_type="xhs_image_text"`（已在 main，由 xhs-note-creator 落库）

## 背景与目标

小红书图文文章落进未审核库后，内容管理里选中它仍以**普通文章**（Tiptap 线性文档：一叠大图 + 文本）展示，体验差。新增一个**专用预览形态**：当文章 `content_type="xhs_image_text"` 时，中间内容区自动切换成「小红书图文预览」——图片走**轮播**、文案**可编辑**并存回库。

## 关键决策（已确认）

- 切换：**自动**（content_type=xhs 直接用新预览代替文章编辑器；普通文章不变）。
- 图片：**轮播**（单图大图 + 左右箭头 + 小圆点，像真小红书）。图片**锁定不可编辑**（换图重走生成）。
- 文案：**可编辑**并保存回库（复用现有 `updateArticle`）。
- 后端：只给 `ArticleRead` 加 `content_type`，不动别的。**无迁移、无新端点**。

## 现状事实（代码）

- 内容区：`web/src/features/content/ContentWorkspace.tsx`。选中文章 → `getArticle(id)` 取 `ArticleRead` 详情 → `content_json` 灌进 Tiptap `useEditor`（`buildReadonlyExtensions`，`readonlyExtensions.tsx`）。保存走 `updateArticle(id, {title, content_json, content_html, plain_text, …})`（组件内 ~L617 组装三份并行结构）。
- `ArticleRead`（详情，schemas.py ~L123）**无** `content_type`（只有列表 `ArticleListRead` 有）。前端 `Article` 类型（types.ts）需带 `content_type`。
- xhs 图片是 xhs-cards URL（`assetId=None`），**不是 geo Asset** → 文章 `body_assets` 为空 → 轮播必须从 `content_json` 的 image 节点 `attrs.src` 取。
- 文章正文三份并行结构（`content_json`/`content_html`/`plain_text`）**改一份要同步另两份**。

## 架构与数据流

```
选中文章 detail(ArticleRead, 含 content_type)
  └─ content_type === "xhs_image_text" ?
       ├─ 是 → 渲染 <XhsNotePreview detail onSaved=…/>（代替 Tiptap 编辑器区）
       └─ 否 → 现有 Tiptap 编辑器（不变）

XhsNotePreview:
  加载: splitXhsDoc(detail.content_json) → { images: ImageNode[], textDoc: Doc }
  展示: 轮播(images, 只读) + 标题输入(article.title) + 文案编辑器(Tiptap, textDoc, 可编辑)
  保存: content_json = { type:"doc", content: [...images, ...文案.getJSON().content] }
        content_html = images.map(img => `<img src=… />`).join("") + 文案.getHTML()
        plain_text   = 文案.getText()
        → updateArticle(id, { title, content_json, content_html, plain_text })
```

### 后端
- `server/app/modules/articles/schemas.py`：`ArticleRead` 加 `content_type: str | None = None`，并在其构造处（`services/feed.py` 或详情组装处，`grep "ArticleRead("`）populate `content_type=article.content_type`。仅此。

### 前端

**`web/src/types.ts`**：确认 `Article`（详情类型）含 `content_type: string | null`（列表 `ArticleSummary` 已有；详情类型若缺则补）。

**`web/src/features/content/ContentWorkspace.tsx`**：在渲染选中文章正文的区域，`selectedDetail?.content_type === "xhs_image_text"` 时渲染 `<XhsNotePreview>`，否则现有 Tiptap 区。保留周边审核/分发按钮（它们读 `selectedArticle`，与预览形态无关）。注意：现有 `useEditor` 实例对 xhs 不需要（或空载）——确保不因 xhs 分支破坏普通文章的 editor 生命周期（gate 好条件渲染）。

**`web/src/features/content/XhsNotePreview.tsx`（新）**：
- `splitXhsDoc(doc)`：遍历 `doc.content`，`type==="image"` 收进 `images`（取 `attrs.src`/`attrs.alt`），其余进 `textDoc.content`。纯函数。
- 轮播：`index` state + `‹ ›` 按钮 + 圆点；`images[index].src` 大图；空图兜底提示。复用现有卡片/按钮 class，不新造设计系统。
- 标题：`<input>` 绑 `title` state（初始 `detail.title`）。
- 文案编辑器：一个 Tiptap `useEditor`（可编辑，扩展复用 `buildReadonlyExtensions` 里非只读相关的那套 / 或最小 StarterKit——按现有编辑器扩展对齐），`content=textDoc`。
- 保存按钮：recombine → `updateArticle`；成功 toast + 回调刷新列表/详情。
- 只读降级：`detail.can_edit === false` 时文案编辑器只读、隐藏保存（与现有一致）。

## 测试
- 后端：`ArticleRead` 含 `content_type` 且详情端点回填（加断言到 `test_articles_api.py` 详情用例，或新增小用例）。
- 前端：`splitXhsDoc` 是纯函数——可加轻量断言（但前端无单测框架，主要靠 `typecheck` + `build` 门禁 + 人工点验）。
- 人工验收：未审核库选一篇 xhs 图文 → 轮播翻图 + 改文案保存 → 重新进来看改动生效、图片不变。

## 非目标（YAGNI）
- 图片不可编辑 / 不支持换图 / 不做真实小红书发布 / 不改普通文章展示 / 不做移动端专门适配（沿用现有响应式）。

## 交付 & 部署
纯前端 + 后端一字段。无迁移、无新端点、无新 MCP 工具。发版 `release-*`（server 因 schema 动了、web 也动）。按新纪律：先合 main + 解决冲突再发版。
