# AGENTS.md — Geo 协作平台

Always `conda activate geo_xzpt` before any Python command.
Docker 环境使用 `docker-compose exec app` 运行所有 Python 命令。

## Dev commands (PowerShell)

```powershell
# backend (port 8000)
uvicorn server.app.main:app --reload --host 127.0.0.1 --port 8000

# frontend (port 5173, proxies /api → :8000)
pnpm --filter @geo/web dev

# typecheck
pnpm --filter @geo/web typecheck

# tests (SQLite, 不依赖 Docker)
pytest server/tests/ -v
pytest server/tests/test_tasks_api.py -v --tb=short
pytest server/tests/ -m "not mysql" -q          # 跳过 MySQL 集成测试

# migrations
alembic upgrade head

# build frontend → package exe (本地桌面打包，上云可忽略)
pnpm --filter @geo/web build
pyinstaller geo.spec --noconfirm

# Docker Compose
docker-compose up -d
docker-compose exec app alembic upgrade head
docker-compose exec app python -m server.scripts.seed_users

# health check
Invoke-RestMethod http://127.0.0.1:8000/api/system/status
```

## Setup prerequisites

```powershell
pip install -r requirements.txt
playwright install chromium
alembic upgrade head
pnpm install
```

## Architecture

- **JWT cookie auth** — `/api/auth/login` 下发 JWT 作为 `access_token` httpOnly cookie。需要 `GEO_JWT_SECRET` 环境变量（测试中设为 `"test-secret"`），过期时间 `GEO_JWT_EXPIRE_HOURS`（默认 8 小时）。
  - 多用户模型：`User` 表有 `role`（admin / operator）和 `is_active`。admin 可创建子账号 (`POST /api/auth/users`)，operator 只能看到自己的任务。
  - 登录引导：前端通过 `/api/bootstrap` 检查是否存在 admin → 无则显示首次设置页面。
  - `GEO_SEED_USERS` 环境变量（JSON 数组格式）在 Docker 启动时通过 `seed_users.py` 预建用户。
  - `require_local_token()` 函数仍在 `security.py` 中但**未被任何路由使用**，是遗留死代码。
- **Entry point**: `launcher.py`（Docker entrypoint），执行：`ensure_data_dirs` → `_run_migrations` → 启动 uvicorn。不再做 Chrome 检查、端口扫描或系统托盘。
- **Backend**: FastAPI, SQLAlchemy, Alembic, 8 route modules under `/api/`（auth、accounts、articles、article-groups、assets、publish-records、system、tasks）。Global handlers: `ValueError` → 400, `ConflictError(ValueError)` → 409。
- **Database**: 开发/测试用 **SQLite** (`check_same_thread=False`, WAL mode)，Docker 用 **MySQL** (`mysql+pymysql`)。`alembic.ini` 的 `sqlalchemy.url` 是占位符，运行时 `launcher.py` 通过 `get_database_url()` 覆盖。
- **Frontend**: React 19 + Vite + TypeScript (`web/`), feature-split (`features/content/`, `features/accounts/`, `features/tasks/`, `features/system/`), Tiptap rich-text editor, Lucide icons.
- **Models** (12 ORM): Platform, Account, Article, ArticleGroup+ArticleGroupItem, Asset, ArticleBodyAsset, PublishTask, PublishTaskAccount, PublishRecord, TaskLog, User.
- **Services**: `tasks.py` (~1k lines, concurrent execution engine with `ThreadPoolExecutor`), `toutiao_publisher.py` (Playwright automation), `accounts.py` (login/check/export with persistent browser contexts), `assets.py` (file storage), `browser.py` (`managed_browser_context` context manager), `browser_sessions.py` (long-lived remote browser sessions).
- **Publisher**: ByteDance design system selectors, two-step publish ("预览并发布" + "确认发布"), `stop_before_publish` flag, cover is mandatory (raises if `None`).
- **Data dir**: `%LOCALAPPDATA%/GeoCollab/` (override: `$env:GEO_DATA_DIR`). Subdirs: `assets/`, `browser_states/`, `logs/`, `exports/`.
- **Config**: pydantic-settings with `GEO_` prefix, `get_settings()` is `@lru_cache`'d — call `.cache_clear()` after env changes.
- **Task execution**: `POST /api/tasks/{id}/execute` 返回 202，在 **后台线程** (`threading.Thread(daemon=True)`) 中异步执行。一个 `threading.Lock` 按 task_id 防止同一任务并发执行。内部最多 5 条 PublishRecord 同时执行（`ThreadPoolExecutor`），按 account 再加一把锁串行化同账号操作。Records 有 `lease_until` 字段支持崩溃恢复（`recover_stuck_records` 在 `create_app()` 启动时运行）。
- **Startup order** (`launcher.py` → `create_app()`): migrations → `recover_stuck_records` → uvicorn serve.
- **`TaskCreate.platform_code`** 默认值是 `"toutiao"`——前端不传时后端自动填入。

