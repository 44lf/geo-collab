# 游戏库入库托管化 + 图库栏目迁移 + 前台改造 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把游戏库从「MCP-only 底座」升级为「可管理的托管抓取 + 图库栏目迁移 + 与 demo.pen 对齐的前台」,后端补齐浏览层/配置/抓取执行器,前端按 demo.pen 重构游戏库 tab。

**Architecture:** 共享 API 契约先锁(§契约),之后**后端核心(共享文件)**与**独立文件后端子流**、**前端两条流**并行推进。抓取执行器镜像 `accounts/keepalive.py`(每日窗+软 LRU+有界随机间隔);导入为幂等 CLI(栏目名→游戏名,FK `stock_category_id` 唯一关联);抓取按名刷新(wire 现有 `sources/*.search_by_name`),**仅针对 companion 栏目的游戏**(见 §Reconciliation)。

**Tech Stack:** FastAPI + SQLAlchemy/Alembic(MySQL)+ pydantic-settings;前端 React 19 + Vite + TS + Lucide;后端测试 pytest(`@pytest.mark.mysql`,需 `GEO_TEST_DATABASE_URL`);前端门禁 = typecheck + build(无单测)。

## Global Constraints

- 后端异常走命名异常(`ClientError`/`ConflictError`/`ValidationError`),**不抛裸 `ValueError`**(main.py 无兜底)。
- service 层函数默认 `commit: bool` 模式或不 commit(调用方 commit),对齐 `store_image_bytes` / `get_or_create_companion_category`。
- 迁移 down_revision = `0065_game_library`;新迁移 rev = `0066_game_ingest_config`。
- 测试库走 `Base.metadata.create_all`(`server/tests/utils.py`),ORM 约束要在模型上声明(否则测试环境漂移)。
- 前端 user JWT 走 cookie;API 客户端用现有 `web/src/api/` fetch 封装(非 axios)。
- `.pen` 只读参考,不改;视觉以 `demo.pen` 的 `游戏库桌面展示`(frame `uAkUP`)为准。
- 后端命令用 `env python`(conda activate 在工具 shell 不生效),测试库名须含 `test`。
- 提交尾行保留 `Co-Authored-By: <your model signature>`。

---

## Reconciliation:spec ↔ demo.pen(实现前必读)

| 点 | spec 原文 | demo.pen | 本计划采纳 |
|---|---|---|---|
| 抓取范围 | 软 LRU 轮转**全库** | 「仅用于陪衬库,主推游戏手动维护」+ 主推/陪衬分组切换 | **companion-only**:`select_due_games` 只取 `stock_categories.kind='companion'` 的游戏;主推(main)不进定时抓取 |
| 主推/陪衬 | 无区分 | 左侧「主推游戏 / 陪衬游戏」tab | 浏览层 `list_games` 加 `kind` 过滤(join stock_categories);game 的 kind = 其 stock_category 的 kind |
| 手动编辑/删除游戏 | 无 | 头部「编辑信息」「删除游戏」按钮 | **Track G(可选)**:PATCH/DELETE game;不做则前端按钮置灰。默认纳入 |
| 配置面板 | 设置弹窗 | 右上浮层摘要(开关+时间+配置按钮)+ 弹窗 | 浮层 = 摘要+快捷开关;「配置」按钮开完整弹窗 `GameIngestSettingsModal` |

> ⚠️ **companion-only 是对已批准 spec 的收窄**,若需求实为「全库轮转」,只需把 §契约 的 `select_due_games` 去掉 kind 过滤 + 前端分组切换改为纯浏览过滤。实现前请与需求方确认。

---

## 共享 API 契约(所有 track 对齐,先锁)

**新表 `game_ingest_config`(单例 id=1):**

| 列 | 类型 | 默认 |
|---|---|---|
| id | INT PK | 1 |
| enabled | BOOL | 0 |
| window_start | VARCHAR(5) | "03:00" |
| window_end | VARCHAR(5) | "06:00" |
| batch_size | INT | 30 |
| min_gap_seconds | INT | 20 |
| max_gap_seconds | INT | 90 |
| source_order | VARCHAR(50) | "taptap,baidu" |
| max_shots | INT | 6 |
| last_run_started_at | DATETIME NULL | NULL |
| last_run_finished_at | DATETIME NULL | NULL |
| last_run_summary | JSON NULL | NULL |
| last_run_trigger | VARCHAR(12) NULL | NULL |
| updated_at | DATETIME | now / onupdate |

**端点(全部 user JWT,挂 `game_library_web_router`,prefix `/api/game-library`):**

| 方法 | 路径 | 请求 | 响应 |
|---|---|---|---|
| GET | `/tags` | `?limit` | `list[GameTagOut]` |
| GET | `/games` | `?tag&min_score&q&kind&limit&offset` | `GameListResponse` |
| GET | `/games/{id}` | — | `GameDetail`(404 若无) |
| POST | `/import-image-categories` | `ImageCategoryImportRequest` | `ImageCategoryImportResponse` |
| GET | `/ingest/config` | — | `GameIngestConfigRead` |
| PATCH | `/ingest/config` | `GameIngestConfigPatch` | `GameIngestConfigRead` |
| POST | `/ingest/run` | — | 202 `GameIngestRunStartResponse` / 409 若在跑 |
| PATCH | `/games/{id}`(Track G) | `GameUpdateRequest` | `GameDetail` |
| DELETE | `/games/{id}`(Track G) | — | 204 |

**Schema 形状(pydantic,`server/app/modules/game_library/schemas.py`):**

```
GameListItem: game_id:int, name:str, score:float|None, tags:list[str], icon_url:str|None,
              screenshot_count:int, use_count:int, last_used_at:str|None,
              stock_category_id:int|None, sources:list[str], kind:str|None
GameListResponse: items:list[GameListItem], total:int
GameDetail: game_id, name, name_normalized, score, comment_count, tags:list[str],
            platforms:list[str], sources:list, icon_url, screenshot_urls:list[str],
            description, stock_category_id, kind:str|None, use_count, last_used_at,
            last_used_article_id, first_seen_at, last_verified_at,
            highlight_comments, related_hotspots, is_active
GameIngestConfigRead: enabled:bool, window_start:str, window_end:str, batch_size:int,
            min_gap_seconds:int, max_gap_seconds:int, source_order:str, max_shots:int,
            running:bool, last_run_started_at:str|None, last_run_finished_at:str|None,
            last_run_summary:dict|None, last_run_trigger:str|None
GameIngestConfigPatch: 上述 8 个可写字段全 Optional(HH:MM 校验、min≤max、正整数)
ImageCategoryImportRequest: kind:str|None=None(main/companion/None=全部), only_with_images:bool=False, limit:int|None=None
ImageCategoryImportResponse: scanned:int, created:int, attached:int, skipped:int
GameIngestRunStartResponse: started:bool, status:GameIngestConfigRead
GameUpdateRequest(Track G): name:str|None, score:float|None, description:str|None, tags:list[str]|None
```

**service 层函数签名(契约,跨 track 引用):**

