"""xhs_cards.render 纯函数测试（不触 Playwright）。"""

import asyncio
import os

import pytest

from server.app.modules.xhs_cards import render
from server.app.modules.xhs_cards import render as R


def test_parse_frontmatter_and_body():
    md = '---\nemoji: "🚀"\ntitle: "封面标题"\nsubtitle: "副标题"\n---\n\n正文第一段\n'
    out = render.parse_markdown_string(md)
    assert out["metadata"]["title"] == "封面标题"
    assert out["metadata"]["emoji"] == "🚀"
    assert out["metadata"]["subtitle"] == "副标题"
    assert "正文第一段" in out["body"]


def test_split_by_separator():
    body = "第一张\n\n---\n\n第二张\n\n---\n\n第三张"
    cards = render.split_content_by_separator(body)
    assert len(cards) == 3
    assert cards[0].strip() == "第一张"
    assert cards[2].strip() == "第三张"


def test_load_theme_css_fallback():
    assert "sketch" in render.AVAILABLE_THEMES
    css = render.load_theme_css("no-such-theme")
    assert css  # 回落 default，非空


def test_cover_html_contains_title():
    html = render.generate_cover_html(
        {"emoji": "🔥", "title": "标题X", "subtitle": "副X"}, "default", 1080, 1440
    )
    assert "标题X" in html and "<html" in html.lower()


def test_render_markdown_orchestration_separator(monkeypatch):
    # 用假的截图函数：返回可辨识的 bytes，避免起 chromium
    async def fake_shot(html, width, height, dpr):
        return b"PNG:" + (b"cover" if "cover" in html.lower() else b"card")

    monkeypatch.setattr(R, "render_html_to_png_bytes", fake_shot)
    md = '---\ntitle: "T"\nsubtitle: "S"\nemoji: "🔥"\n---\n\nA\n\n---\n\nB'
    out = asyncio.run(R.render_markdown_to_card_bytes(md, theme="default", mode="separator"))
    assert out["cover"].startswith(b"PNG:")
    assert len(out["cards"]) == 2  # A / B 两张


def test_rewrite_img_src():
    from server.app.modules.xhs_cards import render as R

    base = "http://127.0.0.1:8000"
    html = '<p><img src="/api/stock-images/5/file" alt="x"></p>'
    out = R.rewrite_img_src(html, base)
    assert 'src="http://127.0.0.1:8000/api/stock-images/5/file"' in out
    # 已是绝对 URL 不动
    html2 = '<img src="http://cdn/x.jpg">'
    assert R.rewrite_img_src(html2, base) == html2
    # 非 /api 相对不动
    html3 = '<img src="foo.png">'
    assert R.rewrite_img_src(html3, base) == html3


def test_card_html_has_absolute_img_and_maxheight():
    from server.app.modules.xhs_cards import render as R

    html = R.generate_card_html("正文\n\n![](/api/stock-images/9/file)", "default", 1, 1080, 1440)
    assert "http://127.0.0.1:8000/api/stock-images/9/file" in html
    assert "max-height" in html and "object-fit" in html  # 限高 style 注入


@pytest.mark.skipif(
    os.environ.get("GEO_XHS_RENDER_LIVE") != "1",
    reason="需容器内 chromium；设 GEO_XHS_RENDER_LIVE=1 启用",
)
def test_live_render_produces_png():
    md = '---\ntitle: "真渲染"\nsubtitle: "冒烟"\nemoji: "✅"\n---\n\n正文\n\n---\n\n第二张'
    out = asyncio.run(R.render_markdown_to_card_bytes(md, theme="sketch", mode="separator"))
    assert out["cover"][:8] == b"\x89PNG\r\n\x1a\n"
    assert all(c[:8] == b"\x89PNG\r\n\x1a\n" for c in out["cards"])


