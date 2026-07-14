# Worker 卡死发布线程拖垮修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让一条卡死的发布线程不再拖垮整个单线程 worker——超时归还全局闸槽、主循环有界化后让 worker 跳过卡死任务先跑别的、容器回收 chrome 僵尸。

**Architecture:** 三个改动，全部在 `server/app/modules/tasks/executor.py`、`server/worker/executor.py`、compose 文件内。① watchdog 超时的 `terminated=False` 分支只归还全局闸槽（`_global_publish_gate`），账号锁 + profile 锁按 #2 继续保留。② 主循环连续 K 轮无前进即判 parked 并 return（记录留 pending），通过模块级侧信道告诉 worker；worker 用内存冷却 skip-map + `_claim_next_task` 的 `notin_` 跳过该任务、先跑下一个非 parked 的最旧任务。③ worker 服务加 `init: true`（tini 回收 `<defunct>` chrome）。

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy / pytest（MySQL，`@pytest.mark.mysql`）/ Docker Compose。设计稿见 `docs/superpowers/specs/2026-07-14-worker-wedged-task-recovery-design.md`。

## Global Constraints

- **#2 不变式**：线程卡死（`terminated=False`）时**绝不释放账号锁 + profile 锁**——否则下一条同账号记录对同一 persistent profile 并发开 Chromium 损坏目录。本次只归还与 profile 安全无关的全局闸槽。
- **不破坏 `execute_task(db, task) -> PublishTask` 签名**：现有调用方（`router.py:309/529`、`worker/executor.py:383`）不变；parked 信号走模块级侧信道，不改返回类型。
- **无 DB 迁移、无 base 镜像变化**。
- **MySQL only**：涉及 DB 的测试需 `GEO_TEST_DATABASE_URL`（库名含 `test`），标 `@pytest.mark.mysql`；纯逻辑测试沿用现有 collection-safe 写法（顶层 import executor 不拉 `db.session`）。
- **测试命令**：`GEO_TEST_DATABASE_URL=... pytest server/tests/<file>::<test> -q`。
- **不含 ConflictError 退避**（原候选 ④ 经核实单 worker 生产无法触发，已剔除）。
- **上线非例行**：走正常 MR→CI，**部署前停下等确认**。

---

### Task 1: 改动① — watchdog 超时归还全局闸槽（保留账号/profile 锁）

**Files:**
- Modify: `server/app/modules/tasks/executor.py`（`_retire_running_slot` 抽出 gate 归还 helper；`_handle_timed_out_record` 的 `terminated=False` 分支调用它）
- Test: `server/tests/test_publish_timeout_lock_safety.py`（更新既有 stuck 用例断言：闸槽归还、账号/profile 锁保留）

**Interfaces:**
- Produces: `_return_publish_gate_slot(record_id: int) -> None` — 归还该记录持有的 1 个全局发布闸槽，over-release 吞掉并告警。供 `_retire_running_slot` 与 `_handle_timed_out_record` 共用。
- Consumes: 现有 `_global_publish_gate: ObservableGate`（`.release()` / `.in_use`）、`_release_account_lock`、`_mark_record_zombie`、`emit_resource_alert`。

- [ ] **Step 1: 更新既有 stuck 测试为新期望（先红）**

把 `server/tests/test_publish_timeout_lock_safety.py` 的 `test_stuck_publish_thread_keeps_account_and_profile_locks` 改名并改断言——闸槽现在**归还**（`in_use == 0`），账号锁 + profile 锁仍**保留**：

