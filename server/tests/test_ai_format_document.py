"""formatting/document.py 纯函数单测（无 DB / 无 LLM / 无网络）。

配套计划：docs/plans/2026-07-13-articles-module-decomposition.md（Task 6）。
这些函数从 ai_format.py 原样迁出，此文件直接针对新模块回归；ai_format.py 的显式重导出另由
test_ai_format.py / test_illustrate_render_fix.py / test_illustrate_game_list_resolver.py 守。
"""

from server.app.modules.articles.formatting.document import (
    _apply_headings,
    _derive_html_and_text,
    _find_heading_index,
    _inline_html,
    _node_html,
    _node_plain_text,
    _node_text,
    _non_empty_text_nodes,
    _normalize_game_name,
    _normalize_heading_indices,
    _to_heading,
    _to_paragraph,
    _top_level_text_nodes,
    build_image_positions_from_game_list,
    has_ai_format_targets,
)


def _p(text: str) -> dict:
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


def _h(text: str, level: int = 2) -> dict:
    return {
        "type": "heading",
        "attrs": {"level": level},
        "content": [{"type": "text", "text": text}],
    }


def _doc(*nodes) -> dict:
    return {"type": "doc", "content": list(nodes)}


# ── 节点拍平与文本抽取 ───────────────────────────────────────────────────────


def test_top_level_text_nodes_only_paragraph_and_heading():
    doc = _doc(_p("a"), _h("b"), {"type": "image", "attrs": {"src": "x"}}, _p("c"))
    result = _top_level_text_nodes(doc)
    assert [i for i, _ in result] == [0, 1, 3]


def test_non_empty_text_nodes_drops_blank():
    doc = _doc(_p("hi"), _p("   "), _h("t"))
    assert [i for i, _ in _non_empty_text_nodes(doc)] == [0, 2]


def test_node_text_joins_text_and_hardbreak():
    node = {
        "type": "paragraph",
        "content": [
            {"type": "text", "text": "Hello"},
            {"type": "hardBreak"},
            {"type": "text", "text": "World"},
        ],
    }
    assert _node_text(node) == "Hello\nWorld"


def test_has_ai_format_targets_dict_str_and_empty():
    assert has_ai_format_targets(_doc(_p("x"))) is True
    assert (
        has_ai_format_targets(
            '{"type":"doc","content":[{"type":"paragraph","content":[{"type":"text","text":"x"}]}]}'
        )
        is True
    )
    assert has_ai_format_targets(_doc()) is False
    assert has_ai_format_targets(None) is False
    assert has_ai_format_targets(123) is False


# ── 游戏名归一化与 heading 定位 ──────────────────────────────────────────────


def test_normalize_game_name_strips_prefix_and_brackets():
    assert _normalize_game_name("游戏一、《餐厅养成记》") == "餐厅养成记"
    assert _normalize_game_name("《原神》") == "原神"
    assert _normalize_game_name("游戏10、明日方舟") == "明日方舟"
    assert _normalize_game_name("“原神”") == "原神"
    assert _normalize_game_name("") == ""


def test_find_heading_index_contains_match_first_hit():
    doc = _doc(_p("intro"), _h("游戏一、原神攻略"), _p("body"), _h("《明日方舟》"))
    assert _find_heading_index(doc, "原神") == 1
    assert _find_heading_index(doc, "明日方舟") == 3
    assert _find_heading_index(doc, "不存在") is None
    assert _find_heading_index(doc, "") is None


def test_build_image_positions_match_conflict_hint_and_category():
    doc = _doc(_h("原神"), _h("明日方舟"))
    positions, unmatched = build_image_positions_from_game_list(
        doc,
        [
            {"game": "原神", "category_id": 7},
            {"game": "明日方舟"},
            {"game": "原神"},  # 同 index → index_conflict
            {"game": "王者荣耀"},  # 无 heading、无 index → heading_not_found
            {"game": "崩坏", "index": 5},  # 无 heading 但有 index 兜底
        ],
    )
    assert positions == [
        {"index": 0, "game": "原神", "category_id": 7},
        {"index": 1, "game": "明日方舟"},
        {"index": 5, "game": "崩坏"},
    ]
    reasons = {u["game"]: u["reason"] for u in unmatched}
    assert reasons == {"原神": "index_conflict", "王者荣耀": "heading_not_found"}


# ── HTML / 纯文本派生 ────────────────────────────────────────────────────────


def test_inline_html_marks_link_and_hardbreak():
    children = [
        {"type": "text", "text": "b", "marks": [{"type": "bold"}]},
        {"type": "hardBreak"},
        {"type": "text", "text": "L", "marks": [{"type": "link", "attrs": {"href": "u"}}]},
    ]
    assert _inline_html(children) == '<strong>b</strong><br><a href="u">L</a>'


def test_node_html_block_kinds():
    assert _node_html(_h("t", 3)) == "<h3>t</h3>"
    assert _node_html(_p("p")) == "<p>p</p>"
    assert (
        _node_html({"type": "image", "attrs": {"src": "s", "alt": "a"}}) == '<img src="s" alt="a">'
    )
    ul = {"type": "bulletList", "content": [{"type": "listItem", "content": [_p("i")]}]}
    assert _node_html(ul) == "<ul><li><p>i</p></li></ul>"
    assert (
        _node_html({"type": "somethingUnknown", "content": [{"type": "text", "text": "z"}]})
        == "<p>z</p>"
    )


def test_node_plain_text_recurses_list_and_skips_image():
    ul = {"type": "bulletList", "content": [{"type": "listItem", "content": [_p("a"), _p("b")]}]}
    assert _node_plain_text(ul) == "a\nb"
    assert _node_plain_text({"type": "image", "attrs": {"src": "x"}}) == ""


def test_derive_html_and_text():
    doc = _doc(_h("T"), _p("body"), {"type": "image", "attrs": {"src": "x"}})
    html, text = _derive_html_and_text(doc)
    assert html == '<h2>T</h2><p>body</p><img src="x" alt="">'
    assert text == "T\nbody"


# ── 标题应用 ─────────────────────────────────────────────────────────────────


def test_to_heading_and_to_paragraph():
    node = _p("x")
    assert _to_heading(node, level=2) == {
        "type": "heading",
        "attrs": {"level": 2},
        "content": [{"type": "text", "text": "x"}],
    }
    assert _to_paragraph(_h("y")) == {
        "type": "paragraph",
        "content": [{"type": "text", "text": "y"}],
    }


def test_normalize_heading_indices_filters_valid_ints():
    assert _normalize_heading_indices([0, 2, 5, "x", 9], {0, 1, 2, 3}) == {0, 2}
    assert _normalize_heading_indices("nope", {0}) == set()


def test_apply_headings_upgrades_paragraph_preserves_heading():
    doc = _doc(_p("becomes heading"), _h("stays heading"), _p("stays paragraph"))
    result = _apply_headings(doc, heading_indices={0})
    assert result["content"][0]["type"] == "heading"
    assert result["content"][0]["attrs"]["level"] == 2
    # 未选中的已有标题原样保留、不降级
    assert result["content"][1]["type"] == "heading"
    assert result["content"][2]["type"] == "paragraph"
