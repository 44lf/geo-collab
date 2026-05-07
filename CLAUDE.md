# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Geo 协作平台** — A Windows desktop MVP for managing and auto-publishing articles to 头条号 (Toutiao). Architecture: FastAPI backend + React/TypeScript frontend + Playwright browser automation, packaged as a Windows `.exe` via PyInstaller.

## Dev Commands

**Activate the Python env first** (always required):
```powershell
conda activate geo_xzpt
```

**Backend dev server** (port 8000):
```powershell
uvicorn server.app.main:app --reload --host 127.0.0.1 --port 8000
```

**Frontend dev server** (port 5173, proxies `/api` → `:8000`):
```powershell
pnpm --filter @geo/web dev
```

**Run tests:**
```powershell
pytest server/tests/
pytest server/tests/test_tasks_api.py  # single file
```

**Run database migrations:**
```powershell
alembic upgrade head
```

**Build frontend** (required before building exe):
```powershell
pnpm --filter @geo/web build
```

**Build exe:**
```powershell
pyinstaller geo.spec --noconfirm
# Output: dist/GeoCollab.exe
```

## Architecture

### Backend (`server/app/`)

FastAPI app with SQLite via SQLAlchemy + Alembic migrations.

**Core models** (`server/app/models/`):
- `Platform` — publishing targets (e.g., toutiao)
- `Account` — platform accounts with Playwright storage state
- `Article` — content with triple storage: JSON (Tiptap editor), HTML, plain text
- `ArticleGroup` + `ArticleGroupItem` — article collections for batch publishing
- `Asset` — uploaded images stored in `data_dir/assets/`
- `PublishTask` → `PublishRecord` → `TaskLog` — task execution with per-record status and screenshot logs

**Routes** (`server/app/api/routes/`): accounts, articles, groups, assets, tasks, records, system.

**Services** (`server/app/services/`):
- `toutiao_publisher.py` — Playwright automation for 头条号, see section below
- `accounts.py` — account login/check/export via Playwright persistent contexts
- `tasks.py` — task creation, execution, retry, cancellation; inline synchronous execution
- `assets.py` — file storage (`store_bytes`, `resolve_asset_path`)

### Frontend (`web/`)

React 19 + Vite + TypeScript. Single large component in `web/src/main.tsx`. Tiptap rich-text editor for articles. Lucide React icons.

### Data Directory

`%LOCALAPPDATA%/GeoCollab/` (override with `$env:GEO_DATA_DIR`):
- `geo.db` — SQLite database
- `assets/` — uploaded images
- `browser_states/toutiao/<account_key>/` — Playwright persistent profile + `storage_state.json`
- `exports/` — account auth export ZIPs
- `logs/launcher.log`

### PyInstaller Bundling

`geo.spec` bundles: frontend `web/dist/`, all `server/` modules (`collect_submodules`), all of `playwright` (`collect_all`), Alembic migrations. `launcher.py` is the entry point — it detects `sys._MEIPASS` for path resolution, runs Alembic migrations, finds a free port (8765+), starts uvicorn, and opens the browser.

## Playwright Automation (头条号 Publisher)

`server/app/services/toutiao_publisher.py` automates 头条号 publishing using a persistent Chrome context.

**Key implementation details:**
- Uses `channel="chrome"` with `launch_persistent_context` (profile dir separate from storage state)
- Profile dir: `browser_states/toutiao/<key>/profile/`; storage state: `browser_states/toutiao/<key>/storage_state.json`
- **头条号 uses ByteDance's own design system** (`byte-btn`, `byte-btn-primary`, `publish-btn-last`) — **not** Ant Design classes
- Always close the AI assistant drawer (`.close-btn`) before interacting with the body editor
- Cover image is **mandatory** — `_handle_cover()` raises if `article.cover_asset` is None
- Cover upload: click `.add-icon` → dialog → "本地上传" → `expect_file_chooser()` + `set_files()` → wait for "已上传 1 张图片" text (up to 60s, network-dependent) → confirm with "确定"
- Publish is **two-step**: click "预览并发布" → wait 1.5s → click "确认发布" (not the same button)
- Handle post-publish popups: "作品同步授权" dialog and "加入创作者计划" popup both need dismissal

**When modifying Playwright selectors:** Use `playwright-cli` (the agent-operable browser CLI tool, `@playwright/cli`) to inspect the live page and get real `ref=eXXX` element handles. Do not guess selectors — 头条号's DOM changes frequently. Run commands like `open`, `snapshot`, `click`, `screenshot` via playwright-cli to verify actual page structure before writing code.

## Task Execution Model

Tasks run **synchronously in the request thread** — `POST /api/tasks/{id}/execute` blocks until all `PublishRecord`s complete. A `threading.Lock` per task (keyed by task ID) prevents concurrent runs. There is no async worker or queue.

**`stop_before_publish=true` flow:** The publisher clicks "预览并发布" but skips "确认发布", leaving the record in `waiting_manual_publish` status. Call `POST /api/publish-records/{id}/manual-confirm` with `{"outcome": "succeeded"|"failed", ...}` to resolve it and advance the task to its next pending record.

**Error convention:** `ValueError` raised in any service is caught globally in `create_app()` and returned as HTTP 400. This is the intended pattern — raise `ValueError` for all client-visible validation errors.

## Testing

Tests use `pytest` with `httpx.AsyncClient` or `TestClient`. Test utilities in `server/tests/utils.py` build an in-memory SQLite app and monkeypatch `GEO_DATA_DIR` to a temp dir. Tests cover all API routes; browser automation is not unit-tested (use diagnostic scripts in `scripts/` instead).

To fake the Playwright publisher in task tests, monkeypatch `server.app.services.tasks.build_publisher_for_record` to return a stub with a `publish_article(article, account, stop_before_publish)` method.
