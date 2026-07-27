"""Release A 回归门禁。"""

import ast
from pathlib import Path

import pytest

from server.tests.utils import build_test_app


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
    finally:
        app.cleanup()
