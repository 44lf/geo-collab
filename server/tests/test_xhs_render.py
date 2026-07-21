"""xhs_cards.render 纯函数测试（不触 Playwright）。"""

from server.app.modules.xhs_cards import render


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