```
# browse（Track A / service.py）
list_games(db, *, tag=None, min_score=None, q=None, kind=None, is_active=True, limit=50, offset=0) -> dict{items,total}
get_game(db, game_id) -> dict | None

# upsert 重构（Track A / service.py）
upsert_game(db, game, *, max_screenshots=6, pre_downloaded=None, category_id=None) -> Game

# ingest config（Track A / ingest_service.py）
get_or_create_ingest_config(db) -> GameIngestConfig
update_ingest_config(db, patch: dict) -> GameIngestConfig
ingest_config_to_dict(cfg, *, running: bool) -> dict

# importer（Track B1 / importer.py）
import_image_categories_as_games(db, *, kind=None, only_with_images=False, limit=None) -> dict{scanned,created,attached,skipped}

# sources（Track B2 / sources/*.py）
search_by_name(name: str) -> types.Game | None   # baidu + taptap 各一

# executor（Track B3 / scheduler.py + ingest_service.py）
select_due_games(db, *, limit) -> list[int]       # companion-only, last_verified_at ASC NULLS FIRST
refresh_one_game(session_factory, game_id, *, source_order, max_shots) -> str  # 'refreshed'|'not_found'|'error'
start_configured_ingest(session_factory, *, trigger) -> bool   # 手动/定时启动一批，进程内锁
is_configured_ingest_running() -> bool
```

---

## File Map & Ownership

| 文件 | 动作 | Track |
|---|---|---|
| `server/app/modules/game_library/models.py` | +`GameIngestConfig` | A |
| `server/alembic/versions/0066_game_ingest_config.py` | 新建 | A |
| `server/app/modules/game_library/schemas.py` | +浏览/配置/导入/run 全部 schema | A |
| `server/app/modules/game_library/service.py` | 恢复 browse(+kind)+ upsert 重构 | A |
| `server/app/modules/game_library/router_web.py` | 新建:browse+import+config+run(+G) | A |
| `server/app/main.py` | 挂 web router + 注入 bg_session_factory | A |
| `server/app/modules/game_library/ingest_service.py` | 新建:config CRUD + select_due_games + refresh_one_game | A(config)/B3(executor) |
| `server/app/modules/game_library/importer.py` + `server/scripts/import_image_library_games.py` | 新建 | B1 |
| `server/app/modules/game_library/sources/baidu.py` / `taptap.py` | wire `search_by_name` | B2 |
| `server/app/modules/game_library/scheduler.py` | 重写为 config-driven 批量 loop | B3 |
| `server/app/modules/image_library/models.py` | +`StockImage` UNIQUE 声明 | A(随 upsert) |
| `web/src/api/game-library.ts` | +browse/config/run/import 客户端 | F(browse)/G(ingest) |
| `web/src/types.ts` | +`GameIngestConfig` 等类型 | F/G |
| `web/src/features/game-library/*.tsx` | 对齐 demo.pen | F/G |
| `web/src/features/game-library/GameIngestSettingsModal.tsx` | 新建 | G |
| `server/tests/test_game_web.py` / `test_game_ingest_*.py` / `test_import_*.py` | 新建 | 各 track |

---

## Dependency DAG / 并行波次

```
Wave 0 (立即并行，互不碰文件):
  ┌─ Track A  后端核心(共享文件: models/migration/schemas/service/router_web/main) —— 内部顺序执行
  ├─ Track B1 importer.py + CLI            (独立文件, 只依赖 models.Game)
  ├─ Track B2 sources.search_by_name       (独立文件 baidu/taptap)
  ├─ Track F  前端浏览层(对齐 mockup)      (对契约编码, 落地后接 A 的 /games)
  └─ Track G  前端 ingest UI               (对契约编码, 落地后接 A 的 /ingest/*)

Wave 1 (依赖 Wave 0):
  └─ Track B3 抓取执行器  依赖 A(config+upsert) + B2(search_by_name)

Wave 2 (可选):
  └─ Track H  手动编辑/删除游戏  依赖 A
```

**并行说明:** A / B1 / B2 / F / G 五条可**同时开工**(前端对 §契约 编码、后端 B1/B2 是独立文件)。A 内部因共享 service.py/schemas.py 顺序执行。B3 等 A+B2。集成点:F/G 在 A 落地后连真接口联调。

---

## Track A — 后端核心(共享文件,顺序执行)

### Task A1: `GameIngestConfig` 模型 + 迁移 0066

**Files:**
- Modify: `server/app/modules/game_library/models.py`(追加类)
- Create: `server/alembic/versions/0066_game_ingest_config.py`
- Test: `server/tests/test_game_ingest_config.py`

**Interfaces:**
- Produces: `GameIngestConfig` ORM(表 `game_ingest_config`,单例 id=1)。

- [ ] **Step 1: 写失败测试**

