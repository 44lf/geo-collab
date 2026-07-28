"""Release A 回归门禁。"""

import ast
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.tests.utils import build_test_app


def _assert_release_a_protected_route_methods(methods_by_path: dict[str, set[str]]) -> None:
    shared_get_paths = {
        "/api/generation/question-pools",
        "/api/generation/ai-engines",
        "/api/generation/format-engines",
        "/api/mcp/loop-skill-bundle/info",
        "/api/prompt-templates/{template_id}/performance",
    }
    scheme_read_paths = {
        "/api/generation/schemes",
        "/api/generation/schemes/{scheme_id}",
        "/api/generation/schemes/{scheme_id}/runs",
        "/api/generation/scheme-runs/{run_id}",
    }
    retired_scheme_writes = {
        ("POST", "/api/generation/schemes"),
        ("PUT", "/api/generation/schemes/{scheme_id}"),
        ("PATCH", "/api/generation/schemes/{scheme_id}"),
        ("DELETE", "/api/generation/schemes/{scheme_id}"),
        ("POST", "/api/generation/schemes/{scheme_id}/runs"),
    }

    assert shared_get_paths | scheme_read_paths <= methods_by_path.keys()
    for path in shared_get_paths | scheme_read_paths:
        assert "GET" in methods_by_path[path]
    for method, path in retired_scheme_writes:
        assert method in methods_by_path[path]


def test_release_a_protected_route_method_gate_rejects_mutations():
    """Prove the method gate fails if a protected GET or retired write disappears."""
    valid = {
        "/api/generation/question-pools": {"GET", "POST"},
        "/api/generation/ai-engines": {"GET"},
        "/api/generation/format-engines": {"GET"},
        "/api/mcp/loop-skill-bundle/info": {"GET"},
        "/api/prompt-templates/{template_id}/performance": {"GET"},
        "/api/generation/schemes": {"GET", "POST"},
        "/api/generation/schemes/{scheme_id}": {"GET", "PUT", "PATCH", "DELETE"},
        "/api/generation/schemes/{scheme_id}/runs": {"GET", "POST"},
        "/api/generation/scheme-runs/{run_id}": {"GET"},
    }
    _assert_release_a_protected_route_methods(valid)

    missing_engine_get = dict(valid)
    missing_engine_get["/api/generation/ai-engines"] = set()
    with pytest.raises(AssertionError):
        _assert_release_a_protected_route_methods(missing_engine_get)

    missing_retired_write = dict(valid)
    missing_retired_write["/api/generation/schemes/{scheme_id}/runs"] = {"GET"}
    with pytest.raises(AssertionError):
        _assert_release_a_protected_route_methods(missing_retired_write)


def test_release_a_protected_routes_stay_mounted(monkeypatch):
    """Release A preserves shared HTTP contracts while retiring scheme writes."""
    app = build_test_app(monkeypatch)
    try:
        methods_by_path: dict[str, set[str]] = {}
        for route in app.client.app.routes:
            path = getattr(route, "path", None)
            if path:
                methods_by_path.setdefault(path, set()).update(
                    getattr(route, "methods", set()) or set()
                )

        _assert_release_a_protected_route_methods(methods_by_path)
    finally:
        app.cleanup()


def test_retired_scheme_routes_are_registered_exactly_once(monkeypatch):
    """拆分读写 router 后，每个 retired method/path 仍只能有一个处理器。"""
    app = build_test_app(monkeypatch)
    try:
        expected = {
            ("POST", "/api/generation/schemes"),
            ("PUT", "/api/generation/schemes/{scheme_id}"),
            ("PATCH", "/api/generation/schemes/{scheme_id}"),
            ("DELETE", "/api/generation/schemes/{scheme_id}"),
            ("POST", "/api/generation/schemes/{scheme_id}/runs"),
        }
        counts = {key: 0 for key in expected}
        for route in app.client.app.routes:
            path = getattr(route, "path", None)
            for method in getattr(route, "methods", set()) or set():
                key = (method, path)
                if key in counts:
                    counts[key] += 1
        assert counts == {key: 1 for key in expected}
    finally:
        app.cleanup()


