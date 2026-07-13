"""文章列表 / 全文检索 / 内容 Feed（读模型与合并分页查询）。

从原 service.py 原样迁出，SQLAlchemy where/join/exists/order_by/pagination 与 eager-load、
序列化调用顺序均不变。PublishRecord 跨模块依赖暂不抽象（属另一个计划）。
"""

from __future__ import annotations

import logging

from sqlalchemy import and_, bindparam, func, literal, or_, select, text, union_all
from sqlalchemy.orm import Session, lazyload, load_only

from server.app.modules.articles.models import Article, ArticleGroup, ArticleGroupItem
from server.app.modules.articles.schemas import (
    ArticleFeedItem,
    ArticleFeedResponse,
    ArticleGroupItemRead,
    ArticleGroupReadWithMembers,
    ArticleListRead,
    FeedCounts,
    ReviewSummary,
)
from server.app.modules.articles.services.articles import VALID_REVIEW_STATUSES
from server.app.modules.auto_review.models import AutoReviewDecision
from server.app.modules.tasks.models import PublishRecord
from server.app.shared.errors import ClientError

_logger = logging.getLogger(__name__)


# 列表 / 检索只消费 ArticleListRead 的 summary 字段（见 articles/router.py 与 mcp_catalog/router.py
# 的逐字段构造）。显式 load_only 精简列，避免把 content_json / content_html / plain_text 三个大 Text 列
# 白搬进内存；lazyload(Article.tags) 压掉 mapper 级 lazy="selectin" 的多余 tags 往返；列表不展示
# body_assets 故不再 selectinload（默认 lazy="select"，不访问即不查）。改这里前先确认 ArticleListRead
# 的字段集是否新增了列——漏列会退化成逐行懒加载的 N+1。
def _list_summary_load_options():
    # 必须在函数内（运行时）构造，不能放模块顶层：load_only() 会触发 Article mapper 配置，而本模块在
    # import 链早期被加载时 StockCategory 等关系目标类尚未注册，顶层调用会抛 InvalidRequestError
    # (failed to locate a name 'StockCategory')。
    return (
        load_only(
            Article.title,
            Article.author,
            Article.cover_asset_id,
            Article.word_count,
            Article.status,
            Article.version,
            Article.review_status,
            Article.source_agent_name,
            Article.source_template_name,
            Article.source_template_id,
            Article.created_at,
            Article.updated_at,
        ),
        lazyload(Article.tags),
    )


def _search_articles(db: Session, query: str, user_id: int | None = None) -> list[Article]:
    # MySQL FULLTEXT（ngram parser）自然语言检索 title/author/plain_text。
    # 自然语言模式（不带 IN BOOLEAN MODE）：用户输入里的 + - " * ( ) 等被当词分隔符、不当布尔操作符，
    #   故无需转义、不会因特殊字符触发 syntax error 或返回诡异空集。query 走绑定参数 :q（防注入）；
    #   列名写死在 SQL 文本里（非用户可控）。无 FTS 索引（如某些环境漏建）时本句会抛，由调用方 except 回退 LIKE。
    # 注意：SQLAlchemy 的 func.match(...).against(...) 在 2.x 不可用（Function 无 .against），曾导致检索
    #   永远静默退化成 LIKE、ngram 索引空转（见 issue #50），故这里直接用 text() 显式构造。
    match_clause = text(
        "MATCH (articles.title, articles.author, articles.plain_text) AGAINST (:q) > 0"
    ).bindparams(bindparam("q", query))
    # 结果仅用于取 id / review_status / updated_at 做切片与排序（见 list_articles FTS 分支），
    # 故只 load 这两列 + PK，别 hydrate 全部命中行的正文。
    stmt = (
        select(Article)
        .options(load_only(Article.review_status, Article.updated_at), lazyload(Article.tags))
        .where(
            Article.is_deleted == False,  # noqa: E712
            match_clause,
        )
    )

    if user_id is not None:
        stmt = stmt.where(Article.user_id == user_id)

    return list(db.execute(stmt).scalars().all())