```python
def test_stuck_publish_thread_returns_gate_but_keeps_account_and_profile_locks(monkeypatch):
    """线程超时仍存活：全局闸槽归还（#2 无关），但账号锁 + profile 锁保留、记录标僵尸 + 告警、返回 False。"""
    from server.app.shared import resource_metrics as rm

    gate = ObservableGate(2, name="publish")
    assert gate.try_acquire()
    monkeypatch.setattr(tasks_mod, "_global_publish_gate", gate)

    account_id = 990001
    assert tasks_mod._try_acquire_account_lock(account_id)

    monkeypatch.setattr(tasks_mod, "_mark_record_failed", lambda *a, **k: None)
    zombie: list = []
    monkeypatch.setattr(
        tasks_mod, "_mark_record_zombie", lambda db, tid, rid: zombie.append(rid), raising=False
    )
    monkeypatch.setattr(tasks_mod, "_close_record_browser", lambda rid: None, raising=False)
    released_profiles: list = []
    monkeypatch.setattr(
        tasks_mod,
        "_release_record_profile_lock",
        lambda rid: released_profiles.append(rid),
        raising=False,
    )
    alerts: list = []
    monkeypatch.setattr(rm, "_alert_hook", lambda msg, ctx=None: alerts.append((msg, ctx)))

    rr = SimpleNamespace(record_id=7, account_id=account_id)
    stuck = _running_future()

    try:
        terminated = tasks_mod._handle_timed_out_record(None, 1, rr, stuck, result_timeout=0.05)

        assert terminated is False
        # 账号锁未释放：重拿应失败（#2 保留）
        assert tasks_mod._try_acquire_account_lock(account_id) is False
        # profile 锁未释放（#2 保留）
        assert released_profiles == []
        # 全局闸槽已归还（与 profile 安全无关）
        assert gate.in_use == 0
        # 记录标僵尸 + 告警
        assert zombie == [7]
        assert len(alerts) == 1
    finally:
        tasks_mod._release_account_lock(account_id)
        stuck.set_result(None)
```

同时把文件顶部 docstring 里「账号锁 + profile 锁 + 全局闸槽**均不释放**」一行改为「账号锁 + profile 锁保留（#2），全局闸槽归还」。

- [ ] **Step 2: 运行测试，确认先红**

Run: `pytest server/tests/test_publish_timeout_lock_safety.py::test_stuck_publish_thread_returns_gate_but_keeps_account_and_profile_locks -q`
Expected: FAIL —— 断言 `gate.in_use == 0` 失败（当前代码超时不归还闸槽，实际为 1）。

- [ ] **Step 3: 抽出 gate 归还 helper 并在超时分支调用（实现）**

在 `server/app/modules/tasks/executor.py` 里，把 `_retire_running_slot` 的闸槽归还逻辑抽成 helper，并在 `_handle_timed_out_record` 的 `terminated=False` 分支加一行归还闸槽。

新增 helper（放在 `_retire_running_slot` 上方）：

```python
def _return_publish_gate_slot(record_id: int) -> None:
    """归还该记录持有的 1 个全局发布闸槽。over-release 会被 ObservableGate 抛 ValueError——
    这里吞掉并告警（执行循环不应因释放漏口崩溃），异常本身写进日志供排查。"""
    try:
        _global_publish_gate.release()
    except ValueError:
        _logger.warning(
            "publish gate over-release for record %d (slot accounting bug?)",
            record_id,
            exc_info=True,
        )
```

把 `_retire_running_slot` 改为复用它（行为不变、DRY）：

```python
def _retire_running_slot(running_record: RunningRecord) -> None:
    """记录退场：归还移交给运行生命周期的全局发布槽 + 账号锁（Task 4 Step 5）。"""
    _return_publish_gate_slot(running_record.record_id)
    _release_account_lock(running_record.account_id)
```

在 `_handle_timed_out_record` 的 `else`（`terminated=False`）分支归还闸槽（账号锁 / profile 锁不动），并把告警文案改成反映闸槽已归还：

```python
    if terminated:
        _release_record_profile_lock(running_record.record_id)
        _retire_running_slot(running_record)
    else:
        # 线程卡死：账号锁 + profile 锁按 #2 保留；但全局闸槽与 profile 安全无关，归还它以免
        # 每卡死一次永久扣一格全局并发、拖垮整个 worker 的吞吐（见 2026-07-14 spec 改动①）。
        _return_publish_gate_slot(running_record.record_id)
        _mark_record_zombie(db, task_id, running_record.record_id)
        emit_resource_alert(
            f"record {running_record.record_id} publish thread still alive after "
            f"{result_timeout:g}s; account/profile locks held, gate slot returned, "
            f"leaving for recovery",
            {"record_id": running_record.record_id, "account_id": running_record.account_id},
        )
    return terminated
```

