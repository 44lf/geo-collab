# 配图随机兜底改为「按锚点回填」Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让配图随机兜底只在「已锚定但没配上」的位置各补一张、且插在该锚点，绝不抢已插位、不再全文随机撒图。

**Architecture:** 把随机替补并进 `_maybe_insert_images` 的同一趟插入（精准 > 联网 > 随机替补 > 留空），所有图一次性 `insert_images_at_positions` 落盘，索引位移天然一致、无需事后校正。用一个默认 `False` 的开关 `random_fill_missed` 从 `illustrate_one` 逐层透传，只有 goal + pipeline 共用的 `illustrate_one` 打开；退役旧的 `fallback.py` 全文撒点。

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy；测试 pytest（核心单测用 monkeypatch stub `pick_image_id` / `fetch_image_by_id`，无需 DB）。

设计稿：`docs/superpowers/specs/2026-07-16-illustration-fallback-anchor-refill-design.md`

## Global Constraints

- 后端 lint 硬门禁：`ruff check server/` + `ruff format --check server/` + `mypy server/app` 必须过（line-length=100，忽略 E501/B008）。
- `_maybe_insert_images` 是插图唯一权威、被多路径共用：新增参数**必须**默认 `False`，保证 scheme_executor / 手动 AI 排版路径逐字节等价（回归护栏是硬要求）。
- 优先级固定：精准（本地栏目）> 联网补图 > 随机替补 > 留空。
- 不变量 #1182：随机替补只在「已锚定（requested）但没配上」的位置发生；`anchored=0` → 不补。
- 随机替补图**不附 url 段落**（`official_url` 置空后再插）。
- 面向用户回复用中文。

相关文件当前签名（供实现对照，行号会随改动漂移，按函数名定位）：
- `server/app/modules/articles/ai_format.py`
  - `_maybe_insert_images(content_json, parsed, article, db, *, available_categories=None, web_fallback=False, image_search_query=None, max_images=None, prefetched_downloads=None, out_diagnostics=None) -> tuple[dict, int]`
  - `_ai_format_write_back(article_id, *, lock_started_at, new_content_json, parsed, available_categories, include_images, heading_indices, max_images, out_diagnostics=None) -> int`
  - `_web_fallback_collect_and_write_back(article_id, *, lock_started_at, new_content_json, parsed, available_categories, heading_indices, image_search_query, max_images, out_diagnostics=None) -> int`
  - `run_ai_format(article_id, *, include_images=False, lock_started_at=None, preset_id=None, user_id=None, candidate_categories=None, web_fallback=False, max_images=None, min_spacing=None, builtin_variant="conservative", format_model_selected=None, out_diagnostics=None) -> int`
  - `_run_ai_format_web_fallback(article_id, *, include_images, lock_started_at, preset_id, user_id, candidate_categories, max_images, min_spacing, builtin_variant, format_model_selected=None, out_diagnostics=None) -> int`
  - `run_ai_format_from_game_list(article_id, *, lock_started_at, game_list, preset_id, user_id, candidate_categories, max_images, min_spacing, builtin_variant, out_diagnostics=None) -> int`
- `server/app/modules/articles/ai_illustrate_svc.py`
  - `illustrate_one(*, article_id, main_category_id, user_id, options, session_factory) -> IllustrateResult`（内部 211-227 有 `apply_image_fallback` 调用块）
  - `_resolve_illustration_outcome(*, raw_error, images_inserted, fmt_diag) -> tuple[str|None, str|None, int, int, list[str]]`
- `server/app/modules/image_library/selector.py`
  - `@dataclass StockImageRef(id, url, filename, width, height, category_id=None, official_url=None)`
  - `@dataclass ImageQuery(category_id=None, count=1, excluded_ids=[], article_context=None, category_ids=[], hint=None)`
  - `pick_image_id(query, db) -> int | None`、`fetch_image_by_id(image_id, db) -> StockImageRef | None`
