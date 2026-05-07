from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from server.app.db.session import get_db
from server.app.schemas.article import ArticleCoverUpdate, ArticleCreate, ArticleRead, ArticleUpdate
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


@router.get("", response_model=list[ArticleRead])
def read_articles(q: str | None = Query(default=None), db: Session = Depends(get_db)) -> list[ArticleRead]:
    return [to_article_read(article) for article in list_articles(db, q)]


@router.post("", response_model=ArticleRead)
def create_article_endpoint(payload: ArticleCreate, db: Session = Depends(get_db)) -> ArticleRead:
    return to_article_read(create_article(db, payload))


@router.get("/{article_id}", response_model=ArticleRead)
def read_article(article_id: int, db: Session = Depends(get_db)) -> ArticleRead:
    article = get_article(db, article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    return to_article_read(article)


@router.put("/{article_id}", response_model=ArticleRead)
def update_article_endpoint(article_id: int, payload: ArticleUpdate, db: Session = Depends(get_db)) -> ArticleRead:
    article = get_article(db, article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    return to_article_read(update_article(db, article, payload))


@router.delete("/{article_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_article_endpoint(article_id: int, db: Session = Depends(get_db)) -> Response:
    article = get_article(db, article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    delete_article(db, article)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{article_id}/cover", response_model=ArticleRead)
def update_article_cover(article_id: int, payload: ArticleCoverUpdate, db: Session = Depends(get_db)) -> ArticleRead:
    article = get_article(db, article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    return to_article_read(set_article_cover(db, article, payload.cover_asset_id))