def list_articles(
    db: Session,
    query: str | None = None,
    skip: int = 0,
    limit: int = 50,
    user_id: int | None = None,
    review_status: str | None = None,
) -> list[Article]:
    # query ≥3 字才走 FTS（ngram 最短 token）；FTS 不可用时 except 落到下面的 LIKE 回退
    if query and len(query) >= 3:
        try:
            matching = _search_articles(db, query, user_id=user_id)
            if review_status is not None:
                matching = [a for a in matching if a.review_status == review_status]
            if not matching:
                return []
            matching.sort(key=lambda a: a.updated_at, reverse=True)
            ids = [a.id for a in matching[skip : skip + limit]]
            if not ids:
                return []
            stmt = (
                select(Article)
                .options(*_list_summary_load_options())
                .where(Article.id.in_(ids), Article.is_deleted == False)  # noqa: E712
                .order_by(Article.updated_at.desc())
            )
            articles = list(db.execute(stmt).scalars().all())
            articles.sort(key=lambda a: ids.index(a.id))
            return articles
        except Exception:
            _logger.debug("FTS search unavailable, falling back to LIKE query", exc_info=True)

    stmt = (
        select(Article)
        .where(Article.is_deleted == False)  # noqa: E712
        .options(*_list_summary_load_options())
        .order_by(Article.updated_at.desc())
    )

    if user_id is not None:
        stmt = stmt.where(Article.user_id == user_id)

    if review_status is not None:
        stmt = stmt.where(Article.review_status == review_status)

    if query:
        like = f"%{query}%"
        stmt = stmt.where(
            (Article.title.like(like))
            | (Article.author.like(like))
            | (Article.plain_text.like(like))
        )

    stmt = stmt.offset(skip).limit(limit)
    return list(db.execute(stmt).scalars().all())


def serialize_article_summaries(db: Session, articles: list[Article]) -> dict[int, ArticleListRead]:
    """批量把 Article 序列化成 ArticleListRead,按 id 建 map。

    published_count = 该文成功且未删的 PublishRecord 数;
    auto_review_score = 最新一条 AutoReviewDecision.score_total(仅 MCP 生文有)。

    read_articles 列表端点与 feed 端点(散篇文章 + 分组组员)共用本函数,避免重复拼装逻辑。
    """
    if not articles:
        return {}
    article_ids = [a.id for a in articles]
    count_rows = db.execute(
        select(PublishRecord.article_id, func.count().label("cnt"))
        .where(
            PublishRecord.article_id.in_(article_ids),
            PublishRecord.status == "succeeded",
            PublishRecord.is_deleted == False,  # noqa: E712
        )
        .group_by(PublishRecord.article_id)
    ).all()
    count_map = {row.article_id: row.cnt for row in count_rows}
    # id desc → 每篇保留最新一条决策（只有 MCP loop/goal 经 submit_review_decision 写这张表）
    score_rows = db.execute(
        select(
            AutoReviewDecision.article_id,
            AutoReviewDecision.score_total,
            AutoReviewDecision.pass_line,
        )
        .where(AutoReviewDecision.article_id.in_(article_ids))
        .order_by(AutoReviewDecision.id.desc())
    ).all()
    # auto_review_score 是给前端显示用的字符串：
    #   score_total 为 None / <0（评分失败哨兵）→ None（前端不显示）
    #   pass_line 有值且 score_total < pass_line（没过线）→ "65 _ 80"（前端拆成 65 / 80、标红）
    #   其它（过线 / 无合格线的老数据）→ 纯数字 "84"
    score_map: dict[int, str | None] = {}
    for aid, score, pass_line in score_rows:
        if aid in score_map:
            continue  # 只取最新一条（id desc 已排序，先出现的即最新）
        if score is None or score < 0:
            score_map[aid] = None
        elif pass_line is not None and score < pass_line:
            score_map[aid] = f"{score} _ {pass_line}"
        else:
            score_map[aid] = str(score)
    return {
        a.id: ArticleListRead(
            id=a.id,
            title=a.title,
            author=a.author,
            cover_asset_id=a.cover_asset_id,
            word_count=a.word_count,
            status=a.status,
            version=a.version,
            review_status=a.review_status,
            published_count=count_map.get(a.id, 0),
            source_agent_name=a.source_agent_name,
            source_template_name=a.source_template_name,
            source_template_id=a.source_template_id,
            auto_review_score=score_map.get(a.id),
            created_at=a.created_at,
            updated_at=a.updated_at,
        )
        for a in articles
    }


