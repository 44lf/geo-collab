from datetime import datetime

from pydantic import BaseModel, Field


class ArticleGroupBase(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None


class ArticleGroupCreate(ArticleGroupBase):
    pass


class ArticleGroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    version: int | None = Field(default=None, ge=1)


# 分组中的文章输入项
class ArticleGroupItemInput(BaseModel):
    article_id: int
    sort_order: int | None = None


# 批量更新分组文章
class ArticleGroupItemsUpdate(BaseModel):
    items: list[ArticleGroupItemInput]
    version: int | None = Field(default=None, ge=1)


class ArticleGroupItemRead(BaseModel):
    article_id: int
    sort_order: int


class ArticleGroupRead(BaseModel):
    id: int
    name: str
    description: str | None
    version: int
    items: list[ArticleGroupItemRead]
    created_at: datetime
    updated_at: datetime
