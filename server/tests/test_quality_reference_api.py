"""quality_reference CRUD 路由测试（对抗评审质量门 Task 3）。

覆盖：import 返回 similar + detail 带正文（brief 用例）、adopt 门禁（approved 才过、
非 approved 400）、list 的 origin/is_active 过滤、patch 下架、categories/stats 聚合、
detail 的 source_article_deleted 显式标记。
"""

import pytest

from server.app.modules.ai_generation.models import QuestionItem, QuestionPool
from server.app.modules.articles.models import Article
from server.tests.utils import build_test_app


def _make_article(db, **kw):
    a = Article(
        user_id=1,
        title=kw.get("title", "T"),
        content_json="{}",
        content_html="",
        plain_text="正文",
        word_count=2,
        status="draft",
        review_status=kw.get("review_status", "approved"),
    )
    db.add(a)
    db.flush()
    return a


@pytest.mark.mysql
def test_import_returns_similar_and_detail_has_body(monkeypatch):
    app_ctx = build_test_app(monkeypatch)
    try:
        c = app_ctx.client
        r = c.post(
            "/api/quality-reference/import",
            json={"title": "外部好文", "markdown": "正文正文正文", "category": "通用"},
        )
        assert r.status_code == 200 and "similar" in r.json()
        rid = r.json()["reference"]["id"]
        d = c.get(f"/api/quality-reference/{rid}")
        assert d.status_code == 200 and "content_json" in d.json() and d.json()["plain_text"]
    finally:
        app_ctx.cleanup()


@pytest.mark.mysql
def test_adopt_approved_article_succeeds(monkeypatch):
    app_ctx = build_test_app(monkeypatch)
    db = app_ctx.session_factory()
    try:
        a = _make_article(db, review_status="approved")
        db.commit()
        c = app_ctx.client
        r = c.post("/api/quality-reference/adopt", json={"article_id": a.id})
        assert r.status_code == 200
        assert r.json()["origin"] == "own"
        assert r.json()["article_id"] == a.id
    finally:
        db.close()
        app_ctx.cleanup()


@pytest.mark.mysql
def test_adopt_pending_article_rejected(monkeypatch):
    app_ctx = build_test_app(monkeypatch)
    db = app_ctx.session_factory()
    try:
        a = _make_article(db, review_status="pending")
        db.commit()
        c = app_ctx.client
        r = c.post("/api/quality-reference/adopt", json={"article_id": a.id})
        assert r.status_code == 400
    finally:
        db.close()
        app_ctx.cleanup()


@pytest.mark.mysql
def test_list_filters_by_origin_and_is_active(monkeypatch):
    app_ctx = build_test_app(monkeypatch)
    db = app_ctx.session_factory()
    try:
        a = _make_article(db)
        db.commit()
        c = app_ctx.client
        c.post("/api/quality-reference/adopt", json={"article_id": a.id})
        c.post(
            "/api/quality-reference/import",
            json={"title": "外部A", "markdown": "外部外部外部", "category": "通用"},
        )

        r_own = c.get("/api/quality-reference", params={"origin": "own"})
        assert r_own.status_code == 200
        assert all(row["origin"] == "own" for row in r_own.json())
        assert any(row["article_id"] == a.id for row in r_own.json())

        r_ext = c.get("/api/quality-reference", params={"origin": "external"})
        assert r_ext.status_code == 200
        assert all(row["origin"] == "external" for row in r_ext.json())
        assert len(r_ext.json()) >= 1
    finally:
        db.close()
        app_ctx.cleanup()


