from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.app.core.time import utcnow
from server.app.db.base import Base


class PublishTask(Base):
    __tablename__ = "publish_tasks"
    __table_args__ = (
        CheckConstraint("task_type in ('single', 'group_round_robin')", name="ck_publish_tasks_task_type"),
        CheckConstraint(
            "status in ('pending', 'running', 'succeeded', 'partial_failed', 'failed', 'cancelled')",
            name="ck_publish_tasks_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(300))
    task_type: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    platform_id: Mapped[int] = mapped_column(ForeignKey("platforms.id"), index=True)
    article_id: Mapped[int | None] = mapped_column(ForeignKey("articles.id"), nullable=True)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("article_groups.id"), nullable=True)
    stop_before_publish: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    platform = relationship("Platform", back_populates="publish_tasks")
    article = relationship("Article")
    group = relationship("ArticleGroup", back_populates="publish_tasks")
    accounts = relationship("PublishTaskAccount", back_populates="task", cascade="all, delete-orphan")
    records = relationship("PublishRecord", back_populates="task", cascade="all, delete-orphan")
    logs = relationship("TaskLog", back_populates="task", cascade="all, delete-orphan")


class PublishTaskAccount(Base):
    __tablename__ = "publish_task_accounts"
    __table_args__ = (UniqueConstraint("task_id", "account_id", name="uq_publish_task_accounts_task_account"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("publish_tasks.id"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    task = relationship("PublishTask", back_populates="accounts")
    account = relationship("Account", back_populates="publish_task_accounts")


class PublishRecord(Base):
    __tablename__ = "publish_records"
    __table_args__ = (
        CheckConstraint(
            "status in ('pending', 'running', 'waiting_manual_publish', 'succeeded', 'failed', 'cancelled')",
            name="ck_publish_records_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("publish_tasks.id"), index=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id"), index=True)
    platform_id: Mapped[int] = mapped_column(ForeignKey("platforms.id"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    publish_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_of_record_id: Mapped[int | None] = mapped_column(ForeignKey("publish_records.id"), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    task = relationship("PublishTask", back_populates="records")
    article = relationship("Article", back_populates="publish_records")
    platform = relationship("Platform")
    account = relationship("Account", back_populates="publish_records")
    retry_of = relationship("PublishRecord", remote_side=[id])
    logs = relationship("TaskLog", back_populates="record")


class TaskLog(Base):
    __tablename__ = "task_logs"
    __table_args__ = (CheckConstraint("level in ('info', 'warn', 'error')", name="ck_task_logs_level"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("publish_tasks.id"), index=True)
    record_id: Mapped[int | None] = mapped_column(ForeignKey("publish_records.id"), nullable=True)
    level: Mapped[str] = mapped_column(String(20), default="info", index=True)
    message: Mapped[str] = mapped_column(Text)
    screenshot_asset_id: Mapped[str | None] = mapped_column(ForeignKey("assets.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    task = relationship("PublishTask", back_populates="logs")
    record = relationship("PublishRecord", back_populates="logs")
    screenshot_asset = relationship("Asset", back_populates="task_logs")