def test_convert_markdown_nl2br_linebreaks():
    """单换行 → <br>（nl2br）：多行小红书文案不挤成一坨。"""
    from server.app.modules.xhs_cards import render as R

    html = R.convert_markdown_to_html("第一行\n第二行\n第三行")
    assert html.count("<br") >= 2  # 两处单换行都成断行


def test_cover_title_no_midword_break():
    """封面标题不用 break-all（会把 TOP5 断成 TO/P5），改 normal + overflow-wrap，
    让拉丁/数字词整体折行、中文仍可逐字断。"""
    from server.app.modules.xhs_cards import render as R

    html = R.generate_cover_html(
        {"emoji": "🎮", "title": "合成游戏TOP5", "subtitle": "越玩越上头"},
        "playful-geometric",
        1080,
        1440,
    )
    assert "word-break: break-all" not in html
    assert "overflow-wrap: break-word" in html


def test_split_trailing_image():
    """抽出卡片正文末尾的 ![](url)：返回去图正文 + url；无图返回 (原文, None)。"""
    from server.app.modules.xhs_cards import render as R

    text, url = R.split_trailing_image("标题\n正文一行\n![](/api/stock-images/7/file)")
    assert url == "/api/stock-images/7/file"
    assert "![]" not in text and text.strip() == "标题\n正文一行"
    # 无图
    text2, url2 = R.split_trailing_image("标题\n正文没有图")
    assert url2 is None
    assert text2 == "标题\n正文没有图"


def test_bold_first_line():
    """正文第一非空行加粗（小红书卡片标题行）。"""
    from server.app.modules.xhs_cards import render as R

    out = R.bold_first_line("🥈 TOP2 | 黑暗料理王\n经营+暗黑料理\n推荐指数：★★★★☆")
    assert out.startswith("<strong>🥈 TOP2 | 黑暗料理王</strong>")
    # 后续行不加粗
    assert "<strong>经营" not in out
    # 前导空行跳过、加粗真正的首行
    out2 = R.bold_first_line("\n\n第一行\n第二行")
    assert "<strong>第一行</strong>" in out2 and "<strong>第二行" not in out2


def test_card_fixed_height_and_img_slot():
    """卡片锁定固定高（height 而非 min-height），配图进自适应 slot，首行加粗。"""
    from server.app.modules.xhs_cards import render as R

    html = R.generate_card_html(
        "🥈 TOP2 | 黑暗料理王\n经营暗黑料理\n![](/api/stock-images/1/file)",
        "sketch",
        1,
        1080,
        1440,
    )
    # 固定高：容器用 height: 1440px，不再用 min-height 撑高
    assert "height: 1440px" in html
    assert "min-height: 1440px" not in html
    # 配图落在自适应 slot（填充剩余空间）
    assert "card-img-slot" in html
    assert "http://127.0.0.1:8000/api/stock-images/1/file" in html
    # 首行加粗
    assert "<strong>🥈 TOP2 | 黑暗料理王</strong>" in html


def test_card_no_image_still_fixed_height():
    """无配图的卡片同样锁定固定高、首行加粗，且不生成 img slot。"""
    from server.app.modules.xhs_cards import render as R

    html = R.generate_card_html("纯文案标题\n正文一句", "default", 1, 1080, 1440)
    assert "height: 1440px" in html
    assert "min-height: 1440px" not in html
    assert "<strong>纯文案标题</strong>" in html


def test_card_img_slot_full_width_contain():
    """配图槽位：满宽 + object-fit:contain —— 宽度统一(不再按内在像素宽渲染)，
    且保全整图不裁、随剩余空间自适应缩放。"""
    import re

    from server.app.modules.xhs_cards import render as R

    html = R.generate_card_html("正文\n\n![](/api/stock-images/1/file)", "sketch", 1, 1080, 1440)
    assert "card-img-slot" in html
    # 满宽 → 宽度统一。用 [^-]width 排除主题里 max-width:100% 的误命中。
    assert re.search(r"[^-]width:\s*100%", html)
    assert "object-fit: contain" in html  # 保全整图、不裁不拉伸
