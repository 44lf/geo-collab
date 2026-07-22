"""小红书卡片渲染核心（纯函数，无 Playwright）。

移植自 `Auto-Redbook-Skills-main/scripts/render_xhs.py`：资产路径改指向本模块内的
`assets/`，`parse_markdown_file(path)` 改为直接吃字符串的 `parse_markdown_string(md)`。
Playwright 截图编排在 Task 2 的模块内实现，不在本文件。
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

import markdown
import yaml
from playwright.async_api import async_playwright

ASSETS_DIR = Path(__file__).parent / "assets"
THEMES_DIR = ASSETS_DIR / "themes"

# 卡片内 <img src="/api/..."> 是相对路径，Playwright 走 file:// 打开渲染文件时拉不到；
# 重写成内网绝对地址。已是绝对(http…)或非 /api 相对的不动。
_INTERNAL_BASE = os.environ.get("GEO_INTERNAL_URL", "http://127.0.0.1:8000")
_IMG_SRC_RE = re.compile(r'(<img\b[^>]*\bsrc=")(/api/[^"]*)(")', re.IGNORECASE)

# 默认卡片尺寸配置 (3:4 比例)
DEFAULT_WIDTH = 1080
DEFAULT_HEIGHT = 1440
MAX_HEIGHT = 4320  # dynamic 模式最大高度

# 可用主题列表
AVAILABLE_THEMES = [
    "default",
    "playful-geometric",
    "neo-brutalism",
    "botanical",
    "professional",
    "retro",
    "terminal",
    "sketch",
]

# 分页模式
PAGING_MODES = ["separator", "auto-fit", "auto-split", "dynamic"]

# 主题背景色（封面 / 卡片共用取色逻辑，卡片用 135deg 渐变见 _CARD_THEME_BACKGROUNDS）
_COVER_THEME_BACKGROUNDS = {
    "default": "linear-gradient(180deg, #f3f3f3 0%, #f9f9f9 100%)",
    "playful-geometric": "linear-gradient(180deg, #8B5CF6 0%, #F472B6 100%)",
    "neo-brutalism": "linear-gradient(180deg, #FF4757 0%, #FECA57 100%)",
    "botanical": "linear-gradient(180deg, #4A7C59 0%, #8FBC8F 100%)",
    "professional": "linear-gradient(180deg, #2563EB 0%, #3B82F6 100%)",
    "retro": "linear-gradient(180deg, #D35400 0%, #F39C12 100%)",
    "terminal": "linear-gradient(180deg, #0D1117 0%, #21262D 100%)",
    "sketch": "linear-gradient(180deg, #555555 0%, #999999 100%)",
}

_COVER_TITLE_GRADIENTS = {
    "default": "linear-gradient(180deg, #111827 0%, #4B5563 100%)",
    "playful-geometric": "linear-gradient(180deg, #7C3AED 0%, #F472B6 100%)",
    "neo-brutalism": "linear-gradient(180deg, #000000 0%, #FF4757 100%)",
    "botanical": "linear-gradient(180deg, #1F2937 0%, #4A7C59 100%)",
    "professional": "linear-gradient(180deg, #1E3A8A 0%, #2563EB 100%)",
    "retro": "linear-gradient(180deg, #8B4513 0%, #D35400 100%)",
    "terminal": "linear-gradient(180deg, #39D353 0%, #58A6FF 100%)",
    "sketch": "linear-gradient(180deg, #111827 0%, #6B7280 100%)",
}

_CARD_THEME_BACKGROUNDS = {
    "default": "linear-gradient(180deg, #f3f3f3 0%, #f9f9f9 100%)",
    "playful-geometric": "linear-gradient(135deg, #8B5CF6 0%, #F472B6 100%)",
    "neo-brutalism": "linear-gradient(135deg, #FF4757 0%, #FECA57 100%)",
    "botanical": "linear-gradient(135deg, #4A7C59 0%, #8FBC8F 100%)",
    "professional": "linear-gradient(135deg, #2563EB 0%, #3B82F6 100%)",
    "retro": "linear-gradient(135deg, #D35400 0%, #F39C12 100%)",
    "terminal": "linear-gradient(135deg, #0D1117 0%, #161B22 100%)",
    "sketch": "linear-gradient(135deg, #555555 0%, #888888 100%)",
}


def parse_markdown_string(md: str) -> dict:
    """解析 Markdown 字符串，提取 YAML 头部和正文内容。

    metadata 缺省的 `emoji`/`title`/`subtitle` 补空串。
    """
    yaml_pattern = r"^---\s*\n(.*?)\n---\s*\n"
    yaml_match = re.match(yaml_pattern, md, re.DOTALL)

    metadata: dict = {}
    body = md

    if yaml_match:
        try:
            metadata = yaml.safe_load(yaml_match.group(1)) or {}
        except yaml.YAMLError:
            metadata = {}
        body = md[yaml_match.end() :]

    for key in ("emoji", "title", "subtitle"):
        metadata.setdefault(key, "")

    return {
        "metadata": metadata,
        "body": body.strip(),
    }


def split_content_by_separator(body: str) -> list[str]:
    """按照 --- 分隔符拆分正文为多张卡片内容"""
    parts = re.split(r"\n---+\n", body)
    return [part.strip() for part in parts if part.strip()]


def convert_markdown_to_html(md: str) -> str:
    """将 Markdown 转换为 HTML。

    nl2br：单个换行 → <br>（与原 Auto-Redbook 一致）。小红书文案本就分行短句，
    不加 nl2br 会被 markdown 把单换行折成空格、挤成一大坨、可读性差。
    """
    return markdown.markdown(md, extensions=["extra", "nl2br"])


def load_theme_css(theme: str) -> str:
    """加载主题 CSS 样式，未知主题回落 default"""
    theme_file = THEMES_DIR / f"{theme}.css"
    if theme_file.exists():
        return theme_file.read_text(encoding="utf-8")

    default_file = THEMES_DIR / "default.css"
    if default_file.exists():
        return default_file.read_text(encoding="utf-8")
    return ""


def rewrite_img_src(html: str, base: str = _INTERNAL_BASE) -> str:
    """把 <img src="/api/…"> 的相对 src 重写成 base + src，让 Playwright(file://) 能拉。
    已是绝对(http…)或非 /api 相对的不动。"""
    return _IMG_SRC_RE.sub(rf"\g<1>{base}\g<2>\g<3>", html)


def generate_cover_html(metadata: dict, theme: str, width: int, height: int) -> str:
    """生成封面 HTML"""
    emoji = metadata.get("emoji") or "📝"
    title = metadata.get("title") or "标题"
    subtitle = metadata.get("subtitle") or ""

    # 动态调整标题字体大小
    title_len = len(title)
    if title_len <= 6:
        title_size = int(width * 0.14)  # 极大
    elif title_len <= 10:
        title_size = int(width * 0.12)  # 大
    elif title_len <= 18:
        title_size = int(width * 0.09)  # 中
    elif title_len <= 30:
        title_size = int(width * 0.07)  # 小
    else:
        title_size = int(width * 0.055)  # 极小

    bg = _COVER_THEME_BACKGROUNDS.get(theme, _COVER_THEME_BACKGROUNDS["default"])
    title_bg = _COVER_TITLE_GRADIENTS.get(theme, _COVER_TITLE_GRADIENTS["default"])

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width={width}, height={height}">
    <title>小红书封面</title>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@300;400;500;700;900&display=swap');

        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: 'Noto Sans SC', 'Source Han Sans CN', 'PingFang SC', 'Microsoft YaHei', sans-serif;
            width: {width}px;
            height: {height}px;
            overflow: hidden;
        }}

        .cover-container {{
            width: {width}px;
            height: {height}px;
            background: {bg};
            position: relative;
            overflow: hidden;
        }}

        .cover-inner {{
            position: absolute;
            width: {int(width * 0.88)}px;
            height: {int(height * 0.91)}px;
            left: {int(width * 0.06)}px;
            top: {int(height * 0.045)}px;
            background: #F3F3F3;
            border-radius: 25px;
            display: flex;
            flex-direction: column;
            padding: {int(width * 0.074)}px {int(width * 0.079)}px;
        }}

        .cover-emoji {{
            font-size: {int(width * 0.167)}px;
            line-height: 1.2;
            margin-bottom: {int(height * 0.035)}px;
        }}

        .cover-title {{
            font-weight: 900;
            font-size: {title_size}px;
            line-height: 1.4;
            background: {title_bg};
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            flex: 1;
            display: flex;
            align-items: flex-start;
            word-break: break-all;
        }}

        .cover-subtitle {{
            font-weight: 350;
            font-size: {int(width * 0.067)}px;
            line-height: 1.4;
            color: #000000;
            margin-top: auto;
        }}
    </style>
</head>
<body>
    <div class="cover-container">
        <div class="cover-inner">
            <div class="cover-emoji">{emoji}</div>
            <div class="cover-title">{title}</div>
            <div class="cover-subtitle">{subtitle}</div>
        </div>
    </div>
</body>
</html>"""
    return html


def generate_card_html(content: str, theme: str, page_number: int, width: int, height: int) -> str:
    """生成正文卡片 HTML（separator 分页模式的容器样式）"""
    html_content = convert_markdown_to_html(content)
    html_content = rewrite_img_src(html_content)
    theme_css = load_theme_css(theme)

    page_text = str(page_number) if page_number and page_number > 1 else ""

    bg = _CARD_THEME_BACKGROUNDS.get(theme, _CARD_THEME_BACKGROUNDS["default"])

    container_style = f"""
            width: {width}px;
            min-height: {height}px;
            background: {bg};
            position: relative;
            padding: 50px;
            overflow: hidden;
        """
    inner_style = f"""
            background: rgba(255, 255, 255, 0.95);
            border-radius: 20px;
            padding: 60px;
            min-height: calc({height}px - 100px);
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.1);
            backdrop-filter: blur(10px);
        """

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width={width}">
    <title>小红书卡片</title>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@300;400;500;700;900&display=swap');

        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: 'Noto Sans SC', 'Source Han Sans CN', 'PingFang SC', 'Microsoft YaHei', sans-serif;
            width: {width}px;
            overflow: hidden;
            background: transparent;
        }}

        .card-container {{
            {container_style}
        }}

        .card-inner {{
            {inner_style}
        }}

        .card-content {{
            line-height: 1.7;
        }}

        .card-content-scale {{
            transform-origin: top left;
            will-change: transform;
        }}

        {theme_css}

        .card-content img {{
            width: 100%;
            height: auto;
            max-height: 640px;
            object-fit: cover;
        }}

        .card-content :not(pre) > code {{
            overflow-wrap: anywhere;
            word-break: break-word;
        }}

        .page-number {{
            position: absolute;
            bottom: 80px;
            right: 80px;
            font-size: 36px;
            color: rgba(255, 255, 255, 0.8);
            font-weight: 500;
        }}
    </style>
