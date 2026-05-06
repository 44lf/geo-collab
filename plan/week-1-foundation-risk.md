# Week 1：工程骨架与关键风险验证

目标：项目能在开发模式跑起来，并在第一周拿到头条号自动化是否可行的真实结论。

## 交付物

- 后端 FastAPI 最小服务。
- 前端 React/Vite/TypeScript 最小工作台。
- SQLite、SQLAlchemy、Alembic 最小迁移链路。
- 本地数据目录初始化。
- 头条号登录状态保存 Spike 记录。
- 头条号发布页填充 Spike 记录。
- 最小打包方案选择结论。

## W1-01 项目骨架和启动链路

- 优先级：P0
- 状态：Done
- 覆盖模块：`BE-001`、`FE-001`
- 目标：建立后端、前端、共享配置的基础目录，能分别启动。
- 范围：
  - FastAPI 应用入口和健康检查。
  - React/Vite/TypeScript 应用入口。
  - 基础 lint/typecheck/test 命令或占位脚本。
  - README 中记录开发启动命令。
- 验收：
  - 后端 `GET /api/system/status` 返回基础状态。
  - 前端开发服务可打开。
  - TypeScript 检查通过或记录阻塞。
- 验证结果：
  - 已创建 `server/` FastAPI 工程和 `web/` React/Vite/TypeScript 工程。
  - `C:\Users\Administrator\miniconda3\envs\geo_xzpt\python.exe -m pytest server/tests`：1 passed。
  - `pnpm --filter @geo/web typecheck`：通过。

## W1-02 数据库和迁移最小闭环

- 优先级：P0
- 状态：Done
- 依赖：W1-01
- 覆盖模块：`BE-002`
- 目标：数据库连接、Session 管理、Alembic 迁移可用。
- 范围：
  - SQLite 默认连接。
  - 测试环境临时 SQLite 配置。
  - 最小迁移脚本。
- 验收：
  - 能执行迁移并生成 `geo.db`。
  - 测试环境不会污染正式数据目录。
- 验证结果：
  - 已配置 SQLAlchemy、Session、Alembic。
  - 默认数据目录 `%LOCALAPPDATA%/GeoCollab` 下已生成 `geo.db`。
  - `C:\Users\Administrator\miniconda3\envs\geo_xzpt\python.exe -m alembic current`：`0001_create_platforms (head)`。
  - 备注：当前沙箱内 SQLite 写入 `E:\geo` 会出现 `disk I/O error`，默认 LocalAppData 路径验证正常。

## W1-03 本地数据目录

- 优先级：P0
- 状态：Done
- 依赖：W1-01
- 覆盖模块：`BE-003`、`PK-004`
- 目标：统一创建和读取本地工作区路径。
- 范围：
  - `geo.db`
  - `assets/`
  - `browser_states/`
  - `logs/`
  - `exports/`
  - 环境变量覆盖数据目录。
- 验收：
  - 首次启动自动创建目录。
  - 能通过环境变量切换数据目录。
- 验证结果：
  - 已实现 `geo.db`、`assets/`、`browser_states/`、`logs/`、`exports/` 目录初始化。
  - `GET /api/system/status` 返回 `data_dir`、`database_path`、`directories_ready`。
  - 默认路径 `%LOCALAPPDATA%/GeoCollab` 已验证可创建目录和 SQLite 数据库。

## W1-04 头条号登录状态保存 Spike

- 优先级：P0
- 状态：Done
- 依赖：W1-03
- 覆盖模块：`AU-001`
- 目标：验证可见浏览器人工登录后，Playwright 能保存并复用状态。
- 范围：
  - 打开可见 Chromium。
  - 人工登录或扫码。
  - 保存 `storage_state.json` 或持久化 profile。
  - 新上下文复用状态访问头条号后台。
  - 记录登录态检测依据。
- 验收：
  - 不重新扫码即可进入头条号后台。
  - 文档记录实际 URL、检测逻辑、失败点。
