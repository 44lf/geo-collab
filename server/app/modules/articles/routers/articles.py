"""文章 CRUD、审核、封面与 AI 排版触发路由。"""

import logging
import threading
from datetime import UTC, datetime

from fastapi import (
    APIRouter,
    Body,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from server.app.core.config import get_settings
from server.app.core.security import get_current_user
from server.app.db.session import get_db
from server.app.modules.articles import (
    VALID_REVIEW_STATUSES,
    approve_article,
    create_article,
    delete_article,
    get_article,
    list_article_feed,
    list_articles,
    revoke_article_approval,
    serialize_article_summaries,
    set_article_cover,
    update_article,
)
from server.app.modules.articles.models import Article
from server.app.modules.articles.schemas import (
    ArticleCoverUpdate,
    ArticleCreate,
    ArticleFeedResponse,
    ArticleListRead,
    ArticleRead,
    ArticleUpdate,
    to_article_read,
)
from server.app.modules.audit.service import add_audit_entry
from server.app.modules.system.models import User
from server.app.shared.errors import ClientError, ConflictError

articles_router = APIRouter()


class AIFormatRequest(BaseModel):
    preset_id: int | None = None


# ── 文章辅助函数 ────────────────────────────────────────────────────────────


def _verify_article_ownership(article: Article | None, current_user: User) -> Article:
    if article is None:
        raise HTTPException(status_code=404, detail="文章不存在")
    if current_user.role != "admin" and article.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="文章不存在")
    return article


def _is_ai_lock_expired(article: Article) -> bool:
    # 排版锁是否超时：started 为空（异常状态）视为过期；否则超过 ai_format_timeout_seconds 即过期
    if not article.ai_checking:
        return False
    started = article.ai_checking_started_at
    if started is None:
        return True
    elapsed = (datetime.now(UTC).replace(tzinfo=None) - started).total_seconds()
    return elapsed >= get_settings().ai_format_timeout_seconds


def _clear_ai_lock_if_expired(db: Session, article: Article) -> None:
    # 惰性解锁：读/改文章时顺手清掉超时未释放的排版锁（后台线程崩了也不会让文章永久卡 ai_checking）
    if not _is_ai_lock_expired(article):
        return
    article.ai_checking = False
    article.ai_checking_started_at = None
    article.ai_format_error = "AI 排版超时：模型服务响应超时或后台任务未完成，请重试。"
    db.commit()
    db.refresh(article)


def _check_not_ai_locked(db: Session, article: Article) -> None:
    """文章正在进行 AI 排版时抛 ConflictError。"""
    _clear_ai_lock_if_expired(db, article)
    if not article.ai_checking:
        return
    raise ConflictError("文章正在进行 AI 格式调整，请稍后再试")


# ── 文章路由 ────────────────────────────────────────────────────────────────


@articles_router.get("", response_model=list[ArticleListRead])
def read_articles(
    q: str | None = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, le=200),
    review_status: str | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ArticleListRead]:
    if review_status is not None and review_status not in VALID_REVIEW_STATUSES:
        raise ClientError(f"Invalid review_status: {review_status}")
    articles = list_articles(
        db,
        q,
        skip=skip,
        limit=limit,
        user_id=None if current_user.role == "admin" else current_user.id,
        review_status=review_status,
    )
    if not articles:
        return []
    summaries = serialize_article_summaries(db, articles)
    return [summaries[a.id] for a in articles]


