# Worker 卡死发布线程拖垮问题修复 — 设计稿

- 日期：2026-07-14
- 状态：已确认设计（经一轮代码评审收敛：剔除原①闸槽归还），实现计划见 `docs/superpowers/plans/2026-07-14-worker-wedged-task-recovery.md`
- 范围：解冻单线程 worker（park/skip）+ 收 chrome 僵尸；**不含**重新回收卡死记录占用的闸槽/账号锁（需 SIGKILL 卡死子进程，另立"根因收割"spec）
- 关联代码：`server/app/modules/tasks/executor.py`、`server/worker/executor.py`、`docker-compose.yml`、`deploy/docker-compose.prod.yml`
- 历史坑位：#2（persistent profile 并发损坏防护）、#133（提交边界行锁死锁，已修）

## 1. 背景 / 故障现象

生产曾出现一次单次云盘 I/O 抖动（与 MinIO 掉盘同源）被"放大"成约 79 分钟的 worker 瘫痪：

- 一条头条发布记录的 headless-Chrome 发布线程卡死太深，`context.close()` 后连 Chromium 都无法在 10s 内退出。
- 现象：零发布进展、`<defunct>` chrome 僵尸累积（26→39 仍在涨）。
- 只有 `docker restart` worker 才恢复；外部 `recover_stuck_records` 复位了 DB 行但没解决卡死。

## 2. 根因（已对代码逐行核实）

### 2.1 真实机制

1. 卡死线程 10s 内杀不掉：`_handle_timed_out_record` 里 `future.result(timeout=_THREAD_TERMINATION_TIMEOUT=10.0)` 抛 `FutureTimeoutError` → `terminated=False`（`executor.py:596-599`）。
2. `terminated=False` 分支**故意保留** profile 锁避免 #2（两个 Chromium 开同一 persistent profile 损坏目录），同时账号锁 + 全局闸槽也**未归还**：`_retire_running_slot`（同时归还闸槽 + 账号锁的唯一函数）在超时处置路径里只由 `terminated=True` 分支（606 行）触达；`terminated=False` 时既不调它，记录又已在 `executor.py:318` 被 `running.pop` 移出（`finally` 330-331 遍历 `running.values()` 的 `_retire_running_slot` 也兜不到），故闸槽 + 账号锁一直被占。profile 锁是 DB 表 `browser_profile_locks`、有租约（`_record_execution_budget()+120s`，`executor.py:401`），会过期/可恢复对账。
3. 账号锁被占 → `_run_pending_records` 在"无 running + 剩余 pending 被 `_start_runnable_records` 的 `try_acquire` 挡住"时落到 `executor.py:302-309` 的 `sleep(0.2); continue` **无限空转**。
4. 单线程 worker 主循环阻塞在 `worker/executor.py:383` 的 `execute_task(...)` 调用里出不来 → 冻死；`execute_task` 的 `finally`（`executor.py:176-179`）到不了 → `_task_locks` 永不释放。
5. 恢复只能动 DB：`recover_stuck_records` / `recover_stuck_task_claims`（`service.py:365/414`）对 `_account_locks` / `_global_publish_gate` / `_task_locks` **零引用**，碰不到卡死 OS 线程、进程内锁、闸槽 → 只有重启能清。

### 2.2 早前分析写反的一环（本 spec 予以纠正）

早前根因分析称"卡死线程持记录行的 DB 行锁不提交 → 主循环 `db.commit()` 撞锁阻塞 → 心跳停"。**与代码相反**：

- `_make_commit_guard._mark_pending`（`executor.py:1134-1149`）写 `commit_attempted_at` 后**立即 `db.commit()` + `db.close()`**，独立 session 是短事务，不持行锁。
- `executor.py:264-268` 注释描述的 #133 死锁是**反方向、且已被 269 行每轮立即 commit 修好**的隐患。
- 真实 stall 是进程内空转热循环（0.2s 节流），**不是 db.commit() 阻塞**；且该热循环里 `_heartbeat_task_worker`（`executor.py:262`）照跑，**任务心跳没停、租约不过期**。

结论修正：病（一个卡死线程冻死整个单线程 worker、只能重启）为真；真正解冻靠"主循环有界化 + worker 跳过卡死任务先跑别的"（改动①）。至于卡死记录占用的闸槽/账号锁，本期**不回收**（见 2.4），交后续 SIGKILL 收割 spec。

