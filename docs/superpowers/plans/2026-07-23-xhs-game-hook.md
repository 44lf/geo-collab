# xhs-game-hook 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 skill `xhs-game-hook`，从 GEO 游戏库取材，产出「单图（合成/原图封面）+ 蹭同类大IP文案」的小红书图文，落未审核库、不自动发布。

**Architecture:** 后端只做两处小改：`render.py:generate_cover_html` 支持 `cover_image`（游戏图铺底 + 渐变蒙层 + 标题叠加的合成封面），`save_xhs_note` 工具把 `source_article_id` 改可选（游戏来源无源文章；后端 `save-from-mcp` 本就可选）。其余全复用 `compose_xhs_cards`/`get_xhs_status`/`save-from-mcp` 流水线。新增一份 SKILL.md 编排主对话流程。

**Tech Stack:** Python / FastMCP tools / Playwright headless render / pytest（MySQL，dev 容器内跑）。

设计稿：`docs/superpowers/specs/2026-07-23-xhs-game-hook-design.md`。

## Global Constraints

- 标题硬门禁 **≤20 字**（`save-from-mcp` 对 `content_type="xhs_image_text"` 已强制，超限 400）；skill 标题也照此。
- **不自动发布**：skill 只产素材 + 落未审核库（`review_status="pending"`），不调任何 distribute/publish 工具。
- **MCP 工具总数保持 39**（`mcp_catalog/connect_router.py:MCP_TOOLS_COUNT`）——本方案不新增工具（复用 `compose_xhs_cards` + `cover_image` frontmatter）。
- 蹭大IP的**关系必须 WebSearch 核实**（同公司/同发行/同玩法）；核实不了只写恒真软框架，**绝不编造公司/关系**。
- 落库 `content_type="xhs_image_text"`；单图 = 1 张封面图 + 文案 + tags。
- render 改动走**纯函数 + 单测 + dev 容器实测渲染 PNG 人工验收**。
- 门禁：`ruff check` + `ruff format --check` + `mypy server/app` 全绿；后端测试在 dev 容器跑并显式传 `GEO_TEST_DATABASE_URL`（DB 名含 test）。
- 文件行尾 LF。

测试运行范式（dev 容器，密码从容器内环境变量取）：
```bash
docker compose exec -T app sh -c '
export GEO_TEST_DATABASE_URL="mysql+pymysql://${GEO_DB_USER}:${GEO_DB_PASS}@${GEO_DB_HOST}:${GEO_DB_PORT}/geo_test"
python -m pytest <路径> -q'
```

---

### Task 1: 合成封面渲染（`generate_cover_html` 支持 `cover_image`）

**Files:**
- Modify: `server/app/modules/xhs_cards/render.py`（`generate_cover_html` 顶部加分支 + 新增内部 helper `_image_cover_html`）
- Test: `server/tests/test_xhs_render.py`

**Interfaces:**
- Consumes: 现有 `rewrite_img_src(html, base=_INTERNAL_BASE) -> str`（把 `<img src="/api/…">` 相对 src 重写成内网绝对地址）。
- Produces: `generate_cover_html(metadata: dict, theme: str, width: int, height: int) -> str`（签名不变）——当 `metadata["cover_image"]` 非空时返回「图片铺底 + 渐变蒙层 + 标题叠加」的合成封面 HTML；为空时维持现有「渐变 + emoji + title + subtitle」文字封面。

- [ ] **Step 1: 写失败测试**

在 `server/tests/test_xhs_render.py` 末尾追加：