def test_release_a_mcp_tools_stay_registered():
    """MCP's external entrypoint must keep its Release A tool contract."""
    from server.app.modules.mcp_catalog.connect_router import MCP_TOOLS_COUNT
    from server.mcp.server import mcp

    names = set(mcp._tool_manager._tools)
    assert MCP_TOOLS_COUNT == 39
    assert len(names) == MCP_TOOLS_COUNT
    assert {
        "list_question_pools",
        "list_question_items",
        "save_article",
        "get_template_performance",
        "get_account_performance",
        "record_publish_metrics",
        "install_loop_skills",
    } <= names


def test_release_a_question_and_performance_contract_sources_are_present():
    """Keep active/default question semantics and real template aggregation discoverable."""
    generation_router = Path("server/app/modules/ai_generation/router.py").read_text(
        encoding="utf-8"
    )
    question_bank = Path("server/app/modules/ai_generation/question_bank.py").read_text(
        encoding="utf-8"
    )
    performance_service = Path("server/app/modules/performance/service.py").read_text(
        encoding="utf-8"
    )

    assert 'status: str = "pending"' in generation_router
    assert 'status not in {"pending", "all", "consumed"}' in generation_router
    router_tree = ast.parse(generation_router)
    availability_annotations = [
        ast.unparse(node.annotation)
        for node in ast.walk(router_tree)
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "availability"
    ]
    assert availability_annotations == ["Literal['active', 'all']"]
    assert 'status="pending"' in question_bank
    assert "Article.source_template_id == template_id" in performance_service
    assert '"approval_rate": approval_rate' in performance_service


def test_pipeline_does_not_import_scheme_modules():
    roots = [
        Path("server/app/modules/pipelines/nodes/ai_compose.py"),
        Path("server/app/modules/pipelines/nodes/ai_generate_node.py"),
    ]
    forbidden = {"scheme_router", "scheme_service", "scheme_executor"}
    for path in roots:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        assert not any(any(part in name for part in forbidden) for name in imported)


def _snapshot_scheme_side_effects(db):
    from server.app.modules.ai_generation.models import (
        GenerationScheme,
        GenerationSchemeLine,
        GenerationSchemeLineQuestion,
        GenerationSchemeRun,
        GenerationSchemeRunTask,
    )
    from server.app.modules.articles.models import Article
    from server.app.modules.audit.models import AuditLog

    models = {
        "generation_schemes": GenerationScheme,
        "generation_scheme_lines": GenerationSchemeLine,
        "generation_scheme_line_questions": GenerationSchemeLineQuestion,
        "generation_scheme_runs": GenerationSchemeRun,
        "generation_scheme_run_tasks": GenerationSchemeRunTask,
        "audit_logs": AuditLog,
        "articles": Article,
    }
    return {name: db.query(model).count() for name, model in models.items()}


