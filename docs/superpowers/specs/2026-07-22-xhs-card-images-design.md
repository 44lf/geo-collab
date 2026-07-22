# 小红书卡片配图（原文图 + 三层兜底 + 限高）设计

> 日期：2026-07-22　状态：待实现　类型：后端(新 MCP 工具) + 渲染器 + skill
> 依赖：`xhs_cards` 渲染模块 + `image_library` 搜图/rehost（均在 main）

## 背景与目标

当前生成的小红书卡片是纯文案、文字多、卡片下方大片空白（截图），没用上原文的配图。给卡片文案下方加上相关配图，体验更好。做成 **skill 里的 opt-in 选项**（选了才配）。配图来源走**三层兜底链**保证「选了尽量有图」，且文字/图顺序块级排列、天然互不遮挡，图片**限高**保持竖版。

## 关键决策（已确认）

- **触发**：xhs-note-creator 加「配图」选项（问用户）。关=现状纯文案；开=走配图。
- **来源三层兜底**（主对话每张卡）：① 原文 body 图 → ② 游戏库（`list_stock_images`）→ ③ 新工具 `search_web_image(keyword)`（库外联网兜底）。取到就 embed，三层都空才该卡留空。
- **新工具 `search_web_image`**：复用现有千帆/百度搜图取一张 → `store_image_bytes` rehost MinIO → 返图 URL（不插文章）。`MCP_TOOLS_COUNT` 38→39。
- **渲染**：卡片 HTML 相对 `/api/…` 图 src 重写成绝对内网地址（`GEO_INTERNAL_URL` 默认 `http://127.0.0.1:8000`，plan 里查实际 src 定「重写 or data-URI 内联」）；`.card-content img` 加 `max-height`(~卡片高 45%)+`object-fit: contain`。

## 现状事实（代码）

- 渲染：`xhs_cards/render.py:generate_card_html(content, theme, page_number, width, height)` → `convert_markdown_to_html(content)`（`![](url)`→`<img>`）→ 包进 `.card-content .card-content-scale`。各主题 CSS 已有 `.card-content img`（max-width 100%、height auto、圆角、居中、margin 35px）→ markdown 图天然渲染在文字下方。卡片容器 `min-height:{height}` + `overflow:hidden`，内容撑高不裁剪。
- rehost 组件：`image_library/service.py:store_image_bytes(db, category, data, content_type, source_url=…)`（bytes→MinIO→StockImage，含 source_url 去重）+ `get_or_create_companion_category(...)`。千帆/百度**关键词搜图**在 `articles/ai_format.py` 的 web_fallback 路径（plan 定位复用）。
- StockImage 公开 URL：`/api/stock-images/{id}/file`（`/api/stock-images/*` 有意公开）。
- MCP：工具在 `server/mcp/tools/*.py`，端点带 `Depends(require_mcp_token)`；`MCP_TOOLS_COUNT`（connect_router）当前 38。
- 渲染进程：compose 在 web 进程后台线程，Playwright headless chromium 同容器；能走 loopback 访问 web 自身的公开端点。
- xhs skill：`.claude/skills/xhs-note-creator/SKILL.md`。

## 架构与改动

### 后端：新 `search_web_image` 工具（第三层兜底）

**端点**（`server/app/modules/image_library/router.py` 或 xhs 相关 MCP sub-router，带 `require_mcp_token`）：
- `POST /api/mcp/search-web-image` body `{keyword: str}` → 复用 `ai_format` 里的千帆/百度搜图取一张图字节 → `get_or_create_companion_category(db, name="小红书web兜底")` → `store_image_bytes(...)` → 返 `{"ok":true,"data":{"url": "/api/stock-images/{id}/file", "stock_image_id": id}, "error":null}`。
- 无 `GEO_BAIDU_API_KEY` / 搜不到 / 失败：返 `{"ok":true,"data":{"url": null}, "error":null}`（best-effort，主对话据此跳到留空）。未捕获异常走 `mcp_exception_response`。
- 复用点：把 `ai_format.py` 里「keyword → 一张图字节(+content_type/source_url)」的搜图逻辑抽成可复用函数（若已是独立函数直接调；否则小重构抽出）。

**MCP 工具**（`server/mcp/tools/`）：
- `search_web_image(keyword: str) -> {ok,data:{url,stock_image_id},error}`。docstring：库外游戏/主题联网兜底取一张图，返 URL 供 embed 进卡片；缺 key/搜不到返 url=null。
- `MCP_TOOLS_COUNT` 38→39；CLAUDE.md「Tool 三组」action 组补 search_web_image。

### 渲染器：图可达 + 限高

- **图可达（`render.py`）**：`generate_card_html`/`generate_cover_html` 里，`convert_markdown_to_html` 后对 HTML 做 img-src 规范化：相对 `/api/…` → `{INTERNAL_BASE}/api/…`（`INTERNAL_BASE = os.environ.get("GEO_INTERNAL_URL", "http://127.0.0.1:8000")`）。plan 阶段先查实际图 src（stock-images 公开→重写即可；若有鉴权 asset 图→改抓字节内联 data URI 更稳，二选一在 plan 定）。纯函数 `rewrite_img_src(html, base)` 便于测试。
- **限高（各主题 CSS `.card-content img`）**：加 `max-height: <约卡片高 45%>px`（或用 vh/固定值）+ `object-fit: contain`。8 个主题 CSS 各改一处（或在 `generate_card_html` 注入的公共 `<style>` 里统一加一条 `.card-content img { max-height:…; object-fit:contain; }` 覆盖，避免改 8 文件——**优先注入公共 style 覆盖**）。

### skill：配图选项 + 兜底链引导

`xhs-note-creator/SKILL.md`：
- 新增一步「是否配图」（问用户，默认可关）。
- 配图开时的兜底链引导：每张卡先用原文对应 body 图；没有→`list_stock_categories`/`list_stock_images` 按卡片游戏/主题搜；还没有→`search_web_image(该卡关键词)`；都空则该卡不配。取到的 URL embed 成 `![](url)` 放该卡文案末尾（渲染即在文字下方）。

## 测试
- 后端：`rewrite_img_src` 纯函数（相对→绝对、绝对不动、非 /api 不动）；`search-web-image` 端点（mock 搜图返字节→返 url；无 key/搜不到→url null；MCP token 守卫）。对标 `test_xhs_*` / `build_test_app`。
- MCP 计数测试同步到 39。
- 渲染真图人工验收（dev 容器）：配图卡片图在文案下方、限高、不遮挡。
- 前端无改动。

## 非目标（YAGNI）
- 不自动配图（必须 skill 选项开）。不做图裁剪/编辑/多图每卡。不改 compose API（图 embed 在 render-markdown）。不动封面（封面已有 emoji，本期只正文卡配图；如需封面配图后续）。

## 交付 & 部署
后端新工具 + 渲染器 + skill。`release-*`（server + 若 CLAUDE.md/skill 算）。需 `GEO_BAIDU_API_KEY`（第三层才用；缺则退化为两层）。按新纪律先合 main + 解决冲突再发版。
