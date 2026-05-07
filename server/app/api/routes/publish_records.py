from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from server.app.db.session import get_db
from server.app.schemas.task import ManualConfirmInput, PublishRecordRead
from server.app.services.tasks import get_record, manual_confirm_record, retry_record, to_record_read

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
    return to_record_read(manual_confirm_record(db, record, payload.outcome, payload.publish_url, payload.error_message))


# 重试失败的发布记录
@router.post("/{record_id}/retry", response_model=PublishRecordRead)
def retry_record_endpoint(record_id: int, db: Session = Depends(get_db)) -> PublishRecordRead:
    record = get_record(db, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")
    return to_record_read(retry_record(db, record))
