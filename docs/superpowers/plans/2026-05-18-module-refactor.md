# Module Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize `server/app/services/` into three domain modules (articles, accounts, tasks) with explicit `__init__.py` contracts and a `PublishPayload` DTO that decouples platform drivers from article/asset internals.

**Architecture:** Three domain packages under `server/app/modules/`; a `server/app/shared/` for cross-cutting utilities. Compatibility shims keep existing tests green at every step. The one piece of new logic is `publish_Runner.py` assembling `PublishPayload` before calling the driver, so drivers stop importing `articles`/`assets` directly. Migration order: shared → articles → accounts → tasks → routes → cleanup → frontend.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy, Playwright, pytest

---

## File Map

### Created
```
server/app/shared/__init__.py
server/app/shared/errors.py          ← from services/errors.py
server/app/shared/diagnostics.py     ← from services/publish_diagnostics.py
server/app/shared/feishu.py          ← from services/feishu.py

server/app/modules/__init__.py

server/app/modules/articles/__init__.py
server/app/modules/articles/tiptap_Parser.py   ← NEW: consolidates articles.py parsing + toutiao._body_segments
server/app/modules/articles/asset_Store.py     ← from services/assets.py
server/app/modules/articles/article_Crud.py    ← from services/articles.py + article_groups.py

server/app/modules/accounts/__init__.py
server/app/modules/accounts/account_Crud.py   ← CRUD + export/import from services/accounts.py
server/app/modules/accounts/account_Auth.py   ← login session state machine from services/accounts.py
server/app/modules/accounts/browser_Session.py ← from services/browser_sessions.py

server/app/modules/tasks/__init__.py
server/app/modules/tasks/task_Crud.py          ← DB read/write functions from services/tasks.py
server/app/modules/tasks/task_Executor.py      ← execute_task + ThreadPoolExecutor from services/tasks.py
server/app/modules/tasks/publish_Runner.py     ← from services/publish_runner.py, builds PublishPayload
server/app/modules/tasks/drivers/__init__.py   ← from services/drivers/__init__.py (updated Protocol)
server/app/modules/tasks/drivers/driver_Base.py ← from services/drivers/base.py + new PublishPayload
server/app/modules/tasks/drivers/toutiao.py   ← from services/drivers/toutiao.py (updated signature)

server/tests/test_tiptap_parser.py    ← new tests for parse_body_segments
server/tests/test_publish_payload.py  ← new test for PublishPayload assembly

web/src/api/articles.ts
web/src/api/accounts.ts
web/src/api/tasks.ts
web/src/api/assets.ts
```

### Modified
```
server/app/api/routes/articles.py      ← update imports
server/app/api/routes/article_groups.py ← update imports
server/app/api/routes/assets.py        ← update imports
server/app/api/routes/accounts.py      ← update imports
server/app/api/routes/tasks.py         ← update imports
server/app/api/routes/publish_records.py ← update imports
server/app/api/routes/system.py        ← update imports
server/app/main.py                     ← update driver import
server/worker/executor.py              ← update imports
server/tests/test_publish_runner.py    ← update to new module path + PublishPayload signature
web/src/api/client.ts                  ← keep as barrel re-export or delete
```

### Deleted (Phase 6)
```
server/app/services/  (entire directory, after all shims removed)
```

---

## Task 1: Create shared/ module

**Files:**
- Create: `server/app/shared/__init__.py`
- Create: `server/app/shared/errors.py`
- Create: `server/app/shared/diagnostics.py`
- Create: `server/app/shared/feishu.py`
- Modify: `server/app/services/errors.py` (shim)
- Modify: `server/app/services/publish_diagnostics.py` (shim)
- Modify: `server/app/services/feishu.py` (shim)

- [ ] **Step 1: Create shared/ directory**

```bash
mkdir server/app/shared
touch server/app/shared/__init__.py
```

- [ ] **Step 2: Create server/app/shared/errors.py**

Copy content verbatim from `server/app/services/errors.py` (no import changes needed — it has no internal imports):

```python
class ClientError(Exception):
    """Base for all client-visible errors (HTTP 4xx). Use instead of ValueError in service code."""


class ConflictError(ClientError):
    """Raised when an optimistic version or idempotency conflict is detected. (HTTP 409)"""


class ValidationError(ClientError):
    """Raised when user input validation fails. (HTTP 400)"""


class AccountError(ClientError):
    """Raised for account-related errors (expired, not found, platform mismatch). (HTTP 400)"""
```

- [ ] **Step 3: Create server/app/shared/diagnostics.py**

Copy content verbatim from `server/app/services/publish_diagnostics.py` (no internal service imports):

```python
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator


@dataclass(frozen=True)
class PublishDiagnosticEvent:
    level: str
    message: str
    screenshot: bytes | None = None


_local = threading.local()


def _current_events() -> list[PublishDiagnosticEvent] | None:
    events = getattr(_local, "events", None)
    return events if isinstance(events, list) else None


@contextmanager
def capture_publish_diagnostics(events: list[PublishDiagnosticEvent]) -> Iterator[None]:
    previous = getattr(_local, "events", None)
    _local.events = events
    try:
        yield
    finally:
        if previous is None:
            try:
                delattr(_local, "events")
            except AttributeError:
                pass
        else:
            _local.events = previous


def record_publish_diagnostic(message: str, *, level: str = "info", screenshot: bytes | None = None) -> None:
    events = _current_events()
    if events is not None:
        events.append(PublishDiagnosticEvent(level=level, message=message, screenshot=screenshot))


def _safe_screenshot(page: Any | None) -> bytes | None:
    if page is None:
        return None
    try:
        return page.screenshot(full_page=True)
    except Exception:
        return None


@contextmanager
def publish_step(name: str, *, page: Any | None = None) -> Iterator[None]:
    started = time.monotonic()
    try:
        yield
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        record_publish_diagnostic(
            f"step failed: {name}; elapsed_ms={elapsed_ms}; error={type(exc).__name__}: {exc}",
            level="error",
            screenshot=_safe_screenshot(page),
        )
        raise
    else:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        record_publish_diagnostic(f"step completed: {name}; elapsed_ms={elapsed_ms}")
```

- [ ] **Step 4: Create server/app/shared/feishu.py**