- `server/app/modules/image_library/fallback.py`：`apply_image_fallback` / `fill_random_images` / `_spread_positions` / `count_body_images` / `collect_used_stock_image_ids`（`apply_image_fallback` 仅 `ai_illustrate_svc.py:219` 一处调用）

---

### Task 1: `_maybe_insert_images` 加「随机替补」档 + `random_filled` 诊断

**Files:**
- Modify: `server/app/modules/articles/ai_format.py`（`_maybe_insert_images` + 顶部加 `import dataclasses`）
- Test: `server/tests/test_maybe_insert_images_random_fill.py`（新建）

**Interfaces:**
- Produces: `_maybe_insert_images(..., random_fill_missed: bool = False)`；当 `True` 且某锚点精准/联网都取不到图时，从 `valid_category_ids` 池随机取一张（排除 `used_ids`）插在同一锚点、`official_url` 置空。`out_diagnostics["random_filled"]` = 随机替补张数；`missed` 只计连随机都没补到的锚点。默认 `False` 时行为与现状逐字节一致。

- [ ] **Step 1: 写失败测试**

新建 `server/tests/test_maybe_insert_images_random_fill.py`：

```python
"""_maybe_insert_images 随机替补档（random_fill_missed）单测——不依赖 DB。"""
import dataclasses

from server.app.modules.articles import ai_format
from server.app.modules.image_library.selector import ImageQuery, StockImageRef


def _heading(text):
    return {"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "text": text}]}


def _para(text):
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


def _doc(*nodes):
    return {"type": "doc", "content": list(nodes)}


def _image_indices(doc):
    return [i for i, n in enumerate(doc["content"]) if n.get("type") == "image"]


def _install_stubs(monkeypatch, *, empty_category_ids):
    """精准池：非空栏目返回自增 id、空栏目返回 None；随机池（category_ids 多个）返回 900+。"""
    counter = {"n": 0, "r": 900}

    def fake_pick(query: ImageQuery, db):
        cids = query.category_ids
        if len(cids) == 1:  # 精准/联网单栏目
            if cids[0] in empty_category_ids:
                return None
            counter["n"] += 1
            return counter["n"]
        # 随机池（多栏目）
        counter["r"] += 1
        return counter["r"]

    def fake_fetch(image_id, db):
        return StockImageRef(
            id=image_id, url=f"/api/stock-images/{image_id}/file", filename="f.jpg",
            width=100, height=60, category_id=1, official_url="http://src.example/x",
        )

    monkeypatch.setattr(ai_format, "pick_image_id", fake_pick)
    monkeypatch.setattr(ai_format, "fetch_image_by_id", fake_fetch)


def test_random_fill_lands_on_missed_anchor_only(monkeypatch):
    # 5 个标题锚点 [0,2,4,6,8]，第 4 个（栏目 40 空）精准取不到
    doc = _doc(
        _heading("g1"), _para("a"), _heading("g2"), _para("b"),
        _heading("g4-empty"), _para("c"), _heading("g3"), _para("d"), _heading("g5"),
    )
    parsed = {"image_positions": [
        {"index": 0, "category_id": 10}, {"index": 2, "category_id": 20},
        {"index": 4, "category_id": 40}, {"index": 6, "category_id": 30},
        {"index": 8, "category_id": 50},
    ]}
    cats = [{"id": 10}, {"id": 20}, {"id": 30}, {"id": 40}, {"id": 50}]
    _install_stubs(monkeypatch, empty_category_ids={40})
    diag = {}
    new_doc, count = ai_format._maybe_insert_images(
        doc, parsed, object(), object(),
        available_categories=cats, web_fallback=False, max_images=None,
        random_fill_missed=True, out_diagnostics=diag,
    )
    # 5 个锚点各配上（含替补）→ 5 张图，落在每个标题之后
    assert count == 5
    assert diag["random_filled"] == 1
    assert diag["missed"] == 0
    # g4-empty 标题后那张是随机替补（id>=900），且其后不跟 url 段落
    img_idxs = _image_indices(new_doc)
    # 找到 g4-empty 标题在新文档里的下标
    h4 = next(i for i, n in enumerate(new_doc["content"])
              if n.get("type") == "heading" and n["content"][0]["text"] == "g4-empty")
    assert new_doc["content"][h4 + 1]["type"] == "image"
    assert new_doc["content"][h4 + 1]["attrs"]["src"].split("/")[-2] and \
        int(new_doc["content"][h4 + 1]["attrs"]["stockImageId"]) >= 900
    # 替补图后面不是 url 段落（下一个块是 g4 的正文 "c"）
    assert new_doc["content"][h4 + 2]["type"] == "paragraph"
    assert new_doc["content"][h4 + 2]["content"][0]["text"] == "c"


def test_random_fill_off_by_default_leaves_gap(monkeypatch):
    doc = _doc(_heading("g1"), _heading("g2-empty"))
    parsed = {"image_positions": [{"index": 0, "category_id": 10}, {"index": 1, "category_id": 20}]}
    cats = [{"id": 10}, {"id": 20}]
    _install_stubs(monkeypatch, empty_category_ids={20})
    diag = {}
    _new, count = ai_format._maybe_insert_images(
        doc, parsed, object(), object(),
        available_categories=cats, web_fallback=False, out_diagnostics=diag,
    )  # random_fill_missed 默认 False
    assert count == 1
    assert diag.get("random_filled", 0) == 0
    assert diag["missed"] == 1


def test_random_fill_respects_max_images(monkeypatch):
    doc = _doc(_heading("g1-empty"), _heading("g2-empty"), _heading("g3-empty"))
    parsed = {"index": None, "image_positions": [
        {"index": 0, "category_id": 10}, {"index": 1, "category_id": 20}, {"index": 2, "category_id": 30},
    ]}
    cats = [{"id": 10}, {"id": 20}, {"id": 30}]
    _install_stubs(monkeypatch, empty_category_ids={10, 20, 30})
    diag = {}
    _new, count = ai_format._maybe_insert_images(
        doc, parsed, object(), object(),
        available_categories=cats, web_fallback=False, max_images=2,
        random_fill_missed=True, out_diagnostics=diag,
    )
    assert count == 2  # 硬上限
    assert diag["random_filled"] == 2


def test_random_pool_empty_leaves_gap(monkeypatch):
    doc = _doc(_heading("g1-empty"))
    parsed = {"image_positions": [{"index": 0, "category_id": 10}]}
    cats = [{"id": 10}]

    def fake_pick(query, db):
        return None  # 精准 + 随机池都空

    monkeypatch.setattr(ai_format, "pick_image_id", fake_pick)
    monkeypatch.setattr(ai_format, "fetch_image_by_id", lambda i, db: None)
    diag = {}
    _new, count = ai_format._maybe_insert_images(
        doc, parsed, object(), object(),
        available_categories=cats, web_fallback=False,
        random_fill_missed=True, out_diagnostics=diag,
    )
    assert count == 0
    assert diag.get("random_filled", 0) == 0
    assert diag["missed"] == 1
```

