"""小红书卡片渲染任务 ORM。产物 PNG 以 MinIO 对象 key 记录。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from server.app.core.time import utcnow
from server.app.db.base import Base


class XhsRenderJob(Base):
    __tablename__ = "xhs_render_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    source_article_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    theme: Mapped[str] = mapped_column(String(50), nullable=False, server_default="sketch")
    mode: Mapped[str] = mapped_column(String(20), nullable=False, server_default="separator")
    cover_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    card_keys: Mapped[list | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
