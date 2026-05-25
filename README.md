# Geo 协作平台

Geo 协作平台云端发布管理系统。**仅支持 Linux 服务器部署（Docker Compose）**。

## Docker Compose 快速启动

```bash
cp .env.example .env
# 编辑 .env，设置 MYSQL_ROOT_PASSWORD、GEO_JWT_SECRET、GEO_SEED_USERS
docker-compose up -d
docker-compose exec app python -m server.scripts.seed_users
# 打开浏览器访问 http://服务器IP/
```

Docker 启动时自动执行 `alembic upgrade head`，无需手动迁移。

发布 worker 按单实例设计，请不要使用 `docker compose up --scale worker=N`。noVNC 端口默认只绑定宿主机 `127.0.0.1`，远程处理登录/验证码请通过 VPN 或 SSH 隧道访问。

## 代码阅读顺序

以下顺序适合新人从零开始掌握项目全貌：

### 第一层：项目骨架
1. **`server/app/core/config.py`** — 全局配置（数据目录、应用名等）
2. **`server/app/db/session.py`** — 数据库连接方式（MySQL + SQLAlchemy）
3. **各模块 `models.py`** — ORM 模型按领域分布：
   - `modules/system/models.py` — User、Platform、WorkerHeartbeat
   - `modules/accounts/models.py` — Account、AccountLoginSession、BrowserSession
   - `modules/articles/models.py` — Article、ArticleGroup、Asset、Tag 等
   - `modules/tasks/models.py` — PublishTask、PublishRecord、TaskLog 等

### 第二层：业务逻辑
4. **`server/app/modules/accounts/service.py`** — 账号 CRUD；**`auth.py`** — 登录 / 检测 / 导入导出，了解 storage_state 生命周期
5. **`server/app/modules/tasks/drivers/toutiao.py`** — Playwright 自动化发文，了解头条页面操作流程
6. **`server/app/modules/tasks/runner.py`** — 通用发布编排（`run_publish`），了解 Xvfb session 管理
7. **`server/app/modules/tasks/service.py`** + **`executor.py`** — 任务调度引擎，了解 publish 执行链路和状态机

### 第三层：API 接口
8. **各模块 `router.py`** — 路由按领域内聚：
   - `modules/system/auth_router.py` + `system_router.py` — 认证与系统信息
   - `modules/accounts/router.py` — 账号管理
   - `modules/articles/router.py` — 文章、分组、资产、分块上传（四个 router 合一）
   - `modules/tasks/router.py` — 任务与发布记录（两个 router 合一）
   - `modules/ai_generation/router.py`、`modules/skills/router.py`、`modules/prompt_templates/router.py`

### 第四层：前端
9. **`web/src/`** — React 前端，feature-split 结构（`features/content/`, `features/accounts/`, `features/tasks/`, `features/system/`），Tiptap 富文本编辑器，Lucide 图标

## 环境

- Python 使用 conda 环境：`geo_xzpt`
- 前端使用 Node.js + pnpm

## 后端开发

```bash
conda activate geo_xzpt
pip install -r requirements.txt
# 需要 MySQL；设置 GEO_DATABASE_URL，或设置 GEO_DB_HOST/GEO_DB_USER/GEO_DB_NAME
alembic upgrade head
uvicorn server.app.main:app --reload --host 127.0.0.1 --port 8000
```

健康检查：

```bash
curl http://127.0.0.1:8000/api/system/status
```

## 前端开发

```bash
pnpm install
pnpm --filter @geo/web dev
```

## 数据目录

`GEO_DATA_DIR` 环境变量控制数据存储位置，Docker 内默认为 `/app/data`：

```
data/
├── assets/             # 上传图片
├── browser_states/     # Playwright persistent profiles
│   └── <platform_code>/
│       └── <account_key>/
│           ├── profile/            # Chromium profile 目录
│           └── storage_state.json  # 登录态快照
├── exports/            # 账号授权导出 ZIP
└── logs/               # 远程浏览器 session 进程日志
```
