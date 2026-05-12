# Bug 修复方案

## Bug 1：文字格式缩进问题

### 现象
头条发布后文字格式丢失：加粗、斜体、下划线、标题层级、引用、列表缩进全部消失，所有文字被拍平成普通正文。

### 根因

**`server/app/services/toutiao_publisher.py:287-320` — Tiptap 富文本→纯文本降级过激进**

`_append_tiptap_segments()` 在提取正文内容时：

| 问题 | 位置 | 说明 |
|------|------|------|
| 丢弃 marks | L296-299 | 只读 `node.get("text")`，完全忽略 `marks` 数组（bold/italic/underline/link 等均丢失） |
| 所有 block 类型同等对待 | L319-320 | heading(1-6)、blockquote、paragraph 全部打平成 `"text\n"`，丢失语义 |
| 嵌套列表无缩进 | L313-316 | 不管嵌套多深，前缀永远是 `"1. "` / `"- "`，且 orderedList 编号每层重置 |
| 段落边界被压缩 | L322-337 | `_compact_segments()` 合并所有相邻 text，段落结构只剩内嵌 `\n` |

最终 `_fill_body()` 只能调 `page.keyboard.type(text)` 一个字符一个字符地敲进头条编辑器，没有任何格式快捷键或命令下发。

### 修复方案

> 已与产品确认（XW-1937）：当前阶段**不要求**富文本格式（加粗、标题层级等）。下阶段由头条 Web 端富文本工具栏配合格式命令补齐。

当前紧迫的"缩进"问题实质是**段落间信息丢失**和**列表层级退化**。按优先级分步修：

1. **段落分隔保真**（P0） — `_append_tiptap_segments` 中 `"heading"`、`"blockquote"`、`"listItem"` 各类型在句尾加 `\n` 前，先用头条编辑器快捷键设样式：
   - heading(1-6) → `page.keyboard.press("Control+Alt+{level}")` 设标题层级
   - 或先用 `page.keyboard.press("Control+")` 组合键尝试联动对端
2. **嵌套缩进保序**（P1） — 递归时记录 `depth`，`orderedList`/`bulletList` 前缀按深度补空格 `"\t" * depth`；编号改用计数器而非 `index + 1`
3. **远期补齐 marks**（P2） — `_insert_body_text` 改为按 segment 粒度下发，marks 翻译成头条工具栏快捷键

---

## Bug 2：正文图片位置偏移（第 2 张起）

### 现象
第一张图片位置正确，第二张起图片插入位置逐张向上偏移，最终混乱。

### 根因

**`server/app/services/toutiao_publisher.py:256-269` — 焦点只在循环前设置一次，图片上传流程造成焦点逃逸**

```
_fill_body() 只调了一次 _focus_body_editor()
  → 第 1 张图：editor 有焦点 ✓
  → _open_body_image_drawer() 点工具栏按钮 → 焦点移到按钮上
  → _upload_body_image_in_drawer() set_input_files → OS 文件对话框
  → _confirm_body_image_drawer() 点"确定" → 焦点留在按钮
  → 后续 text typing + image insertion 全落到错误位置 ✗
  → 第 2、3...张图：位置持续偏移
```

具体调用链：

| 步骤 | 方法 | 焦点后果 |
|------|------|---------|
| 1 | `_open_body_image_drawer:399-401` | `button.click()` → 焦点移到工具栏按钮 |
| 2 | `_upload_body_image_in_drawer:415` | `set_input_files()` → OS 文件选择器打开，焦点离开浏览器 |
| 3 | `_confirm_body_image_drawer:432` | `candidate.click()` → 焦点留在"确定"按钮 |
| 4 | `_wait_body_image_inserted:443-447` | 只用 JS eval 检测，不恢复焦点 |
| 5 | L269 `_insert_body_text("\n")` → 敲到错误位置 |
| 6 | 回到循环 L264-269 → 下一张图错上加错 |

### 修复方案

**每张图片上传完成后立即重新对焦**（改动最小、风险最低的方案）：