- [ ] **Step 2: 跑测试确认 RED**

Run: `set -a; . ./.env; set +a; GEO_TEST_DATABASE_URL="$GEO_TEST_DATABASE_URL" pytest server/tests/test_maybe_insert_images_random_fill.py -q`
（本机用 conda geo_xzpt 的 python 全路径：`/c/Users/Administrator/miniconda3/envs/geo_xzpt/python.exe -m pytest ...`）
Expected: FAIL —— `_maybe_insert_images() got an unexpected keyword argument 'random_fill_missed'`

- [ ] **Step 3: 实现 —— 加参数 + 随机替补分支 + 诊断**

在 `ai_format.py` 顶部 import 区加：

```python
import dataclasses
```

`_maybe_insert_images` 签名末尾（`out_diagnostics` 之后）加参数：

```python
    out_diagnostics: dict[str, Any] | None = None,
    random_fill_missed: bool = False,
) -> tuple[dict, int]:
```

在循环前、与其它计数器一起初始化：

```python
    random_filled = 0  # 随机替补落图数（random_fill_missed 时用）
```

把循环里「联网补图之后、`if image_id is None: missed_labels.append(...)` 之前」的段落改成：

```python
        # 联网补图（web_fallback）尝试已在上方做完；仍无图时，若开了随机替补，
        # 从候选栏目池随机取一张替补（best-effort），插在同一锚点。
        used_random = False
        if image_id is None and random_fill_missed:
            image_id = pick_image_id(
                ImageQuery(category_ids=list(valid_category_ids), excluded_ids=used_ids), db
            )
            used_random = image_id is not None
        if image_id is None:
            missed_labels.append(label)  # 精准/联网/随机都没补到 → 记一笔 miss
            continue

        ref = fetch_image_by_id(image_id, db)
        if ref is not None:
            if used_random:
                random_filled += 1
                ref = dataclasses.replace(ref, official_url=None)  # 替补不附来源 url
            used_ids.append(image_id)
            matched_refs.append(ref)
            matched_positions.append(idx)
        else:
            missed_labels.append(label)  # 拿到 id 但取不到 ref，同样没配上
```

