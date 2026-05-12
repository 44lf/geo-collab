# Phase 3 实施计划：账号权限与数据持久化

> 排期：5/16-5/22 | 前置：Phase 1+2 已完成
> 执行策略：4 Wave 并行，缩短 wall-clock 时间

---

## 决策汇总

| # | 决策 | 方案 |
|---|------|------|
| Q1 | User 模型 | `id`, `username`, `password_hash`, `role`, `is_active`, `must_change_password`, `created_at`, `last_login_at` |
| Q2 | 鉴权 | JWT 存 httpOnly cookie |
| Q3 | 初始账号 | seed 脚本，`GEO_SEED_USERS` 环境变量注入，默认密码 `geo123456` + 首次登录强制改密 |
| Q3a | 权限 | admin / operator 两个 role，暂同权，设计上预留隔离 |
| Q4 | 数据库 | MySQL 8.0 + pymysql，`pool_size=5, max_overflow=10, pool_recycle=3600, pool_pre_ping=True` |
| Q5 | 全文搜索 | MySQL FULLTEXT INDEX + ngram parser，分词调优后置到 Phase 5 |
| Q6 | 数据迁移 | 全新库，不保留旧数据 |
| Q7-8 | 数据隔离 | `user_id` 加到 accounts / articles / article_groups / publish_tasks / assets，约束改为 user-scoped |
| Q9 | 部署 | Docker Compose（app + MySQL 8.0） |
| Q10 | launcher.py | 保留为 Docker entrypoint，删 Windows 专属代码 |
| Q11 | 测试 | 日常开发 SQLite（90 全量），CI MySQL 关键路径（15 个 marker） |
| Q12 | exe 模式 | 删除 |

---

## 并行依赖关系图

```
Wave 1  (预置)       A1(User+seed)  ∥  A2(Docker+compose)
                          │
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                  │
Wave 2  (核心)       B1(Auth后端)  ∥  B2(MySQL配置)
        │                 │                  │
        ▼                 ▼                  ▼
Wave 3  (业务)   C1(前端登录)  ∥  C2(迁移×8)  ∥  C3(数据隔离+搜索)
        │                 │                  │
        └─────────────────┴──────────────────┘
                          │
                          ▼
Wave 4  (收尾)        D(测试+清理+文档+Docker验证)
```

**并行公理：**
- A1∥A2 — 全部新建文件，零交集 ✓
- B1∥B2 — B1 改 `security.py/auth.py/main.py/client.ts`，B2 改 `config.py/paths.py/session.py/env.py`，零重合 ✓
- C1∥C2∥C3 — C1 前端，C2 迁移文件，C3 模型+路由+service，三类文件零交集 ✓
- C3 内部 D1(data isolation) 先做，D2(search rewrite) 在 D1 之后改同一个 `articles.py`，同 agent 内串行

**依赖说明：**
- C2 需要 User 模型（来自 A1）决定 `users` 表结构
- C3 需要 User 模型（来自 A1）+ `get_current_user`（来自 B1）
- C1 需要 B1 的 `/api/auth/*` 端点存在
- D 需要全部前置完成

---

## Wave 1：预置（2 agent 并行）

### A1 — User 模型 + seed 脚本

- [ ] **A1.1** 建 User 模型 `server/app/models/user.py`
  - 字段：`id`, `username`(unique), `password_hash`, `role`(admin/operator), `is_active`, `must_change_password`, `created_at`, `last_login_at`
  - 方法：`set_password(raw)` → bcrypt 哈希；`check_password(raw)` → bool
- [ ] **A1.2** `requirements.txt` 加 `pymysql`, `bcrypt`, `python-jose`
- [ ] **A1.3** 写 `server/scripts/seed_users.py`
  - 读 `GEO_SEED_USERS` 环境变量（JSON 数组：`[{"username":"admin","password":"xxx","role":"admin"},...]`）
  - bcrypt 哈希后写入 → 幂等（users 表已有时跳过）
  - 默认密码 `geo123456`、强制改密标志 `must_change_password=True`

