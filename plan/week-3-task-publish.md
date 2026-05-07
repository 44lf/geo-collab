# Week 3：任务系统与头条号发布主链路

目标：打通 MVP 核心链路。任务系统必须能驱动头条号半自动填充、等待人工发布确认、记录结果并支持失败重试。

## 交付物

- 单篇任务。
- 分组轮询任务。
- 发布记录生成。
- 任务执行器。
- 任务日志。
- `ToutiaoPublisher` 集成。
- 等待人工发布确认。
- 自动检测发布结果的最小实现。
- 失败继续和失败重试。
- 分发引擎 UI。

## W3-01 任务和发布记录 API

- 优先级：P0
- 状态：Done
- 依赖：W2-01、W2-04、W2-06
- 覆盖模块：`TS-001`
- 目标：任务创建和查询可用。
- 范围：
  - 单篇任务创建。
  - 分组轮询任务创建。
  - 发布记录生成。
  - 任务账号选择和顺序保存。
- 接口：
  - `GET /api/tasks`
  - `POST /api/tasks`
  - `GET /api/tasks/{id}`
  - `GET /api/tasks/{id}/records`
- 验收：
  - 单篇任务生成 1 条记录。
  - 分组任务按文章数生成 N 条记录。
  - 未授权或失效账号不能创建发布任务，或创建时明确提示风险。
- 验证结果：
  - 已新增 `GET /api/tasks`、`POST /api/tasks`、`GET /api/tasks/{id}`、`GET /api/tasks/{id}/records`。
  - 单篇任务要求恰好 1 个有效账号，并生成 1 条 `publish_records`。
  - 分组任务按分组内文章 `sort_order` 生成 N 条记录，账号按任务内 `sort_order` 轮询分配。
  - 创建任务时会保存 `publish_task_accounts` 的账号顺序。
  - 已校验任务类型、文章、分组、账号存在性；账号必须属于目标平台且状态为 `valid`。
  - `C:\Users\Administrator\miniconda3\envs\geo_xzpt\python.exe -m pytest server/tests`：19 passed。

## W3-02 分组轮询分配

- 优先级：P0
- 状态：Done
- 依赖：W3-01
- 覆盖模块：`TS-002`
- 目标：实现文章到账号的顺序分配。
- 规则：
  - 文章按分组内 `sort_order` 排序。
  - 账号按任务内 `sort_order` 排序。
  - 文章 A/B/C/D + 账号 1/2/3 分配为 1/2/3/1。
  - 不允许同一篇文章在同一任务中分配多个账号。
- 验收：
  - 分配预览和后端记录一致。
  - 有单元测试或可复现的手工验证记录。
- 验证结果：
  - 已新增 `POST /api/tasks/preview`，创建任务前可返回文章到账号的分配预览。
  - 预览和任务创建共用同一套 `_build_assignments` 分配逻辑，避免规则漂移。
  - 已验证文章按分组内 `sort_order` 排序，账号按任务内 `sort_order` 排序。
  - 已验证文章 A/B/C/D + 账号 1/2/3 分配为 1/2/3/1。
  - 已校验同一任务内文章不能重复分配；重复文章会返回明确错误。
  - `C:\Users\Administrator\miniconda3\envs\geo_xzpt\python.exe -m pytest server/tests`：20 passed。
  - `pnpm --filter @geo/web typecheck`：通过。

## W3-03 任务执行器和日志

- 优先级：P0
- 状态：Done
- 依赖：W1-05、W3-01
- 覆盖模块：`TS-003`
- 目标：串行执行发布记录，并写入可查询日志。
- 范围：
  - `POST /api/tasks/{id}/execute`
  - `POST /api/tasks/{id}/cancel`
  - `GET /api/tasks/{id}/logs`
  - 任务状态聚合。
  - 记录级状态流转。
- 验收：
  - 执行任务后状态从 `pending` 到 `running`。
  - 每条记录串行执行。
  - 日志可由前端轮询查看。
  - 终止任务后状态为 `cancelled`。
- 验证结果：
  - 已新增 `POST /api/tasks/{id}/execute`、`POST /api/tasks/{id}/cancel`、`GET /api/tasks/{id}/logs`。
  - 执行任务会把任务从 `pending` 推进到 `running`，并写入任务开始日志。
  - 执行器当前串行推进第一条 `pending` 记录到 `waiting_manual_publish`，其余记录保持 `pending`，等待 W3-04/W3-05 接入真实发布器和人工确认后继续推进。
  - 重复执行时如果已有 `running` 或 `waiting_manual_publish` 记录，不会并发推进下一条，会写入等待日志。
  - 取消任务会把任务设为 `cancelled`，并把未完成记录设为 `cancelled`。
  - `GET /api/tasks/{id}/logs` 可按时间顺序返回任务日志，供前端轮询。
  - `C:\Users\Administrator\miniconda3\envs\geo_xzpt\python.exe -m pytest server/tests`：22 passed。
  - `pnpm --filter @geo/web typecheck`：通过。

