# Worker 卡死发布线程拖垮问题修复 — 设计稿

- 日期：2026-07-14
- 状态：已确认设计，待出实现计划
- 范围：核心 #2 安全修（不含根因收割 = SIGKILL 卡死子进程，另立 spec）
- 关联代码：`server/app/modules/tasks/executor.py`、`server/worker/executor.py`、`deploy/docker-compose.prod.yml`
- 历史坑位：#2（persistent profile 并发损坏防护）、#133（提交边界行锁死锁，已修）

## 1. 背景 / 故障现象

生产曾出现一次单次云盘 I/O 抖动（与 MinIO 掉盘同源）被"放大"成约 79 分钟的 worker 瘫痪：

- 一条头条发布记录的 headless-Chrome 发布线程卡死太深，`context.close()` 后连 Chromium 都无法在 10s 内退出。
- 现象：零发布进展、`ConflictError: already being executed` 长时间刷屏、`<defunct>` chrome 僵尸累积（26→39 仍在涨）。
- 只有 `docker restart` worker 才恢复；外部 `recover_stuck_records` 复位了 DB 行但没解决卡死。

## 2. 根因（已对代码逐行核实）

### 2.1 真实机制

1. 卡死线程 10s 内杀不掉：`_handle_timed_out_record` 里 `future.result(timeout=_THREAD_TERMINATION_TIMEOUT=10.0)` 抛 `FutureTimeoutError` → `terminated=False`（`executor.py:596-599`）。
2. `terminated=False` 分支**故意保留** profile 锁避免 #2（两个 Chromium 开同一 persistent profile 损坏目录），但同时**泄漏了两把纯进程内资源**：
   - 全局闸槽 `_global_publish_gate`（`MAX_CONCURRENT_RECORDS=5`）与账号锁 `_account_locks`（进程内 `threading.Lock`）——`_retire_running_slot` 是同时归还这两者的唯一函数，但在超时处置路径里只由 `terminated=True` 分支（606 行）触达；`terminated=False` 时既不调它，记录又已在 `executor.py:318` 被 `running.pop` 移出（`finally` 330-331 遍历 `running.values()` 的 `_retire_running_slot` 也兜不到），故闸槽 + 账号锁**永不归还**。
   - profile 锁是 DB 表 `browser_profile_locks`、有租约（`_record_execution_budget()+120s`，`executor.py:401`），会过期/可恢复对账，且是 #2 正主，**该留**。
3. 闸槽泄漏 → 全局并发永久少一格。账号锁泄漏 → `_run_pending_records` 在"无 running + 剩余 pending 被 `_start_runnable_records` 的 `try_acquire` 挡住"时落到 `executor.py:302-309` 的 `sleep(0.2); continue` **无限空转**。
4. 单线程 worker 主循环阻塞在 `worker/executor.py:383` 的 `execute_task(...)` 调用里出不来 → 冻死；`execute_task` 的 `finally`（`executor.py:176-179`）到不了 → `_task_locks` 永不释放。
5. 恢复只能动 DB：`recover_stuck_records` / `recover_stuck_task_claims`（`service.py:365/414`）对 `_account_locks` / `_global_publish_gate` / `_task_locks` **零引用**，碰不到卡死 OS 线程、进程内锁、闸槽 → 只有重启能清。

### 2.2 早前分析写反的一环（本 spec 予以纠正）

早前根因分析称"卡死线程持记录行的 DB 行锁不提交 → 主循环 `db.commit()` 撞锁阻塞 → 心跳停"。**与代码相反**：

- `_make_commit_guard._mark_pending`（`executor.py:1134-1149`）写 `commit_attempted_at` 后**立即 `db.commit()` + `db.close()`**，独立 session 是短事务，不持行锁。
- `executor.py:264-268` 注释描述的 #133 死锁是**反方向、且已被 269 行每轮立即 commit 修好**的隐患。
- 真实 stall 是进程内空转热循环（0.2s 节流），**不是 db.commit() 阻塞**；且该热循环里 `_heartbeat_task_worker`（`executor.py:262`）照跑，**任务心跳没停、租约不过期**。

结论修正：病（一个卡死线程拖垮整个 worker、只能重启）为真；药（隔离泄漏的进程内闸槽/账号锁）对症；但"DB 行锁阻塞主循环 commit / 心跳停"是错因，本修复不针对它。

### 2.3 ConflictError 在单 worker 生产下不成立

`_claim_next_task` 只认 `worker_id IS NULL` 的任务（`worker/executor.py:93`），单实例 worker 一旦冻在 383 行就回不到 claim、也不会自我重抢。首要症状是"单线程 worker 冻死"。

关于 `ConflictError: already being executed`（`executor.py:126`）：它由两个并发的 `execute_task` 撞同一把 `_task_locks` 产生。**全仓 `execute_task(` 只有三个调用点**——`router.py:309` / `router.py:529`（均被 `_inline_execute_active()` 门控，`inline_execute_enabled` 默认 `False`、生产 no-op）与 `worker/executor.py:383`（单线程 worker）。故在 CLAUDE.md 既定的"单 worker + 非 inline"生产配置下，`execute_task` 只有一个调用者，ConflictError 无法触发。早前分析里的"ConflictError 刷屏"在该配置下不成立（要么当时跑了 inline-execute / 多 worker，要么是别的错误被并入统计）。**因此本 spec 不含 ConflictError 退避改动**（原候选 ④ 经核实为 YAGNI 剔除）；若将来真启用 inline/多 worker 再单立。

## 3. 非目标（刻意排除，划入后续"根因收割" spec）

