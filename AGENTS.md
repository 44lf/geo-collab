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

# migrations
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

- **No auth** — binds only 127.0.0.1, single-user desktop app
- **Entry point**: `launcher.py` (runs migrations, finds free port 8765+, starts uvicorn, opens browser). PyInstaller target.
- **Backend**: FastAPI, SQLite via SQLAlchemy, Alembic, 7 route modules under `/api/`
- **Frontend**: React 19 + Vite + TypeScript, single 1806-line `web/src/main.tsx` + 1056-line `web/src/styles.css`
- **Models** (11 ORM models): Platform, Account, Article, ArticleGroup+ArticleGroupItem, Asset, ArticleBodyAsset, PublishTask, PublishTaskAccount, PublishRecord, TaskLog
- **Services**: `tasks.py` (535 lines, sync execution engine), `toutiao_publisher.py` (269 lines, Playwright automation), `accounts.py` (login/check/export), `assets.py` (file storage)
- **Publisher**: ByteDance design system selectors (`byte-btn`), two-step publish ("预览并发布" + "确认发布"), `stop_before_publish` flag
- **Error convention**: `ValueError` → HTTP 400 (global handler in `create_app()`)
- **Data dir**: `%LOCALAPPDATA%/GeoCollab/` (override: `$env:GEO_DATA_DIR`)
- **Config**: pydantic-settings with `GEO_` prefix, `get_settings()` is `@lru_cache`'d
- **No CI/CD** — local desktop app only

## Testing quirks

- `build_test_app(monkeypatch)` creates in-memory SQLite + temp data dir; every test must call `test_app.cleanup()` in `finally`
- Tests that execute tasks **must** pass `"stop_before_publish": False` or the task waits for manual confirmation
- Mock the publisher: `monkeypatch.setattr("server.app.services.tasks.build_publisher_for_record", lambda r: FakePublisher())`
- `get_settings.cache_clear()` needed after env changes (done in `build_test_app` and `cleanup`)
- `test_system_status.py` now uses `build_test_app()` (not real database)

## Gotchas

- `server/app/db/session.py`: `ensure_data_dirs()`, `check_same_thread=False`, WAL mode + busy_timeout=5000 on connect
- Alembic `alembic.ini`: `sqlalchemy.url` is a placeholder (runtime override in `launcher.py`)
- `alembic upgrade head` run automatically by `launcher.py` at startup; also run manually during dev
- `ToutiaoPublisher.publish_article(article, account, stop_before_publish=False)` — `stop_before_publish` threads through to `_click_publish_and_wait`
- Task execution uses `threading.Lock` per task_id — concurrent execute returns error
- `_aggregate_task_status` treats `waiting_manual_publish` as non-terminal (task stays "running")
- `_handle_cover()` raises if `article.cover_asset` is None (cover is mandatory)
- Do NOT guess Toutiao selectors — use `playwright-cli` to inspect live page (DOM changes frequently)
- Spike/debug scripts: `python -m server.scripts.toutiao_login_spike --account-key spike`, `python -m server.scripts.toutiao_publish_spike --account-key spike`