@pytest.mark.mysql
@pytest.mark.parametrize(
    ("method", "route"),
    [
        ("post", "/api/generation/schemes"),
        ("put", "/api/generation/schemes/{scheme_id}"),
        ("patch", "/api/generation/schemes/{scheme_id}"),
        ("delete", "/api/generation/schemes/{scheme_id}"),
        ("post", "/api/generation/schemes/{scheme_id}/runs"),
    ],
)
def test_scheme_mutations_are_gone_without_writes(monkeypatch, method, route):
    from server.app.modules.ai_generation.models import QuestionItem, QuestionPool
    from server.app.modules.ai_generation.schemas import SchemeCreate
    from server.app.modules.ai_generation.scheme_service import create_scheme
    from server.app.modules.prompt_templates.models import PromptTemplate
    from server.app.modules.system.models import User

    app = build_test_app(monkeypatch)
    try:
        with app.session_factory() as db:
            user = db.query(User).filter(User.username == "testadmin").one()
            pool = QuestionPool(user_id=user.id, name="retired-pool")
            db.add(pool)
            db.flush()
            question = QuestionItem(
                pool_id=pool.id,
                record_id="retired-q",
                fields={},
                question_text="历史问题",
                category="A",
                source_active=True,
            )
            template = PromptTemplate(
                name="retired-template",
                content="写：{{问题}}",
                scope="generation",
                user_id=user.id,
                is_enabled=True,
            )
            db.add_all([question, template])
            db.flush()
            payload = {
                "name": "retired",
                "pool_id": pool.id,
                "lines": [
                    {
                        "question_type": "A",
                        "question_item_ids": [question.id],
                        "article_count": 1,
                        "allowed_prompt_template_ids": [template.id],
                    }
                ],
            }
            scheme = create_scheme(
                db,
                user_id=user.id,
                pool_id=pool.id,
                payload=SchemeCreate.model_validate(payload),
            )
            db.commit()
            path = route.format(scheme_id=scheme.id)
            before = _snapshot_scheme_side_effects(db)

        request = getattr(app.client, method)
        if method == "delete":
            response = request(path)
        elif method == "patch":
            response = request(path, json={"is_enabled": False})
        elif method == "put":
            response = request(
                path, json={key: value for key, value in payload.items() if key != "pool_id"}
            )
        elif path.endswith("/runs"):
            response = request(path, json={})
        else:
            response = request(path, json=payload)

        assert response.status_code == 410, response.text
        assert response.json()["detail"] == "方案生文已停用，请使用智能体工作流"
        with app.session_factory() as db:
            assert _snapshot_scheme_side_effects(db) == before
    finally:
        app.cleanup()


@pytest.mark.mysql
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/generation/schemes"),
        ("PUT", "/api/generation/schemes/987654"),
        ("PATCH", "/api/generation/schemes/987654"),
    ],
)
def test_retired_scheme_body_is_never_parsed(monkeypatch, method, path):
    """缺 body 和非法 JSON 都必须在 Pydantic 解析前直接 410。"""
    app = build_test_app(monkeypatch)
    try:
        missing = app.client.request(method, path)
        invalid = app.client.request(
            method,
            path,
            content=b"{not-json",
            headers={"content-type": "application/json"},
        )
        for response in (missing, invalid):
            assert response.status_code == 410, response.text
            assert response.json()["detail"] == "方案生文已停用，请使用智能体工作流"
    finally:
        app.cleanup()


@pytest.mark.mysql
@pytest.mark.parametrize(
    ("method", "path", "json_body"),
    [
        ("POST", "/api/generation/schemes", {"name": "ignored"}),
        ("PUT", "/api/generation/schemes/987654", {"name": "ignored"}),
        ("PATCH", "/api/generation/schemes/987654", {"name": "ignored"}),
        ("DELETE", "/api/generation/schemes/987654", None),
        ("POST", "/api/generation/schemes/987654/runs", None),
    ],
)
def test_retired_scheme_mutations_do_not_require_authentication(
    monkeypatch, method, path, json_body
):
    """匿名请求也直接 410，不能先触发 JWT 或属主探测。"""
    app = build_test_app(monkeypatch)
    anonymous = TestClient(app.client.app)
    try:
        response = anonymous.request(method, path, json=json_body)
        assert response.status_code == 410, response.text
        assert response.json()["detail"] == "方案生文已停用，请使用智能体工作流"
    finally:
        anonymous.close()
        app.cleanup()


@pytest.mark.mysql
def test_retired_scheme_mutations_ignore_unavailable_dependencies(monkeypatch):
    """DB/auth dependency 即使被 override 成 raise，五个写入口仍不得触碰它们。"""
    from server.app.core.security import get_current_user
    from server.app.db.session import get_db

    app = build_test_app(monkeypatch)

    def unavailable():
        raise RuntimeError("dependency unavailable")

    app.client.app.dependency_overrides[get_db] = unavailable
    app.client.app.dependency_overrides[get_current_user] = unavailable
    client = TestClient(app.client.app, raise_server_exceptions=False)
    try:
        for method, path in [
            ("POST", "/api/generation/schemes"),
            ("PUT", "/api/generation/schemes/987654"),
            ("PATCH", "/api/generation/schemes/987654"),
            ("DELETE", "/api/generation/schemes/987654"),
            ("POST", "/api/generation/schemes/987654/runs"),
        ]:
            response = client.request(method, path)
            assert response.status_code == 410, (method, path, response.text)
    finally:
        client.close()
        app.cleanup()


