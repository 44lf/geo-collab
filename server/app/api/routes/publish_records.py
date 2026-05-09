import logging
import threading

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from server.app.db.session import get_db
from server.app.schemas.task import ManualConfirmInput, PublishRecordRead
from server.app.services.tasks import (
    TERMINAL_TASK_STATUSES,
    execute_task,
    get_record,
    get_task,
    manual_confirm_record,
    retry_record,
    to_record_read,
)

router = APIRouter()


# 手动确认发布结果（当任务设置了 stop_before_publish=True 时使用）
@router.post("/{record_id}/manual-confirm", response_model=PublishRecordRead)
def manual_confirm_record_endpoint(
    record_id: int,
    payload: ManualConfirmInput,
    db: Session = Depends(get_db),
) -> PublishRecordRead:
    record = get_record(db, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")
    result = manual_confirm_record(db, record, payload.outcome, payload.publish_url, payload.error_message)
    db.commit()

    task_id = record.task_id
    task = get_task(db, task_id)
    if task is not None and task.status not in TERMINAL_TASK_STATUSES:
        def _run() -> None:
            from server.app.api.routes.tasks import bg_session_factory as _bf
            from server.app.db.session import SessionLocal as _SL

            factory = _bf or _SL
            bg_db = factory()
            try:
                bg_task = get_task(bg_db, task_id)
                if bg_task:
                    execute_task(bg_db, bg_task)
                bg_db.commit()
            except Exception:
                bg_db.rollback()
                logging.getLogger(__name__).exception("Background execute after manual-confirm failed for task %s", task_id)
            finally:
                bg_db.close()

        threading.Thread(target=_run, daemon=True).start()

    return to_record_read(result)


# 重试失败的发布记录
@router.post("/{record_id}/retry", response_model=PublishRecordRead)
def retry_record_endpoint(record_id: int, db: Session = Depends(get_db)) -> PublishRecordRead:
    record = get_record(db, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")
    return to_record_read(retry_record(db, record))
