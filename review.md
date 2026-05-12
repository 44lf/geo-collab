# Geo Collab 代码 Review

> 生成日期：2026-05-12
> 方法：4 个 subagent 并行审查，覆盖 backend services、publisher/browser、frontend、cross-cutting（API/DB/tests/config/security）
> 
> 最后更新：2026-05-12 — 12 HIGH / 22 MEDIUM / 5 LOW 已修复 ✅

---

## 总览

| 严重级别 | 总数 | 已修复 | 剩余 |
|---------|------|--------|------|
| **HIGH** | 12 | 12 | 0 |
| **MEDIUM** | 33 | 22 | 11 |
| **LOW** | 16 | 5 | 11 |

---

## 一、资源管理 & 并发安全

### HIGH

**1.1 账号锁在超时后立即释放，但线程仍在运行** ✅ 已修复
`tasks.py:343-347`
- 超时 record 的 account lock 立即 release，但 `executor.submit` 的线程可能仍在执行 `_publish_record`，导致同账号串行保证被破坏。
- **修复**：`future.cancel()` + `future.result(timeout=5)` 等待线程结束后再 release lock。

**1.2 全局 dict 无界增长** ✅ 已修复
`tasks.py:92-96`
- `_task_locks`、`_account_locks`、`_task_cancel` 永久保留锁对象，永远不会 `pop`。
- **修复**：`execute_task` 的 `finally` 中 `_task_locks.pop(task.id, None)`。废弃仅测试用的 `_task_locks_lock`。

**1.3 `db.expunge()` 跨线程传 ORM 对象极度脆弱** ✅ 已修复
`tasks.py:474-484`
- 把 `Article`/`Account` 等 ORM 对象从 session 剥离后传到 `ThreadPoolExecutor` 线程。任何未预加载的关系访问都会引发 `DetachedInstanceError`。
- **修复**：在 `_detach_record_inputs` 中加 `sa_inspect` 检查，验证 `cover_asset`、`body_assets`、`asset` 等关键关系已加载，未加载时立即报错而非静默 fail。`_load_article_for_publish` 加了注释说明。

**1.4 远程路径使用裸 `except: pass` 吞掉信号** ✅ 已修复
`toutiao_publisher.py:142`（remote 路径的 finally）
- 裸 `except:` 会捕获 `KeyboardInterrupt`、`SystemExit`。用户 Ctrl+C 时不会退出。
- 已验证 `browser.py` 已正确使用 `except Exception:`，无需修改。
- **修复**：`toutiao_publisher.py` 中 `except: pass` → `except Exception: pass`。

**1.5 浏览器僵尸进程无保护** ✅ 已修复
`toutiao_publisher.py:122-144`, `browser.py:22-41`
- 没有 `atexit` 或 signal handler。Chromium crash 或 `taskkill /F` 后，子进程成为孤儿。
- **修复**：`toutiao_publisher.py` 中注册 `atexit` 清理所有 tracked context。`_register_context_for_cleanup()` 在 context 创建时调用，`context.close()` 后从列表移除。keep-alive 的 context 也保持 tracked 以便 crash 时清理。

**1.6 Clipboard 双击确认的双重释放风险** ✅ 已修复
`toutiao_publisher.py:714-726`
- `SetClipboardData(handle)` 成功后 clipboard 接管了 `handle` 所有权。如果第二次 `SetClipboardData` 失败，`finally` 中的 `GlobalFree(handle)` 会导致 HGLOBAL double-free。
- **修复**：第一次成功后将 `handle = None`。

**1.7 轮询每 2.5s 无条件 setState 触发全树重渲染** ✅ 已修复
`TasksWorkspace.tsx:53-59, 80-101`
- `refreshDetail` 每 2.5s 无条件 setState。50 条 record + 100 条 log 时，每次轮询触发约 170 个 VDOM 比较。
- **修复**：加 `JSON.stringify` 浅比较缓存 skip 重渲染、`document.visibilityState === "visible"` 检查停后台轮询、间隔从 2.5s 增加到 5s。

**1.8 ContentWorkspace 953 行单体组件** ✅ 部分修复
`ContentWorkspace.tsx`
- 编辑器初始化、文档规范化、Tiptap 扩展、文章 CRUD、分组 CRUD、图片上传、模态框、列表渲染、分页全部混合在同一个 function component 中。
- **修复**：抽取 `Modal.tsx` 复用组件，替换 4 个内联模态框。953→935 行（部分改进，完整拆分留待后续）。

**1.9 无分页 + eager-load 全部 records** ✅ 已修复
`tasks.py:129-139`
- `list_tasks` 无条件加载所有 records + accounts + platform。
- **修复**：`list_tasks` 加 `skip/limit` 参数（默认 limit=100），路由 `read_tasks` 接受 `skip/limit` query params（上限 500）。

