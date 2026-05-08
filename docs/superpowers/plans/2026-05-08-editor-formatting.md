# Editor Formatting Enhancement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add font size, text/highlight color, underline, strikethrough, text alignment, undo/redo, clear-format, and drag-to-resize images to the Tiptap editor in `web/src/main.tsx`.

**Architecture:** Five official Tiptap v3 extensions are installed (`text-style`, `color`, `highlight`, `underline`, `text-align`). Font size is implemented by extending `TextStyle` with a `fontSize` attribute — no extra package needed. Image resizing uses a custom React `NodeView` (no new packages) that wraps the existing `CustomImage` node and renders drag handles when selected. All changes are in `web/src/main.tsx` and `web/src/styles.css`; no backend changes.

**Tech Stack:** Tiptap v3 (`@tiptap/react` `^3.22.5`), React 19, TypeScript, lucide-react, pnpm workspaces.

---

### Task 1: Install new Tiptap extension packages

**Files:**
- Modify: `web/package.json`

- [ ] **Step 1: Run install**

```powershell
pnpm --filter @geo/web add @tiptap/extension-text-style @tiptap/extension-color @tiptap/extension-highlight @tiptap/extension-underline @tiptap/extension-text-align
```

Expected: five new entries appear under `"dependencies"` in `web/package.json`, all at version `^3.x.x`.

- [ ] **Step 2: Commit**

```bash
git add web/package.json pnpm-lock.yaml
git commit -m "feat: install tiptap formatting extensions"
```

---

### Task 2: Replace imports and add CustomTextStyle + ImageResizeView + updated CustomImage

**Files:**
- Modify: `web/src/main.tsx` lines 1–17 (current imports + CustomImage definition)

- [ ] **Step 1: Replace lines 1–17 with updated imports**

The current file starts with:
```tsx
import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { EditorContent, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import Image from "@tiptap/extension-image";
const CustomImage = Image.extend({
  addAttributes() {
    return {
      ...this.parent?.(),
      assetId: {
        default: null,
        parseHTML: (el) => el.getAttribute("data-asset-id"),
        renderHTML: (attrs) => (attrs.assetId ? { "data-asset-id": attrs.assetId } : {}),
      },
    };
  },
});
import Link from "@tiptap/extension-link";
```

Replace all of the above (lines 1–18) with:

```tsx
import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { EditorContent, NodeViewWrapper, ReactNodeViewRenderer, useEditor } from "@tiptap/react";
import type { NodeViewProps } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import Image from "@tiptap/extension-image";
import Link from "@tiptap/extension-link";
import TextStyle from "@tiptap/extension-text-style";
import Color from "@tiptap/extension-color";
import Highlight from "@tiptap/extension-highlight";
import Underline from "@tiptap/extension-underline";
import TextAlign from "@tiptap/extension-text-align";

const CustomTextStyle = TextStyle.extend({
  addAttributes() {
    return {
      ...this.parent?.(),
      fontSize: {
        default: null,
        parseHTML: (el) => el.style.fontSize || null,
        renderHTML: (attrs) =>
          attrs.fontSize ? { style: `font-size: ${attrs.fontSize}` } : {},
      },
    };
  },
});

function ImageResizeView({ node, updateAttributes, selected }: NodeViewProps) {
  const attrs = node.attrs as {
    src: string;
    alt: string;
    title: string;
    assetId: string | null;
    width: string;
  };
  const imgRef = useRef<HTMLImageElement>(null);

  function startResize(e: React.MouseEvent) {
    e.preventDefault();
    const startX = e.clientX;
    const startWidth = imgRef.current?.offsetWidth ?? 300;
    const containerWidth = imgRef.current?.parentElement?.offsetWidth ?? 600;

    function onMove(ev: MouseEvent) {
      const pct = Math.min(
        100,
        Math.max(10, Math.round(((startWidth + ev.clientX - startX) / containerWidth) * 100)),
      );
      updateAttributes({ width: `${pct}%` });
    }
    function onUp() {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    }
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  }

  return (
    <NodeViewWrapper style={{ display: "block", position: "relative", width: attrs.width ?? "100%" }}>
      <img
        ref={imgRef}
        src={attrs.src}
        alt={attrs.alt ?? ""}
        title={attrs.title ?? ""}
        data-asset-id={attrs.assetId ?? undefined}
        style={{ width: "100%", display: "block", borderRadius: "var(--r)" }}
        draggable={false}
      />
      {selected && <div className="imgResizeHandle" onMouseDown={startResize} />}
    </NodeViewWrapper>
  );
}

const CustomImage = Image.extend({
  addAttributes() {
    return {
      ...this.parent?.(),
      assetId: {
        default: null,
        parseHTML: (el) => el.getAttribute("data-asset-id"),
        renderHTML: (attrs) => (attrs.assetId ? { "data-asset-id": attrs.assetId } : {}),
      },
      width: {
        default: "100%",
        parseHTML: (el) => el.style.width || "100%",
        renderHTML: (attrs) => ({ style: `width: ${attrs.width ?? "100%"}` }),
      },
    };
  },
  addNodeView() {
    return ReactNodeViewRenderer(ImageResizeView);
  },
});
```

