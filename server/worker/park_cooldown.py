"""worker park 冷却 skip-map。

被 execute_task_with_parked 判 parked 的任务 → 冷却到期（monotonic）；_claim_next_task 冷却窗内
跳过它，先跑下一个非 parked 的最旧任务。刻意与 worker.executor 分家、不 import db.session——
纯逻辑（无 DB）可直接 import 测试（见 gotcha：顶层 import session→collection/执行失败）。
"""

from __future__ import annotations

_parked_until: dict[int, float] = {}


def park_cooldown_seconds() -> float:
    from server.app.core.config import get_settings  # lazy：config 不牵连 db.session

    return float(get_settings().publish_park_cooldown_seconds)


def mark_parked(task_id: int, now: float) -> None:
    _parked_until[task_id] = now + park_cooldown_seconds()


def active_parked_task_ids(now: float) -> set[int]:
    """仍在冷却窗内的 parked task_id；顺带清掉已到期的条目。"""
    for tid in [tid for tid, until in _parked_until.items() if now >= until]:
        _parked_until.pop(tid, None)
    return {tid for tid, until in _parked_until.items() if now < until}