在 `out_diagnostics` 写回块里补一行（`missed` 那几行附近）：

```python
        out_diagnostics["random_filled"] = random_filled
```

- [ ] **Step 4: 跑测试确认 GREEN**

Run: `/c/Users/Administrator/miniconda3/envs/geo_xzpt/python.exe -m pytest server/tests/test_maybe_insert_images_random_fill.py -q`
Expected: 4 passed

- [ ] **Step 5: Lint**

Run: `ruff check server/app/modules/articles/ai_format.py && ruff format --check server/app/modules/articles/ai_format.py server/tests/test_maybe_insert_images_random_fill.py`
Expected: 无报错（有格式问题就 `ruff format` 去掉 `--check` 改写后重跑）

- [ ] **Step 6: Commit**

```bash
git add server/app/modules/articles/ai_format.py server/tests/test_maybe_insert_images_random_fill.py
git commit -m "feat(illustrate): _maybe_insert_images 随机替补档（按锚点回填，不附url）+ random_filled 诊断"
```

---

### Task 2: 透传开关 + `illustrate_one` 接线（去掉 apply_image_fallback）

**Files:**
- Modify: `server/app/modules/articles/ai_format.py`（5 个 wrapper 加 `random_fill_missed` 转发 + `run_ai_format_from_game_list` 传播 `random_filled`）
- Modify: `server/app/modules/articles/ai_illustrate_svc.py`（`illustrate_one` 传 `True` 并删除 `apply_image_fallback` 调用块 + import；`_resolve_illustration_outcome` 补 `random_filled` 软提示）
- Test: `server/tests/test_illustrate_random_fill_wiring.py`（新建）

**Interfaces:**
- Consumes: Task 1 的 `_maybe_insert_images(..., random_fill_missed)`。
- Produces: `run_ai_format` / `run_ai_format_from_game_list` / `_run_ai_format_web_fallback` / `_ai_format_write_back` / `_web_fallback_collect_and_write_back` 均新增 `random_fill_missed: bool = False`；`illustrate_one` 对两条 run_ai_format* 调用都传 `random_fill_missed=True`，不再调用 `apply_image_fallback`；`IllustrateResult` 的 `images_inserted` 现含随机替补，`fmt_diag["random_filled"]` 透出到 `_resolve_illustration_outcome` 生成的 warning。

- [ ] **Step 1: 写失败测试（透传 + 门控）**

新建 `server/tests/test_illustrate_random_fill_wiring.py`：

