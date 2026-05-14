ALL CLAUDE TASKS COMPLETE
Tasks: A1 A2 B1 B2 F3 F4

## What was done

- A1: Created `server/app/services/drivers/__init__.py` — PlatformDriver Protocol + register/get_driver/all_driver_codes registry
- A2: Created `server/app/services/drivers/toutiao.py` — ToutiaoDriver implementing PlatformDriver, all publish logic moved from ToutiaoPublisher as module-level functions, registered at import
- B1: Created `server/app/services/publish_runner.py` — generic run_publish() using driver registry + managed_remote_browser_session (no if/else)
- B2: Updated `server/app/services/tasks.py` — build_publisher_for_record → build_publish_runner_for_record, imports moved to drivers.toutiao. Updated all monkeypatches in test_tasks_api.py, test_concurrent_publish.py, test_tasks_state_machine.py, test_publish_validation.py, test_phase4.py, test_feishu.py
- F3: Created `server/tests/test_drivers.py` — 7 tests, all pass
- F4: Created `server/tests/test_publish_runner.py` — 2 tests, all pass

## Also fixed (coordination)

- `server/tests/test_delete_guards.py` — updated import get_or_create_toutiao_platform → get_or_create_platform (broken by Codex C1)
- `server/tests/test_toutiao_publisher_flow.py` — deleted (tests ToutiaoPublisher which is broken by Codex D1 removing remote_browser_enabled; ToutiaoDriver tests now in test_drivers.py)

## Test suite status

163 passed, 1 pre-existing failure (test_feishu.py::test_notify_not_called_when_url_not_set — existed before this refactoring)

## Ready for D4

D4 can now delete: launcher.py, geo.spec, server/app/services/browser.py, server/app/services/toutiao_publisher.py
(All their callers are now using the new driver/runner path)
