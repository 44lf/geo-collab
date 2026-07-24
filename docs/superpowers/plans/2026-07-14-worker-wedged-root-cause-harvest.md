# Worker 卡死线程「根因收割」实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 发布记录超时且 Playwright 级 close 杀不动卡死 chromium 时，纯 OS 级 profile 维度 SIGKILL 该 chromium 进程树，确认发布线程解绕后干净归还全局闸槽 + 账号锁 + profile 锁，恢复满额并发、无需重启 worker。

**Architecture:** 新增一个纯 OS 收割器 `harvest_chromium_by_profile`（扫 `/proc`、SIGKILL、依赖注入可测），挂到 `_handle_timed_out_record` 的 `terminated=False` 分支：先杀 chromium → 二次 `future.result` 确认线程死透 → 复用现有 `terminated=True` 收尾归还三锁；杀不动 / 线程仍卡则退回今天的保锁行为。开关默认开、可运行时关。

**Tech Stack:** Python 3、`concurrent.futures.Future`、Linux `/proc`、`os.kill(SIGKILL)`、pydantic-settings、pytest（纯逻辑、无 DB、collection-safe）。

设计稿：`docs/superpowers/specs/2026-07-14-worker-wedged-root-cause-harvest-design.md`。基线 `origin/main @ 25061fe`（含已上线 server-1.0.6 的 park/skip 修复）。

## Global Constraints

- **平台**：收割真实生效仅 Linux 容器；无 `/proc`（Windows 本地）时 `harvest_chromium_by_profile` no-op 返回 `killed=0`。发布本就只在容器跑。
- **不碰 Playwright 句柄**：收割器与新增路径**纯 OS 系统调用**，绝不调 `context.*` / `playwright.*` / `stop_remote_browser_session` / `_close_browser_handles`（那些是卡死线程 greenlet 专属，跨线程即坏）。
- **归还铁律**：只有二次 `future.result` **确认线程终止**（返回，不抛 `FutureTimeoutError`）才归还任何锁；chromium 杀不死（`survived` 非空）或线程仍卡 → 保锁、退回今天行为，**严格不劣于现状**。
- **无 double-release**：超时记录的闸槽/账号锁**有且仅有** `_handle_timed_out_record` 内归还一次（调用点 `executor.py:344-350` 已 `running.pop` 并全权委托 helper，`finally` 够不到）；沿用 `_retire_running_slot` 单次释放语义。
- **循环 import / collection 安全**：`_harvest_wedged_record` 内 `harvest_chromium_by_profile` / `profile_dir_from_state_path` 一律**函数内 lazy import**（`accounts/service.py` 反向 import 了 `tasks.models`）；测试顶层 import `tasks.executor` 是 collection 安全的（不拉 `db.session`）。
- **开关**：`GEO_PUBLISH_HARVEST_ENABLED` 默认 `True`；`GEO_PUBLISH_HARVEST_REJOIN_SECONDS` 默认 `5.0`。改环境后测试需 `get_settings.cache_clear()`。
- **无 DB 迁移、无 base 镜像变化**。门禁：`ruff check server/` + `ruff format --check server/` + `mypy server/app`。
- 运行测试：工具 shell 里 conda activate 不生效，用 `python -m pytest`（PATH 里的 conda 环境 python）。这些用例 DB-free、不带 `@pytest.mark.mysql`，无需 `GEO_TEST_DATABASE_URL`。

---

## File Structure

- `server/app/core/config.py` — 加两个 `Settings` 字段（开关 + rejoin 超时）。
- `server/app/modules/accounts/browser.py` — 新增 `HarvestResult` + `harvest_chromium_by_profile` + `/proc` 扫描辅助（`_read_proc_table` / `_read_ppid` / `_cmdline_targets_profile`）。收割器是本期核心、自成一个可独立单测的单元。
- `server/app/modules/tasks/executor.py` — 新增 `_harvest_wedged_record` helper；改 `_handle_timed_out_record` 的 `terminated=False` 分支接入收割。
- `server/tests/test_worker_harvest_chromium.py` — 新增，收割器纯逻辑单测（假 `/proc`）。
- `server/tests/test_publish_timeout_lock_safety.py` — 既有：更新 1 个用例 + 新增收割相关用例。

