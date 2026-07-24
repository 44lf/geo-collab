"""AI 排版的无 IO 文档纯函数。

从 ai_format.py 原样迁出：Tiptap 顶层节点拍平 / 文本抽取 / 游戏名归一化与 heading 定位 /
inline+block → HTML 与纯文本派生 / 段落升级为标题。全部为纯函数（无 LLM / 图片 / DB / 网络），
仅依赖 parser.loads_content_json。语义与原实现逐字节一致，仅换了物理位置。
"""

from __future__ import annotations

import re
from typing import Any

from server.app.modules.articles.parser import loads_content_json


def _top_level_text_nodes(content_json: dict) -> list[tuple[int, dict]]:
    """返回顶层 paragraph/heading 节点及其原始 content 下标。"""
    content = content_json.get("content") or []
    return [
        (i, node)
        for i, node in enumerate(content)
        if isinstance(node, dict) and node.get("type") in ("paragraph", "heading")
    ]


def _non_empty_text_nodes(content_json: dict) -> list[tuple[int, dict]]:
    return [
        (i, node) for i, node in _top_level_text_nodes(content_json) if _node_text(node).strip()
    ]


def has_ai_format_targets(raw_content_json: Any) -> bool:
    """正文里是否有可排版对象（非空的顶层 paragraph/heading 节点）。空正文时 router 拒绝触发排版。"""
    if isinstance(raw_content_json, str):
        content_json = loads_content_json(raw_content_json)
    elif isinstance(raw_content_json, dict):
        content_json = raw_content_json
    else:
        content_json = {}
    return bool(_non_empty_text_nodes(content_json))


def _node_text(node: dict) -> str:
    parts = []
    for child in node.get("content") or []:
        if not isinstance(child, dict):
            continue
        if child.get("type") == "text":
            parts.append(child.get("text", ""))
        elif child.get("type") == "hardBreak":
            parts.append("\n")
    return "".join(parts)


_GAME_PREFIX_RE = re.compile(r"^游戏[0-9一二三四五六七八九十百]+、\s*")
_BRACKET_CHARS = "《》〈〉「」『』\"'“”‘’ 	　"


def _normalize_game_name(s: str) -> str:
    """归一化游戏名/heading 文本：去『游戏N、』前缀、去书名号/引号/空白。用于 contains 匹配。"""
    t = (s or "").strip()
    t = _GAME_PREFIX_RE.sub("", t)
    return t.strip(_BRACKET_CHARS)


def _find_heading_index(content_json: dict, game: str) -> int | None:
    """在顶层 heading 节点里找文本含 game 的，返回其绝对下标；多命中取首个；无则 None。"""
    target = _normalize_game_name(game)
    if not target:
        return None
    for i, node in enumerate(content_json.get("content") or []):
        if not isinstance(node, dict) or node.get("type") != "heading":
            continue
        if target in _normalize_game_name(_node_text(node)):
            return i
    return None


def build_image_positions_from_game_list(
    content_json: dict, game_list: list[dict]
) -> tuple[list[dict], list[dict]]:
    """游戏清单 → (合成 image_positions, unmatched)。game 名为权威锚点，index 仅未命中时兜底。

    - 同一 game 命中多个 heading：取首个。
    - 多个 game 解析到同一 index：按 index 去重，保留先到的，其余记 index_conflict。
    - 命中不到 heading 且无 index 提示：记 heading_not_found。
    """
    positions: list[dict] = []
    unmatched: list[dict] = []
    used_index: set[int] = set()
    for item in game_list or []:
        if not isinstance(item, dict):
            continue
        game = (item.get("game") or "").strip()
        if not game:
            continue
        idx = _find_heading_index(content_json, game)
        if idx is None:
            hint = item.get("index")
            if isinstance(hint, int):
                idx = hint
            else:
                unmatched.append({"game": game, "reason": "heading_not_found"})
                continue
        if idx in used_index:
            unmatched.append({"game": game, "reason": "index_conflict"})
            continue
        used_index.add(idx)
        pos: dict = {"index": idx, "game": game}
        cat = item.get("category_id")
        if isinstance(cat, int):
            pos["category_id"] = cat
        positions.append(pos)
    return positions, unmatched


def _to_heading(node: dict, level: int = 1) -> dict:
    return {"type": "heading", "attrs": {"level": level}, "content": node.get("content", [])}


def _to_paragraph(node: dict) -> dict:
    return {"type": "paragraph", "content": node.get("content", [])}


