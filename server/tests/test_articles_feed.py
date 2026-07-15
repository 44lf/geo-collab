"""内容列表服务端合并分页 feed —— service 层 + /api/articles/feed 端点测试。"""

import pytest

from server.app.modules.articles.schemas import (
    ArticleCreate,
    ArticleGroupCreate,
    ArticleGroupItemInput,
    ArticleGroupItemsUpdate,
)
from server.app.modules.articles.service import (
    create_article,
    create_group,
    list_article_feed,
    replace_group_items,
)
from server.tests.utils import build_test_app

pytestmark = pytest.mark.mysql


@pytest.fixture
def app_ctx(monkeypatch):
    ctx = build_test_app(monkeypatch)
    try:
        yield ctx
    finally:
        ctx.cleanup()


@pytest.fixture
def db(app_ctx):
    session = app_ctx.session_factory()
    try:
        yield session
    finally:
        session.close()


def _mk_article(db, user_id, title, review_status="pending"):
    art = create_article(db, user_id, ArticleCreate(title=title, plain_text=title))
    art.review_status = review_status
    db.flush()
    return art


def test_feed_merges_articles_and_groups_by_created_at_desc(app_ctx, db):
    uid = app_ctx.admin_id
    a1 = _mk_article(db, uid, "文章一", "pending")
    grp = create_group(db, uid, ArticleGroupCreate(name="分组甲"))
    a2 = _mk_article(db, uid, "组员", "pending")
    replace_group_items(
        db, grp, ArticleGroupItemsUpdate(items=[ArticleGroupItemInput(article_id=a2.id)])
    )
    a3 = _mk_article(db, uid, "文章三", "pending")
    db.commit()

    resp = list_article_feed(db, review_status="pending", skip=0, limit=10, user_id=None)

    kinds = [(it.kind, it.article.id if it.kind == "article" else it.group.id) for it in resp.items]
    # a3(最新散文) / 分组甲 / a1 都在;组员 a2 不作为散文出现(它在分组里,被 NOT EXISTS 排除)
    assert ("article", a3.id) in kinds
    assert ("group", grp.id) in kinds
    assert ("article", a1.id) in kinds
    assert ("article", a2.id) not in kinds
    # 混排按 created_at 倒序 + 稳定次级键(entity_id 倒序):后建的 a3 在 a1 前
    ids_in_order = [k[1] for k in kinds if k[0] == "article"]
    assert ids_in_order.index(a3.id) < ids_in_order.index(a1.id)
    # 分组内嵌组员(按 sort_order),供只查一页也能展开/分发
    grp_item = next(it for it in resp.items if it.kind == "group")
    assert [m.id for m in grp_item.group.members] == [a2.id]
    # 计数:3 个 pending 内容项(a1 散文 + 分组甲 + a3 散文),approved=0
    assert resp.counts.pending == 3
    assert resp.counts.approved == 0


def test_feed_pagination_and_limit(app_ctx, db):
    uid = app_ctx.admin_id
    for i in range(5):
        _mk_article(db, uid, f"文章{i}", "pending")
    db.commit()

    p1 = list_article_feed(db, review_status="pending", skip=0, limit=2, user_id=None)
    p2 = list_article_feed(db, review_status="pending", skip=2, limit=2, user_id=None)
    assert len(p1.items) == 2 and len(p2.items) == 2
    ids1 = {it.article.id for it in p1.items}
    ids2 = {it.article.id for it in p2.items}
    assert ids1.isdisjoint(ids2)  # 页间不重叠(稳定排序保证)
    assert p1.counts.pending == 5


def test_feed_tab_filter_and_empty_group_is_pending(app_ctx, db):
    uid = app_ctx.admin_id
    approved_art = _mk_article(db, uid, "已审", "approved")
    empty_grp = create_group(db, uid, ArticleGroupCreate(name="空组"))
    db.commit()

    pending = list_article_feed(db, review_status="pending", user_id=None)
    approved = list_article_feed(db, review_status="approved", user_id=None)

    # 空组(total=0)归 pending
    assert empty_grp.id in [it.group.id for it in pending.items if it.kind == "group"]
    # 已审文章只在 approved tab
    assert approved_art.id in [it.article.id for it in approved.items if it.kind == "article"]
    assert approved_art.id not in [it.article.id for it in pending.items if it.kind == "article"]


