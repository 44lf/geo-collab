# 游戏库语料底座 (Game Library Corpus) 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把「图片库」升级成以游戏为一等公民的取材语料库——新增 `games`/`game_tags` 表 + `stock_images` 扩列，折叠 game-search 爬包做内置定时入库，加 2 个检索 MCP 工具，让 `/goal` 生文按标签从预灌真实游戏库取材、用真实截图配图，并按用量做取材/配图均衡。

**Architecture:** 新模块 `server/app/modules/game_library/`（爬取 sources + service + scheduler + router），复用现有 `image_library`（StockCategory/StockImage/MinIO）当媒体底座。`games` 按 `name_normalized` 跨源合并（并集）；截图靠 `UNIQUE(category_id, source_url_hash)` 去重；用量双粒度（游戏级在 `games`、图片级在 `stock_images`），软 LRU + `last_used_at` 时间窗做短期去重。入库＝`create_app()` 内后台守护线程（复用 `sync_scheduler` 模式）+ 手动 CLI。

**Tech Stack:** FastAPI + SQLAlchemy 2.0 (Mapped) + Alembic (MySQL only, `mysql+pymysql`) + FastMCP (stdio + HTTP mount) + MinIO + pytest (`@pytest.mark.mysql`)。爬取纯 stdlib `urllib`（无新依赖、无登录态）。

## Global Constraints

以下为全局约束，**每个 task 都隐含适用**（值从定稿 spec `docs/superpowers/specs/2026-07-17-game-library-corpus-design.md` 逐字抄来）：

- **MySQL only**：无 SQLite 兼容。DB tests 需 `@pytest.mark.mysql` + `GEO_TEST_DATABASE_URL`（scheme `mysql+pymysql://`、库名含 `"test"`）。
- **service 层抛命名异常**（`ClientError`/`ConflictError`/`ValidationError`/`AccountError`），**不抛裸 `ValueError`**（无全局兜底）。
- **MCP 端点**：走独立 sub-router，标 `dependencies=[Depends(require_mcp_token)]`，**不复用 user JWT**；未捕获异常用 `core/mcp_errors.mcp_exception_response(exc, context=...)`。
- **文章正文三份并行结构**（`content_json`/`content_html`/`plain_text`）改一份要同步——本计划不改正文文本，只读 `content_json` 取 `stockImageId`。
- **`name_normalized` 归一化复用** `server/app/modules/articles/formatting/document.py:59` 的 `_normalize_game_name`，**不另造**。
- **跨源并集合并**：集合字段（tags/screenshots/platforms/sources）取并集去重；标量 `score`/`comment_count` 取 `max`、`description`/`icon_url` 取更长/非空。
- **用量列不由入库触碰**：`use_count`/`last_used_*` 只在生文消费时回写（Task 10/11），入库/刷新只管语料+截图。
- **敏感/长文本无关**：本功能不引入加密列。
- **迁移不写死版本号**；当前 head = `0064_qref_external_ingestion`，新迁移接在其后。
- **MCP 工具总数真值**在 `server/app/modules/mcp_catalog/connect_router.py:27` `MCP_TOOLS_COUNT`（本计划 33→35）。
- **跨仓**：writer 是 geo-goal 插件 skill、**不在本仓**；本计划只做后端 + MCP，writer 契约改由 Task 12 记为 handoff。
- **测试命令**（全计划统一）：`GEO_TEST_DATABASE_URL=mysql+pymysql://geo_user:password@127.0.0.1:3306/geo_test pytest <path> -q`（引擎/账号按本机 `.env` 调整；conda 环境 `geo_xzpt`，工具 shell 里 conda activate 不生效，用 `env python -m pytest` 或全路径 python）。
- **提交**：每 task 末尾一次 commit，消息尾行加 `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`；分支 `feat/game-library-corpus`（已存在，勿新建）。

---

## File Structure

**新增：**
- `server/app/modules/game_library/__init__.py` — 模块导出
- `server/app/modules/game_library/types.py` — `Game` dataclass + 常量（折叠自 game-search）
- `server/app/modules/game_library/sources/__init__.py`
- `server/app/modules/game_library/sources/baidu.py` — 百度乐玩爬取（折叠）
- `server/app/modules/game_library/sources/taptap.py` — TapTap 爬取 + `get_detail`（折叠）
- `server/app/modules/game_library/registry.py` — `SOURCES` + `search` + `collect_pool`
- `server/app/modules/game_library/models.py` — `Game` / `GameTag` ORM
- `server/app/modules/game_library/schemas.py` — 检索 Pydantic
- `server/app/modules/game_library/service.py` — `upsert_game` / `query_games_by_tags` / `list_game_tags` / `bump_game_usage`
- `server/app/modules/game_library/scheduler.py` — `run_ingest_once` / `start_game_ingest` / `stop_game_ingest`
- `server/app/modules/game_library/router.py` — MCP-token 只读检索端点
- `server/app/shared/image_download.py` — 通用截图下载器（限体积/类型/重定向）
- `server/scripts/ingest_games.py` — 手动入库 CLI
- `server/alembic/versions/0065_game_library.py` — 迁移
- `server/tests/test_game_sources.py` / `test_game_library_models.py` / `test_game_upsert.py` / `test_game_query.py` / `test_game_scheduler.py` / `test_game_ingest_cli.py` / `test_game_mcp.py` / `test_game_usage_writeback.py` / `test_image_download.py`

**修改：**
- `server/app/modules/image_library/models.py:37-45` — `StockImage` 加 5 列
- `server/app/modules/image_library/service.py:88` — `store_image_bytes` 写 hash + 加 no-commit 变体 + hash 去重；加 `bump_stock_image_usage`
- `server/app/modules/image_library/selector.py:47-63` — `pick_image_id` 改软 LRU
- `server/app/modules/articles/hook.py` — `insert_images_for_article` 回写图片用量
- `server/app/modules/articles/ai_illustrate_svc.py:121-233` — `illustrate_one` 回写图片用量
- `server/app/modules/articles/routers/mcp.py:194-301` — `SaveArticleFromMcpPayload` + `save_article_from_mcp` 加 `selected_games` + bump 游戏用量
- `server/app/core/config.py:113` — 3 个 `GEO_GAME_INGEST_*` 设置
- `server/app/main.py:488` — 启动 `start_game_ingest` + mount `game_library.router` + import models
- `server/mcp/tools/catalog.py` — 加 2 个工具
- `server/app/modules/mcp_catalog/connect_router.py:27` — `MCP_TOOLS_COUNT` 35
- `server/tests/test_mcp_status_count.py:17` + `server/tests/test_mcp_tools_registration.py:22` — 断言 31→35
- `CLAUDE.md` — 后台线程/Domain Modules/MCP catalog 三处

---

## Task 1: 折叠 game-search 爬包 → `game_library.{types,sources,registry}`

**Files:**
- Create: `server/app/modules/game_library/__init__.py`, `types.py`, `sources/__init__.py`, `sources/baidu.py`, `sources/taptap.py`, `registry.py`
- Source: 逐文件复制自 `E:\1\game-search\scripts\game_search\`（已解压的真实爬包），改相对 import
- Test: `server/tests/test_game_sources.py`

**Interfaces:**
- Produces: `types.Game`（dataclass: `source, game_id, name, score, tags, platforms, comment_count, icon_url, screenshot_urls, android_package, description, raw`）；`sources.baidu.search(category,*,platform,order,page,page_size)->list[Game]`；`sources.taptap.search(...)->list[Game]` + `sources.taptap.get_detail(game_id)->Game`；`registry.search(source,category,**kw)->list[Game]`；`registry.collect_pool(source,category,pool_size,**kw)->list[Game]`；常量 `SOURCE_BAIDU="baidu"`/`SOURCE_TAPTAP="taptap"`。

- [ ] **Step 1: 写失败测试**（用固定 fixture dict → Game，不出网）

```python
# server/tests/test_game_sources.py
from server.app.modules.game_library.sources import baidu, taptap
from server.app.modules.game_library import registry


def test_baidu_to_game_maps_screenshots_and_score():
    raw = {
        "gameId": 123, "gameName": "餐厅养成记", "gameScore": "8.7",
        "gameTags": ["经营", "养成"], "gamePlatform": ["Android", "IOS"],
        "commentCount": 42, "gameIcon": "http://x/icon.png",
        "gameOfficialPic": ["http://x/1.jpg", "http://x/2.jpg"],
        "gameDesc": "开一家餐厅",
    }
    g = baidu._to_game(raw)
    assert g.source == "baidu" and g.game_id == "123" and g.name == "餐厅养成记"
    assert g.score == 8.7 and g.comment_count == 42
    assert g.screenshot_urls == ["http://x/1.jpg", "http://x/2.jpg"]
    assert g.tags == ["经营", "养成"]


def test_taptap_by_tag_has_placeholder_tag_and_no_screenshots():
    raw = {"id": 45213, "title": "心动小镇",
           "stat": {"rating": {"score": 9.2}, "review_count": 10},
           "icon": {"original_url": "http://x/i.png"}, "identifier": "com.x.y"}
    g = taptap._to_game(raw, "养成")
    assert g.tags == ["养成"]          # 占位=查询分类名
    assert g.screenshot_urls == []      # 列表接口无截图
    assert g.android_package == "com.x.y"


def test_registry_collect_pool_paginates(monkeypatch):
    calls = []
    def fake_search(source, category, *, page=1, page_size=20, **kw):
        calls.append((page, page_size))
        return [] if page > 2 else [object()] * page_size
    monkeypatch.setattr(registry, "search", fake_search)
    pool = registry.collect_pool("baidu", "养成", 30, page_size_cap=20)
    assert len(pool) == 30 and calls[0] == (1, 20)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `env python -m pytest server/tests/test_game_sources.py -q`
