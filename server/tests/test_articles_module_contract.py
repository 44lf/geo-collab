"""Articles 模块结构拆分的黄金基线（characterization test）。

配套计划：docs/plans/2026-07-13-articles-module-decomposition.md（Task 0）。

目的：在把 router.py / service.py 按职责拆分**之前**，把 5 个 APIRouter 的完整路由契约
（相对 path / methods / name / status_code / response_model / 依赖调用名）与兼容导入入口
冻结成快照。Task 1 之后（端点搬到 routers/*.py、旧 router.py 变 facade）此测试必须**原样通过**——
任何签名漂移（漏挂路由、状态码变、response_model 掉成 None、鉴权依赖变位）都会让它变红。

注意：
- 这里断言的是**路由自身**携带的相对路径与依赖；`/api/articles` 等 prefix 与 include 时统一附加的
  JWT 依赖挂在 `main.py`（本计划不改 main.py），不在本快照范围内，由 §8.4 人工核对守。
- 大多数 JWT 端点在函数签名里显式 `Depends(get_current_user)`，故其内联可见；少数 asset 文件服务
  路由（read_asset_file / meta / thumbnail）只有 `get_db`，靠 include-time 依赖兜底——这是当前事实，
  如实冻结即可，不要为"补齐鉴权"往路由上加依赖（那才是真改了鉴权位置）。
- 依赖 DB：session.py 在 import 期即建引擎、要求 GEO_DATABASE_URL/GEO_DATA_DIR。故统一走
  build_test_app（它设好 env），并把 Router 的 import 放在函数体内、全部打 @pytest.mark.mysql。
"""

import pytest

from server.tests.utils import build_test_app


def _call_name(call) -> str | None:
    """依赖对应的可调用对象标识：<模块末段>.<qualname>，如 'security.get_current_user'。"""
    if call is None:
        return None
    return f"{call.__module__.split('.')[-1]}.{call.__qualname__}"


def _route_signature(router) -> set:
    """锁住 path/methods/name + status_code/response_model/依赖——只锁前三项不够（见计划 §3.2/§3.3）。"""
    return {
        (
            route.path,
            tuple(sorted(route.methods or [])),
            route.name,
            route.status_code,
            getattr(route.response_model, "__name__", None),
            tuple(
                _call_name(dep.call) for dep in route.dependant.dependencies if dep.call is not None
            ),
        )
        for route in router.routes
    }