- [ ] **Step 4: 运行测试，确认转绿（含对照用例不回归）**

Run: `pytest server/tests/test_publish_timeout_lock_safety.py -q`
Expected: PASS —— 新 stuck 用例通过；对照用例 `test_terminated_publish_thread_releases_locks`（走 `_retire_running_slot`，`in_use == 0`）仍通过。

- [ ] **Step 5: Commit**

```bash
git add server/app/modules/tasks/executor.py server/tests/test_publish_timeout_lock_safety.py
git commit -m "fix(worker): watchdog 超时归还全局闸槽，账号/profile 锁按#2保留

卡死线程漏掉全局闸槽会每卡死一次永久扣一格并发、拖垮整个 worker 吞吐。
抽 _return_publish_gate_slot helper，在 _handle_timed_out_record 的
terminated=False 分支归还闸槽（与 profile 安全无关），账号锁+profile 锁不动。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: 改动②a+②b — 主循环有界化 + parked 侧信道

**Files:**
- Modify: `server/app/modules/tasks/executor.py`（新增 parked 侧信道 + `PARK_STALL_THRESHOLD`；改 `_run_pending_records` 的 no-progress 分支）
- Test: `server/tests/test_run_pending_records_park.py`（新建，mysql）

**Interfaces:**
- Produces:
  - `PARK_STALL_THRESHOLD: int`（模块常量，默认 5；连续 N 轮无前进即 park）
  - `_register_task_parked(task_id: int) -> None` — 登记本次 run 被 park。
  - `consume_task_parked(task_id: int) -> bool` — worker 消费并清除 parked 标记；本次被 park 返回 `True`。
- Consumes: 现有 `_run_pending_records(db, task)` 内部的 `running` / `records` / `_start_runnable_records` / `_task_cancel` / `_try_acquire_account_lock`（Task 3 的 worker 侧消费 `consume_task_parked`）。

- [ ] **Step 1: 写失败测试（先红）**

新建 `server/tests/test_run_pending_records_park.py`——预占卡死账号的进程内账号锁（模拟泄漏），断言 `_run_pending_records` 在有界轮数内返回（不挂）、记录仍 pending、parked 信号已登记：

```python
"""改动②a：主循环遇到泄漏的进程内账号锁时有界返回（park），不再无限空转拖死单线程 worker。"""

from __future__ import annotations

import threading

import pytest

from server.app.modules.tasks import executor as ex
from server.app.modules.tasks.models import PublishRecord, PublishTask
from server.tests.utils import build_test_app
from server.tests.test_worker_executor import _create_publishable_task


@pytest.mark.mysql
def test_run_pending_records_parks_when_account_lock_leaked(monkeypatch):
    test_app = build_test_app(monkeypatch)
    try:
        # 缩短阈值让 park 快（默认 5 × 0.2s，测试降到 2）
        monkeypatch.setattr(ex, "PARK_STALL_THRESHOLD", 2)

        task_id = _create_publishable_task(test_app)
        with test_app.session_factory() as db:
            task = db.get(PublishTask, task_id)
            task.status = "running"  # 视为已 claim
            rec = db.query(PublishRecord).filter_by(task_id=task_id).first()
            rec_id = rec.id
            account_id = rec.account_id
            db.commit()

        # 模拟卡死线程泄漏的进程内账号锁：预占它，_start_runnable_records 永远拿不到
        assert ex._try_acquire_account_lock(account_id)
        ex._task_cancel[task_id] = threading.Event()
        try:
            with test_app.session_factory() as db:
                task = db.get(PublishTask, task_id)
                ex._run_pending_records(db, task)  # 必须有界返回，不能挂

            # 记录仍 pending（park 不改终态）
            with test_app.session_factory() as db:
                assert db.get(PublishRecord, rec_id).status == "pending"

            # parked 信号已登记，worker 可消费
            assert ex.consume_task_parked(task_id) is True
        finally:
            ex._release_account_lock(account_id)
            ex._task_cancel.pop(task_id, None)
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_consume_task_parked_is_false_without_park(monkeypatch):
    """判别性对照：没 park 过的 task，consume 返回 False。"""
    assert ex.consume_task_parked(20260714) is False