---

## Task 1: 配置开关

**Files:**
- Modify: `server/app/core/config.py:62`（紧接 `publish_park_cooldown_seconds` 后）
- Test: `server/tests/test_worker_harvest_chromium.py`（本任务先建文件，只放 config 测试）

**Interfaces:**
- Produces: `settings.publish_harvest_enabled: bool`（默认 `True`）、`settings.publish_harvest_rejoin_seconds: float`（默认 `5.0`）。

- [ ] **Step 1: 写失败测试**

新建 `server/tests/test_worker_harvest_chromium.py`：

```python
"""根因收割：chromium OS 级收割器 + 配置开关（纯逻辑、无 DB、collection-safe）。"""

from __future__ import annotations

from pathlib import Path

from server.app.core.config import Settings


def test_harvest_settings_defaults():
    fields = Settings.model_fields
    assert fields["publish_harvest_enabled"].default is True
    assert fields["publish_harvest_rejoin_seconds"].default == 5.0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest server/tests/test_worker_harvest_chromium.py::test_harvest_settings_defaults -q`
Expected: FAIL — `KeyError: 'publish_harvest_enabled'`

- [ ] **Step 3: 加字段**

`server/app/core/config.py`，在 `publish_park_cooldown_seconds: float = 60.0` 那行之后插入：

```python
    publish_harvest_enabled: bool = True
    publish_harvest_rejoin_seconds: float = 5.0
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest server/tests/test_worker_harvest_chromium.py::test_harvest_settings_defaults -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git -C /e/geo-wtr add server/app/core/config.py server/tests/test_worker_harvest_chromium.py
git -C /e/geo-wtr commit -m "feat(worker): 根因收割配置开关 GEO_PUBLISH_HARVEST_ENABLED/REJOIN_SECONDS"
```

---

## Task 2: OS 级 chromium 收割器

**Files:**
- Modify: `server/app/modules/accounts/browser.py`（顶部加 `import os` / `import signal`；文件内新增函数，建议放在 `reconcile_leaked_sessions` 之后、`_cleanup_x11_socket` 附近的进程管理区）
- Test: `server/tests/test_worker_harvest_chromium.py`

**Interfaces:**
- Produces:
  - `HarvestResult`（dataclass：`killed: int`、`survived: list[int]`）
  - `harvest_chromium_by_profile(profile_dir: Path, *, proc_scan=None, kill=None, is_alive=None, settle_timeout: float = 2.0) -> HarvestResult` — 扫 `/proc` 找 cmdline 含 `--user-data-dir=<profile_dir>` 的 chromium + 其整棵进程树（子进程未必带 flag，靠 ppid 兜住）→ SIGKILL → 短等确认。`proc_scan`/`kill`/`is_alive` 可注入以单测。非 Linux（无 `/proc`）→ `killed=0`。

- [ ] **Step 1: 写失败测试**

在 `server/tests/test_worker_harvest_chromium.py` 追加：

