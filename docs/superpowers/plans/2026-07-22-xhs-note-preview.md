# 小红书图文预览 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 内容管理里选中 `content_type="xhs_image_text"` 的文章时，自动用「小红书图文预览」（图片轮播锁定 + 标题/文案可编辑）代替普通 Tiptap 文章编辑器，保存回库。

**Architecture:** 后端仅给 `ArticleRead` 详情加 `content_type`（复用现有 `updateArticle` 存回，无迁移无新端点）。前端在 `ContentWorkspace` 的编辑器区按 `content_type` 分叉到自包含的 `XhsNotePreview` 组件：拆 `content_json` 为图节点（轮播）+ 文本节点（可编辑），保存时合回三份并行结构。

**Tech Stack:** FastAPI + React/TS + Tiptap。

## Global Constraints

- 分支 `feat/xhs-note-preview` 基于最新 `main`（`content_type` 列 + xhs-note-creator 落库已在 main）。
- 后端测试在容器：`docker compose exec -T app sh -c 'export GEO_TEST_DATABASE_URL="mysql+pymysql://$GEO_DB_USER:$GEO_DB_PASS@$GEO_DB_HOST:$GEO_DB_PORT/geo_test"; cd /app && python -m pytest <path> -q'`。测试用 `from server.tests.utils import build_test_app`（`test_app.client`/`.admin_id`/`.session_factory()`/`.cleanup()`，client 带 admin JWT cookie）。`server/` bind-mount，host 编辑即时生效。
- 前端在 host：`pnpm --filter @geo/web typecheck` + `build`（无单测框架，这是门禁）。web/ 不在容器。
- 后端 lint（容器）：`ruff check server/` + `ruff format --check server/` + `mypy server/app`。
- 三份并行结构（`content_json`/`content_html`/`plain_text`）改一份要同步另两份——保存时前端一并算好。
- **不新造设计系统**：复用 ContentWorkspace / promptTemplate 现有 class 与按钮样式。
- 图片来源：xhs 图片是 xhs-cards URL（`assetId=None`）不是 geo Asset → `body_assets` 为空 → 轮播从 `content_json` image 节点 `attrs.src` 取。
- 发版 `release-*`；按新纪律先合 main + 解决冲突再发版。

## File Structure

- `server/app/modules/articles/schemas.py` — `ArticleRead` 加 `content_type`（类 L124 + 构造 L239）
- `server/tests/test_articles_api.py` — 详情返回 content_type 断言
- `web/src/features/content/XhsNotePreview.tsx` — 新：轮播 + 可编辑文案 + 保存
- `web/src/features/content/ContentWorkspace.tsx` — 编辑器区（~L1344）按 content_type 分叉
- （`web/src/types.ts` 无需改：`Article` 经 `ArticleSummary` 已含 `content_type`）

---

### Task 1: 后端 ArticleRead 加 content_type

**Files:**
- Modify: `server/app/modules/articles/schemas.py`（`ArticleRead` 类 ~L124 + 构造 ~L239）
- Test: `server/tests/test_articles_api.py`

**Interfaces:**
- Produces: `GET /api/articles/{id}` 详情返回体含 `content_type: str | None`。

- [ ] **Step 1: 写失败测试**

在 `server/tests/test_articles_api.py` 追加（对齐该文件既有建文章 + 取详情的写法；若已有 helper 建文章则复用）：

```python
def test_article_detail_includes_content_type(monkeypatch):
    from server.tests.utils import build_test_app

    test_app = build_test_app(monkeypatch)
    try:
        # 直接建一篇带 content_type 的文章
        from server.app.modules.articles.models import Article
        from server.app.db.session import SessionLocal

        with test_app.session_factory() as db:
            a = Article(
                title="xhs 图文A",
                content_json="{}",
                content_html="",
                plain_text="",
                word_count=0,
                user_id=test_app.admin_id,
                review_status="pending",
                content_type="xhs_image_text",
            )
            db.add(a)
            db.commit()
            db.refresh(a)
            aid = a.id

        r = test_app.client.get(f"/api/articles/{aid}")
        assert r.status_code == 200
        assert r.json()["content_type"] == "xhs_image_text"
    finally:
        test_app.cleanup()
```

