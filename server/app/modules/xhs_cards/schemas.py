"""小红书卡片渲染请求 / 响应 schema。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ComposeXhsRequest(BaseModel):
    render_markdown: str = Field(min_length=1)
    theme: str = "sketch"
    mode: str = "separator"
    width: int = Field(default=1080, ge=320, le=2160)
    dpr: int = Field(default=2, ge=1, le=3)
    source_article_id: int | None = None