Copy content verbatim from `server/app/services/feishu.py` — only import change needed is any `from server.app.services.errors` reference (check the file first; if it imports from services.errors, change to `from server.app.shared.errors`).

- [ ] **Step 5: Replace services/errors.py with compatibility shim**

```python
# server/app/services/errors.py
from server.app.shared.errors import (  # noqa: F401
    ClientError,
    ConflictError,
    ValidationError,
    AccountError,
)
```

- [ ] **Step 6: Replace services/publish_diagnostics.py with compatibility shim**

```python
# server/app/services/publish_diagnostics.py
from server.app.shared.diagnostics import (  # noqa: F401
    PublishDiagnosticEvent,
    capture_publish_diagnostics,
    record_publish_diagnostic,
    publish_step,
    _safe_screenshot,
)
```

- [ ] **Step 7: Replace services/feishu.py with compatibility shim**

```python
# server/app/services/feishu.py
from server.app.shared.feishu import *  # noqa: F401, F403
```

- [ ] **Step 8: Run tests**

```bash
pytest server/tests/ -x -q
```

Expected: all green (same count as before)

- [ ] **Step 9: Commit**

```bash
git add server/app/shared/ server/app/services/errors.py server/app/services/publish_diagnostics.py server/app/services/feishu.py
git commit -m "refactor: create shared/ module (errors, diagnostics, feishu)"
```

---

## Task 2: Create articles/ module — tiptap_Parser.py (new file with new logic)

This file consolidates Tiptap parsing from `services/articles.py` AND the body-segment logic from `services/drivers/toutiao.py` (`_body_segments`, `_append_tiptap_segments`, `_compact_segments`). This is the only new function: `parse_body_segments(article) → list[BodySegment]`.

**Files:**
- Create: `server/app/modules/__init__.py`
- Create: `server/app/modules/articles/__init__.py` (empty for now)
- Create: `server/app/modules/articles/tiptap_Parser.py`
- Create: `server/tests/test_tiptap_parser.py`

- [ ] **Step 1: Create module directories**

```bash
mkdir -p server/app/modules/articles
touch server/app/modules/__init__.py
touch server/app/modules/articles/__init__.py
```

- [ ] **Step 2: Write failing test**

```python
# server/tests/test_tiptap_parser.py
from __future__ import annotations
import types
from server.app.modules.articles.tiptap_Parser import parse_body_segments, BodySegment


def _article(content_json="", plain_text="", html="", body_assets=None):
    a = types.SimpleNamespace(
        content_json=content_json,
        plain_text=plain_text,
        content_html=html,
        body_assets=body_assets or [],
    )
    return a


def test_text_paragraph():
    content = '{"type":"doc","content":[{"type":"paragraph","content":[{"type":"text","text":"Hello"}]}]}'
    segs = parse_body_segments(_article(content_json=content))
    texts = [s.text for s in segs if s.kind == "text"]
    assert any("Hello" in t for t in texts)


def test_image_segment_has_asset_id():
    content = '{"type":"doc","content":[{"type":"image","attrs":{"assetId":"abc123"}}]}'
    segs = parse_body_segments(_article(content_json=content))
    img_segs = [s for s in segs if s.kind == "image"]
    assert len(img_segs) == 1
    assert img_segs[0].image_asset_id == "abc123"
    assert img_segs[0].image_path is None  # not resolved yet


def test_fallback_to_plain_text():
    segs = parse_body_segments(_article(plain_text="fallback"))
    assert len(segs) == 1
    assert segs[0].kind == "text"
    assert segs[0].text == "fallback"


def test_empty_article_returns_empty():
    segs = parse_body_segments(_article())
    assert segs == []


def test_hard_break_produces_newline():
    content = '{"type":"doc","content":[{"type":"paragraph","content":[{"type":"text","text":"A"},{"type":"hardBreak"},{"type":"text","text":"B"}]}]}'
    segs = parse_body_segments(_article(content_json=content))
    full = "".join(s.text for s in segs if s.kind == "text")
    assert "A" in full and "B" in full and "\n" in full
```

- [ ] **Step 3: Run test to confirm it fails**

```bash
pytest server/tests/test_tiptap_parser.py -x -q
```

Expected: `ImportError` or `ModuleNotFoundError`

- [ ] **Step 4: Create server/app/modules/articles/tiptap_Parser.py**