@pytest.mark.mysql
def test_patch_deactivates_reference(monkeypatch):
    app_ctx = build_test_app(monkeypatch)
    try:
        c = app_ctx.client
        r = c.post(
            "/api/quality-reference/import",
            json={"title": "待下架", "markdown": "待下架待下架待下架", "category": "通用"},
        )
        rid = r.json()["reference"]["id"]

        p = c.patch(f"/api/quality-reference/{rid}", json={"is_active": False})
        assert p.status_code == 200 and p.json()["is_active"] is False

        active_list = c.get("/api/quality-reference", params={"is_active": True})
        assert all(row["id"] != rid for row in active_list.json())

        inactive_list = c.get("/api/quality-reference", params={"is_active": False})
        assert any(row["id"] == rid for row in inactive_list.json())

        # categories 未传（None）不应清空已有关联（replace-all 只在显式传 categories 时触发）
        p2 = c.patch(f"/api/quality-reference/{rid}", json={"is_active": True})
        assert p2.status_code == 200
        assert [c["category"] for c in p2.json()["categories"]] == ["通用"]

        # 显式传空数组 = 清空 = 通用
        p3 = c.patch(f"/api/quality-reference/{rid}", json={"categories": []})
        assert p3.status_code == 200 and p3.json()["categories"] == []
    finally:
        app_ctx.cleanup()


@pytest.mark.mysql
def test_categories_and_stats(monkeypatch):
    app_ctx = build_test_app(monkeypatch)
    db = app_ctx.session_factory()
    try:
        pool = QuestionPool(user_id=1, name="池子")
        db.add(pool)
        db.flush()
        db.add(
            QuestionItem(
                pool_id=pool.id,
                record_id="rec-1",
                category="题库类目",
                question_text="问题",
            )
        )
        db.commit()

        c = app_ctx.client
        c.post(
            "/api/quality-reference/import",
            json={"title": "餐厅参考1", "markdown": "餐厅甲餐厅甲", "category": "餐厅"},
        )
        c.post(
            "/api/quality-reference/import",
            json={"title": "餐厅参考2", "markdown": "餐厅乙餐厅乙", "category": "餐厅"},
        )

        a = _make_article(db)
        db.commit()
        adopt_r = c.post("/api/quality-reference/adopt", json={"article_id": a.id})
        own_id = adopt_r.json()["id"]
        c.patch(f"/api/quality-reference/{own_id}", json={"categories": [{"category": "餐厅"}]})

        cats = c.get("/api/quality-reference/categories")
        assert cats.status_code == 200
        body = cats.json()
        assert body == sorted(body)  # 排序
        assert "题库类目" in body and "餐厅" in body

        stats = c.get("/api/quality-reference/stats")
        assert stats.status_code == 200
        by_cat = {row["category"]: row for row in stats.json()}
        assert by_cat["餐厅"]["external"] == 2
        assert by_cat["餐厅"]["own"] == 1
        assert by_cat["餐厅"]["total"] == 3
    finally:
        db.close()
        app_ctx.cleanup()


@pytest.mark.mysql
def test_adopt_then_patch_multi_category_and_list_filter(monkeypatch):
    """采纳后 patch 加两类型：主表一行、GET detail 两类目、list 按任一类目都能过滤到。"""
    app_ctx = build_test_app(monkeypatch)
    db = app_ctx.session_factory()
    try:
        a = _make_article(db)
        db.commit()
        c = app_ctx.client
        r = c.post("/api/quality-reference/adopt", json={"article_id": a.id, "category": "餐厅"})
        assert r.status_code == 200
        rid = r.json()["id"]
        assert [x["category"] for x in r.json()["categories"]] == ["餐厅"]  # 回落补选

        # replace-all 加第二类型
        p = c.patch(
            f"/api/quality-reference/{rid}",
            json={
                "categories": [{"category": "餐厅"}, {"category": "酒店", "question_texts": ["q1"]}]
            },
        )
        assert p.status_code == 200
        assert sorted(x["category"] for x in p.json()["categories"]) == ["酒店", "餐厅"]

        # 两个类目各自都能 list 过滤到同一 ref（主表仍一行）
        for cat in ("餐厅", "酒店"):
            lst = c.get("/api/quality-reference", params={"category": cat})
            assert lst.status_code == 200
            assert any(row["id"] == rid for row in lst.json())

        detail = c.get(f"/api/quality-reference/{rid}")
        assert detail.status_code == 200
        assert sorted(x["category"] for x in detail.json()["categories"]) == ["酒店", "餐厅"]
    finally:
        db.close()
        app_ctx.cleanup()