```python
def test_cover_image_composed():
    """metadata 带 cover_image → 合成封面：游戏图铺底(object-fit:cover, 绝对 src) +
    底部渐变蒙层 + 标题/副标题叠加。"""
    from server.app.modules.xhs_cards import render as R

    html = R.generate_cover_html(
        {
            "cover_image": "/api/stock-images/5/file",
            "title": "女生爱玩的宝藏游戏",
            "subtitle": "梦想城镇同款·更冷门",
        },
        "playful-geometric",
        1080,
        1440,
    )
    assert 'class="cover-bg"' in html
    assert "http://127.0.0.1:8000/api/stock-images/5/file" in html  # rewrite_img_src 后绝对 src
    assert "object-fit: cover" in html  # 背景铺满
    assert "linear-gradient" in html  # 底部渐变蒙层保证文字可读
    assert "女生爱玩的宝藏游戏" in html
    assert "梦想城镇同款·更冷门" in html


def test_cover_without_image_is_text_cover():
    """无 cover_image → 维持现有文字封面(渐变+emoji+title)，不含背景图。"""
    from server.app.modules.xhs_cards import render as R

    html = R.generate_cover_html(
        {"emoji": "🎮", "title": "合成游戏TOP5", "subtitle": "越玩越上头"},
        "playful-geometric",
        1080,
        1440,
    )
    assert 'class="cover-bg"' not in html
    assert "合成游戏TOP5" in html
```

- [ ] **Step 2: 跑测试确认失败**

```bash
docker compose exec -T app python -m pytest \
  server/tests/test_xhs_render.py::test_cover_image_composed \
  server/tests/test_xhs_render.py::test_cover_without_image_is_text_cover -q
```
Expected: `test_cover_image_composed` FAIL（现无 cover_image 分支、无 `class="cover-bg"`）；`test_cover_without_image_is_text_cover` PASS（现状即文字封面）。

- [ ] **Step 3: 实现合成封面 helper + 分支**

在 `server/app/modules/xhs_cards/render.py` 的 `generate_cover_html` 定义**之前**插入 helper：

```python
def _image_cover_html(
    cover_image: str, title: str, subtitle: str, width: int, height: int
) -> str:
    """合成封面：游戏截图铺底(object-fit:cover) + 底部渐变蒙层 + 标题(可选副标题)白字叠加。

    背景 src 走 rewrite_img_src 变内网绝对地址，让 Playwright(file://) 能拉。标题按长度
    自适应字号；word-break:normal + overflow-wrap:break-word 让拉丁/数字词(如 TOP5)整体不拆。
    """
    title = title or "标题"
    tl = len(title)
    if tl <= 6:
        title_size = int(width * 0.13)
    elif tl <= 10:
        title_size = int(width * 0.11)
    elif tl <= 18:
        title_size = int(width * 0.085)
    else:
        title_size = int(width * 0.065)

    bg_img = rewrite_img_src(f'<img class="cover-bg" src="{cover_image}">')
    subtitle_html = f'<div class="cover-subtitle">{subtitle}</div>' if subtitle else ""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width={width}, height={height}">
    <title>小红书封面</title>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@300;400;500;700;900&display=swap');
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: 'Noto Sans SC', 'Source Han Sans CN', 'PingFang SC', 'Microsoft YaHei', sans-serif;
            width: {width}px; height: {height}px; overflow: hidden;
        }}
        .cover-container {{
            position: relative; width: {width}px; height: {height}px; overflow: hidden; background: #000;
        }}
        .cover-bg {{
            position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover;
        }}
        .cover-scrim {{
            position: absolute; left: 0; right: 0; bottom: 0; height: 55%;
            background: linear-gradient(180deg, rgba(0, 0, 0, 0) 0%, rgba(0, 0, 0, 0.78) 100%);
        }}
        .cover-overlay {{
            position: absolute; left: 0; right: 0; bottom: 0;
            padding: {int(width * 0.08)}px {int(width * 0.07)}px {int(height * 0.06)}px;
        }}
        .cover-title {{
            font-weight: 900; font-size: {title_size}px; line-height: 1.35; color: #ffffff;
            text-shadow: 0 4px 24px rgba(0, 0, 0, 0.6);
            word-break: normal; overflow-wrap: break-word;
        }}
        .cover-subtitle {{
            margin-top: {int(height * 0.02)}px; font-weight: 500; font-size: {int(width * 0.05)}px;
            line-height: 1.4; color: rgba(255, 255, 255, 0.92);
            text-shadow: 0 2px 12px rgba(0, 0, 0, 0.6);
        }}
    </style>
</head>
<body>
    <div class="cover-container">
        {bg_img}
        <div class="cover-scrim"></div>
        <div class="cover-overlay">
            <div class="cover-title">{title}</div>
            {subtitle_html}
        </div>
    </div>
</body>
</html>"""
```