```python
"""验证 random_fill_missed 从 illustrate_one 透传到 _maybe_insert_images；默认路径不开。"""
import inspect

from server.app.modules.articles import ai_format, ai_illustrate_svc


def test_all_wrappers_accept_random_fill_missed():
    for fn in (
        ai_format._maybe_insert_images,
        ai_format._ai_format_write_back,
        ai_format._web_fallback_collect_and_write_back,
        ai_format.run_ai_format,
        ai_format._run_ai_format_web_fallback,
        ai_format.run_ai_format_from_game_list,
    ):
        assert "random_fill_missed" in inspect.signature(fn).parameters, fn.__name__
        assert inspect.signature(fn).parameters["random_fill_missed"].default is False


def test_illustrate_one_enables_random_fill_and_no_apply_image_fallback():
    src = inspect.getsource(ai_illustrate_svc.illustrate_one)
    assert "random_fill_missed=True" in src
    assert "apply_image_fallback" not in src


def test_run_ai_format_forwards_random_fill_missed(monkeypatch):
    captured = {}

    def fake_write_back(article_id, **kw):
        captured.update(kw)
        return 0

    # 让 run_ai_format 走到 _ai_format_write_back（web_fallback=False 同步路径）
    monkeypatch.setattr(ai_format, "_ai_format_write_back", fake_write_back)

    class _Prep:
        content_json = {"type": "doc", "content": [{"type": "paragraph",
            "content": [{"type": "text", "text": "x"}]}]}
        valid_indices = {0}
        system_prompt = "p"
        available_categories = []
        model = "m"; api_key = "k"; base_url = None; timeout_seconds = 1; image_search_query = None

    monkeypatch.setattr(ai_format, "_ai_format_prepare", lambda *a, **k: _Prep())
    monkeypatch.setattr(ai_format, "_call_litellm_completion",
                        lambda **k: type("R", (), {"choices": [type("C", (), {"message":
                        type("M", (), {"content": '{"heading_indices": []}'})()})()]})())
    ai_format.run_ai_format(1, include_images=True, random_fill_missed=True)
    assert captured.get("random_fill_missed") is True
```

- [ ] **Step 2: 跑测试确认 RED**

Run: `/c/Users/Administrator/miniconda3/envs/geo_xzpt/python.exe -m pytest server/tests/test_illustrate_random_fill_wiring.py -q`
Expected: FAIL（signature 无 `random_fill_missed` / `illustrate_one` 仍含 `apply_image_fallback`）

- [ ] **Step 3a: 5 个 wrapper 加参数并转发**

对 `ai_format.py` 逐个函数在签名加 `random_fill_missed: bool = False`，并在其调用下游的地方带上：

- `_ai_format_write_back(...)`：签名加参数；把 `_maybe_insert_images(... out_diagnostics=image_diag)` 改为 `... out_diagnostics=image_diag, random_fill_missed=random_fill_missed)`。
- `_web_fallback_collect_and_write_back(...)`：签名加参数；两处对 `_maybe_insert_images` / `_ai_format_write_back` 的调用都补 `random_fill_missed=random_fill_missed`（含 `has_images_in_content` 早退分支调用的 `_ai_format_write_back`）。
- `run_ai_format(...)`：签名加参数；转发给 `_run_ai_format_web_fallback(..., random_fill_missed=random_fill_missed)` 与 `_ai_format_write_back(..., random_fill_missed=random_fill_missed)`。
- `_run_ai_format_web_fallback(...)`：签名加参数；转发给 `_web_fallback_collect_and_write_back(..., random_fill_missed=random_fill_missed)`（含 `include_images=False` 分支调用的 `_ai_format_write_back`）。
- `run_ai_format_from_game_list(...)`：签名加参数；转发给 `_web_fallback_collect_and_write_back(..., random_fill_missed=random_fill_missed)`；并在其 `out_diagnostics` 写回块补 `out_diagnostics["random_filled"] = fmt_diag.get("random_filled", 0)`。

- [ ] **Step 3b: `illustrate_one` 打开开关、删除旧兜底块**

`ai_illustrate_svc.py`：给两处 run_ai_format* 调用都加 `random_fill_missed=True`：

