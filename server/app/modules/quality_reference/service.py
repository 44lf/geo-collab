"""quality_reference 业务层：采纳站内文章 / 导入站外参考 / 查重 / 挑选（对抗评审质量门）。

设计要点（详见 docs/superpowers/specs/2026-07-13-adversarial-review-quality-gate-design.md）：
- content_hash（sha256(归一化 title+plain_text)）做幂等查重；UNIQUE 约束是并发兜底
  （预查未命中也可能撞车 → 捕 IntegrityError 重查返回已有，不让并发请求报 500）。
- adopt_article 全员可写、不校验 owner，只校验 review_status == approved（NAMED 异常，不用裸
  ValueError）。
- import_external 的 content_html 必须过 nh3.clean（防存储型 XSS）；content_json 用
  dumps_content_json 序列化成字符串存（与 Article 同款正文三份并行结构约定）。
- pick_references 同类目优先 external、own 补足；类目不足回落 category IS NULL 通用池。
"""

from __future__ import annotations

import hashlib
import random
import re
import unicodedata

from sqlalchemy import bindparam, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from server.app.modules.articles.models import Article
from server.app.modules.quality_reference.models import QualityReference
from server.app.shared.errors import ClientError, ValidationError

_UNSET = object()


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", s or "")).strip()


def compute_content_hash(title: str, plain_text: str) -> str:
    return hashlib.sha256((_normalize(title) + "\n" + _normalize(plain_text)).encode()).hexdigest()


def _by_hash(db, h):
    return db.query(QualityReference).filter(QualityReference.content_hash == h).first()


def _reactivate(db, ref):
    if not ref.is_active:
        ref.is_active = True
        db.flush()
    return ref


def _insert_idempotent(db: Session, ref: QualityReference) -> QualityReference:
    """content_hash UNIQUE 做并发兜底：预查未命中仍可能撞车 → 捕 IntegrityError、回滚重查返回已有那条。"""
    existing = _by_hash(db, ref.content_hash)
    if existing is not None:
        return _reactivate(db, existing)
    try:
        db.add(ref)
        db.flush()
        return ref
    except IntegrityError:
        db.rollback()  # 另一并发已插入同 content_hash：回滚本次失败插入后重查
        again = _by_hash(db, ref.content_hash)
        if again is not None:
            return _reactivate(db, again)
        raise


def adopt_article(db, *, user_id: int, article_id: int) -> QualityReference:
    a = db.query(Article).filter(Article.id == article_id, Article.is_deleted == False).first()  # noqa: E712
    if a is None:
        raise ClientError(f"article not found: {article_id}")
    if a.review_status != "approved":
        raise ValidationError("只能采纳已审核（approved）文章")
    dup = db.query(QualityReference).filter(QualityReference.article_id == article_id).first()
    if dup is not None:
        return _reactivate(db, dup)  # 同篇不重复采纳（article_id UNIQUE）
    plain = a.plain_text or ""
    ref = QualityReference(
        origin="own",
        article_id=article_id,
        title=a.title,
        content_json=a.content_json or "{}",
        content_html=a.content_html or "",
        plain_text=plain,
        content_hash=compute_content_hash(a.title, plain),
        category=a.source_question_category,
        added_by_user_id=user_id,
    )
    return _insert_idempotent(db, ref)


def import_external(db, *, user_id, title, markdown, category, source_url, platform):
    import nh3

    from server.app.modules.ai_generation.converter import markdown_to_html, markdown_to_tiptap
    from server.app.modules.ai_generation.markdown_sanitizer import normalize_markdown_content
    from server.app.modules.articles.parser import dumps_content_json

    md = normalize_markdown_content(markdown)
    similar = find_similar(db, plain_text=md, limit=5)  # near-dup 软提示（不硬挡）
    ref = QualityReference(
        origin="external",
        article_id=None,
        title=title,
        content_json=dumps_content_json(markdown_to_tiptap(md)),  # dict→str
        content_html=nh3.clean(markdown_to_html(md)),  # 防存储型 XSS
        plain_text=md,
        content_hash=compute_content_hash(title, md),
        category=category,
        source_url=source_url,
        platform=platform,
        added_by_user_id=user_id,
    )
    return _insert_idempotent(db, ref), similar