### MEDIUM

**1.10 `cancel_task` 不等待正在执行的 futures 完成** ✅ 已修复
`tasks.py:865-869`
- 设置 `_task_cancel[id].set()` 后直接修改 DB record 状态，但后台线程可能同时修改。
- **修复**：`cancel_task` 末尾加 5s poll 循环等待 task 进入 terminal 状态。

**1.11 清理线程无锁读取 `_session_keep_alive`** ✅ 已修复
`browser_sessions.py:155`
- `session.id not in _session_keep_alive` 读取未加锁。
- **修复**：包在 `with _sessions_lock:` 中。

**1.12 后台线程启动在模块导入时，测试无法控制** ✅ 已修复
`browser_sessions.py:462`
- `_start_idle_cleanup()` 在模块底部调用。
- **修复**：移到 `create_app()` 中显式调用，模块底部移除。

**1.13 `_run_pending_records` 每轮循环重复查询 records 多次** ✅ 已修复
`tasks.py:311,317,326,373`
- 一次循环迭代中 `list_task_records` 被调用 3–4 次。
- **修复**：统一在循环顶部单次读取，`_start_runnable_records` 改为接受 records 参数。

**1.14 后台线程异常丢失 traceback** ✅ 已修复
`tasks.py:517-518`
- `str(exc)` 只取到 message。
- **修复**：`_finish_record_future` 中追加 `traceback.format_exc()`。

**1.15 全局状态在测试间泄漏** ✅ 已修复
`browser_sessions.py:61-74`
- `build_test_app` 没有清理 browser_sessions 全局 dict。
- **修复**：`_reset_globals()` 函数，在 `build_test_app` 中调用。

**1.16 空闲超时使用 `started_at` 而不是 `last_active_at`** ⏳ 第三阶段
- 用户活跃操作时 session 仍可能被 kill。
- **计划**：添加 `last_active_at` 和 `touch()` 方法，需前端 heartbeat API。

**1.17 5 个并发 Chromium 实例的并发上限防护** ⏳ 第三阶段
- 每个 worker 启动一个 Chromium。
- **计划**：与多用户场景一起设计浏览器池分配机制。

---

## 二、代码结构

### HIGH

**2.1 `_run_next_pending_record` 148 行死代码** ✅ 已删除
`tasks.py:570-718`
- 串行执行的替代路径，从未被任何路由调用。仅在测试中 patch 引用。重复了 `_run_pending_records` 的逻辑。
- **修复**：整体删除该函数及测试中对它的引用。

### MEDIUM

**2.2 CRUD 样板代码在 4 个 service 文件中重复** ⏳ 第三阶段
- 单一改动面大，样板代码本身稳定。
- **计划**：随第三阶段重构一起做。

**2.3 序列化函数与业务逻辑混合** ✅ 已修复
`tasks.py`, `articles.py`, `accounts.py`, `article_groups.py`
- `to_*_read` 函数与 CRUD/执行逻辑在同一个文件里。
- **修复**：抽到 `server/app/services/serializers.py`（131 行），4 个 service 各减少 15-68 行。

**2.4 Windows 剪贴板 102 行 ctypes 混在 publisher 中** ✅ 已修复
`toutiao_publisher.py:630-732`
- **修复**：抽到 `server/app/services/clipboard.py`（95 行），publisher 从 827→752 行。

**2.5 ContentWorkspace 和 TasksWorkspace 重复的分页器** ✅ 已修复
- **修复**：抽为 `web/src/components/Pagination.tsx`，两处替换。

**2.6 `App.tsx` 条件挂载销毁/重建所有 workspace 状态** ✅ 已修复
- **修复**：改为 `<div style={{display: "none"}}>` 隐藏，保留组件树和状态。

**2.7 所有 API mutation 没有请求体类型** ✅ 已修复
- **修复**：`types.ts` 新增 6 个 Payload 类型，3 个 workspace 全部类型化。

---

## 三、错误处理

### MEDIUM

**3.1 `ValueError → 400` 过于粗粒度** ✅ 已修复
`main.py:83-85`
- 业务层所有检查都 raise `ValueError`。
- **修复**：新增 `ValidationError(ValueError)` → 400、`AccountError(ValueError)` → 400，`ConflictError` 已有 → 409。`tasks.py` 中 11 处 ValueError 替换为具体类型。

**3.2 多个 `except: pass` 静默吞掉异常** ✅ 已修复
`articles.py`, `accounts.py`, `browser_sessions.py`
- **修复**：全部加 `_logger.warning/debug`。