```python
        images_inserted = run_ai_format_from_game_list(
            article_id,
            lock_started_at=lock_started_at,
            game_list=effective_game_list,
            preset_id=options.preset_id,
            user_id=user_id,
            candidate_categories=candidate_categories,
            max_images=max_images,
            min_spacing=min_spacing,
            builtin_variant=builtin_variant,
            out_diagnostics=fmt_diag,
            random_fill_missed=True,
        )
    else:
        images_inserted = run_ai_format(
            article_id,
            include_images=True,
            lock_started_at=lock_started_at,
            preset_id=options.preset_id,
            user_id=user_id,
            candidate_categories=candidate_categories,
            web_fallback=options.web_fallback,
            max_images=max_images,
            min_spacing=min_spacing,
            builtin_variant=builtin_variant,
            format_model_selected=options.format_model,
            out_diagnostics=fmt_diag,
            random_fill_missed=True,
        )
```

删除整段 `fallback_inserted` 块（约 211-227 行，从 `fallback_inserted = 0` 到 `_logger.exception("fallback random fill failed ...")` 的 try/except），并删除顶部 `from server.app.modules.image_library.fallback import apply_image_fallback`。若下游有 `images_inserted + fallback_inserted` 之类的合计，改为只用 `images_inserted`（现已含替补）。删除 `candidate_categories` 仅用于兜底 `category_ids` 的死代码（若其它地方仍用则保留）。

- [ ] **Step 3c: `_resolve_illustration_outcome` 透出 `random_filled` 软提示**

在 `_resolve_illustration_outcome` 里 `missed_games = ...` 之后读一行：

```python
    random_filled = int(fmt_diag.get("random_filled", 0) or 0)
```

在 partial_images warning 生成之后追加（不覆盖更高优先级信号）：

```python
    if random_filled > 0:
        note = f"random_filled: {random_filled} 张为随机替补（位置对、图可能与正文不相关）"
        warning = note if warning is None else f"{warning}；{note}"
```

- [ ] **Step 4: 跑测试确认 GREEN + 相关回归**

Run: `/c/Users/Administrator/miniconda3/envs/geo_xzpt/python.exe -m pytest server/tests/test_illustrate_random_fill_wiring.py server/tests/test_ai_illustrate_svc.py -q`
Expected: 全 PASS（若 `test_ai_illustrate_svc.py` 有断言旧 `apply_image_fallback` 撒点行为的用例，按新语义改：随机替补应落在漏配锚点后，而非 `_spread_positions` 撒点位置）

- [ ] **Step 5: Lint + mypy**

Run: `ruff check server/app/modules/articles/ && ruff format --check server/app/modules/articles/ && mypy server/app/modules/articles/ai_format.py server/app/modules/articles/ai_illustrate_svc.py`
Expected: 无报错

- [ ] **Step 6: Commit**

```bash
git add server/app/modules/articles/ai_format.py server/app/modules/articles/ai_illustrate_svc.py server/tests/test_illustrate_random_fill_wiring.py server/tests/test_ai_illustrate_svc.py
git commit -m "feat(illustrate): random_fill_missed 透传到 illustrate_one（goal+pipeline）替代 apply_image_fallback 撒点"
```

---

### Task 3: 退役 `fallback.py` 撒点 + 迁移测试

**Files:**
- Modify/Delete: `server/app/modules/image_library/fallback.py`
- Modify: `server/tests/test_illustration_fallback.py`

**Interfaces:**
- Consumes: Task 2 已删除 `apply_image_fallback` 的唯一调用方。
- Produces: `fallback.py` 不再导出 `_spread_positions` / `fill_random_images` / `apply_image_fallback`；随机兜底能力完全由 Task 1 的 `_maybe_insert_images(random_fill_missed=True)` 承担。

- [ ] **Step 1: 确认无残留引用**

Run: `grep -rn "apply_image_fallback\|fill_random_images\|_spread_positions" server --include=*.py | grep -v "server/tests/test_illustration_fallback.py"`
Expected: 无输出（Task 2 已摘除生产调用）。若有输出，先回到对应位置清理。

- [ ] **Step 2: 删除撒点函数**