```python
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class BodySegment:
    kind: str                           # "text" | "image"
    text: str = ""                      # populated for kind="text"
    image_path: Path | None = None      # populated after resolution in publish_Runner
    image_asset_id: str | None = None   # populated by parser; used for tracing


def _iter_nodes(node: Any) -> Iterable[dict[str, Any]]:
    if isinstance(node, dict):
        yield node
        content = node.get("content")
        if isinstance(content, list):
            for child in content:
                yield from _iter_nodes(child)
    elif isinstance(node, list):
        for child in node:
            yield from _iter_nodes(child)


def _asset_id_from_image_node(node: dict[str, Any]) -> str | None:
    attrs = node.get("attrs")
    if not isinstance(attrs, dict):
        return None
    for key in ("assetId", "asset_id", "dataAssetId"):
        value = attrs.get(key)
        if isinstance(value, str) and value:
            return value
    src = attrs.get("src")
    if isinstance(src, str) and "/api/assets/" in src:
        return src.rstrip("/").split("/api/assets/")[-1].split("?")[0]
    return None


def loads_content_json(raw: str) -> dict[str, Any]:
    if not raw:
        return {}
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}


def dumps_content_json(content_json: dict[str, Any]) -> str:
    return json.dumps(content_json, ensure_ascii=False, separators=(",", ":"))


def extract_body_image_nodes(content_json: dict[str, Any]) -> list[tuple[str, str | None]]:
    """Return list of (asset_id, editor_node_id) for every image node in document order."""
    result = []
    for node in _iter_nodes(content_json):
        if node.get("type") != "image":
            continue
        asset_id = _asset_id_from_image_node(node)
        if not asset_id:
            continue
        attrs = node.get("attrs") if isinstance(node.get("attrs"), dict) else {}
        editor_node_id = attrs.get("id") or attrs.get("nodeId")
        result.append((asset_id, editor_node_id))
    return result


def has_publishable_body(article: Any) -> bool:
    if (article.plain_text or "").strip():
        return True
    if re.sub(r"<[^>]+>", "", article.content_html or "").strip():
        return True
    return bool(extract_body_image_nodes(loads_content_json(article.content_json)))


def _append_segments(node: Any, segments: list[BodySegment], depth: int = 0) -> None:
    if isinstance(node, list):
        for child in node:
            _append_segments(child, segments, depth)
        return
    if not isinstance(node, dict):
        return

    node_type = node.get("type")

    if node_type == "text":
        text = node.get("text")
        if isinstance(text, str) and text:
            segments.append(BodySegment(kind="text", text=text))
        return

    if node_type == "hardBreak":
        segments.append(BodySegment(kind="text", text="\n"))
        return

    if node_type == "image":
        asset_id = _asset_id_from_image_node(node)
        if asset_id:
            segments.append(BodySegment(kind="image", image_asset_id=asset_id))
        return

    content = node.get("content")
    if isinstance(content, list):
        for child in content:
            _append_segments(
                child,
                segments,
                depth + (1 if node_type in ("orderedList", "bulletList") else 0),
            )

    if node_type in {"paragraph", "heading"}:
        segments.append(BodySegment(kind="text", text="\n"))


def _compact(segments: list[BodySegment]) -> list[BodySegment]:
    compacted: list[BodySegment] = []
    for seg in segments:
        if seg.kind == "text":
            if not seg.text:
                continue
            if seg.text == "\n":
                compacted.append(seg)
                continue
            if compacted and compacted[-1].kind == "text" and compacted[-1].text != "\n":
                prev = compacted.pop()
                compacted.append(BodySegment(kind="text", text=prev.text + seg.text))
            else:
                compacted.append(seg)
        else:
            compacted.append(seg)
    while compacted and compacted[-1].kind == "text" and not compacted[-1].text.strip():
        compacted.pop()
    return compacted


def parse_body_segments(article: Any) -> list[BodySegment]:
    """Parse article body into ordered text/image segments.

    Image segments have image_asset_id set and image_path=None.
    publish_Runner resolves image_path before passing to drivers.
    """
    content_json = loads_content_json(article.content_json)
    segments: list[BodySegment] = []
    _append_segments(content_json, segments)
    segments = _compact(segments)
    if segments:
        return segments
    body = (article.plain_text or re.sub(r"<[^>]+>", "", article.content_html or "")).strip()
    return [BodySegment(kind="text", text=body)] if body else []
```

- [ ] **Step 5: Run test to confirm it passes**