然后在 `generate_cover_html` 函数体**最开头**（`emoji = metadata.get(...)` 之前）加分支：

```python
def generate_cover_html(metadata: dict, theme: str, width: int, height: int) -> str:
    """生成封面 HTML"""
    cover_image = (metadata.get("cover_image") or "").strip()
    if cover_image:
        return _image_cover_html(
            cover_image,
            metadata.get("title") or "标题",
            metadata.get("subtitle") or "",
            width,
            height,
        )

    # 以下为原有文字封面逻辑，保持不变
    emoji = metadata.get("emoji") or "📝"
    ...
```
（`theme` 参数在合成封面分支不用，但签名保持不变——文字封面仍用它。）

- [ ] **Step 4: 跑测试确认通过**

```bash
docker compose exec -T app python -m pytest server/tests/test_xhs_render.py -q
```
Expected: 全绿（新增 2 个 PASS，旧用例不回归）。

- [ ] **Step 5: 实测渲染人工验收**

```bash
docker compose exec -T app sh -c '
python - <<PY
import asyncio, struct
from server.app.modules.xhs_cards import render as R
def dims(b): return struct.unpack(">II", b[16:24])
html = R.generate_cover_html(
    {"cover_image":"/api/stock-images/1/file","title":"女生爱玩的宝藏游戏","subtitle":"梦想城镇同款·更冷门"},
    "playful-geometric", 1080, 1440)
png = asyncio.run(R.render_html_to_png_bytes(html, 1080, 1440, 2))
open("/app/_gc.png","wb").write(png); print("dims", dims(png), "isPNG", png[:8]==b"\x89PNG\r\n\x1a\n")
PY
'
docker compose cp app:/app/_gc.png ./_gc.png
```
用 Read 工具看 `_gc.png`：游戏图铺满、底部渐变、标题+副标题白字清晰可读、尺寸 2160×2880。看完删除：`rm -f _gc.png && docker compose exec -T app rm -f /app/_gc.png`。

- [ ] **Step 6: Lint + 提交**

```bash
docker compose exec -T app sh -c 'ruff check server/app/modules/xhs_cards/render.py server/tests/test_xhs_render.py && ruff format --check server/app/modules/xhs_cards/render.py server/tests/test_xhs_render.py && mypy server/app/modules/xhs_cards/render.py'
git add server/app/modules/xhs_cards/render.py server/tests/test_xhs_render.py
git commit -m "feat(xhs): 合成封面渲染(游戏图铺底+标题叠加) generate_cover_html 支持 cover_image"
```

---

### Task 2: `save_xhs_note` 的 `source_article_id` 改可选（游戏来源无源文章）

**Files:**
- Modify: `server/mcp/tools/xhs.py`（`save_xhs_note` 签名 + body 组装）
- Test: `server/tests/test_save_article_mcp.py`（端点侧：无 source_article_id 也能落库）

**Interfaces:**
- Produces: `save_xhs_note(prompt_template_id: int, title: str, markdown_content: str, source_article_id: int | None = None, model_label: str | None = None) -> dict`。`source_article_id` 非空才放进请求体；恒置 `content_type="xhs_image_text"`。后端 `POST /api/articles/save-from-mcp` 无改动（其 `source_article_id` 本就 `int | None = None`）。

- [ ] **Step 1: 写失败测试**

在 `server/tests/test_save_article_mcp.py` 追加（放在 `test_save_from_mcp_xhs_no_question` 之后）：

```python
def test_save_from_mcp_xhs_without_source_article(monkeypatch):
    """游戏来源单图图文：无 source_article_id、无 question_item_id → 落库 pending，
    content_type 正确，metrics 不含 source_article_id。"""
    from server.app.modules.articles.models import Article
    from server.tests.utils import build_test_app

    test_app = build_test_app(monkeypatch)
    try:
        monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
        from server.app.core import config

        config.get_settings.cache_clear()

        _item_id, tpl_id = _seed_question_and_template(test_app)

        r = test_app.client.post(
            "/api/articles/save-from-mcp",
            json={
                "prompt_template_id": tpl_id,
                "user_id": test_app.admin_id,
                "title": "梦想城镇同款宝藏游戏",
                "markdown_content": "![](/api/stock-images/1/file)\n\n正文文案\n\n#梦想城镇 #适合女生玩的游戏",
                "content_type": "xhs_image_text",
            },
            headers={"X-MCP-Token": "secret"},
        )
        assert r.status_code == 200, r.text
        aid = r.json()["article_id"]
        with test_app.session_factory() as db:
            art = db.get(Article, aid)
            assert art is not None
            assert art.review_status == "pending"
            assert art.content_type == "xhs_image_text"
            assert "source_article_id" not in (art.metrics or {})
    finally:
        test_app.cleanup()
```