Expected: FAIL（`ModuleNotFoundError: server.app.modules.game_library`）

- [ ] **Step 3: 折叠爬包源文件**

复制这四个文件（内容与 `E:\1\game-search\scripts\game_search\` 逐字一致，仅改 import 前缀）：
- `types.py` ← 原 `types.py`（`Game` dataclass + 常量，无改动）。
- `sources/baidu.py` ← 原 `sources/baidu.py`，把顶部 `from ..types import ...` 保持不变（相对 import 在新包下同样成立）。
- `sources/taptap.py` ← 原 `sources/taptap.py`（**含 `get_detail` / `search_by_name`**，保留；`search()` 与 `get_detail()` 是入库要用的）。
- `sources/__init__.py` ← 空文件。

- [ ] **Step 4: 写精简 `registry.py`**（只留入库要用的 `search` + `collect_pool`；丢 `recommend`/`random_pick`/`search_with_fallback`——那是旧「生文时推荐」用途，YAGNI）

```python
# server/app/modules/game_library/registry.py
"""数据源注册表：入库只需 search + 分页 collect_pool。"""
from . import types
from .sources import baidu, taptap

SOURCES = {types.SOURCE_BAIDU: baidu, types.SOURCE_TAPTAP: taptap}
# taptap by-tag 服务端 limit 硬上限 20；百度无此限
_PAGE_SIZE_CAP = {types.SOURCE_TAPTAP: 20}


def search(source, category, **kwargs):
    if source not in SOURCES:
        raise ValueError(f"未知数据源: {source!r}，可选: {list(SOURCES)}")
    return SOURCES[source].search(category, **kwargs)


def collect_pool(source, category, pool_size, *, page_size_cap=None, **kwargs):
    """按 pool_size 翻页收集（taptap 受 20 上限约束）。数据源枯竭即停。"""
    cap = page_size_cap or _PAGE_SIZE_CAP.get(source, pool_size)
    games, page = [], 1
    while len(games) < pool_size:
        size = min(cap, pool_size - len(games))
        batch = search(source, category, page=page, page_size=size, **kwargs)
        if not batch:
            break
        games.extend(batch)
        if len(batch) < size:
            break
        page += 1
    return games
```

`__init__.py`：
```python
# server/app/modules/game_library/__init__.py
from . import types, registry  # noqa: F401
```

- [ ] **Step 5: 跑测试确认通过**

Run: `env python -m pytest server/tests/test_game_sources.py -q`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add server/app/modules/game_library/ server/tests/test_game_sources.py
git commit -m "feat(game-library): 折叠 game-search 爬包 (types/sources/registry)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: DB 层 — `Game`/`GameTag` 模型 + `StockImage` 扩列 + 迁移 0065

**Files:**
- Create: `server/app/modules/game_library/models.py`, `server/alembic/versions/0065_game_library.py`, `server/tests/test_game_library_models.py`
- Modify: `server/app/modules/image_library/models.py:45`（`StockImage` 尾部加 5 列）, `server/app/main.py`（import models 触发建表登记）

**Interfaces:**
- Produces: ORM `Game`（`__tablename__="games"`, `name_normalized` UNIQUE, `use_count`/`last_used_article_id`/`last_used_at`, `stock_category_id` FK SET NULL, `sources`/`screenshot_urls`/`highlight_comments`/`related_hotspots` JSON, `tags` relationship→GameTag cascade）；`GameTag`（`game_id` FK CASCADE, `tag`, `axis`, UNIQUE(game_id,tag)）；`StockImage` 新增 `source_url`/`source_url_hash`/`use_count`/`last_used_at`/`last_used_article_id`。迁移 revision `0065_game_library`（down `0064_qref_external_ingestion`）。

- [ ] **Step 1: 写失败测试**（mysql）

```python
# server/tests/test_game_library_models.py
import pytest


@pytest.mark.mysql
def test_game_and_tags_roundtrip(monkeypatch):
    from server.tests.utils import build_test_app
    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.game_library.models import Game, GameTag
        s = app.session_factory()
        try:
            g = Game(name="餐厅养成记", name_normalized="餐厅养成记",
                     sources=[{"source": "baidu", "source_game_id": "1"}], score=8.7,
                     use_count=0)
            s.add(g); s.flush()
            s.add(GameTag(game_id=g.id, tag="经营")); s.commit()
            assert g.id is not None and g.use_count == 0
            got = s.get(Game, g.id)
            assert got.name_normalized == "餐厅养成记"
        finally:
            s.close()
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_stock_image_has_usage_and_hash_columns(monkeypatch):
    from server.tests.utils import build_test_app
    app = build_test_app(monkeypatch)
    try:
        from sqlalchemy import inspect
        cols = {c["name"] for c in inspect(app.engine).get_columns("stock_images")}
        assert {"source_url", "source_url_hash", "use_count",
                "last_used_at", "last_used_article_id"} <= cols
    finally:
        app.cleanup()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `GEO_TEST_DATABASE_URL=mysql+pymysql://geo_user:password@127.0.0.1:3306/geo_test env python -m pytest server/tests/test_game_library_models.py -q`
Expected: FAIL（`ModuleNotFoundError` / 缺列）

- [ ] **Step 3: 写 `Game`/`GameTag` 模型**

```python
# server/app/modules/game_library/models.py
"""游戏库 ORM：games（跨源合并的游戏户口本）+ game_tags（归一化标签）。"""
from datetime import datetime

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Integer,
                        String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.app.core.time import utcnow
from server.app.db.base import Base


class Game(Base):
    __tablename__ = "games"
    __table_args__ = (UniqueConstraint("name_normalized", name="uq_games_name_normalized"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    name_normalized: Mapped[str] = mapped_column(String(200), nullable=False)
    sources: Mapped[list | None] = mapped_column(JSON, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    comment_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    platforms: Mapped[list | None] = mapped_column(JSON, nullable=True)
    icon_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    screenshot_urls: Mapped[list | None] = mapped_column(JSON, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    stock_category_id: Mapped[int | None] = mapped_column(
        ForeignKey("stock_categories.id", ondelete="SET NULL"), nullable=True)
    highlight_comments: Mapped[list | None] = mapped_column(JSON, nullable=True)
    related_hotspots: Mapped[list | None] = mapped_column(JSON, nullable=True)
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_used_article_id: Mapped[int | None] = mapped_column(
        ForeignKey("articles.id", ondelete="SET NULL"), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True,
                                            server_default="1")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    tags = relationship("GameTag", back_populates="game", cascade="all, delete-orphan")


class GameTag(Base):
    __tablename__ = "game_tags"
    __table_args__ = (UniqueConstraint("game_id", "tag", name="uq_game_tags_game_tag"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"), index=True)
    tag: Mapped[str] = mapped_column(String(100), index=True)
    axis: Mapped[str | None] = mapped_column(String(20), nullable=True)

    game = relationship("Game", back_populates="tags")
```

- [ ] **Step 4: `StockImage` 加 5 列**（`server/app/modules/image_library/models.py`，在 `created_at`（第 45 行）后追加）

```python
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # ── 游戏库扩展：入库去重 + 图片级用量（2026-07 game-library）──
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    source_url_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_used_article_id: Mapped[int | None] = mapped_column(
        ForeignKey("articles.id", ondelete="SET NULL"), nullable=True)
```

- [ ] **Step 5: 写迁移 0065**（照 `0062_adversarial_review.py` 的 idiom）

```python
# server/alembic/versions/0065_game_library.py
from __future__ import annotations
from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

revision: str = "0065_game_library"
down_revision: str | None = "0064_qref_external_ingestion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "games",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("name_normalized", sa.String(200), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("comment_count", sa.Integer(), nullable=True),
        sa.Column("platforms", sa.JSON(), nullable=True),
        sa.Column("icon_url", sa.String(1000), nullable=True),
        sa.Column("screenshot_urls", sa.JSON(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("stock_category_id", sa.Integer(),
                  sa.ForeignKey("stock_categories.id", ondelete="SET NULL"), nullable=True),
        sa.Column("highlight_comments", sa.JSON(), nullable=True),
        sa.Column("related_hotspots", sa.JSON(), nullable=True),
        sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_used_article_id", sa.Integer(),
                  sa.ForeignKey("articles.id", ondelete="SET NULL"), nullable=True),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("first_seen_at", sa.DateTime(), nullable=True),
        sa.Column("last_verified_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("name_normalized", name="uq_games_name_normalized"),
        mysql_engine="InnoDB", mysql_charset="utf8mb4",
    )
    op.create_index("ix_games_is_active", "games", ["is_active"])
    op.create_table(
        "game_tags",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("game_id", sa.Integer(),
                  sa.ForeignKey("games.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tag", sa.String(100), nullable=False),
        sa.Column("axis", sa.String(20), nullable=True),
        sa.UniqueConstraint("game_id", "tag", name="uq_game_tags_game_tag"),
        mysql_engine="InnoDB", mysql_charset="utf8mb4",
    )
    op.create_index("ix_game_tags_game_id", "game_tags", ["game_id"])
    op.create_index("ix_game_tags_tag", "game_tags", ["tag"])

    op.add_column("stock_images", sa.Column("source_url", sa.String(1000), nullable=True))
    op.add_column("stock_images", sa.Column("source_url_hash", sa.String(64), nullable=True))
    op.add_column("stock_images",
                  sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("stock_images", sa.Column("last_used_at", sa.DateTime(), nullable=True))
    op.add_column("stock_images", sa.Column("last_used_article_id", sa.Integer(),
                  sa.ForeignKey("articles.id", ondelete="SET NULL"), nullable=True))
    op.create_unique_constraint(
        "uq_stock_images_category_source_hash", "stock_images",
        ["category_id", "source_url_hash"])


def downgrade() -> None:
    op.drop_constraint("uq_stock_images_category_source_hash", "stock_images", type_="unique")
    for col in ("last_used_article_id", "last_used_at", "use_count",
                "source_url_hash", "source_url"):
        op.drop_column("stock_images", col)
    op.drop_table("game_tags")
    op.drop_table("games")
```