```python
def _harvest():
    from server.app.modules.accounts.browser import harvest_chromium_by_profile

    return harvest_chromium_by_profile


def test_harvest_kills_target_profile_tree_only():
    rows = [
        (100, 1, ["chrome", "--user-data-dir=/data/acc/1/profile", "--type=browser"]),
        (101, 100, ["chrome", "--type=renderer"]),  # 子进程，不带 flag，靠 ppid 兜住
        (200, 1, ["chrome", "--user-data-dir=/data/acc/2/profile"]),  # 别的 profile
    ]
    killed: list[int] = []
    result = _harvest()(
        Path("/data/acc/1/profile"),
        proc_scan=lambda: rows,
        kill=lambda pid: killed.append(pid),
        is_alive=lambda pid: False,
        settle_timeout=0.2,
    )
    assert set(killed) == {100, 101}
    assert 200 not in killed
    assert result.survived == []
    assert result.killed == 2


def test_harvest_reports_survivors_when_kill_fails():
    rows = [(100, 1, ["chrome", "--user-data-dir=/p/profile"])]
    result = _harvest()(
        Path("/p/profile"),
        proc_scan=lambda: rows,
        kill=lambda pid: None,
        is_alive=lambda pid: True,  # 杀不死
        settle_timeout=0.1,
    )
    assert result.survived == [100]
    assert result.killed == 0


def test_harvest_noop_when_no_proc():
    killed: list[int] = []
    result = _harvest()(
        Path("/p/profile"),
        proc_scan=lambda: [],  # 非 Linux / 无 /proc
        kill=lambda pid: killed.append(pid),
    )
    assert killed == []
    assert result.killed == 0
    assert result.survived == []


def test_harvest_no_match_leaves_all_alone():
    rows = [(100, 1, ["chrome", "--user-data-dir=/other/profile"])]
    killed: list[int] = []
    result = _harvest()(
        Path("/p/profile"), proc_scan=lambda: rows, kill=lambda pid: killed.append(pid)
    )
    assert killed == []
    assert result.killed == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest server/tests/test_worker_harvest_chromium.py -q -k harvest_kills or harvest_reports or harvest_noop or harvest_no_match`
Expected: FAIL — `ImportError: cannot import name 'harvest_chromium_by_profile'`

- [ ] **Step 3: 实现收割器**

`server/app/modules/accounts/browser.py` 顶部 import 区（`import subprocess` 附近）加：

```python
import os
import signal
```

文件内新增（放进程管理区，如 `reconcile_leaked_sessions` 之后）：

```python
@dataclass
class HarvestResult:
    """chromium OS 收割结果：杀掉数 + SIGKILL 仍不死的 pid（survived 非空＝不该归还锁）。"""

    killed: int
    survived: list[int]


def _read_ppid(stat_path: Path) -> int:
    # /proc/<pid>/stat: "pid (comm) state ppid ..."；comm 可含空格/括号，故取最后一个 ')' 之后第 2 字段
    data = stat_path.read_text()
    rparen = data.rfind(")")
    fields = data[rparen + 2 :].split()
    return int(fields[1])


def _read_proc_table() -> list[tuple[int, int, list[str]]]:
    """扫 /proc，返回 [(pid, ppid, cmdline_args)]。非 Linux（无 /proc）返回空。"""
    proc = Path("/proc")
    if not proc.is_dir():
        return []
    rows: list[tuple[int, int, list[str]]] = []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
            ppid = _read_ppid(entry / "stat")
        except (FileNotFoundError, ProcessLookupError, PermissionError, ValueError):
            continue
        args = [a for a in raw.decode("utf-8", "replace").split("\x00") if a]
        rows.append((int(entry.name), ppid, args))
    return rows


def _cmdline_targets_profile(args: list[str], profile_dir: str) -> bool:
    if f"--user-data-dir={profile_dir}" in args:
        return True
    for i, a in enumerate(args):
        if a == "--user-data-dir" and i + 1 < len(args) and args[i + 1] == profile_dir:
            return True
    return False


def harvest_chromium_by_profile(
    profile_dir: Path,
    *,
    proc_scan: Callable[[], list[tuple[int, int, list[str]]]] | None = None,
    kill: Callable[[int], None] | None = None,
    is_alive: Callable[[int], bool] | None = None,
    settle_timeout: float = 2.0,
) -> HarvestResult:
    """OS 级 SIGKILL 指定 persistent profile 的 chromium 进程树（含子进程）。

    纯 OS 系统调用、不碰 Playwright 句柄，可从任意线程安全调用。seed＝cmdline 含
    `--user-data-dir=<profile_dir>` 的进程；再按 ppid 展开整棵树（renderer/gpu 未必带 flag）。
    survived 非空＝SIGKILL 都没杀死，交调用方决定不归还锁。非 Linux（无 /proc）no-op。
    """
    scan = proc_scan or _read_proc_table
    do_kill = kill or (lambda pid: os.kill(pid, signal.SIGKILL))
    alive = is_alive or (lambda pid: Path(f"/proc/{pid}").exists())

    rows = scan()
    target = str(profile_dir).rstrip("/")
    children: dict[int, list[int]] = {}
    for pid, ppid, _args in rows:
        children.setdefault(ppid, []).append(pid)

    targets: set[int] = set()
    stack = [pid for pid, _ppid, args in rows if _cmdline_targets_profile(args, target)]
    while stack:
        pid = stack.pop()
        if pid in targets:
            continue
        targets.add(pid)
        stack.extend(children.get(pid, []))

    if not targets:
        return HarvestResult(killed=0, survived=[])

    for pid in targets:
        try:
            do_kill(pid)
        except (ProcessLookupError, PermissionError):
            pass

    deadline = time.monotonic() + settle_timeout
    survived = list(targets)
    while survived and time.monotonic() < deadline:
        time.sleep(0.05)
        survived = [pid for pid in survived if alive(pid)]

    return HarvestResult(killed=len(targets) - len(survived), survived=survived)
```

