# xhs-game-hook — 游戏图蹭大IP小红书图文 设计

> 日期：2026-07-23　状态：待实现　类型：新 skill + 渲染器扩展 + 小改 MCP 工具
> 依赖：`game_library` MCP（`query_games_by_tags` 返 `GameCard`）、`xhs_cards` 渲染 + compose 流水线、`save-from-mcp`（`source_article_id` 已可选）

## 背景与目标

小红书上一类「游戏推荐图文」比较容易起量（参考案例 575赞/262藏/70评）：**封面是主推游戏自己的一张游戏图**（原截图或"游戏图铺底+标题叠加"的合成封面），**正文蹭一个同类知名大 IP 游戏**（案例蹭"梦想城镇"，靠"同为 Playrix 出品"的真实关系 + "冷门/宝藏/开朝元老"的发现感），tags 堆大 IP 名 + 品类 + 受众（`#梦想城镇 #部落冲突 #模拟游戏 #适合女生玩的游戏 #宝藏游戏推荐`）。

目标：新建独立 skill `xhs-game-hook`，让主对话从 GEO 游戏库取材，产出这种「**单图 + 蹭大IP文案**」的小红书图文，落未审核库、带「小红书图文」徽标、**不自动发布**。与现有 `xhs-note-creator`（已审文章→多卡）职责分离、互不干扰。

## 关键决策（已与用户确认）

1. **结构**：单图图文（一张封面图 + 蹭大IP文案 + tags），对齐案例；**不做**多图轮播 / 正文卡。
2. **封面**：两者可选，**默认合成**（游戏截图铺底 + 标题文字叠加），可切纯原截图。
3. **蹭大IP来源**：Claude 从品类知识挑同类知名大 IP + **WebSearch 核实蹭点真实性**，用户可指定覆盖。
4. **打包**：新建独立 skill `xhs-game-hook`（不改 `xhs-note-creator`）。

## 现状事实（代码，已核对）

- `query_games_by_tags`（MCP，`/api/mcp/game-library/query`）返回 `list[GameCard]`，`GameCard` 含 `name / score / tags / description / stock_category_id / icon_url / **screenshot_urls: list[str]** / highlight_comments / related_hotspots`。→ 一次调用即可拿到目标游戏 + 真实游戏截图 URL + 同类游戏池。
- `list_game_tags` 先看有哪些真实标签再 query（不要臆造标签）。
- `search_web_image(keyword)`：库外/无图时联网取一张横版图 rehost 站内、返 `/api/stock-images/{id}/file`（第三层兜底封面）。
- 渲染：`xhs_cards/render.py`
  - `parse_markdown_string(md)` 读 YAML frontmatter 到 `metadata`（已接受**任意** key）+ body。
  - `generate_cover_html(metadata, theme, width, height)` 现在渲染「渐变底 + emoji + title + subtitle」文字封面。
  - `render_markdown_to_card_bytes(md, ...)`：先渲封面，再按 `---` 切正文卡；**body 为空 → cards=[]，只出封面**（单图图文天然支持）。
  - compose 流水线：`compose_xhs_cards(render_markdown, theme, mode, source_article_id?) → job_id` → `get_xhs_status(job_id) → {cover_url, card_urls[]}`。
- 落库：`save_xhs_note(source_article_id, prompt_template_id, title, markdown_content, model_label?)` → POST `/api/articles/save-from-mcp`，置 `content_type="xhs_image_text"`、`review_status="pending"`。**后端 `save-from-mcp` 的 `source_article_id` 本就 `int | None = None`（可选）**；仅 `save_xhs_note` 工具签名把它写成必填 `int`。
- 标题硬门禁：save-from-mcp 对 `content_type="xhs_image_text"` 已强制标题 `≤20` 字（`XHS_TITLE_MAX_CHARS`），超限 400。→ 本 skill 的标题同样受此约束。

## 架构与改动

### A. 渲染器：合成封面（图片铺底 + 标题叠加）

**`render.py:generate_cover_html`** 增加对 `metadata.cover_image` 的支持：

- `cover_image` 为空（现状）→ 维持「渐变底 + emoji + title + subtitle」不变。
- `cover_image` 有值 → 渲染**合成封面**：
  - 背景：`<img>` 铺满封面画布，`object-fit: cover`（等价卡片配图满宽逻辑）；src 走 `rewrite_img_src`（`/api/…` → 内网绝对地址，让 Playwright file:// 能拉）。
  - 底部**渐变蒙层**（`linear-gradient(transparent → rgba(0,0,0,.55))`）保证文字可读。
  - 前景：`title` 白色加粗大字（封面钩子，可与正文标题不同）+ 可选 `subtitle` 白色小字，压在底部蒙层上。
  - 复用现有标题按长度自适应字号逻辑。
- 纯函数可测：拆一个 `generate_cover_html` 分支 or 内部 helper `_cover_image_html(metadata, ...)`；单测断言含 `object-fit: cover` 的背景 img + `rewrite_img_src` 后的绝对 src + 渐变蒙层 + 标题；`cover_image` 缺省时不含背景 img（回落文字封面）。

**compose 透传**：`render_markdown_to_card_bytes` → `parse_markdown_string` 已把 `cover_image` 收进 `metadata`，`generate_cover_html(metadata, ...)` 直接用，无需改 compose 签名。单图 = frontmatter-only render-markdown（无 body、无 `---`）→ `cards=[]`，只回 `cover_url`。

