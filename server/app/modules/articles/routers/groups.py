"""文章分组路由。"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from server.app.core.security import get_current_user
from server.app.db.session import get_db
from server.app.modules.articles import (
    approve_group,
    compute_group_review_summary,
    create_group,
    delete_group,
    get_group,
    list_groups,
    replace_group_items,
    update_group,
)
from server.app.modules.articles.models import ArticleGroup
from server.app.modules.articles.schemas import (
    ArticleGroupCreate,
    ArticleGroupItemsUpdate,
    ArticleGroupRead,
    ArticleGroupUpdate,
    ReviewSummary,
    to_group_read,
)
from server.app.modules.audit.service import add_audit_entry
from server.app.modules.system.models import User
from server.app.shared.errors import ClientError

article_groups_router = APIRouter()


# ── 文章分组辅助函数 ────────────────────────────────────────────────────────


def _verify_group_ownership(group: ArticleGroup | None, current_user: User) -> ArticleGroup:
    if group is None:
        raise HTTPException(status_code=404, detail="文章分组不存在")
    if current_user.role != "admin" and group.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="文章分组不存在")
    return group


def _group_read_with_summary(db: Session, group: ArticleGroup) -> ArticleGroupRead:
    total, approved = compute_group_review_summary(db, group.id)
    return to_group_read(group, ReviewSummary(total=total, approved=approved))


# ── 文章分组路由 ────────────────────────────────────────────────────────────


@article_groups_router.get("", response_model=list[ArticleGroupRead])
def read_groups(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ArticleGroupRead]:
    groups = list_groups(db)
    if current_user.role != "admin":
        groups = [g for g in groups if g.user_id == current_user.id]
    return [_group_read_with_summary(db, group) for group in groups]


@article_groups_router.post("", response_model=ArticleGroupRead)
def create_group_endpoint(
    payload: ArticleGroupCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ArticleGroupRead:
    try:
        group = create_group(db, current_user.id, payload)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="分组名称已存在") from exc
    add_audit_entry(
        db,
        user=current_user,
        action="article_group.create",
        target_type="article_group",
        target_id=group.id,
        payload={"name": group.name},
        request=request,
    )
    return _group_read_with_summary(db, group)


@article_groups_router.get("/{group_id}", response_model=ArticleGroupRead)
def read_group(
    group_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ArticleGroupRead:
    group = _verify_group_ownership(get_group(db, group_id), current_user)
    return _group_read_with_summary(db, group)


@article_groups_router.put("/{group_id}", response_model=ArticleGroupRead)
def update_group_endpoint(
    group_id: int,
    payload: ArticleGroupUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ArticleGroupRead:
    group = _verify_group_ownership(get_group(db, group_id), current_user)
    changed_fields = sorted(payload.model_dump(exclude_unset=True).keys())
    try:
        updated = update_group(db, group, payload)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="分组名称已存在") from exc
    add_audit_entry(
        db,
        user=current_user,
        action="article_group.update",
        target_type="article_group",
        target_id=group_id,
        payload={"changed_fields": changed_fields},
        request=request,
    )
    return _group_read_with_summary(db, updated)


@article_groups_router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_group_endpoint(
    group_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    group = _verify_group_ownership(get_group(db, group_id), current_user)
    group_name = group.name
    try:
        delete_group(db, group)
    except ClientError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    add_audit_entry(
        db,
        user=current_user,
        action="article_group.delete",
        target_type="article_group",
        target_id=group_id,
        payload={"name": group_name},
        request=request,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@article_groups_router.put("/{group_id}/items", response_model=ArticleGroupRead)
def update_group_items(
    group_id: int,
    payload: ArticleGroupItemsUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ArticleGroupRead:
    group = _verify_group_ownership(get_group(db, group_id), current_user)
    updated = replace_group_items(db, group, payload)
    add_audit_entry(
        db,
        user=current_user,
        action="article_group.items.replace",
        target_type="article_group",
        target_id=group_id,
        payload={"item_count": len(payload.items)},
        request=request,
    )
    return _group_read_with_summary(db, updated)


@article_groups_router.post("/{group_id}/approve-all", response_model=ArticleGroupRead)
def approve_group_endpoint(
    group_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ArticleGroupRead:
    group = _verify_group_ownership(get_group(db, group_id), current_user)
    updated = approve_group(db, group.id, current_user.id, current_user.role)
    add_audit_entry(
        db,
        user=current_user,
        action="article_group.review.approve_all",
        target_type="article_group",
        target_id=group_id,
        payload=None,
        request=request,
    )
    return _group_read_with_summary(db, updated)
