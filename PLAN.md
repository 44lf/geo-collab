# Geo 协作发布平台

> 最后更新：2026-05-12 | 当前进度：Phase 3 完成，角色权限 + review 修复待办

## 1. 目标

从本地 exe MVP 升级为云端 Web 发布系统，支持多用户通过 Web 界面完成头条号文章发布。业务核心：文章编辑 → 选择账号 → 创建任务 → 自动发布（Playwright 模拟），遇到登录失效/验证码/扫码时**人工远程操作服务器浏览器**处理。

### 关键边界

- 先只做**头条号**，后续扩展其他平台
- 多用户模型：admin（全部权限）+ operator（增改不可删，不可管理账号/系统）
- 并发上限：5 个发布浏览器同时工作，超出排队
- 发布过程中遇到登录失效/验证码/扫码/弹窗 → 暂停，人工通过 noVNC 操作后再恢复
- 目标上线：2026-06-07

---

## 2. 技术环境

| 层 | 技术 |
|----|------|
| 后端 | FastAPI + SQLAlchemy 2.0 + Alembic |
| 数据库 | SQLite（本地开发） / MySQL 8.0 + pymysql（生产） |
| 鉴权 | JWT + bcrypt，存 httpOnly cookie，admin / operator 双角色 |
| 前端 | React 19 + Vite + TypeScript + Tiptap 编辑器 |
| 浏览器 | Playwright + Chromium（有头），服务器端 Xvfb + noVNC 远程桌面 |
| 部署 | Docker Compose（app + MySQL 8.0） |
| 数据目录 | `%LOCALAPPDATA%/GeoCollab/`（开发），容器内 `/app/data/` |

### 环境变量（核心）

| 变量 | 说明 |
|------|------|
| `GEO_JWT_SECRET` | JWT 签名密钥（必填，不设启动报错） |
| `GEO_JWT_EXPIRE_HOURS` | JWT 过期时间（默认 8h） |
| `GEO_DATABASE_URL` | 生产 MySQL 连接串（不设回退 SQLite） |
| `GEO_DATA_DIR` | 数据目录（覆盖默认 %LOCALAPPDATA%/GeoCollab） |
| `GEO_SEED_USERS` | JSON 数组预建用户 `[{"username":"admin","password":"...","role":"admin"}]` |
| `GEO_PUBLISH_REMOTE_BROWSER_ENABLED` | 启用以使用 Xvfb + noVNC（生产 true） |

### 本地开发命令

```powershell
conda activate geo_xzpt
uvicorn server.app.main:app --reload --host 127.0.0.1 --port 8000
pnpm --filter @geo/web dev           # 前端 (port 5173, proxy /api → :8000)
pytest server/tests/ -v               # 测试
alembic upgrade head                  # 数据库迁移
```

---

## 3. 项目结构

```
server/app/
  api/routes/     auth / accounts / articles / article-groups / assets / publish-records / tasks / system
  core/           config / paths / security
  db/             session
  models/         user / article / account / article_group / asset / publish (+ platform)
  services/       articles / accounts / tasks / assets / article_groups / toutiao_publisher / browser / browser_sessions / clipboard / serializers
web/src/
  features/       auth / accounts / content / tasks / system
  components/     Modal / Toast / Pagination / ErrorBoundary
  api/            client.ts
  types.ts
server/alembic/   SQLite+MySQL 双方言迁移 (0001-0008)
docker-compose.yml / Dockerfile / launcher.py
```

---

## 4. 排期与进度

| 阶段 | 排期 | 主题 | 状态 |
|------|------|------|------|
| Phase 1 | 5/7–5/11 | 本地 MVP：FastAPI + React + SQLite + Playwright 基础发布链 | ✅ |
| Phase 2 | 5/11–5/15 | 云端浏览器：Xvfb + noVNC + 远程人工介入 + 部署文档 | ✅ |
| Phase 3 | 5/16–5/22 | 账号鉴权 + MySQL + 数据隔离 + Docker 部署 | ✅ 5/12 提前完成 |
| **Phase 4** | **5/23–5/30** | 任务调度、人工介入完善、恢复机制 | ⏳ 部分提前完成 |
| Phase 5 | 5/31–6/7 | 管理后台、飞书通知、稳定性收尾 | ⬜ |

---

## 5. Phase 3 完成清单

