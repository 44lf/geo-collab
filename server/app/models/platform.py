from datetime import datetime

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.app.core.time import utcnow
from server.app.db.base import Base


# 发布平台：目前仅支持头条号，预留扩展更多平台
class Platform(Base):
    __tablename__ = "platforms"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True)  # 平台编码，如 toutiao
    name: Mapped[str] = mapped_column(String(100))  # 显示名称
    base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    accounts = relationship("Account", back_populates="platform")
    publish_tasks = relationship("PublishTask", back_populates="platform")
