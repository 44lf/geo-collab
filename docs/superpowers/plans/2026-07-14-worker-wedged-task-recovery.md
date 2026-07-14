# Worker 卡死发布线程拖垮修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让一条卡死的发布线程不再冻死整个单线程 worker——主循环无前进 K 轮即 park+return，worker 冷却跳过该任务先跑别的；容器回收 chrome 僵尸。

**Architecture:** 两个改动。① `_run_pending_records` 连续 `PARK_STALL_THRESHOLD` 轮"无 running + 零启动 + 有 pending"即判 parked 并 return（记录留 pending），parked 经 `execute_task_with_parked` 透给 worker（公开 `execute_task` 签名不变）；worker 用可配冷却 skip-map + `_claim_next_task` 的 `notin_` 跳过卡死任务、先跑下一个非 parked 的最旧任务。② worker 服务加 `init: true` 收 `<defunct>` chrome。**不回收**卡死记录占用的闸槽/账号锁（保持 ≤`MAX_CONCURRENT_RECORDS` 硬上限，回收留后续 SIGKILL 收割 spec）。

**Tech Stack:** Python 3.12 / SQLAlchemy / pytest（MySQL，`@pytest.mark.mysql`）/ Docker Compose。设计稿 `docs/superpowers/specs/2026-07-14-worker-wedged-task-recovery-design.md`。

## Global Constraints

- **#2 不变式**：卡死（`terminated=False`）时绝不释放账号锁 + profile 锁。本次也**不释放全局闸槽**（评审否决：会破坏并发上限、需真杀 chrome 才安全）——`_handle_timed_out_record` 不动。
- **不破坏 `execute_task(db, task) -> PublishTask` 签名**：现有调用方（`router.py:309/529`、worker）不变；parked 经新 `execute_task_with_parked` 透出，不加模块级 side-channel。
- **测试 lazy import 纪律**：`server/worker/executor.py` 顶层 import 拉 `db.session`（非 collection-safe）→ 测试**一律函数内 import worker.executor**。`server/app/modules/tasks/executor.py` 顶层 import 是 collection-safe（`db.session` 仅在 `_make_commit_guard` 内 lazy import），可顶层 import。
- **MySQL only**：涉 DB 用例标 `@pytest.mark.mysql`、需 `GEO_TEST_DATABASE_URL`（库名含 `test`）。
- **无 DB 迁移、无 base 镜像变化**。
- **上线非例行**：走正常 MR→CI，**部署前停下等确认**。

---

### Task 1: 改动①b — `_run_pending_records` 有界 park + `execute_task_with_parked` 透出

**Files:**
- Modify: `server/app/modules/tasks/executor.py`
- Test: `server/tests/test_run_pending_records_park.py`（新建，mysql）

**Interfaces:**
- Produces:
  - `PARK_STALL_THRESHOLD: int`（默认 5；连续 N 轮无前进即 park）
  - `_run_pending_records(db, task) -> bool`（返回本次是否 park；原返回 None）
  - `execute_task_with_parked(db, task) -> tuple[PublishTask, bool]`（worker 专用）
  - `execute_task(db, task) -> PublishTask`（签名不变，内部丢弃 parked）
- Consumes: 现有 `_start_runnable_records` / `running` / `_task_cancel` / `_try_acquire_account_lock`。

- [ ] **Step 1: 写失败测试（先红）**

新建 `server/tests/test_run_pending_records_park.py`：