```bash
pytest server/tests/test_tiptap_parser.py -x -q
```

Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add server/app/modules/ server/tests/test_tiptap_parser.py
git commit -m "feat: add modules/articles/tiptap_Parser with parse_body_segments + BodySegment"
```

---

## Task 3: Create articles/asset_Store.py and article_Crud.py, wire __init__.py

**Files:**
- Create: `server/app/modules/articles/asset_Store.py`
- Create: `server/app/modules/articles/article_Crud.py`
- Modify: `server/app/modules/articles/__init__.py`
- Modify: `server/app/services/assets.py` (shim)
- Modify: `server/app/services/articles.py` (shim)
- Modify: `server/app/services/article_groups.py` (shim)

- [ ] **Step 1: Create server/app/modules/articles/asset_Store.py**

Copy content of `server/app/services/assets.py` verbatim. Change the one import line:

```python
# Change this line:
from server.app.services.errors import ClientError
# To:
from server.app.shared.errors import ClientError
```

All other content stays identical.

- [ ] **Step 2: Create server/app/modules/articles/article_Crud.py**

Copy content of `server/app/services/articles.py` verbatim. Change import lines:

```python
# Change:
from server.app.services.errors import ClientError, ConflictError
# To:
from server.app.shared.errors import ClientError, ConflictError
```

Also copy the group functions from `server/app/services/article_groups.py` into the bottom of this file. Change that file's imports similarly:

```python
from server.app.shared.errors import ClientError, ConflictError
```

- [ ] **Step 3: Write server/app/modules/articles/__init__.py**

```python
from server.app.modules.articles.tiptap_Parser import (  # noqa: F401
    BodySegment,
    parse_body_segments,
    has_publishable_body,
    loads_content_json,
    dumps_content_json,
    extract_body_image_nodes,
)
from server.app.modules.articles.asset_Store import (  # noqa: F401
    StoredAsset,
    store_bytes,
    store_upload,
    resolve_asset_path,
    guess_image_size,
    normalize_ext,
    asset_url,
)
from server.app.modules.articles.article_Crud import (  # noqa: F401
    get_article,
    list_articles,
    create_article,
    update_article,
    delete_article,
    article_has_publishable_body,
    sync_article_body_assets,
    ensure_asset_exists,
    validate_article_status,
    get_group,
    list_groups,
    create_group,
    update_group,
    delete_group,
)
```

- [ ] **Step 4: Replace services/assets.py with shim**

```python
# server/app/services/assets.py
from server.app.modules.articles.asset_Store import *  # noqa: F401, F403
from server.app.modules.articles.asset_Store import StoredAsset, store_bytes, store_upload, resolve_asset_path  # noqa: F401
```

- [ ] **Step 5: Replace services/articles.py with shim**

```python
# server/app/services/articles.py
from server.app.modules.articles.article_Crud import *  # noqa: F401, F403
from server.app.modules.articles.tiptap_Parser import *  # noqa: F401, F403
from server.app.modules.articles.tiptap_Parser import (  # noqa: F401
    BodySegment, loads_content_json, dumps_content_json,
    extract_body_image_nodes, has_publishable_body,
)
# Keep ImageNode alias for backward compat with any tests that import it
from server.app.modules.articles.tiptap_Parser import extract_body_image_nodes as _ebin  # noqa: F401
```

- [ ] **Step 6: Replace services/article_groups.py with shim**

```python
# server/app/services/article_groups.py
from server.app.modules.articles.article_Crud import (  # noqa: F401
    get_group, list_groups, create_group, update_group, delete_group,
)
```

- [ ] **Step 7: Run tests**

```bash
pytest server/tests/ -x -q
```

Expected: all green

- [ ] **Step 8: Commit**

```bash
git add server/app/modules/articles/ server/app/services/assets.py server/app/services/articles.py server/app/services/article_groups.py
git commit -m "refactor: create modules/articles/ (asset_Store, article_Crud, tiptap_Parser)"
```

---

## Task 4: Create accounts/ module

**Files:**
- Create: `server/app/modules/accounts/__init__.py`
- Create: `server/app/modules/accounts/account_Crud.py`
- Create: `server/app/modules/accounts/account_Auth.py`
- Create: `server/app/modules/accounts/browser_Session.py`
- Modify: `server/app/services/accounts.py` (shim)
- Modify: `server/app/services/browser_sessions.py` (shim)

- [ ] **Step 1: Create server/app/modules/accounts/account_Auth.py**

Copy from `server/app/services/accounts.py` the following functions and constants (everything related to login session management and path utilities):

```
account_key_from_state_path, profile_dir_for_key, state_path_for_key,
state_dir_for_key, relative_to_data_dir, normalize_account_key,
launch_options, get_or_create_platform,
BrowserCheckResult, AccountBrowserSessionResult, LoginBrowserSessionHandle,
LOGIN_STATUS_* constants, LOGIN_TERMINAL_STATUSES,
LOGIN_SESSION_*_TIMEOUT_SECONDS, LOGIN_SESSION_POLL_SECONDS,
_run_in_plain_thread,
process_account_login_session_requests (and its helpers),
start_login_session, finish_login_session, cancel_login_session,
get_login_session_status
```

Change import:
```python
from server.app.shared.errors import ClientError
```

- [ ] **Step 2: Create server/app/modules/accounts/account_Crud.py**

Copy from `server/app/services/accounts.py` the CRUD and import/export functions:

```
get_account, list_accounts, create_account, update_account, delete_account,
export_account, import_account (and their helpers),
check_account_login (the non-browser-session version)
```

Change import:
```python
from server.app.shared.errors import ClientError, AccountError
from server.app.modules.accounts.account_Auth import (
    account_key_from_state_path, profile_dir_for_key, state_path_for_key,
    state_dir_for_key, relative_to_data_dir, normalize_account_key,
    get_or_create_platform,
)
```

- [ ] **Step 3: Create server/app/modules/accounts/browser_Session.py**

Copy content of `server/app/services/browser_sessions.py` verbatim. Change imports:

```python
# No services/ imports in browser_sessions.py currently; no changes needed.
```

- [ ] **Step 4: Write server/app/modules/accounts/__init__.py**

```python
from server.app.modules.accounts.account_Auth import (  # noqa: F401
    account_key_from_state_path,
    profile_dir_for_key,
    state_path_for_key,
    launch_options,
    get_or_create_platform,
    BrowserCheckResult,
    LoginBrowserSessionHandle,
    process_account_login_session_requests,
    start_login_session,
    finish_login_session,
    cancel_login_session,
    get_login_session_status,
)
from server.app.modules.accounts.account_Crud import (  # noqa: F401
    get_account,
    list_accounts,
    create_account,
    update_account,
    delete_account,
    export_account,
    import_account,
)
from server.app.modules.accounts.browser_Session import (  # noqa: F401
    RemoteBrowserSession,
    get_or_create_account_session,
    stop_remote_browser_session,
    attach_browser_handles,
    keep_session_alive,
    associate_record_with_session,
    disassociate_record,
    get_session_for_record,
    _reset_globals,
)
```

- [ ] **Step 5: Replace services/accounts.py with shim**

```python
# server/app/services/accounts.py
from server.app.modules.accounts.account_Auth import *  # noqa: F401, F403
from server.app.modules.accounts.account_Crud import *  # noqa: F401, F403
```

- [ ] **Step 6: Replace services/browser_sessions.py with shim**

```python
# server/app/services/browser_sessions.py
from server.app.modules.accounts.browser_Session import *  # noqa: F401, F403
```

- [ ] **Step 7: Run tests**

```bash
pytest server/tests/ -x -q
```

Expected: all green

- [ ] **Step 8: Commit**

```bash
git add server/app/modules/accounts/ server/app/services/accounts.py server/app/services/browser_sessions.py
git commit -m "refactor: create modules/accounts/ (account_Crud, account_Auth, browser_Session)"
```

---

## Task 5: Create tasks/drivers/ — driver_Base.py with PublishPayload, updated toutiao.py

This is the most significant change: `PublishPayload` DTO is introduced, and `toutiao.publish()` signature changes from `(page, context, article, account, state_path, stop_before_publish)` to `(page, context, payload, stop_before_publish)`.

**Files:**
- Create: `server/app/modules/tasks/__init__.py` (empty for now)
- Create: `server/app/modules/tasks/drivers/__init__.py`
- Create: `server/app/modules/tasks/drivers/driver_Base.py`
- Create: `server/app/modules/tasks/drivers/toutiao.py`
- Create: `server/tests/test_publish_payload.py`

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p server/app/modules/tasks/drivers
touch server/app/modules/tasks/__init__.py
touch server/app/modules/tasks/drivers/__init__.py
```

- [ ] **Step 2: Create server/app/modules/tasks/drivers/driver_Base.py**

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from server.app.modules.articles.tiptap_Parser import BodySegment  # noqa: F401 — re-exported for drivers


@dataclass(frozen=True)
class PublishPayload:
    """Fully-resolved publish data passed to platform drivers.

    Assembled by publish_Runner before calling driver.publish().
    Drivers must not import articles or assets modules directly.
    """
    title: str
    author: str
    cover_image_path: Path | None       # None → driver must raise PublishError
    body_segments: list[BodySegment]    # image segments have image_path resolved
    article_id: int                     # for logging/tracing only
    account_id: int                     # for logging/tracing only


@dataclass(frozen=True)
class PublishResult:
    url: str | None
    title: str
    message: str


class PublishError(Exception):
    """Platform-neutral publish failure with optional diagnostic screenshot."""

    def __init__(self, message: str, screenshot: bytes | None = None):
        super().__init__(message)
        self.screenshot = screenshot


class UserInputRequired(PublishError):
    """Raised when publishing must pause for login, captcha, or similar input."""

    def __init__(
        self,
        message: str,
        screenshot: bytes | None = None,
        session_id: str | None = None,
        novnc_url: str | None = None,
        error_type: str = "login_required",
    ):
        super().__init__(message, screenshot)
        self.session_id = session_id
        self.novnc_url = novnc_url
        self.error_type = error_type
