from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.app.db.session import get_db
from server.app.models import PublishRecord
from server.app.schemas.article import ArticleCoverUpdate, ArticleCreate, ArticleListRead, ArticleRead, ArticleUpdate
from server.app.services.articles import (
    create_article,
    delete_article,
    get_article,
    list_articles,
    set_article_cover,
    to_article_read,
    update_article,
)

router = APIRouter()


# 获取文章列表，支持按标题/作者搜索、分页
@router.get("", response_model=list[ArticleListRead])
def read_articles(
    q: str | None = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, le=200),
    db: Session = Depends(get_db),
) -> list[ArticleListRead]:
    articles = list_articles(db, q, skip=skip, limit=limit)
    if not articles:
        return []
    article_ids = [a.id for a in articles]
    rows = db.execute(
        select(PublishRecord.article_id, func.count().label("cnt"))
        .where(PublishRecord.article_id.in_(article_ids), PublishRecord.status == "succeeded")
        .group_by(PublishRecord.article_id)
    ).all()
    count_map = {row.article_id: row.cnt for row in rows}
    return [
        ArticleListRead(
            id=a.id,
            title=a.title,
            author=a.author,
            cover_asset_id=a.cover_asset_id,
            word_count=a.word_count,
            status=a.status,
            published_count=count_map.get(a.id, 0),
            created_at=a.created_at,
            updated_at=a.updated_at,
        )
        for a in articles
    ]


# 创建新文章
@router.post("", response_model=ArticleRead)
def create_article_endpoint(payload: ArticleCreate, db: Session = Depends(get_db)) -> ArticleRead:
    return to_article_read(create_article(db, payload))


# 获取单篇文章详情
@router.get("/{article_id}", response_model=ArticleRead)
def read_article(article_id: int, db: Session = Depends(get_db)) -> ArticleRead:
    article = get_article(db, article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    return to_article_read(article)


# 更新文章内容（标题、正文、封面等）
@router.put("/{article_id}", response_model=ArticleRead)
def update_article_endpoint(article_id: int, payload: ArticleUpdate, db: Session = Depends(get_db)) -> ArticleRead:
    article = get_article(db, article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    return to_article_read(update_article(db, article, payload))


# 删除文章
@router.delete("/{article_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_article_endpoint(article_id: int, db: Session = Depends(get_db)) -> Response:
    article = get_article(db, article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    delete_article(db, article)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# 仅更新文章封面图
@router.post("/{article_id}/cover", response_model=ArticleRead)
def update_article_cover(article_id: int, payload: ArticleCoverUpdate, db: Session = Depends(get_db)) -> ArticleRead:
    article = get_article(db, article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    return to_article_read(set_article_cover(db, article, payload.cover_asset_id))

