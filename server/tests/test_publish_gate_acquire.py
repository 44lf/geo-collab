"""Task 4 Step 5（封堵 #8）：发布全局并发闸搬到主线程 submit 前获取 —— 行为契约。

关键变化（见 docs/plans/2026-06-16-resource-hardening.md Task 4 Step 5）：
- `_global_publish_sem`（裸 Semaphore、发布线程内 acquire）→ `_global_publish_gate`（ObservableGate）。
- 闸在主线程 `_start_runnable_records` 里 submit **之前**用 `try_acquire()` 获取：满了直接 return，
  本轮不再填；排队不再占记录执行预算（watchdog），也不再在发布线程里阻塞。
- 只有 submit 成功并登记 RunningRecord 后槽位才"移交"给运行生命周期；任何 submit 前的
  跳过 / 异常都由 finally 归还，绝不泄漏。
- 发布线程 `_publish_record` 不再 acquire/release 闸。
"""

from __future__ import annotations

import threading
from concurrent.futures import Future
from io import BytesIO
from types import SimpleNamespace

import pytest

from server.app.modules.accounts.browser import release_profile_lock_by_owner
from server.app.modules.tasks import executor as tasks_mod
from server.app.modules.tasks.models import PublishTask
from server.app.modules.tasks.service import list_task_records
from server.app.shared.concurrency import ObservableGate
from server.tests.utils import build_test_app

_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f"
    b"\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


class _FakeExecutor:
    """只记录 submit、返回未完成 Future，不真正跑发布——用于单测主线程 submit 决策。"""

    def __init__(self) -> None:
        self.submitted: list = []

    def submit(self, fn, *args):
        self.submitted.append((fn, args))
        return Future()


def _create_article(client, title: str = "Gate Article") -> int:
    cover = client.post(
        "/api/assets", files={"file": ("c.png", BytesIO(_PNG), "image/png")}
    ).json()["id"]
    return client.post(
        "/api/articles",
        json={
            "title": title,
            "content_json": {"type": "doc", "content": []},
            "plain_text": "body content",
            "cover_asset_id": cover,
        },
    ).json()["id"]


def _create_account(test_app, key: str) -> int:
    d = test_app.data_dir / "browser_states" / "toutiao" / key
    d.mkdir(parents=True, exist_ok=True)
    (d / "storage_state.json").write_text('{"cookies":[],"origins":[]}', encoding="utf-8")
    return test_app.client.post(
        "/api/accounts/toutiao/login",
        json={"display_name": f"Acc {key}", "account_key": key, "use_browser": False},
    ).json()["id"]


def _create_single_task(client, article_id: int, account_id: int, name: str = "gate-task") -> int:
    return client.post(
        "/api/tasks",
        json={
            "name": name,
            "task_type": "single",
            "article_id": article_id,
            "accounts": [{"account_id": account_id}],
        },
    ).json()["id"]


def test_publish_record_does_not_touch_global_gate(monkeypatch):
    """发布线程 _publish_record 不再 acquire/release 全局闸：闸打满时它仍立即跑完、in_use 不变。"""
    # 容量 1 的新闸并打满——旧实现（发布线程内 acquire）会在此死锁、join 超时
    gate = ObservableGate(1, name="publish")
    assert gate.try_acquire()
    monkeypatch.setattr(tasks_mod, "_global_publish_gate", gate)

    called: dict = {}

    class _Result:
        url = "https://example.com/x"
        message = "ok"

    def _runner_factory(_record):
        def _runner(article, account, *, stop_before_publish=False):
            called["ran"] = True
            return _Result()

        return _runner

    monkeypatch.setattr(tasks_mod, "build_publish_runner_for_record", _runner_factory)

    record = SimpleNamespace(id=1)
    article = SimpleNamespace(id=2)
    account = SimpleNamespace(id=3)

    box: dict = {}

    def _go():
        box["outcome"] = tasks_mod._publish_record(record, article, account, True)

    thread = threading.Thread(target=_go)
    thread.start()
    thread.join(timeout=3.0)

    assert not thread.is_alive(), "_publish_record 在闸打满时阻塞了——发布线程仍在 acquire 全局闸"
    assert called.get("ran") is True
    # 发布线程既不 acquire 也不 release：in_use 维持我们手动占的 1
    assert gate.in_use == 1
    gate.release()


