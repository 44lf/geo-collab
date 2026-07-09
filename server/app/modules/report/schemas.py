"""打点上报事件 Pydantic 出参模型。"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class ReportEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    source_module: str
    source_type: str | None = None
    source_id: int | None = None
    event_type: str
    level: str
    message: str
    payload_json: Any | None = None


class ReportEventList(BaseModel):
    items: list[ReportEventRead]
    next_cursor: int | None = None


class ReportEventMcpPayload(BaseModel):
    source_module: str
    event_type: str
    message: str
    level: str = "info"
    source_type: str | None = None
    source_id: int | None = None
    payload: dict[str, Any] | None = None


class ReportEventMcpResponse(BaseModel):
    id: int