# ── 移动前实测冻结（geo_xzpt + 真库内省，2026-07-13）───────────────────────────
EXPECTED_SIGNATURES: dict[str, set] = {
    "articles_router": {
        (
            "",
            ("GET",),
            "read_articles",
            None,
            "list",
            ("session.get_db", "security.get_current_user"),
        ),
        (
            "",
            ("POST",),
            "create_article_endpoint",
            None,
            "ArticleRead",
            ("session.get_db", "security.get_current_user"),
        ),
        (
            "/feed",
            ("GET",),
            "read_article_feed",
            None,
            "ArticleFeedResponse",
            ("session.get_db", "security.get_current_user"),
        ),
        (
            "/{article_id}",
            ("DELETE",),
            "delete_article_endpoint",
            204,
            None,
            ("session.get_db", "security.get_current_user"),
        ),
        (
            "/{article_id}",
            ("GET",),
            "read_article",
            None,
            "ArticleRead",
            ("session.get_db", "security.get_current_user"),
        ),
        (
            "/{article_id}",
            ("PUT",),
            "update_article_endpoint",
            None,
            "ArticleRead",
            ("session.get_db", "security.get_current_user"),
        ),
        (
            "/{article_id}/ai-format",
            ("POST",),
            "trigger_ai_format_endpoint",
            202,
            "dict",
            ("session.get_db", "security.get_current_user"),
        ),
        (
            "/{article_id}/approve",
            ("POST",),
            "approve_article_endpoint",
            None,
            "ArticleRead",
            ("session.get_db", "security.get_current_user"),
        ),
        (
            "/{article_id}/cover",
            ("POST",),
            "update_article_cover",
            None,
            "ArticleRead",
            ("session.get_db", "security.get_current_user"),
        ),
        (
            "/{article_id}/revoke-approval",
            ("POST",),
            "revoke_article_approval_endpoint",
            None,
            "ArticleRead",
            ("session.get_db", "security.get_current_user"),
        ),
    },
    "article_groups_router": {
        (
            "",
            ("GET",),
            "read_groups",
            None,
            "list",
            ("session.get_db", "security.get_current_user"),
        ),
        (
            "",
            ("POST",),
            "create_group_endpoint",
            None,
            "ArticleGroupRead",
            ("session.get_db", "security.get_current_user"),
        ),
        (
            "/{group_id}",
            ("DELETE",),
            "delete_group_endpoint",
            204,
            None,
            ("session.get_db", "security.get_current_user"),
        ),
        (
            "/{group_id}",
            ("GET",),
            "read_group",
            None,
            "ArticleGroupRead",
            ("session.get_db", "security.get_current_user"),
        ),
        (
            "/{group_id}",
            ("PUT",),
            "update_group_endpoint",
            None,
            "ArticleGroupRead",
            ("session.get_db", "security.get_current_user"),
        ),
        (
            "/{group_id}/approve-all",
            ("POST",),
            "approve_group_endpoint",
            None,
            "ArticleGroupRead",
            ("session.get_db", "security.get_current_user"),
        ),
        (
            "/{group_id}/items",
            ("PUT",),
            "update_group_items",
            None,
            "ArticleGroupRead",
            ("session.get_db", "security.get_current_user"),
        ),
    },
    "assets_router": {
        (
            "",
            ("POST",),
            "upload_asset",
            None,
            "AssetRead",
            ("session.get_db", "security.get_current_user"),
        ),
        (
            "/cleanup-orphans",
            ("POST",),
            "cleanup_orphan_assets",
            None,
            "dict",
            ("session.get_db", "security.require_admin"),
        ),
        (
            "/stats",
            ("GET",),
            "asset_stats",
            None,
            "dict",
            ("session.get_db", "security.require_admin"),
        ),
        ("/{asset_id}", ("GET",), "read_asset_file", None, None, ("session.get_db",)),
        ("/{asset_id}/meta", ("GET",), "read_asset_meta", None, "AssetRead", ("session.get_db",)),
        (
            "/{asset_id}/thumbnail",
            ("GET",),
            "read_asset_thumbnail",
            None,
            None,
            ("session.get_db",),
        ),
    },
    "chunked_assets_router": {
        (
            "/upload-chunk/{upload_id}",
            ("POST",),
            "upload_chunk",
            None,
            "dict",
            ("session.get_db", "security.get_current_user"),
        ),
        (
            "/upload-complete/{upload_id}",
            ("POST",),
            "complete_chunked_upload",
            None,
            "dict",
            ("session.get_db", "security.get_current_user"),
        ),
        (
            "/upload-start",
            ("POST",),
            "start_chunked_upload",
            None,
            "dict",
            ("session.get_db", "security.get_current_user"),
        ),
        (
            "/upload-status/{upload_id}",
            ("POST",),
            "get_upload_status",
            None,
            "dict",
            ("session.get_db", "security.get_current_user"),
        ),
    },
    "articles_mcp_router": {
        (
            "/save-from-mcp",
            ("POST",),
            "save_article_from_mcp",
            None,
            "SaveArticleFromMcpResponse",
            ("mcp_auth.require_mcp_token", "session.get_db"),
        ),
        (
            "/{article_id}/ai-illustrate",
            ("POST",),
            "ai_illustrate_article_mcp",
            None,
            "AiIllustrateResponse",
            ("mcp_auth.require_mcp_token",),
        ),
        (
            "/{article_id}/illustrate",
            ("POST",),
            "illustrate_article_mcp",
            None,
            "IllustrateResponse",
            ("mcp_auth.require_mcp_token", "session.get_db"),
        ),
        (
            "/{article_id}/review-card",
            ("POST",),
            "post_review_card",
            None,
            "ReviewCardResponse",
            ("mcp_auth.require_mcp_token", "session.get_db"),
        ),
        (
            "/{article_id}/set-review-status",
            ("POST",),
            "set_review_status_mcp",
            None,
            "SetReviewStatusResponse",
            ("mcp_auth.require_mcp_token", "session.get_db"),
        ),
    },
}