```

- [ ] **Step 3: Write failing test for PublishPayload structure**

```python
# server/tests/test_publish_payload.py
from __future__ import annotations
from pathlib import Path
from server.app.modules.tasks.drivers.driver_Base import PublishPayload, PublishResult, PublishError
from server.app.modules.articles.tiptap_Parser import BodySegment


def test_publish_payload_is_frozen():
    payload = PublishPayload(
        title="Test",
        author="Author",
        cover_image_path=Path("/tmp/cover.jpg"),
        body_segments=[BodySegment(kind="text", text="Hello")],
        article_id=1,
        account_id=2,
    )
    import pytest
    with pytest.raises((TypeError, AttributeError)):
        payload.title = "Modified"  # type: ignore


def test_publish_payload_no_cover():
    payload = PublishPayload(
        title="Test",
        author="Author",
        cover_image_path=None,
        body_segments=[],
        article_id=1,
        account_id=2,
    )
    assert payload.cover_image_path is None


def test_body_segment_image_has_path():
    seg = BodySegment(kind="image", image_path=Path("/tmp/img.jpg"), image_asset_id="abc")
    assert seg.image_path == Path("/tmp/img.jpg")
    assert seg.image_asset_id == "abc"
    assert seg.text == ""
```

- [ ] **Step 4: Run test to confirm it fails**

```bash
pytest server/tests/test_publish_payload.py -x -q
```

Expected: `ImportError`

- [ ] **Step 5: Run test to confirm it passes (after driver_Base.py is created)**

```bash
pytest server/tests/test_publish_payload.py -x -q
```

Expected: 3 passed

- [ ] **Step 6: Create server/app/modules/tasks/drivers/toutiao.py**

Copy content of `server/app/services/drivers/toutiao.py` with these changes:

**a) Update imports:**

```python
# Remove:
from server.app.services.articles import article_has_publishable_body, loads_content_json
from server.app.services.assets import resolve_asset_path
from server.app.services.drivers.base import PublishError, PublishResult, UserInputRequired
from server.app.services.publish_diagnostics import publish_step, record_publish_diagnostic

# Add:
from server.app.modules.tasks.drivers.driver_Base import (
    PublishPayload, PublishResult, PublishError, UserInputRequired,
)
from server.app.shared.diagnostics import publish_step, record_publish_diagnostic
```

**b) Remove these functions** (now handled by `tiptap_Parser.parse_body_segments` in `publish_Runner`):
- `_body_segments(article)`
- `_append_tiptap_segments(node, segments, depth)`
- `_compact_segments(segments)`
- `_asset_id_from_tiptap_image(node)`
- `_body_asset_for_segment(article, segment)`
- The `BodySegment` dataclass definition

**c) Update `_fill_body` signature from `(page, article)` to `(page, payload: PublishPayload)`:**

```python
def _fill_body(page: Any, payload: PublishPayload) -> None:
    """Fill body using pre-resolved segments from PublishPayload."""
    segments = payload.body_segments
    if not segments:
        raise ToutiaoPublishError("文章正文为空")

    _focus_body_editor(page)
    for segment in segments:
        if segment.kind == "text":
            _insert_body_text(page, segment.text)
        elif segment.kind == "image":
            if segment.image_path is None:
                raise ToutiaoPublishError(f"正文图片路径未解析: {segment.image_asset_id}")
            _dismiss_blocking_popups(page)
            _paste_body_image_from_path(page, segment.image_path)
            _focus_body_editor(page)
            page.keyboard.press("End")
            page.keyboard.press("Enter")
```

**d) Rename `_paste_body_image(page, asset)` to `_paste_body_image_from_path(page, image_path: Path)`:**

Replace the `asset` parameter usage with the direct `image_path: Path` argument. Specifically, the function currently calls `resolve_asset_path(asset)` to get the path — remove that call and use `image_path` directly. The `_maybe_resize_for_upload` call remains unchanged.

**e) Update `_handle_cover` to accept `cover_image_path: Path | None` instead of `article`:**

```python
def _handle_cover(page: Any, cover_image_path: Path | None) -> None:
    if cover_image_path is None:
        raise ToutiaoPublishError("封面图片是必填项")
    # rest of the function uses cover_image_path directly (was: resolve_asset_path(article.cover_asset))
```

**f) Update the `publish()` method signature and body:**

```python
def publish(
    self,
    *,
    page: Any,
    context: Any,
    payload: PublishPayload,
    stop_before_publish: bool = False,
) -> PublishResult:
    # Replace all `article.xxx` references with `payload.xxx`
    # _fill_title(page, payload.title)
    # _fill_body(page, payload)
    # _handle_cover(page, payload.cover_image_path)
    ...
```

- [ ] **Step 7: Update tasks/drivers/__init__.py**

Copy content of `server/app/services/drivers/__init__.py`. Update imports and the `PlatformDriver` Protocol:

```python
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from server.app.modules.tasks.drivers.driver_Base import PublishPayload, PublishResult


@runtime_checkable
class PlatformDriver(Protocol):
    code: str
    name: str
    home_url: str
    publish_url: str

    def detect_logged_in(self, *, url: str, title: str, body: str) -> bool: ...

    def publish(
        self,
        *,
        page: Any,
        context: Any,
        payload: PublishPayload,
        stop_before_publish: bool,
    ) -> PublishResult: ...


_REGISTRY: dict[str, PlatformDriver] = {}


def register(driver: PlatformDriver) -> None:
    if driver.code in _REGISTRY:
        raise ValueError(f"Driver already registered: {driver.code}")
    _REGISTRY[driver.code] = driver


def get_driver(platform_code: str) -> PlatformDriver:
    if platform_code not in _REGISTRY:
        raise ValueError(f"Unknown platform: {platform_code}")
    return _REGISTRY[platform_code]


def all_driver_codes() -> list[str]:
    return sorted(_REGISTRY.keys())
