"""quality_reference MCP 端点：GET /pick（挑参考）+ POST /articles/{id}/adversarial-score（记分）。

两条都走 MCP token 鉴权（与 user JWT cookie 隔离），供 /goal verifier 子代理调用：
- pick：包一层 svc.pick_references，k/truncate_chars 缺省落到 Settings 的
  adversarial_topk / adversarial_ref_truncate_chars。
- record_score：只写 `Article.adversarial_score` 一列（纯 advisory，不碰 review_status），
  过滤 is_deleted 且 404 命名异常 / HTTPException 不吞进 mcp_exception_response。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from server.app.core.config import get_settings
from server.app.core.mcp_auth import require_mcp_token
from server.app.core.mcp_errors import mcp_exception_response
from server.app.db.session import get_db
from server.app.modules.articles.models import Article
from server.app.modules.quality_reference import service as svc
from server.app.shared.errors import ClientError, ValidationError

quality_reference_mcp_router = APIRouter()


@quality_reference_mcp_router.get(
    "/quality-reference/pick",
    dependencies=[Depends(require_mcp_token)],
)
def pick(
    category: str | None = None,
    k: int | None = None,
    db: Session = Depends(get_db),
) -> dict:
    """[MCP] 挑参考文：同类目优先 external、own 补足，不足回落通用池。"""
    settings = get_settings()
    try:
        refs = svc.pick_references(
            db,
            category=category,
            k=(k or settings.adversarial_topk),
            truncate_chars=settings.adversarial_ref_truncate_chars,
        )
    except Exception as exc:
        raise mcp_exception_response(exc, context=f"pick category={category}") from exc
    return {"references": refs}


class AdoptFromMcpPayload(BaseModel):
    article_id: int
    category: str | None = Field(default=None, max_length=200)  # 无溯源类目时回落关联
    question_texts: list | None = None  # 与回落 category 配对；文章有溯源时忽略
    user_id: int = 1  # operator（Loop 身份）；tool 传 _OPERATOR_USER_ID，缺省回落 admin(1)


@quality_reference_mcp_router.post(
    "/quality-reference/adopt-from-mcp",
    dependencies=[Depends(require_mcp_token)],
)
def adopt_from_mcp(
    payload: AdoptFromMcpPayload,
    db: Session = Depends(get_db),
) -> dict:
    """[MCP] 采纳一篇【已过人审(approved)】站内文章进高质量库当对抗判分参考。

    复用 svc.adopt_article 的审核门禁：非 approved / 不存在文章抛命名异常（ClientError/
    ValidationError → 全局 handler 映射 400，与 record_score 一致）。同篇幂等（article_id
    UNIQUE，dup 复活不重复建）。**本端点只采纳站内已审文章**——真·站外文章（爬虫外部真品）走独立的 import-external
    异步入库端点（POST /api/quality-reference/import-external，source_url 溯源），此端点不注入
    外部内容、防 AI 自灌毒化。
    """
    try:
        ref = svc.adopt_article(
            db,
            user_id=payload.user_id,
            article_id=payload.article_id,
            fallback_category=payload.category,
            fallback_question_texts=payload.question_texts,
        )
        db.commit()
        db.refresh(ref)
    except (ClientError, ValidationError):
        db.rollback()
        raise  # 命名异常交全局 handler → 400，Loop 可读
    except Exception as exc:
        db.rollback()
        raise mcp_exception_response(
            exc, context=f"adopt_from_mcp article={payload.article_id}"
        ) from exc
    return {
        "id": ref.id,
        "origin": ref.origin,
        "article_id": ref.article_id,
        "title": ref.title,
        "is_active": ref.is_active,
        "categories": [c.category for c in ref.categories],
    }


class ScorePayload(BaseModel):
    score: int = Field(ge=0, le=100)


@quality_reference_mcp_router.post(
    "/articles/{article_id}/adversarial-score",
    dependencies=[Depends(require_mcp_token)],
)
def record_score(
    article_id: int,
    payload: ScorePayload,
    db: Session = Depends(get_db),
) -> dict:
    """[MCP] 写对抗评审分（纯 advisory，不改 review_status）。软删/不存在文章 404。"""
    try:
        article = (
            db.query(Article)
            .filter(Article.id == article_id, Article.is_deleted == False)  # noqa: E712
            .first()
        )
        if article is None:
            raise HTTPException(status_code=404, detail="article not found")
        article.adversarial_score = payload.score
        db.commit()
    except HTTPException:
        raise
    except (ClientError, ValidationError):
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise mcp_exception_response(exc, context=f"record_score article={article_id}") from exc
    return {"article_id": article_id, "adversarial_score": payload.score}