### 2.3 ConflictError 在单 worker 生产下不成立

`_claim_next_task` 只认 `worker_id IS NULL` 的任务（`worker/executor.py:93`），单实例 worker 一旦冻在 383 行就回不到 claim、也不会自我重抢。首要症状是"单线程 worker 冻死"。

`ConflictError: already being executed`（`executor.py:126`）由两个并发的 `execute_task` 撞同一把 `_task_locks` 产生。**全仓 `execute_task(` 只有三个调用点**——`router.py:309` / `router.py:529`（均被 `_inline_execute_active()` 门控，`inline_execute_enabled` 默认 `False`、生产 no-op）与 `worker/executor.py:383`（单线程 worker）。故 CLAUDE.md 既定的"单 worker + 非 inline"生产配置下 `execute_task` 只有一个调用者，ConflictError 无法触发（早前分析里的"ConflictError 刷屏"在该配置下不成立）。**因此不含 ConflictError 退避改动**（原候选经核实为 YAGNI 剔除）。

### 2.4 为何本期不回收卡死记录占用的闸槽（评审收敛）

一版设计曾想在超时 `terminated=False` 分支归还全局闸槽以恢复吞吐，经评审否决——它会**破坏全局并发上限**且无法在本期安全实现：

- 每个 `execute_task` 拥有独立 `ThreadPoolExecutor(max_workers=5)`（`executor.py:256`），卡死线程仍占着其中一个 worker 线程 + 一个 Chrome。
- 归还闸槽后 `_start_runnable_records` 会按 `len(running)` 再提交记录，`started_monotonic` 从 submit 时刻起算（`executor.py:433-434`）：新记录可能只是排队却已开始 watchdog 计时、被误判超时；跨多次卡死会留下多个带卡死线程的旧线程池，**活 Chrome 数无上界**。
- 唯一安全的回收＝真去 kill 卡死的 chrome 子进程让线程解绕（那时 profile 锁 + 闸槽都能干净释放）。这属"根因收割"，本期不做。

因此本期**保持闸槽/账号锁被占**（= 当前行为，恰是 ≤`MAX_CONCURRENT_RECORDS` 硬上限的守卫），代价是每卡死一次永久少一格并发、需重启恢复满额。核心故障（79 分钟冻死）由改动① 独立解决。

## 3. 非目标（刻意排除）

- **回收卡死记录占用的闸槽/账号锁**（原①）——破坏并发上限，划入"根因收割"spec。
- SIGKILL 卡死的 chrome 子进程让线程解绕 / 释放 profile 锁 / 改 profile 租约语义。
- 恢复层对账进程内锁 / 闸槽。
- ConflictError 退避（单 worker 生产无法触发，见 2.3）。

## 4. 设计（2 个改动）

### 改动① 主循环有界化 + worker 级冷却跳过（解冻单线程 worker，命脉）

**a. `_run_pending_records` 判定 parked（`executor.py:302-309`）**

现有 `if not running:` 分支在"仍有 pending"时无限 `sleep(0.2); continue`。改为：连续 `PARK_STALL_THRESHOLD` 轮（默认 5 ×0.2s ≈ 1s）"无 running + 本轮零启动 + 仍有 pending"→ 判 **parked**，`db.commit()` 后 return，记录留 pending、**不聚合终态、不改 worker_id**。

- "本轮零启动"判据：进入 `if not running:` 分支即等价于本轮 `_start_runnable_records` 没起任何记录（起了则 `running` 非空）。
- 用 K 轮容忍窗而非首轮即 park：避免把正常的短暂账号锁争用误判；正常路径下同账号争用多半 `running` 非空、根本不进本分支，故 K 可取小值。
- 无需区分"泄漏账号锁"与"合法他方占用闸槽/profile 锁"：两种都用冷却重试兜住——泄漏的等重启，合法占用的等冷却后自然放行。

**b. parked 信号传给 worker（不加全局侧信道）**

- `_run_pending_records(db, task) -> bool` 返回是否 park。
- `execute_task(db, task) -> PublishTask` **签名不变**（现有 API/pipeline 调用方无需感知）；抽内部实现 `_execute_task_impl(db, task) -> tuple[PublishTask, bool]`，`execute_task` 丢掉 bool、新增 worker 专用 `execute_task_with_parked(db, task) -> tuple[PublishTask, bool]` 返回它。无模块级 side-channel（避免 inline/web 无消费者时无界泄漏 + 陈旧信号误消费）。

