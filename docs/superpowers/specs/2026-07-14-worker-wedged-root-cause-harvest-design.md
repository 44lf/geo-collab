# Worker 卡死发布线程「根因收割」— 设计稿

- 日期：2026-07-14
- 状态：已确认设计（brainstorm 通过），实现计划待出（TDD）
- 范围：超时后**真去 SIGKILL 卡死的 chromium 子进程**，确认发布线程解绕后**干净归还全局闸槽 + 账号锁 + profile 锁**，恢复满额并发、无需重启 worker。
- 关联代码：`server/app/modules/tasks/executor.py`（`_handle_timed_out_record`、`_retire_running_slot`）、`server/app/modules/accounts/browser.py`（会话进程台账 / 泄漏对账）、`server/app/core/config.py`（新开关）。
- 基线：`origin/main @ 25061fe`（含已上线的 park/skip 解冻修复 = server-1.0.6）。
- 前置 spec：`docs/superpowers/specs/2026-07-14-worker-wedged-task-recovery-design.md`（本 spec 兑现它第 7 节「遗留 / 后续（根因收割）」）。
- 历史坑位：#2（persistent profile 并发损坏防护）、#3（SIGKILL 后仍不退的会话号段泄漏台账）、#133（提交边界行锁，已修）。

## 1. 背景 / 为什么还要这一期

上一期（server-1.0.6）已让**单条卡死线程不再拖垮整个单线程 worker**：主循环有界 park + 冷却 skip 让 worker 绕开卡死任务继续干活，`init: true` 让 tini 回收 `<defunct>` chrome 僵尸。但它**刻意没做**卡死记录占用资源的回收——`_handle_timed_out_record` 在发布线程 10s 内没确认终止（`terminated=False`）时，**全局闸槽 + 账号锁 + profile 锁一律不放**（`executor.py:634-640`），代价是：

- 每卡死一次，全局并发上限（`MAX_CONCURRENT_RECORDS=5`）**永久少一格**；卡死账号在 worker 重启前发不出。
- 只有 `docker restart` worker 能恢复满额（重启清空进程内闸槽/账号锁 + 硬杀所有 chromium）。

上一期评审**否决了盲目「归还闸槽」**：卡死的 OS 线程仍活着、仍占着 `ThreadPoolExecutor` 的一个 worker 线程 + 一个 Chrome，此时还槽会破坏 ≤5 硬上限、留下无上界的活 Chrome。评审的结论是：**唯一安全的回收 = 真去 kill 卡死的 chromium 让线程解绕**——那时闸槽/账号锁/profile 锁都能干净释放。本 spec 就做这件事。

## 2. 根因回顾（精确到集成点）

`_handle_timed_out_record`（`executor.py:583`）现有流程：

1. 标记记录 failed（跨提交点则标 `commit_uncertain` 待人工核对）。
2. `_close_record_browser(record_id)`（`executor.py:614` → `531`）：调 `stop_remote_browser_session(session.id)`，其内部 `_close_browser_handles` 做 `context.close()` + `playwright.stop()`。**注释意图是「关 Chromium context 让 Playwright 线程收 TargetClosedError 终止」，但这是 Playwright 级 graceful close，对卡死的 chromium 不可靠**——它跑在 watchdog 线程、而 context 由卡死的发布线程持有（`context_thread_id` 不同，Playwright sync API 是 thread-local greenlet），graceful close 要么跨线程即抛被 try/except 吞掉、要么卡在已死的 transport 上，**都杀不动卡死的 chromium**。
3. `future.result(timeout=10s)`（`executor.py:623`）：卡死线程没解绕 → `FutureTimeoutError` → `terminated=False`。
4. `terminated=False` 分支（`634-640`）：标僵尸 + 告警，**三锁全保留**，`return False`。

> 关键洞察：卡死的 chromium 从没被 **OS 级强杀**过。`session.processes` 里只有 Xvfb / x11vnc / websockify（我们自己 `subprocess.Popen` 起的，`_stop_session_processes` 已能 terminate→SIGKILL），**chromium 是 Playwright 起的、不在其中**，只被上面那步不可靠的 graceful close「请求」关闭。**这一层缺失的 OS SIGKILL 就是本 spec 要补的**。

## 3. 设计

### 3.1 选型（brainstorm 定论）