- [ ] **Step 6: 让 Base 看见新模型**（`server/app/main.py` 顶部 import 区，与其它 `import ... models` 并列，触发映射登记）

```python
import server.app.modules.game_library.models  # noqa: F401
```

- [ ] **Step 7: 跑迁移 + 测试确认通过**

Run: `GEO_TEST_DATABASE_URL=mysql+pymysql://geo_user:password@127.0.0.1:3306/geo_test env python -m pytest server/tests/test_game_library_models.py -q`
Expected: PASS (2 passed)（`build_test_app` 建 schema 会执行模型建表；另跑 `alembic upgrade head` 验证迁移在真实链上无误——见下）

- [ ] **Step 8: 单独验证迁移可 upgrade/downgrade**

Run: `env python -m alembic upgrade head && env python -m alembic downgrade -1 && env python -m alembic upgrade head`（用本机 `geo_dev`；`env.py` 会用 `GEO_DATABASE_URL`）
Expected: 三步都无报错，`alembic current` = `0065_game_library`

- [ ] **Step 9: Commit**

```bash
git add server/app/modules/game_library/models.py server/app/modules/image_library/models.py server/alembic/versions/0065_game_library.py server/app/main.py server/tests/test_game_library_models.py
git commit -m "feat(game-library): games/game_tags 模型 + stock_images 扩列 + 迁移 0065

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `store_image_bytes` 写 hash + no-commit 变体 + hash 去重

**Files:**
- Modify: `server/app/modules/image_library/service.py:88-120`
- Test: `server/tests/test_image_store_dedup.py`

**Interfaces:**
- Consumes: `StockImage.source_url`/`source_url_hash`（Task 2）。
- Produces: `store_image_bytes(db, category, data, content_type, *, source_url="", width=None, height=None, commit=True) -> StockImage | None`（新增 `commit` 参数，默认 True 保持 web_fallback 现状；`commit=False` 只 flush）；写 `source_url`+`source_url_hash`；入库前按 `(category_id, source_url_hash)` 命中即返回已存在行、不重灌。helper `source_url_sha256(url: str) -> str`。

- [ ] **Step 1: 写失败测试**（mysql）

```python
# server/tests/test_image_store_dedup.py
import pytest


@pytest.mark.mysql
def test_store_image_dedup_by_source_url_hash(monkeypatch):
    from server.tests.utils import build_test_app
    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.image_library import service, minio_store
        monkeypatch.setattr(minio_store, "ensure_bucket", lambda *a, **k: None)
        monkeypatch.setattr(minio_store, "upload_image", lambda *a, **k: None)
        s = app.session_factory()
        try:
            cat = service.get_or_create_companion_category(s, "餐厅养成记")
            a = service.store_image_bytes(s, cat, b"x", "image/jpeg",
                                          source_url="http://x/1.jpg", commit=False)
            b = service.store_image_bytes(s, cat, b"x", "image/jpeg",
                                          source_url="http://x/1.jpg", commit=False)
            s.commit()
            assert a is not None and b is not None and a.id == b.id  # 去重=同一行
            assert a.source_url_hash and len(a.source_url_hash) == 64
        finally:
            s.close()
    finally:
        app.cleanup()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `GEO_TEST_DATABASE_URL=... env python -m pytest server/tests/test_image_store_dedup.py -q`
Expected: FAIL（`store_image_bytes` 无 `commit` 参数 / 无去重 / 无 hash 列写入）

- [ ] **Step 3: 改 `store_image_bytes`**（`server/app/modules/image_library/service.py`）

在文件顶部加 `import hashlib`，改函数：

```python
def source_url_sha256(url: str) -> str:
    return hashlib.sha256((url or "").encode("utf-8")).hexdigest()


def store_image_bytes(
    db: Session,
    category: StockCategory,
    data: bytes,
    content_type: str,
    *,
    source_url: str = "",
    width: int | None = None,
    height: int | None = None,
    commit: bool = True,
) -> StockImage | None:
    """把图片字节传 MinIO 并建 StockImage 记录。

    - 写 source_url + source_url_hash；同 (category_id, source_url_hash) 已存在则返回旧行、不重灌。
    - commit=True（默认，web_fallback 单图路径）逐张提交；commit=False（批量入库）只 flush、由调用方提交。
    """
    url_hash = source_url_sha256(source_url) if source_url else None
    if url_hash:
        existing = (
            db.query(StockImage)
            .filter(StockImage.category_id == category.id,
                    StockImage.source_url_hash == url_hash)
            .first()
        )
        if existing is not None:
            return existing

    ext = _MIME_EXT.get(content_type, "jpg")
    key = f"{uuid.uuid4().hex}.{ext}"
    try:
        minio_store.upload_image(category.bucket_name, key, data, content_type)
    except Exception as exc:
        logger.warning("上传 MinIO 失败 category=%s：%s", category.name, exc)
        return None

    img = StockImage(
        category_id=category.id,
        minio_key=key,
        filename=f"web_fallback_{key}",
        description=(f"来源：{source_url}" if source_url else "web_fallback"),
        tags=["web_fallback"],
        width=width or None,
        height=height or None,
        source_url=source_url or None,
        source_url_hash=url_hash,
    )
    db.add(img)
    if commit:
        db.commit()
        db.refresh(img)
    else:
        db.flush()
    return img
```

- [ ] **Step 4: 跑测试确认通过**

Run: `GEO_TEST_DATABASE_URL=... env python -m pytest server/tests/test_image_store_dedup.py -q`
Expected: PASS

- [ ] **Step 5: 回归 web_fallback 现有测试**

Run: `GEO_TEST_DATABASE_URL=... env python -m pytest server/tests/ -q -k "illustrate or web_fallback or image_library"`
Expected: 现有相关用例仍 PASS（`commit` 默认 True，行为不变）

- [ ] **Step 6: Commit**

```bash
git add server/app/modules/image_library/service.py server/tests/test_image_store_dedup.py
git commit -m "feat(image-library): store_image_bytes 写 source_url_hash + no-commit 变体 + hash 去重

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: 通用截图下载器 `shared/image_download.py`

**Files:**
- Create: `server/app/shared/image_download.py`, `server/tests/test_image_download.py`

**Interfaces:**
- Produces: `download_image(url: str, *, timeout=10, max_bytes=20*1024*1024) -> tuple[bytes, str] | None`——返回 `(data, content_type)`；限体积、content-type 白名单（magic bytes 校验 JPEG/PNG/WEBP/GIF）、拒跨站重定向、short timeout、失败返 `None`（best-effort，不抛）。常量 `ALLOWED_MIME`。

- [ ] **Step 1: 写失败测试**

```python
# server/tests/test_image_download.py
from server.app.shared import image_download


def test_download_rejects_oversize(monkeypatch):
    class FakeResp:
        headers = {"Content-Type": "image/jpeg", "Content-Length": str(999_999_999)}
        def read(self, n=-1): return b"\xff\xd8\xff" + b"0" * 100
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(image_download, "_urlopen_same_host", lambda *a, **k: FakeResp())
    assert image_download.download_image("http://x/big.jpg", max_bytes=1000) is None


def test_download_rejects_non_image(monkeypatch):
    class FakeResp:
        headers = {"Content-Type": "text/html"}
        def read(self, n=-1): return b"<html>"
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(image_download, "_urlopen_same_host", lambda *a, **k: FakeResp())
    assert image_download.download_image("http://x/p.html") is None


def test_download_ok_jpeg(monkeypatch):
    class FakeResp:
        headers = {"Content-Type": "image/jpeg"}
        def read(self, n=-1): return b"\xff\xd8\xff\xe0" + b"payload"
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(image_download, "_urlopen_same_host", lambda *a, **k: FakeResp())
    out = image_download.download_image("http://x/ok.jpg")
    assert out is not None and out[1] == "image/jpeg" and out[0].startswith(b"\xff\xd8")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `env python -m pytest server/tests/test_image_download.py -q`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现下载器**