- [ ] **Step 2: Update the `extensions` array inside `useEditor`**

Find this block (~line 558 after the edit above):
```ts
extensions: [
  StarterKit,
  Link.configure({ openOnClick: false }),
  CustomImage.configure({ allowBase64: false }),
],
```

Replace it with:
```ts
extensions: [
  StarterKit,
  Link.configure({ openOnClick: false }),
  CustomImage.configure({ allowBase64: false }),
  CustomTextStyle,
  Color,
  Highlight.configure({ multicolor: true }),
  Underline,
  TextAlign.configure({ types: ["heading", "paragraph"] }),
],
```

- [ ] **Step 3: TypeScript check**

```powershell
pnpm --filter @geo/web typecheck
```

Expected: no errors. If `NodeViewProps` is not found, verify the import is `import type { NodeViewProps } from "@tiptap/react"`.

- [ ] **Step 4: Commit**

```bash
git add web/src/main.tsx
git commit -m "feat: add CustomTextStyle, ImageResizeView, register extensions"
```

---

### Task 3: Rewrite EditorToolbar with all new controls

**Files:**
- Modify: `web/src/main.tsx` — lucide import block + `EditorToolbar` component

- [ ] **Step 1: Update the lucide-react import block**

Find the current lucide-react import (starts with `import {` around line 19 after Task 2's edit) and replace the entire block with:

```tsx
import {
  AlignCenter,
  AlignLeft,
  AlignRight,
  Baseline,
  Bold,
  CheckCircle2,
  ChevronRight,
  Download,
  Eraser,
  FileText,
  Heading1,
  Heading2,
  Highlighter,
  ImagePlus,
  Italic,
  LinkIcon,
  List,
  ListOrdered,
  MonitorCog,
  Plus,
  Quote,
  RadioTower,
  Redo2,
  RefreshCw,
  Save,
  Search,
  Send,
  Strikethrough,
  Trash2,
  Underline as UnderlineIcon,
  Undo2,
  Upload,
  UserPlus,
} from "lucide-react";
```

- [ ] **Step 2: Replace the entire `EditorToolbar` function**

Find `function EditorToolbar({` and replace everything from that line through the closing `}` of the function with:

```tsx
function EditorToolbar({
  editor,
  onImageUpload,
}: {
  editor: ReturnType<typeof useEditor>;
  onImageUpload: (file: File | null) => Promise<void>;
}) {
  if (!editor) return null;

  const currentFontSize = (editor.getAttributes("textStyle").fontSize as string | undefined) ?? "15px";

  function setFontSize(size: string) {
    editor.chain().focus().setMark("textStyle", { fontSize: size === "15px" ? null : size }).run();
  }

  return (
    <div className="toolbar">
      {/* Font size */}
      <select
        className="toolbarSelect"
        title="字号"
        value={currentFontSize}
        onChange={(e) => setFontSize(e.target.value)}
      >
        {["12px", "14px", "15px", "16px", "18px", "20px", "24px"].map((s) => (
          <option key={s} value={s}>
            {s.replace("px", "")}px
          </option>
        ))}
      </select>

      <span className="toolbarSep" />

      {/* Text color */}
      <label className="toolbarColorBtn" title="字体颜色">
        <Baseline size={14} />
        <span
          className="toolbarColorBar"
          style={{ background: (editor.getAttributes("textStyle").color as string | undefined) ?? "#1a1a1a" }}
        />
        <input
          type="color"
          defaultValue="#1a1a1a"
          onChange={(e) => editor.chain().focus().setColor(e.target.value).run()}
        />
      </label>

      {/* Highlight */}
      <label className="toolbarColorBtn" title="高亮背景">
        <Highlighter size={14} />
        <span
          className="toolbarColorBar"
          style={{ background: (editor.getAttributes("highlight").color as string | undefined) ?? "#ffd166" }}
        />
        <input
          type="color"
          defaultValue="#ffd166"
          onChange={(e) => editor.chain().focus().setHighlight({ color: e.target.value }).run()}
        />
      </label>

      <span className="toolbarSep" />

      {/* Bold / Italic / Underline / Strikethrough */}
      <button className={editor.isActive("bold") ? "active" : ""} title="加粗" type="button" onClick={() => editor.chain().focus().toggleBold().run()}>
        <Bold size={16} />
      </button>
      <button className={editor.isActive("italic") ? "active" : ""} title="斜体" type="button" onClick={() => editor.chain().focus().toggleItalic().run()}>
        <Italic size={16} />
      </button>
      <button className={editor.isActive("underline") ? "active" : ""} title="下划线" type="button" onClick={() => editor.chain().focus().toggleUnderline().run()}>
        <UnderlineIcon size={16} />
      </button>
      <button className={editor.isActive("strike") ? "active" : ""} title="删除线" type="button" onClick={() => editor.chain().focus().toggleStrike().run()}>
        <Strikethrough size={16} />
      </button>

      <span className="toolbarSep" />

      {/* Alignment */}
      <button className={editor.isActive({ textAlign: "left" }) ? "active" : ""} title="左对齐" type="button" onClick={() => editor.chain().focus().setTextAlign("left").run()}>
        <AlignLeft size={16} />
      </button>
      <button className={editor.isActive({ textAlign: "center" }) ? "active" : ""} title="居中" type="button" onClick={() => editor.chain().focus().setTextAlign("center").run()}>
        <AlignCenter size={16} />
      </button>
      <button className={editor.isActive({ textAlign: "right" }) ? "active" : ""} title="右对齐" type="button" onClick={() => editor.chain().focus().setTextAlign("right").run()}>
        <AlignRight size={16} />
      </button>

      <span className="toolbarSep" />

      {/* Undo / Redo / Clear format */}
      <button title="撤销" type="button" disabled={!editor.can().undo()} onClick={() => editor.chain().focus().undo().run()}>
        <Undo2 size={16} />
      </button>
      <button title="重做" type="button" disabled={!editor.can().redo()} onClick={() => editor.chain().focus().redo().run()}>
        <Redo2 size={16} />
      </button>
      <button title="清除格式" type="button" onClick={() => editor.chain().focus().unsetAllMarks().clearNodes().run()}>
        <Eraser size={16} />
      </button>

      <span className="toolbarSep" />

      {/* Structural buttons */}
      <button className={editor.isActive("heading", { level: 1 }) ? "active" : ""} title="一级标题" type="button" onClick={() => editor.chain().focus().toggleHeading({ level: 1 }).run()}>
        <Heading1 size={16} />
      </button>
      <button className={editor.isActive("heading", { level: 2 }) ? "active" : ""} title="二级标题" type="button" onClick={() => editor.chain().focus().toggleHeading({ level: 2 }).run()}>
        <Heading2 size={16} />
      </button>
      <button className={editor.isActive("bulletList") ? "active" : ""} title="无序列表" type="button" onClick={() => editor.chain().focus().toggleBulletList().run()}>
        <List size={16} />
      </button>
      <button className={editor.isActive("orderedList") ? "active" : ""} title="有序列表" type="button" onClick={() => editor.chain().focus().toggleOrderedList().run()}>
        <ListOrdered size={16} />
      </button>
      <button className={editor.isActive("blockquote") ? "active" : ""} title="引用" type="button" onClick={() => editor.chain().focus().toggleBlockquote().run()}>
        <Quote size={16} />
      </button>
      <button
        title="链接"
        type="button"
        onClick={() => {
          const url = window.prompt("链接地址");
          if (url) editor.chain().focus().setLink({ href: url }).run();
        }}
      >
        <LinkIcon size={16} />
      </button>
      <label className="toolbarFile" title="插入图片">
        <ImagePlus size={16} />
        <input
          accept="image/*"
          type="file"
          onChange={(event) => {
            void onImageUpload(event.target.files?.[0] ?? null);
            event.currentTarget.value = "";
          }}
        />
      </label>
    </div>
  );
}
```

- [ ] **Step 3: TypeScript check**

```powershell
pnpm --filter @geo/web typecheck
```

Expected: no errors. Common issues:
- `toggleUnderline` not found → confirm `Underline` is in the `extensions` array from Task 2
- `setHighlight` type error → confirm `Highlight.configure({ multicolor: true })` is registered
- `can().undo()` returns wrong type → cast as `boolean` if needed: `Boolean(editor.can().undo())`

- [ ] **Step 4: Commit**

```bash
git add web/src/main.tsx
git commit -m "feat: rewrite EditorToolbar with font, color, align, undo, resize controls"
```

---

### Task 4: Add CSS for new toolbar controls and image resize handle

**Files:**
- Modify: `web/src/styles.css`

- [ ] **Step 1: Add new rules after `.editorSurface img` (~line 615)**

Find this line in `styles.css`:
```css
.editorSurface img { max-width: 100%; border-radius: var(--r); }
```

Add immediately after it:

```css
/* ── toolbar new controls ── */
.toolbarSep {
  width: 1px;
  height: 20px;
  background: var(--hair);
  margin: 0 2px;
  align-self: center;
  flex-shrink: 0;
}

.toolbarSelect {
  height: 28px;
  border: 1px solid var(--hair);
  border-radius: var(--r-sm);
  padding: 0 6px;
  font-size: 12px;
  background: var(--paper);
  color: var(--fg);
  cursor: pointer;
  align-self: center;
}

.toolbarColorBtn {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  width: 34px;
  height: 34px;
  border-radius: var(--r-sm);
  color: var(--fg-2);
  border: 1px solid transparent;
  cursor: pointer;
  transition: all .12s;
}
.toolbarColorBtn:hover {
  background: var(--paper);
  border-color: var(--hair);
  color: var(--accent);
}
.toolbarColorBar {
  display: block;
  width: 16px;
  height: 3px;
  border-radius: 1px;
}
.toolbarColorBtn input[type="color"] {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  opacity: 0;
  cursor: pointer;
  border: none;
  padding: 0;
}

.toolbar button:disabled,
.toolbar button:disabled:hover {
  opacity: 0.3;
  cursor: not-allowed;
  background: transparent;
  border-color: transparent;
  color: var(--fg-2);
}

/* ── image resize handle ── */
.imgResizeHandle {
  position: absolute;
  right: -4px;
  top: 50%;
  transform: translateY(-50%);
  width: 8px;
  height: 32px;
  background: #3b82f6;
  border-radius: 4px;
  cursor: ew-resize;
  z-index: 10;
}
```

- [ ] **Step 2: TypeScript + build check**

```powershell
pnpm --filter @geo/web build
```

Expected: builds successfully with the chunk-size warning (that's pre-existing and fine). No TypeScript errors.

- [ ] **Step 3: Commit**

```bash
git add web/src/styles.css
git commit -m "feat: add CSS for toolbar controls and image resize handle"
```

---

### Task 5: Smoke test in the browser

**Files:** none — manual verification only.

- [ ] **Step 1: Start dev servers**

```powershell
# Terminal 1 — backend
conda activate geo_xzpt
uvicorn server.app.main:app --reload --host 127.0.0.1 --port 8000

# Terminal 2 — frontend
pnpm --filter @geo/web dev
```

Open `http://localhost:5173`.

- [ ] **Step 2: Verify font size**

1. Type some text in the editor body.
2. Select the text.
3. Choose `18px` from the font-size dropdown.
4. Confirm the selected text renders larger.
5. Save the article and reopen it — the font size should persist.

- [ ] **Step 3: Verify text color and highlight**

1. Select text.
2. Click the `Baseline` (字色) button — system color picker opens.
3. Pick a color. Text turns that color.
4. Click the `Highlighter` (高亮) button — system color picker opens.
5. Pick a color. Text gets background highlight.

- [ ] **Step 4: Verify underline, strikethrough, alignment, undo/redo**

1. Select text → click U → underline appears.
2. Select text → click S → strikethrough appears.
3. Click a paragraph → click center align → paragraph centers.
4. Type something → click Undo → typing reverses.
5. Click Redo → typing comes back.
6. Select formatted text → click Eraser → all formatting removed.

- [ ] **Step 5: Verify image resize**

1. Upload an image via the image button.
2. Click the image in the editor — a blue handle appears on the right edge.
3. Drag the handle left/right — image width changes.
4. Save the article and reopen — width is preserved.

- [ ] **Step 6: Final commit (if any touch-up fixes were needed)**

```bash
git add web/src/main.tsx web/src/styles.css
git commit -m "fix: editor formatting smoke test adjustments"
```

---

### Task 6: Production build

- [ ] **Step 1: Build frontend**

```powershell
pnpm --filter @geo/web build
```

Expected: `dist/index.html` and assets generated. Chunk-size warning is pre-existing and acceptable.

- [ ] **Step 2: Build exe**

```powershell
conda activate geo_xzpt
pyinstaller geo.spec --noconfirm
```

Expected: `dist/GeoCollab.exe` produced successfully.

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "build: production build with editor formatting enhancement"
```