```python
"""改动①b：卡死线程占着进程内账号锁时，主循环有界 park+return，不再无限空转冻死单线程 worker。"""

from __future__ import annotations

import pytest

from server.app.modules.tasks.models import PublishRecord, PublishTask
from server.tests.utils import build_test_app


@pytest.mark.mysql
def test_execute_task_reports_park_when_account_lock_leaked(monkeypatch):
    test_app = build_test_app(monkeypatch)
    try:
        from server.app.modules.tasks import executor as ex
        from server.tests.test_worker_executor import _create_publishable_task

        monkeypatch.setattr(ex, "PARK_STALL_THRESHOLD", 2)  # 2 × 0.2s，快
        task_id = _create_publishable_task(test_app)
        with test_app.session_factory() as db:
            rec = db.query(PublishRecord).filter_by(task_id=task_id).first()
            rec_id, account_id = rec.id, rec.account_id

        assert ex._try_acquire_account_lock(account_id)  # 模拟卡死线程占着的账号锁
        try:
            with test_app.session_factory() as db:
                task = db.get(PublishTask, task_id)
                result, parked = ex.execute_task_with_parked(db, task)
            assert parked is True
            assert result.id == task_id
            with test_app.session_factory() as db:
                assert db.get(PublishRecord, rec_id).status == "pending"  # park 不改终态
            assert ex._try_acquire_account_lock(account_id) is False  # 账号锁仍被占（未误放）
        finally:
            ex._release_account_lock(account_id)
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_execute_task_public_signature_returns_publishtask(monkeypatch):
    """execute_task 对外仍只返回 PublishTask（不返回 tuple）——现有 API/pipeline 调用方不破。"""
    test_app = build_test_app(monkeypatch)
    try:
        from server.app.modules.tasks import executor as ex
        from server.tests.test_worker_executor import _create_publishable_task

        monkeypatch.setattr(ex, "PARK_STALL_THRESHOLD", 2)
        task_id = _create_publishable_task(test_app)
        with test_app.session_factory() as db:
            account_id = db.query(PublishRecord).filter_by(task_id=task_id).first().account_id

        assert ex._try_acquire_account_lock(account_id)
        try:
            with test_app.session_factory() as db:
                task = db.get(PublishTask, task_id)
                result = ex.execute_task(db, task)
            assert isinstance(result, PublishTask)
        finally:
            ex._release_account_lock(account_id)
    finally:
        test_app.cleanup()
```

- [ ] **Step 2: 运行测试，确认先红**

Run: `pytest server/tests/test_run_pending_records_park.py -q`
Expected: FAIL —— `execute_task_with_parked` / `PARK_STALL_THRESHOLD` 未定义（AttributeError）。

- [ ] **Step 3: 加 `PARK_STALL_THRESHOLD` 常量（实现其一）**

在 `server/app/modules/tasks/executor.py` 的 `MAX_CONCURRENT_RECORDS = 5` 附近加：

```python
# 主循环连续 N 轮"无 running + 本轮零启动 + 仍有 pending"→ 判无前进可能（多为卡死线程占着
# 进程内账号锁/闸槽），park 并 return，交 worker 冷却跳过。5 × 0.2s ≈ 1s 容忍窗。
PARK_STALL_THRESHOLD = 5
```

- [ ] **Step 4: 拆 `execute_task` 为 `_execute_task_impl` + 加 `execute_task_with_parked`（实现其二）**

把现有 `execute_task`（`executor.py:117-179`）改为薄壳 + 内部实现（claim / 锁 / finally 逻辑原样搬进 `_execute_task_impl`，只把 `_run_pending_records` 返回值捕获为 `parked` 并随 task 一起返回）：

```python
def execute_task(db: Session, task: PublishTask) -> PublishTask:
    """执行一个任务：把 pending 记录跑成发布，阻塞到本批次记录全部收口或暂停后返回。

    进程内 per-task 锁串行化（同任务并发执行抛 ConflictError）。pending→running 用条件 UPDATE
    抢占（rowcount==0 说明被别的执行者/worker 抢走，按其状态收尾），非 pending 则只续 worker 心跳。
    """
    result, _parked = _execute_task_impl(db, task)
    return result


def execute_task_with_parked(db: Session, task: PublishTask) -> tuple[PublishTask, bool]:
    """worker 专用：额外返回本次是否 parked（无前进、记录留 pending、需冷却后重试）。
    公开的 execute_task 保持只返回 PublishTask，现有 API/pipeline 调用方无需感知。"""
    return _execute_task_impl(db, task)


def _execute_task_impl(db: Session, task: PublishTask) -> tuple[PublishTask, bool]:
    lock = _task_locks.setdefault(task.id, threading.Lock())
    locked = lock.acquire(blocking=False)
    if not locked:
        raise ConflictError(f"Task {task.id} is already being executed")

    cancel_event = threading.Event()
    _task_cancel[task.id] = cancel_event

    parked = False
    try:
        if task.is_deleted:
            raise ConflictError(f"Task {task.id} has been deleted")
        if task.status in TERMINAL_TASK_STATUSES:
            raise ConflictError(f"Task is already terminal: {task.status}")

        now = utcnow()
        if task.status == "pending":
            stmt = (
                sa_update(PublishTask)
                .where(
                    PublishTask.id == task.id,
                    PublishTask.status == "pending",
                    PublishTask.is_deleted == False,  # noqa: E712
                )
                .values(
                    status="running",
                    started_at=now,
                    cancel_requested=False,
                    worker_heartbeat_at=now,
                )
            )
            if db.execute(stmt).rowcount == 0:  # type: ignore[attr-defined]
                db.flush()
                refreshed = get_task(db, task.id)
                if refreshed is None or refreshed.status in TERMINAL_TASK_STATUSES:
                    return refreshed or task, False
                task = refreshed
            else:
                task.status = "running"
                task.started_at = now
                task.cancel_requested = False
                task.worker_heartbeat_at = now
            add_log(db, task.id, None, "info", "Task started")
            _logger.info("Task %d started", task.id)
        else:
            _heartbeat_task_worker(db, task.id)

        parked = _run_pending_records(db, task)
        db.flush()
        result = get_task(db, task.id) or task
        _logger.info("Task %d finished with status %s", task.id, result.status)
        return result, parked
    finally:
        _task_locks.pop(task.id, None)
        _task_cancel.pop(task.id, None)
        if locked:
            lock.release()
```

