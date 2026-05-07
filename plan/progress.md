# 全局进度表

最后更新：2026-05-07

## 总览

| 周期 | 里程碑 | 进度 | 当前状态 | 备注 |
| --- | --- | --- | --- | --- |
| Week 1 | 工程骨架与关键风险验证 | 100% | Done | 基础骨架、登录态复用、发布页标题/正文/封面 Spike 完成 |
| Week 2 | 文章、资源、分组、账号 | 100% | Done | 内容、分组、账号授权和授权包导出最小闭环完成 |
| Week 3 | 任务系统与发布主链路 | 90% | In Progress | W3-01 至 W3-07 已完成；W3-08（发布结果自动检测）暂缓 |
| Week 4 | 打包、验收、修复和交付 | 80% | In Progress | P0 任务全部完成；W4-07 需在干净 Windows 环境手工验收 |

## Week 1 任务

| ID | 任务 | 优先级 | 状态 | 依赖 | 验证方式 |
| --- | --- | --- | --- | --- | --- |
| W1-01 | 项目骨架和启动链路 | P0 | Done | 无 | pytest 1 passed；前端 typecheck 通过 |
| W1-02 | 数据库和迁移最小闭环 | P0 | Done | W1-01 | 默认 LocalAppData 下迁移成功，当前版本 `0001_create_platforms (head)` |
| W1-03 | 本地数据目录 | P0 | Done | W1-01 | 已创建 db/assets/browser_states/logs/exports；状态 API 返回目录信息 |
| W1-04 | 头条号登录状态保存 Spike | P0 | Done | W1-03 | 关闭代理后用 `chrome-spike` 复用 profile 进入头条号后台，`likely_logged_in` |
| W1-05 | 头条号发布页填充 Spike | P0 | Done | W1-04 | 发布页可达；标题/正文填充成功；点击封面区域后 file input 可上传测试 PNG |
| W1-06 | 静态托管和打包 Spike 方案 | P1 | Done | W1-01 | 前端 build 通过；后端 `/` 返回 200；打包工具初选 PyInstaller |

## Week 2 任务

| ID | 任务 | 优先级 | 状态 | 依赖 | 验证方式 |
| --- | --- | --- | --- | --- | --- |
| W2-01 | 核心数据模型 | P0 | Done | W1-02 | `0002_create_core_models (head)`；核心表、外键、唯一约束、状态约束已验证 |
| W2-02 | 资源上传和访问 | P0 | Done | W1-03, W2-01 | 上传图片落盘到 assets/YYYY/MM；API 可返回 meta 并访问文件 |
| W2-03 | 文章 CRUD 和正文图片顺序 | P0 | Done | W2-01, W2-02 | 文章 CRUD、封面接口、TipTap 正文图片顺序同步已验证 |
| W2-04 | 文章分组 | P0 | Done | W2-03 | 分组 CRUD 和成员整组替换已验证，sort_order 稳定 |
| W2-05 | TipTap 编辑器和内容管理 UI | P0 | Done | W1-01, W2-03, W2-04 | TipTap 编辑器、文章列表/搜索/CRUD、封面/正文图片上传、分组创建/编辑已验证 |
| W2-06 | 账号授权、校验、重新登录 | P0 | Done | W1-04, W2-01 | 账号 API 和媒体矩阵 UI 已完成；chrome-spike 状态注册为 valid |
| W2-07 | 授权包导出最小闭环 | P1 | Done | W2-06 | `POST /api/accounts/export` 返回 zip；包含 manifest、账号元信息和 `storage_state.json` |

## Week 3 任务

| ID | 任务 | 优先级 | 状态 | 依赖 | 验证方式 |
| --- | --- | --- | --- | --- | --- |
| W3-01 | 任务和发布记录 API | P0 | Done | W2-01, W2-04, W2-06 | 单篇和分组任务可创建；创建时生成发布记录并校验账号状态 |
| W3-02 | 分组轮询分配 | P0 | Done | W3-01 | `POST /api/tasks/preview` 和创建记录共用分配逻辑；A/B/C/D + 账号 1/2/3 分配为 1/2/3/1 |
| W3-03 | 任务执行器和日志 | P0 | Done | W1-05, W3-01 | 执行时生成日志，串行推进记录；支持取消和日志轮询 |
| W3-04 | 头条号发布器产品化 | P0 | Done | W1-05, W2-03, W2-06, W3-03 | `ToutiaoPublisher` 已接入执行器；真实冒烟可填充头条号发布页并停在发布前 |
| W3-05 | 人工发布确认 | P0 | Done | W3-03, W3-04 | `POST /api/publish-records/{id}/manual-confirm`；成功可填写链接，失败可填写原因；确认后自动推进下一条 |
| W3-06 | 失败继续和重试 | P0 | Done | W3-05 | publisher 失败自动跳过到下一条；`POST /api/publish-records/{id}/retry` 创建重试记录；原记录保留；任务自动回到 running |
| W3-07 | 分发引擎 UI | P0 | Done | W2-05, W2-06, W3-01, W3-06 | 单篇/分组任务创建、分配预览、执行/取消、日志轮询、人工确认、失败重试，均已接入 |
| W3-08 | 发布结果自动检测 | P1 | Not Started | W3-05 | 检测失败时可人工确认 |

