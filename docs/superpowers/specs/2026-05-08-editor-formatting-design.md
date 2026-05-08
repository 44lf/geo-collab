# 编辑器格式增强设计文档

**日期：** 2026-05-08  
**范围：** Tiptap 富文本编辑器 — 字体控制、文字排版对齐、图片拖拽缩放

---

## 背景

当前编辑器（`web/src/main.tsx`）使用 Tiptap v3 + StarterKit，只有基础格式（加粗、斜体、标题、列表、引用、链接、图片插入）。用户需要类似 Word 的字体调整能力和图片缩放能力。

---

## 功能范围

### 1. 字体控制

| 功能 | 扩展包 |
|------|--------|
| 字号（12/14/15/16/18/20/24px） | `@tiptap/extension-font-size` + `@tiptap/extension-text-style` |
| 字体颜色 | `@tiptap/extension-color` |
| 文字高亮背景色 | `@tiptap/extension-highlight` |
| 下划线 | `@tiptap/extension-underline` |

删除线已由 StarterKit 内置（`Strike`），只需补工具栏按钮。

### 2. 段落对齐

| 功能 | 扩展包 |
|------|--------|
| 左对齐 / 居中 / 右对齐 | `@tiptap/extension-text-align` |

`TextAlign` 作用于块级节点（Paragraph、Heading），通过 `textAlign` 属性渲染为 `text-align: center` 等内联样式。

### 3. 图片拖拽缩放

用社区包 `tiptap-extension-resize-image` 替换现有 `CustomImage`。

- 点击图片后四角出现拖拽手柄
- 宽度以百分比存储在 Tiptap JSON（`width: "60%"`）
- 渲染时输出 `<img style="width:60%">` 到 HTML
- 保留现有 `assetId` 自定义属性，通过 `.extend({ addAttributes() })` 继承

---

## 工具栏变更

在现有工具栏的图标按钮组**前面**插入以下控件：

```
[ 字号▾ ] | [ A字色 ] [ A高亮 ] | [ B ] [ I ] [ U ] [ S ] | [ ≡左 ] [ ≡中 ] [ ≡右 ] | ...原有按钮...
```

- **字号**：`<select>` 下拉，选项 12/14/15/16/18/20/24，默认 15px
- **字体颜色**：图标按钮 + 隐藏的 `<input type="color">`，点击触发系统颜色选择器，按钮底部色条实时显示当前颜色
- **高亮色**：同上，预置几个常用颜色（黄/绿/粉/无）
- **下划线 / 删除线**：图标按钮，toggle 行为
- **对齐**：三个图标按钮（Left / Center / Right），互斥激活状态

---

## 数据模型兼容性

- 旧文章 JSON 不受影响，打开时新属性缺失即按默认值渲染
- 新属性只在用户主动操作时写入 JSON
- `content_html` 输出包含对应内联样式，头条号发布时原样使用
- `plain_text` / `word_count` 不受影响

---

## 文件改动范围

| 文件 | 改动 |
|------|------|
| `web/package.json` | 新增 7 个 `@tiptap/*` 和 1 个社区包依赖 |
| `web/src/main.tsx` | `useEditor` 扩展列表、`EditorToolbar` 组件、`CustomImage` 替换 |
| `web/src/styles.css` | 图片 resize 手柄样式、颜色选择器按钮样式 |

不涉及后端改动。

---

## 不在此次范围内

- 字体家族（font-family）选择
- 行间距调整
- 表格、代码块等结构扩展
- 自动保存、版本历史
