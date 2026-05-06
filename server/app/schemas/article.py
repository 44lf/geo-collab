from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ArticleBodyAssetRead(BaseModel):
    asset_id: str
    position: int
    editor_node_id: str | None = None


class ArticleBase(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    author: str | None = Field(default=None, max_length=200)
    cover_asset_id: str | None = None
    content_json: dict[str, Any] = Field(default_factory=dict)
    content_html: str = ""
    plain_text: str = ""
    word_count: int = 0
    status: str = "draft"


class ArticleCreate(ArticleBase):
    pass


class ArticleUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    author: str | None = Field(default=None, max_length=200)
    cover_asset_id: str | None = None
    content_json: dict[str, Any] | None = None
    content_html: str | None = None
    plain_text: str | None = None
    word_count: int | None = None
    status: str | None = None


class ArticleCoverUpdate(BaseModel):
    cover_asset_id: str | None = None


class ArticleRead(BaseModel):
    id: int
    title: str
    author: str | None
    cover_asset_id: str | None
    content_json: dict[str, Any]
    content_html: str
    plain_text: str
    word_count: int
    status: str
    body_assets: list[ArticleBodyAssetRead]
    created_at: datetime
    updated_at: datetime

