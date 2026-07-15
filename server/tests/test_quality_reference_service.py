"""quality_reference service 层测试（对抗评审质量门 Task 2）。

覆盖：hash 归一化、adopt 门禁（仅 approved）、import 的 nh3 清洗 + 幂等、
pick_references 的 external 优先，以及本任务额外补的两个 helper：
is_source_article_deleted（own 参考的源文章软删判定）、
category_origin_stats（按 category 聚合 external/own/total）。
"""

import pytest

from server.app.modules.articles.models import Article
from server.app.modules.quality_reference import service as svc
from server.app.modules.quality_reference.models import QualityReference
from server.tests.utils import build_test_app


def _make_article(db, *, review_status="approved", plain="正文正文正文", title="T", cat=None):
    a = Article(
        user_id=1,
        title=title,
        content_json="{}",
        content_html="",
        plain_text=plain,
        word_count=3,
        status="draft",
        review_status=review_status,
        source_question_category=cat,
    )
    db.add(a)
    db.flush()
    return a


def test_hash_normalizes():
    assert svc.compute_content_hash("标题", "正 文  内容") == svc.compute_content_hash(
        "标题", "正 文 内容"
    )


@pytest.mark.mysql
def test_adopt_rejects_non_approved(monkeypatch):
    app_ctx = build_test_app(monkeypatch)
    db = app_ctx.session_factory()
    try:
        a = _make_article(db, review_status="pending")
        db.commit()
        from server.app.shared.errors import ValidationError

        with pytest.raises(ValidationError):
            svc.adopt_article(db, user_id=1, article_id=a.id)
    finally:
        db.close()
        app_ctx.cleanup()


@pytest.mark.mysql
def test_import_sanitizes_html_and_is_idempotent(monkeypatch):
    app_ctx = build_test_app(monkeypatch)
    db = app_ctx.session_factory()
    try:
        ref, _ = svc.import_external(
            db,
            user_id=1,
            title="T",
            markdown="正文<script>alert(1)</script>内容",
            category="通用",
            source_url=None,
            platform=None,
        )
        db.commit()
        assert "<script>" not in ref.content_html  # nh3 清洗
        ref2, _ = svc.import_external(
            db,
            user_id=1,
            title="T",
            markdown="正文<script>alert(1)</script>内容",
            category="通用",
            source_url=None,
            platform=None,
        )
        db.commit()
        assert ref2.id == ref.id and db.query(QualityReference).count() == 1  # 幂等
    finally:
        db.close()
        app_ctx.cleanup()


@pytest.mark.mysql
def test_pick_prefers_external_then_universal(monkeypatch):
    app_ctx = build_test_app(monkeypatch)
    db = app_ctx.session_factory()
    try:
        # 同类目下 own + external，应优先 external
        a = _make_article(db)
        db.commit()
        svc.adopt_article(
            db, user_id=1, article_id=a.id
        )  # own，category=None（文章无 source_question_category）
        svc.import_external(
            db,
            user_id=1,
            title="X",
            markdown="乙" * 50,
            category="餐厅",
            source_url=None,
            platform=None,
        )
        db.commit()
        hit = svc.pick_references(db, category="餐厅", k=3, truncate_chars=10)
        assert hit and hit[0]["origin"] == "external" and len(hit[0]["plain_text"]) <= 10
    finally:
        db.close()
        app_ctx.cleanup()


@pytest.mark.mysql
def test_is_source_article_deleted(monkeypatch):
    app_ctx = build_test_app(monkeypatch)
    db = app_ctx.session_factory()
    try:
        # external 参考：article_id 恒 NULL，本无源文章 → False
        ext_ref, _ = svc.import_external(
            db,
            user_id=1,
            title="外部参考",
            markdown="外部" * 30,
            category="通用",
            source_url=None,
            platform=None,
        )
        db.commit()
        assert svc.is_source_article_deleted(db, ext_ref) is False

        # own 参考：源文章已软删 → True
        deleted_article = _make_article(db, title="被删文章")
        db.commit()
        own_ref_deleted = svc.adopt_article(db, user_id=1, article_id=deleted_article.id)
        db.commit()
        deleted_article.is_deleted = True
        db.commit()
        db.refresh(own_ref_deleted)
        assert svc.is_source_article_deleted(db, own_ref_deleted) is True

        # own 参考：源文章仍存活 → False
        live_article = _make_article(db, title="存活文章")
        db.commit()
        own_ref_live = svc.adopt_article(db, user_id=1, article_id=live_article.id)
        db.commit()
        assert svc.is_source_article_deleted(db, own_ref_live) is False

        # own 参考但 article_id 已 NULL（FK SET NULL / 源被物理删）→ True
        own_ref_live.article_id = None
        db.commit()
        db.refresh(own_ref_live)
        assert own_ref_live.origin == "own" and own_ref_live.article_id is None
        assert svc.is_source_article_deleted(db, own_ref_live) is True
    finally:
        db.close()
        app_ctx.cleanup()