在 `_fill_body()` 的循环内，图片插入后加一次 `_focus_body_editor()`：

```python
def _fill_body(self, page: Any, article: Article) -> None:
    segments = self._body_segments(article)
    if not segments:
        raise ToutiaoPublishError("文章正文为空")

    self._focus_body_editor(page)
    for segment in segments:
        if segment.kind == "text":
            self._insert_body_text(page, segment.text)
        elif segment.kind == "image":
            asset = self._body_asset_for_segment(article, segment)
            self._paste_body_image(page, asset)
            self._focus_body_editor(page)          # ← 只加这一行
            self._insert_body_text(page, "\n")
```

另外在 `_confirm_body_image_drawer` 返回前（L433）的 `wait_for_timeout(1000)` 之后，也可主动 `page.keyboard.press("Escape")` 确保抽屉关闭干净。

### 验证方法

1. 构造一本含 3+ 张正文图片的文章
2. 执行发布，观察每张图在头条编辑器中的位置
3. 检查 `_body_image_count()` 前后一致，且光标停在正确的段落位置
4. 对第一张图 + 第二张图之间无文字的用例做边界覆盖

---

## Bug 3：正文过长时整页滚动，工具栏和左侧列表被拖走

### 现象
编辑文章时正文内容一长，整个页面滚动，顶部工具栏和左侧文章列表滚出视口，需要不断滚上滚下。

### 根因

**CSS 布局链缺少内部滚动容器——`section.workspace` 承担了全文滚动，但其子元素 `.editorWrap` 却设了 `overflow: hidden`**

布局链（`web/src/styles.css` + `ContentWorkspace.tsx`）：

```
.shell (height: 100vh; overflow: hidden)
  ├── .sidebar (overflow-y: auto)                 ← 左侧面板独立滚动 ✅
  └── .workspace (overflow-y: auto; min-height: 100vh)  ← 问题：撑高后全局滚动
        └── .workspaceInner (min-height: 100vh)
              ├── .topbar                         ← 工具栏，滚走了 ❌
              └── .contentGrid
                    ├── .listPane (min-height: 0) ← 文章列表，滚走了 ❌
                    └── .editorPane (无 min-height: 0)
                          └── .editorWrap (overflow: hidden) ← 问题：本该滚动的地方却 hidden
                                └── .editorSurface (无高度约束，无限生长)
```

关键问题：

| 元素 | 问题 | 后果 |
|------|------|------|
| `.workspace` | `min-height: 100vh; overflow-y: auto` | 正文撑高后整区滚动 |
| `.editorPane` | **缺少 `min-height: 0`** | CSS Grid 子项无法收缩，撑开网格行高 |
| `.editorWrap` | `overflow: hidden` 而非 `auto` | 编辑器内容溢出后不出现滚动条，溢出传给父级 |

### 修复方案

只改 CSS，不涉及 JS 逻辑。5 处修改：

```css
/* 1. workspace — 取消全局滚动 */
.workspace {
  height: 100%;
  overflow: hidden;
  min-height: unset;        /* 原 min-height: 100vh */
}

/* 2. workspaceInner — 不随内容撑高 */
.workspaceInner {
  height: 100%;
  min-height: 0;            /* 原 min-height: 100vh */
}

/* 3. contentGrid — 防止子项撑开网格 */
.contentGrid {
  overflow: hidden;
  min-height: 0;
}

/* 4. editorPane — 让 grid 子项可以收缩 */
.editorPane {
  display: flex;
  flex-direction: column;
  min-height: 0;            /* 关键：CSS Grid 收缩 */
}

/* 5. editorWrap — 变成内部滚动容器 */
.editorWrap {
  overflow-y: auto;         /* 原 overflow: hidden */
  flex: 1;
}
```

改完后的滚动行为：

