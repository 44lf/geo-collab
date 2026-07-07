"""通用打点上报事件 ORM 模型。

跨模块的事件/异常上报表：AI 生文、pipeline 执行、任务发布、账号登录等任意模块
都可以调用 service.record_event() 往里写一条事件，用于回溯业务流程、定位报错。
不做敏感字段脱敏（这是系统内部埋点，不是用户提交数据）。
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from server.app.core.time import utcnow
from server.app.db.base import Base


class ReportEvent(Base):
    __tablename__ = "report_events"
    __table_args__ = (
        Index("ix_report_events_source_created", "source_module", "created_at"),
        Index("ix_report_events_event_type_created", "event_type", "created_at"),
        Index("ix_report_events_source_type_id", "source_type", "source_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    source_module: Mapped[str] = mapped_column(String(50), index=True)
    source_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(50), index=True)
    level: Mapped[str] = mapped_column(String(10), default="info")
    message: Mapped[str] = mapped_column(Text)
    payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