# ── 内容 feed（服务端合并分页）──────────────────────────────────────────────
#
# 「内容管理」列表把散篇文章 + 分组按 created_at 混排、一起翻页。旧前端靠一次全量拉 1600+ 篇
# 再客户端切页，随文章量线性变慢。list_article_feed 把合并 / 排序 / 切页下沉到 DB，一次只查一页。
# 排序键 = created_at 倒序（对齐旧可见顺序，非 updated_at）+ 稳定次级键 (kind, entity_id)：
#   created_at 是秒级 DATETIME，批量生文常同秒 → 纯 created_at 排序对 tie 不确定，OFFSET 分页下
#   会让同秒文章跨页重复 / 漏项。加 (kind, entity_id) 凑成全序，翻页稳定。时间戳不同时次级键不参与，
#   可见语义不变。


def _feed_article_branch(review_status: str, user_id: int | None, like: str | None):
    """散篇文章分支：未删、命中 tab、且不属于任何未删分组。投影 (kind, entity_id, sort_time)。"""
    grouped_ids = (
        select(ArticleGroupItem.article_id)
        .join(ArticleGroup, ArticleGroup.id == ArticleGroupItem.group_id)
        .where(ArticleGroup.is_deleted == False)  # noqa: E712
    )
    stmt = select(
        literal("article").label("kind"),
        Article.id.label("entity_id"),
        Article.created_at.label("sort_time"),
    ).where(
        Article.is_deleted == False,  # noqa: E712
        Article.review_status == review_status,
        Article.id.notin_(grouped_ids),
    )
    if user_id is not None:
        stmt = stmt.where(Article.user_id == user_id)
    if like is not None:
        stmt = stmt.where(
            (Article.title.like(like))
            | (Article.author.like(like))
            | (Article.plain_text.like(like))
        )
    return stmt


def _feed_group_match(review_status: str):
    """分组是否纳入当前 tab 的 correlated 条件（关联外层 ArticleGroup.id）。

    approved = 有已审未删成员；pending = 无未删成员（空组）或有非 approved 未删成员。
    与前端旧 groupHasStatus 规则一致。
    """
    member = (
        select(1)
        .select_from(ArticleGroupItem)
        .join(
            Article,
            and_(Article.id == ArticleGroupItem.article_id, Article.is_deleted == False),  # noqa: E712
        )
        .where(ArticleGroupItem.group_id == ArticleGroup.id)
    )
    if review_status == "approved":
        return member.where(Article.review_status == "approved").exists()
    has_any = member.exists()
    has_pending = member.where(Article.review_status != "approved").exists()
    return or_(~has_any, has_pending)


def _feed_group_branch(review_status: str, user_id: int | None, like: str | None):
    """分组分支：未删、按 tab 规则纳入。投影 (kind, entity_id, sort_time)。"""
    stmt = select(
        literal("group").label("kind"),
        ArticleGroup.id.label("entity_id"),
        ArticleGroup.created_at.label("sort_time"),
    ).where(
        ArticleGroup.is_deleted == False,  # noqa: E712
        _feed_group_match(review_status),
    )
    if user_id is not None:
        stmt = stmt.where(ArticleGroup.user_id == user_id)
    if like is not None:
        stmt = stmt.where(ArticleGroup.name.like(like))
    return stmt