### A2 — Docker + docker-compose

- [ ] **A2.1** 建 `Dockerfile`
  - 基础：`python:3.12-slim`
  - 系统依赖：Chromium、Xvfb、x11vnc、websockify、noVNC、fonts
  - Python：`COPY requirements.txt → pip install`
  - 入口：`CMD ["python", "launcher.py"]`
- [ ] **A2.2** 建 `docker-compose.yml`
  - Service `mysql`：mysql:8.0, port 3306, volume `mysql_data:/var/lib/mysql`
  - Service `app`：build ., port 8000, depends_on mysql, env_file
  - 环境变量模板 `env.example`：`GEO_DATABASE_URL`, `GEO_JWT_SECRET`, `GEO_SEED_USERS`, `GEO_PUBLISH_REMOTE_BROWSER_ENABLED` 等

**Wave 1 产物：** `docker-compose up` 启动 MySQL + users 表结构就绪 + seed 脚本可调用

---

## Wave 2：核心基础设施（2 agent 并行）

### B1 — 鉴权后端

- [ ] **B1.1** JWT 工具（`core/security.py` 扩展）
  - `create_access_token(user_id: int, role: str)` → JWT string
  - `verify_token(token: str)` → `{"user_id": int, "role": str}` 或 None
  - 密钥从 `os.environ["GEO_JWT_SECRET"]` 读取，过期 8h（`GEO_JWT_EXPIRE_HOURS`）
- [ ] **B1.2** `get_current_user` FastAPI 依赖（`core/security.py`）
  - 读 `request.cookies.get("access_token")` → 验证 JWT → `db.get(User, user_id)` → 返回 User ORM
  - 验证失败或用户不存在 → `HTTPException(401)`
  - 检查 `is_active` → 非活跃 → 403
- [ ] **B1.3** Auth 路由 `server/app/api/routes/auth.py`（无需鉴权依赖）
  - `POST /api/auth/login` — 验证密码 → Set-Cookie（httpOnly, SameSite=Lax, path=/, maxAge=8h）
  - `POST /api/auth/logout` — 清除 cookie
  - `GET /api/auth/me` — 返回 `{id, username, role, must_change_password}`
  - `POST /api/auth/change-password` — 验旧密码 → 设新密码 → 清 `must_change_password`
  - `POST /api/auth/users` — admin 创建子账号
- [ ] **B1.4** `main.py` 改造
  - 所有 router 的 `dependencies=[Depends(require_local_token)]` → `dependencies=[Depends(get_current_user)]`
  - 注册 `auth_router`（`prefix="/api/auth"`，不加 token 依赖）
  - 保留 `/api/bootstrap`：无用户时返回 `{"needs_setup": true}`，有用户但未登录返回 `{"authenticated": false}`
- [ ] **B1.5** 前端 `api/client.ts`
  - 去掉 `getToken()` 和 `X-Geo-Token` header（cookie 自动带）
  - 加 `getCurrentUser()` → 调 `/api/auth/me`
  - 401 时抛统一错误供 App.tsx 判断跳登录页

### B2 — MySQL 配置层

- [ ] **B2.1** `core/config.py` 加设置
  - `GEO_DATABASE_URL: str | None = None`（未设时回退 SQLite）
  - `GEO_JWT_SECRET: str = ""`
- [ ] **B2.2** `core/paths.py` 改造
  - `get_database_url()`：优先 `GEO_DATABASE_URL`，未设 → 现有 SQLite 逻辑
- [ ] **B2.3** `db/session.py` 改造
  - 检测 dialect：`engine.url.get_dialect().name`
  - SQLite：原有 PRAGMA + `check_same_thread=False`
  - MySQL：`connect_args={"init_command": "SET SESSION time_zone='+00:00'"}`, `pool_size=5`, `max_overflow=10`, `pool_recycle=3600`, `pool_pre_ping=True`
- [ ] **B2.4** `alembic/env.py` 改造
  - 去掉 `literal_binds=True`（MySQL 不支持）
  - `NullPool` → 无指定（用引擎默认 QueuePool）