> 注：`Callable` / `Path` / `dataclass` / `time` 在 `browser.py` 顶部已 import（`from collections.abc import Callable`、`from pathlib import Path`、`from dataclasses import dataclass`、`import time`）；只需新增 `import os` / `import signal`。`signal.SIGKILL` 仅在默认 kill lambda **被调用时**求值——非 Linux 走空 scan、早 return，永不触发，Windows import signal 本身安全。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest server/tests/test_worker_harvest_chromium.py -q`
Expected: PASS（5 个用例：config + 4 收割器）

- [ ] **Step 5: lint**

Run: `python -m ruff check server/app/modules/accounts/browser.py && python -m ruff format --check server/app/modules/accounts/browser.py`
Expected: 无报错（有 format 差异则 `ruff format` 改写后重跑）

- [ ] **Step 6: 提交**

```bash
git -C /e/geo-wtr add server/app/modules/accounts/browser.py server/tests/test_worker_harvest_chromium.py
git -C /e/geo-wtr commit -m "feat(worker): OS 级 chromium 收割器 harvest_chromium_by_profile(/proc SIGKILL 进程树)"
```

---

## Task 3: 接入 `_handle_timed_out_record` 超时分支

**Files:**
- Modify: `server/app/modules/tasks/executor.py`（新增 `_harvest_wedged_record`；改 `_handle_timed_out_record` 的 631-641 收尾块）
- Test: `server/tests/test_publish_timeout_lock_safety.py`（更新 1 + 新增 4）

**Interfaces:**
- Consumes: `harvest_chromium_by_profile` / `HarvestResult`（Task 2）、`profile_dir_from_state_path`（`accounts/service.py`）、`settings.publish_harvest_enabled` / `publish_harvest_rejoin_seconds`（Task 1）。
- Produces: `_harvest_wedged_record(db: Session, running_record: RunningRecord, future: Future) -> bool`（True＝线程确认死、可归还三锁）。`_handle_timed_out_record` 语义扩展：`terminated=False` 且开关开时，尝试收割把 `terminated` 翻成 True。

- [ ] **Step 1: 写失败测试（收割成功 → 归还三锁）**

在 `server/tests/test_publish_timeout_lock_safety.py` 追加。先加一个能造「已终止」态的 future 辅助（文件已有 `_running_future`，这里用 `finished=True`）：

```python
def test_harvest_recovers_wedged_thread_releases_all_locks(monkeypatch):
    """开关开 + 收割成功（无 survivor）+ 线程解绕：profile 锁 + 账号锁 + 闸槽全归还，返回 True。"""
    from server.app.core.config import get_settings
    from server.app.modules.accounts import browser as browser_mod
    from server.app.modules.accounts import service as service_mod

    monkeypatch.setenv("GEO_PUBLISH_HARVEST_ENABLED", "true")
    get_settings.cache_clear()

    gate = ObservableGate(2, name="publish")
    assert gate.try_acquire()  # 该记录持有的 1 格
    monkeypatch.setattr(tasks_mod, "_global_publish_gate", gate)

    account_id = 990201
    assert tasks_mod._try_acquire_account_lock(account_id)

    monkeypatch.setattr(tasks_mod, "_mark_record_failed", lambda *a, **k: None)
    monkeypatch.setattr(tasks_mod, "_record_crossed_commit", lambda db, rid: False)  # fake_db 无 .execute
    monkeypatch.setattr(tasks_mod, "_close_record_browser", lambda rid: None, raising=False)
    released: list = []
    monkeypatch.setattr(
        tasks_mod, "_release_record_profile_lock", lambda rid: released.append(rid), raising=False
    )
    # lazy import 的目标：patch 源模块函数
    monkeypatch.setattr(
        browser_mod,
        "harvest_chromium_by_profile",
        lambda profile_dir: browser_mod.HarvestResult(killed=3, survived=[]),
    )
    monkeypatch.setattr(service_mod, "profile_dir_from_state_path", lambda sp: "/p/profile")

    # 假 db：get(Account, id) → 有 state_path 的账号
    fake_db = SimpleNamespace(get=lambda model, _id: SimpleNamespace(state_path="acc/1/x.json"))
    rr = SimpleNamespace(record_id=71, account_id=account_id)
    # 卡死态：第一次 result(timeout) 抛超时；收割后二次 join 用「已完成」future 表示线程解绕
    stuck = _running_future(finished=True)  # 已 FINISHED：二次 future.result 立即返回＝线程死透

    try:
        terminated = tasks_mod._handle_timed_out_record(fake_db, 1, rr, stuck, result_timeout=0.05)
        assert terminated is True
        assert released == [71]                    # profile 锁释放
        assert tasks_mod._try_acquire_account_lock(account_id) is True  # 账号锁已归还→可重拿
        tasks_mod._release_account_lock(account_id)
        assert gate.in_use == 0                     # 闸槽归还，计数回零
    finally:
        tasks_mod._release_account_lock(account_id)
        get_settings.cache_clear()
