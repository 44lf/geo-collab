"""Task 8（封堵 #2）：发布超时——确认线程终止后再释放账号/profile 锁 —— 锁所有权不变式。

根因：超时分支 `future.result(timeout=10)` 若仍 `TimeoutExpired`（发布线程卡在 IO、未对
Chromium context 关闭做出响应）被吞，而账号锁 + profile 锁已释放（`_retire_running_slot` +
`_stop_record_session`）→ 下一条同账号记录对同一 persistent profile 再开 Chromium，目录并发写
损坏。

契约（纯逻辑、mock 卡死 future、无 DB）：
- 线程仍存活（`result` 抛 `FutureTimeoutError`）→ 账号锁 + profile 锁 + 全局闸槽**均不释放**；
  记录标"僵尸待清" + 走告警；返回 `False`。
- 线程确认终止（`result` 正常返回）→ profile 锁释放 + 退场（账号锁 + 闸槽归还）；
  不标僵尸、不告警；返回 `True`。

注意：顶层 import executor / concurrency 是 collection 安全的（不拉 db.session，参 test_publish_gate_acquire）；
resource_metrics 在函数内 lazy import 以稳妥避开 collection 期建引擎陷阱。
"""

from __future__ import annotations

from concurrent.futures import Future
from types import SimpleNamespace

from server.app.modules.tasks import executor as tasks_mod
from server.app.shared.concurrency import ObservableGate


def _running_future(*, finished: bool = False) -> Future:
    """造一个处于 RUNNING 态的真实 Future：cancel() 必失败、result() 行为可控。

    - finished=False：留在 RUNNING，result(timeout) 会 TimeoutExpired（模拟线程卡死）。
    - finished=True：RUNNING→FINISHED，result() 立即返回（模拟线程已终止）。
    """
    f: Future = Future()
    f.set_running_or_notify_cancel()  # PENDING→RUNNING：模拟发布线程在跑
    if finished:
        f.set_result(None)  # RUNNING→FINISHED
    return f


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


def test_stuck_publish_thread_keeps_account_and_profile_locks(monkeypatch):
    """线程超时仍存活：账号锁 + profile 锁 + 闸槽均不释放，记录标僵尸 + 告警，返回 False。"""
    from server.app.shared import resource_metrics as rm

    gate = ObservableGate(2, name="publish")
    assert gate.try_acquire()  # 该记录在跑时持有的 1 个闸槽
    monkeypatch.setattr(tasks_mod, "_global_publish_gate", gate)

    account_id = 990001
    assert tasks_mod._try_acquire_account_lock(account_id)  # 该记录持有的账号锁

    # DB 写入旁路（纯逻辑）：mark_failed / mark_zombie 仅记调用，不碰 DB
    monkeypatch.setattr(tasks_mod, "_mark_record_failed", lambda *a, **k: None)
    zombie: list = []
    monkeypatch.setattr(
        tasks_mod, "_mark_record_zombie", lambda db, tid, rid: zombie.append(rid), raising=False
    )
    # 关浏览器旁路：不真起会话
    monkeypatch.setattr(tasks_mod, "_close_record_browser", lambda rid: None, raising=False)
    # profile 锁释放探针
    released_profiles: list = []
    monkeypatch.setattr(
        tasks_mod,
        "_release_record_profile_lock",
        lambda rid: released_profiles.append(rid),
        raising=False,
    )
    # 告警探针
    alerts: list = []
    monkeypatch.setattr(rm, "_alert_hook", lambda msg, ctx=None: alerts.append((msg, ctx)))

    rr = SimpleNamespace(record_id=7, account_id=account_id)
    stuck = _running_future()
    # 收割尝试但线程仍卡（chromium 杀不动 / 卡在非 chromium IO）→ 保锁，退回今天行为
    monkeypatch.setattr(tasks_mod, "_harvest_wedged_record", lambda *a, **k: False, raising=False)

    try:
        terminated = tasks_mod._handle_timed_out_record(None, 1, rr, stuck, result_timeout=0.05)

        assert terminated is False
        # 账号锁未释放：重拿应失败
        assert tasks_mod._try_acquire_account_lock(account_id) is False
        # profile 锁未释放
        assert released_profiles == []
        # 闸槽未归还（in_use 维持占用的 1）
        assert gate.in_use == 1
        # 记录标僵尸 + 告警
        assert zombie == [7]
        assert len(alerts) == 1
    finally:
        tasks_mod._release_account_lock(account_id)
        stuck.set_result(None)


def test_terminated_publish_thread_releases_locks(monkeypatch):
    """线程确认终止：profile 锁释放 + 闸槽 + 账号锁归还，不标僵尸/不告警，返回 True。"""
    from server.app.shared import resource_metrics as rm

    gate = ObservableGate(2, name="publish")
    assert gate.try_acquire()
    monkeypatch.setattr(tasks_mod, "_global_publish_gate", gate)

    account_id = 990002
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

    rr = SimpleNamespace(record_id=8, account_id=account_id)
    done = _running_future(finished=True)

    released_account = False
    try:
        terminated = tasks_mod._handle_timed_out_record(None, 1, rr, done, result_timeout=0.05)

        assert terminated is True
        assert released_profiles == [8]  # profile 锁释放
        assert gate.in_use == 0  # 闸槽归还
        # 账号锁已释放 → 能重拿（重拿后下方 finally 再放）
        assert tasks_mod._try_acquire_account_lock(account_id) is True
        released_account = True
        assert zombie == []
        assert alerts == []
    finally:
        if released_account:
            tasks_mod._release_account_lock(account_id)


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
    monkeypatch.setattr(
        tasks_mod, "_record_crossed_commit", lambda db, rid: False
    )  # fake_db 无 .execute
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
    # 卡死态：第一次 result(timeout) 抛超时；收割后二次 join 表示线程解绕
    stuck = _TwoPhaseFuture(ever_unwinds=True)

    try:
        terminated = tasks_mod._handle_timed_out_record(fake_db, 1, rr, stuck, result_timeout=0.05)
        assert terminated is True
        assert released == [71]  # profile 锁释放
        assert tasks_mod._try_acquire_account_lock(account_id) is True  # 账号锁已归还→可重拿
        tasks_mod._release_account_lock(account_id)
        assert gate.in_use == 0  # 闸槽归还，计数回零
    finally:
        tasks_mod._release_account_lock(account_id)
        get_settings.cache_clear()


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
    monkeypatch.setattr(
        tasks_mod, "_record_crossed_commit", lambda db, rid: False
    )  # fake_db 无 .execute
    monkeypatch.setattr(tasks_mod, "_mark_record_zombie", lambda db, tid, rid: None, raising=False)
    monkeypatch.setattr(tasks_mod, "_close_record_browser", lambda rid: None, raising=False)
    monkeypatch.setattr(tasks_mod, "_release_record_profile_lock", lambda rid: None, raising=False)
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
        assert gate.in_use == 1  # 闸槽保留
        assert any("survived" in msg for msg, _ in alerts)  # survivor 告警
    finally:
        tasks_mod._release_account_lock(account_id)
        get_settings.cache_clear()


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
