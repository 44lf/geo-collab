"""方案池 CRUD + 校验 + 问题类型聚合（question-types）测试。

覆盖：question-types 只聚合 active；创建/更新/删除；以及全套校验失败
（跨池 / 类型不一致 / 非 active / 空题 / 文章数<=0 / 模板不存在/停用/删除/非 generation / 空模板）。
"""

import json
import sys

import pytest

from server.app.core.config import get_settings
from server.tests.utils import build_test_app


def _admin_id(session_factory) -> int:
    from server.app.modules.system.models import User

    with session_factory() as db:
        return db.query(User).first().id


def _seed_pool_with_types(app):
    """池含 category A(a1,a2 active) / B(b1 active) / A(x1 inactive)。返回 (pool_id, {rec:id}, uid)。"""
    from server.app.modules.ai_generation.models import QuestionItem, QuestionPool

    uid = _admin_id(app.session_factory)
    items: dict[str, int] = {}
    with app.session_factory() as db:
        pool = QuestionPool(user_id=uid, name="P")
        db.add(pool)
        db.flush()
        for rec, cat, active in [
            ("a1", "A", True),
            ("a2", "A", True),
            ("b1", "B", True),
            ("x1", "A", False),
        ]:
            it = QuestionItem(
                pool_id=pool.id,
                record_id=rec,
                fields={},
                question_text=f"问题-{rec}",
                category=cat,
                source_active=active,
            )
            db.add(it)
            db.flush()
            items[rec] = it.id
        db.commit()
        return pool.id, items, uid


def _seed_templates(app, uid) -> dict[str, int]:
    from server.app.modules.prompt_templates.models import PromptTemplate

    with app.session_factory() as db:
        good = PromptTemplate(
            name="g", content="写：{{问题}}", scope="generation", user_id=uid, is_enabled=True
        )
        disabled = PromptTemplate(
            name="d", content="x", scope="generation", user_id=uid, is_enabled=False
        )
        deleted = PromptTemplate(
            name="x", content="x", scope="generation", user_id=uid, is_enabled=True, is_deleted=True
        )
        wrong = PromptTemplate(
            name="w", content="x", scope="ai_format", user_id=uid, is_enabled=True
        )
        db.add_all([good, disabled, deleted, wrong])
        db.commit()
        return {
            "good": good.id,
            "disabled": disabled.id,
            "deleted": deleted.id,
            "wrong": wrong.id,
        }


# ── question-types 聚合 ───────────────────────────────────────────────────────


def test_question_types_endpoint_groups_active_only(monkeypatch):
    app = build_test_app(monkeypatch)
    try:
        pool_id, items, uid = _seed_pool_with_types(app)
        r = app.client.get(f"/api/generation/question-pools/{pool_id}/question-types")
        assert r.status_code == 200, r.text
        by_type = {d["question_type"]: d for d in r.json()}
        # x1（inactive）被排除 → A 只有 a1,a2
        assert by_type["A"]["count"] == 2
        assert {q["record_id"] for q in by_type["A"]["questions"]} == {"a1", "a2"}
        assert by_type["B"]["count"] == 1
    finally:
        app.cleanup()


def test_question_types_are_served_without_scheme_service(monkeypatch):
    """The shared question-pool endpoint must not load scheme-only services."""
    import server.app.modules.ai_generation as generation_module

    app = build_test_app(monkeypatch)
    try:
        pool_id, _, _ = _seed_pool_with_types(app)
        monkeypatch.delattr(generation_module, "scheme_service", raising=False)
        monkeypatch.setitem(sys.modules, "server.app.modules.ai_generation.scheme_service", None)

        response = app.client.get(f"/api/generation/question-pools/{pool_id}/question-types")

        assert response.status_code == 200, response.text
    finally:
        app.cleanup()


# ── 创建：happy path ─────────────────────────────────────────────────────────