```python
# server/tests/test_game_ingest_config.py
import pytest


@pytest.mark.mysql
def test_ingest_config_singleton_defaults(monkeypatch):
    from server.tests.utils import build_test_app
    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.game_library import ingest_service
        s = app.session_factory()
        try:
            cfg = ingest_service.get_or_create_ingest_config(s)
            s.commit()
            assert cfg.id == 1
            assert cfg.enabled is False
            assert cfg.window_start == "03:00" and cfg.window_end == "06:00"
            assert cfg.batch_size == 30
            assert cfg.source_order == "taptap,baidu"
            # 再取一次仍是同一行（单例）
            again = ingest_service.get_or_create_ingest_config(s)
            assert again.id == 1
        finally:
            s.close()
    finally:
        app.cleanup()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `env GEO_TEST_DATABASE_URL=... env python -m pytest server/tests/test_game_ingest_config.py -q`
Expected: FAIL(`GameIngestConfig` / `ingest_service` 不存在)

- [ ] **Step 3: 加模型**

```python
# 追加到 server/app/modules/game_library/models.py
class GameIngestConfig(Base):
    __tablename__ = "game_ingest_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    window_start: Mapped[str] = mapped_column(String(5), nullable=False, server_default="03:00")
    window_end: Mapped[str] = mapped_column(String(5), nullable=False, server_default="06:00")
    batch_size: Mapped[int] = mapped_column(Integer, nullable=False, server_default="30")
    min_gap_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default="20")
    max_gap_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default="90")
    source_order: Mapped[str] = mapped_column(String(50), nullable=False, server_default="taptap,baidu")
    max_shots: Mapped[int] = mapped_column(Integer, nullable=False, server_default="6")
    last_run_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_run_finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_run_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    last_run_trigger: Mapped[str | None] = mapped_column(String(12), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
```

- [ ] **Step 4: 写迁移 0066**

```python
# server/alembic/versions/0066_game_ingest_config.py
"""game_ingest_config singleton"""
from alembic import op
import sqlalchemy as sa

revision = "0066_game_ingest_config"
down_revision = "0065_game_library"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "game_ingest_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("window_start", sa.String(5), nullable=False, server_default="03:00"),
        sa.Column("window_end", sa.String(5), nullable=False, server_default="06:00"),
        sa.Column("batch_size", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("min_gap_seconds", sa.Integer(), nullable=False, server_default="20"),
        sa.Column("max_gap_seconds", sa.Integer(), nullable=False, server_default="90"),
        sa.Column("source_order", sa.String(50), nullable=False, server_default="taptap,baidu"),
        sa.Column("max_shots", sa.Integer(), nullable=False, server_default="6"),
        sa.Column("last_run_started_at", sa.DateTime(), nullable=True),
        sa.Column("last_run_finished_at", sa.DateTime(), nullable=True),
        sa.Column("last_run_summary", sa.JSON(), nullable=True),
        sa.Column("last_run_trigger", sa.String(12), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.execute("INSERT INTO game_ingest_config (id, updated_at) VALUES (1, NOW())")


def downgrade() -> None:
    op.drop_table("game_ingest_config")
```

- [ ] **Step 5: 写 `ingest_service.get_or_create_ingest_config`**(见 Task A2,先落最小版让测试过)

```python
# server/app/modules/game_library/ingest_service.py（新建，本 task 先放这一函数）
from __future__ import annotations
from sqlalchemy.orm import Session
from server.app.modules.game_library.models import GameIngestConfig


def get_or_create_ingest_config(db: Session) -> GameIngestConfig:
    cfg = db.get(GameIngestConfig, 1)
    if cfg is None:
        cfg = GameIngestConfig(id=1)
        db.add(cfg)
        db.flush()
    return cfg
```

- [ ] **Step 6: 跑测试确认通过**

Run: `env python -m pytest server/tests/test_game_ingest_config.py::test_ingest_config_singleton_defaults -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add server/app/modules/game_library/models.py server/alembic/versions/0066_game_ingest_config.py server/app/modules/game_library/ingest_service.py server/tests/test_game_ingest_config.py
git commit -m "feat(game-library): game_ingest_config 单例表 + 迁移 0066"
```

### Task A2: config CRUD service + schema

**Files:**
- Modify: `server/app/modules/game_library/ingest_service.py`(+`update_ingest_config`/`ingest_config_to_dict`)
- Modify: `server/app/modules/game_library/schemas.py`(+config schema)
- Test: `server/tests/test_game_ingest_config.py`(+CRUD 用例)

**Interfaces:**
- Consumes: `get_or_create_ingest_config`(A1)。
- Produces: `update_ingest_config(db, patch)` / `ingest_config_to_dict(cfg, *, running)`;`GameIngestConfigRead` / `GameIngestConfigPatch`。

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.mysql
def test_update_ingest_config_validates(monkeypatch):
    from server.tests.utils import build_test_app
    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.game_library import ingest_service
        from server.app.shared.errors import ValidationError
        s = app.session_factory()
        try:
            cfg = ingest_service.update_ingest_config(
                s, {"enabled": True, "window_start": "02:00", "batch_size": 10}
            )
            s.commit()
            assert cfg.enabled is True and cfg.window_start == "02:00" and cfg.batch_size == 10
            d = ingest_service.ingest_config_to_dict(cfg, running=False)
            assert d["enabled"] is True and d["running"] is False and d["batch_size"] == 10
            with pytest.raises(ValidationError):
                ingest_service.update_ingest_config(s, {"window_start": "9am"})
            with pytest.raises(ValidationError):
                ingest_service.update_ingest_config(s, {"min_gap_seconds": 100, "max_gap_seconds": 10})
        finally:
            s.close()
    finally:
        app.cleanup()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `env python -m pytest server/tests/test_game_ingest_config.py::test_update_ingest_config_validates -q`
Expected: FAIL

- [ ] **Step 3: 实现 CRUD + 校验**

```python
# 追加到 ingest_service.py
import re
from server.app.shared.errors import ValidationError

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_WRITABLE = {
    "enabled", "window_start", "window_end", "batch_size",
    "min_gap_seconds", "max_gap_seconds", "source_order", "max_shots",
}


def update_ingest_config(db, patch: dict) -> "GameIngestConfig":
    cfg = get_or_create_ingest_config(db)
    data = {k: v for k, v in (patch or {}).items() if k in _WRITABLE}
    for key in ("window_start", "window_end"):
        if key in data and not _HHMM.match(str(data[key])):
            raise ValidationError(f"{key} 必须是 HH:MM")
    for key in ("batch_size", "min_gap_seconds", "max_gap_seconds", "max_shots"):
        if key in data and int(data[key]) <= 0:
            raise ValidationError(f"{key} 必须为正整数")
    lo = int(data.get("min_gap_seconds", cfg.min_gap_seconds))
    hi = int(data.get("max_gap_seconds", cfg.max_gap_seconds))
    if lo > hi:
        raise ValidationError("min_gap_seconds 不能大于 max_gap_seconds")
    for k, v in data.items():
        setattr(cfg, k, v)
    db.flush()
    return cfg


def _iso(dt):
    return dt.isoformat() if dt else None


def ingest_config_to_dict(cfg, *, running: bool) -> dict:
    return {
        "enabled": cfg.enabled,
        "window_start": cfg.window_start,
        "window_end": cfg.window_end,
        "batch_size": cfg.batch_size,
        "min_gap_seconds": cfg.min_gap_seconds,
        "max_gap_seconds": cfg.max_gap_seconds,
        "source_order": cfg.source_order,
        "max_shots": cfg.max_shots,
        "running": running,
        "last_run_started_at": _iso(cfg.last_run_started_at),
        "last_run_finished_at": _iso(cfg.last_run_finished_at),
        "last_run_summary": cfg.last_run_summary,
        "last_run_trigger": cfg.last_run_trigger,
    }
```

- [ ] **Step 4: 加 schema**

```python
# 追加到 schemas.py
class GameIngestConfigRead(BaseModel):
    enabled: bool
    window_start: str
    window_end: str
    batch_size: int
    min_gap_seconds: int
    max_gap_seconds: int
    source_order: str
    max_shots: int
    running: bool = False
    last_run_started_at: str | None = None
    last_run_finished_at: str | None = None
    last_run_summary: dict | None = None
    last_run_trigger: str | None = None


class GameIngestConfigPatch(BaseModel):
    enabled: bool | None = None
    window_start: str | None = None
    window_end: str | None = None
    batch_size: int | None = Field(default=None, ge=1)
    min_gap_seconds: int | None = Field(default=None, ge=1)
    max_gap_seconds: int | None = Field(default=None, ge=1)
    source_order: str | None = None
    max_shots: int | None = Field(default=None, ge=1)
```

- [ ] **Step 5: 跑测试确认通过 + Commit**

```bash
env python -m pytest server/tests/test_game_ingest_config.py -q
git add -A && git commit -m "feat(game-library): ingest 配置 CRUD + 校验 + schema"
```

### Task A3: 恢复浏览层 + kind 过滤(service + schema)

**Files:**
- Modify: `server/app/modules/game_library/service.py`(补 browse 函数 + kind)
- Modify: `server/app/modules/game_library/schemas.py`(+`GameListItem`/`GameListResponse`/`GameDetail`)
- Test: `server/tests/test_game_web.py`(见下 A5 整合)

**Interfaces:**
- Produces: `list_games(db, *, tag, min_score, q, kind, is_active, limit, offset)`;`get_game(db, game_id)`;`GameListItem`/`GameListResponse`/`GameDetail`。

- [ ] **Step 1: service.py import 补 `ColumnElement` + join 依赖**

```python
# service.py 顶部
from sqlalchemy import ColumnElement, func, select
from server.app.modules.image_library.models import StockCategory  # kind 过滤用
```

- [ ] **Step 2: 追加 browse 函数(在 `query_games_by_tags` 之后、`bump_game_usage` 之前)**

```python
def _iso(dt):
    return dt.isoformat() if dt else None


def _source_names(sources) -> list[str]:
    names: list[str] = []
    for entry in sources or []:
        name = entry.get("source") if isinstance(entry, dict) else None
        if name and name not in names:
            names.append(name)
    return sorted(names)


def _kind_of(db: Session, stock_category_id: int | None) -> str | None:
    if stock_category_id is None:
        return None
    cat = db.get(StockCategory, stock_category_id)
    return cat.kind if cat is not None else None


def _game_to_list_item(db: Session, g: Game) -> dict:
    return {
        "game_id": g.id,
        "name": g.name,
        "score": g.score,
        "tags": [t.tag for t in g.tags],
        "icon_url": g.icon_url,
        "screenshot_count": len(g.screenshot_urls or []),
        "use_count": g.use_count,
        "last_used_at": _iso(g.last_used_at),
        "stock_category_id": g.stock_category_id,
        "sources": _source_names(g.sources),
        "kind": _kind_of(db, g.stock_category_id),
    }


def _game_to_detail(db: Session, g: Game) -> dict:
    return {
        "game_id": g.id, "name": g.name, "name_normalized": g.name_normalized,
        "score": g.score, "comment_count": g.comment_count,
        "tags": [t.tag for t in g.tags], "platforms": g.platforms or [],
        "sources": g.sources or [], "icon_url": g.icon_url,
        "screenshot_urls": g.screenshot_urls or [], "description": g.description,
        "stock_category_id": g.stock_category_id, "kind": _kind_of(db, g.stock_category_id),
        "use_count": g.use_count, "last_used_at": _iso(g.last_used_at),
        "last_used_article_id": g.last_used_article_id,
        "first_seen_at": _iso(g.first_seen_at), "last_verified_at": _iso(g.last_verified_at),
        "highlight_comments": g.highlight_comments, "related_hotspots": g.related_hotspots,
        "is_active": g.is_active,
    }


def list_games(db, *, tag=None, min_score=None, q=None, kind=None, is_active=True, limit=50, offset=0) -> dict:
    conds: list[ColumnElement[bool]] = []
    if is_active:
        conds.append(Game.is_active.is_(True))
    if tag:
        conds.append(Game.id.in_(select(GameTag.game_id).where(GameTag.tag == tag)))
    if min_score is not None:
        conds.append(Game.score >= min_score)
    if q:
        conds.append(Game.name.like(f"%{q}%"))
    if kind:
        conds.append(Game.stock_category_id.in_(
            select(StockCategory.id).where(StockCategory.kind == kind)
        ))
    count_stmt = select(func.count()).select_from(Game)
    if conds:
        count_stmt = count_stmt.where(*conds)
    total = int(db.execute(count_stmt).scalar_one())
    stmt = select(Game).options(selectinload(Game.tags))
    if conds:
        stmt = stmt.where(*conds)
    stmt = stmt.order_by(func.coalesce(Game.score, -1).desc(), Game.id.asc()).limit(
        max(1, min(200, limit))
    ).offset(max(0, offset))
    rows = list(db.execute(stmt).scalars().all())
    return {"items": [_game_to_list_item(db, g) for g in rows], "total": total}


def get_game(db, game_id: int) -> dict | None:
    g = db.execute(
        select(Game).options(selectinload(Game.tags)).where(Game.id == game_id)
    ).scalar_one_or_none()
    return _game_to_detail(db, g) if g is not None else None
```

- [ ] **Step 3: 加 schema(GameListItem/GameListResponse/GameDetail,含 `kind`)**

```python
class GameListItem(BaseModel):
    game_id: int; name: str; score: float | None = None
    tags: list[str] = Field(default_factory=list); icon_url: str | None = None
    screenshot_count: int = 0; use_count: int = 0; last_used_at: str | None = None
    stock_category_id: int | None = None; sources: list[str] = Field(default_factory=list)
    kind: str | None = None


class GameListResponse(BaseModel):
    items: list[GameListItem] = Field(default_factory=list); total: int = 0


class GameDetail(BaseModel):
    game_id: int; name: str; name_normalized: str
    score: float | None = None; comment_count: int | None = None
    tags: list[str] = Field(default_factory=list); platforms: list[str] = Field(default_factory=list)
    sources: list = Field(default_factory=list); icon_url: str | None = None
    screenshot_urls: list[str] = Field(default_factory=list); description: str | None = None
    stock_category_id: int | None = None; kind: str | None = None
    use_count: int = 0; last_used_at: str | None = None; last_used_article_id: int | None = None
    first_seen_at: str | None = None; last_verified_at: str | None = None
    highlight_comments: list | None = None; related_hotspots: list | None = None
    is_active: bool = True
```

- [ ] **Step 4: Commit**(测试在 A5 一并跑)

```bash
git add server/app/modules/game_library/service.py server/app/modules/game_library/schemas.py
git commit -m "feat(game-library): 恢复浏览层 service + kind 过滤 + 浏览 schema"
```

### Task A4: `upsert_game` 重构(下载搬出事务 + category_id + 竞态兜底)

**Files:**
- Modify: `server/app/modules/game_library/service.py`(`upsert_game` 签名+实现)
- Modify: `server/app/modules/image_library/models.py`(+`StockImage` UNIQUE)
- Test: `server/tests/test_game_upsert.py`(补 category_id / pre_downloaded / IntegrityError)

**Interfaces:**
- Produces: `upsert_game(db, game, *, max_screenshots=6, pre_downloaded=None, category_id=None) -> Game`。

- [ ] **Step 1: 写失败测试(传 category_id 不 re-resolve 栏目)**

```python
@pytest.mark.mysql
def test_upsert_with_category_id_skips_name_resolve(monkeypatch):
    from server.tests.utils import build_test_app
    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.game_library import service, types
        from server.app.modules.image_library.models import StockCategory
        s = app.session_factory()
        try:
            cat = StockCategory(name="餐厅养成记", bucket_name="canting", kind="companion")
            s.add(cat); s.flush()
            g = types.Game(source="taptap", game_id="1", name="餐厅养成记", tags=["经营"],
                           score=8.0, screenshot_urls=[])
            row = service.upsert_game(s, g, category_id=cat.id, pre_downloaded=[])
            s.commit()
            assert row.stock_category_id == cat.id
        finally:
            s.close()
    finally:
        app.cleanup()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `env python -m pytest server/tests/test_game_upsert.py::test_upsert_with_category_id_skips_name_resolve -q`
Expected: FAIL(签名不接受 `category_id`/`pre_downloaded`)

- [ ] **Step 3: 重构 upsert_game**

```python
def upsert_game(db, game, *, max_screenshots=6, pre_downloaded=None, category_id=None):
    """跨源并集合并;下载已在 session 外做好经 pre_downloaded 传入,session 内只写库。
    category_id 非空 = 直接用该桶,不按名 re-resolve(迁移集游戏)。"""
    shots = pre_downloaded if pre_downloaded is not None else _download_screenshots(
        game.screenshot_urls, max_screenshots
    )
    norm = _normalize_game_name(game.name) or game.name
    if category_id is not None:
        cat = db.get(StockCategory, category_id)
    else:
        cat = get_or_create_companion_category(db, game.name, commit=False)
    row = _get_or_create_game_row(db, game, norm)
    row.score = _merge_scalar_max(row.score, game.score)
    row.comment_count = _merge_scalar_max(row.comment_count, game.comment_count)
    row.description = _prefer_longer(row.description, game.description)
    row.icon_url = _prefer_longer(row.icon_url, game.icon_url)
    src_entry = {"source": game.source, "source_game_id": game.game_id}
    row.sources = list(row.sources or [])
    if src_entry not in row.sources:
        row.sources = row.sources + [src_entry]
    row.platforms = sorted(set((row.platforms or []) + list(game.platforms or [])))
    row.screenshot_urls = list(dict.fromkeys((row.screenshot_urls or []) + list(game.screenshot_urls or [])))
    existing_tags = {t.tag for t in db.query(GameTag).filter(GameTag.game_id == row.id)}
    for tag in game.tags or []:
        if tag and tag not in existing_tags:
            _add_game_tag_if_missing(db, row.id, tag)
            existing_tags.add(tag)
    if cat is not None:
        row.stock_category_id = cat.id
        for url, data, mime in shots:
            store_image_bytes(db, cat, data, mime, source_url=url, commit=False)
    row.last_verified_at = utcnow()
    db.flush()
    return row
```

- [ ] **Step 4: `StockImage` 补 UNIQUE 声明(M4)**

```python
# server/app/modules/image_library/models.py — StockImage.__table_args__
__table_args__ = (
    UniqueConstraint("category_id", "source_url_hash", name="uq_stock_images_category_source_hash"),
)
```

- [ ] **Step 5: 跑测试确认通过 + Commit**

```bash
env python -m pytest server/tests/test_game_upsert.py -q
git add -A && git commit -m "refactor(game-library): upsert 下载搬出事务 + category_id + StockImage UNIQUE(C2/M1/M4)"
```

### Task A5: `router_web.py` 全端点 + main.py 挂载 + web 测试

**Files:**
- Create: `server/app/modules/game_library/router_web.py`
- Modify: `server/app/main.py`
- Test: `server/tests/test_game_web.py`

**Interfaces:**
- Consumes: A2/A3(service+ingest_service)、B1(importer,先 stub)、B3(executor,先 stub)。
- Produces: `game_library_web_router`;端点见 §契约。

> `bg_session_factory` 注入模式沿用现有:在 router_web 模块级 `bg_session_factory=None`,main.py 启动时赋值。

- [ ] **Step 1: 写 router_web.py(browse 立即可用;import/config/run 调 ingest_service + importer + scheduler)**

```python
"""游戏库前台(user JWT)接口:浏览 + 图库导入 + 抓取配置/触发。"""
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from server.app.core.security import get_current_user
from server.app.db.session import get_db
from server.app.modules.game_library import service, ingest_service
from server.app.modules.game_library.schemas import (
    GameDetail, GameListResponse, GameTagOut,
    GameIngestConfigRead, GameIngestConfigPatch,
    ImageCategoryImportRequest, ImageCategoryImportResponse, GameIngestRunStartResponse,
)

bg_session_factory: Any = None

game_library_web_router = APIRouter(
    prefix="/api/game-library", tags=["game-library"],
    dependencies=[Depends(get_current_user)],
)


@game_library_web_router.get("/tags", response_model=list[GameTagOut])
def web_list_game_tags(limit: int = Query(200, ge=1, le=1000), db: Session = Depends(get_db)):
    return service.list_game_tags(db, limit=limit)


@game_library_web_router.get("/games", response_model=GameListResponse)
def web_list_games(tag: str | None = None, min_score: float | None = None, q: str | None = None,
                   kind: str | None = None, limit: int = Query(50, ge=1, le=200),
                   offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    return service.list_games(db, tag=tag, min_score=min_score, q=q, kind=kind, limit=limit, offset=offset)


@game_library_web_router.get("/games/{game_id}", response_model=GameDetail)
def web_get_game(game_id: int, db: Session = Depends(get_db)):
    g = service.get_game(db, game_id)
    if g is None:
        raise HTTPException(status_code=404, detail="游戏不存在")
    return g


@game_library_web_router.post("/import-image-categories", response_model=ImageCategoryImportResponse)
def web_import_image_categories(payload: ImageCategoryImportRequest, db: Session = Depends(get_db)):
    from server.app.modules.game_library import importer
    result = importer.import_image_categories_as_games(
        db, kind=payload.kind, only_with_images=payload.only_with_images, limit=payload.limit
    )
    db.commit()
    return result


@game_library_web_router.get("/ingest/config", response_model=GameIngestConfigRead)
def web_get_ingest_config(db: Session = Depends(get_db)):
    from server.app.modules.game_library import scheduler
    cfg = ingest_service.get_or_create_ingest_config(db)
    db.commit()
    return ingest_service.ingest_config_to_dict(cfg, running=scheduler.is_configured_ingest_running())


@game_library_web_router.patch("/ingest/config", response_model=GameIngestConfigRead)
def web_patch_ingest_config(payload: GameIngestConfigPatch, db: Session = Depends(get_db)):
    from server.app.modules.game_library import scheduler
    cfg = ingest_service.update_ingest_config(db, payload.model_dump(exclude_unset=True))
    db.commit()
    if cfg.enabled and bg_session_factory is not None:
        scheduler.start_game_ingest(bg_session_factory)
    return ingest_service.ingest_config_to_dict(cfg, running=scheduler.is_configured_ingest_running())


@game_library_web_router.post("/ingest/run", response_model=GameIngestRunStartResponse, status_code=202)
def web_start_ingest_run(db: Session = Depends(get_db)):
    from server.app.modules.game_library import scheduler
    if bg_session_factory is None:
        raise HTTPException(status_code=503, detail="ingest executor 未就绪")
    if scheduler.is_configured_ingest_running():
        cfg = ingest_service.get_or_create_ingest_config(db); db.commit()
        raise HTTPException(status_code=409, detail="抓取正在进行中")
    started = scheduler.start_configured_ingest(bg_session_factory, trigger="manual")
    cfg = ingest_service.get_or_create_ingest_config(db); db.commit()
    return {"started": started,
            "status": ingest_service.ingest_config_to_dict(cfg, running=scheduler.is_configured_ingest_running())}
```

- [ ] **Step 2: 加 import/run schema**

```python
# schemas.py
class ImageCategoryImportRequest(BaseModel):
    kind: str | None = None
    only_with_images: bool = False
    limit: int | None = None


class ImageCategoryImportResponse(BaseModel):
    scanned: int = 0; created: int = 0; attached: int = 0; skipped: int = 0


class GameIngestRunStartResponse(BaseModel):
    started: bool
    status: GameIngestConfigRead
```

- [ ] **Step 3: main.py 挂载 + 注入 bg_session_factory**

```python
# main.py：在 game_library_mcp_router include 之后
from server.app.modules.game_library.router_web import game_library_web_router
from server.app.modules.game_library import router_web as _gl_web
_gl_web.bg_session_factory = SessionLocal
app.include_router(game_library_web_router)
```

- [ ] **Step 4: web 测试(browse happy-path + 401 + kind 过滤)**

（复用 §Reconciliation 恢复的 `test_game_web.py` 用例,加一条 `kind="companion"` 过滤断言;import/config/run 端点用例待 B1/B3 落地后补,本 task 先测 browse。）

- [ ] **Step 5: 跑测试 + Commit**

```bash
env python -m pytest server/tests/test_game_web.py -q
git add -A && git commit -m "feat(game-library): web router 全端点 + main 挂载 + browse 测试"
```

---

## Track B1 — 图库栏目导入(独立文件,可并行)

### Task B1: `importer.py` + CLI + 测试

**Files:**
- Create: `server/app/modules/game_library/importer.py`
- Create: `server/scripts/import_image_library_games.py`
- Test: `server/tests/test_import_image_library_games.py`

**Interfaces:**
- Produces: `import_image_categories_as_games(db, *, kind=None, only_with_images=False, limit=None) -> dict`。

- [ ] **Step 1: 写失败测试(建/attach/去重/幂等)**

```python
@pytest.mark.mysql
def test_import_creates_and_is_idempotent(monkeypatch):
    from server.tests.utils import build_test_app
    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.game_library import importer
        from server.app.modules.game_library.models import Game
        from server.app.modules.image_library.models import StockCategory
        s = app.session_factory()
        try:
            s.add(StockCategory(name="餐厅养成记", bucket_name="cant", kind="companion"))
            s.add(StockCategory(name="心动小镇", bucket_name="xin", kind="main"))
            s.flush()
            r1 = importer.import_image_categories_as_games(s); s.commit()
            assert r1["created"] == 2
            names = {g.name for g in s.query(Game).all()}
            assert names == {"餐厅养成记", "心动小镇"}
            r2 = importer.import_image_categories_as_games(s); s.commit()
            assert r2["created"] == 0 and r2["skipped"] == 2  # 幂等
        finally:
            s.close()
    finally:
        app.cleanup()
```

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 实现 importer**

```python
# server/app/modules/game_library/importer.py
from __future__ import annotations
import logging
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from server.app.core.time import utcnow
from server.app.modules.articles.formatting.document import _normalize_game_name
from server.app.modules.game_library.models import Game
from server.app.modules.image_library.models import StockCategory, StockImage

logger = logging.getLogger(__name__)


def import_image_categories_as_games(db: Session, *, kind=None, only_with_images=False, limit=None) -> dict:
    q = db.query(StockCategory)
    if kind:
        q = q.filter(StockCategory.kind == kind)
    if only_with_images:
        with_img = select(StockImage.category_id).distinct().subquery()
        q = q.filter(StockCategory.id.in_(select(with_img.c.category_id)))
    if limit:
        q = q.limit(int(limit))
    scanned = created = attached = skipped = 0
    for cat in q.all():
        scanned += 1
        norm = _normalize_game_name(cat.name) or cat.name
        game = db.query(Game).filter(Game.name_normalized == norm).first()
        if game is None:
            nested = db.begin_nested()
            try:
                db.add(Game(name=cat.name, name_normalized=norm, stock_category_id=cat.id,
                            sources=[], platforms=[], screenshot_urls=[], use_count=0,
                            is_active=True, first_seen_at=utcnow()))
                db.flush(); nested.commit(); created += 1
            except IntegrityError:
                nested.rollback(); skipped += 1
        elif game.stock_category_id is None:
            game.stock_category_id = cat.id; attached += 1
        else:
            skipped += 1
    db.flush()
    return {"scanned": scanned, "created": created, "attached": attached, "skipped": skipped}
```

- [ ] **Step 4: CLI**

```python
# server/scripts/import_image_library_games.py
from server.app.db.session import SessionLocal
from server.app.modules.game_library.importer import import_image_categories_as_games


def main():
    db = SessionLocal()
    try:
        r = import_image_categories_as_games(db)
        db.commit()
        print(f"导入完成: scanned={r['scanned']} created={r['created']} attached={r['attached']} skipped={r['skipped']}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: 跑测试 + Commit**

```bash
env python -m pytest server/tests/test_import_image_library_games.py -q
git add -A && git commit -m "feat(game-library): 图库栏目→游戏 幂等导入 CLI"
```

---

## Track B2 — sources.search_by_name(独立文件,可并行)

### Task B2: wire `search_by_name` + 测试

**Files:**
- Modify: `server/app/modules/game_library/sources/baidu.py` / `taptap.py`(确保 `search_by_name(name)->types.Game|None` 可用)
- Test: `server/tests/test_game_sources.py`(mock HTTP,断言字段映射)

**Interfaces:**
- Produces: `baidu.search_by_name(name)` / `taptap.search_by_name(name)`,返回 `types.Game | None`。

> 现状:两个 `search_by_name` 是死代码。taptap 侧走 `_bootstrap_xsrf`/`_multipart_encode`,可能脆;先写用例跑通,不稳则文档标注 baidu 优先(见 §Reconciliation 风险)。

- [ ] **Step 1: 写测试(mock 各源的底层 HTTP,断言返回 types.Game 字段)**

```python
@pytest.mark.mysql
def test_baidu_search_by_name_maps_fields(monkeypatch):
    from server.app.modules.game_library.sources import baidu
    monkeypatch.setattr(baidu, "_http_json", lambda *a, **k: {  # 按实际底层函数名替换
        "data": {"list": [{"name": "餐厅养成记", "score": 8.0, "tags": ["经营"], "id": "1"}]}
    })
    g = baidu.search_by_name("餐厅养成记")
    assert g is not None and g.name == "餐厅养成记" and "经营" in (g.tags or [])
```

- [ ] **Step 2: 跑测试确认失败/暴露真实签名**（据实调整 mock 目标 + 字段映射）

- [ ] **Step 3: 修正 `search_by_name` 使其返回 `types.Game`**（对齐 `_to_game_from_query`/`_to_game`;taptap 补 `get_detail` 缺图路径）

- [ ] **Step 4: 跑测试 + Commit**

```bash
env python -m pytest server/tests/test_game_sources.py -q
git add -A && git commit -m "feat(game-library): wire sources.search_by_name(按名精确查)"
```

---

## Track B3 — 抓取执行器(Wave 1,依赖 A+B2)

### Task B3.1: `select_due_games`(companion-only)+ `refresh_one_game`

**Files:**
- Modify: `server/app/modules/game_library/ingest_service.py`
- Test: `server/tests/test_game_ingest_batch.py`

**Interfaces:**
- Consumes: `upsert_game(...,category_id=...)`(A4)、`sources.search_by_name`(B2)、`get_or_create_ingest_config`(A1)。
- Produces: `select_due_games(db, *, limit)`;`refresh_one_game(session_factory, game_id, *, source_order, max_shots) -> str`。

- [ ] **Step 1: 写失败测试(LRU 顺序 + companion-only + 未命中 bump)**

```python
@pytest.mark.mysql
def test_select_due_games_companion_only_lru(monkeypatch):
    from server.tests.utils import build_test_app
    app = build_test_app(monkeypatch)
    try:
        from server.app.modules.game_library import ingest_service
        from server.app.modules.game_library.models import Game
        from server.app.modules.image_library.models import StockCategory
        s = app.session_factory()
        try:
            comp = StockCategory(name="c", bucket_name="c", kind="companion")
            main = StockCategory(name="m", bucket_name="m", kind="main")
            s.add_all([comp, main]); s.flush()
            g_new = Game(name="new", name_normalized="new", stock_category_id=comp.id, is_active=True)  # last_verified NULL
            g_main = Game(name="mm", name_normalized="mm", stock_category_id=main.id, is_active=True)
            s.add_all([g_new, g_main]); s.commit()
            due = ingest_service.select_due_games(s, limit=10)
            assert g_new.id in due and g_main.id not in due  # 只取 companion
        finally:
            s.close()
    finally:
        app.cleanup()
```

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 实现**

```python
# ingest_service.py
import logging
from sqlalchemy import select
from server.app.core.time import utcnow
from server.app.modules.game_library.models import Game
from server.app.modules.image_library.models import StockCategory

logger = logging.getLogger(__name__)


def select_due_games(db, *, limit: int) -> list[int]:
    comp = select(StockCategory.id).where(StockCategory.kind == "companion")
    stmt = (
        select(Game.id)
        .where(Game.is_active.is_(True), Game.stock_category_id.in_(comp))
        .order_by(Game.last_verified_at.asc().nullsfirst())
        .limit(max(1, int(limit)))
    )
    return list(db.execute(stmt).scalars().all())


def _search_by_name(source_order: str, name: str):
    from server.app.modules.game_library.sources import baidu, taptap
    srcs = {"baidu": baidu, "taptap": taptap}
    for key in [s.strip() for s in (source_order or "").split(",") if s.strip()]:
        mod = srcs.get(key)
        if mod is None:
            continue
        try:
            hit = mod.search_by_name(name)
        except Exception:
            logger.warning("search_by_name failed source=%s name=%s", key, name, exc_info=True)
            hit = None
        if hit is not None:
            if key == "taptap" and not hit.screenshot_urls:
                try:
                    hit = taptap.get_detail(hit.game_id)
                except Exception:
                    logger.warning("taptap get_detail failed name=%s", name, exc_info=True)
            return hit
    return None


def refresh_one_game(session_factory, game_id: int, *, source_order: str, max_shots: int) -> str:
    from server.app.modules.game_library import service
    from server.app.shared import image_download
    db = session_factory()
    try:
        game = db.get(Game, game_id)
        if game is None:
            return "error"
        name, category_id = game.name, game.stock_category_id
    finally:
        db.close()
    hit = _search_by_name(source_order, name)  # 无 session：联网 + 下载
    shots = []
    if hit is not None:
        for url in list(dict.fromkeys(hit.screenshot_urls or []))[:max_shots]:
            got = image_download.download_image(url)
            if got:
                shots.append((url, got[0], got[1]))
    db = session_factory()
    try:
        if hit is None:
            g = db.get(Game, game_id)
            if g is not None:
                g.last_verified_at = utcnow()
            db.commit()
            return "not_found"
        service.upsert_game(db, hit, max_screenshots=max_shots, pre_downloaded=shots, category_id=category_id)
        db.commit()
        return "refreshed"
    except Exception:
        db.rollback()
        logger.warning("refresh_one_game failed id=%s", game_id, exc_info=True)
        return "error"
    finally:
        db.close()
```

- [ ] **Step 4: 跑测试 + Commit**

```bash
env python -m pytest server/tests/test_game_ingest_batch.py -q
git add -A && git commit -m "feat(game-library): select_due_games(companion-only)+refresh_one_game(下载在session外)"
```

### Task B3.2: scheduler 重写(窗口+软 LRU loop + 手动触发 + 进程内锁)

**Files:**
- Modify: `server/app/modules/game_library/scheduler.py`(保留旧 `run_ingest_once` 供手动种子;新增 config-driven loop)
- Test: `server/tests/test_game_ingest_batch.py`(窗口纯函数 + 进程锁)

**Interfaces:**
- Consumes: `select_due_games` / `refresh_one_game`(B3.1)、`get_or_create_ingest_config`(A1)。
- Produces: `start_configured_ingest(session_factory, *, trigger) -> bool`;`is_configured_ingest_running() -> bool`;`start_game_ingest(session_factory)`(重写,内部走 config)。

- [ ] **Step 1: 写窗口纯函数 + 进程锁测试(镜像 keepalive `in_window`/`compute_next_gap`)**

```python
def test_in_window_wraps_midnight():
    import datetime as dt
    from server.app.modules.game_library.scheduler import in_window
    from server.app.modules.game_library.scheduler import parse_hhmm as ph
    start, end = ph("23:00"), ph("03:00")
    assert in_window(start, end, dt.datetime(2026, 7, 21, 0, 30))
    assert not in_window(start, end, dt.datetime(2026, 7, 21, 12, 0))
```

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 实现(照搬 keepalive 的 `parse_hhmm`/`in_window`/`window_*_instant`/`compute_next_gap`;`start_configured_ingest` 用 `threading.Lock` 起后台线程跑一批 `batch_size` 个,写 `last_run_*`;`start_game_ingest` 起守护线程,窗内每 tick 处理 1 个、本窗计数≤batch_size)**

```python
# 关键骨架（完整实现照 accounts/keepalive.py 同构）
import threading
_run_lock = threading.Lock()
_running = False


def is_configured_ingest_running() -> bool:
    return _running


def _run_batch(session_factory, *, trigger: str):
    global _running
    from server.app.modules.game_library import ingest_service
    with _run_lock:
        _running = True
    try:
        db = session_factory()
        try:
            cfg = ingest_service.get_or_create_ingest_config(db)
            cfg.last_run_started_at = _now(); cfg.last_run_trigger = trigger; db.commit()
            k, order, shots = cfg.batch_size, cfg.source_order, cfg.max_shots
            due = ingest_service.select_due_games(db, limit=k)
        finally:
            db.close()
        summary = {"batch": len(due), "refreshed": 0, "not_found": 0, "error": 0}
        for i, gid in enumerate(due):
            r = ingest_service.refresh_one_game(session_factory, gid, source_order=order, max_shots=shots)
            summary[{"refreshed": "refreshed", "not_found": "not_found"}.get(r, "error")] += 1
            if i < len(due) - 1:
                _sleep_gap(cfg)  # 有界随机
        db = session_factory()
        try:
            cfg = ingest_service.get_or_create_ingest_config(db)
            cfg.last_run_finished_at = _now(); cfg.last_run_summary = summary; db.commit()
        finally:
            db.close()
    finally:
        with _run_lock:
            _running = False


def start_configured_ingest(session_factory, *, trigger: str) -> bool:
    if _running:
        return False
    threading.Thread(target=_run_batch, args=(session_factory,), kwargs={"trigger": trigger},
                     daemon=True, name="game-ingest-manual").start()
    return True
```

- [ ] **Step 4: 跑测试 + Commit**

```bash
env python -m pytest server/tests/test_game_ingest_batch.py -q
git add -A && git commit -m "feat(game-library): scheduler 重写 config-driven 批量 loop + 手动触发 + 进程锁"
```

---

## Track F — 前端浏览层(对齐 demo.pen,可并行)

> 现有已提交 v2 六子组件(`GameLibraryWorkspace`/`GameLibraryHeader`/`GameGroupSwitch`/`GameList`/`GameDetailCard`/`GameMaterialPanel`/`GameSourceTable`)对齐 `demo.pen` frame `uAkUP`。前端门禁 = `pnpm --filter @geo/web typecheck && build` + 对照 `get_screenshot(uAkUP)` 视觉核对。

### Task F1: API 客户端 + 类型(browse)

**Files:**
- Modify: `web/src/api/game-library.ts`、`web/src/types.ts`

**Interfaces:**
- Produces: `listGames({tag,minScore,q,kind,limit,offset})`、`getGame(id)`、`listGameTags()`;类型 `GameListItem`/`GameDetail`(字段对齐 §契约)。

- [ ] **Step 1: 加类型**(`GameListItem`/`GameDetail`/`GameListResponse` 对齐 §契约,含 `kind`)
- [ ] **Step 2: 加客户端函数**(fetch `/api/game-library/games?...&kind=`、`/games/{id}`、`/tags`)
- [ ] **Step 3: `pnpm --filter @geo/web typecheck`** Expected: PASS
- [ ] **Step 4: Commit** `feat(web/game-library): browse API 客户端 + 类型(含 kind)`

### Task F2: 分组切换 + 列表(主推/陪衬)

**Files:** `GameGroupSwitch.tsx`、`GameList.tsx`、`GameLibraryWorkspace.tsx`

**数据绑定(→ demo.pen 节点):**
- 分组切换(`jbAUO`):`主推游戏`(Dtiwm)/`陪衬游戏`(Xqpef)两 tab → 切 `kind='main'|'companion'` 调 `listGames`。
- 列表标题(`g6UEr`):`主推候选`(Z3QXNo)/副标题(I8muU)/数量徽标(LWU0Q=total)。
- 游戏条目(`R26GP1` 等):小图标(icon_url)+ 名称行 + 次级行(score/tags/last_used)。选中态 = 紫描边 `#6B5CE7`。

- [ ] **Step 1: GroupSwitch 受控 `kind` state,切换重拉列表**
- [ ] **Step 2: GameList 渲染 items(icon/name/score/tags),空态区分(加载/无数据)**
- [ ] **Step 3: typecheck + build + 对照截图**
- [ ] **Step 4: Commit** `feat(web/game-library): 主推/陪衬分组切换 + 列表对齐 mockup`

### Task F3: 详情卡 + 素材面板 + 来源表

**Files:** `GameDetailCard.tsx`、`GameMaterialPanel.tsx`、`GameSourceTable.tsx`

**数据绑定(→ demo.pen 节点):**
- 详情卡(`oHrzJ`):图标(image fill)、大评分(cQlDH=score)、指标卡 sources/platforms/use_count/stock_category_id/last_verified_at(`kPZSG` 组)、描述(RHm4k=description)、底部元信息(first_seen_at/last_used_at/source_game_id)。
- 取材状态条(`ekLsW`):`selected_games.game_id = {id}`。
- 素材面板(`PMsQE`):`listImages({category_id})`(现有)渲染缩略图(`Ezhv1`)+ 上传按钮(bG6f3)。`stock_category_id` 为空显示空态。
- 来源表(`uGr3x`):source/name_normalized/game_tags/screenshot_urls/last_used_article_id 字段行。

- [ ] **Step 1: DetailCard 绑定 GameDetail 字段**
- [ ] **Step 2: MaterialPanel 复用现有 `listImages({categoryId})`(已存在,无需改后端)**
- [ ] **Step 3: SourceTable 渲染字段行**
- [ ] **Step 4: typecheck + build + 对照截图**
- [ ] **Step 5: Commit** `feat(web/game-library): 详情卡/素材/来源表对齐 mockup`

---

## Track G — 前端 ingest UI(对齐 demo.pen,可并行)

### Task G1: config/run/import API 客户端 + 类型

**Files:** `web/src/api/game-library.ts`、`web/src/types.ts`

**Interfaces:**
- Produces: `getIngestConfig()`、`patchIngestConfig(patch)`、`startIngestRun()`、`importImageCategories(payload)`;类型 `GameIngestConfig`。

- [ ] **Step 1: 类型 `GameIngestConfig`(对齐 §契约 GameIngestConfigRead)**
- [ ] **Step 2: 客户端函数(GET/PATCH `/ingest/config`、POST `/ingest/run`、POST `/import-image-categories`)**
- [ ] **Step 3: typecheck + Commit** `feat(web/game-library): ingest 配置/触发/导入 API 客户端`

### Task G2: 头部按钮 + 陪衬抓取浮层

**Files:** `GameLibraryHeader.tsx`

**数据绑定(→ demo.pen 节点):**
- 头部操作(`PjVgF`):搜索框(ljAk0)、排序(hAxVD)、`编辑信息`(Hp3Un→Track H)、`陪衬抓取`(gVWB9→开配置弹窗)、`删除游戏`(eyeEZ→Track H)。
- 浮层(`RuwWK`):标题「陪衬游戏定时抓取」+ 副标题「仅用于陪衬库,主推游戏手动维护」+ 开关(jmrzP→`enabled`)+ 时间摘要(zK0gc=`每天 {window_start}`)+ 游戏摘要(VnB0N)+ `配置`按钮(QCKXJ→开弹窗)。开关切换 = `patchIngestConfig({enabled})`。

- [ ] **Step 1: 头部按钮行 + 搜索/排序绑定 `q`/排序**
- [ ] **Step 2: 浮层摘要:读 `getIngestConfig`,开关调 `patchIngestConfig`,显示窗/摘要**
- [ ] **Step 3: typecheck + build + 对照截图**
- [ ] **Step 4: Commit** `feat(web/game-library): 头部操作 + 陪衬抓取浮层对齐 mockup`

### Task G3: 抓取设置弹窗 `GameIngestSettingsModal` + 立即抓取 + 迁移

**Files:** Create `web/src/features/game-library/GameIngestSettingsModal.tsx`;wire 到 Header 的「配置」/「陪衬抓取」

**弹窗字段(← GameIngestConfigRead):** enabled 开关、window_start/end(HH:MM)、batch_size、min/max_gap、source_order、max_shots;底部「立即抓取一批」按钮(`startIngestRun`,409→toast「进行中」)+ 上次汇总(`last_run_summary`:刷新/未命中/失败 + `last_run_finished_at`);另有「从图片库导入」按钮(`importImageCategories({kind:'companion'})`,完成后 toast + 重拉列表)。

- [ ] **Step 1: 弹窗表单(受控,PATCH 保存)**
- [ ] **Step 2: 「立即抓取一批」+ 运行指示 + 上次汇总**
- [ ] **Step 3: 「从图片库导入」按钮 + 完成重拉 `listGames`**
- [ ] **Step 4: typecheck + build + 对照截图**
- [ ] **Step 5: Commit** `feat(web/game-library): 抓取设置弹窗 + 立即抓取 + 图库导入`

---

## Track H — 手动编辑/删除游戏(可选,Wave 2,依赖 A)

### Task H1: PATCH/DELETE game 端点 + service

**Files:** `service.py`(+`update_game`/`soft_delete_game`)、`router_web.py`(+2 端点)、`schemas.py`(+`GameUpdateRequest`)、`test_game_web.py`

**Interfaces:** `update_game(db, id, patch) -> Game|None`、`soft_delete_game(db, id) -> bool`(置 `is_active=False`)。

- [ ] **Step 1: 失败测试(PATCH 改 name/score/description/tags;DELETE 置 is_active=False)**
- [ ] **Step 2: 实现 service(tags 走 delete-all+重插或并集,按需)**
- [ ] **Step 3: 端点 `PATCH/DELETE /games/{id}`(404 兜底)**
- [ ] **Step 4: 测试 + Commit** `feat(game-library): 手动编辑/软删游戏`

### Task H2: 前端编辑弹窗 + 删除确认

**Files:** `GameLibraryHeader.tsx`、新建 `GameEditModal.tsx`

- [ ] **Step 1: 「编辑信息」(Hp3Un)开弹窗改 name/score/description/tags → `PATCH`**
- [ ] **Step 2: 「删除游戏」(eyeEZ)confirm → `DELETE` → 重拉列表**
- [ ] **Step 3: typecheck + build + Commit** `feat(web/game-library): 编辑/删除游戏 UI`

---

## 集成与收尾

- [ ] **I1:** A+B1+B3 落地后,`test_game_web.py` 补 import/config/run 端点用例(202/409/幂等导入)。
- [ ] **I2:** 全量后端测试:`env python -m pytest server/tests/test_game_*.py server/tests/test_import_*.py -q`。
- [ ] **I3:** 后端门禁:`ruff check server/` + `ruff format --check server/` + `mypy server/app`。
- [ ] **I4:** 前端门禁:`pnpm --filter @geo/web typecheck && pnpm --filter @geo/web build`。
- [ ] **I5:** 手动跑一次导入 CLI(本地 LAN 库):`env python -m server.scripts.import_image_library_games`,核对 games 建出。
- [ ] **I6:** CLAUDE.md `game_library/` 段同步(调度改 config-driven + companion-only + 导入 CLI + web 端点)。
- [ ] **I7:** 更新记忆 [[project-game-library-ingest-spec-and-baseline-reset]](浏览层已恢复、ingest 已实现)。

---

## Self-Review

**Spec 覆盖:** 导入(B1)✓ / 每日窗+软 LRU(B3.2)✓ / 按名刷新取代按体裁(B2+B3.1)✓ / 手动抓一批(B3.2+G3)✓ / FK 唯一关联(A4 category_id + B1)✓ / screenshot_urls 台账(A4 保留)✓ / upsert 重构 C2/M1/M4(A4)✓ / 浏览层(A3)✓ / demo.pen 前台(F/G)✓ / 手动编辑删除(mockup 独有,H 可选)✓。

**未决:** companion-only 是对 spec 的收窄(§Reconciliation),需需求方确认;M2(GameTag/store_image IntegrityError 兜底)与 M3(scheduler 隔离)已随 A4/B3 部分覆盖,剩余可在集成期补。

**类型一致性:** `list_games`/`get_game` 签名(A3)= router(A5)= 客户端(F1);`upsert_game(...,pre_downloaded,category_id)`(A4)= `refresh_one_game`(B3.1);`get_or_create_ingest_config`/`update_ingest_config`/`ingest_config_to_dict`(A1/A2)= router(A5)一致;`GameIngestConfigRead` 字段(A2)= 客户端 `GameIngestConfig`(G1)一致。