## W3-04 头条号发布器产品化

- 优先级：P0
- 状态：Done
- 依赖：W1-05、W2-03、W2-06、W3-03
- 覆盖模块：`AU-002`、`TS-003`
- 目标：把 Spike 中验证过的填充策略封装到 `ToutiaoPublisher`。
- 范围：
  - 加载账号浏览器状态。
  - 打开头条号发布页。
  - 填标题。
  - 填正文。
  - 上传封面。
  - 停在发布前。
  - 失败时截图和日志。
- 验收：
  - 单篇任务能自动填充发布页。
  - 不自动点击最终发布。
  - 选择器失败、登录失效、封面异常有明确错误信息。
- 验证结果：
  - 已新增 `ToutiaoPublisher` 服务，封装登录态加载、发布页打开、标题填充、正文填充、封面上传和失败截图。
  - 执行器已接入发布器：成功填充后记录进入 `waiting_manual_publish`，等待 W3-05 人工确认。
  - 发布器使用 `browser_states/toutiao/<account_key>/profile` 持久化上下文，并回写 `storage_state.json`。
  - 失败时会把错误写入 `publish_records.error_message`，并将截图保存为资产后写入 `task_logs.screenshot_asset_id`。
  - 已覆盖选择器失败路径，错误信息明确；登录失效和封面文件异常也会返回明确 `ToutiaoPublishError`。
  - 真实冒烟通过：使用 `chrome-spike` 账号打开 `https://mp.toutiao.com/profile_v4/graphic/publish`，填入测试标题和正文，停在发布前，未点击最终发布。
  - `C:\Users\Administrator\miniconda3\envs\geo_xzpt\python.exe -m pytest server/tests`：23 passed。
  - `pnpm --filter @geo/web build`：通过。

## W3-05 人工发布确认

- 优先级：P0
- 状态：Done
- 依赖：W3-03、W3-04
- 覆盖模块：`TS-004`
- 验证结果：
  - `POST /api/publish-records/{id}/manual-confirm` 已实现；`outcome` 为 `succeeded`（可填 `publish_url`）或 `failed`（可填 `error_message`）。
  - 确认后自动调用 `_run_next_pending_record`，推进任务到下一条 pending 记录。
  - 用户不操作时记录保持 `waiting_manual_publish`，任务保持 `running`，不自动跳过。
  - `pytest server/tests`：27 passed。

## W3-06 失败继续和重试

- 优先级：P0
- 状态：Done
- 依赖：W3-05
- 覆盖模块：`TS-005`
- 验证结果：
  - publisher 报错时 `_run_next_pending_record` 会循环推进到下一条，不中断任务。
  - `POST /api/publish-records/{id}/retry` 创建 `retry_of_record_id` 指向原记录的新 pending 记录；原记录保留；任务从 `failed`/`partial_failed` 重置为 `running`。
  - `pytest server/tests`：27 passed。

## W3-07 分发引擎 UI

- 优先级：P0
- 状态：Done
- 依赖：W2-05、W2-06、W3-01、W3-06
- 覆盖模块：`FE-006`
- 验证结果：
  - `TasksWorkspace` 组件已接入"分发引擎"导航。
  - 左侧面板：任务列表 + 创建表单（单篇/分组轮询选择、文章/分组/账号选择、分配预览）。
  - 右侧面板：任务详情、执行/取消按钮、发布记录列表（含状态徽章、标记成功/失败、重试）、执行日志（任务执行中自动 2.5s 轮询）。
  - `pnpm --filter @geo/web build`：通过，`tsc -b`：通过。

## W3-08 发布结果自动检测

- 优先级：P1
- 状态：Not Started
- 依赖：W3-05
- 覆盖模块：`AU-006`
- 目标：用户人工点击发布后，尝试自动识别成功状态或 URL。
- 验收：
  - 成功时自动写入链接或成功状态。
  - 检测失败时进入人工确认，不阻塞手动流程。

## 本周完成标准

- 单篇任务发布主链路必须跑通。
- 分组轮询任务必须能生成正确记录并串行执行。
- P0 任务全部 Done 或明确 Blocked。
- 自动检测结果可以降级，但人工确认不可降级。