```

Then add at the bottom:
```python
from server.app.modules.tasks.drivers import toutiao  # noqa: F401 — triggers register()
```

- [ ] **Step 8: Run tests**

```bash
pytest server/tests/ -x -q
```

Expected: most green; `test_publish_runner.py` will fail because it still imports from `services.publish_runner` with old driver signature — that's expected and will be fixed in Task 6.

- [ ] **Step 9: Commit**

```bash
git add server/app/modules/tasks/drivers/ server/tests/test_publish_payload.py
git commit -m "feat: add tasks/drivers/ with PublishPayload DTO, update toutiao.publish() signature"
```

---

## Task 6: Create tasks/task_Crud.py, publish_Runner.py, task_Executor.py; wire __init__.py

**Files:**
- Create: `server/app/modules/tasks/task_Crud.py`
- Create: `server/app/modules/tasks/publish_Runner.py`
- Create: `server/app/modules/tasks/task_Executor.py`
- Modify: `server/app/modules/tasks/__init__.py`
- Modify: `server/app/services/drivers/__init__.py` (shim)
- Modify: `server/app/services/drivers/base.py` (shim)
- Modify: `server/app/services/drivers/toutiao.py` (shim)
- Modify: `server/app/services/publish_runner.py` (shim)
- Modify: `server/app/services/tasks.py` (shim)
- Modify: `server/tests/test_publish_runner.py` (update to new module + signature)

- [ ] **Step 1: Create server/app/modules/tasks/task_Crud.py**

Copy from `server/app/services/tasks.py` all DB-only functions (no threads, no Playwright):

```
get_task, list_tasks, create_task, cancel_task,
list_task_records, list_task_logs, preview_task_assignment,
manual_confirm_record, resolve_user_input_record,
recover_stuck_records, recover_stuck_task_claims,
build_publish_runner_for_record,
TERMINAL_TASK_STATUSES, _aggregate_task_status, _claim_record, etc.
```

Change imports:
```python
from server.app.shared.errors import AccountError, ClientError, ConflictError, ValidationError
from server.app.modules.articles import has_publishable_body
from server.app.modules.accounts import get_account
```

- [ ] **Step 2: Create server/app/modules/tasks/publish_Runner.py**

This file replaces `services/publish_runner.py` AND adds the `PublishPayload` assembly logic. The critical new section is building `PublishPayload` before calling `driver.publish()`.

```python
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from server.app.core.paths import get_data_dir
from server.app.models import Account, Article
from server.app.modules.accounts import (
    account_key_from_state_path,
    attach_browser_handles,
    get_or_create_account_session,
    keep_session_alive,
    launch_options,
    profile_dir_for_key,
    stop_remote_browser_session,
)
from server.app.modules.articles import (
    parse_body_segments,
    resolve_asset_path,
)
from server.app.modules.tasks.drivers import get_driver
from server.app.modules.tasks.drivers.driver_Base import (
    PublishError,
    PublishPayload,
    PublishResult,
    UserInputRequired,
)
from server.app.modules.articles.tiptap_Parser import BodySegment
from server.app.shared.diagnostics import publish_step, record_publish_diagnostic

_logger = logging.getLogger(__name__)


def _build_payload(article: Article, account: Account) -> PublishPayload:
    """Resolve all article data into a fully self-contained PublishPayload.

    After this function returns, the driver needs no DB access and no module imports.
    Log the segment count here — if publish content is missing, check this log first.
    """
    raw_segments = parse_body_segments(article)

    resolved_segments: list[BodySegment] = []
    for seg in raw_segments:
        if seg.kind == "image" and seg.image_asset_id is not None:
            # Find the asset in body_assets (already loaded via selectinload in tasks)
            asset = next(
                (link.asset for link in article.body_assets if link.asset_id == seg.image_asset_id),
                None,
            )
            if asset is not None:
                resolved_segments.append(
                    BodySegment(
                        kind="image",
                        image_path=resolve_asset_path(asset),
                        image_asset_id=seg.image_asset_id,
                    )
                )
            else:
                _logger.warning(
                    "publish payload: image asset %s not found in body_assets for article %d — skipping",
                    seg.image_asset_id,
                    article.id,
                )
        else:
            resolved_segments.append(seg)

    cover_path: Path | None = None
    if article.cover_asset is not None:
        cover_path = resolve_asset_path(article.cover_asset)

    _logger.debug(
        "publish payload built: article_id=%d segments=%d (images=%d) cover=%s",
        article.id,
        len(resolved_segments),
        sum(1 for s in resolved_segments if s.kind == "image"),
        cover_path,
    )

    return PublishPayload(
        title=article.title or "",
        author=article.author or "",
        cover_image_path=cover_path,
        body_segments=resolved_segments,
        article_id=article.id,
        account_id=account.id,
    )


def _short_url(url: str, limit: int = 180) -> str:
    return url if len(url) <= limit else f"{url[:limit]}..."


def _attach_page_network_diagnostics(page: Any) -> None:
    # Copy verbatim from services/publish_runner.py
    ...


def _clear_profile_locks(profile_dir: Path) -> None:
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        lock = profile_dir / name
        try:
            lock.unlink(missing_ok=True)
        except OSError:
            pass


def run_publish(
    *,
    article: Article,
    account: Account,
    channel: str = "chromium",
    executable_path: str | None = None,
    stop_before_publish: bool = False,
) -> PublishResult:
    """Assemble PublishPayload, start/reuse browser session, call driver.publish()."""
    if not article.title or not article.title.strip():
        raise PublishError("标题不能为空")
    if article.cover_asset is None:
        raise PublishError("封面图片是必填项")

    platform_code, account_key = account_key_from_state_path(account.state_path)
    state_path = (get_data_dir() / account.state_path).resolve()
    if not state_path.exists():
        raise PublishError(f"Account storage state not found: {account.state_path}")

    driver = get_driver(platform_code)

    with publish_step("build publish payload"):
        payload = _build_payload(article, account)

    with publish_step("remote browser session"):
        session = get_or_create_account_session(platform_code, account_key)

    if session.browser_context is None:
        pw = None
        try:
            with publish_step("start Playwright"):
                pw = sync_playwright().start()
            with publish_step("launch Chromium"):
                _clear_profile_locks(profile_dir_for_key(platform_code, account_key))
                options = launch_options(channel, executable_path)
                options["env"] = {**os.environ, "DISPLAY": session.display}
                context = pw.chromium.launch_persistent_context(
                    user_data_dir=str(profile_dir_for_key(platform_code, account_key)),
                    **options,
                )
                context.set_default_navigation_timeout(30000)
                grant_permissions = getattr(context, "grant_permissions", None)
                if callable(grant_permissions):
                    grant_permissions(["clipboard-read", "clipboard-write"])
                attach_browser_handles(session.id, pw, context, None)
        except Exception:
            if pw is not None:
                try:
                    pw.stop()
                except Exception:
                    pass
            stop_remote_browser_session(session.id)
            raise
    else:
        context = session.browser_context

    page = None
    _keep_browser = False
    try:
        page = context.new_page()
        _attach_page_network_diagnostics(page)
        with publish_step("driver publish flow", page=page):
            return driver.publish(
                page=page,
                context=context,
                payload=payload,
                stop_before_publish=stop_before_publish,
            )
    except UserInputRequired as exc:
        _keep_browser = True
        exc.session_id = session.id
        exc.novnc_url = session.novnc_url
        keep_session_alive(session.id)
        raise
    finally:
        if not _keep_browser:
            if page is not None:
                try:
                    page.close()
                except Exception:
                    pass