> 注意早退分支也要返回 tuple：`return refreshed or task, False`。

- [ ] **Step 5: `_run_pending_records` 改返回 bool + 有界 park（实现其三）**

改签名 `def _run_pending_records(db: Session, task: PublishTask) -> bool:`，在 `while True` 前加计数器：

```python
    cancel_evt = _task_cancel.get(task.id)
    running: dict[Future, RunningRecord] = {}
    stalled_passes = 0  # 连续"无 running + 零启动 + 有 pending"轮数
    executor = ThreadPoolExecutor(
        max_workers=_max_concurrent_records(), thread_name_prefix="publish"
    )
```

把三个 `return`（cancel 收尾、paused 收尾、no-pending 收尾）改成 `return False`：

- `executor.py:286`（cancel 分支 `aggregate_task_status` 后）→ `return False`
- `executor.py:297`（paused 分支 `db.commit()` 后）→ `return False`
- `executor.py:305-306`（`if not running:` → `if not any pending:` → `aggregate_task_status`）→ `return False`

把 `if not running:` 的 has-pending 分支（原 `db.commit(); time.sleep(0.2); continue`）改为累计 stall + 到阈值 park，并在其后加清零：

```python
            if not running:
                if not any(record.status == "pending" for record in records):
                    aggregate_task_status(db, task, records)
                    db.commit()
                    return False
                # 有 pending 却无 running、且本轮零启动（否则 running 非空）→ 无前进可能
                # （多为卡死线程占着进程内账号锁/闸槽）。累计到阈值即 park：留 pending、不聚合
                # 终态、不改 worker_id，返回 True 让 worker 冷却跳过、先跑别的任务。
                stalled_passes += 1
                if stalled_passes >= PARK_STALL_THRESHOLD:
                    db.commit()
                    return True
                db.commit()
                time.sleep(0.2)
                continue

            stalled_passes = 0  # 有 running＝有前进，清零
            done, _ = wait(running.keys(), timeout=1, return_when=FIRST_COMPLETED)
```

- [ ] **Step 6: 运行测试，确认转绿**

Run: `pytest server/tests/test_run_pending_records_park.py -q`
Expected: PASS —— `execute_task_with_parked` 返回 `(task, True)`、记录仍 pending、账号锁仍被占；`execute_task` 返回 `PublishTask` 实例。

- [ ] **Step 7: 回归既有 executor 状态机测试**

Run: `pytest server/tests/test_tasks_state_machine.py server/tests/test_publish_timeout_lock_safety.py -q`
Expected: PASS —— 正常执行路径账号锁可拿、record 进 running、`stalled_passes` 永不到阈值，行为不变；超时锁安全测试不受影响（`_handle_timed_out_record` 未改）。

- [ ] **Step 8: Commit**

