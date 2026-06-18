"""只读 Catalog 类工具。

每个 tool 走 `@mcp.tool` 装饰，签名直接做 LLM-facing schema:
- 参数有默认值则在 LLM prompt 里可省
- 返回 dict 顶层 `{ok, data, error}` —— 失败时 data=None, error=str
"""

from __future__ import annotations

from typing import Any

from server.mcp.config import get_config
from server.mcp.http_client import ApiError, GeoApiClient
from server.mcp.server import mcp


def _client() -> GeoApiClient:
    cfg = get_config()
    return GeoApiClient(base_url=cfg.api_base_url, token=cfg.token, timeout=cfg.timeout_seconds)


def _ok(data: Any) -> dict[str, Any]:
    return {"ok": True, "data": data, "error": None}


def _fail(error: str) -> dict[str, Any]:
    return {"ok": False, "data": None, "error": error}


@mcp.tool()
def list_articles(
    status: str | None = None,
    review_status: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """List GEO articles with filters.

    Args:
        status: Article workflow status. Common values: "draft", "ready".
        review_status: Editorial review status. Values: "pending", "approved".
        limit: Max number of articles to return (1-100).

    Returns:
        {"ok": True, "data": {"items": [...], "total": N}, "error": None} on success.
        {"ok": False, "data": None, "error": "<message>"} on failure.
    """
    params: dict[str, Any] = {"limit": max(1, min(100, limit))}
    if status:
        params["status"] = status
    if review_status:
        params["review_status"] = review_status
    try:
        data = _client().get("/api/articles", params=params)
        return _ok(data)
    except ApiError as exc:
        return _fail(str(exc))
