from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# 文章正文中的图片信息
class ArticleBodyAssetRead(BaseModel):
    asset_id: str
    position: int
    editor_node_id: str | None = None  # Tiptap 编辑器节点 ID


# 文章基础信息（创建时使用）
class ArticleBase(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    author: str | None = Field(default=None, max_length=200)
    cover_asset_id: str | None = None
    content_json: dict[str, Any] = Field(default_factory=dict)  # Tiptap JSON
    content_html: str = ""
    plain_text: str = ""
    word_count: int = 0
    status: str = "draft"  # draft / ready / archived


class ArticleCreate(ArticleBase):
    pass


# 文章更新请求（所有字段可选）
class ArticleUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    author: str | None = Field(default=None, max_length=200)
    cover_asset_id: str | None = None
    content_json: dict[str, Any] | None = None
    content_html: str | None = None
    plain_text: str | None = None
    word_count: int | None = None
    status: str | None = None


# 仅更新文章封面
class ArticleCoverUpdate(BaseModel):
    cover_asset_id: str | None = None


# 文章完整响应体
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
    published_count: int = 0  # 成功发布次数
    created_at: datetime
    updated_at: datetime