```bash
git add server/app/modules/tasks/executor.py server/tests/test_run_pending_records_park.py
git commit -m "fix(worker): 主循环无前进 K 轮即 park+return，execute_task_with_parked 透出

卡死线程占着进程内账号锁会让 _run_pending_records 在无 running+有 pending 时
无限 sleep(0.2) 空转→单线程 worker 冻死。改为连续 PARK_STALL_THRESHOLD 轮无前进
即返回 parked，记录留 pending。拆 _execute_task_impl，公开 execute_task 签名不变，
worker 专用 execute_task_with_parked 透出 parked。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: 改动①c-1 — 可配冷却 + worker skip-map + `_claim_next_task` 跳过

**Files:**
- Modify: `server/app/core/config.py`（加 `publish_park_cooldown_seconds`）
- Modify: `server/worker/executor.py`（`_parked_until` / `_active_parked_task_ids` / `_park_cooldown_seconds` / `_claim_next_task` 加 `skip_task_ids` + id 次序）
- Test: `server/tests/test_worker_park_cooldown.py`（新建）

**Interfaces:**
- Produces:
  - `settings.publish_park_cooldown_seconds: float`（env `GEO_PUBLISH_PARK_COOLDOWN_SECONDS`，默认 60.0）
  - `_parked_until: dict[int, float]`（worker 模块级，task_id → monotonic 到期）
  - `_active_parked_task_ids(now: float) -> set[int]`
  - `_park_cooldown_seconds() -> float`
  - `_claim_next_task(db, skip_task_ids: frozenset[int] = frozenset()) -> PublishTask | None`
- Consumes: 现有 `select` / `PublishTask` / `PublishRecord`。

- [ ] **Step 1: 写失败测试（先红）**

新建 `server/tests/test_worker_park_cooldown.py`（顶层**不** import worker.executor）：

```python
"""改动①c：worker 可配冷却 skip-map + _claim_next_task 跳过 parked task。"""

from __future__ import annotations

from datetime import timedelta

import pytest

from server.app.core.time import utcnow
from server.app.modules.tasks.models import PublishTask
from server.tests.utils import build_test_app


def test_active_parked_filters_by_cooldown_and_prunes(monkeypatch):
    """纯逻辑：冷却窗内的 task_id 返回，已到期的被清出 map。"""
    from server.worker import executor as wex  # 函数内 import，避免 collection 期拉 db.session

    wex._parked_until.clear()
    wex._parked_until[101] = 1000.0
    wex._parked_until[202] = 2000.0
    try:
        active = wex._active_parked_task_ids(now=1500.0)  # 101 过期、202 在冷却
        assert active == {202}
        assert 101 not in wex._parked_until  # 到期项被 prune
    finally:
        wex._parked_until.clear()


@pytest.mark.mysql
def test_claim_skips_parked_task_and_picks_next(monkeypatch):
    """_claim_next_task(skip_task_ids={old}) 跳过 parked 的最旧任务、返回下一个可跑任务。"""
    test_app = build_test_app(monkeypatch)
    try:
        from server.worker import executor as wex
        from server.tests.test_worker_executor import _create_publishable_task

        old_id = _create_publishable_task(test_app, suffix="old")
        new_id = _create_publishable_task(test_app, suffix="new")
        # 显式拉开 created_at，避免同秒 tie（MySQL DATETIME 无小数秒）
        with test_app.session_factory() as db:
            db.get(PublishTask, old_id).created_at = utcnow() - timedelta(minutes=2)
            db.get(PublishTask, new_id).created_at = utcnow() - timedelta(minutes=1)
            db.commit()

        with test_app.session_factory() as db:
            claimed = wex._claim_next_task(db)  # 不 skip → 最旧的 old
            assert claimed is not None and claimed.id == old_id
            wex._release_task_claim(db, old_id)

        with test_app.session_factory() as db:
            claimed = wex._claim_next_task(db, skip_task_ids=frozenset({old_id}))
            assert claimed is not None and claimed.id == new_id
            wex._release_task_claim(db, new_id)
    finally:
        test_app.cleanup()
```

同时给 `server/tests/test_worker_executor.py` 的 `_create_publishable_task` 加可选 `suffix`（默认空、既有无参调用不变），让两任务用不同 account_key / state_dir / article：

```python
def _create_publishable_task(test_app, suffix: str = "") -> int:
    client = test_app.client
    tag = f"worker-claim{('-' + suffix) if suffix else ''}"
    cover = client.post(
        "/api/assets", files={"file": ("cover.png", BytesIO(_PNG), "image/png")}
    ).json()["id"]
    article = client.post(
        "/api/articles",
        json={
            "title": f"Worker Claim Article {suffix}".strip(),
            "content_json": {"type": "doc", "content": []},
            "plain_text": "body",
            "cover_asset_id": cover,
        },
    ).json()
    state_dir = test_app.data_dir / "browser_states" / "toutiao" / tag
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "storage_state.json").write_text('{"cookies":[],"origins":[]}', encoding="utf-8")
    account = client.post(
        "/api/accounts/toutiao/login",
        json={
            "display_name": f"Worker Claim {suffix}".strip(),
            "account_key": tag,
            "use_browser": False,
        },
    ).json()
    task = client.post(
        "/api/tasks",
        json={
            "name": f"worker claim {suffix}".strip(),
            "task_type": "single",
            "article_id": article["id"],
            "accounts": [{"account_id": account["id"]}],
        },
    ).json()
    return task["id"]
