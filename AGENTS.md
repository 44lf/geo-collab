# AGENTS.md — Geo 协作平台

Always `conda activate geo_xzpt` before any Python command.

## Dev commands (PowerShell)

```powershell
# backend (port 8000)
uvicorn server.app.main:app --reload --host 127.0.0.1 --port 8000

# frontend (port 5173, proxies /api → :8000)
pnpm --filter @geo/web dev

# typecheck
pnpm --filter @geo/web typecheck

# tests
pytest server/tests/ -v
pytest server/tests/test_tasks_api.py -v --tb=short

# single migration
alembic upgrade head

# build: frontend first, then exe
pnpm --filter @geo/web build
pyinstaller geo.spec --noconfirm  # → dist/GeoCollab.exe

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

- **Local token auth** — `launcher.py` generates `GEO_LOCAL_API_TOKEN` (stored in `data_dir/local_token.txt`). All routes except `/api/bootstrap` require `X-Geo-Token` header. Single-user desktop app, binds only 127.0.0.1.
- **Entry point**: `launcher.py` (runs migrations, checks Chrome, initializes token, finds free port 8765+, starts uvicorn in background thread, opens browser, shows system tray). PyInstaller target.
- **Backend**: FastAPI, SQLite via SQLAlchemy, Alembic, 7 route modules under `/api/`. Global handlers: `ValueError` → 400, `ConflictError` → 409.
- **Frontend**: React 19 + Vite + TypeScript, feature-split (`features/content/`, `features/accounts/`, `features/tasks/`, `features/system/`), Tiptap rich-text editor, Lucide icons.
- **Models** (11 ORM): Platform, Account, Article, ArticleGroup+ArticleGroupItem, Asset, ArticleBodyAsset, PublishTask, PublishTaskAccount, PublishRecord, TaskLog.
- **Services**: `tasks.py` (~1k lines, sync + concurrent execution engine with `ThreadPoolExecutor`), `toutiao_publisher.py` (Playwright automation), `accounts.py` (login/check/export with persistent browser contexts), `assets.py` (file storage), `browser.py` (`managed_browser_context` context manager), `browser_sessions.py` (long-lived sessions).
- **Publisher**: ByteDance design system selectors, two-step publish ("预览并发布" + "确认发布"), `stop_before_publish` flag, cover is mandatory (raises if `None`).
- **Data dir**: `%LOCALAPPDATA%/GeoCollab/` (override: `$env:GEO_DATA_DIR`). Subdirs: `assets/`, `browser_states/`, `logs/`, `exports/`.
- **Config**: pydantic-settings with `GEO_` prefix, `get_settings()` is `@lru_cache`'d — call `.cache_clear()` after env changes.
- **No CI/CD** — local desktop app only.
- **Task execution**: synchronous in request thread, one `threading.Lock` per task_id prevents concurrent runs. Up to 5 concurrent records via `ThreadPoolExecutor`, with per-account locks for serialized access. Records have `lease_until` for crash recovery (`recover_stuck_records` runs at startup).
- **Startup order** (in `launcher.py` and `create_app()`): migrations → Chrome check → token init → find port → uvicorn → browser open. `create_app()` also runs `recover_stuck_records` on boot.

- For detailed Playwright selectors and cover upload flow, see `CLAUDE.md`.

## Testing quirks

- `build_test_app(monkeypatch)` creates temp data dir + SQLite DB, sets `GEO_DATA_DIR`, clears `get_settings.cache_clear()`, and clears global `_task_locks`/`_account_locks`/`_task_cancel`. Every test **must** call `test_app.cleanup()` in `finally`.
- FTS5 tables are created manually in `build_test_app` (not via Alembic) — any test using full-text search needs those triggers.
- Tests that execute tasks **must** pass `"stop_before_publish": False` or the task stays in `waiting_manual_publish`.
- Mock the publisher: `monkeypatch.setattr("server.app.services.tasks.build_publisher_for_record", lambda r: FakePublisher())`.
- Background task execution uses `bg_session_factory` (patched in `build_test_app` to use test DB session from `TestingSessionLocal`).
- `test_launcher_startup.py` tests `launcher.py` directly (not via `create_app`).
- `build_test_app` also calls `browser_sessions._reset_globals()` to reset browser sessions (prevents cross-test leaks).

## Gotchas

- `server/app/db/session.py`: `ensure_data_dirs()` runs at module import, `check_same_thread=False`, WAL + busy_timeout=5000 + foreign_keys=ON on connect.
- Alembic `alembic.ini`: `sqlalchemy.url` is a placeholder — runtime override in `launcher.py` via `get_database_url()`.
- `alembic upgrade head` run automatically by `launcher.py` at startup.
- `ToutiaoPublisher.publish_article(article, account, stop_before_publish=False)` — `stop_before_publish` stops after "预览并发布", user must call `POST /api/publish-records/{id}/manual-confirm`.
- Cover image is mandatory: `_handle_cover()` raises if `article.cover_asset is None`.
- Do NOT guess Toutiao selectors — use `playwright-cli` to inspect live page (ByteDance DOM changes frequently).
- Exception hierarchy (`server/app/services/errors.py`): `ValueError` → 400, `ConflictError(ValueError)` → 409, `AccountError(ValueError)` and `ValidationError(ValueError)` → 400.
- Retry only on original records (not retry records).
- Spike/debug scripts: `python -m server.scripts.toutiao_login_spike --account-key spike`, `python -m server.scripts.toutiao_publish_spike --account-key spike`.
- Chrome is required at runtime: `launcher.py` checks for Chrome and shows error dialog if missing.
- `bg_session_factory` (module-level var in `server/app/api/routes/tasks.py`) is imported lazily inside functions in both `tasks.py` and `publish_records.py` to avoid circular imports. Do NOT toplevel-import it.