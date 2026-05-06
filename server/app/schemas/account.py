from datetime import datetime

from pydantic import BaseModel, Field


class AccountRead(BaseModel):
    id: int
    platform_code: str
    platform_name: str
    display_name: str
    platform_user_id: str | None
    status: str
    last_checked_at: datetime | None
    last_login_at: datetime | None
    state_path: str
    note: str | None
    created_at: datetime
    updated_at: datetime


class ToutiaoLoginRequest(BaseModel):
    display_name: str = Field(default="头条号账号", min_length=1, max_length=200)
    account_key: str | None = Field(default=None, max_length=120)
    channel: str = "chrome"
    executable_path: str | None = None
    wait_seconds: int = Field(default=180, ge=5, le=600)
    use_browser: bool = True
    note: str | None = None


class AccountCheckRequest(BaseModel):
    channel: str = "chrome"
    executable_path: str | None = None
    wait_seconds: int = Field(default=30, ge=3, le=180)
    use_browser: bool = True


class AccountExportRequest(BaseModel):
    account_ids: list[int] | None = None