def test_create_scheme_happy_path_snapshots_questions(monkeypatch):
    from server.app.modules.ai_generation.schemas import SchemeCreate
    from server.app.modules.ai_generation.scheme_service import create_scheme

    app = build_test_app(monkeypatch)
    try:
        pool_id, items, uid = _seed_pool_with_types(app)
        tpls = _seed_templates(app, uid)
        body = {
            "name": "方案1",
            "pool_id": pool_id,
            "lines": [
                {
                    "question_type": "A",
                    "question_item_ids": [items["a1"], items["a2"]],
                    "article_count": 3,
                    "allowed_prompt_template_ids": [tpls["good"]],
                },
                {
                    "question_type": "B",
                    "question_item_ids": [items["b1"]],
                    "article_count": 1,
                    "allowed_prompt_template_ids": [tpls["good"]],
                },
            ],
        }
        with app.session_factory() as db:
            scheme = create_scheme(
                db, user_id=uid, pool_id=pool_id, payload=SchemeCreate.model_validate(body)
            )
            db.commit()
            sid = scheme.id

        data = app.client.get(f"/api/generation/schemes/{sid}").json()
        assert data["name"] == "方案1"
        assert len(data["lines"]) == 2
        la = next(ln for ln in data["lines"] if ln["question_type"] == "A")
        assert la["article_count"] == 3
        assert {q["record_id"] for q in la["questions"]} == {"a1", "a2"}
        # 快照保存了题面文本
        assert all(q["question_text"] for q in la["questions"])

        # GET 一致
        r2 = app.client.get(f"/api/generation/schemes/{sid}")
        assert r2.status_code == 200
        assert len(r2.json()["lines"]) == 2

        # LIST 含该方案
        r3 = app.client.get("/api/generation/schemes")
        assert any(s["id"] == sid for s in r3.json())
    finally:
        app.cleanup()


# ── 校验失败 ───────────────────────────────────────────────────────────────────


def test_create_scheme_validation_failures(monkeypatch):
    from server.app.modules.ai_generation.schemas import SchemeCreate
    from server.app.modules.ai_generation.scheme_service import create_scheme
    from server.app.shared.errors import ValidationError

    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.ai_generation.models import QuestionItem, QuestionPool

        pool_id, items, uid = _seed_pool_with_types(app)
        tpls = _seed_templates(app, uid)
        good = tpls["good"]

        # 另一个池的问题
        with app.session_factory() as db:
            other = QuestionPool(user_id=uid, name="other")
            db.add(other)
            db.flush()
            oq = QuestionItem(
                pool_id=other.id,
                record_id="o1",
                fields={},
                question_text="o",
                category="A",
                source_active=True,
            )
            db.add(oq)
            db.flush()
            other_q = oq.id
            db.commit()

        cases = {
            "跨池问题": {
                "question_type": "A",
                "question_item_ids": [other_q],
                "article_count": 1,
                "allowed_prompt_template_ids": [good],
            },
            "类型不一致": {
                "question_type": "B",
                "question_item_ids": [items["a1"]],
                "article_count": 1,
                "allowed_prompt_template_ids": [good],
            },
            "非active问题": {
                "question_type": "A",
                "question_item_ids": [items["x1"]],
                "article_count": 1,
                "allowed_prompt_template_ids": [good],
            },
            "空题": {
                "question_type": "A",
                "question_item_ids": [],
                "article_count": 1,
                "allowed_prompt_template_ids": [good],
            },
            "文章数<=0": {
                "question_type": "A",
                "question_item_ids": [items["a1"]],
                "article_count": 0,
                "allowed_prompt_template_ids": [good],
            },
            "模板不存在": {
                "question_type": "A",
                "question_item_ids": [items["a1"]],
                "article_count": 1,
                "allowed_prompt_template_ids": [999999],
            },
            "模板停用": {
                "question_type": "A",
                "question_item_ids": [items["a1"]],
                "article_count": 1,
                "allowed_prompt_template_ids": [tpls["disabled"]],
            },
            "模板删除": {
                "question_type": "A",
                "question_item_ids": [items["a1"]],
                "article_count": 1,
                "allowed_prompt_template_ids": [tpls["deleted"]],
            },
            "模板非generation": {
                "question_type": "A",
                "question_item_ids": [items["a1"]],
                "article_count": 1,
                "allowed_prompt_template_ids": [tpls["wrong"]],
            },
            "空模板": {
                "question_type": "A",
                "question_item_ids": [items["a1"]],
                "article_count": 1,
                "allowed_prompt_template_ids": [],
            },
        }
        for line in cases.values():
            with app.session_factory() as db:
                with pytest.raises(ValidationError):
                    create_scheme(
                        db,
                        user_id=uid,
                        pool_id=pool_id,
                        payload=SchemeCreate.model_validate(
                            {"name": "s", "pool_id": pool_id, "lines": [line]}
                        ),
                    )
    finally:
        app.cleanup()