| | 方案 | 定位并杀 chromium 的方式 | 结论 |
|---|---|---|---|
| **A ✅** | profile 维度 OS 收割 + 复用泄漏台账 | watchdog 扫 `/proc/*/cmdline` 找 `--user-data-dir=<本条 profile_dir>` 的 chromium 进程树 → SIGKILL；只回收 chromium，让解绕后的卡死线程 `finally` 自清残余 | 面最小、profile 精准（#2 安全）、复用 #3 台账；纯 OS 不碰 Playwright 句柄；容器即 Linux，`/proc` 现成、无新依赖 |
| B | watchdog 主动全会话拆除 | 直接 OS 杀 chromium+Xvfb+vnc，不等线程自清 | 和线程 `finally` 抢拆、双拆竞态；收益不抵风险。否决 |
| C | 启动期钻 Playwright 内部记 chromium PID | 按记录的 PID 杀 | 耦合 Playwright 私有实现、跨版本脆 + 改启动链路。否决 |

采用 **A**。

### 3.2 新增：纯 OS 收割器（`accounts/browser.py`）

```
harvest_chromium_by_profile(profile_dir: Path) -> HarvestResult
```

- 扫 `/proc/*/cmdline`，匹配 cmdline 含 `--user-data-dir=<profile_dir>`（renderer / gpu / zygote 子进程也继承该参数，故整棵树都命中）的进程 → `os.kill(pid, SIGKILL)`。
- 短等（poll `/proc/<pid>` 消失）确认；返回 `HarvestResult(killed: int, survived: list[pid])`。
- **绝不调** `_close_browser_handles` / `stop_remote_browser_session` / 任何 `context.*` / `playwright.*`——那些是卡死线程 greenlet 专属，跨线程碰即坏。收割器**纯 OS 系统调用**、greenlet 无关，可安全从 watchdog 线程跑。
- `survived` 非空（极少：SIGKILL 都杀不死）→ 交既有泄漏台账 `_register_leaked_session` 风格记账 + `emit_resource_alert`，`reconcile_leaked_sessions` 下轮重试；**此时视同「未确认死」，不归还任何锁**。
- 平台守卫：`/proc` 不存在（非 Linux，如 Windows 本地）时 no-op 返回 `killed=0`——发布本就只在容器（Linux）跑，本地不触发。

`profile_dir` 解析：与 `runner.py` 一致，由账号 `state_path` 经 `profile_dir_from_state_path` 得出；`RunningRecord` 已带 `account_id`（必要时在 submit 时把 `profile_dir` 一并 stash 到 `RunningRecord`，实现计划定）。

### 3.3 改造：`_handle_timed_out_record` 的 `terminated=False` 分支

仅当 `settings.publish_harvest_enabled`（默认 `True`）时，在标僵尸/告警**之前**插入 OS 收割升级：

```
else:  # future.result 超时，Playwright 级 close 没解绕线程
    if settings.publish_harvest_enabled:
        result = harvest_chromium_by_profile(profile_dir_for(running_record))  # OS SIGKILL，精准到本 profile
        if not result.survived:
            try:
                future.result(timeout=settings.publish_harvest_rejoin_seconds)  # 默认 5s，给线程解绕
                terminated = True
            except FutureTimeoutError:
                terminated = False   # chromium 死了但线程仍卡在别处（非 chromium 的系统调用）
            except Exception:
                terminated = True
        if terminated:
            _release_record_profile_lock(running_record.record_id)  # chromium 确认死 → #2 安全
            _retire_running_slot(running_record)                    # 归还闸槽 + 账号锁 → 满额并发恢复
            return True
    # 收割未开 / chromium 杀不死 / 线程仍卡 → 退回今天行为（严格不劣于现状）
    _mark_record_zombie(db, task_id, running_record.record_id)
    emit_resource_alert(...)  # 现有告警
return terminated
```

### 3.4 铁律（本设计的安全命脉，与被否的「盲目还槽」的分界）

**只有第二次 `future.result` 确认线程死透，才归还闸槽/账号锁/profile 锁——绝不在活线程上还任何锁。**

- 杀 chromium 是**手段**，`future` 完成是**证据**；两者缺一不还锁。
- chromium 被杀但线程仍卡（卡在非 chromium 的系统调用，如无关磁盘 I/O）→ 保锁、退回今天行为。**最坏情况 = 今天的行为 + 一条台账/告警，绝不劣化。**
- 复用现有 `terminated=True` 收尾（`_release_record_profile_lock` + `_retire_running_slot`），`_retire_running_slot` 的**单次释放语义**（`ObservableGate` over-release 会抛 `ValueError`、被吞并告警，`executor.py:498-505`）原样沿用，不引入 double-release。
- **无 double-retire 之忧**（已核控制流）：调用点 `executor.py:344-350` 对超时记录先 `running.pop(future)`（345）移出、再把归还全权委托给 helper（348 后仅 `db.commit(); continue`，**不**调 `_retire_running_slot`）；`finally`（357-358）只遍历仍在 `running` 里的记录，够不到已 pop 的超时记录。故超时记录的闸槽/账号锁**有且仅有** helper 内归还一次——harvest 的 `terminated=True` 收尾与现有路径同源，天然单次。