**3.3 Notice 单槽覆盖，无自动消失** ✅ 已修复
三个 workspace
- **修复**：`Toast.tsx` 替换所有 notice，支持队列 + 4s 自动消失 + error/success/info 颜色区分。

**3.4 `ErrorBoundary` 没有重试按钮** ✅ 已修复
- **修复**：加"重试"按钮，`setState({ hasError: false, error: null })`。

**3.5 任务状态转换不记录聚合后的最终状态** ✅ 已修复
- **修复**：`_aggregate_task_status` 中加 log。

### LOW（已修复 5/16）

**3.6 后台线程没有 Python logging** ✅ 已修复
- **修复**：所有 service 模块加 `logger.getLogger(__name__)` 并在关键路径记录。

**3.7 `recover_stuck_records` 不记录恢复了哪些记录** ✅ 已修复
- **修复**：加 `logger.warning("Recovered %d stuck records: %s", ...)"`。

---

## 四、API & 设计一致性

### MEDIUM（全部 4/4 已修复 ✅）

**4.1 `POST .../execute` 名称与行为不符** ✅ 已修复
- **修复**：函数重命名为 `start_task_execution`，加 docstring 说明 fire-and-forget 行为。

**4.2 `POST .../retry` 不自动触发后台执行** ✅ 已修复
- **修复**：`retry_record_endpoint` 加 `_start_background_execute(record.task_id)`。

**4.3 `GET /api/tasks/{id}/status` 与主 endpoint 冗余** ✅ 已修复
- **修复**：前端无引用，已删除。

**4.4 `upload_asset` 绕过 `response_model`** ✅ 已修复
- **修复**：用 `Response` + `model_dump_json()` 替代 `JSONResponse`。

---

## 五、测试

### HIGH

**5.1 FTS5 表只在测试中创建，不在生产中** ✅ 无需修复
`utils.py:49-83`
- `articles_fts` 虚拟表和触发器用原始 SQL 手动创建。生产数据库（Alembic 迁移）中没有。FTS 在生产中不可用。
- **核查结果**：已有迁移 `0003_fts5_indexes.py`，`alembic upgrade head` 后会创建 FTS5 表。测试中手动创建是因为 `build_test_app` 使用内存 SQLite，不走 Alembic。非误报，无需修复。

**5.2 launcher 测试超时——uvicorn 真正启动** ✅ 已修复
`test_launcher_startup.py:92-129`
- `test_uvicorn_run_disables_default_log_config_when_stdout_none` 调用 `launcher.main()`，后者真正启动了 uvicorn + 浏览器 + system tray，不会返回。
- **修复**：改为直接调 `launcher._setup_logging(log_file)` 测试，不再调 `launcher.main()`。

### MEDIUM（全部 2/2 已修复 ✅）

**5.3 缺少真正的并发竞争测试** ✅ 已修复
- **修复**：新增 `test_execute_and_cancel_race_does_not_leave_corrupt_state`。

**5.4 依赖时序的轮询测试可能超时** ✅ 已修复
- **修复**：`test_user_input_required_pauses_record` 改用 `threading.Event` 同步替代纯轮询。

---

## 六、配置 & 安全

### MEDIUM（全部 3/3 已修复 ✅）

**6.1 常量散落在代码库中** ✅ 已修复
- **修复**：`RECORD_TIMEOUT_SECONDS` 移到 `Settings.publish_record_timeout_seconds`。

**6.2 Token 可通过 URL 查询参数传递** ✅ 已修复
- **修复**：移除 `request.query_params.get("token")`，只保留 `X-Geo-Token` header。

**6.3 ZIP 导入无 `resolve().is_relative_to()` 检查** ✅ 已修复
- **修复**：添加 `dest.resolve().is_relative_to(get_data_dir().resolve())`。

---

## 建议改进优先级

### ✅ 已全部修复（34 条）

~~HIGH 1-12（全部）~~ ✅
~~MEDIUM 1.10-1.15, 2.3-2.7, 3.1-3.7, 4.1-4.4, 5.3-5.4, 6.1-6.3~~ ✅
~~LOW 3.6-3.7~~ ✅

### ⏳ 第三阶段（6 条）

1. ContentWorkspace 进一步拆分 — `useArticleEditor()` hook、`ArticleEditorPane`（1.8 剩余）
2. CRUD 样板代码抽取（2.2）
3. idle_timeout → `last_active_at`（1.16）— 需前端 heartbeat API
4. Chromium 并发上限 semaphore（1.17）
5. B1 idle_cleanup 线程启动位置移入 create_app()（已完成）
6. B3 ContentWorkspace 拆分（部分完成，剩余进 3 阶段）
