# Geo 协作平台

本仓库是 Geo 协作平台 Windows 本地 MVP 的工程目录。实施计划见 `plan/README.md`。

## 代码阅读顺序

以下顺序适合新人从零开始掌握项目全貌：

### 第一层：项目骨架
1. **`server/app/core/config.py`** — 全局配置（数据目录、应用名等）
2. **`server/app/db/session.py`** — 数据库连接方式（SQLite WAL + busy_timeout）
3. **`server/app/models/`** — 11 个 ORM 模型：Platform → Account → Article → PublishTask → PublishRecord，以及 ArticleGroup / Asset 等辅助表

### 第二层：业务逻辑
4. **`server/app/services/accounts.py`** — 账号登录 / 检测 / 导入导出，了解 storage_state 生命周期
5. **`server/app/services/toutiao_publisher.py`** — Playwright 自动化发文，了解头条页面操作流程
6. **`server/app/services/tasks.py`** — 任务调度引擎，了解 publish 执行链路和状态机

### 第三层：API 接口
7. **`server/app/api/routes/`** — 6 个路由模块，了解 RESTful 接口如何暴露业务逻辑

### 第四层：前端
8. **`web/src/main.tsx`** — 单文件 1377 行，所有 UI 组件集中于此，配合 `web/src/styles.css` 理解界面

### 第五层：入口与测试
9. **`server/app/launcher.py`** — 桌面应用入口（Alembic 自动升级等启动逻辑）
10. **`server/tests/`** — 关键测试文件，验证对各模块的理解

## 环境

- Python 使用 conda 环境：`geo_xzpt`
- 前端使用 Node.js + pnpm

## 后端开发

```powershell
conda activate geo_xzpt
python -m pip install -r requirements.txt
alembic upgrade head
uvicorn server.app.main:app --reload --host 127.0.0.1 --port 8000
```

健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/system/status
```

## 前端开发

```powershell
pnpm install
pnpm --filter @geo/web dev
```

## 数据目录

默认数据目录为 `%LOCALAPPDATA%/GeoCollab`，可用环境变量覆盖：

```powershell
$env:GEO_DATA_DIR="E:\geo\GeoAppData"
```

## 头条号 Spike

先运行登录状态保存脚本，按浏览器提示人工登录或扫码：

```powershell
conda activate geo_xzpt
python -m server.scripts.toutiao_login_spike --account-key spike
```

如果验证码阶段卡住，先停止重试，等待一段时间后改用本机 Edge 或 Chrome channel：

```powershell
python -m server.scripts.toutiao_login_spike --account-key edge-spike --channel msedge
python -m server.scripts.toutiao_login_spike --account-key chrome-spike --channel chrome
```

本机路径可显式指定：

```powershell
python -m server.scripts.toutiao_login_spike --account-key edge-spike --executable-path "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
```

登录状态验证通过后，再打开发布页 Spike：

```powershell
python -m server.scripts.toutiao_publish_spike --account-key spike
```