```

- [ ] **Step 2: 运行测试，确认先红**

Run: `pytest server/tests/test_run_pending_records_park.py -q`
Expected: FAIL —— `PARK_STALL_THRESHOLD` / `consume_task_parked` 未定义（AttributeError），或 `_run_pending_records` 在当前代码里无限空转导致用例超时挂起。

- [ ] **Step 3: 加 parked 侧信道 + 常量（实现其一）**

在 `server/app/modules/tasks/executor.py` 常量区（`MAX_CONCURRENT_RECORDS` 附近）加：

```python
# 主循环连续 N 轮"无 running + 本轮零启动 + 仍有 pending"→ 判定无前进可能（多为卡死线程
# 泄漏的进程内账号锁挡住），park 并 return，交 worker 冷却跳过。5 × 0.2s ≈ 1s 容忍窗。
PARK_STALL_THRESHOLD = 5
```

在 `_task_locks` 等模块级状态附近加侧信道（worker 单线程消费，进程内安全；inline/test 模式无消费者时最多按 task 数留存、有界）：

```python
# execute_task 内的 _run_pending_records 判 parked 时登记；worker 主循环 execute_task 返回后消费。
# 不改 execute_task 返回签名（现有 API/pipeline 调用方无需感知）。
_parked_signals: dict[int, float] = {}


def _register_task_parked(task_id: int) -> None:
    _parked_signals[task_id] = time.monotonic()


def consume_task_parked(task_id: int) -> bool:
    """worker 消费 parked 信号：本次被 park 则返回 True 并清除标记。"""
    return _parked_signals.pop(task_id, None) is not None
```

- [ ] **Step 4: 改 `_run_pending_records` 的 no-progress 分支（实现其二）**

在 `_run_pending_records` 进入 `while True` 前加计数器：

```python
    cancel_evt = _task_cancel.get(task.id)
    running: dict[Future, RunningRecord] = {}
    stalled_passes = 0  # 连续"无 running + 零启动 + 有 pending"的轮数
    executor = ThreadPoolExecutor(
        max_workers=_max_concurrent_records(), thread_name_prefix="publish"
    )
```

把现有 `if not running:` 分支（原 `db.commit(); time.sleep(0.2); continue`）改为累计 stall + 到阈值 park：

```python
            if not running:
                if not any(record.status == "pending" for record in records):
                    aggregate_task_status(db, task, records)
                    db.commit()
                    return
                # 有 pending 却无 running、且本轮 _start_runnable_records 零启动（否则 running 非空）
                # → 无前进可能（多为卡死线程泄漏的进程内账号锁）。累计到阈值即 park：留 pending、
                # 不聚合终态、不改 worker_id，登记 parked 信号让 worker 冷却跳过、先跑别的任务。
                stalled_passes += 1
                if stalled_passes >= PARK_STALL_THRESHOLD:
                    _register_task_parked(task.id)
                    db.commit()
                    return
                db.commit()
                time.sleep(0.2)
                continue

            stalled_passes = 0  # 有 running＝有前进，清零
```

> 注：`stalled_passes = 0` 放在上面 `if not running:` 整块**之后**、`done, _ = wait(...)` 之前，确保只要有 running 就清零。

- [ ] **Step 5: 运行测试，确认转绿**

Run: `pytest server/tests/test_run_pending_records_park.py -q`
Expected: PASS —— `_run_pending_records` 在 ~0.4s（2×0.2s）内返回、记录仍 pending、`consume_task_parked` 返回 True；对照用例返回 False。

- [ ] **Step 6: 回归既有 executor 状态机测试（不回归正常路径）**

Run: `pytest server/tests/test_tasks_state_machine.py -q`
Expected: PASS —— 正常执行路径下账号锁可拿、record 进入 running、`stalled_passes` 永不到阈值，行为不变。

- [ ] **Step 7: Commit**

```bash
git add server/app/modules/tasks/executor.py server/tests/test_run_pending_records_park.py
git commit -m "fix(worker): 主循环无前进 K 轮即 park+return，加 parked 侧信道

