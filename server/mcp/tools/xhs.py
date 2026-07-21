"""小红书卡片渲染 MCP 工具（zero-config：主对话写 render-markdown，GEO 后端 Playwright 渲染）。

compose_xhs_cards 之于小红书卡片 = compose_video 之于视频：Claude 主对话写
render-markdown，GEO 后端确定性跑 Playwright 截图 + 存 MinIO，全程不调 LLM。
tool 一律 async + 丢线程池（见 catalog.py docstring）。
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


async def _aget(path: str) -> dict[str, Any]:
    def _impl() -> dict[str, Any]:
        try:
            return _ok(_client().get(path))
        except ApiError as exc:
            return _fail(str(exc))

    return await anyio.to_thread.run_sync(_impl)


@mcp.tool()
async def compose_xhs_cards(
    render_markdown: str,
    theme: str | None = None,
    mode: str | None = None,
    width: int | None = None,
    dpr: int | None = None,
    source_article_id: int | None = None,
) -> dict[str, Any]:
    """Render Xiaohongshu (Redbook) image cards from a render-markdown YOU author.

    Zero-config, mirroring compose_video: you write the render-markdown (YAML frontmatter
    `emoji/title/subtitle` + body, `---` to split cards), GEO renders cover + card PNGs
    via headless Chromium and stores them to MinIO. NO LLM call.

    Workflow:
        1. get_article(source_article_id) to read the approved article.
        2. Condense it into Xiaohongshu style per the chosen prompt template.
        3. Build render-markdown: frontmatter (emoji/title<=15/subtitle<=15) + body,
           use `---` between cards.
        4. compose_xhs_cards(render_markdown, theme, mode, source_article_id) -> job_id.
        5. Poll get_xhs_status(job_id) until status == "done", then read cover_url + card_urls.
        6. Assemble article markdown embedding those image URLs and call save_xhs_note.

    Args:
        render_markdown: frontmatter + body markdown (see above).
        theme: one of sketch/default/playful-geometric/neo-brutalism/botanical/
            professional/retro/terminal. None -> sketch.
        mode: separator/auto-split/auto-fit/dynamic. None -> separator. (MVP renders
            separator semantics; use `---` to control paging.)
        width: card width px (default 1080). dpr: 1-3 (default 2).
        source_article_id: the approved article this is derived from (traceability).

    Returns:
        {"ok": True, "data": {"job_id": str, "status": "pending", ...}, "error": None}
    """
    body: dict[str, Any] = {"render_markdown": render_markdown}
    if theme:
        body["theme"] = theme
    if mode:
        body["mode"] = mode
    if width:
        body["width"] = width
    if dpr:
        body["dpr"] = dpr
    if source_article_id:
        body["source_article_id"] = source_article_id
    return await _apost("/api/xhs-cards/compose", json=body)


@mcp.tool()
async def get_xhs_status(job_id: str) -> dict[str, Any]:
    """Poll an xhs render job; when done returns cover_url + card_urls (MinIO-backed).

    Returns:
        {"ok": True, "data": {"job_id": str, "status": "pending|running|done|failed",
         "cover_url": str|null, "card_urls": [str], "error": str|null}, "error": None}
    """
    return await _aget(f"/api/xhs-cards/status/{job_id}")