```python
# server/app/shared/image_download.py
"""通用截图下载器：限体积/类型/重定向，best-effort（失败返 None、不抛）。"""
from __future__ import annotations

import logging
from urllib.parse import urlparse
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp", "image/gif"}
_MAGIC = {
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"GIF87a": "image/gif",
    b"GIF89a": "image/gif",
}
_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "image/*"}


def _urlopen_same_host(url: str, timeout: int):
    """打开 URL，拒绝跨站重定向（默认 opener 会跟随；这里限制 host 不变）。"""
    req = Request(url, headers=_HEADERS)
    resp = urlopen(req, timeout=timeout)  # noqa: S310  (host 校验在下方)
    if urlparse(resp.geturl()).hostname != urlparse(url).hostname:
        resp.close()
        raise ValueError("cross-host redirect rejected")
    return resp


def _sniff(data: bytes, declared: str) -> str | None:
    for magic, mime in _MAGIC.items():
        if data.startswith(magic):
            return mime
    if data[8:12] == b"WEBP":
        return "image/webp"
    return declared if declared in ALLOWED_MIME else None


def download_image(url: str, *, timeout: int = 10,
                   max_bytes: int = 20 * 1024 * 1024) -> tuple[bytes, str] | None:
    try:
        with _urlopen_same_host(url, timeout) as resp:
            declared = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
            clen = resp.headers.get("Content-Length")
            if clen and int(clen) > max_bytes:
                return None
            data = resp.read(max_bytes + 1)
        if len(data) > max_bytes:
            return None
        mime = _sniff(data, declared)
        if mime not in ALLOWED_MIME:
            return None
        return data, mime
    except Exception as exc:  # best-effort：单图失败不影响整体
        logger.info("download_image 失败 url=%s：%s", url, exc)
        return None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `env python -m pytest server/tests/test_image_download.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add server/app/shared/image_download.py server/tests/test_image_download.py
git commit -m "feat(shared): 通用截图下载器 (限体积/类型/拒跨站重定向)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: `service.upsert_game` — 跨源并集合并 + 截图入库

**Files:**
- Create: `server/app/modules/game_library/service.py`（本 task 只加 `upsert_game` + 内部 helper）
- Test: `server/tests/test_game_upsert.py`

**Interfaces:**
- Consumes: `types.Game`（Task 1）、`Game`/`GameTag`（Task 2）、`store_image_bytes(commit=False)`（Task 3）、`download_image`（Task 4）、`get_or_create_companion_category`、`_normalize_game_name`。
- Produces: `upsert_game(db, game: types.Game, *, max_screenshots: int = 6) -> Game`（ORM）——**假定入参 `game` 已补详情**（screenshot_urls/tags 非空）；按 `name_normalized` 合并；标量 max/挑非空；`sources`/`platforms`/`screenshot_urls` 并集；`game_tags` `INSERT IGNORE` 并集（不 delete-all）；截图下载 + `store_image_bytes(commit=False)` 去重入 MinIO；回写 `stock_category_id`/`last_verified_at`；**不 commit**（调用方按游戏 commit）。

- [ ] **Step 1: 写失败测试**（mysql）

```python
# server/tests/test_game_upsert.py
import pytest
from server.app.modules.game_library import types


def _game(source, gid, name, tags, shots, score, comments=1):
    return types.Game(source=source, game_id=gid, name=name, score=score,
                      tags=tags, platforms=["android"], comment_count=comments,
                      icon_url="http://x/i.png", screenshot_urls=shots,
                      description="d", raw={})


@pytest.mark.mysql
def test_upsert_merges_two_sources_into_one_row(monkeypatch):
    from server.tests.utils import build_test_app
    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.game_library import service
        from server.app.modules.game_library.models import Game, GameTag
        from server.app.modules.image_library import minio_store
        from server.app.shared import image_download
        monkeypatch.setattr(minio_store, "ensure_bucket", lambda *a, **k: None)
        monkeypatch.setattr(minio_store, "upload_image", lambda *a, **k: None)
        monkeypatch.setattr(image_download, "download_image",
                            lambda url, **k: (b"\xff\xd8\xff", "image/jpeg"))
        s = app.session_factory()
        try:
            service.upsert_game(s, _game("baidu", "1", "餐厅养成记", ["经营"],
                                         ["http://x/b1.jpg"], 8.0)); s.commit()
            service.upsert_game(s, _game("taptap", "9", "餐厅养成记", ["养成"],
                                         ["http://x/t1.jpg"], 9.2, comments=50)); s.commit()
            rows = s.query(Game).filter(Game.name_normalized == "餐厅养成记").all()
            assert len(rows) == 1                       # 合并成一行
            g = rows[0]
            assert g.score == 9.2 and g.comment_count == 50   # 标量取 max
            assert len(g.sources) == 2                        # sources 并集
            tags = {t.tag for t in s.query(GameTag).filter(GameTag.game_id == g.id)}
            assert tags == {"经营", "养成"}                    # 标签并集、taptap 不覆盖 baidu
        finally:
            s.close()
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_upsert_idempotent_same_source(monkeypatch):
    from server.tests.utils import build_test_app
    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.game_library import service
        from server.app.modules.game_library.models import Game
        from server.app.modules.image_library import minio_store
        from server.app.shared import image_download
        monkeypatch.setattr(minio_store, "ensure_bucket", lambda *a, **k: None)
        monkeypatch.setattr(minio_store, "upload_image", lambda *a, **k: None)
        monkeypatch.setattr(image_download, "download_image",
                            lambda url, **k: (b"\xff\xd8\xff", "image/jpeg"))
        s = app.session_factory()
        try:
            for _ in range(2):
                service.upsert_game(s, _game("baidu", "1", "星露谷", ["模拟"],
                                             ["http://x/a.jpg"], 9.0)); s.commit()
            assert s.query(Game).count() == 1  # 重跑不增行
        finally:
            s.close()
    finally:
        app.cleanup()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `GEO_TEST_DATABASE_URL=... env python -m pytest server/tests/test_game_upsert.py -q`
Expected: FAIL（`service.upsert_game` 不存在）

- [ ] **Step 3: 实现 `upsert_game`**

```python
# server/app/modules/game_library/service.py
"""游戏库服务：入库并集合并 + 检索聚合。"""
from __future__ import annotations

from sqlalchemy.orm import Session

from server.app.core.time import utcnow
from server.app.modules.articles.formatting.document import _normalize_game_name
from server.app.modules.game_library import types
from server.app.modules.game_library.models import Game, GameTag
from server.app.modules.image_library.service import (
    get_or_create_companion_category, store_image_bytes)
from server.app.shared.image_download import download_image


def _merge_scalar_max(cur, new):
    if new is None:
        return cur
    if cur is None:
        return new
    return max(cur, new)


def upsert_game(db: Session, game: types.Game, *, max_screenshots: int = 6) -> Game:
    """按 name_normalized 跨源并集合并；不 commit（调用方按游戏 commit）。"""
    norm = _normalize_game_name(game.name) or game.name
    row = db.query(Game).filter(Game.name_normalized == norm).first()
    if row is None:
        row = Game(name=game.name, name_normalized=norm, sources=[],
                   platforms=[], screenshot_urls=[], use_count=0, is_active=True,
                   first_seen_at=utcnow())
        db.add(row)
        db.flush()

    # 标量：max / 挑非空
    row.score = _merge_scalar_max(row.score, game.score)
    row.comment_count = _merge_scalar_max(row.comment_count, game.comment_count)
    if game.description and len(game.description) > len(row.description or ""):
        row.description = game.description
    row.icon_url = row.icon_url or game.icon_url

    # 并集：sources / platforms / screenshot_urls
    src_entry = {"source": game.source, "source_game_id": game.game_id}
    row.sources = list(row.sources or [])
    if src_entry not in row.sources:
        row.sources = row.sources + [src_entry]
    row.platforms = sorted(set((row.platforms or []) + list(game.platforms or [])))
    row.screenshot_urls = list(dict.fromkeys((row.screenshot_urls or []) + list(game.screenshot_urls or [])))

    # 标签并集（INSERT IGNORE 语义：查已有、只补缺，不 delete-all）
    existing_tags = {t.tag for t in db.query(GameTag).filter(GameTag.game_id == row.id)}
    for tag in game.tags or []:
        if tag and tag not in existing_tags:
            db.add(GameTag(game_id=row.id, tag=tag, axis=None))
            existing_tags.add(tag)

    # 截图：get_or_create 栏目 → 下载 → store（去重=并集）→ 回写栏目
    cat = get_or_create_companion_category(db, game.name)
    if cat is not None:
        row.stock_category_id = cat.id
        for url in (game.screenshot_urls or [])[:max_screenshots]:
            got = download_image(url)
            if got is None:
                continue
            data, mime = got
            store_image_bytes(db, cat, data, mime, source_url=url, commit=False)

    row.last_verified_at = utcnow()
    db.flush()
    return row
```

> 注：`get_or_create_companion_category` 内部会 `db.commit()`（建栏目并发安全所需）——这在批量入库里是可接受的「栏目建好即提交」，游戏行/标签/图仍由本函数 flush、调用方在游戏边界 commit。若后续要更严格的单事务，可在 Task 7 的 `run_ingest_once` 里预建栏目。

- [ ] **Step 4: 跑测试确认通过**

Run: `GEO_TEST_DATABASE_URL=... env python -m pytest server/tests/test_game_upsert.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add server/app/modules/game_library/service.py server/tests/test_game_upsert.py
git commit -m "feat(game-library): upsert_game 跨源并集合并 + 截图入库去重

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: `query_games_by_tags` + `list_game_tags`（relevant 准入 / diversity 只排序）

**Files:**
- Modify: `server/app/modules/game_library/service.py`（加两个检索函数）
- Create: `server/app/modules/game_library/schemas.py`
- Test: `server/tests/test_game_query.py`

**Interfaces:**
- Produces: `list_game_tags(db, limit=200) -> list[dict]`（`[{"tag","game_count"}]`，按 game_count 降序，只数 `is_active`）；`query_games_by_tags(db, relevant_tags, diversity_tags=None, exclude_tags=None, min_score=None, limit=20) -> list[dict]`——候选=命中任一 `relevant_tags`（准入）；`diversity_tags` 只影响排序不放宽准入；排序 `last_used_at ASC, score DESC, comment_count DESC`；每项 `{game_id,name,score,tags,description,stock_category_id,icon_url,screenshot_urls,highlight_comments,related_hotspots,use_count,last_used_at}`。

