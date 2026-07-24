"""文章审核：通过 / 撤销，含所有权校验与 review_status 状态迁移。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from server.app.core.time import utcnow
from server.app.modules.articles.models import Article
from server.app.modules.articles.services.articles import get_article
from server.app.shared.errors import ClientError


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