**触发**：skill 组 render-markdown：
```markdown
---
cover_image: "/api/stock-images/123/file"   # 目标游戏的一张截图，或 search_web_image 兜底图
title: "女生爱玩的宝藏游戏"                    # 封面钩子（≤ 一行）
subtitle: "梦想城镇同款 · 更冷门"              # 可选
---
```
（无 `---` 正文 → 只出一张合成封面。）纯原图模式则跳过 compose，直接用 `screenshot_url` 当图。

### B. `save_xhs_note` 工具：`source_article_id` 改可选

`server/mcp/tools/xhs.py:save_xhs_note`：`source_article_id: int | None = None`；仅当非空才放进 body（后端已可选）。docstring 说明「游戏来源无源文章时可省」。其余不变（仍置 `content_type="xhs_image_text"`）。**后端无改动**。

### C. 新 skill `.claude/skills/xhs-game-hook/SKILL.md`

流程（主对话零配置、单条产出、不循环、不自动发布）：

1. **定目标（主推）游戏**：用户点名游戏 or 给品类/标签 → `list_game_tags` 选真实标签 → `query_games_by_tags(relevant_tags=…)` 取 `GameCard`（拿 `name/tags/description/screenshot_urls/score`）。库里没有或 `screenshot_urls` 空 → `search_web_image(游戏名)` 兜底封面图。
2. **选蹭的大IP + 核实**：Claude 从该品类知识挑一个**同类知名大 IP**；**WebSearch 核实蹭点**（同公司 / 同发行 / 同玩法 / "像X"）。核实通过才写强关系；核实不了 → 回落恒真软框架（"同为城建模拟类"、"XX玩腻了可以试试")，**绝不编造公司/关系**。用户可指定覆盖蹭谁。
3. **写蹭大IP文案**：标题（蹭大 IP，**≤20 字**硬门禁）+ 正文（对比 + 发现感：冷门/宝藏/同公司/节奏差异）+ 5-10 个 tags（大 IP 名 + 品类 + `#适合女生玩的游戏 #宝藏游戏推荐` 等）。文案是可直接发布的成品，不带脚手架标签（沿用 xhs-note-creator 约定）。
4. **出封面（问用户：合成 / 原图，默认合成）**：
   - 合成 → 从 `screenshot_urls` 选一张作 `cover_image` + `title`（封面钩子）→ `compose_xhs_cards`（frontmatter-only）→ 轮询 `get_xhs_status` → 拿 `cover_url`。
   - 原图 → 直接用某个 `screenshot_url`（或 search_web_image 兜底 URL）当封面图，不渲染。
5. **落库**：`markdown_content` = `![](封面图 url)` + 正文文案 + tags；`save_xhs_note(prompt_template_id=<步骤3模板>, title=<小红书标题>, markdown_content=…)`（**不传 source_article_id**）。可带 `selected_games`（目标游戏 game_id）回写用量。→ pending、「小红书图文」徽标。

语言约定、主题问用户、不自动发布等沿用 xhs-note-creator 风格。

### D. 提示词模板（数据，非代码）

建一个「游戏蹭大IP · 发现感」提示词模板（`platform=xiaohongshu`，scope=generation），运营在 Web「提示词管理」创建；skill 步骤 3 让用户选它（或任一 xiaohongshu 模板）。设计稿附推荐模板文案要点：蹭点前置、发现感（冷门/宝藏/开朝元老）、对比同类大 IP、tags 堆 IP+品类+受众、标题≤20。**不写死进代码**。

## 前端

无改动。单图 + `content_type="xhs_image_text"` 复用现有内容列表徽标 + `XhsNotePreview`（1 图轮播 + 可编辑文案 + 灯箱）。

## 测试

- 后端 render：`generate_cover_html` 合成分支纯函数单测（含背景 img + `object-fit: cover` + `rewrite_img_src` 绝对 src + 渐变蒙层 + 标题；`cover_image` 缺省回落文字封面不含背景 img）。真图人工验收（dev 容器渲染 PNG，铺底+文字可读）。
- `save_xhs_note` `source_article_id` 可选：不传时 body 不含该键、落库成功（对标 `test_save_from_mcp_xhs_no_question`）。
- MCP 工具计数：`save_xhs_note` 仅改签名不新增工具 → `MCP_TOOLS_COUNT` **不变（39）**；若最终决定拆独立 compose 工具才 +1（当前方案①不拆）。
- 无前端测试（typecheck+build 门禁，本期前端零改动）。

## 交付 & 部署

后端 render.py + save_xhs_note 工具 + 新 skill + 设计稿。`server-*`（render.py/工具在 server/；SKILL.md 是 skill 分发物）。需 `GEO_BAIDU_API_KEY`（仅原图兜底/无库图时的 search_web_image 才用；缺则退化为只用库内截图）。按纪律先合 main + 解决冲突再发版。

## 非目标（YAGNI）

不自动发布；不做多图轮播 / 正文卡（本类型即单图）；不改 `xhs-note-creator`；不做封面多版 A/B；蹭 IP 清单不入库（靠 Claude 知识 + WebSearch 核实）；不新增 compose 工具（复用 `compose_xhs_cards` + `cover_image` frontmatter）。