- [ ] **Step 1: 写失败测试**（mysql）

```python
# server/tests/test_game_query.py
import pytest


def _seed(s):
    from server.app.modules.game_library.models import Game, GameTag
    def mk(name, tags, score):
        g = Game(name=name, name_normalized=name, score=score, use_count=0, is_active=True)
        s.add(g); s.flush()
        for t in tags:
            s.add(GameTag(game_id=g.id, tag=t))
        return g
    mk("经营A", ["经营"], 9.0)
    mk("离题B", ["射击"], 9.9)       # 只命中 diversity，不该入池
    mk("经营C", ["经营", "射击"], 7.0)
    s.commit()


@pytest.mark.mysql
def test_relevant_gates_diversity_only_ranks(monkeypatch):
    from server.tests.utils import build_test_app
    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.game_library import service
        s = app.session_factory()
        try:
            _seed(s)
            out = service.query_games_by_tags(
                s, relevant_tags=["经营"], diversity_tags=["射击"], limit=10)
            names = [g["name"] for g in out]
            assert "离题B" not in names            # 离题(只命中 diversity)不入池
            assert set(names) == {"经营A", "经营C"}  # 只 relevant 命中入池
        finally:
            s.close()
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_list_game_tags_counts_desc(monkeypatch):
    from server.tests.utils import build_test_app
    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.game_library import service
        s = app.session_factory()
        try:
            _seed(s)
            tags = service.list_game_tags(s)
            counts = {t["tag"]: t["game_count"] for t in tags}
            assert counts["经营"] == 2 and counts["射击"] == 2
        finally:
            s.close()
    finally:
        app.cleanup()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `GEO_TEST_DATABASE_URL=... env python -m pytest server/tests/test_game_query.py -q`
Expected: FAIL（函数不存在）

- [ ] **Step 3: 实现检索**（追加到 `service.py`；顶部加 `from sqlalchemy import func, select`）

```python
def list_game_tags(db: Session, limit: int = 200) -> list[dict]:
    stmt = (
        select(GameTag.tag, func.count(func.distinct(GameTag.game_id)).label("c"))
        .join(Game, Game.id == GameTag.game_id)
        .where(Game.is_active.is_(True))
        .group_by(GameTag.tag)
        .order_by(func.count(func.distinct(GameTag.game_id)).desc())
        .limit(max(1, min(1000, limit)))
    )
    return [{"tag": tag, "game_count": c} for tag, c in db.execute(stmt).all()]


def _game_to_dict(g: Game) -> dict:
    return {
        "game_id": g.id, "name": g.name, "score": g.score,
        "tags": [t.tag for t in g.tags], "description": g.description,
        "stock_category_id": g.stock_category_id, "icon_url": g.icon_url,
        "screenshot_urls": g.screenshot_urls or [],
        "highlight_comments": g.highlight_comments, "related_hotspots": g.related_hotspots,
        "use_count": g.use_count, "last_used_at": g.last_used_at.isoformat() if g.last_used_at else None,
    }


def query_games_by_tags(db: Session, relevant_tags: list[str],
                        diversity_tags: list[str] | None = None,
                        exclude_tags: list[str] | None = None,
                        min_score: float | None = None, limit: int = 20) -> list[dict]:
    """relevant_tags=准入(命中任一)；diversity_tags 只排序不放宽准入。"""
    if not relevant_tags:
        return []
    # 准入：命中任一 relevant_tag 的 game_id
    admit = select(GameTag.game_id).where(GameTag.tag.in_(relevant_tags)).distinct()
    stmt = select(Game).where(Game.id.in_(admit), Game.is_active.is_(True))
    if exclude_tags:
        excl = select(GameTag.game_id).where(GameTag.tag.in_(exclude_tags)).distinct()
        stmt = stmt.where(Game.id.notin_(excl))
    if min_score is not None:
        stmt = stmt.where(Game.score >= min_score)
    # 取材均衡：近期没用过优先(MySQL NULL 最小→从没写过的排最前)，质量兜底
    stmt = stmt.order_by(Game.last_used_at.asc(), Game.score.desc().nullslast(),
                         Game.comment_count.desc().nullslast()).limit(max(1, min(100, limit)))
    rows = list(db.execute(stmt).scalars().all())
    # diversity 只影响排序：命中 diversity 的相关游戏微微提前（稳定排序）
    if diversity_tags:
        dset = set(diversity_tags)
        rows.sort(key=lambda g: 0 if dset & {t.tag for t in g.tags} else 1)
    return [_game_to_dict(g) for g in rows]
```

> `schemas.py`：为 MCP 端点定义响应/请求模型（`GameTagOut`/`GameCard`/`QueryGamesRequest`），字段与 `_game_to_dict` 一致；Task 9 端点用。（此处按现有 modules 的 schemas.py 风格建即可。）

- [ ] **Step 4: 跑测试确认通过**

Run: `GEO_TEST_DATABASE_URL=... env python -m pytest server/tests/test_game_query.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add server/app/modules/game_library/service.py server/app/modules/game_library/schemas.py server/tests/test_game_query.py
git commit -m "feat(game-library): query_games_by_tags (relevant 准入/diversity 排序) + list_game_tags

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Settings + `scheduler.run_ingest_once`/`start_game_ingest` + `create_app` 接线

**Files:**
- Modify: `server/app/core/config.py:113`
- Create: `server/app/modules/game_library/scheduler.py`
- Modify: `server/app/main.py:488`
- Test: `server/tests/test_game_scheduler.py`

**Interfaces:**
- Consumes: `registry.search`/`collect_pool`、`sources.taptap.get_detail`、`service.upsert_game`。
- Produces: 设置 `game_ingest_scheduler_enabled: bool=False`、`game_ingest_interval_seconds: int=21600`、`game_ingest_targets: str=""`（JSON，缺省回落模块种子常量）；`run_ingest_once(session_factory, *, targets=None) -> dict`（`{"targets","upserted","failed"}`，逐目标 try/except 隔离，taptap 逐游戏补 `get_detail` + 轮内 `(source,game_id)` 去重 + 每目标游戏数上限）；`start_game_ingest(session_factory) -> bool`；`stop_game_ingest() -> None`。

- [ ] **Step 1: 写失败测试**（run_ingest_once 纯函数，monkeypatch search，不出网）

```python
# server/tests/test_game_scheduler.py
import pytest


@pytest.mark.mysql
def test_run_ingest_once_isolates_target_failure(monkeypatch):
    from server.tests.utils import build_test_app
    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.game_library import scheduler, registry, service
        from server.app.modules.game_library import types
        from server.app.modules.game_library.models import Game

        def fake_collect(source, category, pool_size, **kw):
            if category == "boom":
                raise RuntimeError("source down")
            return [types.Game(source="baidu", game_id="1", name=f"{category}游戏",
                              score=8.0, tags=[category], platforms=[], comment_count=1,
                              icon_url=None, screenshot_urls=[], description="d", raw={})]
        monkeypatch.setattr(registry, "collect_pool", fake_collect)
        # 让 upsert 不触 MinIO/网络
        monkeypatch.setattr(service, "download_image", lambda *a, **k: None, raising=False)

        targets = [{"source": "baidu", "category": "经营"},
                   {"source": "baidu", "category": "boom"}]
        result = scheduler.run_ingest_once(app.session_factory, targets=targets)
        assert result["failed"] == 1 and result["upserted"] >= 1
        s = app.session_factory()
        try:
            assert s.query(Game).count() == 1   # 好目标入库，坏目标隔离
        finally:
            s.close()
    finally:
        app.cleanup()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `GEO_TEST_DATABASE_URL=... env python -m pytest server/tests/test_game_scheduler.py -q`
Expected: FAIL（`scheduler` 不存在）

- [ ] **Step 3: 加 settings**（`server/app/core/config.py`，在第 113 行 `scheduler_tz` 后）

```python
    # 游戏库定时入库（应用内后台线程）。默认关闭。
    game_ingest_scheduler_enabled: bool = False  # GEO_GAME_INGEST_SCHEDULER_ENABLED
    game_ingest_interval_seconds: int = 21600  # GEO_GAME_INGEST_INTERVAL_SECONDS（6h）
    game_ingest_targets: str = ""  # GEO_GAME_INGEST_TARGETS（JSON，空则回落种子常量）
```

- [ ] **Step 4: 实现 `scheduler.py`**（照 `sync_scheduler.py` 的线程/wait 循环 idiom）

```python
# server/app/modules/game_library/scheduler.py
"""游戏库定时入库：run_ingest_once 纯函数 + 后台守护线程（复用 sync_scheduler 模式）。"""
from __future__ import annotations

import json
import logging
import threading
from typing import Any, Callable

from server.app.core.config import get_settings
from server.app.modules.game_library import registry, service
from server.app.modules.game_library.sources import taptap

logger = logging.getLogger(__name__)
SessionFactory = Callable[[], Any]

_thread: threading.Thread | None = None
_stop = threading.Event()

# 缺省种子清单（GEO_GAME_INGEST_TARGETS 为空时用）。分类词按源不同——见 spec §四。
SEED_TARGETS = [
    {"source": "baidu", "category": "经营", "pages": 2, "max_games": 30, "max_shots": 6},
    {"source": "taptap", "category": "养成", "pages": 2, "max_games": 30, "max_shots": 6},
    {"source": "taptap", "category": "国风", "pages": 2, "max_games": 30, "max_shots": 6},
]


