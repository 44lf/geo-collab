# AGENTS.md — Geo 协作平台

Always `conda activate geo_xzpt` before any Python command.

## Dev commands

```powershell
# backend (port 8000)
uvicorn server.app.main:app --reload --host 127.0.0.1 --port 8000

# frontend (port 5173, proxies /api → :8000)
pnpm --filter @geo/web dev

# tests
pytest server/tests/ -v
pytest server/tests/test_tasks_api.py -v --tb=short  # single file

# migrations
alembic upgrade head

# build: frontend first, then exe
pnpm --filter @geo/web build
pyinstaller geo.spec --noconfirm  # → dist/GeoCollab.exe
```

## Architecture

- **No authentication** — binds only 127.0.0.1, single-user desktop app
- **Backend**: FastAPI, SQLite via SQLAlchemy, Alembic migrations, 6 route modules under `/api/`
- **Frontend**: React 19 + Vite + TypeScript, single 1377-line `web/src/main.tsx` (all components)
- **Models** (11 SQLAlchemy ORM models in `server/app/models/`): Platform, Account, Article, ArticleGroup + ArticleGroupItem, Asset, ArticleBodyAsset, PublishTask, PublishTaskAccount, PublishRecord, TaskLog
- **Services** (`server/app/services/`): `tasks.py` (402 lines, execution engine), `toutiao_publisher.py` (Playwright automation), `accounts.py` (login/check/export), `assets.py` (file storage)
- **Publisher** (`toutiao_publisher.py`): ByteDance design system selectors, two-step publish ("预览并发布" + "确认发布"), `stop_before_publish` flag respected
- **Data dir**: `%LOCALAPPDATA%/GeoCollab/` (override: `$env:GEO_DATA_DIR`)
- **Config**: pydantic-settings with `GEO_` prefix, `get_settings()` is `@lru_cache`'d

## Testing quirks

- `build_test_app(monkeypatch)` creates in-memory SQLite + temp data dir; every test must call `test_app.cleanup()` in `finally`
- Tests that execute tasks **must** pass `"stop_before_publish": False` or the task will wait for manual confirmation
- Mock the publisher: `monkeypatch.setattr("server.app.services.tasks.build_publisher_for_record", lambda r: FakePublisher())`
- `get_settings.cache_clear()` needed after env changes (done in `build_test_app` and `cleanup`)
- `test_system_status.py` now uses `build_test_app()` (not real database)

## Gotchas

- `server/app/db/session.py`: `ensure_data_dirs()`, `check_same_thread=False`, WAL mode + busy_timeout=5000 on connect
- Alembic `alembic.ini`: `sqlalchemy.url` is a placeholder (runtime override in launcher.py)
- `alembic upgrade head` run automatically by `launcher.py` at startup; also run manually during dev
- `ToutiaoPublisher.publish_article(article, account, stop_before_publish=False)` — `stop_before_publish` threads through to `_click_publish_and_wait`
- Task execution uses `threading.Lock` per task_id — concurrent execute returns error
- `_aggregate_task_status` treats `waiting_manual_publish` as non-terminal (task stays "running")
