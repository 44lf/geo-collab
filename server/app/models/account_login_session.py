from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from server.app.core.time import utcnow
from server.app.db.base import Base


class AccountLoginSession(Base):
    """Worker-owned interactive account login session command/state row."""

    __tablename__ = "account_login_sessions"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    account_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    platform_code: Mapped[str] = mapped_column(String(80), nullable=False)
    account_key: Mapped[str] = mapped_column(String(200), nullable=False)
    channel: Mapped[str] = mapped_column(String(80), nullable=False, default="chromium")
    executable_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    browser_session_id: Mapped[str | None] = mapped_column(String(12), nullable=True, index=True)
    novnc_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    logged_in: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    result_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    result_title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
