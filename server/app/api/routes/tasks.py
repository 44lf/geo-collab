from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from server.app.db.session import get_db
from server.app.schemas.task import PublishRecordRead, TaskAssignmentPreviewRead, TaskCreate, TaskLogRead, TaskRead
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


@router.get("", response_model=list[TaskRead])
def read_tasks(db: Session = Depends(get_db)) -> list[TaskRead]:
    return [to_task_read(task) for task in list_tasks(db)]


@router.post("", response_model=TaskRead)
def create_task_endpoint(payload: TaskCreate, db: Session = Depends(get_db)) -> TaskRead:
    try:
        task = create_task(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return to_task_read(task)


@router.post("/preview", response_model=TaskAssignmentPreviewRead)
def preview_task_assignment_endpoint(payload: TaskCreate, db: Session = Depends(get_db)) -> TaskAssignmentPreviewRead:
    try:
        return preview_task_assignment(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{task_id}/execute", response_model=TaskRead)
def execute_existing_task(task_id: int, db: Session = Depends(get_db)) -> TaskRead:
    task = get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    try:
        executed = execute_task(db, task)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return to_task_read(executed)


@router.post("/{task_id}/cancel", response_model=TaskRead)
def cancel_existing_task(task_id: int, db: Session = Depends(get_db)) -> TaskRead:
    task = get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return to_task_read(cancel_task(db, task))


@router.get("/{task_id}/logs", response_model=list[TaskLogRead])
def read_task_logs(task_id: int, db: Session = Depends(get_db)) -> list[TaskLogRead]:
    if get_task(db, task_id) is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return [to_log_read(log) for log in list_task_logs(db, task_id)]


@router.get("/{task_id}", response_model=TaskRead)
def read_task(task_id: int, db: Session = Depends(get_db)) -> TaskRead:
    task = get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return to_task_read(task)


@router.get("/{task_id}/records", response_model=list[PublishRecordRead])
def read_task_records(task_id: int, db: Session = Depends(get_db)) -> list[PublishRecordRead]:
    if get_task(db, task_id) is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return [to_record_read(record) for record in list_task_records(db, task_id)]