```

- [ ] **Step 2: 运行测试，确认先红**

Run: `pytest server/tests/test_worker_park_cooldown.py -q`
Expected: FAIL —— `_active_parked_task_ids` 未定义（AttributeError）；`_claim_next_task` 不接受 `skip_task_ids`（TypeError）。

- [ ] **Step 3: config 加可配冷却（实现其一）**

在 `server/app/core/config.py` 的 `publish_max_concurrent_records: int = 5` 后加：

```python
    publish_park_cooldown_seconds: float = 60.0
```

- [ ] **Step 4: worker skip-map + helper（实现其二）**

在 `server/worker/executor.py` 的 `_shutdown = False` 附近加模块级状态与 helper：

```python
# 被 execute_task_with_parked 判 parked 的任务 → 冷却到期（monotonic）。_claim_next_task 冷却窗内跳过它。
_parked_until: dict[int, float] = {}


def _park_cooldown_seconds() -> float:
    from server.app.core.config import get_settings

    return float(get_settings().publish_park_cooldown_seconds)


def _active_parked_task_ids(now: float) -> set[int]:
    """仍在冷却窗内的 parked task_id；顺带清掉已到期的条目。"""
    for tid in [tid for tid, until in _parked_until.items() if now >= until]:
        _parked_until.pop(tid, None)
    return {tid for tid, until in _parked_until.items() if now < until}
```

- [ ] **Step 5: `_claim_next_task` 加 `skip_task_ids` + id 次序（实现其三）**

改 `_claim_next_task`（`worker/executor.py:81`）：签名加参数、排序加 `id.asc()` 稳定次序、非空 skip 时追加 `notin_`。认领段（`now`/`lease_until`/`sa_update`/`rowcount`/`db.commit()`/`get_task`）原样保留：

```python
def _claim_next_task(db, skip_task_ids: frozenset[int] = frozenset()) -> PublishTask | None:
    """通过乐观锁抢占带待处理记录的任务，成功时返回任务，否则返回 None。

    skip_task_ids：本 worker 冷却中的 parked 任务，本轮跳过（先跑下一个非 parked 的最旧任务）。
    """
    from sqlalchemy import exists

    from server.app.modules.tasks.models import PublishRecord

    stmt = (
        select(PublishTask.id)
        .where(
            PublishTask.status.in_(["pending", "running"]),
            PublishTask.is_deleted == False,  # noqa: E712
            PublishTask.worker_id.is_(None),
            exists(
                select(1).where(
                    PublishRecord.task_id == PublishTask.id,
                    PublishRecord.status == "pending",
                    PublishRecord.is_deleted == False,  # noqa: E712
                )
            ),
        )
        .order_by(PublishTask.created_at.asc(), PublishTask.id.asc())
        .limit(1)
    )
    if skip_task_ids:
        stmt = stmt.where(PublishTask.id.notin_(skip_task_ids))
    candidate_id = db.execute(stmt).scalar_one_or_none()

    if candidate_id is None:
        return None

    now = utcnow()
    lease_until = now + timedelta(minutes=CLAIM_LEASE_MINUTES)
    rows = db.execute(
        sa_update(PublishTask)
        .where(
            PublishTask.id == candidate_id,
            PublishTask.worker_id.is_(None),
            PublishTask.is_deleted == False,  # noqa: E712
        )
        .values(worker_id=WORKER_ID, worker_lease_until=lease_until, worker_heartbeat_at=now)
    ).rowcount

    if rows == 0:
        return None

    db.commit()
    return get_task(db, candidate_id)
```

- [ ] **Step 6: 运行测试，确认转绿**

Run: `pytest server/tests/test_worker_park_cooldown.py server/tests/test_worker_executor.py -q`
Expected: PASS —— 冷却过滤 + prune 正确；claim 跳过 parked 返回 new；`_create_publishable_task` 既有无参调用不回归。

- [ ] **Step 7: Commit**

```bash
git add server/app/core/config.py server/worker/executor.py server/tests/test_worker_park_cooldown.py server/tests/test_worker_executor.py
git commit -m "fix(worker): 可配冷却 skip-map + _claim_next_task 跳过 parked