> ⚠️ `Article` 模型必填字段以实际为准（`grep -n "mapped_column" server/app/modules/articles/models.py` 里 `nullable=False` 的都要给）。若直接建模型太繁，改用文件里既有的「建文章」helper / API，再 PATCH `content_type`——但 `ArticleUpdate` 的 PATCH 会过滤 None、且不一定收 content_type，故直接建模型更稳。

- [ ] **Step 2: 运行确认失败**

Run: `docker compose exec -T app sh -c 'export GEO_TEST_DATABASE_URL="mysql+pymysql://$GEO_DB_USER:$GEO_DB_PASS@$GEO_DB_HOST:$GEO_DB_PORT/geo_test"; cd /app && python -m pytest server/tests/test_articles_api.py -q -k content_type'`
Expected: FAIL（响应无 `content_type` → KeyError/None）

- [ ] **Step 3: 加字段 + populate**

`schemas.py` 的 `class ArticleRead`（~L124）里，`review_status` 附近加：

```python
    content_type: str | None = None  # 内容形态，如 "xhs_image_text"（小红书图文）
```

`schemas.py` ~L239 的 `return ArticleRead(` 构造里加一行（与其它 `article.xxx` 并列）：

```python
        content_type=article.content_type,
```

- [ ] **Step 4: 运行确认通过**

Run: `docker compose exec -T app sh -c 'export GEO_TEST_DATABASE_URL="mysql+pymysql://$GEO_DB_USER:$GEO_DB_PASS@$GEO_DB_HOST:$GEO_DB_PORT/geo_test"; cd /app && python -m pytest server/tests/test_articles_api.py -q'`
Expected: PASS（新用例 + 既有详情用例都绿）

- [ ] **Step 5: lint + commit**

Run: `docker compose exec -T app sh -c 'cd /app && ruff check server/app/modules/articles/schemas.py server/tests/test_articles_api.py && ruff format --check server/app/modules/articles/schemas.py'`

```bash
git add server/app/modules/articles/schemas.py server/tests/test_articles_api.py
git commit -m "feat(articles): ArticleRead 详情返回 content_type"
```

---

### Task 2: 前端小红书图文预览（轮播 + 可编辑文案）

**Files:**
- Create: `web/src/features/content/XhsNotePreview.tsx`
- Modify: `web/src/features/content/ContentWorkspace.tsx`（编辑器区 ~L1344 分叉）
- Test: `pnpm --filter @geo/web typecheck` + `build`

**Interfaces:**
- Consumes: `getArticle`（已在选中流程加载的 detail）、`updateArticle(id, payload)`。
- Produces: `content_type==="xhs_image_text"` 的文章在内容区显示轮播 + 可编辑文案，自包含保存。

- [ ] **Step 1: 写 XhsNotePreview.tsx**

参考 ContentWorkspace 的 `useEditor` + `buildReadonlyExtensions` 用法（`extensions: buildReadonlyExtensions()`，该扩展集用于可编辑主编辑器，名字里的 readonly 是历史命名）。组件：