卡死线程泄漏的进程内账号锁会让 _run_pending_records 在无 running+有 pending
时无限 sleep(0.2) 空转→单线程 worker 冻死。改为连续 PARK_STALL_THRESHOLD 轮
无前进即登记 parked 并 return（记录留 pending），worker 侧据此冷却跳过。
execute_task 返回签名不变。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: 改动②c — worker 冷却 skip-map + `_claim_next_task` 跳过 parked

**Files:**
- Modify: `server/worker/executor.py`（`PARK_COOLDOWN_SECONDS` + `_parked_until` + `_active_parked_task_ids`；`_claim_next_task` 加 `skip_task_ids` 参数；主循环消费 parked 信号并传 skip 集）
- Test: `server/tests/test_worker_park_cooldown.py`（新建：纯逻辑冷却 + mysql claim 跳过）

**Interfaces:**
- Consumes: `server.app.modules.tasks.executor.consume_task_parked`（Task 2）。
- Produces:
  - `PARK_COOLDOWN_SECONDS: float`（默认 60.0）
  - `_parked_until: dict[int, float]`（task_id → monotonic 到期）
  - `_active_parked_task_ids(now: float) -> set[int]` — 返回仍在冷却窗内的 task_id，顺带清理到期项。
  - `_claim_next_task(db, skip_task_ids: frozenset[int] = frozenset()) -> PublishTask | None` — 候选查询排除 skip 集。

- [ ] **Step 1: 写失败测试（先红）**

新建 `server/tests/test_worker_park_cooldown.py`：

```python
"""改动②c：worker 冷却 skip-map + _claim_next_task 跳过 parked task。"""

from __future__ import annotations

import pytest

from server.worker import executor as wex
from server.app.modules.tasks.models import PublishTask
from server.tests.utils import build_test_app
from server.tests.test_worker_executor import _create_publishable_task


def test_active_parked_filters_by_cooldown_and_prunes(monkeypatch):
    """纯逻辑：冷却窗内的 task_id 返回，已到期的被清出 map。"""
    wex._parked_until.clear()
    wex._parked_until[101] = 1000.0  # 到期时刻
    wex._parked_until[202] = 2000.0
    try:
        # now=1500：101 已过期被清、202 仍在冷却
        active = wex._active_parked_task_ids(now=1500.0)
        assert active == {202}
        assert 101 not in wex._parked_until  # 到期项被 prune
    finally:
        wex._parked_until.clear()


@pytest.mark.mysql
def test_claim_skips_parked_task_and_picks_next(monkeypatch):
    """_claim_next_task(skip_task_ids={old}) 跳过 parked 的最旧任务、返回下一个可跑任务。"""
    test_app = build_test_app(monkeypatch)
    try:
        old_id = _create_publishable_task(test_app, suffix="old")
        new_id = _create_publishable_task(test_app, suffix="new")

        with test_app.session_factory() as db:
            # 不 skip：order_by(created_at.asc) 先返回 old
            claimed = wex._claim_next_task(db)
            assert claimed is not None and claimed.id == old_id
            wex._release_task_claim(db, old_id)

        with test_app.session_factory() as db:
            # skip old → 返回 new
            claimed = wex._claim_next_task(db, skip_task_ids=frozenset({old_id}))
            assert claimed is not None and claimed.id == new_id
            wex._release_task_claim(db, new_id)
    finally:
        test_app.cleanup()
```

同时把 `server/tests/test_worker_executor.py` 的 `_create_publishable_task` 加一个可选 `suffix` 形参（默认空，保持既有调用不变），让两个任务用不同 account_key / state_dir：

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
        json={"display_name": f"Worker Claim {suffix}".strip(), "account_key": tag, "use_browser": False},
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

- [ ] **Step 3: 加冷却常量 + skip-map + helper（实现其一）**

在 `server/worker/executor.py` 常量区（`LOGIN_SESSION_*` 附近）加：

```python
PARK_COOLDOWN_SECONDS = 60.0  # 被 park 的任务在此冷却窗内不被本 worker 重抢，先跑别的任务
```

在 `_shutdown` 等模块级状态附近加：