**Wave 2 产物：** `curl -c cookie -X POST /api/auth/login` → 拿 cookie → `curl -b cookie /api/accounts` 调通

---

## Wave 3：业务实现（3 agent 并行）

### C1 — 前端登录

- [ ] **C1.1** `features/auth/AuthContext.tsx`（新建）
  - React Context：`user | null`, `login(username, pw)`, `logout()`, `changePassword(old, new)`
  - 启动时自动调 `/api/auth/me` 检查登录态
- [ ] **C1.2** `features/auth/LoginPage.tsx`（新建）
  - 居中卡片：logo + 标题 + 用户名 + 密码 + 登录按钮
  - 错误提示（用户名不存在、密码错误、账号被禁用）
  - 品牌色 `#2563eb`，暗色背景
- [ ] **C1.3** `features/auth/ChangePasswordPage.tsx`（新建）
  - 旧密码 + 新密码 + 确认新密码
  - 改密成功后跳工作区
- [ ] **C1.4** `App.tsx` 改造
  - `AuthProvider` 包裹
  - 路由守卫：未登录 → LoginPage；`must_change_password` → ChangePasswordPage；正常 → 工作区
  - 右上角：用户名 + 退出按钮

### C2 — 8 个 MySQL 兼容迁移

- [ ] **C2.1** 删除旧迁移文件 `server/alembic/versions/0001-0005*`
- [ ] **C2.2** 新建 MySQL 兼容迁移（每个迁移单张或关联表）
  - `0001_create_platforms.py` — `platforms` 表
  - `0002_create_accounts.py` — `accounts` 表（FK → platforms, UniqueConstraint）
  - `0003_create_articles_assets.py` — `articles` + `assets` + `article_body_assets`
  - `0004_create_article_groups.py` — `article_groups` + `article_group_items`
  - `0005_create_publish_tasks.py` — `publish_tasks` + `publish_task_accounts` + `publish_records` + `task_logs`
  - `0006_create_users.py` — `users` 表（复用 A1 定义的字段）
  - `0007_fts_indexes.py` — `ALTER TABLE articles ADD FULLTEXT INDEX ft_articles (title, author) WITH PARSER ngram`
  - `0008_add_user_id.py` — ALTER TABLE 5 表加 `user_id BIGINT NOT NULL` + FK + index
- [ ] **C2.3** 每个迁移文件标记 `revision` / `down_revision` 链

### C3 — 数据隔离 + 搜索重写

**Phase A — 数据隔离（先做，为搜索重写铺路）**

- [ ] **C3.1** 5 个模型加 `user_id` 列
  - `Account`, `Article`, `ArticleGroup`, `PublishTask`, `Asset`
  - 声明：`user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)`
- [ ] **C3.2** 约束改为 user-scoped
  - `article_groups`: `UniqueConstraint("user_id", "name")`
  - `accounts`: `UniqueConstraint("user_id", "platform_id", "platform_user_id")`
- [ ] **C3.3** 7 个路由模块加 user 过滤
  - GET list：`.filter(Model.user_id == current_user.id)`
  - POST create：`model.user_id = current_user.id`
  - GET/PUT/DELETE by id：`.filter(Model.user_id == current_user.id, Model.id == target_id)`
  - admin 可看全量：`if current_user.role == "admin": skip filter`
- [ ] **C3.4** Service 层签名改版
  - `create_article(db, user_id, data)`
  - `create_task(db, user_id, data)`
  - `create_article_group(db, user_id, data)`
  - `store_bytes(db, user_id, ...)`
  - `login_account(db, user_id, ...)`

**Phase B — 搜索重写（在 Phase A 完成后，同 agent 内继续）**

- [ ] **C3.5** `services/articles.py` 搜索兼容层
  - 检测 `db.bind.dialect.name`：`sqlite` → FTS5 MATCH；`mysql` → `MATCH(title,author) AGAINST(:q IN BOOLEAN MODE)`
  - 封装为 `_search_articles(db, query)` 函数