@pytest.mark.mysql
def test_category_origin_stats(monkeypatch):
    app_ctx = build_test_app(monkeypatch)
    db = app_ctx.session_factory()
    try:
        svc.import_external(
            db,
            user_id=1,
            title="A1",
            markdown="甲" * 30,
            category="餐厅",
            source_url=None,
            platform=None,
        )
        svc.import_external(
            db,
            user_id=1,
            title="A2",
            markdown="乙" * 30,
            category="餐厅",
            source_url=None,
            platform=None,
        )
        db.commit()
        a1 = _make_article(db, title="own1", plain="own own own")
        db.commit()
        own_ref = svc.adopt_article(db, user_id=1, article_id=a1.id)
        svc.set_reference_categories(db, own_ref.id, [{"category": "餐厅", "question_texts": None}])
        db.commit()

        svc.import_external(
            db,
            user_id=1,
            title="B1",
            markdown="丙" * 30,
            category="酒店",
            source_url=None,
            platform=None,
        )
        db.commit()

        stats = {row["category"]: row for row in svc.category_origin_stats(db)}
        assert stats["餐厅"]["external"] == 2
        assert stats["餐厅"]["own"] == 1
        assert stats["餐厅"]["total"] == 3
        assert stats["酒店"]["external"] == 1
        assert stats["酒店"]["own"] == 0
        assert stats["酒店"]["total"] == 1
    finally:
        db.close()
        app_ctx.cleanup()


@pytest.mark.mysql
def test_adopt_fills_category_from_article_source(monkeypatch):
    """own 采纳时用 article.source_question_category + source_question_texts 关联一条。"""
    app_ctx = build_test_app(monkeypatch)
    db = app_ctx.session_factory()
    try:
        a = _make_article(db, cat="餐厅")
        a.source_question_texts = ["问题甲"]
        db.commit()
        ref = svc.adopt_article(db, user_id=1, article_id=a.id)
        db.commit()
        db.refresh(ref)
        assert [c.category for c in ref.categories] == ["餐厅"]
        assert ref.categories[0].question_texts == ["问题甲"]
    finally:
        db.close()
        app_ctx.cleanup()


@pytest.mark.mysql
def test_adopt_fallback_category_when_article_has_none(monkeypatch):
    """文章无 source_question_category 时回落 fallback_category（前端补选），无问题词。"""
    app_ctx = build_test_app(monkeypatch)
    db = app_ctx.session_factory()
    try:
        a = _make_article(db, cat=None)
        db.commit()
        ref = svc.adopt_article(db, user_id=1, article_id=a.id, fallback_category="酒店")
        db.commit()
        db.refresh(ref)
        assert [c.category for c in ref.categories] == ["酒店"]
        assert ref.categories[0].question_texts is None
    finally:
        db.close()
        app_ctx.cleanup()


@pytest.mark.mysql
def test_set_reference_categories_replace_all_single_row(monkeypatch):
    """adopt 后再 patch 加第二类型：主表仍一行、子表两行；replace-all 整体替换。"""
    app_ctx = build_test_app(monkeypatch)
    db = app_ctx.session_factory()
    try:
        a = _make_article(db, cat="餐厅")
        db.commit()
        ref = svc.adopt_article(db, user_id=1, article_id=a.id)
        db.commit()
        # replace-all 加两类型
        svc.set_reference_categories(
            db,
            ref.id,
            [
                {"category": "餐厅", "question_texts": None},
                {"category": "酒店", "question_texts": ["问题乙"]},
            ],
        )
        db.commit()
        db.refresh(ref)
        assert db.query(QualityReference).count() == 1  # 主表仍一行（铁律）
        cats = sorted(c.category for c in ref.categories)
        assert cats == ["酒店", "餐厅"]  # 子表两行

        # 再 replace-all 成单一类型 → 覆盖
        svc.set_reference_categories(db, ref.id, [{"category": "民宿", "question_texts": None}])
        db.commit()
        db.refresh(ref)
        assert [c.category for c in ref.categories] == ["民宿"]

        # 空数组 = 清空 = 通用
        svc.set_reference_categories(db, ref.id, [])
        db.commit()
        db.refresh(ref)
        assert ref.categories == []
    finally:
        db.close()
        app_ctx.cleanup()


@pytest.mark.mysql
def test_pick_multi_category_ref_hits_both_and_falls_back(monkeypatch):
    """一个 ref 挂两类型：pick(类型A) 命中、pick(类型B) 也命中；pick(无关类型) 回落通用池。"""
    app_ctx = build_test_app(monkeypatch)
    db = app_ctx.session_factory()
    try:
        # ref1：external，挂 餐厅 + 酒店 两类型
        ref1, _ = svc.import_external(
            db,
            user_id=1,
            title="双类型参考",
            markdown="丁" * 40,
            category="餐厅",
            source_url=None,
            platform=None,
        )
        db.commit()
        svc.set_reference_categories(
            db,
            ref1.id,
            [
                {"category": "餐厅", "question_texts": None},
                {"category": "酒店", "question_texts": None},
            ],
        )
        db.commit()
        # ref2：无任何关联行 = 通用兜底池
        ref2, _ = svc.import_external(
            db,
            user_id=1,
            title="通用参考",
            markdown="戊" * 40,
            category=None,
            source_url=None,
            platform=None,
        )
        db.commit()

        hit_a = svc.pick_references(db, category="餐厅", k=5, truncate_chars=10)
        assert ref1.id in {r["id"] for r in hit_a}
        assert any(r["id"] == ref1.id and r["category"] == "餐厅" for r in hit_a)

        hit_b = svc.pick_references(db, category="酒店", k=5, truncate_chars=10)
        assert ref1.id in {r["id"] for r in hit_b}
        assert any(r["id"] == ref1.id and r["category"] == "酒店" for r in hit_b)

        # 无关类型：池 A 空 → 回落通用池（无关联行的 ref2），且 category=None
        hit_c = svc.pick_references(db, category="不存在类型", k=5, truncate_chars=10)
        ids_c = {r["id"] for r in hit_c}
        assert ref2.id in ids_c
        assert ref1.id not in ids_c  # ref1 有关联行 → 不进通用池
        assert all(r["category"] is None for r in hit_c)
    finally:
        db.close()
        app_ctx.cleanup()
