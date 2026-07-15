"""quality_reference 业务层：采纳站内文章 / 导入站外参考 / 查重 / 挑选（对抗评审质量门）。

设计要点（详见 docs/superpowers/specs/2026-07-13-adversarial-review-quality-gate-design.md）：
- content_hash（sha256(归一化 title+plain_text)）做幂等查重；UNIQUE 约束是并发兜底
  （预查未命中也可能撞车 → 捕 IntegrityError 重查返回已有，不让并发请求报 500）。
- adopt_article 全员可写、不校验 owner，只校验 review_status == approved（NAMED 异常，不用裸
  ValueError）。
- import_external 的 content_html 必须过 nh3.clean（防存储型 XSS）；content_json 用
  dumps_content_json 序列化成字符串存（与 Article 同款正文三份并行结构约定）。
- 问题类型关联走子表 quality_reference_category（多对多）：set_reference_categories 做
  replace-all；pick_references 池 A＝有该 category 关联行、池 B＝无任何关联行（通用兜底），
  每池仍优先 external、own 补足。
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
from server.app.modules.quality_reference.models import (
    QualityReference,
    QualityReferenceCategory,
)
from server.app.shared.errors import ClientError, ValidationError

_UNSET = object()


def set_reference_categories(db, ref_id: int, items: list[dict]) -> None:
    """replace-all：整体替换某 reference 的问题类型关联（先删旧、再插新）。

    items = list[{"category": str, "question_texts": list|None}]；跳过空 category、按 category
    去重（UNIQUE(reference_id,category) 兜底）。经关系集合 clear→flush→append→flush 落地：
    中间 flush 保证 DELETE 早于 INSERT，避免类目重叠时撞 UNIQUE。
    """
    ref = get_reference(db, ref_id)
    ref.categories.clear()  # 标记旧关联为 orphan
    db.flush()  # 立即 DELETE 旧行，早于下面的 INSERT
    seen: set[str] = set()
    for it in items or []:
        cat = (it.get("category") or "").strip()
        if not cat or cat in seen:
            continue
        seen.add(cat)
        ref.categories.append(
            QualityReferenceCategory(category=cat, question_texts=it.get("question_texts"))
        )
    db.flush()


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


def _insert_idempotent(db: Session, ref: QualityReference) -> tuple[QualityReference, bool]:
    """content_hash UNIQUE 做并发兜底：预查未命中仍可能撞车 → 捕 IntegrityError、回滚重查返回已有那条。

    返回 (ref, created)：created=True 仅当本次真正新插入；dup 复活时 created=False（调用方据此
    决定是否建/改类型关联，避免复活已有参考时误动其关联）。
    """
    existing = _by_hash(db, ref.content_hash)
    if existing is not None:
        return _reactivate(db, existing), False
    try:
        db.add(ref)
        db.flush()
        return ref, True
    except IntegrityError:
        db.rollback()  # 另一并发已插入同 content_hash：回滚本次失败插入后重查
        again = _by_hash(db, ref.content_hash)
        if again is not None:
            return _reactivate(db, again), False
        raise


def adopt_article(
    db, *, user_id: int, article_id: int, fallback_category: str | None = None
) -> QualityReference:
    a = db.query(Article).filter(Article.id == article_id, Article.is_deleted == False).first()  # noqa: E712
    if a is None:
        raise ClientError(f"article not found: {article_id}")
    if a.review_status != "approved":
        raise ValidationError("只能采纳已审核（approved）文章")
    dup = db.query(QualityReference).filter(QualityReference.article_id == article_id).first()
    if dup is not None:
        return _reactivate(db, dup)  # 同篇不重复采纳（article_id UNIQUE）；复活不动关联
    plain = a.plain_text or ""
    ref = QualityReference(
        origin="own",
        article_id=article_id,
        title=a.title,
        content_json=a.content_json or "{}",
        content_html=a.content_html or "",
        plain_text=plain,
        content_hash=compute_content_hash(a.title, plain),
        added_by_user_id=user_id,
    )
    ref, created = _insert_idempotent(db, ref)
    if created:
        # 关联类型：文章有 source_question_category 就用它（带 source_question_texts）；否则回落
        # 前端补选的 fallback_category（无问题词）；都无则不关联（=通用兜底）。
        if a.source_question_category:
            set_reference_categories(
                db,
                ref.id,
                [
                    {
                        "category": a.source_question_category,
                        "question_texts": a.source_question_texts,
                    }
                ],
            )
        elif fallback_category:
            set_reference_categories(
                db, ref.id, [{"category": fallback_category, "question_texts": None}]
            )
    return ref


def import_external(
    db, *, user_id, title, markdown, category, source_url, platform, question_texts=None
):
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
        source_url=source_url,
        platform=platform,
        added_by_user_id=user_id,
    )
    ref, created = _insert_idempotent(db, ref)
    if created and category:  # 新插入且给了单值 category → 关联一条；多类型走后续 patch replace-all
        set_reference_categories(
            db, ref.id, [{"category": category, "question_texts": question_texts}]
        )
    return ref, similar


def list_references(db, *, origin=None, category=None, is_active=None, q=None, skip=0, limit=50):
    """列表 + 标题关键词搜索（q）+ 偏移分页（skip/limit）。

    q：标题子串匹配（LIKE，转义 % _ 避免用户输入被当通配符）。分页无 total——前端用
    「返回条数 < limit 即末页」判尾页（curated 库规模有限，offset 分页足够，不引入计数开销）。
    """
    query = db.query(QualityReference)
    if origin is not None:
        query = query.filter(QualityReference.origin == origin)
    if category is not None:
        # join 子表按 category 过滤（UNIQUE(reference_id,category) 保证至多一行、不产生重复）
        query = query.join(QualityReferenceCategory).filter(
            QualityReferenceCategory.category == category
        )
    if is_active is not None:
        query = query.filter(QualityReference.is_active == is_active)
    if q and q.strip():
        kw = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.filter(QualityReference.title.like(f"%{kw}%"))
    return (
        query.order_by(QualityReference.created_at.desc())
        .offset(max(0, skip))
        .limit(max(1, min(limit, 200)))
        .all()
    )


def get_reference(db, ref_id):
    ref = db.query(QualityReference).filter(QualityReference.id == ref_id).first()
    if ref is None:
        raise ClientError(f"quality_reference not found: {ref_id}")
    return ref


def patch_reference(db, ref_id, *, is_active=None, categories=_UNSET):
    ref = get_reference(db, ref_id)
    if is_active is not None:
        ref.is_active = is_active
    if categories is not _UNSET:
        set_reference_categories(db, ref_id, categories or [])  # replace-all；空数组=清空=通用
    db.flush()
    return ref


def pick_references(db, *, category, k, truncate_chars) -> list[dict]:
    """池 A＝有该 category 关联行的 active ref；不足回落池 B＝无任何关联行的 active ref（通用）；
    每池优先 external、own 补足。返回保持 `category` 键（池 A 填命中类目、池 B 填 None）供 verifier 兼容。"""

    def prefer_ext(rows, n):
        ext = [r for r in rows if r.origin == "external"]
        own = [r for r in rows if r.origin != "external"]
        random.shuffle(ext)
        random.shuffle(own)
        return (ext + own)[:n]

    picked: list[tuple[QualityReference, str | None]] = []
    if category:
        rows_a = (
            db.query(QualityReference)
            .join(QualityReferenceCategory)
            .filter(
                QualityReference.is_active == True,  # noqa: E712
                QualityReferenceCategory.category == category,
            )
            .all()
        )
        picked = [(r, category) for r in prefer_ext(rows_a, k)]
    if len(picked) < k:
        taken = {r.id for r, _ in picked}
        rows_b = (
            db.query(QualityReference)
            .outerjoin(QualityReferenceCategory)
            .filter(
                QualityReference.is_active == True,  # noqa: E712
                QualityReferenceCategory.id.is_(None),  # 无任何关联行 = 通用兜底池
            )
            .all()
        )
        rows_b = [r for r in rows_b if r.id not in taken]
        picked += [(r, None) for r in prefer_ext(rows_b, k - len(picked))]
    return [
        {
            "id": r.id,
            "title": r.title,
            "category": cat,
            "origin": r.origin,
            "plain_text": (r.plain_text or "")[:truncate_chars],
        }
        for r, cat in picked[:k]
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

    # join 子表按 child.category 聚合；一篇挂多类型的参考在各类目分别计数，external/own 看 origin。
    rows = (
        db.query(
            QualityReferenceCategory.category,
            func.sum(case((QualityReference.origin == "external", 1), else_=0)),
            func.sum(case((QualityReference.origin == "own", 1), else_=0)),
            func.count(),
        )
        .join(QualityReference, QualityReference.id == QualityReferenceCategory.reference_id)
        .filter(QualityReference.is_active == True)  # noqa: E712
        .group_by(QualityReferenceCategory.category)
        .all()
    )
    return [
        {"category": c, "external": int(e), "own": int(o), "total": int(t)} for c, e, o, t in rows
    ]
