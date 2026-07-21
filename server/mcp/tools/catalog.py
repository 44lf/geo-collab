"""只读 Catalog 类工具。

每个 tool 走 `@mcp.tool` 装饰，签名直接做 LLM-facing schema:
- 参数有默认值则在 LLM prompt 里可省
- 返回 dict 顶层 `{ok, data, error}` —— 失败时 data=None, error=str

tool 一律声明为 `async def`：FastMCP 对同步 tool 是在事件循环里 inline 跑的
(func_metadata.call_fn_with_arg_validation 的同步分支 `return fn(...)`)。HTTP-mount 下
self-call 打的是同进程同一个单 worker uvicorn(127.0.0.1:8000)，同步阻塞会把事件循环
锁死、self-call 永远等不到处理 → 死锁直到超时。改 async + 把阻塞 HTTP 调用经
`anyio.to_thread.run_sync` 丢线程池，事件循环就空出来服务 self-call。详见 _aget。
"""

from __future__ import annotations

from typing import Any

import anyio

from server.mcp.config import get_config
from server.mcp.http_client import ApiError, GeoApiClient
from server.mcp.server import mcp


def _client() -> GeoApiClient:
    cfg = get_config()
    return GeoApiClient(
        base_url=cfg.internal_api_url,
        token=cfg.token,
        timeout=cfg.timeout_seconds,
    )


def _ok(data: Any) -> dict[str, Any]:
    return {"ok": True, "data": data, "error": None}


def _fail(error: str) -> dict[str, Any]:
    return {"ok": False, "data": None, "error": error}