- [ ] **Step 2: 跑测试确认通过（端点本就可选，应直接 PASS）**

```bash
docker compose exec -T app sh -c '
export GEO_TEST_DATABASE_URL="mysql+pymysql://${GEO_DB_USER}:${GEO_DB_PASS}@${GEO_DB_HOST}:${GEO_DB_PORT}/geo_test"
python -m pytest server/tests/test_save_article_mcp.py::test_save_from_mcp_xhs_without_source_article -q'
```
Expected: PASS。这条钉住「游戏来源无源文章」的落库路径（端点侧契约）。若失败，说明端点未按预期处理，需回 systematic-debugging。

- [ ] **Step 3: 放开 `save_xhs_note` 工具的 `source_article_id`**

`server/mcp/tools/xhs.py`，把 `save_xhs_note` 改为：

```python
async def save_xhs_note(
    prompt_template_id: int,
    title: str,
    markdown_content: str,
    source_article_id: int | None = None,
    model_label: str | None = None,
) -> dict[str, Any]:
    """Persist a rendered Xiaohongshu image-text note into GEO's review queue (pending).

    Assemble markdown_content as the cover/card image link(s) (as ![](url)) + the
    Xiaohongshu copy text (title / body / SEO #tags) at the end. Lands review_status=pending
    with content_type="xhs_image_text" so the content list badges it as 小红书图文.

    Args:
        prompt_template_id: the template used to write/condense (list_prompt_templates).
        title: note title (<=20 chars for xhs; over 20 → 400 from backend).
        markdown_content: image links + copy text (see above).
        source_article_id: the approved article this derives from — OPTIONAL. Omit for
            game-sourced notes (xhs-game-hook) that have no source article.
        model_label: optional writer label.

    Returns:
        {"ok": True, "data": {"article_id": N}, "error": None}
    """
    body: dict[str, Any] = {
        "prompt_template_id": prompt_template_id,
        "user_id": _OPERATOR_USER_ID,
        "title": title,
        "markdown_content": markdown_content,
        "content_type": "xhs_image_text",
    }
    if source_article_id is not None:
        body["source_article_id"] = source_article_id
    if model_label:
        body["model_label"] = model_label
    return await _apost("/api/articles/save-from-mcp", json=body)
```

注意：参数顺序调整为「必填在前、可选在后」（Python 语法要求，且 MCP/LLM 按名传参，不影响现有 `xhs-note-creator` 的关键字调用）。

- [ ] **Step 4: 确认已注册工具签名生效（不新增工具，计数仍 39）**

```bash
docker compose exec -T app sh -c '
export GEO_TEST_DATABASE_URL="mysql+pymysql://${GEO_DB_USER}:${GEO_DB_PASS}@${GEO_DB_HOST}:${GEO_DB_PORT}/geo_test"
python -m pytest server/tests/test_mcp_status_count.py server/tests/test_mcp_tools_registration.py -q'
```
Expected: PASS（`MCP_TOOLS_COUNT` 仍 39——只改签名未加工具）。

- [ ] **Step 5: Lint + 提交**

```bash
docker compose exec -T app sh -c 'ruff check server/mcp/tools/xhs.py server/tests/test_save_article_mcp.py && ruff format --check server/mcp/tools/xhs.py server/tests/test_save_article_mcp.py'
git add server/mcp/tools/xhs.py server/tests/test_save_article_mcp.py
git commit -m "feat(xhs): save_xhs_note 的 source_article_id 改可选(游戏来源无源文章)"
```

---

### Task 3: 新 skill `xhs-game-hook/SKILL.md`

**Files:**
- Create: `.claude/skills/xhs-game-hook/SKILL.md`