# ── 更新 / 删除 ────────────────────────────────────────────────────────────────


def test_update_scheme_replaces_lines_and_snapshots(monkeypatch):
    from server.app.modules.ai_generation.schemas import SchemeCreate, SchemeUpdate
    from server.app.modules.ai_generation.scheme_service import create_scheme, update_scheme

    app = build_test_app(monkeypatch)
    try:
        pool_id, items, uid = _seed_pool_with_types(app)
        tpls = _seed_templates(app, uid)
        good = tpls["good"]
        with app.session_factory() as db:
            scheme = create_scheme(
                db,
                user_id=uid,
                pool_id=pool_id,
                payload=SchemeCreate.model_validate(
                    {
                        "name": "s",
                        "pool_id": pool_id,
                        "lines": [
                            {
                                "question_type": "A",
                                "question_item_ids": [items["a1"], items["a2"]],
                                "article_count": 2,
                                "allowed_prompt_template_ids": [good],
                            }
                        ],
                    }
                ),
            )
            db.commit()
            sid = scheme.id

        # 改成只剩 B 一行
        with app.session_factory() as db:
            update_scheme(
                db,
                scheme=db.get(type(scheme), sid),
                user_id=uid,
                payload=SchemeUpdate.model_validate(
                    {
                        "name": "s2",
                        "lines": [
                            {
                                "question_type": "B",
                                "question_item_ids": [items["b1"]],
                                "article_count": 5,
                                "allowed_prompt_template_ids": [good],
                            }
                        ],
                    }
                ),
            )
            db.commit()
        data = app.client.get(f"/api/generation/schemes/{sid}").json()
        assert data["name"] == "s2"
        assert len(data["lines"]) == 1
        assert data["lines"][0]["question_type"] == "B"
        assert data["lines"][0]["article_count"] == 5

        # 旧 A 行的快照已删除
        from server.app.modules.ai_generation.models import GenerationSchemeLineQuestion

        with app.session_factory() as db:
            texts = {q.record_id for q in db.query(GenerationSchemeLineQuestion).all()}
            assert texts == {"b1"}
    finally:
        app.cleanup()


