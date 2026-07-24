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

# 预览缓存版本。渲染输出变化（改示例文案 / 修 emoji 字体 / 改主题 CSS）时 bump 它，
# 缓存 key 随之改变 → 旧缓存被绕过、启动预热自动重渲全新图。
# v2：修 emoji 字体后启用（旧的无版本路径 theme-previews/{theme}/… = 隐式 v1，其豆腐块图弃用）。
PREVIEW_VERSION = "v2"

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
    base = f"{PREVIEW_PREFIX}/{PREVIEW_VERSION}/{theme}"
    return f"{base}/cover.png", f"{base}/card.png"


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
    try:
        threading.Thread(target=_run_regenerate, daemon=True).start()
    except Exception:  # noqa: BLE001 — 线程起不来（极罕见）也别把 _generating 卡死成 True
        with _lock:
            _generating = False
        raise
    return True


def preview_gallery_state() -> dict:
    """样式库一次取全：主题预览列表 + 是否正在生成。前端据 generating 决定轮询/引导。"""
    return {"themes": list_theme_previews(), "generating": is_generating()}


def ensure_prewarmed() -> bool:
    """启动预热：当前版本缓存不全就后台生成。全齐则不动。返回是否启动了本轮。

    幂等靠 spawn_regenerate 的单飞锁；best-effort，失败静默（不阻塞启动）。
    """
    try:
        if all(row["cached"] for row in list_theme_previews()):
            return False
        return spawn_regenerate()
    except Exception:  # noqa: BLE001 — 预热失败不能拖垮 app 启动
        logger.exception("样式库预览预热失败")
        return False
