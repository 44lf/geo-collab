"""quality_reference CRUD 路由（对抗评审质量门）：采纳 / 导入 / 列表 / 详情 / 下架 / 聚合。

全部走 user JWT（`Depends(get_current_user)`），无独立 MCP token 路径——本模块只服务前端
高质量库页，不接 Claude Code Loop（选参考走的是 pipelines/scheme_router 内部调用 svc.pick_references）。
"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from server.app.core.security import get_current_user
from server.app.db.session import get_db
from server.app.modules.ai_generation.models import QuestionItem
from server.app.modules.quality_reference import service as svc
from server.app.modules.quality_reference.models import QualityReference
from server.app.modules.quality_reference.schemas import (
    AdoptRequest,
    ImportRequest,
    ImportResponse,
    PatchRequest,
    QualityReferenceDetail,
    QualityReferenceRead,
)

quality_reference_router = APIRouter()


@quality_reference_router.post("/adopt", response_model=QualityReferenceRead)
def adopt(p: AdoptRequest, db: Session = Depends(get_db), user=Depends(get_current_user)):
    ref = svc.adopt_article(db, user_id=user.id, article_id=p.article_id)
    if p.category and ref.category is None:  # 文章无分类时用前端补选
        ref.category = p.category
    db.commit()
    db.refresh(ref)
    return ref


@quality_reference_router.post("/import", response_model=ImportResponse)
def import_ext(p: ImportRequest, db: Session = Depends(get_db), user=Depends(get_current_user)):
    ref, similar = svc.import_external(
        db,
        user_id=user.id,
        title=p.title,
        markdown=p.markdown,
        category=p.category,
        source_url=p.source_url,
        platform=p.platform,
    )
    db.commit()
    db.refresh(ref)
    return ImportResponse(reference=ref, similar=similar)


@quality_reference_router.get("", response_model=list[QualityReferenceRead])
def list_refs(
    origin: str | None = None,
    category: str | None = None,
    is_active: bool | None = None,
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    return svc.list_references(
        db, origin=origin, category=category, is_active=is_active, skip=skip, limit=limit
    )


# 静态路径 /categories /stats 必须注册在动态 /{ref_id:int} 之前；详情/patch 用 :int 转换器双保险
# （否则 GET /categories 会命中 /{ref_id} 做 int 校验 → 422；现有文章路由同款纪律 articles.py:137）
@quality_reference_router.get("/categories", response_model=list[str])
def categories(db: Session = Depends(get_db), user=Depends(get_current_user)):
    q = (
        db.execute(
            select(QuestionItem.category).where(QuestionItem.category.isnot(None)).distinct()
        )
        .scalars()
        .all()
    )
    r = (
        db.execute(
            select(QualityReference.category)
            .where(QualityReference.category.isnot(None))
            .distinct()
        )
        .scalars()
        .all()
    )
    return sorted({*q, *r})


@quality_reference_router.get("/stats", response_model=list[dict])
def stats(db: Session = Depends(get_db), user=Depends(get_current_user)):
    # 按类目聚合 external/own 计数 → 前端显配比 + 「某类目无 external」告警（命门风险的可见化缓解）
    return svc.category_origin_stats(db)


@quality_reference_router.get("/{ref_id:int}", response_model=QualityReferenceDetail)
def detail(ref_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    ref = svc.get_reference(db, ref_id)
    out = QualityReferenceDetail.model_validate(ref)
    out.source_article_deleted = svc.is_source_article_deleted(
        db, ref
    )  # 软删不触发 SET NULL，需显式判
    return out


@quality_reference_router.patch("/{ref_id:int}", response_model=QualityReferenceRead)
def patch(
    ref_id: int,
    p: PatchRequest,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    ref = svc.patch_reference(
        db,
        ref_id,
        is_active=p.is_active,
        category=p.category if p.category is not None else svc._UNSET,
    )
    db.commit()
    db.refresh(ref)
    return ref
