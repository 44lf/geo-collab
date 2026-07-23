# 删除游戏库 TapTap 爬虫 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从 `game_library` 模块彻底移除 TapTap 数据源爬虫（`sources/taptap.py` 及其所有运行时接线、测试、配置默认值、文档），让游戏库入库只保留 baidu 一个源。

**Architecture:** TapTap 爬虫是 `game_library` 三层接线里的一个 `GameSource`：`registry.SOURCES` 注册它、`ingest_service._collect_from_all_sources` 与 `scheduler.run_ingest_once` 调它（含 `get_detail` 补截图的特判）、`GameIngestConfig.source_order` 默认把它排在 baidu 前面。删除按"先摘引用、再删文件、最后清常量与文档"的顺序,保证每个 commit 后 import 树可加载、`test_game_*` 全绿。

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy / Alembic（MySQL）/ pytest；前端 React+TS（仅一处 placeholder 文案）。

## Global Constraints

- **只删爬虫,绝不碰 TapTap 发布驱动。** 本计划范围 = `server/app/modules/game_library/` 下的爬虫这条线。**以下 TapTap 发布驱动文件严禁改动**（往 TapTap 发文章,与爬虫无关）：`server/app/modules/tasks/drivers/taptap*.py`、`tasks/drivers/bootstrap.py`（其 L15 `import ...tasks.drivers.taptap`）、`tasks/drivers/base.py`、`tasks/taptap_health.py`、`tasks/runner_api.py`、`server/app/modules/accounts/*`、`web/src/features/accounts/*`、`web/src/api/accounts.ts`、`web/src/types.ts`（含 TapTap 发布驱动字段 `group_id`/`x_ua_configured`,爬虫无关）、`server/tests/test_taptap_driver.py`、`test_taptap_contents.py`、`test_taptap_health.py`、`server/alembic/versions/0051_seed_taptap_platform.py`、`.env.example`/`.env` 里的 `GEO_TAPTAP_*`、`config.py` 的 `taptap_cookie_check_*`、`main.py:553` 的 `taptap_health` import。
- **已应用的历史迁移不改。** `0067_game_library.py` / `0068_game_ingest_config.py` / `0069_*` 已在生产/dev 应用,一律新增迁移,不回改旧文件。
- **每个 commit 后 `ruff check server/` 与相关 `test_game_*` 必须绿。** 后端测试需 `GEO_TEST_DATABASE_URL`(库名含 "test");无 DB 环境时 `@pytest.mark.mysql` 用例自动跳过,纯逻辑用例仍跑。
- **命名口径:** 源标识串一律小写 `"baidu"`；不要新增替身源名。

---

## ⚠️ 决策点（交审核子代理重点确认，非本计划自行拍板）

