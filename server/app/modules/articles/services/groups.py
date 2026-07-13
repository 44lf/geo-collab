"""文章分组：普通分组 CRUD、成员替换与整组审核。"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from server.app.core.time import utcnow
from server.app.modules.articles.models import Article, ArticleGroup, ArticleGroupItem
from server.app.modules.articles.schemas import (
    ArticleGroupCreate,
    ArticleGroupItemsUpdate,
    ArticleGroupUpdate,
)
from server.app.modules.articles.services.review import _set_article_review_status
from server.app.modules.tasks.models import PublishTask
from server.app.shared.errors import ClientError, ConflictError


def get_group(db: Session, group_id: int) -> ArticleGroup | None:
    stmt = (
        select(ArticleGroup)
        .where(ArticleGroup.id == group_id, ArticleGroup.is_deleted == False)  # noqa: E712
        .options(selectinload(ArticleGroup.items).selectinload(ArticleGroupItem.article))
    )
    return db.execute(stmt).scalar_one_or_none()


def list_groups(db: Session) -> list[ArticleGroup]:
    stmt = (
        select(ArticleGroup)
        .where(ArticleGroup.is_deleted == False)  # noqa: E712
        .options(selectinload(ArticleGroup.items))
        .order_by(ArticleGroup.updated_at.desc())
    )
    return list(db.execute(stmt).scalars().all())


def create_group(db: Session, user_id: int, payload: ArticleGroupCreate) -> ArticleGroup:
    """新建分组。撞到同名软删分组则原地复活（清空成员、刷新元数据），绕开 (user_id, name) 唯一约束。"""
    existing = db.execute(
        select(ArticleGroup).where(
            ArticleGroup.user_id == user_id, ArticleGroup.name == payload.name
        )
    ).scalar_one_or_none()
    # 同名分组已软删 → 复活而非新建（否则唯一约束会冲突）
    if existing is not None and existing.is_deleted:
        existing.description = payload.description
        existing.is_deleted = False
        existing.deleted_at = None
        existing.version += 1
        existing.updated_at = utcnow()
        existing.items.clear()
        db.flush()
        return get_group(db, existing.id) or existing

    group = ArticleGroup(user_id=user_id, name=payload.name, description=payload.description)
    db.add(group)
    db.flush()
    return get_group(db, group.id) or group


def update_group(db: Session, group: ArticleGroup, payload: ArticleGroupUpdate) -> ArticleGroup:
    update_data = payload.model_dump(exclude_unset=True)
    expected_version = update_data.pop("version", None)
    if expected_version is not None and group.version != expected_version:
        raise ConflictError("Article group has been modified; refresh before saving")

    for field in ("name", "description"):
        if field in update_data:
            setattr(group, field, update_data[field])
    group.version += 1
    group.updated_at = utcnow()
    db.flush()
    return get_group(db, group.id) or group


def replace_group_items(
    db: Session, group: ArticleGroup, payload: ArticleGroupItemsUpdate
) -> ArticleGroup:
    """整组替换成员（先校验去重 + 文章存在，再清空重建）。乐观锁，未传 sort_order 用下标兜底。"""
    if payload.version is not None and group.version != payload.version:
        raise ConflictError("Article group has been modified; refresh before saving")

    seen: set[int] = set()
    article_ids: list[int] = []
    for item in payload.items:
        if item.article_id in seen:
            raise ClientError(f"Duplicate article_id: {item.article_id}")
        seen.add(item.article_id)
        article_ids.append(item.article_id)

    if article_ids:
        existing_ids = set(
            db.execute(
                select(Article.id).where(
                    Article.id.in_(article_ids),
                    Article.is_deleted == False,  # noqa: E712
                )
            )
            .scalars()
            .all()
        )
        missing_ids = [aid for aid in article_ids if aid not in existing_ids]
        if missing_ids:
            raise ClientError(f"Article not found: {missing_ids[0]}")

    group.items.clear()
    db.flush()
    for index, item in enumerate(payload.items):
        group.items.append(
            ArticleGroupItem(
                article_id=item.article_id,
                sort_order=item.sort_order if item.sort_order is not None else index,
            )
        )
    group.updated_at = utcnow()
    group.version += 1
    db.flush()
    return get_group(db, group.id) or group


def delete_group(db: Session, group: ArticleGroup) -> None:
    """软删除分组。存在 pending/running 的发布任务则拒删。"""
    active_task = db.execute(
        select(PublishTask.id).where(
            PublishTask.group_id == group.id,
            PublishTask.status.in_(["pending", "running"]),
        )
    ).scalar_one_or_none()
    if active_task:
        raise ClientError("存在未完成发布任务，无法删除分组")

    group.is_deleted = True
    group.deleted_at = utcnow()
    group.updated_at = utcnow()
    db.flush()


def compute_group_review_summary(db: Session, group_id: int) -> tuple[int, int]:
    """返回 (total, approved)：组内未删除文章总数 / 已审核数。

    「整组已审核」由调用方判断：approved == total and total > 0。
    """
    base = (
        select(ArticleGroupItem.article_id)
        .join(Article, Article.id == ArticleGroupItem.article_id)
        .where(
            ArticleGroupItem.group_id == group_id,
            Article.is_deleted == False,  # noqa: E712
        )
    )
    total = db.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    approved = db.execute(
        select(func.count()).select_from(base.where(Article.review_status == "approved").subquery())
    ).scalar_one()
    return int(total), int(approved)


def approve_group(db: Session, group_id: int, user_id: int, role: str) -> ArticleGroup:
    """把组内所有未删除文章置 review_status='approved'（version+1）。"""
    group = get_group(db, group_id)
    if group is None or (role != "admin" and group.user_id != user_id):
        raise ClientError("文章分组不存在")

    article_ids = [item.article_id for item in group.items]
    if article_ids:
        articles = list(
            db.execute(
                select(Article).where(
                    Article.id.in_(article_ids),
                    Article.is_deleted == False,  # noqa: E712
                    Article.review_status != "approved",
                )
            )
            .scalars()
            .all()
        )
        for article in articles:
            _set_article_review_status(article, "approved")
        db.flush()
    return get_group(db, group_id) or group
