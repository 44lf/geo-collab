import logging
import threading
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from server.app.db.session import get_db
from server.app.schemas.task import (
    PublishRecordRead,
    TaskAssignmentPreviewRead,
    TaskCreate,
    TaskLogRead,
    TaskRead,
    TaskStatusRead,
)
from server.app.services.tasks import (
    cancel_task,
    create_task,
    execute_task,
    get_task,
    list_task_logs,
    list_task_records,
    list_tasks,
    preview_task_assignment,
    to_log_read,
    to_record_read,
    to_task_read,
)

router = APIRouter()

# 后台任务使用的 Session 工厂（测试时可替换为内存数据库的 factory）
bg_session_factory: Any = None


# 获取所有任务列表
@router.get("", response_model=list[TaskRead])
def read_tasks(db: Session = Depends(get_db)) -> list[TaskRead]:
    return [to_task_read(task) for task in list_tasks(db)]


# 创建新任务
@router.post("", response_model=TaskRead)
def create_task_endpoint(payload: TaskCreate, db: Session = Depends(get_db)) -> TaskRead:
    return to_task_read(create_task(db, payload))


# 预览任务分配（分组轮询时的文章-账号映射）
@router.post("/preview", response_model=TaskAssignmentPreviewRead)
def preview_task_assignment_endpoint(payload: TaskCreate, db: Session = Depends(get_db)) -> TaskAssignmentPreviewRead:
    return preview_task_assignment(db, payload)


# 执行任务（启动 Playwright 自动发布，后台异步执行）
@router.post("/{task_id}/execute", status_code=202)
def execute_existing_task(task_id: int, db: Session = Depends(get_db)) -> dict:
    task = get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

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


# 获取任务执行状态（含租约信息）
@router.get("/{task_id}/status", response_model=TaskStatusRead)
def read_task_status(task_id: int, db: Session = Depends(get_db)) -> TaskStatusRead:
    task = get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    records = list_task_records(db, task_id)
    active = next((r for r in records if r.status == "running"), None)
    lease_until = active.lease_until if active else None
    return TaskStatusRead(id=task.id, status=task.status, lease_until=lease_until)


# 取消任务
@router.post("/{task_id}/cancel", response_model=TaskRead)
def cancel_existing_task(task_id: int, db: Session = Depends(get_db)) -> TaskRead:
    task = get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return to_task_read(cancel_task(db, task))


# 获取任务日志（支持增量拉取）
@router.get("/{task_id}/logs", response_model=list[TaskLogRead])
def read_task_logs(
    task_id: int,
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=100, le=500),
    db: Session = Depends(get_db),
) -> list[TaskLogRead]:
    if get_task(db, task_id) is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return [to_log_read(log) for log in list_task_logs(db, task_id, after_id=after_id, limit=limit)]


# 获取任务详情
@router.get("/{task_id}", response_model=TaskRead)
def read_task(task_id: int, db: Session = Depends(get_db)) -> TaskRead:
    task = get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return to_task_read(task)


# 获取任务的发布记录列表
@router.get("/{task_id}/records", response_model=list[PublishRecordRead])
def read_task_records(task_id: int, db: Session = Depends(get_db)) -> list[PublishRecordRead]:
    if get_task(db, task_id) is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return [to_record_read(record) for record in list_task_records(db, task_id)]
