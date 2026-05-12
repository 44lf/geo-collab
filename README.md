# Geo 协作平台

Geo 协作平台云端发布管理系统。

## Docker Compose 快速启动

```powershell
cp .env.example .env
# 编辑 .env，设置 MYSQL_ROOT_PASSWORD、GEO_JWT_SECRET、GEO_SEED_USERS
docker-compose up -d
docker-compose exec app alembic upgrade head
docker-compose exec app python -m server.scripts.seed_users
# 打开浏览器访问 http://服务器IP:8000
```

## 代码阅读顺序

以下顺序适合新人从零开始掌握项目全貌：

### 第一层：项目骨架
1. **`server/app/core/config.py`** — 全局配置（数据目录、应用名等）
2. **`server/app/db/session.py`** — 数据库连接方式（MySQL + SQLAlchemy）
3. **`server/app/models/`** — 12 个 ORM 模型：Platform → Account → Article → PublishTask → PublishRecord，以及 ArticleGroup / Asset 等辅助表，加上 User 模型

### 第二层：业务逻辑
4. **`server/app/services/accounts.py`** — 账号登录 / 检测 / 导入导出，了解 storage_state 生命周期
5. **`server/app/services/toutiao_publisher.py`** — Playwright 自动化发文，了解头条页面操作流程
6. **`server/app/services/tasks.py`** — 任务调度引擎，了解 publish 执行链路和状态机

### 第三层：API 接口
7. **`server/app/api/routes/`** — 7 个路由模块（accounts, article_groups, articles, assets, publish_records, system, tasks），加上 auth 路由

### 第四层：前端
8. **`web/src/`** — React 前端，feature-split 结构（`features/content/`, `features/accounts/`, `features/tasks/`, `features/system/`），Tiptap 富文本编辑器，Lucide 图标

### 第五层：入口与测试
9. **`launcher.py`** — 应用入口（Alembic 自动升级、启动 uvicorn）
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

登录状态验证通过后，再打开发布页 Spike：

```powershell
python -m server.scripts.toutiao_publish_spike --account-key spike
```
