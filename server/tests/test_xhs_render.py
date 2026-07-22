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