def list_references(db, *, origin=None, category=None, is_active=None, skip=0, limit=50):
    q = db.query(QualityReference)
    if origin is not None:
        q = q.filter(QualityReference.origin == origin)
    if category is not None:
        q = q.filter(QualityReference.category == category)
    if is_active is not None:
        q = q.filter(QualityReference.is_active == is_active)
    return q.order_by(QualityReference.created_at.desc()).offset(skip).limit(min(limit, 200)).all()


def get_reference(db, ref_id):
    ref = db.query(QualityReference).filter(QualityReference.id == ref_id).first()
    if ref is None:
        raise ClientError(f"quality_reference not found: {ref_id}")
    return ref


def patch_reference(db, ref_id, *, is_active=None, category=_UNSET):
    ref = get_reference(db, ref_id)
    if is_active is not None:
        ref.is_active = is_active
    if category is not _UNSET:
        ref.category = category
    db.flush()
    return ref


def pick_references(db, *, category, k, truncate_chars) -> list[dict]:
    """精确类目→不足回落 category IS NULL 通用池；每池优先 external、own 补足；返回带 origin。"""

    def prefer_ext(rows, n):
        ext = [r for r in rows if r.origin == "external"]
        own = [r for r in rows if r.origin != "external"]
        random.shuffle(ext)
        random.shuffle(own)
        return (ext + own)[:n]

    picked = []
    if category:
        picked = prefer_ext(
            db.query(QualityReference)
            .filter(QualityReference.is_active == True, QualityReference.category == category)  # noqa: E712
            .all(),
            k,
        )
    if len(picked) < k:
        picked += prefer_ext(
            db.query(QualityReference)
            .filter(QualityReference.is_active == True, QualityReference.category.is_(None))  # noqa: E712
            .all(),
            k - len(picked),
        )
    return [
        {
            "id": r.id,
            "title": r.title,
            "category": r.category,
            "origin": r.origin,
            "plain_text": (r.plain_text or "")[:truncate_chars],
        }
        for r in picked[:k]
    ]


def find_similar(db, *, plain_text, limit) -> list[dict]:
    import logging

    probe = _normalize(plain_text)[:200]
    if len(probe) < 3:
        return []
    try:
        stmt = text(
            "SELECT id, title FROM quality_reference "
            "WHERE MATCH(plain_text) AGAINST (:q) > 0 AND is_active = 1 LIMIT :lim"
        ).bindparams(bindparam("q", probe), bindparam("lim", limit))
        return [{"id": r.id, "title": r.title} for r in db.execute(stmt).all()]
    except Exception:
        logging.getLogger(__name__).warning(
            "qref find_similar FTS unavailable", exc_info=True
        )  # 不静默吞
        return []


def is_source_article_deleted(db, ref) -> bool:
    if ref.article_id is None:
        return ref.origin == "own"  # own 但 FK 已 NULL = 源被物理删；external 本无源文章
    return bool(
        db.query(Article.is_deleted).filter(Article.id == ref.article_id).scalar()
    )  # 软删源 article_id 仍指向它


def category_origin_stats(db) -> list[dict]:
    from sqlalchemy import case, func

    rows = (
        db.query(
            QualityReference.category,
            func.sum(case((QualityReference.origin == "external", 1), else_=0)),
            func.sum(case((QualityReference.origin == "own", 1), else_=0)),
            func.count(),
        )
        .filter(QualityReference.is_active == True)  # noqa: E712
        .group_by(QualityReference.category)
        .all()
    )
    return [
        {"category": c, "external": int(e), "own": int(o), "total": int(t)} for c, e, o, t in rows
    ]