@pytest.mark.mysql
@pytest.mark.parametrize(
    ("method", "path", "json_body"),
    [
        ("PUT", "/api/generation/schemes/not-an-int", {"name": "ignored"}),
        ("PATCH", "/api/generation/schemes/not-an-int", {"name": "ignored"}),
        ("DELETE", "/api/generation/schemes/not-an-int", None),
        ("POST", "/api/generation/schemes/not-an-int/runs", None),
    ],
)
def test_retired_scheme_routes_keep_integer_path_validation(monkeypatch, method, path, json_body):
    app = build_test_app(monkeypatch)
    try:
        response = app.client.request(method, path, json=json_body)
        assert response.status_code == 422, response.text
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_scheme_read_routes_remain_available(monkeypatch):
    from server.app.modules.ai_generation.models import (
        GenerationScheme,
        GenerationSchemeLine,
        GenerationSchemeRun,
        GenerationSchemeRunTask,
        QuestionPool,
    )
    from server.app.modules.system.models import User

    app = build_test_app(monkeypatch)
    try:
        with app.session_factory() as db:
            user = db.query(User).filter(User.username == "testadmin").one()
            pool = QuestionPool(user_id=user.id, name="history-pool")
            db.add(pool)
            db.flush()
            scheme = GenerationScheme(user_id=user.id, pool_id=pool.id, name="history-scheme")
            db.add(scheme)
            db.flush()
            line = GenerationSchemeLine(scheme_id=scheme.id, question_type="A")
            db.add(line)
            db.flush()
            run = GenerationSchemeRun(scheme_id=scheme.id, user_id=user.id, status="done")
            db.add(run)
            db.flush()
            db.add(GenerationSchemeRunTask(run_id=run.id, scheme_line_id=line.id, status="done"))
            db.commit()
            scheme_id, run_id = scheme.id, run.id

        assert app.client.get("/api/generation/schemes").status_code == 200
        assert app.client.get(f"/api/generation/schemes/{scheme_id}").status_code == 200
        assert app.client.get(f"/api/generation/schemes/{scheme_id}/runs").status_code == 200
        assert app.client.get(f"/api/generation/scheme-runs/{run_id}").status_code == 200

        anonymous = TestClient(app.client.app)
        try:
            assert anonymous.get("/api/generation/schemes").status_code == 401
            assert anonymous.get(f"/api/generation/schemes/{scheme_id}").status_code == 401
            assert anonymous.get(f"/api/generation/schemes/{scheme_id}/runs").status_code == 401
            assert anonymous.get(f"/api/generation/scheme-runs/{run_id}").status_code == 401
        finally:
            anonymous.close()
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_retired_generation_session_post_is_410_but_history_get_stays_private(monkeypatch):
    """文档契约：只退役 session 创建，历史详情 GET 仍可鉴权读取。"""
    from server.app.modules.ai_generation.models import GenerationSession
    from server.tests.utils import create_extra_user

    app = build_test_app(monkeypatch)
    anonymous = TestClient(app.client.app)
    try:
        with app.session_factory() as db:
            session = GenerationSession(
                user_id=app.admin_id,
                status="done",
                article_ids="[11, 12]",
            )
            db.add(session)
            db.commit()
            session_id = session.id

        _operator_id, operator = create_extra_user(app, "session-history-reader")
        try:
            post_response = app.client.post("/api/generation/sessions")
            assert post_response.status_code == 410, post_response.text

            owner_response = app.client.get(f"/api/generation/sessions/{session_id}")
            assert owner_response.status_code == 200, owner_response.text
            assert owner_response.json()["article_ids"] == [11, 12]

            assert anonymous.get(f"/api/generation/sessions/{session_id}").status_code == 401
            assert operator.get(f"/api/generation/sessions/{session_id}").status_code == 404
        finally:
            operator.close()
    finally:
        anonymous.close()
        app.cleanup()