execute_task 判 parked 后 worker 把该 task 加进内存冷却 map（可配
GEO_PUBLISH_PARK_COOLDOWN_SECONDS 默认 60s），_claim_next_task 冷却窗内经
notin_ 跳过它、先跑下一个非 parked 的最旧任务；排序加 id.asc() 稳定次序
（created_at 无小数秒同秒会 tie）。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: 改动①c-2 — 抽 `_run_worker_iteration` 接线 + 端到端 glue 测试

**Files:**
- Modify: `server/worker/executor.py`（`_recovery_cycle` 上移模块级；抽 `_run_worker_iteration(db)`；主循环 while 改调它；接 `execute_task_with_parked` + 写冷却）
- Test: `server/tests/test_worker_park_cooldown.py`（追加端到端 glue 用例）

**Interfaces:**
- Consumes: `server.app.modules.tasks.executor.execute_task_with_parked`（Task 1）、`_active_parked_task_ids` / `_parked_until` / `_park_cooldown_seconds`（Task 2）、`_claim_next_task(skip_task_ids=...)`（Task 2）。
- Produces: `_run_worker_iteration(db) -> None`（一轮主循环体）；模块级 `_recovery_cycle: int`。

- [ ] **Step 1: 写失败测试（先红）**

在 `server/tests/test_worker_park_cooldown.py` 追加：

```python
@pytest.mark.mysql
def test_worker_iteration_parks_then_next_claim_skips(monkeypatch):
    """端到端 glue：一轮 _run_worker_iteration 让卡死老任务 park 并写冷却，
    下一次 claim 跳过冷却中的老任务、选中新任务。"""
    import time as _t

    test_app = build_test_app(monkeypatch)
    try:
        from server.worker import executor as wex
        from server.app.modules.tasks import executor as ex
        from server.tests.test_worker_executor import _create_publishable_task

        monkeypatch.setattr(ex, "PARK_STALL_THRESHOLD", 2)
        monkeypatch.setattr(wex, "_periodic_recovery", lambda db: None)  # 隔离周期恢复
        wex._parked_until.clear()

        old_id = _create_publishable_task(test_app, suffix="old")
        new_id = _create_publishable_task(test_app, suffix="new")
        with test_app.session_factory() as db:
            db.get(PublishTask, old_id).created_at = utcnow() - timedelta(minutes=2)
            db.get(PublishTask, new_id).created_at = utcnow() - timedelta(minutes=1)
            from server.app.modules.tasks.models import PublishRecord
            old_account = db.query(PublishRecord).filter_by(task_id=old_id).first().account_id
            db.commit()

        assert ex._try_acquire_account_lock(old_account)  # 老任务账号锁被占→会 park
        try:
            wex._run_worker_iteration(test_app.session_factory())  # 一轮：claim old→park→写冷却→释放认领
            assert old_id in wex._active_parked_task_ids(_t.monotonic())

            skip = frozenset(wex._active_parked_task_ids(_t.monotonic()))
            with test_app.session_factory() as db:
                claimed = wex._claim_next_task(db, skip_task_ids=skip)
                assert claimed is not None and claimed.id == new_id
                wex._release_task_claim(db, new_id)
        finally:
            ex._release_account_lock(old_account)
            wex._parked_until.clear()
    finally:
        test_app.cleanup()
```

- [ ] **Step 2: 运行测试，确认先红**

Run: `pytest server/tests/test_worker_park_cooldown.py::test_worker_iteration_parks_then_next_claim_skips -q`
Expected: FAIL —— `_run_worker_iteration` 未定义（AttributeError）。

- [ ] **Step 3: `_recovery_cycle` 上移模块级 + import（实现其一）**

在 `server/worker/executor.py` 顶部 import 区加：

```python
from server.app.modules.tasks.executor import execute_task_with_parked
```

在 `_shutdown = False` 附近加：

```python
_recovery_cycle = 0
```

- [ ] **Step 4: 抽 `_run_worker_iteration` 并改写主循环（实现其二）**

把主循环块（`worker/executor.py` 现 `_recovery_cycle = 0` 到 while 结束，约 364-402 行）替换为：

```python
    while not _shutdown:
        _run_worker_iteration(SessionLocal())
```