- 当前结果：
  - 已安装 Playwright Chromium。
  - 已新增 `server/scripts/toutiao_login_spike.py`。
  - 用户反馈手机号验证码阶段卡住，疑似网络问题、平台风控或默认 Chromium 被拒绝。
  - 已调整脚本：默认使用本机 Edge channel，并在验证阶段复用同一个持久化 profile；同时支持 `--channel msedge/chrome/chromium` 和 `--executable-path`。
  - 关闭代理后，用户完成登录，状态保存到 `chrome-spike`。
  - `python -m server.scripts.toutiao_login_spike --account-key chrome-spike --channel chrome --check-only --wait-ms 8000`：打开 `https://mp.toutiao.com/profile_v4/index`，判断 `likely_logged_in`。
  - Week 1 结论：本机 Chrome + 持久化 profile 可保存并复用头条号登录态。

## W1-05 头条号发布页填充 Spike

- 优先级：P0
- 状态：Done
- 依赖：W1-04
- 覆盖模块：`AU-002`
- 目标：验证标题、正文、封面填充并停在发布前是否可行。
- 范围：
  - 发布页 URL。
  - 标题输入策略。
  - 正文编辑器填充策略。
  - 封面上传策略。
  - 平台弹窗、裁剪、校验失败点。
- 验收：
  - 一篇测试文章能被填到头条号发布页。
  - 不自动点击最终发布。
  - Spike 结论写入本文件或 `progress.md`。
- 当前结果：
  - 已新增 `server/scripts/toutiao_publish_spike.py`，用于打开发布页并记录选择器。
  - `python -m server.scripts.toutiao_publish_spike --account-key chrome-spike --channel chrome --check-only --wait-ms 10000`：打开 `https://mp.toutiao.com/profile_v4/graphic/publish`，判断 `likely_publish_page`。
  - 标题控件：可见 `textarea`，placeholder 为 `请输入文章标题（2～30个字）`。
  - 正文控件：可见 `div[contenteditable="true"]`，区域约 `854x500`。
  - `--fill-test`：标题和正文均填充成功，不点击最终发布。
  - 封面策略：页面初始无 `input[type=file]`；选择/点击封面区域 `.article-cover` 后出现 2 个 file input，并显示“本地上传”。
  - 封面上传验证：测试 PNG 通过 `set_input_files` 上传后页面出现“已上传 1 张图片，支持拖拽调整图片顺序”。
  - Week 1 结论：标题、正文、封面本地上传均具备自动填充可行性。

## W1-06 静态托管和打包 Spike 方案

- 优先级：P1
- 状态：Done
- 依赖：W1-01
- 覆盖模块：`PK-001`、`PK-002`
- 目标：提前确认前端 build 托管方式和 exe 打包工具方向。
- 范围：
  - FastAPI 静态托管 React build 的最小验证。
  - PyInstaller 或 Nuitka 二选一的可行性记录。
- 验收：
  - 后端根路径能打开前端构建产物，或记录待解决问题。
  - 打包工具选择有结论。
- 验证结果：
  - 已实现 FastAPI 托管 `web/dist`。
  - `pnpm --filter @geo/web build`：通过。
  - FastAPI TestClient 验证 `/api/system/status` 和 `/` 均返回 200。
  - 打包工具初步选择 PyInstaller，Week 4 继续做 exe 验证。

## 本周完成标准

- P0 全部 Done 或明确 Blocked。
- `progress.md` 更新每个任务状态。
- 头条号 Spike 结论必须包含“可行 / 不稳定 / 不可行”和下一步策略。

当前状态：Week 1 完成。基础工程、数据库、本地目录、静态托管、头条号登录态复用、发布页标题/正文/封面填充 Spike 均已验证。

## Week 1 总结

- 完成：工程骨架、数据库迁移、本地数据目录、前端构建、FastAPI 静态托管、Playwright Chromium 安装、头条号登录态复用、发布页标题/正文/封面填充 Spike。
- 风险记录：开启代理时手机号验证码阶段卡住；关闭代理后可登录并复用状态。后续账号授权流程应提示用户避免代理或在验证码失败时切换网络。
- 决策：Week 2 正常推进文章、资源、分组和账号基础能力。
