from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from server.app.core.time import utcnow
from server.app.models import Article, ArticleGroup, ArticleGroupItem
from server.app.services.errors import ConflictError
from server.app.schemas.article_group import (
    ArticleGroupCreate,
    ArticleGroupItemRead,
    ArticleGroupItemsUpdate,
    ArticleGroupRead,
    ArticleGroupUpdate,
)


# 获取单个分组（含文章列表）
def get_group(db: Session, group_id: int) -> ArticleGroup | None:
    stmt = (
        select(ArticleGroup)
        .where(ArticleGroup.id == group_id)
        .options(selectinload(ArticleGroup.items).selectinload(ArticleGroupItem.article))
    )
    return db.execute(stmt).scalar_one_or_none()


# 获取所有分组列表
def list_groups(db: Session) -> list[ArticleGroup]:
    stmt = select(ArticleGroup).options(selectinload(ArticleGroup.items)).order_by(ArticleGroup.updated_at.desc())
    return list(db.execute(stmt).scalars().all())


# 创建新分组
def create_group(db: Session, payload: ArticleGroupCreate) -> ArticleGroup:
    group = ArticleGroup(name=payload.name, description=payload.description)
    db.add(group)
    db.flush()
    return get_group(db, group.id) or group


# 更新分组信息
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


# 全量替换分组中的文章列表（先清空再插入）
def replace_group_items(db: Session, group: ArticleGroup, payload: ArticleGroupItemsUpdate) -> ArticleGroup:
    if payload.version is not None and group.version != payload.version:
        raise ConflictError("Article group has been modified; refresh before saving")

    seen: set[int] = set()
    article_ids: list[int] = []
    for item in payload.items:
        if item.article_id in seen:
            raise ValueError(f"Duplicate article_id: {item.article_id}")
        seen.add(item.article_id)
        article_ids.append(item.article_id)

    if article_ids:
        existing_ids = set(db.execute(select(Article.id).where(Article.id.in_(article_ids))).scalars().all())
        missing_ids = [article_id for article_id in article_ids if article_id not in existing_ids]
        if missing_ids:
            raise ValueError(f"Article not found: {missing_ids[0]}")

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


# 删除分组
def delete_group(db: Session, group: ArticleGroup) -> None:
    db.delete(group)
    db.flush()


# 将 ORM ArticleGroup 转为响应体
def to_group_read(group: ArticleGroup) -> ArticleGroupRead:
    items = sorted(group.items, key=lambda item: item.sort_order)
    return ArticleGroupRead(
        id=group.id,
        name=group.name,
        description=group.description,
        version=group.version,
        items=[
            ArticleGroupItemRead(
                article_id=item.article_id,
                sort_order=item.sort_order,
            )
            for item in items
        ],
        created_at=group.created_at,
        updated_at=group.updated_at,
    )
