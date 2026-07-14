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
