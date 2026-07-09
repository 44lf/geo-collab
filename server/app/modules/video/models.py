"""视频生成任务 ORM。产物 mp4/srt 以 MinIO 对象 key 记录。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from server.app.core.time import utcnow
from server.app.db.base import Base


class VideoJob(Base):
    __tablename__ = "video_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    storyboard: Mapped[dict] = mapped_column(JSON, nullable=False)
    engine: Mapped[str | None] = mapped_column(String(50), nullable=True)
    video_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    srt_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
