# 中等项修复计划（review.md 剩余 MEDIUM）

> 生成日期：2026-05-12
> 前置条件：12 条 HIGH 全部修复、11 条 MEDIUM 已修复、54/54 核查通过

---

## 总览

| 档位 | 数量 | 策略 |
|------|------|------|
| **A 档** — 可立即修复 | 5 | 单个文件改动，风险低，直接开工 |
| **B 档** — 需设计讨论 | 5 | 涉及架构决策，先讨论方案再动手 |
| **C 档** — 第三阶段更合适 | 5 | 改动量太大或与下阶段强耦合，建议后移 |

---

## A 档：可立即修复（5 条）

### A1. cancel_task 等待 futures 完成 (review 1.10)
- **文件**: `tasks.py` cancel_task 函数
- **问题**: 设置 `_task_cancel[id].set()` 后直接改 DB record 状态，不等待 ThreadPoolExecutor 中的线程结束
- **方案**: cancel_task 最后轮询 `time.sleep(0.1)` 等待 `get_task().status` 变为 terminal（最长 5s）
- **风险**: 低。cancel 本身概率就低

### A2. _run_pending_records 重复查询 (review 1.13)
- **文件**: `tasks.py` _run_pending_records 和 _start_runnable_records
- **问题**: 每轮循环 `list_task_records` 被调 3-4 次，每次都 SELECT
- **方案**: 在循环顶部缓存 records 列表，仅在 `db.commit()` 或新 record 变为 running 后刷新
- **风险**: 低。纯性能优化，不改逻辑

### A3. 剪贴板代码抽离 (review 2.4)
- **文件**: `toutiao_publisher.py` → 新建 `server/app/services/clipboard.py`
- **问题**: `_set_clipboard_files` + `_build_hdrop_payload` 共 102 行 Windows ctypes 代码混在 publisher 中
- **方案**: 抽到独立模块 `services/clipboard.py`，publisher 中 `from server.app.services.clipboard import set_clipboard_files`
- **风险**: 低。纯代码移动，不改逻辑

### A4. POST .../execute 更名 (review 4.1)
- **文件**: `routes/tasks.py` + 前端 `TasksWorkspace.tsx`
- **问题**: 端点名 "execute" 但行为是 fire-and-forget thread 启动，无队列。名称误导
- **方案**: 路由保持不变（避免前端改动），在 docstring 中说明语义。或者路由从 `/{task_id}/execute` 改为 `/{task_id}/start`
- **风险**: 低。如保持路由不动只改文档，零风险

### A5. 压缩多余的 /tasks/{id}/status endpoint (review 4.3)
- **文件**: `routes/tasks.py`
- **问题**: `/status` 返回 `id/status/lease_until`，全是 `TaskRead` 的子集但仍全量加载 records
- **方案**: 弃用该 endpoint，前端从未使用
- **风险**: 需确认前端从未调用。grep 一下即可

---

## B 档：需设计讨论（5 条）

### B1. idle_cleanup 线程启动位置 (review 1.12)
- **文件**: `browser_sessions.py:462` + `tests/utils.py`
- **问题**: `_start_idle_cleanup()` 在模块底部调用，测试不可控。死线程阻止重建
- **方案选项**:
  - A) 移到 `create_app()` 中显式调用 → 测试中不会启动（干净）
  - B) 保留模块级调用但在 `build_test_app` 中 stop/restart → 复杂度增加
- **推荐**: A，更清晰

### B2. 全局状态测试间隔离 (review 1.15)
- **文件**: `browser_sessions.py` 全局 dict + `tests/utils.py`
- **问题**: `_active_sessions`、`_record_to_session`、`_session_keep_alive` 在测试间泄漏
- **方案**: 在 `browser_sessions.py` 中加 `_reset_globals()` 函数，在 `build_test_app` 中调用
- **风险**: 低，纯测试基础设施

### B3. ContentWorkspace 进一步拆分 (review 1.8 剩余)
- **文件**: `ContentWorkspace.tsx` (935 行 → 目标 ~500)
- **问题**: 编辑器逻辑 + 列表 + 表单仍在一个文件
- **方案**: 下一步抽 `useArticleEditor()` hook（~200 行编辑器初始化 + 图片上传逻辑）
- **是否应该现在做**: 取决于第二个阶段完成度。如果编辑器行为稳定，可以推迟到第三阶段

### B4. 异常子类化 (review 3.1)
- **文件**: `errors.py` + `main.py` + 各 service 文件
- **问题**: 所有业务错误 `raise ValueError`，前端无法区分
- **方案**: 在 `errors.py` 中加 `ValidationError(ValueError)` → 400、`AccountError(ValueError)` → 400、`PublishError(Exception)` → 500 等子类，`main.py` 中注册对应 handler
- **风险**: 中。改动涉及多个文件，但测试覆盖会兜底

### B5. 请求体类型定义 (review 2.7)
- **文件**: `web/src/types.ts`（新增类型）+ 各 workspace
- **问题**: `api<T>` 只类型化响应，mutation 传匿名对象。后端改字段名 TS 不报错
- **方案**: 在 `types.ts` 中定义 `TaskCreatePayload`、`ArticleCreatePayload`、`AccountLoginPayload` 等，替换 mutation 中的匿名对象
- **风险**: 低。纯类型层面，不改运行时

---

## C 档：建议第三阶段（5 条）

### C1. idle_timeout → last_active_at (review 1.16)
- 需要前端加 heartbeat API 调用 `touch()`，服务端和前端各改动
- 当前 `started_at` 的 5 分钟超时够用，不是紧迫问题

### C2. Chromium 并发上限 semaphore (review 1.17)
- 需要设计浏览器池分配机制，和后续多用户场景高度耦合
- 放在第三阶段（MySQL 迁移 + 鉴权后）一起做更合理

### C3. CRUD 样板代码抽取 (review 2.2)
- 4 个 service 文件涉及 10+ 处重复模式
- 单一改动面大，且样板代码本身稳定，不值得单独为一轮重构

### C4. 序列化层抽离 (review 2.3)
- `to_*_read` 函数分散在 4 个文件
- 纯代码组织优化，不影响功能

### C5. 竞争测试补全 (review 5.3 + 5.4)
- execute+cancel、retry+execute、manual_confirm 竞争测试
- 耗时高、回报低（已有 89 个测试覆盖核心路径）

---

## 建议执行顺序

```
第一天（今天）: A 档 5 条 → 快速收尾
第二天        : B 档 5 条 → 讨论后选择性修复
第三阶段      : C 档 5 条 → 随 MySQL/鉴权重构一起做
```