async def _aget(path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """同步 GET 丢线程池跑，避免阻塞事件循环（见模块 docstring 的自调用死锁说明）。"""

    def _impl() -> dict[str, Any]:
        try:
            return _ok(_client().get(path, params=params))
        except ApiError as exc:
            return _fail(str(exc))

    return await anyio.to_thread.run_sync(_impl)


async def _apost(path: str, *, json: dict[str, Any] | None = None) -> dict[str, Any]:
    """同步 POST 丢线程池跑，避免阻塞事件循环（见模块 docstring 的自调用死锁说明）。"""

    def _impl() -> dict[str, Any]:
        try:
            return _ok(_client().post(path, json=json))
        except ApiError as exc:
            return _fail(str(exc))

    return await anyio.to_thread.run_sync(_impl)


@mcp.tool()
async def list_articles(
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
    return await _aget("/api/mcp/articles", params=params)


@mcp.tool()
async def list_question_pools() -> dict[str, Any]:
    """List all question pools (Feishu-synced topic libraries)."""
    return await _aget("/api/mcp/question-pools")


@mcp.tool()
async def list_question_items(
    pool_id: int,
    limit: int = 20,
    category: str | None = None,
) -> dict[str, Any]:
    """List question items within a pool, optionally filtered by category.

    Args:
        pool_id: Question pool id (from list_question_pools).
        limit: Max items to return (1-100).
        category: Optional category filter (e.g. "未分类" / specific category name).
    """
    params: dict[str, Any] = {"limit": max(1, min(100, limit))}
    if category:
        params["category"] = category
    return await _aget(f"/api/mcp/question-pools/{pool_id}/items", params=params)


@mcp.tool()
async def list_prompt_templates(scope: str = "generation") -> dict[str, Any]:
    """List prompt templates filtered by scope.

    Args:
        scope: One of "generation", "ai_format", "image_search", "image_companion".
               "generation" = article writing prompts (most common for Loops).
    """
    return await _aget("/api/mcp/prompt-templates", params={"scope": scope})


@mcp.tool()
async def list_pipelines(type_filter: str | None = None) -> dict[str, Any]:
    """List all pipelines (智能体 / workflows).

    Args:
        type_filter: Optional pipeline type filter (e.g. "agent" / "workflow").
    """
    params: dict[str, Any] = {}
    if type_filter:
        params["type"] = type_filter
    return await _aget("/api/mcp/pipelines", params=params or None)


@mcp.tool()
async def list_accounts(
    platform_code: str | None = None,
    distribution_enabled: bool | None = None,
) -> dict[str, Any]:
    """List publishing accounts.

    Args:
        platform_code: Filter by platform (e.g. "toutiao", "wechat_mp").
        distribution_enabled: If true, only accounts available for distribution.
    """
    params: dict[str, Any] = {}
    if platform_code:
        params["platform_code"] = platform_code
    if distribution_enabled is not None:
        params["distribution_enabled"] = str(distribution_enabled).lower()
    return await _aget("/api/mcp/accounts", params=params or None)


@mcp.tool()
async def get_article(article_id: int) -> dict[str, Any]:
    """Get one article by id, including full content_json / content_html / plain_text."""
    return await _aget(f"/api/mcp/articles/{article_id}")


@mcp.tool()
async def list_today_loop_articles(
    decided_by: str = "claude-goal-verifier",
    decision: str = "approved",
    since_hours: int = 24,
    model_label: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Count + list articles that the /goal loop wrote and verifier decided on,
    within a rolling time window.

    Used by the /goal orchestrator as the source-of-truth stop condition,
    independent of the writer subagent's self-report.

    Args:
        decided_by: AutoReviewDecision.decided_by filter. Default
            "claude-goal-verifier" matches the verifier skill convention.
        decision: AutoReviewDecision.decision filter. Default "approved".
        since_hours: Window length in hours. Default 24, cap 168 (1 week).
        model_label: Optional. If supplied, also filter
            Article.metrics.writer_model == model_label.
        limit: Max items in returned list. Default 50, cap 200.

    Returns:
        {"ok": True, "data": {"count": int, "items": [...]}, "error": None}
        on success. items: [{article_id, title, decided_at, score_total}].
    """
    params: dict[str, Any] = {
        "decided_by": decided_by,
        "decision": decision,
        "since_hours": max(1, min(168, since_hours)),
        "limit": max(1, min(200, limit)),
    }
    if model_label:
        params["model_label"] = model_label
    return await _aget("/api/articles/today-loop-decisions", params=params)


@mcp.tool()
async def list_stock_categories(
    kind: str | None = None,
) -> dict[str, Any]:
    """List stock image library categories (image buckets) — for /goal Loop onboarding.

    Use this when the user needs to find a `main_category_id` to fill into
    their writer SKILL.md matrix section. Show the returned list to the user
    so they can pick the one matching their content matrix (e.g. "餐厅养成记").

    Args:
        kind: Filter by category kind. Common values:
            - "main": 主推栏目 (one per content matrix — what writer skill picks)
            - "companion": 陪衬栏目 (AI auto-detects across all of them)
            - None (default): return all categories

    Returns:
        {"ok": True, "data": [
            {
                "id": int,
                "name": str,           # e.g. "餐厅养成记"
                "kind": str,           # "main" | "companion"
                "description": str | None,
                "official_url": str | None,
                "image_count": int,    # total images in this bucket
            },
            ...
        ], "error": None}
    """
    params: dict[str, Any] = {}
    if kind:
        params["kind"] = kind
    return await _aget("/api/mcp/stock-categories", params=params or None)


@mcp.tool()
async def list_skills(category: str | None = None) -> dict[str, Any]:
    """List installable skill packages in GEO's Skill library.

    Each entry is one installable package (a Skill record). Use its `slug`
    with install_loop_skills(slug=...) to install it. `units` lists the
    SKILL.md sub-skills the package expands into under .claude/skills/.

    Args:
        category: Optional business-category filter — one of
            "generation" / "distribute" / "video" / "general".

    Returns:
        {"ok": True, "data": {"skills": [{id, slug, name, category,
         is_official, current_version_label, file_count, total_bytes,
         units:[str]}]}, "error": None}
    """
    params: dict[str, Any] = {}
    if category:
        params["category"] = category
    return await _aget("/api/mcp/skills/catalog", params=params or None)


@mcp.tool()
async def pick_quality_references(
    category: str | None = None, k: int | None = None
) -> dict[str, Any]:
    """取 1~k 篇同类高质量参考（服务端优先 external、随机、正文截断），供对抗判分对比。"""
    params: dict[str, Any] = {"category": category}
    if k is not None:
        params["k"] = k
    return await _aget("/api/quality-reference/pick", params=params)


@mcp.tool()
async def search_articles_by_title(
    title: str,
    review_status: str = "approved",
    limit: int = 20,
) -> dict[str, Any]:
    """按标题关键词搜自有（站内）文章，主要用于挑一篇已审文章去 adopt_quality_reference 入高质量库。

    Args:
        title: 标题关键词（子串匹配，只搜标题）。
        review_status: 默认 "approved"（只回能直接采纳的候选）；"pending" / "draft" 搜对应状态，
            "all" 搜全部状态。
        limit: 1–100，默认 20。

    Returns:
        {"ok": True, "data": {"items": [
            {"id", "title", "review_status", "word_count",
             "already_adopted": bool, "snippet": str, "created_at": str}
        ]}, "error": None}

    典型用法：先本工具拿到 id → 再 adopt_quality_reference(article_id=id) 入高质量库。
    already_adopted=True 表示该文已在库、无需重复采纳。
    """
    params: dict[str, Any] = {
        "title": title,
        "review_status": review_status,
        "limit": max(1, min(100, limit)),
    }
    return await _aget("/api/mcp/articles/search", params=params)


@mcp.tool()
async def list_game_tags(limit: int = 200) -> dict[str, Any]:
    """List available game tags (with game_count) to pick topical取材 tags from.

    Use before query_games_by_tags so you choose real tags, not invented ones.
    """
    return await _aget("/api/mcp/game-library/tags", params={"limit": max(1, min(1000, limit))})


@mcp.tool()
async def query_games_by_tags(
    relevant_tags: list[str],
    diversity_tags: list[str] | None = None,
    exclude_tags: list[str] | None = None,
    min_score: float | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Query real games from the library by tags, for on-topic取材.

    relevant_tags gate admission (a game must match at least one). diversity_tags
    only diversify ordering — they never admit off-topic games. Threshold checks
    (>=N to skip WebSearch) count only the relevant-matched pool.
    """
    body: dict[str, Any] = {
        "relevant_tags": relevant_tags,
        "limit": max(1, min(100, limit)),
    }
    if diversity_tags:
        body["diversity_tags"] = diversity_tags
    if exclude_tags:
        body["exclude_tags"] = exclude_tags
    if min_score is not None:
        body["min_score"] = min_score
    return await _apost("/api/mcp/game-library/query", json=body)
