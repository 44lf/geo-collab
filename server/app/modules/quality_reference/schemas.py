"""quality_reference 请求/响应 schema（对抗评审质量门）。"""

from datetime import datetime

from pydantic import BaseModel, Field


class AdoptRequest(BaseModel):
    article_id: int
    category: str | None = Field(
        default=None, max_length=200
    )  # 文章无 source_question_category 时前端补选


class ImportRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    markdown: str = Field(min_length=1)
    category: str | None = Field(default=None, max_length=200)
    source_url: str | None = Field(default=None, max_length=1000)
    platform: str | None = Field(default=None, max_length=100)


class PatchRequest(BaseModel):
    is_active: bool | None = None
    category: str | None = None


class QualityReferenceRead(BaseModel):  # 列表用，轻量，不含正文
    id: int
    origin: str
    article_id: int | None
    title: str
    category: str | None
    source_url: str | None
    platform: str | None
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class QualityReferenceDetail(QualityReferenceRead):  # 详情用，带三份正文（只读 Tiptap 渲染）
    content_json: str
    content_html: str
    plain_text: str
    source_article_deleted: bool = False  # own 参考源文章已软删/物理删时前端提示


class ImportResponse(BaseModel):
    reference: QualityReferenceRead
    similar: list[dict]  # near-dup 疑似重复（不硬挡）
