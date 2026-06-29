"""配图兜底：检查正文图数、不足时从图库随机补图（不做语义匹配）。

挂在 illustrate_one 配图主流程之后，保证"该有图的文章不会图太少"。
纯逻辑函数 + 一个写库函数 + 一个 orchestrator；全部 best-effort，调用方负责吞异常。
只回写 content_json + version（沿用 inserter.py 既定行为，不动 content_html/plain_text）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from server.app.modules.image_library.inserter import insert_images_at_positions
from server.app.modules.image_library.selector import (
    ImageQuery,
    fetch_image_by_id,
    pick_image_id,
)

_logger = logging.getLogger(__name__)


def count_body_images(content_json: dict) -> int:
    """数 Tiptap 顶层 content 数组里 type==image 的节点数。"""
    return sum(
        1
        for node in (content_json.get("content") or [])
        if isinstance(node, dict) and node.get("type") == "image"
    )


def collect_used_stock_image_ids(content_json: dict) -> set[int]:
    """收集正文已用的 stockImageId，用于补图去重。"""
    used: set[int] = set()
    for node in content_json.get("content") or []:
        if isinstance(node, dict) and node.get("type") == "image":
            sid = (node.get("attrs") or {}).get("stockImageId")
            if isinstance(sid, int):
                used.add(sid)
    return used


def _spread_positions(content_json: dict, n: int) -> list[int]:
    """在正文顶层块里均匀挑 n 个插入位（跳过 image 节点本身、跳过紧邻已有 image 的位置）。

    返回的下标语义同 inserter.insert_images_at_positions：表示"在该节点之后插入"。
    候选不足 n 个时返回实际候选；完全没有候选时退化为末尾。
    """
    nodes = content_json.get("content") or []
    total = len(nodes)
    if total == 0 or n <= 0:
        return []
    candidates: list[int] = []
    for i, node in enumerate(nodes):
        if not isinstance(node, dict) or node.get("type") == "image":
            continue
        nxt = nodes[i + 1] if i + 1 < total else None
        if isinstance(nxt, dict) and nxt.get("type") == "image":
            continue
        candidates.append(i)
    if not candidates:
        return [total - 1]
    if n >= len(candidates):
        return candidates
    step = len(candidates) / n
    return [candidates[int(k * step)] for k in range(n)]


def fill_random_images(db: Session, article: Any, *, category_ids: list[int], gap: int) -> int:
    """从 category_ids 随机取 gap 张图（排除正文已用），均匀插入正文。返回实际补入张数。

    best-effort：候选不足时按实际数量补；一张都取不到则返回 0、不改文档。
    """
    content = article.content_json or {}
    excluded = list(collect_used_stock_image_ids(content))
    refs = []
    for _ in range(max(0, gap)):
        img_id = pick_image_id(ImageQuery(category_ids=category_ids, excluded_ids=excluded), db)
        if img_id is None:
            break
        excluded.append(img_id)
        ref = fetch_image_by_id(img_id, db)
        if ref is not None:
            refs.append(ref)
    if not refs:
        return 0
    positions = _spread_positions(content, len(refs))
    article.content_json = insert_images_at_positions(content, refs, positions)
    article.version = (article.version or 0) + 1
    db.commit()
    return len(refs)


def apply_image_fallback(
    *,
    article_id: int,
    requested: int,
    category_ids: list[int],
    max_images: int,
    session_factory: Callable[[], Session],
) -> int:
    """兜底 orchestrator：开独立短 session，按 target 规则决定缺口并随机补足。返回补入张数。

    target = min(max(requested, 1), max_images)；gap = target − 当前正文图数；gap>0 才补。
    """
    if not category_ids:
        return 0
    from server.app.modules.articles.models import Article

    db = session_factory()
    try:
        article = db.get(Article, article_id)
        if article is None or getattr(article, "is_deleted", False):
            return 0
        content = article.content_json or {}
        current = count_body_images(content)
        target = min(max(requested, 1), max_images)
        gap = target - current
        if gap <= 0:
            return 0
        return fill_random_images(db, article, category_ids=category_ids, gap=gap)
    finally:
        db.close()