```

> 说明：`stuck = _running_future(finished=True)` 会让 `_handle_timed_out_record` 第一次 `future.result(0.05)` **立即返回**→ `terminated=True`，那样根本不进收割分支。要测收割，需第一次超时、第二次成功。改用下方"两段式" future 辅助。

先在测试文件顶部（`_running_future` 之后）加一个受控 future 辅助：

```python
class _TwoPhaseFuture:
    """第一次 result() 抛 FutureTimeoutError（模拟卡死），之后 result() 立即返回（模拟收割后解绕）。"""

    def __init__(self, *, ever_unwinds: bool):
        self._calls = 0
        self._ever_unwinds = ever_unwinds

    def cancel(self):
        return False

    def done(self):
        return False

    def result(self, timeout=None):
        from concurrent.futures import TimeoutError as FutureTimeoutError

        self._calls += 1
        if self._calls == 1 or not self._ever_unwinds:
            raise FutureTimeoutError()
        return None
```

并把上面测试里的 `stuck = _running_future(finished=True)` 改为 `stuck = _TwoPhaseFuture(ever_unwinds=True)`。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest server/tests/test_publish_timeout_lock_safety.py::test_harvest_recovers_wedged_thread_releases_all_locks -q`
Expected: FAIL — `AttributeError: module ... has no attribute '_harvest_wedged_record'`（或 `terminated is True` 断言失败，因收割未接入）

- [ ] **Step 3: 实现 `_harvest_wedged_record` + 接入超时分支**

`server/app/modules/tasks/executor.py`，在 `_handle_timed_out_record` 定义**之前**新增：

