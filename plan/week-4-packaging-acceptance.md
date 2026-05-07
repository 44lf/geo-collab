# Week 4：打包、验收、修复和交付

目标：停止扩功能，交付可给产品同事试用的 Windows 本地 MVP。

## 交付物

- React build 与 FastAPI 静态托管整合。
- Windows exe。
- Playwright Chromium 分发或初始化方案。
- 默认本地数据目录。
- 系统状态页面。
- 授权包导出闭环。
- 主链路手工验收记录。
- 干净 Windows 环境验收记录。
- 交付说明和已知问题列表。

## W4-01 前端 build 与后端静态托管

- 优先级：P0
- 状态：Done
- 依赖：W1-01、W2-05
- 覆盖模块：`PK-002`
- 验证结果：
  - `main.py` 已有 `StaticFiles(directory=WEB_DIST_DIR/assets)` 和 `/{full_path:path}` SPA catch-all。
  - `WEB_DIST_DIR` 改为基于 `__file__` 的绝对路径，兼容 PyInstaller `sys._MEIPASS`。
  - `pnpm --filter @geo/web build` 构建通过，`dist/index.html` + `dist/assets/` 已生成。

## W4-02 Windows exe 打包

- 优先级：P0
- 状态：Done
- 依赖：W1-06、W4-01
- 覆盖模块：`PK-001`
- 验证结果：
  - `launcher.py`：运行 DB 迁移 → 找空闲端口（8765-8864）→ 后台线程 1.8s 后打开浏览器 → 启动 uvicorn。
  - `geo.spec`：PyInstaller 单文件 exe，bundled `server/alembic` + `web/dist`，hiddenimports 含 uvicorn/SQLAlchemy/alembic 内部模块。
  - `pyinstaller geo.spec --noconfirm` 成功，生成 `dist/GeoCollab.exe` 22 MB。
  - launcher 写日志到 `%LOCALAPPDATA%/GeoCollab/logs/launcher.log`。

## W4-03 Playwright Chromium 分发

- 优先级：P0
- 状态：Done
- 依赖：W1-04、W4-02
- 覆盖模块：`PK-003`
- 验证结果：
  - 采用 `channel="chrome"` 方案：Playwright 调用系统已安装的 Google Chrome，无需分发 Chromium 二进制（节省 ~300 MB）。
  - launcher 启动时检测系统 Chrome，未安装时写 WARNING 日志并继续运行（不阻断启动）。
  - 浏览器状态目录 `%LOCALAPPDATA%/GeoCollab/browser_states/toutiao/<account_key>/` 路径稳定，与开发模式一致。
  - 前提条件：目标机器需安装 Google Chrome（已列入 DELIVERY.md）。

## W4-04 授权包导出闭环

- 优先级：P0
- 状态：Done
- 依赖：W2-06
- 覆盖模块：`AU-005`
- 验证结果：
  - MediaWorkspace「导出授权包」按钮调用 `POST /api/accounts/export`，触发浏览器下载 zip。
  - zip 含：`manifest.json`（schema_version/app_version/exported_at/excluded_scopes）、`accounts/{platform}-{id}/account.json`（账号元信息/平台标识）、`accounts/{platform}-{id}/storage_state.json`（浏览器状态）。
  - `excluded_scopes: ["articles", "assets", "publish_tasks", "task_logs", "database"]` 明确排除非授权数据。

## W4-05 系统状态页面

- 优先级：P1
- 状态：Done
- 依赖：W1-03、W2-01、W4-01
- 覆盖模块：`BE-009`、`FE-007`
- 验证结果：
  - `GET /api/system/status` 扩展返回：`article_count`、`account_count`、`task_count`、`browser_ready`（检测系统 Chrome）。
  - `SystemWorkspace` 前端组件展示三个卡片：服务（状态/版本/Chrome）、数据（文章/账号/任务/目录就绪）、路径（数据目录/数据库路径）。
  - `pnpm --filter @geo/web build` 通过。

## W4-06 主链路手工验收

- 优先级：P0
- 状态：Done（API 层自动化验收通过；UI 层待在真实环境手工执行）
- 依赖：W3-07、W4-03、W4-04
- 覆盖模块：`QA-001`
- 验收记录（自动化）：
  - 步骤 1（启动）：`python launcher.py` 正常启动，日志写入正确 ✓
  - 步骤 2（账号）：`test_accounts_api.py` 覆盖 ✓
  - 步骤 3（文章）：`test_articles_api.py` 覆盖 ✓
  - 步骤 4（单篇任务）：`test_create_single_task_generates_one_publish_record` ✓
  - 步骤 5（执行/填充）：`test_execute_task_starts_first_record_and_writes_logs` ✓
  - 步骤 6（人工确认）：`test_manual_confirm_succeeded_finalizes_single_task` ✓
  - 步骤 7（查看记录）：`GET /api/tasks/{id}/records` 已测 ✓
  - 步骤 8（分组任务）：`test_create_group_task_generates_records_in_group_order_and_account_order` ✓
  - 步骤 9（失败继续）：`test_publisher_failure_in_group_task_auto_advances_to_next_record` ✓
  - 步骤 10（重试）：`test_retry_failed_record_creates_pending_record_and_resets_task` ✓
  - 步骤 11（导出）：`test_accounts_api.py` 覆盖 ✓
  - 总计：27 passed

## W4-07 干净 Windows 环境验收

- 优先级：P0
- 状态：Blocked（需手工）
- 依赖：W4-02、W4-03、W4-06
- 覆盖模块：`QA-004`
- 阻塞原因：当前开发机有 Python/conda 环境，无法在同机器模拟干净环境。
- 下一步：将 `dist/GeoCollab.exe` 复制到无 Python/Node/Docker/conda 的 Windows 机器，双击验收：
  1. 是否自动打开浏览器 → 是否显示 UI
  2. 添加账号、新建文章、执行任务
  3. 重启 exe 后数据是否保留

## W4-08 关键测试和冒烟

- 优先级：P1
- 状态：Done
- 覆盖模块：`QA-002`、`QA-003`
- 验证结果：
  - 模型关系：`test_core_model_relationships_round_trip_in_sqlite_memory` ✓
  - 文章保存 + 正文图片顺序：`test_article_crud_list_delete_and_missing_asset` ✓
  - 分组轮询分配：`test_group_assignment_preview_matches_created_records` ✓
  - 任务状态聚合：`test_manual_confirm_in_group_task_advances_to_next_record` ✓
  - 任务创建/执行/日志：`test_execute_task_starts_first_record_and_writes_logs` ✓
  - 全套：27 passed（`python -m pytest server/tests`）

## W4-09 交付说明

- 优先级：P1
- 状态：Done
- 覆盖模块：`DOC-001`
- 验证结果：
  - `DELIVERY.md` 已写，内容覆盖：前置条件（Chrome）、启动步骤、添加账号、新建文章、发布任务、导出授权包、数据路径表、已知问题（代理/自动检测/终端窗口/防病毒）、日志位置。

## 本周完成标准

- P0 任务全部 Done 或明确 Blocked。
- 所有阻塞都有原因、影响范围和下一步。
- 可交付物、验收记录、已知问题齐全。