def test_feed_group_tab_by_member_status(app_ctx, db):
    """有已审成员 → approved tab;有未审成员 → pending tab(混合组两 tab 都出现)。"""
    uid = app_ctx.admin_id
    grp = create_group(db, uid, ArticleGroupCreate(name="混合组"))
    approved_member = _mk_article(db, uid, "已审成员", "approved")
    pending_member = _mk_article(db, uid, "待审成员", "pending")
    replace_group_items(
        db,
        grp,
        ArticleGroupItemsUpdate(
            items=[
                ArticleGroupItemInput(article_id=approved_member.id),
                ArticleGroupItemInput(article_id=pending_member.id),
            ]
        ),
    )
    db.commit()

    pending = list_article_feed(db, review_status="pending", user_id=None)
    approved = list_article_feed(db, review_status="approved", user_id=None)
    assert grp.id in [it.group.id for it in pending.items if it.kind == "group"]
    assert grp.id in [it.group.id for it in approved.items if it.kind == "group"]


def test_feed_search_like_matches_title(app_ctx, db):
    uid = app_ctx.admin_id
    hit = _mk_article(db, uid, "关键词命中", "pending")
    _mk_article(db, uid, "无关内容", "pending")
    db.commit()

    resp = list_article_feed(db, review_status="pending", query="关键词", user_id=None)
    got = [it.article.id for it in resp.items if it.kind == "article"]
    assert hit.id in got
    assert len(got) == 1
    assert resp.counts.pending == 1


def test_feed_per_user_scope(app_ctx, db):
    """user_id 指定时只看自己的文章;admin(None)看全部。"""
    from server.app.modules.system.models import User

    owner_id = app_ctx.admin_id
    other = User(username="other-op", role="operator", is_active=True, must_change_password=False)
    other.set_password("pw-123456")
    db.add(other)
    db.flush()
    mine = _mk_article(db, owner_id, "我的文章", "pending")
    theirs = _mk_article(db, other.id, "别人的文章", "pending")
    db.commit()

    scoped = list_article_feed(db, review_status="pending", user_id=owner_id)
    scoped_ids = [it.article.id for it in scoped.items if it.kind == "article"]
    assert mine.id in scoped_ids
    assert theirs.id not in scoped_ids
    assert scoped.counts.pending == 1

    all_view = list_article_feed(db, review_status="pending", user_id=None)
    all_ids = [it.article.id for it in all_view.items if it.kind == "article"]
    assert {mine.id, theirs.id} <= set(all_ids)


# ── /api/articles/feed 端点(Task 4)──────────────────────────────────────────


def test_feed_endpoint_returns_items_and_counts(app_ctx, db):
    _mk_article(db, app_ctx.admin_id, "端点文章", "pending")
    db.commit()

    resp = app_ctx.client.get(
        "/api/articles/feed", params={"review_status": "pending", "limit": 10}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body and "counts" in body
    assert set(body["counts"].keys()) == {"pending", "approved"}
    assert body["counts"]["pending"] >= 1


def test_feed_endpoint_rejects_bad_review_status(app_ctx):
    resp = app_ctx.client.get("/api/articles/feed", params={"review_status": "bogus"})
    assert resp.status_code == 400


# ── 对抗判分带出（Task 6）───────────────────────────────────────────────────


def test_feed_item_surfaces_adversarial_score(app_ctx, db):
    art = _mk_article(db, app_ctx.admin_id, "对抗判分文章", "pending")
    art.adversarial_score = 88
    db.commit()

    resp = list_article_feed(db, review_status="pending", skip=0, limit=10, user_id=None)

    item = next(it for it in resp.items if it.kind == "article" and it.article.id == art.id)
    assert item.article.adversarial_score == 88