```python
def _harvest_wedged_record(db: Session, running_record: RunningRecord, future: Future) -> bool:
    """根因收割：OS 级 SIGKILL 卡死记录的 chromium(profile 维度) → 二次 join 确认线程解绕。

    仅由 _handle_timed_out_record 的 terminated=False 分支调用。返回线程是否确认终止——
    True 才可由调用方安全归还 profile 锁 + 闸槽 + 账号锁；False 退回今天的保锁行为。
    纯 OS 收割，不碰 Playwright 句柄，可从 watchdog 线程安全跑。
    """
    # lazy import：accounts.service 反向 import 了 tasks.models，避免模块级循环 import
    from server.app.modules.accounts.browser import harvest_chromium_by_profile
    from server.app.modules.accounts.service import profile_dir_from_state_path

    account = db.get(Account, running_record.account_id)
    if account is None or account.state_path is None:
        return False
    result = harvest_chromium_by_profile(profile_dir_from_state_path(account.state_path))
    if result.survived:
        emit_resource_alert(
            f"record {running_record.record_id}: {len(result.survived)} chromium proc(s) survived "
            f"SIGKILL; account/profile locks held, leaving for recovery",
            {"record_id": running_record.record_id, "account_id": running_record.account_id},
        )
        return False
    try:
        future.result(timeout=get_settings().publish_harvest_rejoin_seconds)
        return True
    except FutureTimeoutError:
        return False
    except Exception:
        return True
```

再改 `_handle_timed_out_record` 结尾的收尾块（当前 631-641），把：

```python
    if terminated:
        _release_record_profile_lock(running_record.record_id)
        _retire_running_slot(running_record)
    else:
        _mark_record_zombie(db, task_id, running_record.record_id)
        emit_resource_alert(
            f"record {running_record.record_id} publish thread still alive after "
            f"{result_timeout:g}s; account/profile locks held, leaving for recovery",
            {"record_id": running_record.record_id, "account_id": running_record.account_id},
        )
    return terminated
```

改成：

```python
    if not terminated and get_settings().publish_harvest_enabled:
        terminated = _harvest_wedged_record(db, running_record, future)

    if terminated:
        _release_record_profile_lock(running_record.record_id)
        _retire_running_slot(running_record)
    else:
        _mark_record_zombie(db, task_id, running_record.record_id)
        emit_resource_alert(
            f"record {running_record.record_id} publish thread still alive after "
            f"{result_timeout:g}s; account/profile locks held, leaving for recovery",
            {"record_id": running_record.record_id, "account_id": running_record.account_id},
        )
    return terminated
```

> `emit_resource_alert` / `get_settings` / `FutureTimeoutError` / `Account` / `Future` / `Session` 均已在 `executor.py` 顶部 import，无需新增。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest server/tests/test_publish_timeout_lock_safety.py::test_harvest_recovers_wedged_thread_releases_all_locks -q`
Expected: PASS

- [ ] **Step 5: 更新既有「卡死保锁」用例（开关开时代表"收割也救不回"）**

既有 `test_stuck_publish_thread_keeps_account_and_profile_locks`（默认开关开、`db=None`）现在会进收割分支并对 `None` 调 `db.get` 崩溃。改成模拟「收割尝试过但线程仍卡」：在该用例造 `stuck = _running_future()` 之后、调用 `_handle_timed_out_record` 之前，插入一行：

```python
    # 收割尝试但线程仍卡（chromium 杀不动 / 卡在非 chromium IO）→ 保锁，退回今天行为
    monkeypatch.setattr(tasks_mod, "_harvest_wedged_record", lambda *a, **k: False, raising=False)