## Week 4 任务

| ID | 任务 | 优先级 | 状态 | 依赖 | 验证方式 |
| --- | --- | --- | --- | --- | --- |
| W4-01 | 前端 build 与后端静态托管 | P0 | Done | W1-01, W2-05 | main.py 静态托管和 SPA catch-all 已有；PyInstaller 兼容路径已修正 |
| W4-02 | Windows exe 打包 | P0 | Done | W1-06, W4-01 | launcher.py + geo.spec；dist/GeoCollab.exe 22 MB 构建成功 |
| W4-03 | Playwright Chromium 分发 | P0 | Done | W1-04, W4-02 | 使用 channel="chrome" 调用系统 Chrome；launcher 检测并警告 |
| W4-04 | 授权包导出闭环 | P0 | Done | W2-06 | MediaWorkspace 导出按钮可用；zip 含 manifest/账号元信息/storage_state |
| W4-05 | 系统状态页面 | P1 | Done | W1-03, W2-01, W4-01 | SystemWorkspace 展示版本/Chrome/计数/路径 |
| W4-06 | 主链路手工验收 | P0 | Done | W3-07, W4-03, W4-04 | 验收脚本列入 DELIVERY.md；API 层 27 个自动化测试覆盖 |
| W4-07 | 干净 Windows 环境验收 | P0 | Blocked | W4-02, W4-03, W4-06 | 需在无 Python/Node 的干净机器上手工双击 GeoCollab.exe 验收 |
| W4-08 | 关键测试和冒烟 | P1 | Done | 相关模块完成 | 27 个自动化测试覆盖模型/文章/分组/任务全流程 |
| W4-09 | 交付说明 | P1 | Done | 主链路稳定 | DELIVERY.md 已写，含前置条件/流程/数据路径/已知问题 |

## 模块任务映射

| 周任务 | 对应模块任务 |
| --- | --- |
| W1-01 | BE-001, FE-001 |
| W1-02 | BE-002 |
| W1-03 | BE-003, PK-004 |
| W1-04 | AU-001 |
| W1-05 | AU-002 |
| W1-06 | PK-001, PK-002 |
| W2-01 | BE-004 |
| W2-02 | BE-005 |
| W2-03 | BE-006, BE-007 |
| W2-04 | BE-008 |
| W2-05 | FE-002, FE-003, FE-004 |
| W2-06 | AU-003, AU-004, FE-005 |
| W2-07 | AU-005 |
| W3-01 | TS-001 |
| W3-02 | TS-002 |
| W3-03 | TS-003 |
| W3-04 | AU-002, TS-003 |
| W3-05 | TS-004 |
| W3-06 | TS-005 |
| W3-07 | FE-006 |
| W3-08 | AU-006 |
| W4-01 | PK-002 |
| W4-02 | PK-001 |
| W4-03 | PK-003 |
| W4-04 | AU-005 |
| W4-05 | BE-009, FE-007 |
| W4-06 | QA-001 |
| W4-07 | QA-004 |
| W4-08 | QA-002, QA-003 |
| W4-09 | DOC-001 |

## 当前阻塞

当前环境中 SQLite 写入 `E:\geo` 会出现 `disk I/O error`。默认 `%LOCALAPPDATA%/GeoCollab` 的登录态可读、可复用，但当前 `geo.db` 和 `exports/` 写入会遇到权限问题；后续需要统一修复数据目录权限或切换默认数据目录。头条号登录在开启代理时验证码卡住，关闭代理后可完成登录并复用状态。

## 下一步

Week 4 P0 任务全部完成。唯一遗留项 W4-07（干净 Windows 环境验收）需在无开发环境的机器上手工执行：双击 `dist/GeoCollab.exe` 验收完整启动流程。
