"""
文章 / 文章分组业务逻辑层（增删改查 + 审核 + 全文检索）。

约定：
  - 软删除（is_deleted），查询一律过滤 is_deleted == False。
  - 乐观锁：update_* 用 payload.version 对比 article/group.version，不一致抛 ConflictError；每次写 version+1。
  - PATCH 语义：update_article 跳过值为 None 的字段（见 CLAUDE.md「ArticleUpdate 丢 null」），
    唯一例外是 stock_category_id 允许显式置 None 来解除关联。
  - 正文三份存储（content_json / content_html / plain_text）+ body_assets 由调用方/sync 同步。
  - review_status：pending=待审 / approved=已审，未过审不可发布。
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from server.app.core.time import utcnow
from server.app.modules.articles.models import (
    Article,
    ArticleGroup,
    ArticleGroupItem,
)
from server.app.modules.articles.schemas import (
    ArticleGroupCreate,
    ArticleGroupItemsUpdate,
    ArticleGroupUpdate,
)
from server.app.modules.articles.services.articles import (
    VALID_ARTICLE_STATUSES,
    VALID_REVIEW_STATUSES,
    create_article,
    delete_article,
    get_article,
    set_article_cover,
    update_article,
    validate_article_status,
)
from server.app.modules.articles.services.body_assets import (
    ensure_asset_exists,
    sync_article_body_assets,
)
from server.app.modules.articles.services.feed import (
    list_article_feed,
    list_articles,
    serialize_article_summaries,
)
from server.app.modules.tasks.models import PublishTask
from server.app.shared.errors import ClientError, ConflictError

_logger = logging.getLogger(__name__)

# 单篇文章 CRUD、封面、正文素材与状态常量已迁至 services/articles.py + services/body_assets.py，
# 上面显式重导出以保持 `articles.service` / `articles` 包入口的旧导入路径兼容（见 __all__）。

__all__ = [
    # 文章状态常量与校验（services/articles.py）
    "VALID_ARTICLE_STATUSES",
    "VALID_REVIEW_STATUSES",
    "validate_article_status",
    # 单篇文章 CRUD / 封面（services/articles.py）
    "get_article",
    "create_article",
    "update_article",
    "set_article_cover",
    "delete_article",
    # 正文素材（services/body_assets.py）
    "ensure_asset_exists",
    "sync_article_body_assets",
    # 列表 / 检索 / Feed（本模块）
    "list_articles",
    "serialize_article_summaries",
    "list_article_feed",
    # 文章审核（本模块）
    "approve_article",
    "revoke_article_approval",
    # 文章分组（本模块）
    "get_group",
    "list_groups",
    "create_group",
    "update_group",
    "replace_group_items",
    "delete_group",
    "compute_group_review_summary",
    "approve_group",
    # 每日分组与流式追加（本模块）
    "mark_pending_and_group",
    "mark_pending_and_append_daily",
    "resolve_or_create_daily_group",
    "append_article_to_group_pending",
]


# --- 文章审核 ---


def _get_owned_article(db: Session, article_id: int, user_id: int, role: str) -> Article:
    """按所有权取文章；非 admin 只能取自己的。找不到 / 越权 → ClientError(404 语义)。"""
    article = get_article(db, article_id)
    if article is None or (role != "admin" and article.user_id != user_id):
        raise ClientError("文章不存在")
    return article


def _set_article_review_status(article: Article, review_status: str) -> Article:
    article.review_status = review_status
    article.version += 1
    article.updated_at = utcnow()
    return article


def approve_article(db: Session, article_id: int, user_id: int, role: str) -> Article:
    """通过审核：置 review_status='approved'，version+1。"""
    article = _get_owned_article(db, article_id, user_id, role)
    _set_article_review_status(article, "approved")
    db.flush()
    return get_article(db, article.id) or article


def revoke_article_approval(db: Session, article_id: int, user_id: int, role: str) -> Article:
    """撤销审核：打回 review_status='pending'，version+1。"""
    article = _get_owned_article(db, article_id, user_id, role)
    _set_article_review_status(article, "pending")
    db.flush()
    return get_article(db, article.id) or article


# --- 文章分组增删改查 ---


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


# --- 文章分组审核 ---


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


def mark_pending_and_group(
    session_factory,
    *,
    article_ids: list[int],
    user_id: int,
    base_name: str,
    fallback_suffix: str | None = None,
) -> int | None:
    """把文章标 review_status='pending' 并归入一个新 ArticleGroup（名 base_name）。
    撞 (user_id, name) 唯一约束时改用 base_name + fallback_suffix（调用方应传稳定唯一值，
    如 run_id；未传则回退到不稳定的 #article_ids[0] 旧行为）。尽力执行：失败记日志、不抛。
    用独立 session、本函数内 commit+close。返回 group_id 或 None。"""
    if not article_ids:
        return None
    suffix = fallback_suffix or f"#{article_ids[0]}"
    try:
        from sqlalchemy.exc import IntegrityError

        db = session_factory()
        try:
            for aid in article_ids:
                art = db.get(Article, aid)
                if art is not None:
                    art.review_status = "pending"

            exists = (
                db.query(ArticleGroup.id)
                .filter(
                    ArticleGroup.user_id == user_id,
                    ArticleGroup.name == base_name,
                    ArticleGroup.is_deleted.is_(False),
                )
                .first()
            )
            name = f"{base_name} {suffix}" if exists is not None else base_name
            group = ArticleGroup(user_id=user_id, name=name)
            db.add(group)
            try:
                db.flush()
            except IntegrityError:
                # 并发抢到了 base_name（唯一约束冲突）：rollback 丢掉本次未提交改动，
                # 重新标 pending 并改用带 suffix 的名字重试一次
                db.rollback()
                for aid in article_ids:
                    art = db.get(Article, aid)
                    if art is not None:
                        art.review_status = "pending"
                group = ArticleGroup(user_id=user_id, name=f"{base_name} {suffix}")
                db.add(group)
                db.flush()

            for idx, aid in enumerate(article_ids):
                db.add(ArticleGroupItem(group_id=group.id, article_id=aid, sort_order=idx))
            gid = group.id
            db.commit()
            return gid
        finally:
            db.close()
    except Exception:  # noqa: BLE001 — 尽力而为
        _logger.exception(
            "mark_pending_and_group failed (user=%s, n=%s)", user_id, len(article_ids)
        )
        return None


def mark_pending_and_append_daily(
    session_factory,
    *,
    article_ids: list[int],
    user_id: int,
    group_name: str,
) -> int | None:
    """把文章标 review_status='pending' 并追加进 (user_id, group_name) 分组：
    有同名未软删组则复用，软删同名组则复活，都没有则新建；去重追加，sort_order 接 max+1。
    并发两个 run 同时建组撞 (user_id, name) 唯一约束 → rollback、重标 pending、回查复用。
    尽力而为：失败记日志、不抛；独立 session、本函数内 commit+close。返回 group_id 或 None。"""
    if not article_ids:
        return None
    try:
        from sqlalchemy.exc import IntegrityError

        db = session_factory()
        try:

            def _mark_pending() -> None:
                for aid in article_ids:
                    art = db.get(Article, aid)
                    if art is not None:
                        art.review_status = "pending"

            def _resolve_group() -> ArticleGroup:
                existing = (
                    db.query(ArticleGroup)
                    .filter(ArticleGroup.user_id == user_id, ArticleGroup.name == group_name)
                    .first()
                )
                if existing is not None:
                    if existing.is_deleted:  # 软删同名 → 复活并清空旧成员
                        existing.is_deleted = False
                        existing.deleted_at = None
                        existing.version += 1
                        existing.updated_at = utcnow()
                        existing.items.clear()
                        db.flush()
                    return existing
                grp = ArticleGroup(user_id=user_id, name=group_name)
                db.add(grp)
                db.flush()  # 撞唯一约束在此抛 IntegrityError
                return grp

            _mark_pending()
            try:
                group = _resolve_group()
            except IntegrityError:
                db.rollback()
                _mark_pending()
                group = (
                    db.query(ArticleGroup)
                    .filter(
                        ArticleGroup.user_id == user_id,
                        ArticleGroup.name == group_name,
                        ArticleGroup.is_deleted.is_(False),
                    )
                    .first()
                )
                if group is None:
                    raise

            existing_ids = {
                row[0]
                for row in db.query(ArticleGroupItem.article_id)
                .filter(ArticleGroupItem.group_id == group.id)
                .all()
            }
            max_order = (
                db.query(func.max(ArticleGroupItem.sort_order))
                .filter(ArticleGroupItem.group_id == group.id)
                .scalar()
            )
            next_order = (max_order + 1) if max_order is not None else 0
            for aid in article_ids:
                if aid in existing_ids:
                    continue
                db.add(ArticleGroupItem(group_id=group.id, article_id=aid, sort_order=next_order))
                existing_ids.add(aid)
                next_order += 1

            group.updated_at = utcnow()
            gid = group.id
            db.commit()
            return gid
        finally:
            db.close()
    except Exception:  # noqa: BLE001 — 尽力而为
        _logger.exception(
            "mark_pending_and_append_daily failed (user=%s, name=%s, n=%s)",
            user_id,
            group_name,
            len(article_ids),
        )
        return None


def resolve_or_create_daily_group(
    session_factory,
    *,
    user_id: int,
    group_name: str,
) -> tuple[int, int] | None:
    """查找-或-新建 (user_id, group_name) 分组，返回 (group_id, next_sort_order_start)。

    - 未软删同名组 → 复用；软删同名 → 复活清空成员；都没有 → 新建。
    - next_sort_order_start = 现有 max(sort_order)+1（空组/新建/复活 → 0）。
    - 并发首建撞 (user_id, name) 唯一约束 → rollback 回查复用（catch IntegrityError 与
      OperationalError：InnoDB 并发唯一 INSERT 偶发死锁 1213）；回查仍无 → 抛到外层返回 None。
    - 只解析/建组，不标 pending、不插 item。独立 session、本函数内 commit+close。失败记日志返回 None。
    详见 docs/superpowers/specs/2026-06-15-streaming-daily-group-design.md。"""
    try:
        from sqlalchemy.exc import IntegrityError, OperationalError

        db = session_factory()
        try:

            def _resolve() -> ArticleGroup:
                existing = (
                    db.query(ArticleGroup)
                    .filter(ArticleGroup.user_id == user_id, ArticleGroup.name == group_name)
                    .first()
                )
                if existing is not None:
                    if existing.is_deleted:  # 软删同名 → 复活并清空旧成员
                        existing.is_deleted = False
                        existing.deleted_at = None
                        existing.version += 1
                        existing.updated_at = utcnow()
                        existing.items.clear()
                        db.flush()
                    return existing
                grp = ArticleGroup(user_id=user_id, name=group_name)
                db.add(grp)
                db.flush()  # 撞唯一约束在此抛 IntegrityError（并发偶发 OperationalError/死锁）
                return grp

            try:
                group = _resolve()
            except (IntegrityError, OperationalError):
                db.rollback()
                group = (
                    db.query(ArticleGroup)
                    .filter(
                        ArticleGroup.user_id == user_id,
                        ArticleGroup.name == group_name,
                        ArticleGroup.is_deleted.is_(False),
                    )
                    .first()
                )
                if group is None:
                    raise

            max_order = (
                db.query(func.max(ArticleGroupItem.sort_order))
                .filter(ArticleGroupItem.group_id == group.id)
                .scalar()
            )
            next_start = (max_order + 1) if max_order is not None else 0
            gid = group.id
            db.commit()
            return gid, next_start
        finally:
            db.close()
    except Exception:  # noqa: BLE001 — 尽力而为
        _logger.exception(
            "resolve_or_create_daily_group failed (user=%s, name=%s)", user_id, group_name
        )
        return None


def append_article_to_group_pending(
    session_factory,
    *,
    group_id: int,
    article_id: int,
    sort_order: int,
) -> bool:
    """把单篇标 review_status='pending' 并追加进已存在的 group_id（只插 item、不动组行）。

    - 绝不 UPDATE 组行（不 bump version/updated_at）——避免并发 worker 抢父行排他锁（见 spec 第 7 节）。
    - 撞 (group_id, article_id) 唯一约束（理论上不会，文章是本次新建的）→ 当作已在组、忽略。
    - 独立 session、commit+close。失败记日志返回 False。"""
    try:
        from sqlalchemy.exc import IntegrityError

        db = session_factory()
        try:
            art = db.get(Article, article_id)
            if art is not None:
                art.review_status = "pending"
            db.add(
                ArticleGroupItem(group_id=group_id, article_id=article_id, sort_order=sort_order)
            )
            try:
                db.commit()
            except IntegrityError:
                db.rollback()  # item 已存在 → 仅补标 pending
                # rollback 后上面的 art 已 detached，必须重新 get，不能复用 art
                art2 = db.get(Article, article_id)
                if art2 is not None and art2.review_status != "pending":
                    art2.review_status = "pending"
                    db.commit()
            return True
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        _logger.exception(
            "append_article_to_group_pending failed (group=%s, article=%s)", group_id, article_id
        )
        return False
