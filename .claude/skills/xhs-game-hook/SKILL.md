---
name: xhs-game-hook
description: Use when turning a promoted game into a Xiaohongshu (Redbook) single-image
  note that piggybacks a famous same-genre big-IP game — 用户说「把某游戏做成蹭大IP的
  小红书图文/单图种草」时用。从 GEO 游戏库取目标游戏 + 截图，挑一个同类知名大IP并联网核实
  蹭点真实性，写蹭大IP文案，出合成/原图封面，单图落未审核库（带「小红书图文」徽标）。
  不自动发布到小红书。
---

# 小红书 · 游戏图蹭大IP 单图图文

你把**一款主推游戏**做成一份小红书**单图图文**：一张封面图（游戏截图合成封面 or 原截图）
+ 蹭一个**同类知名大IP游戏**的文案 + tags，落进未审核库等待人工审核。GEO 后端**不调任何
LLM**——取材、选IP、写文案、排封面都由你完成；GEO 只做确定性渲染 + 落库。

不要循环、不要碰其它内容、**不要自动发布到小红书**——只产这一篇。

**语言约定**：对用户输出的一切自然语言用简体中文；技术标识符（工具名、`job_id`/`url`/
`game_id`/`theme`）保留原文。

# 流程

1. **定目标（主推）游戏**：用户直接点名游戏，或给一个品类/玩法。
   - 先 `list_game_tags` 看有哪些**真实**标签（别臆造）。
   - `query_games_by_tags(relevant_tags=[…])` 取候选，拿到 `GameCard`：
     `name / tags / description / score / screenshot_urls / stock_category_id`。
   - 选定目标游戏后，它的 `screenshot_urls` 就是封面素材来源。
   - **兜底**：目标游戏不在库里 / `screenshot_urls` 为空 → `search_web_image(游戏名)`
     取一张横版图当封面素材（返回 `url` 为 null 说明搜不到，就如实告知用户、请其提供图）。

2. **选蹭的大IP + 联网核实（关键）**：
   - 从该品类的知识里挑一个**同类知名大IP游戏**当"蹭"对象（城建=梦想城镇/模拟城市，
     塔防=部落冲突，农场=卡通农场，消除+装修=梦幻花园…）。用户可指定覆盖蹭谁。
   - **必须 WebSearch 核实蹭点真实性**：确认"同公司/同发行商/同玩法/相似度"等你打算写进
     文案的关系。核实通过 → 才写强关系（如"XX同公司的游戏"）。**核实不了 → 回落更软但恒真
     的框架**（"同为城建模拟类"、"XX玩腻了可以试试这个"），**绝不编造公司/发行/独家等关系**。

3. **写蹭大IP文案**（可直接发布的成品，不带脚手架标签）：
   - **标题 ≤20 字（硬门禁，含标点/emoji；超20后端 400，需精简重发）**：蹭大IP + 发现感，
     如"梦想城镇同公司的游戏，没想到这么冷门"。
   - **正文**：对比 + 发现感——和大IP的关系/异同、节奏、"冷门/宝藏/开朝元老"，口语化分行
     （每个点单独一行，渲染会 nl2br 断行）。**只写核实过的事实**。
   - **tags**：5-10 个 = 大IP名（`#梦想城镇` 等）+ 品类（`#模拟游戏 #城市建造游戏`）+
     受众（`#适合女生玩的游戏 #宝藏游戏推荐`）。

4. **出封面（问用户：合成 / 原图，默认合成）**：
   - **合成**（默认）：从 `screenshot_urls` 选一张作 `cover_image`，写一句**封面钩子**当 `title`
     （可与正文标题不同、更短更抓眼，如"女生爱玩的宝藏游戏"），可选 `subtitle`；组 frontmatter-only
     的 render-markdown（**无正文、无 `---`**）：

     ```markdown
     ---
     cover_image: "<某个 screenshot_url，或 search_web_image 兜底 url>"
     title: "女生爱玩的宝藏游戏"
     subtitle: "梦想城镇同款·更冷门"
     ---
     ```
     `compose_xhs_cards(render_markdown=<上面>, theme=<问用户选>, mode="separator")` → `job_id`
     → 轮询 `get_xhs_status(job_id)` 到 `done` → 取 `cover_url`（`card_urls` 为空，单图只用 cover）。
   - **原图**：直接用某个 `screenshot_url`（或 `search_web_image` 兜底 url）当封面图，跳过渲染。

5. **落库**：`markdown_content` = `![](封面图 url)`（合成用 `cover_url`，原图用 screenshot url）
   + 空行 + 正文文案 + 空行 + tags。
   `save_xhs_note(prompt_template_id=<步骤3选的模板 id>, title=<小红书标题>,
   markdown_content=<上面>)` —— **不传 source_article_id**（游戏来源无源文章）。
   → 落**未审核库**（`review_status="pending"`），内容列表显示「小红书图文」徽标。
   步骤3的模板：`list_prompt_templates(scope="generation", platform="xiaohongshu")` 让用户选一个
   （推荐运营预置一个「游戏蹭大IP·发现感」模板）。

# 约束 / 注意

- **不做自动发布**：只产素材 + 落未审核库，不调任何 distribute/publish 工具。
- **蹭点必须核实**：宁可软框架也不编造关系（见流程第 2 步）。
- **主题问用户**：合成封面的 `theme` 每次问，不替用户默认。
- **标题 ≤20 字**：收到 400「标题超过20字」就精简重发（封面已渲染好、只改 title + 重拼 markdown）。
- **单图**：本类型就是一张封面图 + 文案，不做多图轮播 / 正文卡（那是 xhs-note-creator）。
- **游戏用量**：如需回写用量，可在能力允许时带上目标游戏（本 skill 不强制）。

# 失败处理

- `query_games_by_tags` 无结果 / 目标游戏无截图 → `search_web_image` 兜底；仍无图 → 告知用户、
  请其提供封面图，不硬凑。
- `compose_xhs_cards` 提交失败 / `get_xhs_status` 长期 pending 或 failed → 告知用户 `job_id` + `error`，
  不重试、不阻塞。
- `save_xhs_note` 标题超长 400 → 精简标题 ≤20 后重发（已渲染的封面 url 仍有效）。