```

其余断言（账号锁 / profile 锁未释放、标僵尸、告警、返回 False）保持不变。

- [ ] **Step 6: 新增「收割后仍 survivor → 保锁」用例**

追加：

```python
def test_harvest_survivors_keep_locks(monkeypatch):
    """开关开 + 收割后仍有 chromium 存活（SIGKILL 杀不死）→ 三锁保留、返回 False。"""
    from server.app.core.config import get_settings
    from server.app.modules.accounts import browser as browser_mod
    from server.app.modules.accounts import service as service_mod
    from server.app.shared import resource_metrics as rm

    monkeypatch.setenv("GEO_PUBLISH_HARVEST_ENABLED", "true")
    get_settings.cache_clear()

    gate = ObservableGate(2, name="publish")
    assert gate.try_acquire()
    monkeypatch.setattr(tasks_mod, "_global_publish_gate", gate)
    account_id = 990202
    assert tasks_mod._try_acquire_account_lock(account_id)
    monkeypatch.setattr(tasks_mod, "_mark_record_failed", lambda *a, **k: None)
    monkeypatch.setattr(tasks_mod, "_record_crossed_commit", lambda db, rid: False)  # fake_db 无 .execute
    monkeypatch.setattr(tasks_mod, "_mark_record_zombie", lambda db, tid, rid: None, raising=False)
    monkeypatch.setattr(tasks_mod, "_close_record_browser", lambda rid: None, raising=False)
    monkeypatch.setattr(
        tasks_mod, "_release_record_profile_lock", lambda rid: None, raising=False
    )
    alerts: list = []
    monkeypatch.setattr(rm, "_alert_hook", lambda msg, ctx=None: alerts.append((msg, ctx)))
    monkeypatch.setattr(
        browser_mod,
        "harvest_chromium_by_profile",
        lambda profile_dir: browser_mod.HarvestResult(killed=0, survived=[12345]),
    )
    monkeypatch.setattr(service_mod, "profile_dir_from_state_path", lambda sp: "/p/profile")

    fake_db = SimpleNamespace(get=lambda model, _id: SimpleNamespace(state_path="acc/1/x.json"))
    rr = SimpleNamespace(record_id=72, account_id=account_id)
    stuck = _TwoPhaseFuture(ever_unwinds=False)

    try:
        terminated = tasks_mod._handle_timed_out_record(fake_db, 1, rr, stuck, result_timeout=0.05)
        assert terminated is False
        assert tasks_mod._try_acquire_account_lock(account_id) is False  # 账号锁保留
        assert gate.in_use == 1                                          # 闸槽保留
        assert any("survived" in msg for msg, _ in alerts)               # survivor 告警
    finally:
        tasks_mod._release_account_lock(account_id)
        get_settings.cache_clear()
```

- [ ] **Step 7: 新增「开关关 → 逐字退回今天行为、不调收割」用例**

追加：

```python
def test_harvest_disabled_skips_harvest(monkeypatch):
    """开关关：不调收割器，三锁保留、标僵尸、返回 False（逐字今天行为）。"""
    from server.app.core.config import get_settings

    monkeypatch.setenv("GEO_PUBLISH_HARVEST_ENABLED", "false")
    get_settings.cache_clear()

    gate = ObservableGate(2, name="publish")
    assert gate.try_acquire()
    monkeypatch.setattr(tasks_mod, "_global_publish_gate", gate)
    account_id = 990203
    assert tasks_mod._try_acquire_account_lock(account_id)
    monkeypatch.setattr(tasks_mod, "_mark_record_failed", lambda *a, **k: None)
    monkeypatch.setattr(tasks_mod, "_mark_record_zombie", lambda db, tid, rid: None, raising=False)
    monkeypatch.setattr(tasks_mod, "_close_record_browser", lambda rid: None, raising=False)

    def _boom(*a, **k):
        raise AssertionError("harvest must not be called when disabled")

    monkeypatch.setattr(tasks_mod, "_harvest_wedged_record", _boom, raising=False)

    rr = SimpleNamespace(record_id=73, account_id=account_id)
    stuck = _running_future()  # 一直卡死

    try:
        terminated = tasks_mod._handle_timed_out_record(None, 1, rr, stuck, result_timeout=0.05)
        assert terminated is False
        assert tasks_mod._try_acquire_account_lock(account_id) is False  # 账号锁保留
        assert gate.in_use == 1
    finally:
        tasks_mod._release_account_lock(account_id)
        get_settings.cache_clear()