```python
# 被 execute_task 判 parked 的任务 → 冷却到期（monotonic）。_claim_next_task 冷却窗内跳过它。
_parked_until: dict[int, float] = {}


def _active_parked_task_ids(now: float) -> set[int]:
    """仍在冷却窗内的 parked task_id；顺带清掉已到期的条目。"""
    for tid in [tid for tid, until in _parked_until.items() if now >= until]:
        _parked_until.pop(tid, None)
    return {tid for tid, until in _parked_until.items() if now < until}
```

- [ ] **Step 4: `_claim_next_task` 加 `skip_task_ids` 参数（实现其二）**

改签名与候选查询（非空才加过滤；空集不加，避免 `notin_([])` 边角）：

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
        .order_by(PublishTask.created_at.asc())
        .limit(1)
    )
    if skip_task_ids:
        stmt = stmt.where(PublishTask.id.notin_(skip_task_ids))
    candidate_id = db.execute(stmt).scalar_one_or_none()

    if candidate_id is None:
        return None
    # ... 其余（认领 UPDATE / rowcount 校验 / commit / get_task）保持不变
```

> 只改 `select(...)` 组装成 `stmt` 并在非空时追加 `.where(notin_)`，认领段（`now` / `lease_until` / `sa_update` / `rowcount==0` / `db.commit()` / `return get_task`）原样保留。

- [ ] **Step 5: 主循环消费 parked 信号 + 传 skip 集（实现其三）**

在 `server/worker/executor.py` 顶部 import 区加：

```python
from server.app.modules.tasks.executor import consume_task_parked
```

改主循环 claim 段（原 `task = _claim_next_task(db)` 及 execute 后）：

```python
            skip = _active_parked_task_ids(time.monotonic())
            task = _claim_next_task(db, skip_task_ids=frozenset(skip))
            if task is None:
                db.close()
                time.sleep(1)
                continue

            task_id = task.id
            _logger.info("Worker %s claimed task %d", WORKER_ID, task_id)
            execute_task(db, task)
            db.commit()
            if consume_task_parked(task_id):
                _parked_until[task_id] = time.monotonic() + PARK_COOLDOWN_SECONDS
                _logger.info(
                    "Worker %s parked task %d for %.0fs (no forward progress)",
                    WORKER_ID, task_id, PARK_COOLDOWN_SECONDS,
                )
            _logger.info("Worker %s finished task %d", WORKER_ID, task_id)
```

- [ ] **Step 6: 运行测试，确认转绿**

Run: `pytest server/tests/test_worker_park_cooldown.py server/tests/test_worker_executor.py -q`
Expected: PASS —— 冷却过滤 + prune 正确；claim 跳过 parked 返回 new；`_create_publishable_task` 既有调用（无 suffix）不回归。

- [ ] **Step 7: Commit**

```bash
git add server/worker/executor.py server/tests/test_worker_park_cooldown.py server/tests/test_worker_executor.py
git commit -m "fix(worker): parked 任务冷却 skip-map，_claim_next_task 跳过先跑别的

execute_task 判 parked 后 worker 把该 task 加进内存冷却 map（60s），
_claim_next_task 冷却窗内经 notin_ 跳过它——避免 order_by(created_at.asc)
永远先抢卡死老任务把 worker 卡在同一任务上。冷却到期自动清理，
积压记录待 worker 重启（进程内账号锁清空）后自然重跑。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: 改动③ — worker 容器 `init: true` 收 chrome 僵尸

**Files:**
- Modify: `deploy/docker-compose.prod.yml`（`worker` 服务加 `init: true`）
- Modify: `docker-compose.yml`（`worker` 服务加 `init: true`，与 prod 一致化）

**Interfaces:** 无代码接口；纯 compose 配置。

- [ ] **Step 1: prod compose worker 加 `init: true`**

在 `deploy/docker-compose.prod.yml` 的 `worker:` 服务块内（`command: ["python", "-m", "server.worker.executor"]` 同级）加一行：

```yaml
  worker:
    # ... 现有键（image / command / environment / depends_on / restart 等）不变
    init: true   # tini 当 PID 1 回收 reparent 上来的 <defunct> chrome 子进程
```

- [ ] **Step 2: 基础 compose worker 一致化**

