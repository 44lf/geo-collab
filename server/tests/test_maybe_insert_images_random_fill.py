"""_maybe_insert_images 随机替补档（random_fill_missed）单测——不依赖 DB。

覆盖设计稿 §3：精准/联网都取不到图时，从候选栏目池随机取一张替补、插在同一锚点、
不附 url；random_fill_missed 默认 False 时零随机填（回归护栏）。
"""

from server.app.modules.articles import ai_format
from server.app.modules.image_library.selector import ImageQuery, StockImageRef


def _heading(text):
    return {"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "text": text}]}


def _para(text):
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


def _doc(*nodes):
    return {"type": "doc", "content": list(nodes)}


def _install_stubs(monkeypatch, *, empty_category_ids):
    """精准/联网单栏目：非空返回自增 id、空栏目返回 None；随机池（多栏目）返回 900+。"""
    counter = {"n": 0, "r": 900}

    def fake_pick(query: ImageQuery, db):
        cids = query.category_ids
        if len(cids) == 1:  # 精准/联网单栏目
            if cids[0] in empty_category_ids:
                return None
            counter["n"] += 1
            return counter["n"]
        counter["r"] += 1  # 随机池（valid_category_ids 多栏目）
        return counter["r"]

    def fake_fetch(image_id, db):
        return StockImageRef(
            id=image_id,
            url=f"/api/stock-images/{image_id}/file",
            filename="f.jpg",
            width=100,
            height=60,
            category_id=1,
            official_url="http://src.example/x",
        )

    monkeypatch.setattr(ai_format, "pick_image_id", fake_pick)
    monkeypatch.setattr(ai_format, "fetch_image_by_id", fake_fetch)


def test_random_fill_lands_on_missed_anchor_only(monkeypatch):
    # 5 个标题锚点 [0,2,4,6,8]，第 3 个（栏目 40 空）精准取不到 → 随机替补
    doc = _doc(
        _heading("g1"),
        _para("a"),
        _heading("g2"),
        _para("b"),
        _heading("g4-empty"),
        _para("c"),
        _heading("g3"),
        _para("d"),
        _heading("g5"),
    )
    parsed = {
        "image_positions": [
            {"index": 0, "category_id": 10},
            {"index": 2, "category_id": 20},
            {"index": 4, "category_id": 40},
            {"index": 6, "category_id": 30},
            {"index": 8, "category_id": 50},
        ]
    }
    cats = [{"id": 10}, {"id": 20}, {"id": 30}, {"id": 40}, {"id": 50}]
    _install_stubs(monkeypatch, empty_category_ids={40})
    diag = {}
    new_doc, count = ai_format._maybe_insert_images(
        doc,
        parsed,
        object(),
        object(),
        available_categories=cats,
        web_fallback=False,
        max_images=None,
        random_fill_missed=True,
        out_diagnostics=diag,
    )
    assert count == 5
    assert diag["random_filled"] == 1
    assert diag["missed"] == 0

    nodes = new_doc["content"]
    h4 = next(
        i
        for i, n in enumerate(nodes)
        if n.get("type") == "heading" and n["content"][0]["text"] == "g4-empty"
    )
    # g4-empty 标题后紧跟随机替补图（id>=900），再后是原正文 "c"（说明未附 url 段落）
    assert nodes[h4 + 1]["type"] == "image"
    assert int(nodes[h4 + 1]["attrs"]["stockImageId"]) >= 900
    assert nodes[h4 + 2]["type"] == "paragraph"
    assert nodes[h4 + 2]["content"][0]["text"] == "c"


def test_random_fill_off_by_default_leaves_gap(monkeypatch):
    doc = _doc(_heading("g1"), _heading("g2-empty"))
    parsed = {"image_positions": [{"index": 0, "category_id": 10}, {"index": 1, "category_id": 20}]}
    cats = [{"id": 10}, {"id": 20}]
    _install_stubs(monkeypatch, empty_category_ids={20})
    diag = {}
    _new, count = ai_format._maybe_insert_images(
        doc,
        parsed,
        object(),
        object(),
        available_categories=cats,
        web_fallback=False,
        out_diagnostics=diag,
    )  # random_fill_missed 默认 False
    assert count == 1
    assert diag.get("random_filled", 0) == 0
    assert diag["missed"] == 1


def test_random_fill_respects_max_images(monkeypatch):
    doc = _doc(_heading("g1-empty"), _heading("g2-empty"), _heading("g3-empty"))
    parsed = {
        "image_positions": [
            {"index": 0, "category_id": 10},
            {"index": 1, "category_id": 20},
            {"index": 2, "category_id": 30},
        ]
    }
    cats = [{"id": 10}, {"id": 20}, {"id": 30}]
    _install_stubs(monkeypatch, empty_category_ids={10, 20, 30})
    diag = {}
    _new, count = ai_format._maybe_insert_images(
        doc,
        parsed,
        object(),
        object(),
        available_categories=cats,
        web_fallback=False,
        max_images=2,
        random_fill_missed=True,
        out_diagnostics=diag,
    )
    assert count == 2  # 硬上限
    assert diag["random_filled"] == 2


def test_random_pool_empty_leaves_gap(monkeypatch):
    doc = _doc(_heading("g1-empty"))
    parsed = {"image_positions": [{"index": 0, "category_id": 10}]}
    cats = [{"id": 10}]
    monkeypatch.setattr(ai_format, "pick_image_id", lambda query, db: None)
    monkeypatch.setattr(ai_format, "fetch_image_by_id", lambda i, db: None)
    diag = {}
    _new, count = ai_format._maybe_insert_images(
        doc,
        parsed,
        object(),
        object(),
        available_categories=cats,
        web_fallback=False,
        random_fill_missed=True,
        out_diagnostics=diag,
    )
    assert count == 0
    assert diag.get("random_filled", 0) == 0
    assert diag["missed"] == 1