并新增函数（放在主循环函数上方或模块内合适处）：

```python
def _run_worker_iteration(db) -> None:
    """一轮 worker 主循环体：心跳 → 周期恢复 → claim（跳过冷却中的 parked）→ 执行 →
    按 parked 写冷却 → 释放认领。抽出以便端到端测试整条 park→冷却→skip→下一任务链路。"""
    global _recovery_cycle
    task_id: int | None = None
    try:
        _write_worker_heartbeat(db)
        if _recovery_cycle % 60 == 0:
            _periodic_recovery(db)
        _recovery_cycle += 1

        skip = frozenset(_active_parked_task_ids(time.monotonic()))
        task = _claim_next_task(db, skip_task_ids=skip)
        if task is None:
            time.sleep(1)
            return

        task_id = task.id
        _logger.info("Worker %s claimed task %d", WORKER_ID, task_id)
        _result, parked = execute_task_with_parked(db, task)
        db.commit()
        if parked:
            _parked_until[task_id] = time.monotonic() + _park_cooldown_seconds()
            _logger.info(
                "Worker %s parked task %d for %.0fs (no forward progress)",
                WORKER_ID, task_id, _park_cooldown_seconds(),
            )
        else:
            _logger.info("Worker %s finished task %d", WORKER_ID, task_id)
    except Exception:
        _logger.exception("Worker %s: error executing task %s", WORKER_ID, task_id)
        try:
            db.rollback()
        except Exception:
            pass
        time.sleep(5)
    finally:
        if task_id is not None:
            try:
                _release_task_claim(db, task_id)
            except Exception:
                pass
        try:
            db.close()
        except Exception:
            pass
```

> 行为等价原循环：唯一差别是 task-None 分支由"先 `db.close()` 再 `sleep(1)`"变为"`sleep(1)` 后由 finally 关"——功能等价（sleep 期间 db 空闲），且去掉了双 close。

- [ ] **Step 5: 运行测试，确认转绿**

Run: `pytest server/tests/test_worker_park_cooldown.py -q`
Expected: PASS —— 一轮迭代后 old 进冷却、下一次 claim 跳过 old 选 new；前两个用例仍绿。

- [ ] **Step 6: 回归 worker 测试**

Run: `pytest server/tests/test_worker_executor.py server/tests/test_account_keepalive.py -q`
Expected: PASS —— `_claim_next_task` / `_release_task_claim` / 恢复 / 心跳等既有用例不回归。

- [ ] **Step 7: Commit**

```bash
git add server/worker/executor.py server/tests/test_worker_park_cooldown.py
git commit -m "fix(worker): 抽 _run_worker_iteration 接 parked→冷却→skip，端到端覆盖

主循环体抽成 _run_worker_iteration(db)：claim 带 skip、execute_task_with_parked
判 parked 后写冷却、释放认领；_recovery_cycle 上移模块级。加端到端 glue 测试
覆盖 park→冷却→下一次 claim 跳过整条链路。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: 改动② — worker 容器 `init: true` 收 chrome 僵尸

**Files:**
- Modify: `deploy/docker-compose.prod.yml`（worker 加 `init: true`）
- Modify: `docker-compose.yml`（worker 加 `init: true` + 命令改 `exec python`）

- [ ] **Step 1: prod worker 加 `init: true`**

在 `deploy/docker-compose.prod.yml` 的 `worker:`（第 95 行）块内，`restart: unless-stopped` 下一行加：

```yaml
  worker:
    image: geo-collab-server:${SERVER_VERSION:?required}
    restart: unless-stopped
    init: true   # tini 当 PID 1 回收 reparent 上来的 <defunct> chrome 子进程
    command: ["python", "-m", "server.worker.executor"]
```

（命令已是 exec-form array，Python 已是 tini 直接子进程，无需改。）

- [ ] **Step 2: base worker 加 `init: true` + `exec`**

在 `docker-compose.yml` 的 `worker:`（第 62 行）块内加 `init: true`，并把 `sh -c` 命令末尾的 `python` 改成 `exec python`（让 Python 取代 sh 成为 tini 直接子进程、正确收信号）：

```yaml
  worker:
    build: .
    restart: unless-stopped
    init: true
    command: sh -c "alembic upgrade head && exec python -m server.worker.executor"
