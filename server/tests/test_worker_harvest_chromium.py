"""根因收割：chromium OS 级收割器 + 配置开关（纯逻辑、无 DB、collection-safe）。"""

from __future__ import annotations

from pathlib import Path

from server.app.core.config import Settings


def test_harvest_settings_defaults():
    fields = Settings.model_fields
    assert fields["publish_harvest_enabled"].default is True
    assert fields["publish_harvest_rejoin_seconds"].default == 5.0


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
