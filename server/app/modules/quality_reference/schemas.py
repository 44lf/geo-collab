"""quality_reference 请求/响应 schema（对抗评审质量门）。"""

from datetime import datetime

from pydantic import BaseModel, Field


class CategoryAssoc(BaseModel):  # 一条问题类型关联（子表行）
    category: str = Field(min_length=1, max_length=200)
    question_texts: list | None = None  # 该类型下的问题词

    class Config:
        from_attributes = True


class AdoptRequest(BaseModel):
    article_id: int
    category: str | None = Field(
        default=None, max_length=200
    )  # 文章无 source_question_category 时前端补选（单值回落）


class ImportRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    # 正文两条路径（二选一）：
    # - 新：content_json（Tiptap 编辑器直出，含图片节点）+ content_html + plain_text
    # - 旧：markdown（向后兼容，后端 markdown→tiptap 转换，不含图片）
    content_json: str | None = (
        None  # 序列化后的 Tiptap doc（前端 JSON.stringify(editor.getJSON())）
    )
    content_html: str | None = None
    plain_text: str | None = None
    markdown: str | None = Field(default=None, min_length=1)
    category: str | None = Field(default=None, max_length=200)
    question_texts: list | None = None  # 单值 category 下的问题词（多类型走后续 patch replace-all）
    source_url: str | None = Field(default=None, max_length=1000)
    platform: str | None = Field(default=None, max_length=100)


class PatchRequest(BaseModel):
    is_active: bool | None = None
    categories: list[CategoryAssoc] | None = None  # 整体 replace-all（空数组=清空=通用；None=不改）


class QualityReferenceRead(BaseModel):  # 列表用，轻量，不含正文
    id: int
    origin: str
    article_id: int | None
    title: str
    categories: list[CategoryAssoc]  # 该参考挂的问题类型标签集（含问题词）
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
