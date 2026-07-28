"""[MCP] /api/mcp/* catalog 端点：MCP token 鉴权 + 基本返回形态。

覆盖：
- 不带 / 错 token → 401（每个端点）
- 带正确 token → 200 + JSON list（空 DB 也能返回 []）
"""

from __future__ import annotations

import asyncio

import httpx
import pytest


def test_list_question_items_tool_schema_preserves_public_signature():
    from server.mcp.server import mcp

    assert mcp._tool_manager._tools["list_question_pools"].parameters["properties"] == {}

    schema = mcp._tool_manager._tools["list_question_items"].parameters
    properties = schema["properties"]

    assert set(properties) == {"pool_id", "limit", "category"}
    assert properties["limit"]["default"] == 20


@pytest.mark.mysql
def test_nonempty_question_pool_serializes_for_user_and_mcp_http(monkeypatch):
    """同一个真实非空池在 user HTTP 与 MCP-token HTTP 都返回完整 DTO。"""
    from server.app.modules.ai_generation.models import QuestionItem, QuestionPool
    from server.app.modules.ai_generation.schemas import QuestionPoolRead
    from server.tests.utils import build_test_app

    test_app = build_test_app(monkeypatch)
    try:
        monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
        from server.app.core import config

        config.get_settings.cache_clear()
        with test_app.session_factory() as db:
            pool = QuestionPool(
                user_id=test_app.admin_id,
                name="nonempty-pool",
                auto_sync_enabled=False,
            )
            db.add(pool)
            db.flush()
            db.add(
                QuestionItem(
                    pool_id=pool.id,
                    record_id="nonempty-question",
                    fields={},
                    question_text="真实问题",
                    source_active=True,
                )
            )
            db.commit()
            pool_id = pool.id

        user_response = test_app.client.get("/api/generation/question-pools")
        assert user_response.status_code == 200, user_response.text
        user_row = next(row for row in user_response.json() if row["id"] == pool_id)
        assert QuestionPoolRead.model_validate(user_row).auto_sync_enabled is False
        assert user_row["pending_count"] == 1

        mcp_response = test_app.client.get(
            "/api/mcp/question-pools",
            headers={"X-MCP-Token": "secret"},
        )
        assert mcp_response.status_code == 200, mcp_response.text
        mcp_row = next(row for row in mcp_response.json() if row["id"] == pool_id)
        assert QuestionPoolRead.model_validate(mcp_row).auto_sync_enabled is False
        assert mcp_row["pending_count"] == 1
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_fastmcp_list_question_pools_uses_real_geo_api_client_and_http(monkeypatch):
    """FastMCP tool -> GeoApiClient -> mounted FastAPI route 的非空池回归。"""
    from server.app.modules.ai_generation.models import QuestionItem, QuestionPool
    from server.app.modules.ai_generation.schemas import QuestionPoolRead
    from server.mcp.http_client import GeoApiClient
    from server.mcp.server import mcp
    from server.mcp.tools import catalog
    from server.tests.utils import build_test_app

    test_app = build_test_app(monkeypatch)
    try:
        monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
        from server.app.core import config

        config.get_settings.cache_clear()
        with test_app.session_factory() as db:
            pool = QuestionPool(
                user_id=test_app.admin_id,
                name="fastmcp-nonempty",
                auto_sync_enabled=True,
            )
            db.add(pool)
            db.flush()
            db.add(
                QuestionItem(
                    pool_id=pool.id,
                    record_id="fastmcp-question",
                    fields={},
                    question_text="FastMCP 问题",
                    source_active=True,
                )
            )
            db.commit()
            pool_id = pool.id

        def forward_to_test_app(request: httpx.Request) -> httpx.Response:
            response = test_app.client.request(
                request.method,
                request.url.path,
                headers=dict(request.headers),
            )
            return httpx.Response(
                status_code=response.status_code,
                headers=response.headers,
                content=response.content,
                request=request,
            )

        transport = httpx.MockTransport(forward_to_test_app)
        monkeypatch.setattr(
            catalog,
            "_client",
            lambda: GeoApiClient(
                base_url="http://testserver",
                token="secret",
                transport=transport,
            ),
        )

        result = asyncio.run(mcp._tool_manager._tools["list_question_pools"].fn())

        assert result["ok"] is True, result
        row = next(item for item in result["data"] if item["id"] == pool_id)
        assert QuestionPoolRead.model_validate(row).auto_sync_enabled is True
        assert row["pending_count"] == 1
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_mcp_catalog_endpoints_require_token(monkeypatch):
    """所有 /api/mcp/* GET 端点都必须用 MCP token；user JWT cookie 不该过、空 / 错 token 401。"""
    from server.tests.utils import build_test_app

    test_app = build_test_app(monkeypatch)
    try:
        monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
        from server.app.core import config

        config.get_settings.cache_clear()

        paths = [
            "/api/mcp/articles",
            "/api/mcp/question-pools",
            "/api/mcp/prompt-templates",
            "/api/mcp/pipelines",
            "/api/mcp/accounts",
        ]
        for path in paths:
            # 不带 token
            r = test_app.client.get(path)
            assert r.status_code == 401, f"{path} 未带 token 应 401，实际 {r.status_code}"
            # 错 token
            r = test_app.client.get(path, headers={"X-MCP-Token": "wrong"})
            assert r.status_code == 401, f"{path} 错 token 应 401，实际 {r.status_code}"
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_mcp_catalog_endpoints_pass_with_token(monkeypatch):
    """带正确 MCP token 调时应返回 200 + JSON list（空 DB 返回空列表）。"""
    from server.tests.utils import build_test_app

    test_app = build_test_app(monkeypatch)
    try:
        monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
        from server.app.core import config

        config.get_settings.cache_clear()
        headers = {"X-MCP-Token": "secret"}

        for path in [
            "/api/mcp/articles",
            "/api/mcp/question-pools",
            "/api/mcp/prompt-templates",
            "/api/mcp/pipelines",
            "/api/mcp/accounts",
        ]:
            r = test_app.client.get(path, headers=headers)
            assert r.status_code == 200, f"{path}: {r.status_code} {r.text[:200]}"
            assert isinstance(r.json(), list), f"{path} 应返回 list"
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_mcp_prompt_templates_excludes_disabled_but_admin_list_keeps(monkeypatch):
    """MCP catalog 只把"启用"的提示词递给 Loop（关闭的不该出现在清单里）；
    但 admin 管理列表 /api/prompt-templates 仍要看得到关闭模板以便重新启用。

    两条业务逻辑隔离：查询是查询（catalog 过滤 enabled、admin 列全量），
    跟保存层的校验各管各的。
    """
    from server.app.modules.prompt_templates.models import PromptTemplate
    from server.tests.utils import build_test_app

    test_app = build_test_app(monkeypatch)
    try:
        monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
        from server.app.core import config

        config.get_settings.cache_clear()

        with test_app.session_factory() as db:
            db.add(
                PromptTemplate(
                    name="enabled-tpl",
                    content="x",
                    scope="generation",
                    user_id=test_app.admin_id,
                    is_enabled=True,
                )
            )
            db.add(
                PromptTemplate(
                    name="disabled-tpl",
                    content="x",
                    scope="generation",
                    user_id=test_app.admin_id,
                    is_enabled=False,
                )
            )
            db.commit()

        # MCP 视角（service token）：只看得到启用的
        r = test_app.client.get("/api/mcp/prompt-templates", headers={"X-MCP-Token": "secret"})
        assert r.status_code == 200, r.text
        mcp_names = {t["name"] for t in r.json()}
        assert "enabled-tpl" in mcp_names
        assert "disabled-tpl" not in mcp_names

        # admin 管理列表（user JWT，test_app.client 默认 admin）：两者都在
        r2 = test_app.client.get("/api/prompt-templates")
        assert r2.status_code == 200, r2.text
        admin_names = {t["name"] for t in r2.json()}
        assert "enabled-tpl" in admin_names
        assert "disabled-tpl" in admin_names
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_mcp_catalog_articles_filter_by_review_status(monkeypatch):
    """`review_status=approved` 过滤应只返回审核通过的文章。"""
    from server.app.modules.articles import create_article
    from server.app.modules.articles.schemas import ArticleCreate
    from server.tests.utils import build_test_app

    test_app = build_test_app(monkeypatch)
    try:
        monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
        from server.app.core import config

        config.get_settings.cache_clear()

        # 准备一篇 pending 一篇 approved
        from server.app.db.session import SessionLocal

        db = SessionLocal()
        try:
            a1 = create_article(
                db,
                test_app.admin_id,
                ArticleCreate(
                    title="未审核",
                    content_json={"type": "doc", "content": []},
                    plain_text="正文一",
                    word_count=10,
                ),
            )
            a2 = create_article(
                db,
                test_app.admin_id,
                ArticleCreate(
                    title="已审核",
                    content_json={"type": "doc", "content": []},
                    plain_text="正文二",
                    word_count=10,
                ),
            )
            # 默认 review_status="approved"，显式把 a1 改成 pending 才能测过滤
            a1.review_status = "pending"
            db.commit()
            approved_id = a2.id
            pending_id = a1.id
        finally:
            db.close()

        r = test_app.client.get(
            "/api/mcp/articles?review_status=approved",
            headers={"X-MCP-Token": "secret"},
        )
        assert r.status_code == 200, r.text
        ids = {item["id"] for item in r.json()}
        assert approved_id in ids
        assert pending_id not in ids
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_mcp_question_items_use_active_semantics_and_compatibility_projection(monkeypatch):
    from server.app.modules.ai_generation.models import QuestionItem, QuestionPool
    from server.app.modules.articles.models import Article
    from server.tests.utils import build_test_app

    test_app = build_test_app(monkeypatch)
    try:
        monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
        from server.app.core import config

        config.get_settings.cache_clear()
        with test_app.session_factory() as db:
            pool = QuestionPool(user_id=test_app.admin_id, name="compat")
            article = Article(user_id=test_app.admin_id, title="legacy")
            db.add_all([pool, article])
            db.flush()
            db.add_all(
                [
                    QuestionItem(
                        pool_id=pool.id,
                        record_id="active-pending",
                        fields={},
                        question_text="active pending",
                        source_active=True,
                        status="pending",
                    ),
                    QuestionItem(
                        pool_id=pool.id,
                        record_id="active-consumed",
                        fields={},
                        question_text="active consumed",
                        source_active=True,
                        status="consumed",
                        article_id=article.id,
                    ),
                    QuestionItem(
                        pool_id=pool.id,
                        record_id="inactive-pending",
                        fields={},
                        question_text="inactive pending",
                        source_active=False,
                        status="pending",
                    ),
                ]
            )
            db.commit()
            pool_id = pool.id

        response = test_app.client.get(
            f"/api/mcp/question-pools/{pool_id}/items",
            headers={"X-MCP-Token": "secret"},
        )

        assert response.status_code == 200, response.text
        assert [row["record_id"] for row in response.json()] == [
            "active-pending",
            "active-consumed",
        ]
        assert all(row["status"] == "pending" for row in response.json())
        assert all(row["article_id"] is None for row in response.json())
    finally:
        test_app.cleanup()