1. **删除 vs 休眠。** 用户要求"删去"。TapTap 爬虫在架构上是**托管夜间刷新 `refresh_one_game` 唯一能出截图的源**（baidu `search_by_name` 恒 `screenshot_urls=[]`,见 [`sources/baidu.py:150`](../../../server/app/modules/game_library/sources/baidu.py#L150) 与 [`ingest_service.py:188`](../../../server/app/modules/game_library/ingest_service.py#L188)）。**但注意:这不是"删除才产生"的新损失——它已是当前生产既成事实。** taptap 接口在阿里云机房 IP 被 405 封,`taptap.search_by_name` 每晚每次调用都走进 [`ingest_service.py:181-184`](../../../server/app/modules/game_library/ingest_service.py#L181-L184) 的 `except → per_source="error"`,从未真正返回过 hit / 截图。所以删除**不会让生产变差,只是清掉已名存实亡的死代码,并省掉每晚那个必然失败的网络请求**。库里截图现在本就只能靠 seed 路径 `run_ingest_once` 的 baidu `search()`（CLI 手动)补——删除前后一致。审核确认这个理解无误即可批准彻底删除；如未来接入 taptap 代理再另议恢复。本计划按**彻底删除**写。
2. **既有 DB 行的 `source_order`。** 生产/dev 的 `game_ingest_config.source_order` 现值 `"taptap,baidu"`。摘除后 `_collect_from_all_sources` 的 `srcs.get("taptap")` 返回 None → `continue`,**能优雅跳过不崩**。Task 5 仍加一条迁移把存量行规整为 `"baidu"`；审核确认是否需要这条数据迁移,还是靠优雅跳过即可。
3. **上线属性:** 本改动含 **Alembic 迁移**（Task 5）,按团队约定属"非例行",发版前需人确认,不走全自动合并部署。

---

## File Inventory（改动面全清单）

**删除:**
- `server/app/modules/game_library/sources/taptap.py`（整文件）

**修改（运行时接线）:**
- `server/app/modules/game_library/registry.py` — 去 `taptap` import、`SOURCES` 去 `SOURCE_TAPTAP`、删 `_PAGE_SIZE_CAP`（taptap 专用）
- `server/app/modules/game_library/ingest_service.py` — `_collect_from_all_sources` 去 taptap import、`srcs` 去 taptap、删 `get_detail` 特判块（L188-192）
- `server/app/modules/game_library/scheduler.py` — 去 `taptap` import、删 `_TAPTAP_DETAIL_THROTTLE_SECONDS`、`SEED_TARGETS` 去 taptap 两条、`run_ingest_once` 删 taptap `get_detail` 特判块（L105-109）
- `server/app/modules/game_library/types.py` — 删 `SOURCE_TAPTAP`、`PLATFORM_PC`（删后无人引用）；**改 L33 `android_package` 字段的注释**（去掉"仅 taptap 提供"措辞）
- `server/app/modules/game_library/sources/baidu.py` — **改 L16 docstring 注释**（去掉"不像 taptap 的 identifier/itunes_id"措辞）——保留爬虫本身,只清残留 taptap 文字
- `server/app/modules/game_library/models.py` — `source_order` server_default `"taptap,baidu"` → `"baidu"`
- `server/scripts/ingest_games.py` — `--source` choices 去 taptap

**修改（配置/前端/迁移）:**
- 新增 `server/alembic/versions/0071_game_ingest_source_order_baidu_only.py`
- `web/src/features/game-library/GameIngestSettingsModal.tsx` — placeholder `如 taptap,baidu` → `如 baidu`

**修改（测试）:**
- `server/tests/test_game_sources.py` — 删 5 个 taptap 测试 + 2 个 taptap 专用 helper + 顶部 import
- `server/tests/test_game_scheduler.py` — 删 taptap detail 节流测试、SEED_TARGETS 断言随之调整
- `server/tests/test_game_cull_and_create.py` — 删 taptap-only 测试、其余 per_source 替身改用 baidu/第二替身源
- `server/tests/test_game_ingest_cli.py` — `--source taptap` 改 baidu
- `server/tests/test_game_ingest_config.py` — 默认 `source_order` 断言 `"taptap,baidu"` → `"baidu"`
- `server/tests/test_game_ingest_batch.py` — fixture `source_order="taptap"` → `"baidu"`
- `server/tests/test_game_upsert.py` — `_game("taptap", ...)` 源标签改 `"baidu"`（仅一致性,功能无关）

**修改（文档）:**
- `CLAUDE.md` — `game_library/` 段落去 taptap 爬虫描述（保留 baidu）
- 历史设计稿 `docs/superpowers/specs/2026-07-*game-library*` **不改**（按日期存档的既成记录）

---

### Task 1: 从 registry 摘除 taptap

**Files:**
- Modify: `server/app/modules/game_library/registry.py`
- Test: `server/tests/test_game_sources.py::test_registry_collect_pool_paginates`（既有,回归）

**Interfaces:**
- Produces: `registry.SOURCES == {"baidu": baidu}`；`registry.search` / `collect_pool` 签名不变。

- [ ] **Step 1: 改 registry.py**

把 [`registry.py:6-12`](../../../server/app/modules/game_library/registry.py#L6-L12) 改为：

```python
from . import types
from .sources import baidu

logger = logging.getLogger(__name__)

SOURCES = {types.SOURCE_BAIDU: baidu}
```

删除 `_PAGE_SIZE_CAP`（原 L12,taptap 20 上限专用）。`collect_pool` 里 L23 `cap = page_size_cap if page_size_cap is not None else _PAGE_SIZE_CAP.get(source, pool_size)` 改为：

```python
    cap = page_size_cap if page_size_cap is not None else pool_size
```

L22 docstring `"""按 pool_size 翻页收集(taptap 受 20 上限约束)。数据源枯竭即停。"""` 改为 `"""按 pool_size 翻页收集(baidu 无单页上限)。数据源枯竭即停。"""`。

- [ ] **Step 2: 跑 registry 相关测试**

Run: `python -m pytest server/tests/test_game_sources.py -q -k collect_pool`
Expected: PASS（3 个 collect_pool 用例,都用 baidu / 显式 page_size_cap,不依赖 taptap）

- [ ] **Step 3: ruff**

Run: `ruff check server/app/modules/game_library/registry.py`
Expected: 无 F401（未用 import）等报错

- [ ] **Step 4: Commit**

```bash
git add server/app/modules/game_library/registry.py
git commit -m "refactor(game-library): drop taptap from source registry"
```

---

### Task 2: 从 ingest_service 摘除 taptap

**Files:**
- Modify: `server/app/modules/game_library/ingest_service.py:163-195`
- Test: `server/tests/test_game_cull_and_create.py`

**Interfaces:**
- Consumes: `registry.SOURCES`（Task 1 已只剩 baidu）
- Produces: `_collect_from_all_sources(source_order, name)` 只查 baidu；返回结构不变 `{"hits": [...], "per_source": {...}}`。

- [ ] **Step 1: 改 `_collect_from_all_sources`**

[`ingest_service.py:170`](../../../server/app/modules/game_library/ingest_service.py#L170) `from server.app.modules.game_library.sources import baidu, taptap` → `from server.app.modules.game_library.sources import baidu`

L172 `srcs = {"baidu": baidu, "taptap": taptap}` → `srcs = {"baidu": baidu}`

删除 L188-192 的 taptap 补 `get_detail` 特判块：

```python
        if key == "taptap" and not hit.screenshot_urls:
            try:
                hit = taptap.get_detail(hit.game_id)
            except Exception:
                logger.warning("taptap get_detail failed name=%s", name, exc_info=True)
```

L168 docstring 里 `taptap 命中不带截图 → 补 get_detail。` 一句删掉。

- [ ] **Step 2: 改 test_game_cull_and_create.py**

删除 taptap-only 测试 `test_collect_taptap_hit_without_shots_calls_get_detail`（约 L95-111）。
其余测试里把 `source_order="taptap,baidu"` 改为 `"baidu"`、`per_source` 里 `"taptap"` 键改 `"baidu"`。逐处对照 `taptap` 出现点：L13/69/73/75/80/82/84/88/90/92/97/100/110/111/134/180-185/224-232/268-276/311-319/357-365。

关键约束（决定唯一可行改法）：`_collect_from_all_sources`（[`ingest_service.py:170-172`](../../../server/app/modules/game_library/ingest_service.py#L170-L172)）的 `srcs` 字典是**函数体内直接 `from ...sources import baidu` 硬编码构造的**,**不读 `registry.SOURCES`**。因此:
- monkeypatch `registry.SOURCES` 或引入替身源名（如 `"fake"`）**无效**——传进 `source_order` 只会被 `srcs.get("fake") is None → continue` 静默跳过、`per_source` 里根本不出现该键。**不要走这条路。**
- **唯一可行方案:** 缩到单一 baidu 源后,"两个不同真实源同时给出不同结果（一 hit 一 miss / 一 error 一 miss）"这种覆盖场景**本质无法再复现**。把这类断言拆成**多次单源 baidu 调用**分别验证 hit / miss / error 三态（每次 monkeypatch `baidu.search_by_name` 返回命中 / None / 抛异常）,不再在一个断言里塞两个源。`test_collect_from_all_sources_union_and_error_vs_miss`（L67-92）按此拆成两个单源用例。

- [ ] **Step 3: 跑测试**

Run: `python -m pytest server/tests/test_game_cull_and_create.py -q`
Expected: PASS（无 DB 的纯逻辑用例;需 DB 的用例在无 `GEO_TEST_DATABASE_URL` 时跳过）

- [ ] **Step 4: ruff**

Run: `ruff check server/app/modules/game_library/ingest_service.py`
Expected: 无报错

- [ ] **Step 5: Commit**

```bash
git add server/app/modules/game_library/ingest_service.py server/tests/test_game_cull_and_create.py
git commit -m "refactor(game-library): drop taptap from managed refresh collector"
```

---

### Task 3: 从 scheduler 摘除 taptap（含 seed 种子）

**Files:**
- Modify: `server/app/modules/game_library/scheduler.py`
- Test: `server/tests/test_game_scheduler.py`

**Interfaces:**
- Produces: `SEED_TARGETS` 只含 baidu 条目；`run_ingest_once` 不再对 taptap 特判。

- [ ] **Step 1: 改 scheduler.py**

删除 [`scheduler.py:33`](../../../server/app/modules/game_library/scheduler.py#L33) `from server.app.modules.game_library.sources import taptap`。
删除 L41 `_TAPTAP_DETAIL_THROTTLE_SECONDS = 0.3`。
`SEED_TARGETS`（L51-55）删掉两条 taptap 条目,只留 baidu：

```python
SEED_TARGETS = [
    {"source": "baidu", "category": "经营", "max_games": 30, "max_shots": 6},
]
```

删除 `run_ingest_once` 里 L104-109 的 taptap 特判块：

```python
                try:
                    if source == "taptap" and not game.screenshot_urls:
                        if detail_requests > 0:
                            time.sleep(_TAPTAP_DETAIL_THROTTLE_SECONDS)
                        detail_requests += 1
                        game = taptap.get_detail(game.game_id)
                    service.upsert_game(db, game, max_screenshots=max_shots)
```

改为（去掉 taptap 分支,保留 upsert）：

```python
                try:
                    service.upsert_game(db, game, max_screenshots=max_shots)
```

同时删除该函数体内已不再使用的 `detail_requests = 0`（约 L97）。`import time` **保留**（`_run_configured_batch` L442 仍用 `time.sleep`）。

- [ ] **Step 2: 改 test_game_scheduler.py**

删除 `test_run_ingest_once_throttles_consecutive_taptap_detail_requests`（L33-约60）。
若 L70 `assert all("pages" not in target for target in scheduler.SEED_TARGETS)` 仍成立（baidu 条目也无 "pages" 键）则保留;新增/调整一条断言 `SEED_TARGETS` 全部 `source == "baidu"`：

```python
def test_seed_targets_are_baidu_only():
    from server.app.modules.game_library import scheduler
    assert scheduler.SEED_TARGETS
    assert all(t["source"] == "baidu" for t in scheduler.SEED_TARGETS)
```

- [ ] **Step 3: 跑测试**

Run: `python -m pytest server/tests/test_game_scheduler.py -q`
Expected: PASS

- [ ] **Step 4: ruff**

Run: `ruff check server/app/modules/game_library/scheduler.py`
Expected: 无 F401/F811

- [ ] **Step 5: Commit**

```bash
git add server/app/modules/game_library/scheduler.py server/tests/test_game_scheduler.py
git commit -m "refactor(game-library): drop taptap seed target and detail throttle"
```

---

### Task 4: CLI 只保留 baidu

**Files:**
- Modify: `server/scripts/ingest_games.py:13`
- Test: `server/tests/test_game_ingest_cli.py`

- [ ] **Step 1: 改 CLI**

L1 docstring 示例 `--source taptap` → `--source baidu`。
L13 `parser.add_argument("--source", required=True, choices=["taptap", "baidu"])` → `choices=["baidu"]`。

- [ ] **Step 2: 改 test_game_ingest_cli.py**

把三处 `--source taptap`（L16/33/48）改为 `--source baidu`；L18 断言 `captured["targets"][0]["source"] == "taptap"` 改 `"baidu"`。

- [ ] **Step 3: 跑测试**

Run: `python -m pytest server/tests/test_game_ingest_cli.py -q`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add server/scripts/ingest_games.py server/tests/test_game_ingest_cli.py
git commit -m "refactor(game-library): restrict ingest CLI to baidu source"
```

---

### Task 5: source_order 默认值改 baidu（模型 + 迁移 + 前端）

**Files:**
- Modify: `server/app/modules/game_library/models.py:90-92`
- Create: `server/alembic/versions/0071_game_ingest_source_order_baidu_only.py`
- Modify: `web/src/features/game-library/GameIngestSettingsModal.tsx:214`
- Test: `server/tests/test_game_ingest_config.py:20`、`server/tests/test_game_ingest_batch.py`

**Interfaces:**
- Consumes: 现有 `game_ingest_config` 单例表（id=1）。
- Produces: 新行 server_default `"baidu"`；存量行 `"taptap,baidu"` → `"baidu"`。

- [ ] **Step 1: 改 models.py server_default**

[`models.py:90-92`](../../../server/app/modules/game_library/models.py#L90-L92)：

```python
    source_order: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default="baidu"
    )
```

- [ ] **Step 2: 写迁移 0071**

先确认当前 head：`alembic heads`（应为 `0070`）。新建 `server/alembic/versions/0071_game_ingest_source_order_baidu_only.py`：

```python
"""game_ingest_config.source_order 默认改 baidu-only + 规整存量行

Revision ID: 0071_game_ingest_source_order_baidu_only
Revises: 0070_prompt_template_platform
"""
from alembic import op
import sqlalchemy as sa

revision = "0071_game_ingest_source_order_baidu_only"
down_revision = "0070_prompt_template_platform"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "game_ingest_config", "source_order",
        existing_type=sa.String(length=50), nullable=False,
        server_default="baidu",
    )
    op.execute(
        "UPDATE game_ingest_config SET source_order='baidu' "
        "WHERE source_order='taptap,baidu'"
    )


def downgrade() -> None:
    op.alter_column(
        "game_ingest_config", "source_order",
        existing_type=sa.String(length=50), nullable=False,
        server_default="taptap,baidu",
    )
    op.execute(
        "UPDATE game_ingest_config SET source_order='taptap,baidu' "
        "WHERE source_order='baidu'"
    )
```

> 校对 `down_revision` 与实际 head 一致（0070 的 revision id 见其文件头）。

- [ ] **Step 3: 前端 placeholder**

[`GameIngestSettingsModal.tsx:214`](../../../web/src/features/game-library/GameIngestSettingsModal.tsx#L214) `placeholder="如 taptap,baidu"` → `placeholder="如 baidu"`。

- [ ] **Step 4: 改测试**

`test_game_ingest_config.py:20` `assert cfg.source_order == "taptap,baidu"` → `"baidu"`。
`test_game_ingest_batch.py` L26/89 `source_order = "taptap"` → `"baidu"`（这两处只是给 fake refresh 传参占位,改 baidu 保持语义一致）。

- [ ] **Step 5: 迁移可加载 + 前端门禁**

Run: `python -c "import server.alembic.versions.0071_game_ingest_source_order_baidu_only as m; print(m.revision, m.down_revision)"`（若模块名带数字前缀不便 import,改用 `alembic history | head` 确认链接不断）
Run: `pnpm --filter @geo/web typecheck`
Expected: 迁移链接不断;typecheck 通过

- [ ] **Step 6: 跑配置测试**

Run: `python -m pytest server/tests/test_game_ingest_config.py server/tests/test_game_ingest_batch.py -q`
Expected: PASS（需 DB 的用例无 `GEO_TEST_DATABASE_URL` 时跳过）

- [ ] **Step 7: Commit**

```bash
git add server/app/modules/game_library/models.py server/alembic/versions/0071_game_ingest_source_order_baidu_only.py web/src/features/game-library/GameIngestSettingsModal.tsx server/tests/test_game_ingest_config.py server/tests/test_game_ingest_batch.py
git commit -m "refactor(game-library): default source_order to baidu-only + migration"
```

---

### Task 6: 删除爬虫文件 + 清理测试与常量

**Files:**
- Delete: `server/app/modules/game_library/sources/taptap.py`
- Modify: `server/tests/test_game_sources.py`
- Modify: `server/tests/test_game_upsert.py`
- Modify: `server/app/modules/game_library/types.py:11,18`

**Interfaces:**
- Produces: `game_library` 内无任何 `taptap` import；`grep -rn taptap server/app/modules/game_library` 空。

- [ ] **Step 1: 删测试 test_game_sources.py 的 taptap 部分**

顶部 L5 `from server.app.modules.game_library.sources import baidu, taptap` → `from server.app.modules.game_library.sources import baidu`。
删除这些 taptap-only 测试函数与 helper（保留 `_FakeResponse`,baidu 用例仍需）：
- `test_taptap_by_tag_has_placeholder_tag_and_no_screenshots`（L43-54）
- `test_taptap_get_detail_reads_data_app_mapping`（L99-133）
- `_fake_xsrf_cookie`（L183-202）
- `_fake_taptap_opener_factory`（L205-221）
- `test_taptap_search_by_name_maps_brand_hit`（L224-266）
- `test_taptap_search_by_name_ignores_non_brand_and_title_mismatch`（L269-291）
- `test_taptap_search_by_name_missing_xsrf_cookie_raises`（L294-306）

删除顶部 `import http.cookiejar`（L1,删 `_fake_xsrf_cookie` 后无人用）。保留 baidu / registry collect_pool 全部用例。

- [ ] **Step 2: test_game_upsert.py 源标签改 baidu**

把 `_game("taptap", ...)` / `source="taptap"`（L61/196/276/287/314 等）改为 `"baidu"`。此文件测 `upsert_game` 并集合并,源名仅作 `sources` 数组标签,功能无关,改 baidu 保持一致。

- [ ] **Step 3: 删除爬虫文件**

```bash
git rm server/app/modules/game_library/sources/taptap.py
```

- [ ] **Step 4: 清 types.py 未用常量 + 残留注释**

[`types.py:11`](../../../server/app/modules/game_library/types.py#L11) 删 `SOURCE_TAPTAP = "taptap"`；L18 删 `PLATFORM_PC = "pc"`（删 taptap 后全库无引用——已 grep 确认仅 `sources/taptap.py:69,193` 使用,`registry.py` 只用 `SOURCE_TAPTAP` 不用 `PLATFORM_PC`,均在本计划中移除）。保留 `SOURCE_BAIDU` / `PLATFORM_ANDROID` / `PLATFORM_IOS`（baidu 仍用）。
把 [`types.py:33`](../../../server/app/modules/game_library/types.py#L33) 的 `android_package` 字段注释 `# 仅 taptap 提供(identifier 字段);baidu 响应无此字段,恒为 None` 改为 `# baidu 响应无此字段,恒为 None`（字段本身保留,备未来源用）。

- [ ] **Step 4b: 清 baidu.py 残留 taptap 注释**

把 [`sources/baidu.py:16`](../../../server/app/modules/game_library/sources/baidu.py#L16) 的 `- 该接口响应里没有 Android 包名/iOS ID 字段(不像 taptap 的 identifier/itunes_id),` 改为不提 taptap 的措辞,如 `- 该接口响应里没有 Android 包名/iOS ID 字段,`（下一行 `Game.android_package 对 baidu 结果恒为 None。` 保留）。**只改这一句注释,baidu 爬虫代码不动。**

- [ ] **Step 5: 全量核验无残留**

Run: `grep -rn "taptap" server/app/modules/game_library server/scripts/ingest_games.py`
Expected: 空输出（零命中）
Run: `ruff check server/app/modules/game_library server/tests/test_game_sources.py server/tests/test_game_upsert.py`
Expected: 无 F401/F811

- [ ] **Step 6: 跑 game 全套测试**

Run: `python -m pytest server/tests/test_game_sources.py server/tests/test_game_upsert.py server/tests/test_game_scheduler.py server/tests/test_game_cull_and_create.py server/tests/test_game_ingest_batch.py server/tests/test_game_ingest_cli.py server/tests/test_game_ingest_config.py -q`
Expected: PASS（无 DB 的绿,需 DB 用例在无 `GEO_TEST_DATABASE_URL` 时跳过）

- [ ] **Step 7: 冒烟 import 整个模块**

Run: `python -c "import server.app.modules.game_library.registry, server.app.modules.game_library.ingest_service, server.app.modules.game_library.scheduler; print('ok')"`
Expected: `ok`（无 ImportError）

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor(game-library): remove taptap crawler source and its tests"
```

---

### Task 7: 文档

**Files:**
- Modify: `CLAUDE.md`（`game_library/` 段落）

- [ ] **Step 1: 更新 CLAUDE.md**

`game_library/` 段落里凡描述"taptap 命中补 get_detail""`source_order` 默认 taptap,baidu""复用 taptap 截图"等 taptap 爬虫事实的措辞,改为只剩 baidu 的现状(如"按 `source_order` 走 `sources/baidu.search_by_name` 精确刷新"、去掉 taptap `get_detail` 补图那句)。**不改** `tasks/` 段的 TapTap 发布驱动描述。历史 `docs/superpowers/specs|plans/2026-07-*game-library*` 存档稿不动。

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: drop taptap crawler from game-library notes"
```

---

## 收尾验证（全部 Task 后）

- [ ] `grep -rn "taptap" server/app/modules/game_library` → 空
- [ ] `ruff check server/` → 绿
- [ ] `python -m pytest server/tests/test_game_*.py -q` → 绿/跳过,无 error/fail
- [ ] `pnpm --filter @geo/web typecheck && pnpm --filter @geo/web build` → 绿
- [ ] （部署时,非本计划）`alembic upgrade head` 跑 0071;确认 `game_ingest_config.source_order` 变 `baidu`

---

## Self-Review

**Spec coverage:** 每个 taptap 爬虫引用点都有对应 Task：registry(T1)、ingest_service(T2)、scheduler+seed(T3)、CLI(T4)、source_order 默认+迁移+前端(T5)、文件删除+常量+源测试(T6)、文档(T7)。发布驱动全部列入"严禁改动"。

**Placeholder scan:** 无 TODO/TBD；每处改动给了确切文件行与前后代码。唯一"实现者判断"处是 T2 里 `test_game_cull_and_create.py` 的替身源改造——因该文件用 taptap 当"第二个源"验证 hit/miss/error 区分,机械替换会与"只剩 baidu 单源"语义冲突,故要求实现者按测试原意最小改（改单源断言或引入纯替身名 + monkeypatch registry），并给了根因（`srcs.get(key) is None → continue`）。

**Type consistency:** 源标识串全程小写 `"baidu"`；`SOURCES`/`_collect_from_all_sources` 返回结构不变；迁移 `down_revision` 要求执行者对齐真实 head（0070）。