```

- [ ] **Step 8: 跑整组测试确认全绿**

Run: `python -m pytest server/tests/test_publish_timeout_lock_safety.py server/tests/test_worker_harvest_chromium.py -q`
Expected: PASS（既有 2 + 新增：收割成功 / survivor 保锁 / 开关关 + Task1/2 的 5 个）

- [ ] **Step 9: lint + mypy**

Run: `python -m ruff check server/app/modules/tasks/executor.py server/app/modules/accounts/browser.py && python -m ruff format --check server/app/modules/tasks/executor.py server/app/modules/accounts/browser.py && python -m mypy server/app/modules/tasks/executor.py server/app/modules/accounts/browser.py`
Expected: 无报错

- [ ] **Step 10: 提交**

```bash
git -C /e/geo-wtr add server/app/modules/tasks/executor.py server/tests/test_publish_timeout_lock_safety.py
git -C /e/geo-wtr commit -m "feat(worker): 超时分支接入根因收割——杀 chromium 确认线程死后归还闸槽/账号/profile 锁"
```

---

## Task 4: 全量回归 + 收尾

**Files:** 无新增，跑全套受影响测试 + 门禁。

- [ ] **Step 1: 跑 worker/executor 相关既有测试防回归**

Run: `python -m pytest server/tests/test_publish_timeout_lock_safety.py server/tests/test_worker_harvest_chromium.py server/tests/test_worker_executor.py server/tests/test_worker_park_cooldown.py server/tests/test_run_pending_records_park.py -q`
Expected: PASS（收割不改 park/skip 路径，park 系列应原样绿）

- [ ] **Step 2: 全仓 lint / format / mypy 门禁**

Run: `python -m ruff check server/ && python -m ruff format --check server/ && python -m mypy server/app`
Expected: 无报错

- [ ] **Step 3: 确认无 docker-compose / 迁移改动**

Run: `git -C /e/geo-wtr diff --name-only origin/main`
Expected: 仅 `server/app/core/config.py`、`server/app/modules/accounts/browser.py`、`server/app/modules/tasks/executor.py`、两个测试文件、以及 `docs/superpowers/{specs,plans}/2026-07-14-worker-wedged-root-cause-harvest*.md`。**无** `docker-compose*.yml`、**无** `server/alembic/versions/*`。

- [ ] **Step 4: 停下等人工确认再上线**

本 spec 属**非例行**（改并发恢复语义）。按 CLAUDE.md / 记忆约定：推分支 → 建 MR → CI 绿 → **停下等用户确认** → 走 geo-release（server tag）部署。部署后核对：一次超时后闸槽计数回满、卡死账号能再发、`<defunct>` 不堆积。**不自动上线。**

---

## Self-Review（计划 vs spec）

**Spec 覆盖：**
- §3.2 收割器 → Task 2 ✓
- §3.3 超时分支接入 → Task 3 ✓
- §3.4 归还铁律 + 无 double-release → Task 3（`terminated` 翻转仅在二次 join 成功；复用单次 `_retire_running_slot`）+ Global Constraints ✓
- §3.5 #2 安全（确认死才放 profile 锁）→ Task 3（`_release_record_profile_lock` 只在 `terminated=True` 收尾）✓
- §3.6 开关/配置 → Task 1 ✓
- §5 测试（收割器假 /proc、超时分支 3a/3b/开关关、真 chromium 仅容器）→ Task 2 + Task 3（真 chromium 端到端列为 Task4 Step4 部署后核对，非本地单测）✓
- §6 上线/回滚 → Task 4 Step 4 ✓

**占位符扫描：** 无 TBD/TODO；每个代码步给出完整代码与命令。

**类型一致性：** `HarvestResult(killed, survived)` 在 Task 2 定义、Task 3 测试按 `HarvestResult(killed=..., survived=[...])` 构造；`harvest_chromium_by_profile(profile_dir)` 签名一致；`_harvest_wedged_record(db, running_record, future) -> bool` 定义与调用点一致。