```tsx
import { EditorContent, useEditor } from "@tiptap/react";
import { useMemo, useState } from "react";
import { updateArticle } from "../../api/articles";
import type { Article } from "../../types";
import { useToast } from "../../components/Toast";
import { buildReadonlyExtensions } from "./readonlyExtensions";

type XhsDoc = { type: "doc"; content: Record<string, unknown>[] };

/** 拆 content_json → 图节点（轮播，按序）+ 文本节点（可编辑） */
export function splitXhsDoc(doc: unknown): { images: { src: string; alt?: string }[]; textDoc: XhsDoc } {
  const content = (doc as XhsDoc | undefined)?.content;
  const nodes = Array.isArray(content) ? content : [];
  const images: { src: string; alt?: string }[] = [];
  const textNodes: Record<string, unknown>[] = [];
  for (const n of nodes) {
    if ((n as { type?: string }).type === "image") {
      const attrs = (n as { attrs?: { src?: string; alt?: string } }).attrs ?? {};
      if (attrs.src) images.push({ src: attrs.src, alt: attrs.alt });
    } else {
      textNodes.push(n);
    }
  }
  return { images, textDoc: { type: "doc", content: textNodes } };
}

export function XhsNotePreview({ article, onSaved }: { article: Article; onSaved?: () => void }) {
  const { toast } = useToast();
  const { images, textDoc } = useMemo(() => splitXhsDoc(article.content_json), [article.content_json]);
  const [index, setIndex] = useState(0);
  const [title, setTitle] = useState(article.title);
  const [saving, setSaving] = useState(false);
  const canEdit = article.can_edit !== false;

  const editor = useEditor(
    { extensions: buildReadonlyExtensions(), content: textDoc, editable: canEdit },
    [article.id],
  );

  async function save() {
    if (!editor) return;
    setSaving(true);
    try {
      const copyJson = editor.getJSON() as XhsDoc;
      const imageNodes = images.map((im) => ({ type: "image", attrs: { src: im.src, alt: im.alt ?? "" } }));
      const content_json = { type: "doc", content: [...imageNodes, ...(copyJson.content ?? [])] };
      const imagesHtml = images.map((im) => `<img src="${im.src}" alt="${im.alt ?? ""}" />`).join("");
      const content_html = imagesHtml + editor.getHTML();
      const plain_text = editor.getText();
      await updateArticle(article.id, { title, content_json, content_html, plain_text });
      toast("已保存", "success");
      onSaved?.();
    } catch (e) {
      toast(e instanceof Error ? e.message : "保存失败", "error");
    } finally {
      setSaving(false);
    }
  }

  const n = images.length;
  return (
    <div className="xhsNotePreview">
      {/* 轮播（图片锁定） */}
      <div className="xhsCarousel" style={{ position: "relative", textAlign: "center" }}>
        {n > 0 ? (
          <img
            src={images[index].src}
            alt={`图 ${index + 1}`}
            style={{ maxWidth: "100%", maxHeight: 520, borderRadius: "var(--r)", objectFit: "contain" }}
          />
        ) : (
          <div className="aiEmptyText" style={{ padding: 48 }}>无图片</div>
        )}
        {n > 1 && (
          <>
            <button
              className="iconButton"
              type="button"
              style={{ position: "absolute", left: 8, top: "50%" }}
              onClick={() => setIndex((i) => (i - 1 + n) % n)}
            >
              ‹
            </button>
            <button
              className="iconButton"
              type="button"
              style={{ position: "absolute", right: 8, top: "50%" }}
              onClick={() => setIndex((i) => (i + 1) % n)}
            >
              ›
            </button>
            <div style={{ display: "flex", gap: 6, justifyContent: "center", marginTop: 8 }}>
              {images.map((_, i) => (
                <span
                  key={i}
                  onClick={() => setIndex(i)}
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    cursor: "pointer",
                    background: i === index ? "var(--text, #333)" : "var(--border, #ccc)",
                  }}
                />
              ))}
            </div>
          </>
        )}
      </div>

      {/* 文案（可编辑） */}
      <div style={{ marginTop: 16 }}>
        <input
          className="titleInput"
          value={title}
          disabled={!canEdit}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="标题"
          style={{ width: "100%", fontSize: 18, fontWeight: 600, marginBottom: 8 }}
        />
        <div className="editorWrap paper-scope">
          <EditorContent editor={editor} />
        </div>
        {canEdit && (
          <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 8 }}>
            <button className="primaryButton" type="button" disabled={saving} onClick={() => void save()}>
              {saving ? "保存中…" : "保存文案"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
```

> import 路径 / `useToast` / `titleInput` 等 class 名以 ContentWorkspace 里实际用的为准（打开对齐）；`useEditor` 的第二参 deps 形式按项目 Tiptap 版本对齐（v3 用法见 ContentWorkspace L252-256）。若 `titleInput` class 不存在就用 inline style（已给）。