def _load_targets() -> list[dict]:
    raw = (get_settings().game_ingest_targets or "").strip()
    if not raw:
        return SEED_TARGETS
    try:
        return json.loads(raw)
    except Exception:
        logger.exception("GEO_GAME_INGEST_TARGETS 解析失败，回落种子清单")
        return SEED_TARGETS


def run_ingest_once(session_factory: SessionFactory, *, targets: list[dict] | None = None) -> dict:
    """扫一轮入库目标。逐目标隔离（一个失败不影响其它）。纯函数、可单测。"""
    targets = targets if targets is not None else _load_targets()
    upserted, failed = 0, 0
    for t in targets:
        source, category = t["source"], t["category"]
        max_games = int(t.get("max_games", 30))
        max_shots = int(t.get("max_shots", 6))
        db = session_factory()
        try:
            pool = registry.collect_pool(source, category, max_games)
            seen: set[tuple[str, str]] = set()
            for g in pool:
                key = (g.source, g.game_id)
                if key in seen:
                    continue
                seen.add(key)
                # TapTap 列表无截图/占位标签 → 补详情
                if source == "taptap" and not g.screenshot_urls:
                    try:
                        g = taptap.get_detail(g.game_id)
                    except Exception:
                        logger.info("taptap get_detail 失败 game_id=%s", g.game_id)
                        continue
                service.upsert_game(db, g, max_screenshots=max_shots)
                db.commit()  # commit 边界=每游戏一次
                upserted += 1
        except Exception:
            failed += 1
            db.rollback()
            logger.exception("入库目标失败 source=%s category=%s", source, category)
        finally:
            db.close()
    return {"targets": len(targets), "upserted": upserted, "failed": failed}


def start_game_ingest(session_factory: SessionFactory) -> bool:
    global _thread
    if not get_settings().game_ingest_scheduler_enabled:
        return False
    if _thread is not None and _thread.is_alive():
        return False
    _stop.clear()

    def _loop() -> None:
        while not _stop.is_set():
            interval = max(300, get_settings().game_ingest_interval_seconds)
            if _stop.wait(interval):   # 先等再抓；停止事件立即唤醒
                break
            try:
                logger.info("game-ingest round: %s", run_ingest_once(session_factory))
            except Exception:
                logger.exception("game-ingest round failed")

    _thread = threading.Thread(target=_loop, daemon=True, name="game-ingest")
    _thread.start()
    return True


def stop_game_ingest() -> None:
    _stop.set()
```

- [ ] **Step 5: `create_app` 接线**（`server/app/main.py`，在 pipeline scheduler 块之后、约第 488 行后）

```python
    try:
        from server.app.modules.game_library.scheduler import start_game_ingest

        start_game_ingest(SessionLocal)
    except Exception:
        import logging as _logging

        _logging.getLogger(__name__).exception("start_game_ingest failed")
```

- [ ] **Step 6: 跑测试确认通过**

Run: `GEO_TEST_DATABASE_URL=... env python -m pytest server/tests/test_game_scheduler.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add server/app/core/config.py server/app/modules/game_library/scheduler.py server/app/main.py server/tests/test_game_scheduler.py
git commit -m "feat(game-library): 定时入库 scheduler + settings + create_app 接线

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: 手动入库 CLI `server/scripts/ingest_games.py`

**Files:**
- Create: `server/scripts/ingest_games.py`, `server/tests/test_game_ingest_cli.py`

**Interfaces:**
- Consumes: `scheduler.run_ingest_once`、`SessionLocal`。
- Produces: `python -m server.scripts.ingest_games --source {taptap,baidu} --category <名> [--pages N] [--max-games N] [--max-shots N]`——组一个单目标 targets 调 `run_ingest_once`，打印计数；`main(argv=None)` 可测。

- [ ] **Step 1: 写失败测试**（monkeypatch run_ingest_once，不出网）

```python
# server/tests/test_game_ingest_cli.py
def test_cli_builds_single_target(monkeypatch):
    from server.scripts import ingest_games
    captured = {}
    def fake_run(session_factory, *, targets=None):
        captured["targets"] = targets
        return {"targets": 1, "upserted": 3, "failed": 0}
    monkeypatch.setattr(ingest_games, "run_ingest_once", fake_run)
    monkeypatch.setattr(ingest_games, "SessionLocal", lambda: None, raising=False)
    ingest_games.main(["--source", "taptap", "--category", "国风", "--pages", "1"])
    assert captured["targets"][0]["source"] == "taptap"
    assert captured["targets"][0]["category"] == "国风"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `env python -m pytest server/tests/test_game_ingest_cli.py -q`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 CLI**

```python
# server/scripts/ingest_games.py
"""手动入库 CLI：python -m server.scripts.ingest_games --source taptap --category 国风"""
from __future__ import annotations

import argparse