@pytest.mark.mysql
def test_full_gate_blocks_submission(monkeypatch):
    """全局闸已满：主线程 _start_runnable_records 不再 submit，记录留 pending、闸不被超额获取。"""
    test_app = build_test_app(monkeypatch)
    try:
        tasks_mod._account_locks.clear()  # 防跨测试残留账号锁污染
        gate = ObservableGate(2, name="publish")
        assert gate.try_acquire() and gate.try_acquire()  # 打满
        monkeypatch.setattr(tasks_mod, "_global_publish_gate", gate)

        client = test_app.client
        article_id = _create_article(client)
        account_id = _create_account(test_app, "gate-full")
        task_id = _create_single_task(client, article_id, account_id)

        fake_exec = _FakeExecutor()
        with test_app.session_factory() as db:
            task = db.get(PublishTask, task_id)
            records = list_task_records(db, task_id)
            tasks_mod._start_runnable_records(db, task, fake_exec, {}, records)
            db.commit()
            records_after = list_task_records(db, task_id)

        assert fake_exec.submitted == []
        assert all(r.status == "pending" for r in records_after)
        assert gate.in_use == 2  # 没有被超额获取
    finally:
        try:
            gate.release()
            gate.release()
        except Exception:
            pass
        test_app.cleanup()


@pytest.mark.mysql
def test_gate_released_when_account_lock_busy(monkeypatch):
    """账号锁被占（同账号在途）：主线程拿了闸槽后走 blocked 分支，必须由 finally 归还闸——零泄漏。"""
    test_app = build_test_app(monkeypatch)
    locked_account: int | None = None
    try:
        tasks_mod._account_locks.clear()
        gate = ObservableGate(2, name="publish")
        monkeypatch.setattr(tasks_mod, "_global_publish_gate", gate)

        client = test_app.client
        article_id = _create_article(client)
        account_id = _create_account(test_app, "gate-acctlock")
        locked_account = account_id
        task_id = _create_single_task(client, article_id, account_id)

        # 预占该账号锁，模拟同账号已有记录在跑
        assert tasks_mod._try_acquire_account_lock(account_id)

        fake_exec = _FakeExecutor()
        with test_app.session_factory() as db:
            task = db.get(PublishTask, task_id)
            records = list_task_records(db, task_id)
            tasks_mod._start_runnable_records(db, task, fake_exec, {}, records)
            db.commit()

        assert fake_exec.submitted == []
        assert gate.in_use == 0, "账号锁被占时闸槽泄漏了（finally 未归还）"
    finally:
        if locked_account is not None:
            tasks_mod._release_account_lock(locked_account)
        test_app.cleanup()


@pytest.mark.mysql
def test_gate_transferred_on_successful_submit(monkeypatch):
    """submit 成功并登记 RunningRecord 后，闸槽移交运行生命周期：返回后 in_use==1（不被 finally 误放）。"""
    test_app = build_test_app(monkeypatch)
    record_id: int | None = None
    account_id: int | None = None
    try:
        tasks_mod._account_locks.clear()
        gate = ObservableGate(2, name="publish")
        monkeypatch.setattr(tasks_mod, "_global_publish_gate", gate)

        client = test_app.client
        article_id = _create_article(client)
        account_id = _create_account(test_app, "gate-xfer")
        task_id = _create_single_task(client, article_id, account_id)

        fake_exec = _FakeExecutor()
        running: dict = {}
        with test_app.session_factory() as db:
            task = db.get(PublishTask, task_id)
            records = list_task_records(db, task_id)
            tasks_mod._start_runnable_records(db, task, fake_exec, running, records)
            db.commit()

        assert len(fake_exec.submitted) == 1
        assert len(running) == 1
        record_id = next(iter(running.values())).record_id
        assert gate.in_use == 1, "submit 成功后闸槽应移交（保持占用），不应被 finally 误放"
    finally:
        # 模拟记录退场：归还移交出去的槽 + 账号锁 + profile 锁
        if record_id is not None:
            try:
                gate.release()
            except Exception:
                pass
            release_profile_lock_by_owner(owner_kind="publish", owner_id=record_id)
        if account_id is not None:
            tasks_mod._release_account_lock(account_id)
        test_app.cleanup()
