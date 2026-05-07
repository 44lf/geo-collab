from datetime import datetime

from pydantic import BaseModel, Field


class TaskAccountInput(BaseModel):
    account_id: int
    sort_order: int | None = None


class TaskCreate(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    task_type: str
    article_id: int | None = None
    group_id: int | None = None
    platform_code: str = "toutiao"
    accounts: list[TaskAccountInput]
    stop_before_publish: bool = True


class TaskAccountRead(BaseModel):
    account_id: int
    sort_order: int
    display_name: str
    status: str


class PublishRecordRead(BaseModel):
    id: int
    task_id: int
    article_id: int
    platform_id: int
    account_id: int
    status: str
    publish_url: str | None
    error_message: str | None
    retry_of_record_id: int | None
    started_at: datetime | None
    finished_at: datetime | None


class TaskLogRead(BaseModel):
    id: int
    task_id: int
    record_id: int | None
    level: str
    message: str
    screenshot_asset_id: str | None
    created_at: datetime


class TaskAssignmentPreviewItemRead(BaseModel):
    position: int
    article_id: int
    account_id: int
    account_sort_order: int


class TaskAssignmentPreviewRead(BaseModel):
    task_type: str
    platform_code: str
    article_count: int
    account_count: int
    items: list[TaskAssignmentPreviewItemRead]


class TaskRead(BaseModel):
    id: int
    name: str
    task_type: str
    status: str
    platform_id: int
    platform_code: str
    article_id: int | None
    group_id: int | None
    stop_before_publish: bool
    accounts: list[TaskAccountRead]
    record_count: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class ManualConfirmInput(BaseModel):
    outcome: str
    publish_url: str | None = None
    error_message: str | None = None
