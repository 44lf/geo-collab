"""改动①c：可配冷却 skip-map（collection-safe park_cooldown）+ _claim_next_task 跳过 parked。"""

from __future__ import annotations

from datetime import timedelta

import pytest

from server.app.core.time import utcnow
from server.app.modules.tasks.models import PublishTask
from server.tests.utils import build_test_app


def test_active_parked_filters_by_cooldown_and_prunes():
    """纯逻辑（无 DB）：冷却窗内的 task_id 返回，已到期的被清出 map。"""
    from server.worker import park_cooldown as pc  # collection-safe，不拉 db.session

    pc._parked_until.clear()
    pc._parked_until[101] = 1000.0
    pc._parked_until[202] = 2000.0
    try:
        active = pc.active_parked_task_ids(now=1500.0)  # 101 过期、202 在冷却
        assert active == {202}
        assert 101 not in pc._parked_until  # 到期项被 prune
    finally:
        pc._parked_until.clear()


@pytest.mark.mysql
def test_claim_skips_parked_task_and_picks_next(monkeypatch):
    """_claim_next_task(skip_task_ids={old}) 跳过 parked 的最旧任务、返回下一个可跑任务。"""
    test_app = build_test_app(monkeypatch)
    try:
        from server.tests.test_worker_executor import _create_publishable_task
        from server.worker import executor as wex

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


@pytest.mark.mysql
def test_worker_iteration_parks_then_next_claim_skips(monkeypatch):
    """端到端 glue：一轮 _run_worker_iteration 让卡死老任务 park+写冷却+释放认领，
    下一次 claim 跳过冷却中的老任务、选中新任务。"""
    import time as _t

    from server.app.modules.tasks.models import PublishRecord

    test_app = build_test_app(monkeypatch)
    try:
        from server.app.modules.tasks import executor as ex
        from server.tests.test_worker_executor import _create_publishable_task
        from server.worker import executor as wex
        from server.worker import park_cooldown as pc

        monkeypatch.setattr(ex, "PARK_STALL_THRESHOLD", 2)
        monkeypatch.setattr(wex, "_periodic_recovery", lambda db: None)  # 隔离周期恢复
        pc._parked_until.clear()

        old_id = _create_publishable_task(test_app, suffix="old")
        new_id = _create_publishable_task(test_app, suffix="new")
        with test_app.session_factory() as db:
            db.get(PublishTask, old_id).created_at = utcnow() - timedelta(minutes=2)
            db.get(PublishTask, new_id).created_at = utcnow() - timedelta(minutes=1)
            old_account = db.query(PublishRecord).filter_by(task_id=old_id).first().account_id
            db.commit()

        assert ex._try_acquire_account_lock(old_account)  # 老任务账号锁被占→会 park
        try:
            wex._run_worker_iteration(test_app.session_factory())  # claim old→park→写冷却→释放认领

            with test_app.session_factory() as db:
                assert db.get(PublishTask, old_id).worker_id is None  # 认领已释放
            assert old_id in pc.active_parked_task_ids(_t.monotonic())  # 已进冷却

            skip = frozenset(pc.active_parked_task_ids(_t.monotonic()))
            with test_app.session_factory() as db:
                claimed = wex._claim_next_task(db, skip_task_ids=skip)
                assert claimed is not None and claimed.id == new_id  # 下一次选中 new
                wex._release_task_claim(db, new_id)
        finally:
            ex._release_account_lock(old_account)
            pc._parked_until.clear()
    finally:
        test_app.cleanup()