def test_update_scheme_with_prior_run_tasks_succeeds(monkeypatch):
    """编辑「已运行过」的方案不应 500：删旧行前要先断开 run task 的 scheme_line_id 外键。

    run task 的 scheme_line_id 是 FK→generation_scheme_lines（MySQL 默认 RESTRICT）。
    若不先置 NULL，删行会触发 1451 → 未捕获 IntegrityError → 500。
    运行明细是只读快照，断开回指针应保留历史、不丢数据。
    """
    from server.app.modules.ai_generation.models import (
        GenerationSchemeRun,
        GenerationSchemeRunTask,
    )
    from server.app.modules.ai_generation.schemas import SchemeCreate, SchemeUpdate
    from server.app.modules.ai_generation.scheme_service import (
        create_scheme,
        get_lines,
        update_scheme,
    )

    app = build_test_app(monkeypatch)
    try:
        pool_id, items, uid = _seed_pool_with_types(app)
        tpls = _seed_templates(app, uid)
        good = tpls["good"]
        with app.session_factory() as db:
            scheme = create_scheme(
                db,
                user_id=uid,
                pool_id=pool_id,
                payload=SchemeCreate.model_validate(
                    {
                        "name": "s",
                        "pool_id": pool_id,
                        "lines": [
                            {
                                "question_type": "A",
                                "question_item_ids": [items["a1"], items["a2"]],
                                "article_count": 2,
                                "allowed_prompt_template_ids": [good],
                            }
                        ],
                    }
                ),
            )
            db.commit()
            sid = scheme.id
            line_id = get_lines(db, sid)[0].id

        # 模拟该方案被运行过：插一条 run + 引用该行的 run task
        with app.session_factory() as db:
            run = GenerationSchemeRun(scheme_id=sid, user_id=uid, status="done")
            db.add(run)
            db.flush()
            task = GenerationSchemeRunTask(
                run_id=run.id,
                scheme_line_id=line_id,
                question_type="A",
                status="done",
            )
            db.add(task)
            db.commit()
            task_id = task.id

        # 编辑方案（重建行）——修复前这里 500，修复后 200
        with app.session_factory() as db:
            update_scheme(
                db,
                scheme=db.get(type(scheme), sid),
                user_id=uid,
                payload=SchemeUpdate.model_validate(
                    {
                        "name": "s2",
                        "lines": [
                            {
                                "question_type": "B",
                                "question_item_ids": [items["b1"]],
                                "article_count": 5,
                                "allowed_prompt_template_ids": [good],
                            }
                        ],
                    }
                ),
            )
            db.commit()
        assert (
            app.client.get(f"/api/generation/schemes/{sid}").json()["lines"][0]["question_type"]
            == "B"
        )

        # 运行历史保留，但回指针已断开（scheme_line_id 置 NULL）
        with app.session_factory() as db:
            t = db.get(GenerationSchemeRunTask, task_id)
            assert t is not None, "运行明细不应被删除"
            assert t.scheme_line_id is None, "应已断开对已删行的外键引用"
    finally:
        app.cleanup()


# ── AI 引擎字段 ────────────────────────────────────────────────────────────────


def test_ai_engines_endpoint_returns_configured_list(monkeypatch):
    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.ai_models.models import AiModel

        # DB 优先：建一个 generation 行 → 端点反映它（只下发 label/model）
        app.client.post(
            "/api/ai-models",
            json={
                "label": "我的写作模型",
                "model": "anthropic/claude-opus-4-8",
                "scope": "generation",
            },
        )
        monkeypatch.setenv(
            "GEO_AI_ENGINES",
            json.dumps(
                [
                    {
                        "label": "Kimi",
                        "model": "moonshot/kimi-k2.5",
                        "api_key": "KIMI-SECRET-should-not-leak",
                        "base_url": "https://api.moonshot.cn/v1",
                    },
                    {
                        "label": "豆包",
                        "model": "volcengine/ep-m-test",
                        "api_key": "DOUBAO-SECRET-should-not-leak",
                    },
                ]
            ),
        )
        get_settings.cache_clear()
        data = app.client.get("/api/generation/ai-engines").json()
        models = {e["model"]: e["label"] for e in data}
        assert models["anthropic/claude-opus-4-8"] == "我的写作模型"
        # DB 里新增 Claude 后，env-only 旧模型仍要留在 AI 创作/方案下拉里。
        # 这类模型带内联 api_key，不能播种进 DB，否则运行时会丢 per-engine key。
        assert models["moonshot/kimi-k2.5"] == "Kimi"
        assert models["volcengine/ep-m-test"] == "豆包"
        assert all(set(e.keys()) <= {"label", "model"} for e in data)
        text = app.client.get("/api/generation/ai-engines").text
        assert "KIMI-SECRET-should-not-leak" not in text
        assert "DOUBAO-SECRET-should-not-leak" not in text
        assert "https://api.moonshot.cn/v1" not in text

        # DB 无 generation 行 → 回落 settings.ai_engines（带内联密钥），仍永不泄漏 key/base_url
        with app.session_factory() as db:
            db.query(AiModel).filter(AiModel.scope == "generation").delete()
            db.commit()
        monkeypatch.setenv(
            "GEO_AI_ENGINES",
            json.dumps(
                [
                    {
                        "label": "网关",
                        "model": "openai/gpt-4o",
                        "api_key": "SECRET-should-not-leak",
                        "base_url": "https://gw/v1",
                    }
                ]
            ),
        )
        get_settings.cache_clear()
        r = app.client.get("/api/generation/ai-engines")
        assert r.status_code == 200, r.text
        data = r.json()
        assert isinstance(data, list) and len(data) >= 1
        assert data[0]["label"] == "网关" and data[0]["model"] == "openai/gpt-4o"
        # 永不泄漏密钥 / 网关地址给前端：响应里既无字段名、也无密钥值
        assert "api_key" not in data[0]
        assert "base_url" not in data[0]
        assert "SECRET-should-not-leak" not in r.text
        assert "https://gw/v1" not in r.text
    finally:
        get_settings.cache_clear()
        app.cleanup()


