from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from server.app.core.time import utcnow
from server.app.db.base import Base


class BrowserSession(Base):
    """Cross-process browser session registry — written by worker, read by API."""
    __tablename__ = "browser_sessions"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    platform_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    account_key: Mapped[str] = mapped_column(String(200), nullable=False)
    display: Mapped[str | None] = mapped_column(String(20), nullable=True)
    novnc_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    worker_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    keep_alive: Mapped[bool] = mapped_column(Boolean, default=False)
    stop_requested: Mapped[bool] = mapped_column(Boolean, default=False)


class RecordBrowserSession(Base):
    """Maps a publish record to the browser session handling it."""
    __tablename__ = "record_browser_sessions"

    record_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("publish_records.id", ondelete="CASCADE"), primary_key=True
    )
    session_id: Mapped[str] = mapped_column(
        String(12), ForeignKey("browser_sessions.id", ondelete="CASCADE"), nullable=False
    )