</head>
<body>
    <div class="card-container">
        <div class="card-inner">
            <div class="card-content">
                <div class="card-content-scale">{html_content}</div>
            </div>
        </div>
        <div class="page-number">{page_text}</div>
    </div>
</body>
</html>"""
    return html


async def render_html_to_png_bytes(html: str, width: int, height: int, dpr: int) -> bytes:
    """用 headless Chromium 把一段 HTML 截图成 PNG bytes。"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--no-sandbox"])
        page = await browser.new_page(
            viewport={"width": width, "height": height}, device_scale_factor=dpr
        )
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as f:
            f.write(html)
            path = f.name
        try:
            await page.goto(f"file://{path}")
            await page.wait_for_timeout(200)
            png = await page.screenshot(full_page=True)
        finally:
            await browser.close()
            os.unlink(path)
        return png


async def render_markdown_to_card_bytes(
    md: str,
    *,
    theme: str,
    mode: str,
    width: int = 1080,
    height: int = 1440,
    dpr: int = 2,
) -> dict:
    """把整篇 markdown 编排渲染成封面 + 正文卡片的 PNG bytes。"""
    if theme not in AVAILABLE_THEMES:
        theme = "sketch"
    if mode not in PAGING_MODES:
        mode = "auto-split"
    parsed = parse_markdown_string(md)
    metadata, body = parsed["metadata"], parsed["body"]

    cover_html = generate_cover_html(metadata, theme, width, height)
    cover_png = await render_html_to_png_bytes(cover_html, width, height, dpr)

    if mode == "separator":
        chunks = split_content_by_separator(body)
    else:
        # auto-split / auto-fit / dynamic：MVP 先按 separator 语义 + 无分隔时整体一张。
        # 后续如需精确 auto-split，移植原 auto_split_content（@529，依赖真实渲染高度）。
        chunks = split_content_by_separator(body) if "---" in body else [body]

    cards: list[bytes] = []
    for i, chunk in enumerate(chunks, start=1):
        card_html = generate_card_html(chunk, theme, i, width, height)
        cards.append(await render_html_to_png_bytes(card_html, width, height, dpr))
    return {"cover": cover_png, "cards": cards}