`fallback.py`：删除 `_spread_positions`、`fill_random_images`、`apply_image_fallback` 三个函数。`count_body_images` / `collect_used_stock_image_ids` 若删除后无任何引用（Step 1 已确认仅本文件自用）则一并删除；若删空则整文件删除。保留判断：

Run: `grep -rn "count_body_images\|collect_used_stock_image_ids\|from server.app.modules.image_library.fallback\|image_library.fallback import" server/app --include=*.py`
—— 无生产引用则可整文件删（`git rm server/app/modules/image_library/fallback.py`）。

- [ ] **Step 3: 迁移测试文件**

`server/tests/test_illustration_fallback.py`：删除针对 `_spread_positions` / `fill_random_images` / `apply_image_fallback` / `count_body_images` / `collect_used_stock_image_ids` 的用例。若该文件因此变空，`git rm` 删除整文件（能力已被 `test_maybe_insert_images_random_fill.py` 覆盖）。

- [ ] **Step 4: 跑全套配图相关测试确认 GREEN**

Run: `/c/Users/Administrator/miniconda3/envs/geo_xzpt/python.exe -m pytest server/tests/test_maybe_insert_images_random_fill.py server/tests/test_illustrate_random_fill_wiring.py server/tests/test_ai_illustrate_svc.py server/tests/test_ai_illustrate_node.py server/tests/test_pipeline_ai_illustrate.py server/tests/test_illustrate_game_list_endpoint.py server/tests/test_web_fallback.py -q`
Expected: 全 PASS（`test_illustration_fallback.py` 已删或瘦身）

- [ ] **Step 5: Lint + 结构门禁**

Run: `ruff check server/ && ruff format --check server/ && /c/Users/Administrator/miniconda3/envs/geo_xzpt/python.exe -m pytest server/tests/ -q -k "structure or import_gate" 2>/dev/null; true`
Expected: lint 无报错（结构门禁若存在也需过）

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor(illustrate): 退役 fallback.py 全文撒点，随机兜底并入 _maybe_insert_images"
```

---

## Self-Review

**Spec coverage：**
- §3.1 核心机制（同趟随机替补、一次插入、优先级）→ Task 1 Step 3。
- §3.2 max_images / #1182 不变量 → Task 1 测试 3、2。
- §3.3 替补不附 url → Task 1 Step 3（`dataclasses.replace(official_url=None)`）+ 测试 1 断言无 url 段落。
- §4 门控与透传（6 函数 + illustrate_one 传 True + scheme/手动 默认 False）→ Task 2 Step 3a/3b + wiring 测试。
- §5.1 删除撒点 → Task 2 Step 3b（删调用）+ Task 3（删函数/测试）。
- §5.2 诊断 `random_filled` / `missed` 收窄 → Task 1（emit）+ Task 2 Step 3c（软提示）。
- §6 非目标（scheme 不动）→ Task 2 靠默认 False 保证（wiring 测试 signature 断言 default False）。
- §7 测试计划全部映射到 Task 1/2/3 的测试步骤。

**Placeholder scan：** 无 TBD/TODO；每个改代码的 step 都给了具体代码块或具体函数名+改法。Task 2 Step 3a 用「逐函数列举 + 具体转发点」描述而非贴 6 份近似代码（避免 DRY 违背，转发逻辑同构且已在 Interfaces 给出签名）。

**Type consistency：** `random_fill_missed: bool = False` 全链一致；`random_filled` 键名在 Task 1 emit、Task 2 传播/读取一致；`StockImageRef` 字段与 selector 定义一致；`dataclasses.replace` 适用于非 frozen dataclass。

## Execution Handoff

计划已保存到 `docs/superpowers/plans/2026-07-16-illustration-fallback-anchor-refill.md`。用户已表示「后续再弄」，实现时任选：

1. **Subagent-Driven（推荐）** —— 每个 Task 派新 subagent、Task 间两段式评审。
2. **Inline Execution** —— 本会话按 executing-plans 批量执行 + 检查点。