- [ ] **Step 2: ContentWorkspace 分叉**

在编辑器渲染区（`web/src/features/content/ContentWorkspace.tsx` ~L1344 的 `<div className="editorWrap paper-scope"><EditorContent editor={editor} /></div>` + 其下字数 footer）外层，按选中文章 `content_type` 分叉：

```tsx
{selectedArticle?.content_type === "xhs_image_text" ? (
  <XhsNotePreview article={selectedArticle} onSaved={() => void loadArticleById(selectedArticle.id)} />
) : (
  <>
    <div className="editorWrap paper-scope">
      <EditorContent editor={editor} />
    </div>
    <div style={{ /* 现有字数 footer 原样 */ }}>…</div>
  </>
)}
```

关键约束：
- xhs 分支下**不能**再走主 `editor` 的保存路径（避免用线性 editor 的 getJSON 覆盖）——xhs 的保存只由 `XhsNotePreview` 自己的「保存文案」按钮负责。检查 ContentWorkspace 若有独立的「保存正文」按钮作用于主 editor，xhs 选中时应隐藏/禁用它（它在被替换的编辑器 `<section>` 内则天然不显示；若在外层则加 `content_type !== "xhs_image_text"` 守卫）。
- `selectedArticle` 必须是**已加载详情**（含 `content_json` + `content_type`）。确认选中流程 `loadArticleById` 把详情写进 `selectedArticle`（它类型是 `Article`，含这两字段）。若 `selectedArticle` 只存列表 summary、详情在别的 state（如 `detail`），改用那个 detail 变量。
- 审核（通过/驳回）/ 分发按钮读 `selectedArticle`，与预览形态无关，**保持不变**。
- import `XhsNotePreview`。
- 主 `useEditor` hook 仍在顶部照常调用（hook 不能条件调用）；只是 xhs 时不渲染它的 `<EditorContent>`。确认这不破坏普通文章 editor 生命周期（只是多 load 一次、不显示）。

- [ ] **Step 3: 门禁**

Run（host）: `pnpm --filter @geo/web typecheck && pnpm --filter @geo/web build`
Expected: 均通过

- [ ] **Step 4: 人工验收（dev）**

起 `pnpm --filter @geo/web dev`（5173）+ 后端 → 未审核库选一篇 xhs 图文（如之前 compose 的红烧肉）→ 应见轮播（翻图/圆点）+ 可编辑文案 + 「保存文案」；改文案保存 → 重新进来看改动生效、图片不变。普通文章仍是原编辑器。

- [ ] **Step 5: commit**

```bash
git add web/src/features/content/XhsNotePreview.tsx web/src/features/content/ContentWorkspace.tsx
git commit -m "feat(web): 小红书图文专用预览（轮播 + 可编辑文案）"
```

---

## Self-Review 结论

- **Spec 覆盖**：①后端 ArticleRead.content_type→T1；②自动切换（ContentWorkspace 按 content_type 分叉）→T2 Step2；③轮播（锁定图片）→T2 XhsNotePreview 轮播；④文案可编辑 + 保存回三份结构（复用 updateArticle）→T2 save()；图片从 content_json 取（非 body_assets）→splitXhsDoc。均落任务。
- **Placeholder 扫描**：前端 import/class 名标「对齐现有文件」（因前端容器验证不了、需实测）——是明确实现指令非 TBD。T1 建模型的必填字段标了「以实际为准 + grep」。其余代码完整。
- **类型一致**：`splitXhsDoc` 返回 `{images, textDoc}` 在 save 里消费一致；`Article.content_type`（经 ArticleSummary 继承）在 T2 分叉与 XhsNotePreview 一致；`updateArticle` payload 字段（title/content_json/content_html/plain_text）与 `ArticleUpdatePayload` 一致（已核对 types.ts）。
- **风险**：ContentWorkspace 是大组件，主 editor 的 hook/保存与 xhs 分支的交互是最易出错处——T2 Step2 已把「不双保存」「hook 不条件调用」「selectedArticle 须为详情」列为显式约束。
