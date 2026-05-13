"""
任务相关 API 路由。

核心流程：
  1. POST /api/tasks — 创建任务，返回 TaskRead（含 records 数量）
  2. POST /api/tasks/{id}/execute — 启动后台线程立即执行（非队列模式），返回 {"queued": true} + 202
  3. GET  /api/tasks/{id}/records — 获取发布记录列表（含 novnc_url）
  4. GET  /api/tasks/{id}/logs — 增量拉取日志

后台执行：
  - 使用独立 DB Session（bg_session_factory）避免与请求 Session 冲突
  - 测试时 bg_session_factory 被 monkeypatch 为 TestingSessionLocal
"""
import logging
import threading
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from server.app.core.security import get_current_user
from server.app.db.session import get_db
from server.app.models import PublishTask, User
from server.app.schemas.task import (
    PublishRecordRead,
    TaskAssignmentPreviewRead,
    TaskCreate,
    TaskLogRead,
    TaskRead,
)
from server.app.services.serializers import to_log_read, to_record_read, to_task_read
from server.app.services.tasks import (
    TERMINAL_TASK_STATUSES,
    cancel_task,
    create_task,
    execute_task,
    get_task,
    list_task_logs,
    list_task_records,
    list_tasks,
    preview_task_assignment,
)

router = APIRouter()

# 后台任务使用的 Session 工厂（测试时可替换为内存数据库的 factory）
bg_session_factory: Any = None


def _verify_task_ownership(task: PublishTask | None, current_user: User) -> PublishTask:
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if current_user.role != "admin" and task.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


# 获取所有任务列表
@router.get("", response_model=list[TaskRead])
def read_tasks(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[TaskRead]:
    tasks = list_tasks(db, skip=skip, limit=limit)
    if current_user.role != "admin":
        tasks = [t for t in tasks if t.user_id == current_user.id]
    return [to_task_read(task) for task in tasks]


# 创建新任务
@router.post("", response_model=TaskRead)
def create_task_endpoint(
    payload: TaskCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TaskRead:
    try:
        return to_task_read(create_task(db, current_user.id, payload, role=current_user.role))
    except IntegrityError as exc:
        db.rollback()
        if payload.client_request_id:
            existing = db.execute(
                select(PublishTask).where(
                    PublishTask.client_request_id == payload.client_request_id,
                    PublishTask.user_id == current_user.id,
                )
            ).scalar_one_or_none()
            if existing is not None:
                refreshed = get_task(db, existing.id)
                return to_task_read(refreshed or existing)
        raise exc


# 预览任务分配（分组轮询时的文章-账号映射）
@router.post("/preview", response_model=TaskAssignmentPreviewRead)
def preview_task_assignment_endpoint(
    payload: TaskCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TaskAssignmentPreviewRead:
    return preview_task_assignment(db, payload, user_id=current_user.id, role=current_user.role)


# 执行任务（启动后台线程立即执行，非队列模式）
# 返回 202 表示后台线程已启动，用 GET /api/tasks/{id} 轮询状态
# 若任务已处于 terminal 状态则返回 400
@router.post("/{task_id}/execute", status_code=202)
def start_task_execution(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    task = _verify_task_ownership(get_task(db, task_id), current_user)
    if task.status in TERMINAL_TASK_STATUSES:
        raise HTTPException(status_code=409, detail=f"Task is already terminal: {task.status}")

    def _run() -> None:
        from server.app.db.session import SessionLocal as _SL

        factory = bg_session_factory or _SL
        bg_db = factory()
        try:
            bg_task = get_task(bg_db, task_id)
            if bg_task:
                execute_task(bg_db, bg_task)
            bg_db.commit()
        except Exception:
            bg_db.rollback()
            logging.getLogger(__name__).exception("Background task %s failed", task_id)
        finally:
            bg_db.close()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return {"queued": True}


# 取消任务
@router.post("/{task_id}/cancel", response_model=TaskRead)
def cancel_existing_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TaskRead:
    task = _verify_task_ownership(get_task(db, task_id), current_user)
    return to_task_read(cancel_task(db, task))


# 获取任务日志（支持增量拉取）
@router.get("/{task_id}/logs", response_model=list[TaskLogRead])
def read_task_logs(
    task_id: int,
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=100, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[TaskLogRead]:
    _verify_task_ownership(get_task(db, task_id), current_user)
    return [to_log_read(log) for log in list_task_logs(db, task_id, after_id=after_id, limit=limit)]


# 获取任务详情
@router.get("/{task_id}", response_model=TaskRead)
def read_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TaskRead:
    task = _verify_task_ownership(get_task(db, task_id), current_user)
    return to_task_read(task)


# 获取任务的发布记录列表
@router.get("/{task_id}/records", response_model=list[PublishRecordRead])
def read_task_records(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[PublishRecordRead]:
    _verify_task_ownership(get_task(db, task_id), current_user)
    return [to_record_read(record) for record in list_task_records(db, task_id)]