```

- [ ] **Step 3: Create server/app/modules/tasks/task_Executor.py**

Copy the threading/execution logic from `server/app/services/tasks.py`:
- `execute_task`, `_run_pending_records`, `_publish_record`, `_start_runnable_records`, `_finish_record_future`
- `_task_locks`, `_account_locks`, `_task_cancel`, `MAX_CONCURRENT_RECORDS`
- `build_publish_runner_for_record`

Change imports:
```python
from server.app.modules.tasks.task_Crud import (
    get_task, list_task_records, recover_stuck_records, ...
)
from server.app.modules.tasks.publish_Runner import run_publish
from server.app.modules.accounts import get_account
from server.app.modules.articles import get_article
from server.app.shared.errors import AccountError
from server.app.shared.diagnostics import capture_publish_diagnostics
```

- [ ] **Step 4: Write server/app/modules/tasks/__init__.py**

```python
from server.app.modules.tasks.task_Crud import (  # noqa: F401
    get_task,
    list_tasks,
    create_task,
    cancel_task,
    list_task_records,
    list_task_logs,
    preview_task_assignment,
    manual_confirm_record,
    resolve_user_input_record,
    TERMINAL_TASK_STATUSES,
)
from server.app.modules.tasks.task_Executor import (  # noqa: F401
    execute_task,
    recover_stuck_records,
    build_publish_runner_for_record,
)
```

- [ ] **Step 5: Add shims in services/drivers/**

```python
# server/app/services/drivers/base.py
from server.app.modules.tasks.drivers.driver_Base import *  # noqa: F401, F403
from server.app.modules.tasks.drivers.driver_Base import PublishResult, PublishError, UserInputRequired  # noqa: F401
```

```python
# server/app/services/drivers/__init__.py
from server.app.modules.tasks.drivers import *  # noqa: F401, F403
from server.app.modules.tasks.drivers import get_driver, register, all_driver_codes  # noqa: F401
```

```python
# server/app/services/drivers/toutiao.py
from server.app.modules.tasks.drivers.toutiao import *  # noqa: F401, F403
from server.app.modules.tasks.drivers.toutiao import ToutiaoDriver, ToutiaoPublishError, ToutiaoUserInputRequired  # noqa: F401
PublishFillResult = __import__("server.app.modules.tasks.drivers.driver_Base", fromlist=["PublishResult"]).PublishResult
```

```python
# server/app/services/publish_runner.py
from server.app.modules.tasks.publish_Runner import *  # noqa: F401, F403
from server.app.modules.tasks.publish_Runner import run_publish  # noqa: F401
```

```python
# server/app/services/tasks.py
from server.app.modules.tasks.task_Crud import *  # noqa: F401, F403
from server.app.modules.tasks.task_Executor import *  # noqa: F401, F403
```

- [ ] **Step 6: Update test_publish_runner.py**

The test currently patches `server.app.services.publish_runner.*` and uses the old driver signature `(page, context, article, account, state_path, stop_before_publish)`. Update it to patch `server.app.modules.tasks.publish_Runner.*` and use new signature `(page, context, payload, stop_before_publish)`.

Key changes:
```python
# Old stub driver publish:
def publish(self, *, page, context, article, account, state_path, stop_before_publish):
    ...

# New stub driver publish:
def publish(self, *, page, context, payload, stop_before_publish):
    ...

# Old patch path:
monkeypatch.setattr("server.app.services.publish_runner.get_data_dir", ...)
# New patch path:
monkeypatch.setattr("server.app.modules.tasks.publish_Runner.get_data_dir", ...)
# (repeat for all patched symbols)