def test_create_scheme_ai_engine_round_trips_and_normalizes(monkeypatch):
    from server.app.modules.ai_generation.schemas import SchemeCreate, SchemeUpdate
    from server.app.modules.ai_generation.scheme_service import create_scheme, update_scheme

    app = build_test_app(monkeypatch)
    try:
        pool_id, items, uid = _seed_pool_with_types(app)
        tpls = _seed_templates(app, uid)
        line = {
            "question_type": "A",
            "question_item_ids": [items["a1"]],
            "article_count": 1,
            "allowed_prompt_template_ids": [tpls["good"]],
        }
        with app.session_factory() as db:
            scheme = create_scheme(
                db,
                user_id=uid,
                pool_id=pool_id,
                payload=SchemeCreate.model_validate(
                    {
                        "name": "s",
                        "pool_id": pool_id,
                        "ai_engine": "deepseek/deepseek-chat",
                        "lines": [line],
                    }
                ),
            )
            db.commit()
            sid = scheme.id
        assert app.client.get(f"/api/generation/schemes/{sid}").json()["ai_engine"] == (
            "deepseek/deepseek-chat"
        )

        with app.session_factory() as db:
            blank = create_scheme(
                db,
                user_id=uid,
                pool_id=pool_id,
                payload=SchemeCreate.model_validate(
                    {"name": "s2", "pool_id": pool_id, "ai_engine": "  ", "lines": [line]}
                ),
            )
            default = create_scheme(
                db,
                user_id=uid,
                pool_id=pool_id,
                payload=SchemeCreate.model_validate(
                    {"name": "s3", "pool_id": pool_id, "lines": [line]}
                ),
            )
            scheme = db.get(type(scheme), sid)
            update_scheme(
                db,
                scheme=scheme,
                user_id=uid,
                payload=SchemeUpdate.model_validate(
                    {"name": "s", "ai_engine": "gpt-4o", "lines": [line]}
                ),
            )
            db.commit()
            blank_id, default_id = blank.id, default.id

        assert app.client.get(f"/api/generation/schemes/{blank_id}").json()["ai_engine"] is None
        assert app.client.get(f"/api/generation/schemes/{default_id}").json()["ai_engine"] is None
        assert app.client.get(f"/api/generation/schemes/{sid}").json()["ai_engine"] == "gpt-4o"
    finally:
        app.cleanup()


def test_delete_scheme_soft(monkeypatch):
    from server.app.modules.ai_generation.schemas import SchemeCreate
    from server.app.modules.ai_generation.scheme_service import create_scheme, delete_scheme

    app = build_test_app(monkeypatch)
    try:
        pool_id, items, uid = _seed_pool_with_types(app)
        tpls = _seed_templates(app, uid)
        with app.session_factory() as db:
            scheme = create_scheme(
                db,
                user_id=uid,
                pool_id=pool_id,
                payload=SchemeCreate.model_validate(
                    {
                        "name": "s",
                        "pool_id": pool_id,
                        "lines": [
                            {
                                "question_type": "A",
                                "question_item_ids": [items["a1"]],
                                "article_count": 1,
                                "allowed_prompt_template_ids": [tpls["good"]],
                            }
                        ],
                    }
                ),
            )
            db.commit()
            sid = scheme.id
            delete_scheme(db, scheme)
            db.commit()
        assert app.client.get(f"/api/generation/schemes/{sid}").status_code == 404
        assert all(s["id"] != sid for s in app.client.get("/api/generation/schemes").json())
    finally:
        app.cleanup()