### 3.5 #2 安全性与爆炸半径

- profile 锁只在 chromium **确认死透**后释放：此刻该 persistent profile 上无活 chromium，下一条同账号记录再开 Chromium 不会与谁并发损坏目录——恰是 #2 要防的场景已消除。
- SIGKILL 单条 profile 的 chromium，**爆炸半径 ≤ 今天 `docker restart` 的硬杀**（restart 把所有 chromium 一起硬杀）；profile 脏页由 chrome 自身崩溃恢复兜底（与 restart 后重开完全同类）。收割精准到一条，比 restart 更温柔。

### 3.6 开关 / 配置（`core/config.py`）

- `GEO_PUBLISH_HARVEST_ENABLED`（`settings.publish_harvest_enabled`，默认 `True`）：运行时一键关 → 逐字退回今天的 `terminated=False` 分支。
- `GEO_PUBLISH_HARVEST_REJOIN_SECONDS`（`settings.publish_harvest_rejoin_seconds`，默认 `5.0`）：OS 杀后等线程解绕的二次 join 超时。

## 4. 非目标（刻意排除）

- 改 profile 锁租约语义 / 恢复层跨进程对账进程内闸槽（进程内锁天然随重启清空，无需对账）。
- 修 `_close_record_browser` 现有的跨线程 graceful close（既有行为，本 spec 不依赖它、也不扩大它；harvest 是其后的 OS 兜底，不新增任何跨线程 Playwright 调用）。
- 重构 payload / 发布线程模型（另有 detached-ORM 重构话题，与本期无关）。
- 非 Linux 平台真实收割（发布只在容器跑；本地 `/proc` 缺失时 no-op）。

## 5. 测试策略（TDD，先红后绿）

- **收割器 `harvest_chromium_by_profile`**：注入假 `/proc` 表 + 假 `os.kill`（依赖注入或 monkeypatch），断言：① 只命中含本 `--user-data-dir` 的进程树、不误伤别 profile；② 全杀成功 → `survived=[]`；③ 有进程 SIGKILL 不死 → 进 `survived`；④ 无 `/proc`（非 Linux）→ no-op `killed=0`。纯逻辑、无需 DB、collection-safe。
- **`_handle_timed_out_record` 升级路径**：复用 `server/tests/test_publish_timeout_lock_safety.py` 的注入式假卡死 future（`result_timeout` 极小）+ 假收割器，断言：
  - 3a（收割成功 + 二次 join 完成）→ 全局闸槽计数**回到满额** + 账号锁释放 + profile 锁释放 + `return True`；
  - 3b（收割后线程仍卡 / chromium survived）→ 三锁**全保留** + 标僵尸 + 告警 + `return False`，且**无 gate over-release**（`ObservableGate` 计数不为负、不抛未捕获 `ValueError`）；
  - 开关关（`publish_harvest_enabled=False`）→ **逐字退回**旧分支（三锁保留、不调收割器）。
- **真 chromium 端到端**：仅容器（Linux）；Windows 本地起不来（无 Xvfb/Chromium，与发布同限）。部署后核对：一次超时后闸槽计数回满、卡死账号能再发、无 `<defunct>` 堆积。
- DB 相关用例标 `@pytest.mark.mysql`、需 `GEO_TEST_DATABASE_URL`；**`worker.executor` / `tasks.executor` 顶层 import 会拉 `db.session`（非 collection-safe），测试一律函数内 lazy import**（既有 gotcha）。

## 6. 上线 / 回滚

- 无 DB 迁移、无 base 镜像变化。
- **非例行**（改并发恢复语义）→ 走正常 MR→CI（`backend-lint` + `frontend`），**部署前停下等确认**，不自动上线。
- 上线姿态：`GEO_PUBLISH_HARVEST_ENABLED` **默认开**、随镜像第一次部署即生效；出问题运行时改环境变量一键关（配合 `docker compose up -d --no-deps worker` 重建 worker 生效——注意 compose env 冻结，`restart` 不重读，既有 gotcha）。
- 回滚：关开关（运行时）或镜像回滚。上一期的 `init: true` 已在生产，本期不动。

## 7. 观测 / 后续

- 建议给「收割成功恢复满额」与「收割后仍卡（survived / 二次 join 超时）」各打一条可聚合日志/metric，让「根因收割生效率」可观测（承接上一期「parked task 提升为 metric/alert」的建议）。
- 残留 #2 风险（profile 租约到期而僵尸仍存活）在本期由「确认死透才放 profile 锁」进一步收敛；剩余极端态（SIGKILL 都杀不死）走泄漏台账 reconcile，不在本期强行消除。