```
┌───────────────────────────────────────────────┐
│ sidebar (固定) │ topbar (固定，始终可见)       │
│                ├───────────────────────────────┤
│                │ contentGrid (flex:1)          │
│                │ ┌─────────┬─────────────────┐ │
│                │ │listPane │ formRow/cover    │ │
│                │ │(自身    │ toolbar          │ │
│                │ │ 滚动)   │ ──────────────  │ │
│                │ │         │ editorWrap       │ │
│                │ │         │ (滚动条在这里！) │ │
│                │ │         │ ──────────────  │ │
│                │ │         │ char count       │ │
│                │ └─────────┴─────────────────┘ │
└───────────────────────────────────────────────┘
```

工具栏、左侧文章列表始终可见，只有正文框内出现竖直滚动条。

---

## Bug 4：新增文章时间与电脑/服务器时间不一致

### 现象
新创建的文章显示的时间与本地时间不符，通常差 8 小时（或对应时区偏移）。

### 根因

**`server/app/core/time.py:5` — `utcnow()` 生成 UTC 时间后剥离了时区信息，导致 JS 按本地时间解析**

数据流：

```
Server UTC 14:30
  ↓ utcnow() → .replace(tzinfo=None)
Naive datetime(2026, 5, 11, 14, 30)         ← 值是对的时间，但没了时区标记
  ↓ SQLite 存储 "2026-05-11 14:30:00"
  ↓ FastAPI jsonable_encoder
JSON "2026-05-11T14:30:00"                   ← 没有 Z，没有 +00:00
  ↓ new Date("2026-05-11T14:30:00") 按本地时间解析
Display: 14:30                               ← 实际 UTC 14:30，应在 UTC+8 显示 22:30
```

关键文件：

| 文件 | 行号 | 说明 |
|------|------|------|
| `server/app/core/time.py` | 5-6 | `utcnow()` 定义，**此处是根因** |
| `server/app/models/article.py` | 29-30 | `created_at` / `updated_at` 默认值 |
| `server/app/schemas/article.py` | 75-76 | Pydantic response 字段类型 |
| `server/app/main.py` | 31 | FastAPI 无 `json_encoders` 配置 |
| `web/src/components/ArticleListItem.tsx` | 27 | 前端显示 `new Date(...).toLocaleString()` |

### 修复方案

**推荐：在 FastAPI 序列化层加 `Z` 后缀 —— 一处改动，全部模型生效**

```python
# server/app/main.py:31
from datetime import datetime

app = FastAPI(
    title="Geo Collab API",
    version="0.1.0",
    json_encoders={
        datetime: lambda dt: dt.isoformat() + ("Z" if dt.tzinfo is None else "")
    },
)
```

原理：FastAPI 的 `jsonable_encoder` 会调用这个 lambda，naive datetime 自动带上 `Z`。输出变为：

```json
"created_at": "2026-05-11T14:30:00Z"
```

JS `new Date("2026-05-11T14:30:00Z")` 正确识别为 UTC，`toLocaleString()` 自动转本地时区。

**备选方案（前端补丁）**：不推荐，每个显示处都要改，容易漏：

```tsx
// web/src/components/ArticleListItem.tsx:27
new Date(article.updated_at + 'Z').toLocaleString()

// web/src/features/content/ContentWorkspace.tsx:754
new Date(article.updated_at + 'Z').toLocaleString()

// web/src/features/tasks/TasksWorkspace.tsx:380,408
new Date(task.created_at + 'Z').toLocaleString()
```

---

### 现象
第一张图片位置正确，第二张起图片插入位置逐张向上偏移，最终混乱。

### 根因

**`server/app/services/toutiao_publisher.py:256-269` — 焦点只在循环前设置一次，图片上传流程造成焦点逃逸**

```
_fill_body() 只调了一次 _focus_body_editor()
  → 第 1 张图：editor 有焦点 ✓
  → _open_body_image_drawer() 点工具栏按钮 → 焦点移到按钮上
  → _upload_body_image_in_drawer() set_input_files → OS 文件对话框
  → _confirm_body_image_drawer() 点"确定" → 焦点留在按钮
  → 后续 text typing + image insertion 全落到错误位置 ✗
  → 第 2、3...张图：位置持续偏移
```

具体调用链：