def _load_routers():
    """在 build_test_app 备妥 env 后，函数体内导入（顶层导入会因 session.py import 期建引擎而 RuntimeError）。"""
    from server.app.modules.articles.router import (
        article_groups_router,
        articles_mcp_router,
        articles_router,
        assets_router,
        chunked_assets_router,
    )

    return {
        "articles_router": articles_router,
        "article_groups_router": article_groups_router,
        "assets_router": assets_router,
        "chunked_assets_router": chunked_assets_router,
        "articles_mcp_router": articles_mcp_router,
    }


@pytest.mark.mysql
def test_articles_router_contract_is_frozen(monkeypatch):
    """5 个 Router 的完整签名集必须与移动前快照逐项一致（含 status_code / response_model / 依赖）。"""
    test_app = build_test_app(monkeypatch)
    try:
        routers = _load_routers()
        for name, router in routers.items():
            actual = _route_signature(router)
            expected = EXPECTED_SIGNATURES[name]
            missing = expected - actual
            extra = actual - expected
            assert not missing, f"{name} 少了/改了路由（相对移动前快照）：{sorted(missing)}"
            assert not extra, f"{name} 多了/改了路由（相对移动前快照）：{sorted(extra)}"
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_security_critical_route_invariants(monkeypatch):
    """可读的关键不变量：状态码 + MCP token 鉴权，防止拆分时被悄悄改掉。"""
    test_app = build_test_app(monkeypatch)
    try:
        routers = _load_routers()

        def _find(router, path, method):
            for route in router.routes:
                if route.path == path and method in (route.methods or set()):
                    return route
            raise AssertionError(f"路由不存在：{method} {path}")

        # 无内容响应必须保持 204
        assert _find(routers["articles_router"], "/{article_id}", "DELETE").status_code == 204
        assert _find(routers["article_groups_router"], "/{group_id}", "DELETE").status_code == 204
        # 异步触发必须保持 202
        assert (
            _find(routers["articles_router"], "/{article_id}/ai-format", "POST").status_code == 202
        )

        # 5 个 MCP 端点必须都挂 require_mcp_token（拆到 routers/mcp.py 后不能掉）
        for route in routers["articles_mcp_router"].routes:
            dep_names = {
                _call_name(d.call) for d in route.dependant.dependencies if d.call is not None
            }
            assert "mcp_auth.require_mcp_token" in dep_names, (
                f"MCP 路由缺 require_mcp_token：{route.path}"
            )
        # 且 MCP 路由不得误挂 JWT 的 get_current_user
        for route in routers["articles_mcp_router"].routes:
            dep_names = {
                _call_name(d.call) for d in route.dependant.dependencies if d.call is not None
            }
            assert "security.get_current_user" not in dep_names, f"MCP 路由误挂了 JWT：{route.path}"
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_articles_public_imports_remain_available(monkeypatch):
    """兼容导入 smoke test：拆分后这些旧路径仍须可用（router / service / 包入口）。"""
    test_app = build_test_app(monkeypatch)
    try:
        from server.app.modules.articles import store_bytes
        from server.app.modules.articles.router import articles_router
        from server.app.modules.articles.service import create_article, get_article

        assert articles_router is not None
        assert callable(create_article)
        assert callable(get_article)
        assert callable(store_bytes)
    finally:
        test_app.cleanup()