## Playwright automation details

CLAUDE.md 已过期（仍描述旧架构的本地桌面启动器、单用户、local token），**以本文为准**。以下是关键发布管线信息：

- **Selectors**: 头条使用 ByteDance 自有设计系统 (`byte-btn`, `byte-btn-primary`, `syl-toolbar-tool`)，**不是** Ant Design。不要猜测选择器——用 `playwright-cli` 检查真实页面 DOM（ByteDance DOM 经常变动）。
- **Cover upload**: 点击 `.add-icon` → 对话框 → "本地上传" → `expect_file_chooser()` + `set_files()` → 等待 "已上传 1 张图片" 文本（最多 60s）→ 确定。
- **Body image upload**: 点击工具栏图片按钮 → 打开抽屉 → 选择文件 → 确认 → 等待 `<img>` 插入 contenteditable 区域。
- **Two-step publish**: 点击 "预览并发布" → 等待 → 点击 "确认发布"（不是同一个按钮）。`stop_before_publish=True` 在预览后停止。
- **Post-publish popups**: "作品同步授权" 对话框和 "加入创作者计划" 弹窗需要关闭。
- **AI drawer**: 操作前先关闭 AI 助手抽屉 (`.close-btn`)。
- **Browser context**: 发布使用 `managed_browser_context`（本地）或 `managed_remote_browser_session`（Linux 服务器上使用 Xvfb + noVNC）。账号浏览器状态存储在 `browser_states/toutiao/{account_key}/` 下。

## Testing quirks

- `build_test_app(monkeypatch)` creates temp data dir + SQLite DB, sets `GEO_DATA_DIR`, `GEO_JWT_SECRET`, clears `get_settings.cache_clear()`, and clears global `_task_locks`/`_account_locks`/`_task_cancel`. Every test **must** call `test_app.cleanup()` in `finally`.
- FTS5 tables are created manually in `build_test_app` (not via Alembic) — any test using full-text search needs those triggers.
- Tests that execute tasks **must** pass `"stop_before_publish": False` or the task stays in `waiting_manual_publish`.
- Mock the publisher: `monkeypatch.setattr("server.app.services.tasks.build_publisher_for_record", lambda r: FakePublisher())`.
- Background task execution uses `bg_session_factory` — patched in `build_test_app` to use `TestingSessionLocal` for cross-thread DB access.
- `build_test_app` also calls `browser_sessions._reset_globals()` to reset browser sessions (prevents cross-test leaks).
- `test_launcher_startup.py` tests `launcher.py` directly (not via `create_app`).

## Gotchas

- `ensure_data_dirs()` runs at **module import** of `server/app/db/session.py`. All DB sessions: `check_same_thread=False`, WAL + busy_timeout=5000 + foreign_keys=ON.
- Alembic `alembic.ini`: `sqlalchemy.url` is a placeholder — runtime override in `launcher.py` via `get_database_url()`. `alembic upgrade head` runs automatically at startup.
- `ToutiaoPublisher.publish_article(article, account, stop_before_publish=False)` — `stop_before_publish` stops after "预览并发布", user must call `POST /api/publish-records/{id}/manual-confirm`.
- Cover image is **mandatory**: `_handle_cover()` raises if `article.cover_asset is None`.
- Exception hierarchy (`server/app/services/errors.py`): `ValueError` → 400, `ConflictError(ValueError)` → 409, `AccountError(ValueError)` and `ValidationError(ValueError)` → 400.
- Retry only on original records (not retry records).
- `build_publisher_for_record(record)` in `tasks.py:712` **ignores `record.platform_id`** and always returns `ToutiaoPublisher` — single-platform bottleneck for multi-platform expansion.
- `accounts.py`: `state_dir_for_key()` 硬编码 `"toutiao"` 子目录 (`browser_states/toutiao/{key}`). `get_or_create_toutiao_platform()` 只创建 toutiao platform 记录。这两个函数是多平台适配时需要重构的入口。
- `bg_session_factory` (module-level var in `server/app/api/routes/tasks.py`) is imported lazily inside functions in both `tasks.py` and `publish_records.py` to avoid circular imports. Do **NOT** toplevel-import it.
- Spike/debug scripts: `python -m server.scripts.toutiao_publish_spike --account-key spike`。(`toutiao_login_spike.py` 已不存在。)
- `launcher.py` 中 `_read_or_generate_token()` 函数仍存在但**从未被调用**——local token 机制已废弃，后端认证走 JWT cookie。
