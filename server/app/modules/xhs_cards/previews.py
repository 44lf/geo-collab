"""小红书主题预览：固定示例渲染 8 主题封面+正文卡，缓存 MinIO；懒生成。

不碰 DB，不调 LLM。主题真源 = render.AVAILABLE_THEMES。
"""

from __future__ import annotations

import asyncio
import logging
import threading

from server.app.modules.xhs_cards import render, store

logger = logging.getLogger(__name__)

PREVIEW_PREFIX = "theme-previews"

# 固定示例文案（frontmatter 出封面；正文出一张卡）。
PREVIEW_SAMPLE_MD = """---
emoji: "🍜"
title: "3步搞定红烧肉"
subtitle: "新手也能零失败"
---

# 选肉有讲究 🥩

五花肉三层分明最好，切成麻将块大小，肥瘦相间才够香。

#红烧肉 #家常菜 #新手下厨
"""

THEME_LABELS = {
    "sketch": "手绘素描",
    "default": "默认简约",
    "playful-geometric": "活泼几何",
    "neo-brutalism": "新粗野主义",
    "botanical": "植物园自然",
    "professional": "专业商务",
    "retro": "复古怀旧",
    "terminal": "终端命令行",
}

_lock = threading.Lock()
_generating = False


def preview_keys(theme: str) -> tuple[str, str]:
    return f"{PREVIEW_PREFIX}/{theme}/cover.png", f"{PREVIEW_PREFIX}/{theme}/card.png"


def _preview_urls(theme: str) -> tuple[str, str]:
    return (
        f"/api/xhs-cards/themes/{theme}/preview/cover",
        f"/api/xhs-cards/themes/{theme}/preview/card",
    )


def list_theme_previews() -> list[dict]:
    rows: list[dict] = []
    for theme in render.AVAILABLE_THEMES:
        cover_key, card_key = preview_keys(theme)
        cached = store.object_exists(cover_key) and store.object_exists(card_key)
        cover_url, card_url = _preview_urls(theme)
        rows.append(
            {
                "name": theme,
                "label": THEME_LABELS.get(theme, theme),
                "cover_url": cover_url,
                "card_url": card_url,
                "cached": cached,
            }
        )
    return rows


def get_preview_bytes(theme: str, kind: str) -> bytes | None:
    if theme not in render.AVAILABLE_THEMES or kind not in ("cover", "card"):
        return None
    cover_key, card_key = preview_keys(theme)
    key = cover_key if kind == "cover" else card_key
    if not store.object_exists(key):
        return None
    return store.get_object(key)


def regenerate_all_previews() -> None:
    """同步渲染全部主题并写 MinIO。单个主题失败跳过、不整体失败。"""
    store.ensure_bucket()
    for theme in render.AVAILABLE_THEMES:
        try:
            result = asyncio.run(
                render.render_markdown_to_card_bytes(
                    PREVIEW_SAMPLE_MD, theme=theme, mode="separator"
                )
            )
            cover_key, card_key = preview_keys(theme)
            store.put_png(cover_key, result["cover"])
            if result["cards"]:
                store.put_png(card_key, result["cards"][0])
        except Exception:  # noqa: BLE001 — 单主题失败不拖累其它
            logger.exception("主题预览渲染失败: theme=%s", theme)


def is_generating() -> bool:
    return _generating


def _run_regenerate() -> None:
    global _generating
    try:
        regenerate_all_previews()
    finally:
        with _lock:
            _generating = False


def spawn_regenerate() -> bool:
    """启动一轮后台重生。已有在跑 → 返回 False（幂等）。"""
    global _generating
    with _lock:
        if _generating:
            return False
        _generating = True
    threading.Thread(target=_run_regenerate, daemon=True).start()
    return True