_INLINE_MARK_TAGS = {
    "bold": ("<strong>", "</strong>"),
    "italic": ("<em>", "</em>"),
    "code": ("<code>", "</code>"),
    "underline": ("<u>", "</u>"),
    "strike": ("<s>", "</s>"),
}


def _inline_html(children: list | None) -> str:
    """渲染一组 inline 子节点（text/hardBreak）为 HTML，保留 marks。与既有风格一致：text 不转义。"""
    parts: list[str] = []
    for child in children or []:
        if not isinstance(child, dict):
            continue
        ctype = child.get("type")
        if ctype == "hardBreak":
            parts.append("<br>")
            continue
        if ctype != "text":
            continue
        text = child.get("text", "")
        for mark in child.get("marks") or []:
            if not isinstance(mark, dict):
                continue
            mtype = mark.get("type")
            if mtype == "link":
                href = (mark.get("attrs") or {}).get("href", "")
                text = f'<a href="{href}">{text}</a>'
            elif mtype in _INLINE_MARK_TAGS:
                open_tag, close_tag = _INLINE_MARK_TAGS[mtype]
                text = f"{open_tag}{text}{close_tag}"
        parts.append(text)
    return "".join(parts)


def _node_html(node: dict) -> str:
    """单个块节点 → HTML。递归处理列表/引用/列表项内的块子节点。"""
    ntype = node.get("type")
    if ntype == "heading":
        level = (node.get("attrs") or {}).get("level", 1)
        return f"<h{level}>{_inline_html(node.get('content'))}</h{level}>"
    if ntype == "paragraph":
        return f"<p>{_inline_html(node.get('content'))}</p>"
    if ntype == "image":
        attrs = node.get("attrs") or {}
        src = attrs.get("src", "")
        alt = attrs.get("alt", "") or ""
        return f'<img src="{src}" alt="{alt}">'
    if ntype in ("bulletList", "orderedList"):
        tag = "ul" if ntype == "bulletList" else "ol"
        items = "".join(_node_html(c) for c in node.get("content") or [] if isinstance(c, dict))
        return f"<{tag}>{items}</{tag}>"
    if ntype == "listItem":
        return f"<li>{''.join(_node_html(c) for c in node.get('content') or [] if isinstance(c, dict))}</li>"
    if ntype == "blockquote":
        return f"<blockquote>{''.join(_node_html(c) for c in node.get('content') or [] if isinstance(c, dict))}</blockquote>"
    if ntype == "codeBlock":
        return f"<pre><code>{_inline_html(node.get('content'))}</code></pre>"
    # 未知块节点：尽量取 inline 文本，不静默吞整块
    return f"<p>{_inline_html(node.get('content'))}</p>"


def _node_plain_text(node: dict) -> str:
    """单个块节点 → 纯文本（递归）。列表项各成一行。"""
    ntype = node.get("type")
    if ntype in ("heading", "paragraph", "codeBlock"):
        return _node_text(node)
    if ntype in ("bulletList", "orderedList", "blockquote", "listItem"):
        lines = [_node_plain_text(c) for c in node.get("content") or [] if isinstance(c, dict)]
        return "\n".join(t for t in lines if t.strip())
    if ntype == "image":
        return ""
    return _node_text(node)


def _derive_html_and_text(content_json: dict) -> tuple[str, str]:
    html_parts: list[str] = []
    text_parts: list[str] = []
    for node in content_json.get("content") or []:
        if not isinstance(node, dict):
            continue
        html_parts.append(_node_html(node))
        t = _node_plain_text(node)
        if t.strip():
            text_parts.append(t)
    return "".join(html_parts), "\n".join(text_parts)


def _normalize_heading_indices(value: Any, valid_indices: set[int]) -> set[int]:
    if not isinstance(value, list):
        return set()
    result: set[int] = set()
    for item in value:
        if isinstance(item, int) and item in valid_indices:
            result.add(item)
    return result


def _apply_headings(content_json: dict, heading_indices: set[int]) -> dict:
    """只把段落升级为标题，绝不降级已有标题。

    LLM 只识别哪些段落应成为标题。未被 LLM 选中的已有标题原样保留：提示词说
    “保留”，所以不在 heading_indices 中并不表示要降级。
    """
    content = list(content_json.get("content") or [])
    for i, node in enumerate(content):
        if not isinstance(node, dict):
            continue
        if i in heading_indices and node.get("type") == "paragraph":
            content[i] = _to_heading(node, level=2)
    return {**content_json, "content": content}
