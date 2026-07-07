"""视频生成 Action/Catalog 工具。

compose_video 之于视频 = save_article 之于文章：Claude 主对话写 storyboard，
GEO 后端确定性跑 TTS+ffmpeg，全程不调 LLM。tool 一律 async + 丢线程池（见 catalog.py docstring）。
"""

from __future__ import annotations

from typing import Any

import anyio

from server.mcp.config import get_config
from server.mcp.http_client import ApiError, GeoApiClient
from server.mcp.server import mcp


def _client() -> GeoApiClient:
    cfg = get_config()
    return GeoApiClient(base_url=cfg.internal_api_url, token=cfg.token, timeout=cfg.timeout_seconds)


def _ok(data: Any) -> dict[str, Any]:
    return {"ok": True, "data": data, "error": None}


def _fail(error: str) -> dict[str, Any]:
    return {"ok": False, "data": None, "error": error}


async def _apost(path: str, *, json: dict[str, Any]) -> dict[str, Any]:
    def _impl() -> dict[str, Any]:
        try:
            return _ok(_client().post(path, json=json))
        except ApiError as exc:
            return _fail(str(exc))

    return await anyio.to_thread.run_sync(_impl)


async def _aget(path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
    def _impl() -> dict[str, Any]:
        try:
            return _ok(_client().get(path, params=params))
        except ApiError as exc:
            return _fail(str(exc))

    return await anyio.to_thread.run_sync(_impl)


@mcp.tool()
async def compose_video(
    article_id: int,
    storyboard: dict[str, Any],
    engine: str | None = None,
    model_label: str | None = None,
) -> dict[str, Any]:
    """Compose a slideshow short-video for one article from a storyboard YOU author.

    This is the **zero-config video path**, mirroring save_article: you (the calling
    Claude Code conversation) write the storyboard yourself — GEO makes NO LLM call.
    It deterministically runs TTS + ffmpeg and stores mp4 + SRT + metadata.

    Workflow:
        1. get_article(article_id) to read the article body.
        2. list_stock_categories() + list_stock_images(category_id) to see candidate images.
        3. Author the storyboard yourself: split the article into shots; per shot write
           `subtitle` (burned on screen) + `narration` (TTS voice) + point at an
           `asset_id` from list_stock_images. Also write a GEO-friendly title/description/tags.
        4. compose_video(article_id, storyboard) → returns job_id.
        5. Poll get_video_status(job_id) until status == "done" (or "failed").

    Args:
        article_id: Target article (must exist).
        storyboard: dict with:
            - title: str (GEO-friendly, keyword-rich)
            - description: str (upload caption)
            - tags: list[str]
            - aspect_ratio: "9:16" (default) | "16:9"
            - bgm: "default" | "none"
            - shots: list of {subtitle: str, narration: str, asset_id: int|null, duration_hint: float|null}
              (at least 1, at most 30). asset_id must come from list_stock_images.
        engine: TTS engine code. None = edge (free, no key). Only set if you know an
            alternative engine is configured server-side.
        model_label: Optional author label for traceability.

    Returns:
        {"ok": True, "data": {"job_id": str, "article_id": int, "status": "pending", ...}, "error": None}
    """
    body: dict[str, Any] = {"article_id": article_id, "storyboard": storyboard}
    if engine:
        body["engine"] = engine
    if model_label:
        body["model_label"] = model_label
    return await _apost("/api/videos/compose", json=body)


@mcp.tool()
async def get_video_status(job_id: str) -> dict[str, Any]:
    """Poll a video job's status and fetch product URLs when done.

    Args:
        job_id: From compose_video.

    Returns:
        {"ok": True, "data": {
            "job_id": str, "article_id": int,
            "status": "pending"|"running"|"done"|"failed", "progress": float,
            "video_url": str|null, "srt_url": str|null,
            "title": str|null, "description": str|null, "tags": [str], "error": str|null
        }, "error": None}
    """
    return await _aget(f"/api/videos/status/{job_id}")


@mcp.tool()
async def list_stock_images(category_id: int, limit: int = 50) -> dict[str, Any]:
    """List concrete images in a stock category so you can point at asset_id in a storyboard.

    Args:
        category_id: From list_stock_categories.
        limit: Max images (1-200).

    Returns:
        {"ok": True, "data": [
            {"asset_id": int, "filename": str, "tags": [str], "url": str, "w": int|null, "h": int|null}
        ], "error": None}
    """
    return await _aget(
        "/api/mcp/stock-images",
        params={"category_id": category_id, "limit": max(1, min(200, limit))},
    )