from server.app.db.session import SessionLocal
from server.app.modules.game_library.scheduler import run_ingest_once


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="ingest_games")
    p.add_argument("--source", required=True, choices=["taptap", "baidu"])
    p.add_argument("--category", required=True)
    p.add_argument("--pages", type=int, default=2)
    p.add_argument("--max-games", type=int, default=30)
    p.add_argument("--max-shots", type=int, default=6)
    args = p.parse_args(argv)
    targets = [{"source": args.source, "category": args.category,
                "pages": args.pages, "max_games": args.max_games, "max_shots": args.max_shots}]
    result = run_ingest_once(SessionLocal, targets=targets)
    print(f"入库完成：{result}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `env python -m pytest server/tests/test_game_ingest_cli.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/scripts/ingest_games.py server/tests/test_game_ingest_cli.py
git commit -m "feat(game-library): 手动入库 CLI ingest_games

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: MCP 检索端点 + 2 个工具 + `MCP_TOOLS_COUNT` 35 + 修漂移测试

**Files:**
- Create: `server/app/modules/game_library/router.py`
- Modify: `server/app/main.py`（mount router）, `server/mcp/tools/catalog.py`（加 2 工具）, `server/app/modules/mcp_catalog/connect_router.py:27`, `server/tests/test_mcp_status_count.py:17`, `server/tests/test_mcp_tools_registration.py:22`, `CLAUDE.md`
- Test: `server/tests/test_game_mcp.py`

**Interfaces:**
- Consumes: `service.list_game_tags`/`query_games_by_tags`、`require_mcp_token`、`mcp_exception_response`。
- Produces: `GET /api/mcp/game-library/tags?limit=` + `POST /api/mcp/game-library/query`（MCP-token）；MCP 工具 `list_game_tags(limit=200)`、`query_games_by_tags(relevant_tags, diversity_tags=None, exclude_tags=None, min_score=None, limit=20)`（catalog 组）；`MCP_TOOLS_COUNT=35`。

- [ ] **Step 1: 写失败测试**（mysql，MCP token 端点）

```python
# server/tests/test_game_mcp.py
import pytest


@pytest.mark.mysql
def test_game_library_endpoints_auth(monkeypatch):
    from server.tests.utils import build_test_app
    app = build_test_app(monkeypatch)
    try:
        monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
        from server.app.core import config
        config.get_settings.cache_clear()
        # 无 token → 401
        assert app.client.get("/api/mcp/game-library/tags").status_code == 401
        # 有 token → 200 + list
        r = app.client.get("/api/mcp/game-library/tags", headers={"X-MCP-Token": "secret"})
        assert r.status_code == 200 and isinstance(r.json(), list)
        r2 = app.client.post("/api/mcp/game-library/query",
                             json={"relevant_tags": ["经营"]},
                             headers={"X-MCP-Token": "secret"})
        assert r2.status_code == 200 and isinstance(r2.json(), list)
    finally:
        app.cleanup()


def test_mcp_tools_count_is_35():
    from server.app.modules.mcp_catalog.connect_router import MCP_TOOLS_COUNT
    assert MCP_TOOLS_COUNT == 35
```

- [ ] **Step 2: 跑测试确认失败**

Run: `GEO_TEST_DATABASE_URL=... env python -m pytest server/tests/test_game_mcp.py -q`
Expected: FAIL（端点 404 / count 33）

- [ ] **Step 3: 写 `game_library/router.py`**

```python
# server/app/modules/game_library/router.py
"""游戏库 MCP-token 只读检索端点。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from server.app.core.mcp_auth import require_mcp_token
from server.app.core.mcp_errors import mcp_exception_response
from server.app.db.session import get_db
from server.app.modules.game_library import service

game_library_mcp_router = APIRouter(
    prefix="/api/mcp/game-library", tags=["mcp-game-library"],
    dependencies=[Depends(require_mcp_token)])


class QueryGamesRequest(BaseModel):
    relevant_tags: list[str]
    diversity_tags: list[str] | None = None
    exclude_tags: list[str] | None = None
    min_score: float | None = None
    limit: int = 20


@game_library_mcp_router.get("/tags")
def mcp_list_game_tags(limit: int = 200, db: Session = Depends(get_db)) -> list[dict]:
    try:
        return service.list_game_tags(db, limit=limit)
    except Exception as exc:
        raise mcp_exception_response(exc, context="list_game_tags") from exc


@game_library_mcp_router.post("/query")
def mcp_query_games(req: QueryGamesRequest, db: Session = Depends(get_db)) -> list[dict]:
    try:
        return service.query_games_by_tags(
            db, relevant_tags=req.relevant_tags, diversity_tags=req.diversity_tags,
            exclude_tags=req.exclude_tags, min_score=req.min_score, limit=req.limit)
    except Exception as exc:
        raise mcp_exception_response(exc, context="query_games_by_tags") from exc
```

- [ ] **Step 4: mount router**（`server/app/main.py`，与其它 `app.include_router(...)` 并列）

```python
    from server.app.modules.game_library.router import game_library_mcp_router
    app.include_router(game_library_mcp_router)
```

- [ ] **Step 5: 加 2 个 MCP 工具**（`server/mcp/tools/catalog.py` 末尾，照 `list_question_items` 的 `_aget` 模式）

```python
@mcp.tool()
async def list_game_tags(limit: int = 200) -> dict[str, Any]:
    """List available game tags (with game_count) to pick topical取材 tags from.

    Use before query_games_by_tags so you choose real tags, not invented ones.
    """
    return await _aget("/api/mcp/game-library/tags", params={"limit": max(1, min(1000, limit))})


@mcp.tool()
async def query_games_by_tags(
    relevant_tags: list[str],
    diversity_tags: list[str] | None = None,
    exclude_tags: list[str] | None = None,
    min_score: float | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Query real games from the library by tags, for on-topic取材.

    relevant_tags gate admission (a game must match at least one). diversity_tags
    only diversify ordering — they never admit off-topic games. Threshold checks
    (>=N to skip WebSearch) count only the relevant-matched pool.
    """
    body: dict[str, Any] = {"relevant_tags": relevant_tags, "limit": max(1, min(100, limit))}
    if diversity_tags:
        body["diversity_tags"] = diversity_tags
    if exclude_tags:
        body["exclude_tags"] = exclude_tags
    if min_score is not None:
        body["min_score"] = min_score
    return await _apost("/api/mcp/game-library/query", json=body)
```

> `catalog.py` 目前只有 `_aget`（只读）。`query_games_by_tags` 走 POST，需要 `_apost`——若 catalog.py 无 `_apost`，从 `server/mcp/tools/action.py` 复制其 `_apost` helper 到 catalog.py 顶部（同 `_aget` 风格，用 `_client().post`）。二者仍属 catalog（只读语义）。

- [ ] **Step 6: 改计数 + 修漂移测试**

- `server/app/modules/mcp_catalog/connect_router.py:27`：`MCP_TOOLS_COUNT = 35`
- `server/tests/test_mcp_status_count.py:17`：`def test_mcp_tools_count_is_31` → `_is_35`，断言 `== 35`
- `server/tests/test_mcp_tools_registration.py:22`：`assert MCP_TOOLS_COUNT == 31` → `== 35`

- [ ] **Step 7: 跑测试确认通过**

Run: `GEO_TEST_DATABASE_URL=... env python -m pytest server/tests/test_game_mcp.py server/tests/test_mcp_status_count.py server/tests/test_mcp_tools_registration.py -q`
Expected: PASS（含工具注册断言 `len(mcp._tool_manager._tools) >= 35`）

- [ ] **Step 8: Commit**

```bash
git add server/app/modules/game_library/router.py server/app/main.py server/mcp/tools/catalog.py server/app/modules/mcp_catalog/connect_router.py server/tests/test_game_mcp.py server/tests/test_mcp_status_count.py server/tests/test_mcp_tools_registration.py
git commit -m "feat(game-library): MCP 检索端点 + list_game_tags/query_games_by_tags 工具 (33->35)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: `save_article` 加 `selected_games` + 后端同事务 bump 游戏用量

**Files:**
- Modify: `server/app/modules/game_library/service.py`（加 `bump_game_usage`）, `server/app/modules/articles/routers/mcp.py:194-286`, `server/mcp/tools/action.py`（`save_article` 加参数）
- Test: `server/tests/test_game_usage_writeback.py`（游戏级部分）

**Interfaces:**
- Produces: `service.bump_game_usage(db, game_ids: list[int], article_id: int) -> None`（`UPDATE games SET use_count=use_count+1, last_used_at=now, last_used_article_id=:aid WHERE id IN :ids`，未知 id 天然跳过，不 commit）；`SaveArticleFromMcpPayload.selected_games: list[SelectedGame] | None`（`SelectedGame{game_id:int, name:str}`）；`save_article` MCP 工具加 `selected_games` 可选参数。

- [ ] **Step 1: 写失败测试**（mysql，直接测 bump + 端点透传）

```python
# server/tests/test_game_usage_writeback.py
import pytest


@pytest.mark.mysql
def test_bump_game_usage_increments_and_skips_unknown(monkeypatch):
    from server.tests.utils import build_test_app
    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.game_library import service
        from server.app.modules.game_library.models import Game
        s = app.session_factory()
        try:
            g = Game(name="星露谷", name_normalized="星露谷", use_count=0, is_active=True)
            s.add(g); s.flush()
            service.bump_game_usage(s, [g.id, 999999], article_id=42)  # 999999 不存在
            s.commit()
            got = s.get(Game, g.id)
            assert got.use_count == 1 and got.last_used_article_id == 42
            assert got.last_used_at is not None
        finally:
            s.close()
    finally:
        app.cleanup()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `GEO_TEST_DATABASE_URL=... env python -m pytest server/tests/test_game_usage_writeback.py::test_bump_game_usage_increments_and_skips_unknown -q`
Expected: FAIL（`bump_game_usage` 不存在）

- [ ] **Step 3: 实现 `bump_game_usage`**（追加到 `service.py`；顶部已 `from server.app.core.time import utcnow`）

```python
def bump_game_usage(db: Session, game_ids: list[int], article_id: int) -> None:
    """文章采用游戏后回写游戏级用量（不 commit，调用方同事务提交）。未知 id 天然跳过。"""
    ids = [int(i) for i in (game_ids or []) if i]
    if not ids:
        return
    now = utcnow()
    db.query(Game).filter(Game.id.in_(ids)).update(
        {Game.use_count: Game.use_count + 1, Game.last_used_at: now,
         Game.last_used_article_id: article_id},
        synchronize_session=False)
```

- [ ] **Step 4: 端点加 `selected_games`**（`server/app/modules/articles/routers/mcp.py`）

Payload（第 194-206 行的 `SaveArticleFromMcpPayload` 内加字段 + 新增子模型）：
```python
class SelectedGame(BaseModel):
    game_id: int
    name: str


class SaveArticleFromMcpPayload(BaseModel):
    # ...现有字段...
    model_label: str | None = Field(default=None, max_length=120)
    selected_games: list[SelectedGame] | None = None
```

Handler（在第 270 行 `article = _create_article(...)` 之后、第 286 行 `db.commit()` **之前**插入）：
```python
        if payload.selected_games:
            from server.app.modules.game_library.service import bump_game_usage
            bump_game_usage(db, [g.game_id for g in payload.selected_games], article.id)
        db.commit()
```

- [ ] **Step 5: `save_article` MCP 工具加参数**（`server/mcp/tools/action.py` 的 `save_article`）

签名加 `selected_games: list[dict[str, Any]] | None = None`，并在构造 `payload` 时带上：
```python
    if selected_games:
        payload["selected_games"] = selected_games
```
docstring 加：`selected_games: 库检索取材时选中的游戏 [{"game_id": int, "name": str}]，用于回写取材均衡用量；回退 websearch 的游戏无 game_id、留空。`

- [ ] **Step 6: 写端点透传测试 + 跑全部**

```python
# 追加到 server/tests/test_game_usage_writeback.py
@pytest.mark.mysql
def test_save_from_mcp_bumps_selected_games(monkeypatch):
    from server.tests.utils import build_test_app
    app = build_test_app(monkeypatch)
    try:
        monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
        from server.app.core import config
        config.get_settings.cache_clear()
        from server.app.modules.game_library.models import Game
        # 造一个问题 + 模板 + 游戏（最小依赖：直接建行）
        s = app.session_factory()
        try:
            g = Game(name="牧场物语", name_normalized="牧场物语", use_count=0, is_active=True)
            s.add(g); s.commit(); gid = g.id
        finally:
            s.close()
        # 依据现有 test_articles / save-from-mcp 测试建 question_item + template，
        # 取到 qid/tpl_id 后：
        body = {"question_item_id": qid, "prompt_template_id": tpl_id, "user_id": app.admin_id,
                "title": "牧场物语攻略", "markdown_content": "正文...",
                "selected_games": [{"game_id": gid, "name": "牧场物语"}]}
        r = app.client.post("/api/mcp/articles/save-from-mcp", json=body,
                            headers={"X-MCP-Token": "secret"})
        assert r.status_code == 200
        s = app.session_factory()
        try:
            assert s.get(Game, gid).use_count == 1
        finally:
            s.close()
    finally:
        app.cleanup()
```

> 注：`qid`/`tpl_id` 的建法照抄现有 `server/tests/test_articles_api.py` 里 save-from-mcp 用例的 fixture（建 `QuestionItem` + `PromptTemplate`）。实现者读那个文件复用同一段建行代码，不要另造。

Run: `GEO_TEST_DATABASE_URL=... env python -m pytest server/tests/test_game_usage_writeback.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add server/app/modules/game_library/service.py server/app/modules/articles/routers/mcp.py server/mcp/tools/action.py server/tests/test_game_usage_writeback.py
git commit -m "feat(game-library): save_article selected_games -> 同事务 bump 游戏用量

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: 图片级用量 — 软 LRU 选图 + 落图回写

**Files:**
- Modify: `server/app/modules/image_library/selector.py:47-63`（`pick_image_id` 软 LRU）, `server/app/modules/image_library/service.py`（`bump_stock_image_usage`）, `server/app/modules/articles/hook.py`（insert 后 bump）, `server/app/modules/articles/ai_illustrate_svc.py:121-233`（illustrate 后 bump）
- Test: `server/tests/test_image_lru_usage.py`

**Interfaces:**
- Produces: `pick_image_id` 排序改 `last_used_at ASC, use_count ASC, RAND()`（LRU + 平局随机）；`service.bump_stock_image_usage(db, image_ids, article_id) -> None`（同 games 版，UPDATE stock_images，不 commit）；`collect_stock_image_ids(content_json: dict) -> list[int]`（walk 节点收 `attrs.stockImageId`）。

- [ ] **Step 1: 写失败测试**（mysql）

```python
# server/tests/test_image_lru_usage.py
import pytest


@pytest.mark.mysql
def test_pick_image_prefers_least_recently_used(monkeypatch):
    from server.tests.utils import build_test_app
    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.image_library.models import StockCategory, StockImage
        from server.app.modules.image_library.selector import ImageQuery, pick_image_id
        from server.app.core.time import utcnow
        from datetime import timedelta
        s = app.session_factory()
        try:
            cat = StockCategory(name="牧场物语", bucket_name="mcwl", kind="companion")
            s.add(cat); s.flush()
            old = StockImage(category_id=cat.id, minio_key="k1", filename="a",
                             use_count=5, last_used_at=utcnow() - timedelta(days=10))
            fresh = StockImage(category_id=cat.id, minio_key="k2", filename="b",
                               use_count=0, last_used_at=None)  # 从没用过
            s.add_all([old, fresh]); s.commit()
            picked = pick_image_id(ImageQuery(category_ids=[cat.id]), s)
            assert picked == fresh.id   # NULL last_used_at 最小 → 从没用过的优先
        finally:
            s.close()
    finally:
        app.cleanup()


@pytest.mark.mysql
def test_bump_stock_image_usage(monkeypatch):
    from server.tests.utils import build_test_app
    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.image_library.models import StockCategory, StockImage
        from server.app.modules.image_library.service import bump_stock_image_usage
        s = app.session_factory()
        try:
            cat = StockCategory(name="c", bucket_name="c", kind="companion")
            s.add(cat); s.flush()
            img = StockImage(category_id=cat.id, minio_key="k", filename="f", use_count=0)
            s.add(img); s.flush()
            bump_stock_image_usage(s, [img.id], article_id=7); s.commit()
            got = s.get(StockImage, img.id)
            assert got.use_count == 1 and got.last_used_article_id == 7
        finally:
            s.close()
    finally:
        app.cleanup()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `GEO_TEST_DATABASE_URL=... env python -m pytest server/tests/test_image_lru_usage.py -q`
Expected: FAIL（pick 随机 / `bump_stock_image_usage` 不存在）

- [ ] **Step 3: `pick_image_id` 改软 LRU**（`server/app/modules/image_library/selector.py:61-62`）

把 `stmt = stmt.order_by(func.rand()).limit(1)` 改为：
```python
    from server.app.modules.image_library.models import StockImage  # 已在函数内 import
    # 软 LRU：最久没用过优先(MySQL NULL 最小→从没用过的排最前)，用量少优先，平局随机
    stmt = stmt.order_by(
        StockImage.last_used_at.asc(), StockImage.use_count.asc(), func.rand()
    ).limit(1)
```

- [ ] **Step 4: 加 `bump_stock_image_usage` + `collect_stock_image_ids`**（`server/app/modules/image_library/service.py`）

```python
def bump_stock_image_usage(db: Session, image_ids: list[int], article_id: int) -> None:
    """落图后回写图片级用量（不 commit）。"""
    ids = [int(i) for i in (image_ids or []) if i]
    if not ids:
        return
    from server.app.core.time import utcnow
    db.query(StockImage).filter(StockImage.id.in_(ids)).update(
        {StockImage.use_count: StockImage.use_count + 1, StockImage.last_used_at: utcnow(),
         StockImage.last_used_article_id: article_id}, synchronize_session=False)


def collect_stock_image_ids(content_json: dict) -> list[int]:
    """从 Tiptap content 里收集所有 image 节点的 stockImageId。"""
    out: list[int] = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "image":
                sid = (node.get("attrs") or {}).get("stockImageId")
                if isinstance(sid, int):
                    out.append(sid)
            for child in node.get("content") or []:
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(content_json)
    return out
```

- [ ] **Step 5: 接线两个落图点**

`server/app/modules/articles/hook.py:insert_images_for_article`（第 52 行 `article.content_json = ...` 之后、`db.flush()` 之前）：
```python
    from server.app.modules.image_library.service import bump_stock_image_usage
    bump_stock_image_usage(db, [r.id for r in refs], article_id)
```

`server/app/modules/articles/ai_illustrate_svc.py:illustrate_one`（run_ai_format 之后、最终 `db.commit()`（约第 233 行）之前）：
```python
    from server.app.modules.image_library.service import (
        bump_stock_image_usage, collect_stock_image_ids)
    from server.app.modules.articles.formatting.document import loads_content_json
    _ids = collect_stock_image_ids(loads_content_json(article.content_json))
    bump_stock_image_usage(db, _ids, article.id)
```
> 用 content 扫描而非 pick 时 bump：`run_ai_format` 内部多段选图，扫最终 content 的 `stockImageId` 是路径无关、最稳的落图口径。`loads_content_json` 的确切位置以 `hook.py` 里的 import 为准（同模块已用）。

- [ ] **Step 6: 跑测试确认通过 + 回归配图**

Run: `GEO_TEST_DATABASE_URL=... env python -m pytest server/tests/test_image_lru_usage.py -q`
Expected: PASS
Run: `GEO_TEST_DATABASE_URL=... env python -m pytest server/tests/ -q -k "illustrate or ai_format"`
Expected: 现有配图用例仍 PASS

- [ ] **Step 7: Commit**

```bash
git add server/app/modules/image_library/selector.py server/app/modules/image_library/service.py server/app/modules/articles/hook.py server/app/modules/articles/ai_illustrate_svc.py server/tests/test_image_lru_usage.py
git commit -m "feat(image-library): 软 LRU 选图 + 落图回写图片级用量

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: 文档 + writer skill handoff

**Files:**
- Modify: `CLAUDE.md`
- Create: `docs/superpowers/plans/2026-07-20-game-library-corpus-writer-handoff.md`（跨仓 handoff 备忘）

**Interfaces:** 无代码接口；文档定稿。

- [ ] **Step 1: 更新 `CLAUDE.md`**

- 后台线程清单（`create_app()` 段 / Gotchas 多进程 caveat）：加「游戏库定时入库（`game_library.scheduler.start_game_ingest`，开关 `GEO_GAME_INGEST_SCHEDULER_ENABLED`，与问题池同步/pipeline 调度并列，多实例 web 会各起一个线程——`upsert_game` 幂等无害但浪费）」。
- Domain Modules 段：加 `game_library/` 一节（games/game_tags + stock_images 扩列复用图片库当媒体底座；入库定时+CLI；检索 2 MCP 工具）。
- MCP「Tool 三组」catalog 列表 +2（`list_game_tags` / `query_games_by_tags`），`MCP_TOOLS_COUNT` 改 **35**。
- 路由清单加 `/api/mcp/game-library/*`。

- [ ] **Step 2: 写 writer skill handoff 备忘**（geo-goal 插件跨仓，不在本仓改）

内容要点：`geo-article-writer` 动笔前改为——① `list_game_tags()`；② 选 `relevant_tags`(1-2 相关,准入)+`diversity_tags`(几个多样)；③ `query_games_by_tags(relevant_tags, diversity_tags?, ...)`；④ 候选(只数 relevant 池) ≥ 阈值(默认 `>=4`)走库、否则回退 WebSearch；⑤ `report_event` 区分 `games_from_library`/`games_fallback_websearch`；⑥ `save_article(..., selected_games=[{game_id,name}])`（回退搜的游戏无 game_id、留空）。插件侧照此改 + 重发版。

- [ ] **Step 3: 全量回归 + Commit**

Run: `GEO_TEST_DATABASE_URL=... env python -m pytest server/tests/ -q -k "game or mcp or image or illustrate"`
Expected: 全绿

Run: `ruff check server/ && ruff format --check server/ && mypy server/app`
Expected: 无新增报错（CI 硬门禁）

```bash
git add CLAUDE.md docs/superpowers/plans/2026-07-20-game-library-corpus-writer-handoff.md
git commit -m "docs(game-library): CLAUDE.md 同步 + writer skill 跨仓 handoff 备忘

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review 记录

- **Spec 覆盖**：§三 表(Task 2)、`stock_images` 扩列(Task 2/3)、源不对称+get_detail(Task 1/7)、并集合并(Task 5)、检索 relevant/diversity(Task 6)、定时入库+CLI(Task 7/8)、MCP 工具+计数(Task 9)、游戏用量回写(Task 10)、图片用量+软 LRU(Task 11)、事务纪律(Task 3/5/7)、通用下载器(Task 4)、迁移(Task 2)、文档+handoff(Task 12)、已知限制(设计稿已登记，无需代码)。
- **类型一致**：`upsert_game(db, game, *, max_screenshots)`、`query_games_by_tags(db, relevant_tags, diversity_tags, exclude_tags, min_score, limit)`、`bump_game_usage(db, game_ids, article_id)`、`bump_stock_image_usage(db, image_ids, article_id)`、`store_image_bytes(..., commit=True)`、`download_image(url,*,timeout,max_bytes)` 全计划一致。
- **待实现者注意**：Task 9 `_apost` 若 catalog.py 无则从 action.py 复制；Task 10 `qid/tpl_id` fixture 复用现有 save-from-mcp 测试；Task 11 `loads_content_json` import 以 hook.py 现用为准。