```

- [ ] **Step 3: 校验 compose 语法**

Run: `docker compose -f deploy/docker-compose.prod.yml config -q && docker compose -f docker-compose.yml config -q`
Expected: 退出码 0、无输出（`init` 键被识别、YAML 合法）。若本机无 docker，跳过并在 MR 描述标注"待 CI/部署机校验"。

- [ ] **Step 4: Commit**

```bash
git add deploy/docker-compose.prod.yml docker-compose.yml
git commit -m "fix(worker): 容器加 init:true 回收 chrome 僵尸

worker 是容器 PID 1，不回收 reparent 上来的 <defunct> chrome 子进程。
加 init:true 让 tini 当 PID 1 回收；base compose sh -c 命令改 exec python
让 Python 成为 tini 直接子进程正确收信号。仅回收已 reparent 的僵尸，
'部署后 <defunct> 不再累积'为验证目标。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: 门禁 — lint / format / mypy / 回归

- [ ] **Step 1: ruff check + format**

Run: `ruff check server/ && ruff format --check server/`
Expected: PASS。失败则 `ruff format server/` 后 `git add -A && git commit -m "style: ruff format"`。

- [ ] **Step 2: mypy**

Run: `mypy server/app`
Expected: 不新增错误（新增函数带类型注解）。

- [ ] **Step 3: 全相关测试绿**

Run: `pytest server/tests/test_run_pending_records_park.py server/tests/test_worker_park_cooldown.py server/tests/test_worker_executor.py server/tests/test_tasks_state_machine.py server/tests/test_publish_timeout_lock_safety.py server/tests/test_account_keepalive.py -q`
Expected: PASS（全部）。

- [ ] **Step 4: 更大范围回归（时间允许）**

Run: `pytest server/tests/ -q -k "publish or worker or task"`
Expected: PASS（无本改动相关新红）。

---

## Self-Review

**1. Spec coverage**（对照 spec §4）：
- 改动①a 主循环有界化 → Task 1 Step 5 ✅
- 改动①b parked 透出（无 side-channel，`execute_task` 签名不变）→ Task 1 Step 4 ✅
- 改动①c 可配冷却 + skip-map + `_claim_next_task` 跳过 + id 次序 + `_run_worker_iteration` → Task 2 + Task 3 ✅
- 改动② init:true + base compose exec → Task 4 ✅
- 测试策略 §5：①b park/wrapper（Task 1）、①c 纯冷却/claim-skip/端到端 glue（Task 2+3，全 lazy import worker.executor）、② compose config -q（Task 4）✅
- 非目标（不回收闸槽、不 SIGKILL、无 ConflictError 退避）→ Global Constraints 明确，`_handle_timed_out_record` 全程不动 ✅
- 上线/回滚 §6 → 计划末尾"部署前停"，无迁移/base 变化 ✅

**2. Placeholder scan**：无 TBD/TODO；代码步给完整实码；compose 步给确切 YAML。Task 4 Step 3"本机无 docker 则跳过"是明确降级路径。

**3. Type consistency**：
- `_run_pending_records(...) -> bool`（Task 1 Step 5）↔ `_execute_task_impl` 中 `parked = _run_pending_records(...)`（Task 1 Step 4）一致。
- `execute_task_with_parked(...) -> tuple[PublishTask, bool]`（Task 1）↔ Task 3 `_result, parked = execute_task_with_parked(db, task)` 一致。
- `execute_task(...) -> PublishTask`（Task 1）↔ Task 1 Step 1 `isinstance(result, PublishTask)` 一致。
- `_claim_next_task(db, skip_task_ids=frozenset())`（Task 2）↔ Task 3 `_claim_next_task(db, skip_task_ids=skip)` + 既有无参调用一致。
- `_active_parked_task_ids(now: float) -> set[int]`（Task 2）↔ Task 3 `frozenset(_active_parked_task_ids(time.monotonic()))` 一致。
- `_park_cooldown_seconds() -> float`（Task 2）↔ Task 3 `time.monotonic() + _park_cooldown_seconds()` 一致。
- `PARK_STALL_THRESHOLD`（tasks/executor.py）与 `publish_park_cooldown_seconds`（config）/ `_parked_until`（worker）分属不同文件、无混用。

## 上线 Handoff（部署前停）

两个改动完成、全绿 + 门禁过后：推分支 → 建 MR → 等 CI（`backend-lint` + `frontend`）绿。**因动 worker 并发/调度语义（非例行），合并 / 部署前停下等用户确认**，不自动上线。
