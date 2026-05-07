from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from server.app.db.session import get_db
from server.app.schemas.article_group import (
    ArticleGroupCreate,
    ArticleGroupItemsUpdate,
    ArticleGroupRead,
    ArticleGroupUpdate,
)
from server.app.services.article_groups import (
    create_group,
    delete_group,
    get_group,
    list_groups,
    replace_group_items,
    to_group_read,
    update_group,
)

router = APIRouter()


# 获取所有文章分组列表
@router.get("", response_model=list[ArticleGroupRead])
def read_groups(db: Session = Depends(get_db)) -> list[ArticleGroupRead]:
    return [to_group_read(group) for group in list_groups(db)]


# 创建新分组
@router.post("", response_model=ArticleGroupRead)
def create_group_endpoint(payload: ArticleGroupCreate, db: Session = Depends(get_db)) -> ArticleGroupRead:
    try:
        group = create_group(db, payload)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="Article group name already exists") from exc
    return to_group_read(group)


# 获取分组详情
@router.get("/{group_id}", response_model=ArticleGroupRead)
def read_group(group_id: int, db: Session = Depends(get_db)) -> ArticleGroupRead:
    group = get_group(db, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Article group not found")
    return to_group_read(group)


# 更新分组信息（名称、描述）
@router.put("/{group_id}", response_model=ArticleGroupRead)
def update_group_endpoint(group_id: int, payload: ArticleGroupUpdate, db: Session = Depends(get_db)) -> ArticleGroupRead:
    group = get_group(db, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Article group not found")
    try:
        updated = update_group(db, group, payload)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="Article group name already exists") from exc
    return to_group_read(updated)


# 删除分组
@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_group_endpoint(group_id: int, db: Session = Depends(get_db)) -> Response:
    group = get_group(db, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Article group not found")
    delete_group(db, group)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# 更新分组中的文章列表（全量替换）
@router.put("/{group_id}/items", response_model=ArticleGroupRead)
def update_group_items(
    group_id: int,
    payload: ArticleGroupItemsUpdate,
    db: Session = Depends(get_db),
) -> ArticleGroupRead:
    group = get_group(db, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Article group not found")
    return to_group_read(replace_group_items(db, group, payload))