在 `docker-compose.yml` 的 `worker:` 服务块同样加 `init: true`。

- [ ] **Step 3: 校验 compose 语法**

Run: `docker compose -f deploy/docker-compose.prod.yml config -q && docker compose -f docker-compose.yml config -q`
Expected: 无输出、退出码 0（YAML 合法、`init` 键被识别）。若本机无 docker，跳过并在 MR 说明里标注"待 CI/部署机校验"。

- [ ] **Step 4: Commit**

```bash
git add deploy/docker-compose.prod.yml docker-compose.yml
git commit -m "fix(worker): 容器加 init:true 回收 chrome 僵尸

worker 是容器 PID 1，不回收 reparent 上来的 <defunct> chrome 子进程
（卡死超时关会话后累积）。加 init:true 让 tini 当 PID 1 回收僵尸。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: 全量回归 + lint/format/typecheck 门禁

**Files:** 无改动（仅运行门禁；若 ruff 报格式问题，`ruff format` 改写后并入相邻任务的 commit 或单独 commit）

- [ ] **Step 1: ruff check + format 校验**

Run: `ruff check server/ && ruff format --check server/`
Expected: PASS。若 format 失败，运行 `ruff format server/` 后 `git add -A && git commit -m "style: ruff format"`。

- [ ] **Step 2: mypy（宽松）**

Run: `mypy server/app`
Expected: 不新增错误（新增函数带类型注解）。

- [ ] **Step 3: 相关测试全绿**

Run: `pytest server/tests/test_publish_timeout_lock_safety.py server/tests/test_run_pending_records_park.py server/tests/test_worker_park_cooldown.py server/tests/test_worker_executor.py server/tests/test_tasks_state_machine.py -q`
Expected: PASS（全部）。

- [ ] **Step 4: 更大范围 worker/tasks 回归（可选，时间允许）**

Run: `pytest server/tests/ -q -k "publish or worker or task"`
Expected: PASS（无与本改动相关的新红）。

---

## Self-Review

**1. Spec coverage**（对照 `docs/superpowers/specs/2026-07-14-worker-wedged-task-recovery-design.md` §4）：
- 改动① 归还闸槽 → Task 1 ✅
- 改动②a 主循环有界化 → Task 2 Step 4 ✅
- 改动②b parked 侧信道 → Task 2 Step 3 ✅
- 改动②c worker 冷却 skip-map + `_claim_next_task` 跳过 → Task 3 ✅
- 改动③ init:true → Task 4 ✅
- 测试策略 §5 全部有对应 TDD 任务 ✅
- 上线/回滚 §6 → 计划末尾"部署前停"，Task 均无迁移/base 变化 ✅
- 剔除的候选④ → 计划 Global Constraints 明确不含 ✅

**2. Placeholder scan**：无 TBD/TODO；所有代码步给出完整实码；compose 步给出确切 YAML 键。Task 4 Step 3 的"本机无 docker 则跳过"是明确降级路径、非占位。

**3. Type consistency**：
- `_return_publish_gate_slot(record_id: int)` — Task 1 定义、Task 1 两处调用一致。
- `_register_task_parked` / `consume_task_parked` — Task 2 定义（`tasks/executor.py`），Task 3 Step 5 worker import 并调用 `consume_task_parked(task_id) -> bool` 一致。
- `PARK_STALL_THRESHOLD`（tasks/executor.py）vs `PARK_COOLDOWN_SECONDS`（worker/executor.py）— 两个不同常量、不同文件，无混用。
- `_claim_next_task(db, skip_task_ids=frozenset())` — Task 3 定义，Task 3 Step 5 以 `frozenset(skip)` 调用一致；既有无参调用（test_worker_executor.py）因默认值不回归。
- `_active_parked_task_ids(now: float) -> set[int]` — Task 3 定义并在主循环以 `time.monotonic()` 调用一致。

## 上线 Handoff（部署前停）

三个代码改动 + compose 改动完成、全绿后：推分支 → 建 MR → 等 CI（`backend-lint` + `frontend`）绿。**因动 worker 并发/调度语义（非例行），合并 / 部署前停下等用户确认**，不自动上线。