@pytest.mark.mysql
def test_category_questions_maps_active_pool_questions(monkeypatch):
    """category → 活跃问题词映射：去重、排除 source_active=False、按类型分组。"""
    app_ctx = build_test_app(monkeypatch)
    db = app_ctx.session_factory()
    try:
        pool = QuestionPool(user_id=1, name="池")
        db.add(pool)
        db.flush()
        db.add_all(
            [
                QuestionItem(
                    pool_id=pool.id, record_id="r1", category="餐厅", question_text="怎么开餐厅"
                ),
                QuestionItem(
                    pool_id=pool.id, record_id="r2", category="餐厅", question_text="餐厅选址"
                ),
                QuestionItem(
                    pool_id=pool.id, record_id="r3", category="酒店", question_text="酒店攻略"
                ),
                QuestionItem(
                    pool_id=pool.id,
                    record_id="r4",
                    category="餐厅",
                    question_text="已下架问题",
                    source_active=False,  # 飞书已删 → 不该出现在下拉
                ),
            ]
        )
        db.commit()

        r = app_ctx.client.get("/api/quality-reference/category-questions")
        assert r.status_code == 200
        body = r.json()
        assert set(body["餐厅"]) == {"怎么开餐厅", "餐厅选址"}
        assert body["酒店"] == ["酒店攻略"]
        assert "已下架问题" not in body.get("餐厅", [])
    finally:
        db.close()
        app_ctx.cleanup()


@pytest.mark.mysql
def test_list_title_search_and_pagination(monkeypatch):
    """标题关键词 q 只命中含该子串的标题；skip/limit 偏移分页不重叠、按 created_at desc。"""
    app_ctx = build_test_app(monkeypatch)
    try:
        c = app_ctx.client
        for t in ("阿尔法指南", "贝塔攻略", "阿尔法进阶"):
            r = c.post(
                "/api/quality-reference/import",
                json={"title": t, "markdown": f"{t}正文正文", "category": "通用"},
            )
            assert r.status_code == 200

        # 标题搜索：只命中含「阿尔法」的两条，排除「贝塔攻略」
        r = c.get("/api/quality-reference", params={"q": "阿尔法"})
        assert r.status_code == 200
        assert {row["title"] for row in r.json()} == {"阿尔法指南", "阿尔法进阶"}

        # 分页：每页 1 条，skip 递增翻页，两页不重叠（共 3 条 → 第 3 页仍有 1 条）
        p0 = c.get("/api/quality-reference", params={"limit": 1, "skip": 0})
        p1 = c.get("/api/quality-reference", params={"limit": 1, "skip": 1})
        assert len(p0.json()) == 1 and len(p1.json()) == 1
        assert p0.json()[0]["id"] != p1.json()[0]["id"]

        # 搜索 + 分页叠加：阿尔法两条里翻页仍只在命中集合内
        s0 = c.get("/api/quality-reference", params={"q": "阿尔法", "limit": 1, "skip": 0})
        s1 = c.get("/api/quality-reference", params={"q": "阿尔法", "limit": 1, "skip": 1})
        got = {s0.json()[0]["title"], s1.json()[0]["title"]}
        assert got == {"阿尔法指南", "阿尔法进阶"}
    finally:
        app_ctx.cleanup()


@pytest.mark.mysql
def test_detail_source_article_deleted_flag(monkeypatch):
    app_ctx = build_test_app(monkeypatch)
    db = app_ctx.session_factory()
    try:
        a = _make_article(db, title="将被软删")
        db.commit()
        c = app_ctx.client
        r = c.post("/api/quality-reference/adopt", json={"article_id": a.id})
        rid = r.json()["id"]

        d_live = c.get(f"/api/quality-reference/{rid}")
        assert d_live.status_code == 200
        assert d_live.json()["source_article_deleted"] is False

        a.is_deleted = True
        db.commit()

        d_deleted = c.get(f"/api/quality-reference/{rid}")
        assert d_deleted.status_code == 200
        assert d_deleted.json()["source_article_deleted"] is True
    finally:
        db.close()
        app_ctx.cleanup()