- SIGKILL 卡死的 chrome 子进程让线程解绕。
- 释放 profile 锁 / 改 profile 租约语义。
- 恢复层对账进程内锁 / 闸槽。
- profile 租约到期时若僵尸仍存活的 #2 残留风险（既有隐患，不在本次扩大或收敛）。

## 4. 设计（3 个改动）

### 改动 ① 归还泄漏的全局闸槽（最高收益、零 #2 风险）

`_handle_timed_out_record` 的 `terminated=False` 分支（`executor.py:604-613`）在标僵尸 + 告警的同时，**只**调 `_global_publish_gate.release()`，账号锁与 profile 锁不动。

- 理由：闸槽是"全局并发计数"，与 persistent profile 安全（#2）无关；留着它每卡死一次永久扣一格并发。
- 归还后其它账号/任务立刻恢复吞吐。
- 注意：只释放一次（该 RunningRecord 恰好持一格，`gate_transferred=True`）；ObservableGate over-release 会抛 ValueError，实现里吞掉并告警（同 `_retire_running_slot` 防御姿态）。进程重启后为全新 gate、无跨进程双释放。

### 改动 ② 主循环有界化 + worker 级冷却跳过（解冻单线程 worker，命脉）

**a. `_run_pending_records` 判定 parked（`executor.py:302-309`）**

现有 `if not running:` 分支在"仍有 pending"时无限 `sleep(0.2); continue`。改为：连续 K 轮（建议 3-5 轮 ×0.2s ≈ 0.6-1s）"无 running + 本轮零启动 + 仍有 pending"→ 判 **parked**，`db.commit()` 后 return，记录留 pending、**不聚合终态、不改 worker_id**。

- "本轮零启动"判据：调用 `_start_runnable_records` 后 `running` 仍空即等价于零启动（若启动成功 `running` 非空，就不会进本分支）。
- 用 K 轮容忍窗而非首轮即 park：避免把正常的短暂账号锁争用（另一记录刚要退场）误判为泄漏；正常路径下同账号争用多半 `running` 非空、根本不进本分支，故 K 可取小值。
- 无需区分"泄漏账号锁"与"合法他方占用 profile 锁"：两种都用 60s 冷却重试兜住——泄漏的等重启，合法占用的等冷却后自然放行。

**b. execute_task → worker 传递 parked 信号**

- 契约：`_run_pending_records` 把 parked 决定上报给 `execute_task`；`execute_task` 对现有 API/pipeline 调用方保持返回 `PublishTask` 不变（向后兼容），额外通过 `tasks/executor.py` 模块级登记 `task_id→parked_until`（或等价 side-channel）供 worker 读取。实现细节留给实现计划，约束是"不破坏现有 execute_task 调用方签名"。

**c. worker 侧冷却 skip-map（`worker/executor.py`）**

- worker 维护内存 `_parked_until: dict[int, float]`；`execute_task` 返回后若该 task 被 park，写入 `now + PARK_COOLDOWN_SECONDS`（建议 60s，可配）。
- 每轮计算 `active_parked = {id for id, until in _parked_until.items() if now < until}`，传入 `_claim_next_task`，在候选查询上加 `.where(PublishTask.id.notin_(active_parked))`，从而**跳过卡死任务、先跑下一个非 parked 的最旧任务**（避免 `order_by(created_at.asc())` 永远先抢卡死老任务）。
- 冷却到期自动清理 map 中过期项，卡死账号的积压 pending 记录在 worker 重启（进程内账号锁清空）后由既有 claim 流程自然重跑。

### 改动 ③ 容器 `init: true` 收 chrome 僵尸（独立、低风险）

worker 是容器 PID 1，不回收 reparent 上来的 `<defunct>` chrome 子进程。给 `deploy/docker-compose.prod.yml:95` 的 `worker` 服务加 `init: true`（tini 当 PID 1 回收僵尸）；基础 `docker-compose.yml` 的 worker 一致化。纯运维改动，无单测。

> 已剔除的候选 ④（ConflictError 退避）：经 2.3 核实，单 worker + 非 inline 生产配置下 `execute_task` 只有一个调用者、ConflictError 无法触发，YAGNI 剔除。

## 5. 测试策略（TDD，先红后绿）

- 改动 ①：复用 `server/tests/test_publish_timeout_lock_safety.py` 的注入式假卡死 future（`result_timeout=0.05`）——断言超时 `terminated=False` 后全局闸槽计数回到满、账号锁与 profile 锁仍持有。
- 改动 ②a：单测 `_run_pending_records` 在"无 running + 零启动 + 有 pending"下于有界轮数内返回并登记 parked，且账号锁未被释放。
- 改动 ②c：单测 worker skip-map — `_claim_next_task` 在冷却窗内跳过 parked task、选下一个非 parked；冷却到期后可再次被 claim。
- 改动 ③：无单测，部署后核对 `<defunct>` 不再累积。
- 全套需 `GEO_TEST_DATABASE_URL`（MySQL）；涉及 DB 的用例带 `@pytest.mark.mysql`。

## 6. 上线 / 回滚

- 非例行（改动 worker 并发/调度语义）——走正常 MR→CI（`backend-lint` + `frontend`），**部署前停下等确认**，不自动上线。
- 无 DB 迁移、无 base 镜像变化。
- 回滚：①② 随镜像回滚；③ 去掉 `init: true` 即回滚。

## 7. 遗留 / 后续

- "根因收割" spec：watchdog 真去 SIGKILL 卡死 chrome 子进程，使线程解绕、profile 锁干净释放（需子进程追踪 + #2 安全性论证）。
- profile 租约到期而僵尸仍存活的 #2 残留风险，随根因收割一并处理。