def _feed_counts(db: Session, user_id: int | None, like: str | None) -> dict[str, int]:
    """两 tab 各自 (散篇 + 分组) 合计，受同一 q 约束。"""
    counts: dict[str, int] = {}
    for status in ("pending", "approved"):
        art_n = db.execute(
            select(func.count()).select_from(_feed_article_branch(status, user_id, like).subquery())
        ).scalar_one()
        grp_n = db.execute(
            select(func.count()).select_from(_feed_group_branch(status, user_id, like).subquery())
        ).scalar_one()
        counts[status] = int(art_n) + int(grp_n)
    return counts


def list_article_feed(
    db: Session,
    *,
    review_status: str,
    query: str | None = None,
    skip: int = 0,
    limit: int = 10,
    user_id: int | None = None,
) -> ArticleFeedResponse:
    """合并分页：散篇文章 + 分组按 created_at 倒序混排，返回一页 + 两 tab 计数。

    user_id=None 表示 admin（不按用户过滤）；非 None 只看该用户自己的文章和分组。
    """
    if review_status not in VALID_REVIEW_STATUSES:
        raise ClientError(f"Invalid review_status: {review_status}")
    like = f"%{query}%" if query else None

    merged = union_all(
        _feed_article_branch(review_status, user_id, like),
        _feed_group_branch(review_status, user_id, like),
    ).subquery()
    page_rows = db.execute(
        select(merged.c.kind, merged.c.entity_id)
        .order_by(
            merged.c.sort_time.desc(),
            merged.c.kind.asc(),
            merged.c.entity_id.desc(),
        )
        .offset(skip)
        .limit(limit)
    ).all()

    article_ids = [r.entity_id for r in page_rows if r.kind == "article"]
    group_ids = [r.entity_id for r in page_rows if r.kind == "group"]

    art_objs = (
        list(
            db.execute(
                select(Article)
                .options(*_list_summary_load_options())
                .where(Article.id.in_(article_ids))
            )
            .scalars()
            .all()
        )
        if article_ids
        else []
    )

    grp_objs = (
        list(db.execute(select(ArticleGroup).where(ArticleGroup.id.in_(group_ids))).scalars().all())
        if group_ids
        else []
    )
    grp_by_id = {g.id: g for g in grp_objs}
    member_order: dict[int, list[int]] = {}
    all_member_ids: set[int] = set()
    for grp_obj in grp_objs:
        ordered = [it.article_id for it in sorted(grp_obj.items, key=lambda i: i.sort_order)]
        member_order[grp_obj.id] = ordered
        all_member_ids.update(ordered)
    member_objs = (
        list(
            db.execute(
                select(Article)
                .options(*_list_summary_load_options())
                .where(Article.id.in_(all_member_ids), Article.is_deleted == False)  # noqa: E712
            )
            .scalars()
            .all()
        )
        if all_member_ids
        else []
    )

    # 一次性序列化本页出现过的所有文章（散篇 + 组员），避免逐组 N 次拼装
    summaries = serialize_article_summaries(db, art_objs + member_objs)

    items: list[ArticleFeedItem] = []
    for r in page_rows:
        if r.kind == "article":
            summary = summaries.get(r.entity_id)
            if summary is not None:
                items.append(ArticleFeedItem(kind="article", article=summary))
        else:
            g = grp_by_id.get(r.entity_id)
            if g is None:
                continue
            members = [summaries[mid] for mid in member_order.get(g.id, []) if mid in summaries]
            approved = sum(1 for m in members if m.review_status == "approved")
            group_read = ArticleGroupReadWithMembers(
                id=g.id,
                name=g.name,
                description=g.description,
                version=g.version,
                items=[
                    ArticleGroupItemRead(article_id=it.article_id, sort_order=it.sort_order)
                    for it in sorted(g.items, key=lambda i: i.sort_order)
                ],
                review_summary=ReviewSummary(total=len(members), approved=approved),
                created_at=g.created_at,
                updated_at=g.updated_at,
                members=members,
            )
            items.append(ArticleFeedItem(kind="group", group=group_read))

    counts = _feed_counts(db, user_id, like)
    return ArticleFeedResponse(
        items=items,
        counts=FeedCounts(pending=counts["pending"], approved=counts["approved"]),
    )