@articles_router.get("/feed", response_model=ArticleFeedResponse)
def read_article_feed(
    q: str | None = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=10, ge=1, le=100),
    review_status: str = Query(default="pending"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ArticleFeedResponse:
    # 静态段 /feed 必须在 /{article_id:int} 之前注册（本函数在 read_articles 与 get_article 之间），
    # 否则 "feed" 会被当成 article_id。
    if review_status not in VALID_REVIEW_STATUSES:
        raise ClientError(f"Invalid review_status: {review_status}")
    return list_article_feed(
        db,
        review_status=review_status,
        query=q,
        skip=skip,
        limit=limit,
        user_id=None if current_user.role == "admin" else current_user.id,
    )


@articles_router.post("", response_model=ArticleRead)
def create_article_endpoint(
    payload: ArticleCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ArticleRead:
    try:
        created = create_article(db, current_user.id, payload)
        add_audit_entry(
            db,
            user=current_user,
            action="article.create",
            target_type="article",
            target_id=created.id,
            payload={"title": created.title},
            request=request,
        )
        return to_article_read(created)
    except IntegrityError as exc:
        db.rollback()
        if payload.client_request_id:
            existing = db.execute(
                select(Article).where(
                    Article.client_request_id == payload.client_request_id,
                    Article.user_id == current_user.id,
                    Article.is_deleted == False,  # noqa: E712
                )
            ).scalar_one_or_none()
            if existing is not None:
                # 幂等重试：并发请求已经创建了这篇文章。
                return to_article_read(get_article(db, existing.id) or existing)
        # 上面的幂等查询无法消解的 IntegrityError 都是真实约束冲突；
        # 明确抛 409，避免隐式 return None 被序列化成不透明的 500。
        raise HTTPException(
            status_code=409, detail="请求冲突：client_request_id 已存在或数据完整性约束失败"
        ) from exc


@articles_router.get("/{article_id}", response_model=ArticleRead)
def read_article(
    article_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ArticleRead:
    article = get_article(db, article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="文章不存在")
    _clear_ai_lock_if_expired(db, article)
    can_edit = (article.user_id == current_user.id) or (current_user.role == "admin")
    return to_article_read(article, can_edit=can_edit)


@articles_router.put("/{article_id}", response_model=ArticleRead)
def update_article_endpoint(
    article_id: int,
    payload: ArticleUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ArticleRead:
    article = _verify_article_ownership(get_article(db, article_id), current_user)
    _check_not_ai_locked(db, article)
    changed_fields = sorted(payload.model_dump(exclude_unset=True).keys())
    updated = update_article(db, article, payload)
    add_audit_entry(
        db,
        user=current_user,
        action="article.update",
        target_type="article",
        target_id=article_id,
        payload={"changed_fields": changed_fields},
        request=request,
    )
    return to_article_read(updated)


@articles_router.delete("/{article_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_article_endpoint(
    article_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    article = _verify_article_ownership(get_article(db, article_id), current_user)
    _check_not_ai_locked(db, article)
    article_title = article.title
    delete_article(db, article)
    add_audit_entry(
        db,
        user=current_user,
        action="article.delete",
        target_type="article",
        target_id=article_id,
        payload={"title": article_title},
        request=request,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@articles_router.post("/{article_id}/cover", response_model=ArticleRead)
def update_article_cover(
    article_id: int,
    payload: ArticleCoverUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ArticleRead:
    article = _verify_article_ownership(get_article(db, article_id), current_user)
    if payload.version is not None and article.version != payload.version:
        raise ConflictError("文章已被修改，请刷新后再保存")
    updated = set_article_cover(db, article, payload.cover_asset_id)
    add_audit_entry(
        db,
        user=current_user,
        action="article.cover.update",
        target_type="article",
        target_id=article_id,
        payload={"asset_id": payload.cover_asset_id},
        request=request,
    )
    return to_article_read(updated)


@articles_router.post("/{article_id}/approve", response_model=ArticleRead)
def approve_article_endpoint(
    article_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ArticleRead:
    article = _verify_article_ownership(get_article(db, article_id), current_user)
    updated = approve_article(db, article.id, current_user.id, current_user.role)
    add_audit_entry(
        db,
        user=current_user,
        action="article.review.approve",
        target_type="article",
        target_id=article_id,
        payload=None,
        request=request,
    )
    return to_article_read(updated)


@articles_router.post("/{article_id}/revoke-approval", response_model=ArticleRead)
def revoke_article_approval_endpoint(
    article_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ArticleRead:
    article = _verify_article_ownership(get_article(db, article_id), current_user)
    updated = revoke_article_approval(db, article.id, current_user.id, current_user.role)
    add_audit_entry(
        db,
        user=current_user,
        action="article.review.revoke",
        target_type="article",
        target_id=article_id,
        payload=None,
        request=request,
    )
    return to_article_read(updated)


@articles_router.post("/{article_id}/ai-format", status_code=202)
def trigger_ai_format_endpoint(
    article_id: int,
    request: Request,
    payload: AIFormatRequest | None = Body(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    """触发 AI 排版：抢锁（置 ai_checking）后立即启动后台线程跑 run_ai_format，202 返回。

    is_checking 期间不可重复触发；含配图栏目时附带自动配图。线程崩溃由 _run 的 except 兜底解锁。
    """
    article = _verify_article_ownership(get_article(db, article_id), current_user)
    _check_not_ai_locked(db, article)
    from server.app.modules.articles.ai_format import has_ai_format_targets

    if not has_ai_format_targets(article.content_json):
        raise ClientError("文章正文为空，无法进行 AI 格式调整")

    preset_id = (
        payload.preset_id
        if payload and payload.preset_id is not None
        else current_user.ai_format_preset_id
    )
    if preset_id is not None:
        from server.app.modules.prompt_templates.service import get_visible_prompt_template

        preset = get_visible_prompt_template(
            db, preset_id, user_id=current_user.id, scope="ai_format"
        )
        if preset is None or not preset.is_enabled:
            raise HTTPException(status_code=404, detail="AI format prompt preset not found")

    # lock_started_at 当锁指纹传给后台线程，run_ai_format 写回前据此判断锁是否仍属本次
    lock_started_at = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    include_images = (
        article.stock_category_id is not None or len(article.stock_categories or []) > 0
    )
    # 抢锁并先 commit：让后台线程和后续请求都能立刻看到 ai_checking=True
    article.ai_checking = True
    article.ai_checking_started_at = lock_started_at
    article.ai_format_error = None
    db.commit()

    def _run() -> None:
        try:
            from server.app.modules.articles.ai_format import run_ai_format

            run_ai_format(
                article_id,
                include_images=include_images,
                lock_started_at=lock_started_at,
                preset_id=preset_id,
                user_id=current_user.id,
            )
        except Exception as exc:
            # 线程崩溃兜底：run_ai_format 内部异常已自解锁，但若它在解锁前就崩了，
            # 这里用独立 session 强制解锁，绝不让文章永久卡在 ai_checking=True
            logging.getLogger(__name__).exception(
                "ai_format background thread crashed for article %s", article_id
            )
            try:
                from server.app.db.session import SessionLocal
                from server.app.modules.articles.ai_format import (
                    _describe_ai_format_error,
                    _unlock_ai_format,
                )

                cleanup_db = SessionLocal()
                try:
                    _unlock_ai_format(
                        cleanup_db,
                        article_id,
                        lock_started_at,
                        error_message=_describe_ai_format_error(exc),
                    )
                finally:
                    cleanup_db.close()
            except Exception:
                pass

    threading.Thread(target=_run, daemon=True).start()
    add_audit_entry(
        db,
        user=current_user,
        action="article.ai_format.trigger",
        target_type="article",
        target_id=article_id,
        payload=None,
        request=request,
    )
    return {"status": "started"}