- [ ] **C3.6** 删除 FTS5 专用代码（`INSERT OR IGNORE`, `content_rowid`, trigram 触发器）
  - 仅 MySQL 路径：FULLTEXT 索引自维护，无需手动触发器
  - SQLite 路径保留（开发用）

**Wave 3 产物：** 浏览器登录成功 → 两个操作员互不可见对方数据 + 文章搜索可用

---

## Wave 4：收尾（1 agent）

### D — 测试 + 清理 + 文档

- [ ] **D.1** 全量 90 测试 SQLite 适配
  - `build_test_app` 加 `test_user` fixture（自动创建 User → 生成 JWT → 注入 cookie → TestClient）
  - 所有 `api()` 调用加 cookie 替换 `X-Geo-Token` header
- [ ] **D.2** 删除 exe 残留
  - 删 `geo.spec`
  - `launcher.py` 精简：删 `_make_tray_image()`, `_run_tray()`, `_check_chrome()`, `_show_chrome_missing_error()`, `_open_browser()`, `_find_free_port()`
  - 保留 `_run_migrations()`, `_setup_logging()`, `main()` 为 Docker entrypoint
- [ ] **D.3** 删 `test_launcher_startup.py` 中 Chrome/托盘相关测试（7 个）
- [ ] **D.4** 加 CI MySQL 测试 marker
  - `conftest.py` 加 `--mysql` option + `mysql_db` fixture（Docker testcontainers）
  - 15 个关键 case 加 `@pytest.mark.mysql`：
    - `test_models.py` — 建表 + 关系
    - `test_fts_and_migrations.py` — 迁移链 + FULLTEXT 搜索
    - `test_tasks_state_machine.py` — 并发状态机
    - `test_articles_api.py` — CRUD + 搜索
    - `test_accounts_api.py` — 账号 CRUD
    - `test_publish_validation.py` — 发布校验
- [ ] **D.5** Docker Compose 全链路验证
  - `docker-compose up -d` → 健康检查通过
  - `alembic upgrade head` → 8 迁移全部执行
  - `python -m server.scripts.seed_users` → 6 个用户写入
  - curl 登录 → 创建文章 → 搜索 → 创建任务 → 数据隔离验证
- [ ] **D.6** 更新文档
  - `docs/deploy.md`：Docker Compose 启动步骤，环境变量清单
  - `AGENTS.md`：去 exe/PyInstaller，加 MySQL/Docker 命令
  - `README.md`：加 Docker Compose 快速启动
  - `plan.md`：标记 Phase 3 完成

**Wave 4 产物：** 90 测试 SQLite 全绿 + 15 MySQL 关键测试全绿 + 全链路可部署

---

## 文件变更统计

| 分类 | 新建 | 修改 | 删除 |
|------|------|------|------|
| 模型/DB | User + 迁移×8 | 5 模型 | 旧迁移×6 |
| 路由 | auth.py | 7 路由 + main.py | — |
| Service | — | articles / tasks / accounts / assets | — |
| 核心 | — | config / paths / session / security | — |
| 前端 | LoginPage / ChangePasswordPage / AuthContext | App.tsx / client.ts | — |
| 部署 | Dockerfile / compose.yml / env.example | deploy.md | geo.spec |
| 工具 | seed_users.py | launcher.py | — |
| 测试 | conftest marker | 20+ 测试文件 | 7 个旧测试 |

---

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| FTS5 → FULLTEXT 分词效果差异 | Wave 3 C3 只保证能搜到，分词调优放 Phase 5 |
| 90 测试全量适配工作量大 | Wave 4 D.1 集中处理，统一 user fixture |
| pymysql 驱动与 SQLAlchemy 兼容 | Wave 1 A1.2 锁定 `PyMySQL>=1.1.0` 版本 |
| Docker Chromium 资源占用 | `docker-compose.yml` 设 `mem_limit: 2g` |
| C3 数据隔离 + 搜索同文件冲突 | 同 agent 内串行：Phase A(data isolation) → Phase B(search) |