**Interfaces:**
- Consumes（MCP 工具，均已存在）：`list_game_tags`、`query_games_by_tags`、`search_web_image`、`list_prompt_templates`、`compose_xhs_cards`、`get_xhs_status`、`save_xhs_note`（Task 2 后 `source_article_id` 可选）；`generate_cover_html` 的 `cover_image` frontmatter（Task 1）。
- Produces: 一份可被 Claude Code 识别触发的 skill。

- [ ] **Step 1: 写 SKILL.md**

创建 `.claude/skills/xhs-game-hook/SKILL.md`，内容如下（完整照抄）：

```markdown
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
```

- [ ] **Step 2: 端到端冒烟（dev 容器，验证封面渲染 + 落库两条真链路）**

用一张真实截图 url 走「合成封面 → save-from-mcp（无 source_article_id）」验证整链（不依赖 MCP 客户端，直接打后端端点复刻工具行为）：

```bash
docker compose exec -T app sh -c '
python - <<PY
import asyncio
from server.app.modules.xhs_cards import render as R
# 1) 合成封面渲染出图（复刻 compose 的 cover 分支）
md = "---\ncover_image: \"/api/stock-images/1/file\"\ntitle: \"女生爱玩的宝藏游戏\"\nsubtitle: \"梦想城镇同款·更冷门\"\n---\n"
out = asyncio.run(R.render_markdown_to_card_bytes(md, theme="playful-geometric", mode="separator"))
print("cover bytes", len(out["cover"]), "cards", len(out["cards"]))  # cards 应为 0
PY
'
```
Expected: 打印 `cover bytes <非0>` 且 `cards 0`（frontmatter-only → 只出封面、无正文卡）。落库路径已由 Task 2 的 `test_save_from_mcp_xhs_without_source_article` 覆盖，无需重复打库。

- [ ] **Step 3: 提交**

```bash
git add -f .claude/skills/xhs-game-hook/SKILL.md
git commit -m "feat(xhs): 新增 xhs-game-hook skill(游戏图蹭大IP单图图文)"
```

---

## 收尾（全部任务后）

- [ ] 全量回归：`docker compose exec -T app sh -c 'export GEO_TEST_DATABASE_URL="mysql+pymysql://${GEO_DB_USER}:${GEO_DB_PASS}@${GEO_DB_HOST}:${GEO_DB_PORT}/geo_test"; python -m pytest server/tests/test_xhs_render.py server/tests/test_save_article_mcp.py server/tests/test_mcp_status_count.py -q'`
- [ ] `ruff check server/` + `ruff format --check server/` + `mypy server/app` 全绿。
- [ ] 用 finishing-a-development-branch：push `feat/xhs-game-hook` + 开 MR（server-only：render.py/xhs.py 在 server/；SKILL.md 是 skill 分发物）→ 用户合并 → geo-release 发 `server-*`（无迁移）。
- [ ] （运营，非本计划代码）Web「提示词管理」建一个 platform=xiaohongshu 的「游戏蹭大IP·发现感」模板。

## Self-Review

**Spec coverage**：① 合成封面渲染（cover_image）→ Task 1 ✓；② save_xhs_note 源文章可选 → Task 2 ✓；③ 新 skill 全流程（取材/选IP核实/文案/封面/落库）→ Task 3 ✓；④ 单图结构、标题≤20、不自动发布、蹭点护栏 → Global Constraints + Task 3 SKILL ✓；⑤ MCP 计数不变 39 → Task 2 Step 4 校验 ✓；⑥ 前端无改动 → 计划未含前端任务 ✓；⑦ 提示词模板（数据）→ 收尾清单（非代码）✓。无遗漏。

**Placeholder scan**：各代码步骤均给出完整可粘贴代码/命令；无 TBD/TODO/"类似上面"。

**Type consistency**：`generate_cover_html(metadata, theme, width, height)` 签名全程一致；helper `_image_cover_html(cover_image, title, subtitle, width, height)` 在 Task 1 定义并即用；`save_xhs_note(prompt_template_id, title, markdown_content, source_article_id=None, model_label=None)` 新签名在 Task 2 定义、Task 3 SKILL 按「不传 source_article_id」使用，一致；`rewrite_img_src` 复用现有签名。