| 步骤 | 方法 | 焦点后果 |
|------|------|---------|
| 1 | `_open_body_image_drawer:399-401` | `button.click()` → 焦点移到工具栏按钮 |
| 2 | `_upload_body_image_in_drawer:415` | `set_input_files()` → OS 文件选择器打开，焦点离开浏览器 |
| 3 | `_confirm_body_image_drawer:432` | `candidate.click()` → 焦点留在"确定"按钮 |
| 4 | `_wait_body_image_inserted:443-447` | 只用 JS eval 检测，不恢复焦点 |
| 5 | L269 `_insert_body_text("\n")` → 敲到错误位置 |
| 6 | 回到循环 L264-269 → 下一张图错上加错 |

### 修复方案

**每张图片上传完成后立即重新对焦**（改动最小、风险最低的方案）：

在 `_fill_body()` 的循环内，图片插入后加一次 `_focus_body_editor()`：

```python
def _fill_body(self, page: Any, article: Article) -> None:
    segments = self._body_segments(article)
    if not segments:
        raise ToutiaoPublishError("文章正文为空")

    self._focus_body_editor(page)
    for segment in segments:
        if segment.kind == "text":
            self._insert_body_text(page, segment.text)
        elif segment.kind == "image":
            asset = self._body_asset_for_segment(article, segment)
            self._paste_body_image(page, asset)
            self._focus_body_editor(page)          # ← 只加这一行
            self._insert_body_text(page, "\n")
```

另外在 `_confirm_body_image_drawer` 返回前（L433）的 `wait_for_timeout(1000)` 之后，也可主动 `page.keyboard.press("Escape")` 确保抽屉关闭干净。

### 验证方法

1. 构造一本含 3+ 张正文图片的文章
2. 执行发布，观察每张图在头条编辑器中的位置
3. 检查 `_body_image_count()` 前后一致，且光标停在正确的段落位置
4. 对第一张图 + 第二张图之间无文字的用例做边界覆盖

---

## 附录：代码位置速查

| 文件 | 行号 | 问题 | Bug |
|------|------|------|-----|
| `server/app/services/toutiao_publisher.py` | 256-269 | `_fill_body()` 只对焦一次 | 2 |
| `server/app/services/toutiao_publisher.py` | 271-274 | `_insert_body_text()` 裸敲，无格式化命令 | 1 |
| `server/app/services/toutiao_publisher.py` | 287-320 | `_append_tiptap_segments()` 丢弃 marks 和 block 语义 | 1 |
| `server/app/services/toutiao_publisher.py` | 296-299 | marks 数组被忽略 | 1 |
| `server/app/services/toutiao_publisher.py` | 313-316 | 嵌套列表无缩进前缀 | 1 |
| `server/app/services/toutiao_publisher.py` | 319-320 | heading/blockquote/paragraph 全部打平 | 1 |
| `server/app/services/toutiao_publisher.py` | 322-337 | `_compact_segments()` 合并导致段落边界丢失 | 1 |
| `server/app/services/toutiao_publisher.py` | 390-440 | 图片上传流程中焦点逃逸 | 2 |
| `server/app/services/toutiao_publisher.py` | 442-448 | `_wait_body_image_inserted()` 不恢复焦点 | 2 |
| `web/src/styles.css` | workspace/contentGrid/editorPane/editorWrap 相关 | 布局链缺少内部滚动容器 | 3 |
| `server/app/core/time.py` | 5-6 | `utcnow()` 剥离时区信息 | 4 |
| `server/app/models/article.py` | 29-30 | created_at 默认值使用 naive datetime | 4 |
| `server/app/main.py` | 31 | FastAPI 缺少 `json_encoders` | 4 |
| `web/src/components/ArticleListItem.tsx` | 27 | 前端用 `new Date()` 解析无时区标记的 ISO 时间 | 4 |
| `web/src/features/content/ContentWorkspace.tsx` | 754 | 同上 | 4 |
| `web/src/features/tasks/TasksWorkspace.tsx` | 380, 408 | 同上 | 4 |
