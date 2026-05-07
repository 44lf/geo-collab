from datetime import datetime

from pydantic import BaseModel, Field


# 任务创建中的账号输入
class TaskAccountInput(BaseModel):
    account_id: int
    sort_order: int | None = None  # 执行顺序


# 任务创建请求体
class TaskCreate(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    task_type: str  # single / group_round_robin
    article_id: int | None = None  # 单篇任务时必填
    group_id: int | None = None  # 分组轮询时必填
    platform_code: str = "toutiao"
    accounts: list[TaskAccountInput]
    stop_before_publish: bool = True  # 默认需要手动确认


# 任务详情中的账号信息
class TaskAccountRead(BaseModel):
    account_id: int
    sort_order: int
    display_name: str
    status: str


# 发布记录响应
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


# 任务日志响应
class TaskLogRead(BaseModel):
    id: int
    task_id: int
    record_id: int | None
    level: str  # info / warn / error
    message: str
    screenshot_asset_id: str | None
    created_at: datetime


# 任务分配预览中的单项
class TaskAssignmentPreviewItemRead(BaseModel):
    position: int
    article_id: int
    account_id: int
    account_sort_order: int


# 任务分配预览
class TaskAssignmentPreviewRead(BaseModel):
    task_type: str
    platform_code: str
    article_count: int
    account_count: int
    items: list[TaskAssignmentPreviewItemRead]


# 任务响应体
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


# 手动确认发布的输入
class ManualConfirmInput(BaseModel):
    outcome: str  # succeeded / failed
    publish_url: str | None = None
    error_message: str | None = None
