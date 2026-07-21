"""小红书卡片渲染 MCP 工具（zero-config：主对话写 render-markdown，GEO 后端 Playwright 渲染）。

compose_xhs_cards 之于小红书卡片 = compose_video 之于视频：Claude 主对话写
render-markdown，GEO 后端确定性跑 Playwright 截图 + 存 MinIO，全程不调 LLM。
tool 一律 async + 丢线程池（见 catalog.py docstring）。
"""

from __future__ import annotations

import os
from typing import Any

import anyio

from server.mcp.config import get_config
from server.mcp.http_client import ApiError, GeoApiClient
from server.mcp.server import mcp

# 与 action.py 的 _OPERATOR_USER_ID 同源（同一环境变量），但不跨 module import 那个私有常量：
# server.py 顶部先 import action 再 import xhs，若这里反向 `from action import _OPERATOR_USER_ID`
# 会在 action.py 自身的 `from server.mcp.server import mcp` 尚未跑完时形成循环 import
# （直接 `import server.mcp.tools.action` 的测试会踩中 partially initialized module 报错）。
_OPERATOR_USER_ID = int(os.environ.get("GEO_MCP_OPERATOR_USER_ID", "1"))


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


@mcp.tool()
async def save_xhs_note(
    source_article_id: int,
    prompt_template_id: int,
    title: str,
    markdown_content: str,
    model_label: str | None = None,
) -> dict[str, Any]:
    """Persist a rendered Xiaohongshu image-text note into GEO's review queue (pending).

    Use AFTER get_xhs_status returns done. Assemble markdown_content as:
    cover image + each card image (as ![](/api/xhs-cards/file/...) links) + the
    Xiaohongshu copy text (title / body / SEO #tags) at the end. Lands review_status=pending
    with content_type="xhs_image_text" so the content list badges it as 小红书图文.

    Args:
        source_article_id: the approved article this note derives from.
        prompt_template_id: the template used to condense (list_prompt_templates).
        title: note title (<=300 chars).
        markdown_content: image links + copy text (see above).
        model_label: optional writer label.

    Returns:
        {"ok": True, "data": {"article_id": N}, "error": None}
    """
    body: dict[str, Any] = {
        "prompt_template_id": prompt_template_id,
        "user_id": _OPERATOR_USER_ID,
        "title": title,
        "markdown_content": markdown_content,
        "content_type": "xhs_image_text",
        "source_article_id": source_article_id,
    }
    if model_label:
        body["model_label"] = model_label
    return await _apost("/api/articles/save-from-mcp", json=body)
