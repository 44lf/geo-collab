"""单篇文章 CRUD、封面，以及文章状态常量与校验。

约定（与原 service.py 一致，不改语义）：
  - 软删除（is_deleted），查询一律过滤 is_deleted == False。
  - 乐观锁：update_* 用 payload.version 对比 article.version，不一致抛 ConflictError；每次写 version+1。
  - PATCH 语义：update_article 跳过值为 None 的字段（见 CLAUDE.md「ArticleUpdate 丢 null」），
    唯一例外是 stock_category_id 允许显式置 None 来解除关联。
  - 正文三份存储（content_json / content_html / plain_text）+ body_assets 由调用方/sync 同步。
"""

from __future__ import annotations

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from server.app.core.time import utcnow
from server.app.modules.articles.models import (
    Article,
    ArticleBodyAsset,
    ArticleGroupItem,
)
from server.app.modules.articles.parser import (
    dumps_content_json,
    loads_content_json,
)
from server.app.modules.articles.schemas import ArticleCreate, ArticleUpdate
from server.app.modules.articles.services.body_assets import (
    ensure_asset_exists,
    sync_article_body_assets,
)
from server.app.modules.tasks.models import PublishRecord
from server.app.shared.errors import ClientError, ConflictError

VALID_ARTICLE_STATUSES = {"draft", "ready", "archived"}
VALID_REVIEW_STATUSES = {"pending", "approved"}


def validate_article_status(status: str) -> None:
    if status not in VALID_ARTICLE_STATUSES:
        raise ClientError(f"Invalid article status: {status}")


def get_article(db: Session, article_id: int) -> Article | None:
    stmt = (
        select(Article)
        .where(Article.id == article_id, Article.is_deleted == False)  # noqa: E712
        .options(
            selectinload(Article.body_assets).selectinload(ArticleBodyAsset.asset),
            selectinload(Article.stock_categories),
        )
    )
    return db.execute(stmt).scalar_one_or_none()


def create_article(db: Session, user_id: int, payload: ArticleCreate) -> Article:
    """新建文章。client_request_id 做幂等：已存在同 request_id 的文章直接返回，不重复创建。"""
    # 软幂等：先按 client_request_id 全局预查（不限 user）；并发下由 per-user 唯一约束 uq_articles_user_client_request_id 兜底（router 捕 IntegrityError 再按 user 查一次）
    if payload.client_request_id:
        existing = db.execute(
            select(Article).where(
                Article.client_request_id == payload.client_request_id,
                Article.is_deleted == False,  # noqa: E712
            )
        ).scalar_one_or_none()
        if existing is not None:
            return get_article(db, existing.id) or existing

    validate_article_status(payload.status)
    ensure_asset_exists(db, payload.cover_asset_id)
    article = Article(
        user_id=user_id,
        title=payload.title,
        author=payload.author,
        cover_asset_id=payload.cover_asset_id,
        content_json=dumps_content_json(payload.content_json),
        content_html=payload.content_html,
        plain_text=payload.plain_text,
        word_count=payload.word_count,
        status=payload.status,
        client_request_id=payload.client_request_id,
    )
    sync_article_body_assets(db, article, payload.content_json)
    db.add(article)
    db.flush()
    return get_article(db, article.id) or article


def update_article(db: Session, article: Article, payload: ArticleUpdate) -> Article:
    """局部更新文章（乐观锁 + None 跳过语义）。改 content_json 时同步 body_assets 并重算 version。"""
    update_data = payload.model_dump(exclude_unset=True)
    expected_version = update_data.pop("version", None)
    if expected_version is not None and article.version != expected_version:
        raise ConflictError("Article has been modified; refresh before saving")

    if "status" in update_data and update_data["status"] is not None:
        validate_article_status(update_data["status"])
    if "cover_asset_id" in update_data:
        ensure_asset_exists(db, update_data["cover_asset_id"])

    content_json = loads_content_json(article.content_json)
    if "content_json" in update_data and update_data["content_json"] is not None:
        content_json = update_data["content_json"]

    # 显式过滤 None：PATCH {"field": null} 不会清空字段（见 CLAUDE.md「ArticleUpdate 丢 null」）
    for field in (
        "title",
        "author",
        "cover_asset_id",
        "content_html",
        "plain_text",
        "word_count",
        "status",
    ):
        if field in update_data and update_data[field] is not None:
            setattr(article, field, update_data[field])
    # stock_category_id 允许显式置 None（移除关联）
    if "stock_category_id" in update_data:
        article.stock_category_id = update_data["stock_category_id"]

    # 多对多栏目：如果传了 stock_category_ids，更新关联表
    if "stock_category_ids" in update_data:
        from server.app.modules.image_library.models import StockCategory as _StockCategory

        cat_ids = update_data["stock_category_ids"] or []
        if cat_ids:
            cats = list(
                db.execute(select(_StockCategory).where(_StockCategory.id.in_(cat_ids)))
                .scalars()
                .all()
            )
        else:
            cats = []
        article.stock_categories = cats
    elif "stock_category_id" in update_data and update_data["stock_category_id"] is not None:
        # 兼容旧字段：如果只传了 stock_category_id 且多对多列表为空，把旧值塞进多对多
        from server.app.modules.image_library.models import StockCategory as _StockCategory

        if not article.stock_categories:
            cat = db.get(_StockCategory, update_data["stock_category_id"])
            if cat is not None:
                article.stock_categories = [cat]

    if "content_json" in update_data:
        article.content_json = dumps_content_json(content_json)
        sync_article_body_assets(db, article, content_json)

    article.version += 1
    article.updated_at = utcnow()
    db.flush()
    return get_article(db, article.id) or article


def set_article_cover(db: Session, article: Article, cover_asset_id: str | None) -> Article:
    ensure_asset_exists(db, cover_asset_id)
    article.cover_asset_id = cover_asset_id
    article.version += 1
    article.updated_at = utcnow()
    db.flush()
    return get_article(db, article.id) or article


def delete_article(db: Session, article: Article) -> None:
    """软删除文章。存在未完成发布记录则拒删；删前清掉其所有分组关联（硬删 ArticleGroupItem）。"""
    article_id = article.id

    active = (
        db.execute(
            select(PublishRecord.id).where(
                PublishRecord.article_id == article_id,
                PublishRecord.status.in_(
                    ["pending", "running", "waiting_manual_publish", "waiting_user_input"]
                ),
            )
        )
        .scalars()
        .all()
    )
    if active:
        raise ClientError("存在未完成发布记录，无法删除文章")

    db.execute(sa_delete(ArticleGroupItem).where(ArticleGroupItem.article_id == article_id))
    article.is_deleted = True
    article.deleted_at = utcnow()
    article.updated_at = utcnow()
    db.flush()