# Add _build_payload patch to return a stub payload:
from server.app.modules.tasks.drivers.driver_Base import PublishPayload
from server.app.modules.articles.tiptap_Parser import BodySegment
stub_payload = PublishPayload(
    title="Test Article", author="", cover_image_path=None,
    body_segments=[], article_id=1, account_id=1,
)
monkeypatch.setattr(
    "server.app.modules.tasks.publish_Runner._build_payload",
    lambda article, account: stub_payload,
)
```

Also update the import at top of test file:
```python
from server.app.modules.tasks.drivers.driver_Base import PublishResult, UserInputRequired
# (instead of from server.app.services.drivers.toutiao import PublishFillResult, ToutiaoUserInputRequired)
```

- [ ] **Step 7: Run tests**

```bash
pytest server/tests/ -x -q
```

Expected: all green

- [ ] **Step 8: Commit**

```bash
git add server/app/modules/tasks/ server/app/services/drivers/ server/app/services/publish_runner.py server/app/services/tasks.py server/tests/test_publish_runner.py
git commit -m "refactor: create modules/tasks/ (task_Crud, publish_Runner with PublishPayload, task_Executor)"
```

---

## Task 7: Update all routes, main.py, and worker/executor.py

**Files:**
- Modify: `server/app/api/routes/articles.py`
- Modify: `server/app/api/routes/article_groups.py`
- Modify: `server/app/api/routes/assets.py`
- Modify: `server/app/api/routes/accounts.py`
- Modify: `server/app/api/routes/tasks.py`
- Modify: `server/app/api/routes/publish_records.py`
- Modify: `server/app/api/routes/system.py`
- Modify: `server/app/main.py`
- Modify: `server/worker/executor.py`

- [ ] **Step 1: Update routes/articles.py**

Replace all `from server.app.services.articles import ...` with `from server.app.modules.articles import ...`

Replace all `from server.app.services.article_groups import ...` with `from server.app.modules.articles import ...`

- [ ] **Step 2: Update routes/assets.py**

Replace `from server.app.services.assets import ...` with `from server.app.modules.articles import ...`

- [ ] **Step 3: Update routes/accounts.py**

Replace `from server.app.services.accounts import ...` with `from server.app.modules.accounts import ...`

Replace `from server.app.services.browser_sessions import ...` with `from server.app.modules.accounts import ...`

- [ ] **Step 4: Update routes/tasks.py**

Replace `from server.app.services.tasks import ...` with `from server.app.modules.tasks import ...`

- [ ] **Step 5: Update routes/publish_records.py**

Replace all services imports with modules equivalents.

- [ ] **Step 6: Update routes/system.py**

Replace all services imports with modules/shared equivalents.

- [ ] **Step 7: Update server/app/main.py**

Replace:
```python
import server.app.services.drivers.toutiao  # noqa: F401
```
With:
```python
import server.app.modules.tasks.drivers.toutiao  # noqa: F401
```

Also update any error handler imports from `services.errors` to `shared.errors`:
```python
from server.app.shared.errors import AccountError, ClientError, ConflictError, ValidationError
```

- [ ] **Step 8: Update server/worker/executor.py**

Replace all `from server.app.services.tasks import ...` with `from server.app.modules.tasks import ...`

- [ ] **Step 9: Run tests**

```bash
pytest server/tests/ -x -q
```

Expected: all green

- [ ] **Step 10: Commit**

```bash
git add server/app/api/routes/ server/app/main.py server/worker/executor.py
git commit -m "refactor: update all routes and worker to import from modules/ and shared/"
```

---

## Task 8: Cleanup — remove shims and delete services/

- [ ] **Step 1: Delete all shim files in services/**

```bash
rm server/app/services/errors.py
rm server/app/services/publish_diagnostics.py
rm server/app/services/feishu.py
rm server/app/services/assets.py
rm server/app/services/articles.py
rm server/app/services/article_groups.py
rm server/app/services/accounts.py
rm server/app/services/browser_sessions.py
rm server/app/services/publish_runner.py
rm server/app/services/tasks.py
rm server/app/services/serializers.py  # move to api/serializers.py if still needed
rm server/app/services/system_status.py  # move to shared/system_Status.py if still needed
rm server/app/services/clipboard.py
rm -rf server/app/services/drivers/
rm server/app/services/__init__.py
rmdir server/app/services/
```

> Note: `serializers.py`, `system_status.py`, and `clipboard.py` are utility files. Before deleting, check where they are imported and move them to `shared/` or `api/` accordingly.

- [ ] **Step 2: Run tests**

```bash
pytest server/tests/ -x -q
```

Expected: all green — this confirms no module still depends on services/

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "refactor: delete services/ — all code migrated to modules/ and shared/"
```

---

## Task 9: Frontend — split web/src/api/client.ts

**Files:**
- Create: `web/src/api/articles.ts`
- Create: `web/src/api/accounts.ts`
- Create: `web/src/api/tasks.ts`
- Create: `web/src/api/assets.ts`
- Modify: `web/src/api/client.ts` (barrel re-export or keep shared types only)

- [ ] **Step 1: Read current web/src/api/client.ts**

Identify all API call functions and group them by domain:
- Article functions → `articles.ts`
- Account functions → `accounts.ts`
- Task/record functions → `tasks.ts`
- Asset functions → `assets.ts`

- [ ] **Step 2: Create web/src/api/articles.ts**

Move article and article-group API functions. Keep the same function signatures — only move, don't rename.

- [ ] **Step 3: Create web/src/api/accounts.ts**

Move account, login-session API functions.

- [ ] **Step 4: Create web/src/api/tasks.ts**

Move task, publish-record, manual-confirm API functions.

- [ ] **Step 5: Create web/src/api/assets.ts**

Move asset upload/fetch functions.

- [ ] **Step 6: Update web/src/api/client.ts to barrel re-export**

```typescript
// web/src/api/client.ts
// Shared HTTP client and types only — import domain functions from their specific files
export * from "./articles";
export * from "./accounts";
export * from "./tasks";
export * from "./assets";
```

- [ ] **Step 7: Update all feature component imports**

Search for `from "../api/client"` or `from "../../api/client"` across the frontend and update to import from the specific domain file where possible. Barrel import still works so this is optional but preferred.

```bash
grep -r "from.*api/client" web/src --include="*.tsx" --include="*.ts" -l
```

- [ ] **Step 8: Build frontend to check for TypeScript errors**

```bash
pnpm --filter @geo/web build
```

Expected: build succeeds with no type errors

- [ ] **Step 9: Commit**

```bash
git add web/src/api/
git commit -m "refactor: split web/src/api/client.ts into domain files (articles, accounts, tasks, assets)"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Covered by task |
|-----------------|-----------------|
| `modules/articles/` with `article_Crud`, `tiptap_Parser`, `asset_Store` | Tasks 2–3 |
| `modules/accounts/` with `account_Crud`, `account_Auth`, `browser_Session` | Task 4 |
| `modules/tasks/` with `task_Crud`, `task_Executor`, `publish_Runner`, `drivers/` | Tasks 5–6 |
| `shared/` (errors, diagnostics, feishu) | Task 1 |
| `PublishPayload` DTO with `BodySegment` | Tasks 2, 5 |
| Drivers receive `PublishPayload`, don't import articles/assets | Task 5 |
| `publish_Runner._build_payload()` logs segment count | Task 6 |
| All routes updated | Task 7 |
| `services/` deleted | Task 8 |
| Frontend API split | Task 9 |
| Camel-case file naming (`article_Crud`, `tiptap_Parser`, etc.) | All tasks |
| `pytest` green at every step | Every task |

**Type consistency check:**

- `BodySegment` defined in `tiptap_Parser.py`, imported by `driver_Base.py` and `publish_Runner.py` ✓
- `PublishPayload` defined in `driver_Base.py`, used in `publish_Runner.py` and `toutiao.py` ✓
- `parse_body_segments` returns `list[BodySegment]` in Task 2; `publish_Runner` consumes `list[BodySegment]` in Task 6 ✓
- Driver `publish()` signature: `(page, context, payload: PublishPayload, stop_before_publish: bool)` consistent in Protocol (Task 5) and toutiao.py (Task 5) ✓