**c. worker 侧冷却 skip-map（`worker/executor.py`）**

- 冷却时长可配：`GEO_PUBLISH_PARK_COOLDOWN_SECONDS`（`settings.publish_park_cooldown_seconds`，默认 60.0，镜像现有 `publish_max_concurrent_records` 范式）。
- worker 维护内存 `_parked_until: dict[int, float]`（monotonic 到期）；`execute_task_with_parked` 返回 parked=True 时写入 `now + cooldown`。
- `_active_parked_task_ids(now)` 返回仍在冷却窗内的 task_id、顺带清理到期项；主循环把它传给 `_claim_next_task(db, skip_task_ids=...)`，在候选查询加 `.where(PublishTask.id.notin_(skip))`（非空才加），**跳过卡死任务、先跑下一个非 parked 的最旧任务**。
- `_claim_next_task` 排序加 `PublishTask.id.asc()` 次序（`created_at` 无小数秒、同秒会 tie），保证稳定 FIFO。
- 抽 `_run_worker_iteration(db)`（一轮主循环体：心跳→周期恢复→claim(带 skip)→`execute_task_with_parked`→按 parked 写冷却→释放认领），使 park→冷却→skip→下一任务整条链路可端到端测试。`_recovery_cycle` 上移为模块级。
- 冷却到期自动清理，卡死账号的积压 pending 记录在 worker 重启（进程内账号锁清空）后由既有 claim 流程自然重跑。

### 改动② 容器 `init: true` 收 chrome 僵尸（独立、低风险）

worker 是容器 PID 1，不回收 reparent 上来的 `<defunct>` chrome 子进程。给 `deploy/docker-compose.prod.yml:95`（exec-form array 命令，Python 已是直接子进程）与 `docker-compose.yml:64` 的 `worker` 服务加 `init: true`。

- 基础 `docker-compose.yml` 的 worker 命令是 `sh -c "alembic … && python …"`——配合 `init: true` 改成 `… && exec python -m server.worker.executor`，让 Python 成为 tini 的直接子进程、正确接收信号。
- `init: true` 只回收已 reparent 给 PID 1 的子进程，不保证回收仍挂在存活父进程下的全部 zombie。故"部署后 `<defunct>` 不再累积"是**验证目标**，不作必然保证。

## 5. 测试策略（TDD，先红后绿）

- 改动①b：单测 `_run_pending_records` 在"无 running + 有 pending + 账号锁被预占"下于有界轮数内返回 `True`（park，不挂）、记录仍 pending、账号锁未被释放；`execute_task_with_parked` 透出 `(task, True)`，`execute_task` 仍只返回 `PublishTask`。
- 改动①c：① 纯逻辑 `_active_parked_task_ids` 冷却过滤 + prune（collection-safe，函数内 lazy import worker.executor）；② mysql `_claim_next_task(db, skip_task_ids={old})` 跳过 parked、返回 new（显式设不同 `created_at` + id 次序防 flaky）；③ **端到端 glue**：`_run_worker_iteration` 一轮 park 后写冷却 + 释放认领，下一次 claim 跳过 parked 选中另一任务。
- 改动②：无单测，`docker compose config -q` 校验语法 + 部署后核对 `<defunct>` 不再累积（验证目标）。
- 涉及 DB 的用例标 `@pytest.mark.mysql`、需 `GEO_TEST_DATABASE_URL`；**worker.executor 顶层 import 会拉 `db.session`（非 collection-safe），测试一律函数内 lazy import**（见 gotcha：顶层 import session→collection 失败）。

## 6. 上线 / 回滚

- 非例行（改动 worker 并发/调度语义）——走正常 MR→CI（`backend-lint` + `frontend`），**部署前停下等确认**，不自动上线。
- 无 DB 迁移、无 base 镜像变化。
- 回滚：改动① 随镜像回滚；改动② 去掉 `init: true` / 还原 `exec` 即回滚。

## 7. 遗留 / 后续（"根因收割" spec）

- watchdog 真去 SIGKILL 卡死 chrome 子进程，使线程解绕、profile 锁 + 闸槽 + 账号锁都能干净释放——这才是原①"恢复满额并发"目标的安全实现。
- profile 租约到期而僵尸仍存活的 #2 残留风险，随根因收割一并处理。