| 模块 | 内容 |
|------|------|
| **User 模型** | `user.py` — username/role(is_active/must_change_password/bcrypt，`seed_users.py` 幂等建用户 |
| **JWT 鉴权** | `security.py` create/verify + `get_current_user` 依赖，8h 过期 |
| **Auth 路由** | login/logout/me/change-password/create-user（`auth.py`） |
| **前端登录** | `LoginPage` / `ChangePasswordPage` / `AuthContext` / 路由守卫 |
| **MySQL 迁移** | 8 个双方言迁移（`server/alembic/versions/0001-0008`） |
| **双引擎** | `session.py` SQLite WAL + MySQL pool，`_search_articles` 方言搜索 |
| **数据隔离** | `user_id` 加到 Account/Article/ArticleGroup/PublishTask/Asset，路由/service 层过滤 |
| **Docker** | `Dockerfile` + `docker-compose.yml` + `.env.example` |
| **测试** | 83/83 全绿（SQLite），15 个 `@pytest.mark.mysql` 标记 |
| **清理** | 删 `geo.spec`、精简 `launcher.py`、删旧测试、更新文档 |

---

## 6. 待完成任务

### 6.1 最高优先 — 跨用户数据泄露（4 项）

| # | 文件:行 | 问题 |
|---|---------|------|
| R1 | `routes/accounts.py:57-70` | `/api/accounts/export` 非 admin 可导出所有账号鉴权包 |
| R2 | `routes/articles.py:69-85` | `client_request_id` 去重不限 `user_id`，可泄露他人文章 |
| R3 | `routes/tasks.py:75-92` | 同上，`client_request_id` 泄露他人任务 |
| R4 | `routes/tasks.py:97-98` | `/api/tasks/preview` 不限 `user_id`，可枚举他人数据 |

### 6.2 高优先 — 鉴权缺陷（3 项）

| # | 文件:行 | 问题 |
|---|---------|------|
| R5 | `security.py:35-55` | `must_change_password` 后端未强制（前端可绕过） |
| R6 | `client.ts:23-41` | 无全局 401 拦截，JWT 过期不跳登录 |
| R7 | `launcher.py:42-52` | `_read_or_generate_token` 死代码 |

### 6.3 角色权限区分（9 项，今日新需求）

**实现方式：** `security.py` 新增 `require_role("admin")` 依赖，DELETE/导出类端点挂此依赖。

| # | 模块 | 操作员（operator）权限 |
|---|------|-----------------------|
| P1 | 文章 | 增、改；**不可删** |
| P2 | 文章分组 | 增、改；**不可删** |
| P3 | 任务 | 创建、执行；**不可删** |
| P4 | 账号 | 增、改、更新鉴权；**不可删**，**不可导出** |
| P5 | 素材 | 上传；**不可删** |
| P6 | 系统状态 | **不可查看**（admin only） |
| P7 | 用户管理 | **不可操作**（admin only） |

### 6.4 安全检查（2 项）

| # | 文件:行 | 问题 |
|---|---------|------|
| R8 | `routes/auth.py:40-47` | Cookie 缺 `secure=True` |
| R9 | `services/tasks.py:859-925` | account/article 不验归属 |

### 6.5 中等 — 部署 & 工程（3 项）

| # | 问题 |
|---|------|
| R10 | `docker-compose.yml` v3 schema + `depends_on condition` 不兼容老版 docker-compose v1 |
| R11 | `Dockerfile` 系统 Chromium + Playwright Chromium 双安装，镜像大约 400MB |
| R12 | DB 密码未 URL-encode，特殊字符导致连接失败 |

### 6.6 低优先 — 代码质量（5 项）

| # | 问题 |
|---|------|
| R13 | `/me`/`change-password`/`/users` 重复实现 `get_current_user` 逻辑 |
| R14 | `get_current_user` 用 `async def` 执行全同步操作 |
| R15 | `AuthContext` login 忽略响应体后又请求 `/me`，多一次网络往返 |
| R16 | `LoginPage` 错误消息用英语字符串匹配 |
| R17 | `App.tsx` 4 个工作区始终挂载，登录瞬间 4 倍 API 请求 |

### 6.7 Phase 3 遗留（原 review.md）

| # | 内容 |
|---|------|
| 1 | ContentWorkspace 进一步拆分（`useArticleEditor` hook） |
| 2 | `browser_sessions` idle_timeout → `last_active_at`（需前端 heartbeat） |
| 3 | Chromium 并发上限 semaphore |
| 4 | CRUD 样板代码抽取 |

### 6.8 Phase 4 — 任务调度与人工介入

| # | 内容 | 说明 |
|---|------|------|
| 4.1 | 前端 `waiting_user_input` 完整交互入口 | noVNC 打开、操作完成后恢复任务 |
| 4.2 | 任务超时 + 失败截图存储 | 目前 timeout 有基础，缺截图持久化 |
| 4.3 | 浏览器僵尸进程清理 | `atexit` 已加，长期运行需定期巡检 |
| 4.4 | 更多异常场景分类 | 登录失效/验证码/弹窗/扫码/网络超时 |
| 4.5 | 重启恢复 | `recover_stuck_records` 已有，需验证完整链路 |
| 4.6 | 任务执行日志展示 | 前端 TasksWorkspace 细化 |

---

## 7. 已知未完成能力

- 飞书通知（Phase 5）
- 压测和长时间稳定性验证（Phase 5）
- 分词搜索优化（Phase 5）
- 多平台扩展（toutiao 之外，未排期）

---

## 8. 建议修复顺序

1. **立即** R1-R4 跨用户数据泄露
2. **立即** R5 `must_change_password` 后端强制
3. **本次迭代** P1-P7 角色权限区分
4. **本次迭代** R6 前端 401 拦截
5. **后续** R8-R12 安全/部署改进
6. **后续** Phase 3 遗留 + Phase 4 任务调度

---

## 9. 关键设计决策记录

| 决策 | 方案 | 原因 |
|------|------|------|
| 鉴权方式 | JWT httpOnly cookie（非 session） | 避免 Redis 依赖，5 用户规模够用 |
| 有头浏览器 | Chromium + Xvfb + noVNC（非无头） | 头条反爬虫 + 需人工扫码/验证码 |
| 数据库 | SQLite 开发 + MySQL 生产 | 开发零配置，生产稳定 |
| 测试策略 | SQLite 全量 83 + `pytest.mark.mysql` 15 个关键 | CI 时间可控 |
| 部署 | Docker Compose | 5 用户规模，不需要 K8s |

---

## 10. 代码约定（详见 AGENTS.md 和 CLAUDE.md）

- **AGENTS.md** — 权威：启动命令 / 架构 / Playwright selectors / 测试 quirks / gotchas
- **CLAUDE.md** — 补充：旧架构参考（CLAUDE.md 已标记过期，以 AGENTS.md 为准）
- 所有 API 错误：`ValueError` → 400, `ConflictError(ValueError)` → 409
- 头条 DOM：ByteDance 设计系统（`byte-btn`），**非** Ant Design
- 发布两步："预览并发布" → "确认发布"
