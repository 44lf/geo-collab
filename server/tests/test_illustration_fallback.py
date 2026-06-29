"""配图兜底：检查 + 随机补图。Task 1 纯函数单测（无需 MySQL，stub selector）。"""

from __future__ import annotations

from types import SimpleNamespace

from server.app.modules.image_library import fallback as fb
from server.app.modules.image_library.selector import StockImageRef


def _doc(*nodes):
    return {"type": "doc", "content": list(nodes)}


def _para(text):
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


def _img(stock_id):
    return {"type": "image", "attrs": {"src": "/x", "stockImageId": stock_id}}


def _ref(image_id):
    return StockImageRef(
        id=image_id, url=f"/u/{image_id}", filename=f"{image_id}.jpg", width=800, height=400
    )


def _seq_pick(pool):
    """返回一个 pick_image_id 替身：从 pool 里给出第一个不在 excluded_ids 的 id。"""

    def _pick(query, db):
        for pid in pool:
            if pid not in query.excluded_ids:
                return pid
        return None

    return _pick


def test_count_body_images():
    assert fb.count_body_images(_doc(_para("a"))) == 0
    assert fb.count_body_images(_doc(_para("a"), _img(1), _img(2))) == 2


def test_collect_used_stock_image_ids():
    assert fb.collect_used_stock_image_ids(_doc(_para("a"), _img(7), _img(9))) == {7, 9}


def test_fill_random_images_inserts_gap(monkeypatch):
    monkeypatch.setattr(fb, "pick_image_id", _seq_pick([101, 102, 103]))
    monkeypatch.setattr(fb, "fetch_image_by_id", lambda i, db: _ref(i))
    article = SimpleNamespace(content_json=_doc(_para("a"), _para("b"), _para("c")), version=1)
    db = SimpleNamespace(commit=lambda: None)
    n = fb.fill_random_images(db, article, category_ids=[5], gap=2)
    assert n == 2
    assert fb.count_body_images(article.content_json) == 2
    assert article.version == 2


def test_fill_random_images_dedups_used(monkeypatch):
    # 正文已含 101；候选只有 101 → 取不到新图 → 0，且不抛异常
    monkeypatch.setattr(fb, "pick_image_id", _seq_pick([101]))
    monkeypatch.setattr(fb, "fetch_image_by_id", lambda i, db: _ref(i))
    article = SimpleNamespace(content_json=_doc(_para("a"), _img(101)), version=1)
    db = SimpleNamespace(commit=lambda: None)
    assert fb.fill_random_images(db, article, category_ids=[5], gap=1) == 0


def test_apply_fallback_fills_to_target(monkeypatch):
    # requested=3, current=1 → target=3 → 补 2
    monkeypatch.setattr(fb, "pick_image_id", _seq_pick([201, 202, 203]))
    monkeypatch.setattr(fb, "fetch_image_by_id", lambda i, db: _ref(i))
    article = SimpleNamespace(
        content_json=_doc(_para("a"), _img(9), _para("b"), _para("c")),
        is_deleted=False,
        version=1,
    )
    db = SimpleNamespace(get=lambda model, _id: article, commit=lambda: None, close=lambda: None)
    n = fb.apply_image_fallback(
        article_id=1, requested=3, category_ids=[5], max_images=12, session_factory=lambda: db
    )
    assert n == 2
    assert fb.count_body_images(article.content_json) == 3


def test_apply_fallback_noop_when_enough():
    # requested=2, current=3 → target=2, gap<0 → 0
    article = SimpleNamespace(
        content_json=_doc(_img(1), _img(2), _img(3)), is_deleted=False, version=1
    )
    db = SimpleNamespace(get=lambda m, i: article, commit=lambda: None, close=lambda: None)
    n = fb.apply_image_fallback(
        article_id=1, requested=2, category_ids=[5], max_images=12, session_factory=lambda: db
    )
    assert n == 0


def test_apply_fallback_noop_when_no_categories():
    assert (
        fb.apply_image_fallback(
            article_id=1, requested=3, category_ids=[], max_images=12, session_factory=lambda: None
        )
        == 0
    )
