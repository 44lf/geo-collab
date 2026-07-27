"""Release A scheme-history 路由（挂在 /api/generation 下）。

方案 list/detail/history GET 只读保留；所有 HTTP 写操作统一 410。历史 executor
与 service 留给旧运行记录读取/内部兼容，不能由此路由启动新运行。
"""

from __future__ import annotations

from typing import Any, NoReturn

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from server.app.core.security import get_current_user
from server.app.db.session import get_db
from server.app.modules.ai_generation import scheme_service as svc
from server.app.modules.ai_generation.models import (
    GenerationScheme,
    GenerationSchemeRun,
    GenerationSchemeRunTask,
)
from server.app.modules.ai_generation.schemas import (
    SchemeCreate,
    SchemeLineQuestionRead,
    SchemeLineRead,
    SchemePatch,
    SchemeRead,
    SchemeRunRead,
    SchemeRunSummary,
    SchemeRunTaskRead,
    SchemeUpdate,
)
from server.app.modules.system.models import User

scheme_router = APIRouter()

# 历史兼容占位：create_app() 仍可注入会话工厂，但 Release A HTTP 路由不使用它启动方案运行。
bg_session_factory: Any = None


def _scheme_retired() -> NoReturn:
    raise HTTPException(status_code=410, detail="方案生文已停用，请使用智能体工作流")


def _get_owned_scheme(db: Session, scheme_id: int, current_user: User) -> GenerationScheme:
    scheme = svc.get_scheme(db, scheme_id)
    if scheme is None or (current_user.role != "admin" and scheme.user_id != current_user.id):
        raise HTTPException(status_code=404, detail="方案不存在")
    return scheme


def _scheme_to_read(db: Session, scheme: GenerationScheme) -> SchemeRead:
    line_reads: list[SchemeLineRead] = []
    for ln in svc.get_lines(db, scheme.id):
        questions = [
            SchemeLineQuestionRead.model_validate(q) for q in svc.get_line_questions(db, ln.id)
        ]
        line_reads.append(
            SchemeLineRead(
                id=ln.id,
                question_type=ln.question_type,
                article_count=ln.article_count,
                allowed_prompt_template_ids=ln.allowed_prompt_template_ids or [],
                questions=questions,
            )
        )
    return SchemeRead(
        id=scheme.id,
        name=scheme.name,
        pool_id=scheme.pool_id,
        is_enabled=scheme.is_enabled,
        ai_engine=scheme.ai_engine,
        created_at=scheme.created_at,
        updated_at=scheme.updated_at,
        lines=line_reads,
    )


@scheme_router.get("/schemes", response_model=list[SchemeRead])
def list_schemes(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    schemes = svc.list_schemes(db, user_id=current_user.id, is_admin=current_user.role == "admin")
    return [_scheme_to_read(db, s) for s in schemes]


@scheme_router.post("/schemes", response_model=None, status_code=410)
def create_scheme(
    payload: SchemeCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    _scheme_retired()


@scheme_router.get("/schemes/{scheme_id}", response_model=SchemeRead)
def get_scheme(
    scheme_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    scheme = _get_owned_scheme(db, scheme_id, current_user)
    return _scheme_to_read(db, scheme)


@scheme_router.put("/schemes/{scheme_id}", response_model=None, status_code=410)
def update_scheme(
    scheme_id: int,
    payload: SchemeUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    _scheme_retired()


@scheme_router.patch("/schemes/{scheme_id}", response_model=None, status_code=410)
def patch_scheme(
    scheme_id: int,
    payload: SchemePatch,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    _scheme_retired()


@scheme_router.delete("/schemes/{scheme_id}", status_code=410, response_model=None)
def delete_scheme(
    scheme_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    _scheme_retired()


# ── 方案运行 ───────────────────────────────────────────────────────────────────


def _run_to_read(db: Session, run: GenerationSchemeRun) -> SchemeRunRead:
    tasks = (
        db.query(GenerationSchemeRunTask)
        .filter(GenerationSchemeRunTask.run_id == run.id)
        .order_by(GenerationSchemeRunTask.id.asc())
        .all()
    )
    return SchemeRunRead(
        id=run.id,
        scheme_id=run.scheme_id,
        status=run.status,
        article_ids=run.article_ids or [],
        error_message=run.error_message,
        created_at=run.created_at,
        completed_at=run.completed_at,
        tasks=[SchemeRunTaskRead.model_validate(t) for t in tasks],
    )


@scheme_router.post("/schemes/{scheme_id}/runs", response_model=None, status_code=410)
def create_scheme_run(
    scheme_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    _scheme_retired()


@scheme_router.get("/schemes/{scheme_id}/runs", response_model=list[SchemeRunSummary])
def list_scheme_runs(
    scheme_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """某方案的历次运行（精简，倒序），供运行历史切换器使用。"""
    from sqlalchemy import func

    scheme = _get_owned_scheme(db, scheme_id, current_user)
    runs = (
        db.query(GenerationSchemeRun)
        .filter(GenerationSchemeRun.scheme_id == scheme.id)
        .order_by(GenerationSchemeRun.created_at.desc(), GenerationSchemeRun.id.desc())
        .limit(50)
        .all()
    )
    run_ids = [r.id for r in runs]
    task_counts: dict[int, int] = {}
    if run_ids:
        rows = (
            db.query(GenerationSchemeRunTask.run_id, func.count())
            .filter(GenerationSchemeRunTask.run_id.in_(run_ids))
            .group_by(GenerationSchemeRunTask.run_id)
            .all()
        )
        task_counts = {rid: cnt for rid, cnt in rows}
    return [
        SchemeRunSummary(
            id=r.id,
            status=r.status,
            article_count=len(r.article_ids or []),
            task_count=task_counts.get(r.id, 0),
            created_at=r.created_at,
            completed_at=r.completed_at,
        )
        for r in runs
    ]


@scheme_router.get("/scheme-runs/{run_id}", response_model=SchemeRunRead)
def get_scheme_run(
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    run = db.get(GenerationSchemeRun, run_id)
    if run is None or (current_user.role != "admin" and run.user_id != current_user.id):
        raise HTTPException(status_code=404, detail="运行记录不存在")
    return _run_to_read(db, run)
