# Geo 协作发布平台

> 最后更新：2026-05-13 | 当前进度：Phase 5 完成，142 个测试全绿

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
| **Phase 5** | **5/31–6/7** | 飞书通知、分词搜索优化、并发稳定性 | ✅ 5/13 提前完成 |

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
| **测试** | 106/106 全绿（SQLite），含 23 个安全边界测试；15 个 `@pytest.mark.mysql` 标记 |
| **清理** | 删 `geo.spec`、精简 `launcher.py`、删旧测试、更新文档 |

---

## 6. 完成清单（5/13 本次迭代）

### 6.1 ✅ 跨用户数据泄露修复（4 项）

| # | 文件 | 修复 | commit |
|---|------|------|--------|
| R1 | `routes/accounts.py` | `/api/accounts/export` 改用 `require_admin`，非 admin 返回 403 | 964eb53 → 749c087 |
| R2 | `routes/articles.py` | `client_request_id` 去重加 `user_id` 过滤 | 964eb53 |
| R3 | `routes/tasks.py` | 同上 | 964eb53 |
| R4 | `routes/tasks.py` | `/api/tasks/preview` 加 `get_current_user` 依赖 | 964eb53 |

### 6.2 ✅ 鉴权修复（R5、R6）

| # | 文件 | 修复 | commit |
|---|------|------|--------|
| R5 | `security.py` | `get_current_user` 强制检查 `must_change_password`，返回 403 | 964eb53 |
| R6 | `client.ts` / `AuthContext.tsx` | 全局 401 拦截 → `setUser(null)`；403 password-change-required → `must_change_password=true` | 704433b → 749c087 |
| R7 | `launcher.py` | 死代码，暂不处理（不影响功能） | — |

### 6.3 ✅ 角色权限区分（P1-P7）

| # | 模块 | 实现 | commit |
|---|------|------|--------|
| P1 | 文章 DELETE | `require_admin` | cff89c0 |
| P2 | 文章分组 DELETE | `require_admin` | cff89c0 |
| P3 | 任务 | 无 DELETE 端点，天然满足 | — |
| P4 | 账号 DELETE / export | `require_admin` | cff89c0 / 749c087 |
| P5 | 素材 | 无 DELETE 端点，天然满足 | — |
| P6 | 系统状态 | `require_admin` | cff89c0 |
| P7 | 用户管理 | `/auth/users` 已内联 admin 检查 | — |

### 6.4 ✅ 安全边界测试（102 个测试全绿）

新增 `test_security_boundaries.py` (19 个测试)，覆盖 R1/R4/R5/P1/P2/P4/P6 所有安全边界。

---

## 7. 待完成任务

### 7.1 ✅ 安全检查（已完成）

| # | 修复 | commit |
|---|------|--------|
| R8 | `config.py` 加 `secure_cookie` 设置，`auth.py` login/logout 均加 `secure=` | 561db6f |
| R9 | 任务创建/预览时校验 account/article 归属，admin 绕过，operator 强制 | 561db6f |

**已知设计债务（暂不修）：**
- `GET /api/assets/{id}` 无认证（公开 CDN 式行为，asset_id 为 UUID，intentional）
- `_read_or_generate_token` 死代码（R7，不影响功能）

### 7.2 部署 & 工程（✅ 已完成）

| # | 问题 | 状态 |
|---|------|------|
| R10 | `docker-compose.yml` 移除废弃 `version: "3.8"` | ✅ |
| R11 | `Dockerfile` 移除 apt chromium/chromium-driver（Playwright 自带），镜像缩小 ~200MB | ✅ |
| R12 | `config.py` 新增 `GEO_DB_HOST/PORT/USER/PASS/NAME`，`paths.py` 自动拼接并 `quote_plus` 密码；`docker-compose.yml` 改用独立变量 | ✅ |
| R13 | `auth.py /users` 从 DB 校验角色（防 JWT 过期内角色变更绕过）；`/change-password` 加 `is_active` 检查 | ✅ |
| R14 | `get_current_user` 改为 `def`（同步依赖，不阻塞事件循环） | ✅ |

### 7.3 代码质量（已知债务，暂不修）

| # | 问题 | 风险等级 | 决策 |
|---|------|---------|------|
| R15 | `AuthContext` login 忽略响应体后又请求 `/me`，多一次网络往返 | 轻微性能 | 暂缓 |
| R16 | `LoginPage` 错误消息用英语字符串匹配（已 `.toLowerCase()` 处理，当前可用） | 脆弱但可用 | 暂缓 |
| R17 | `App.tsx` 4 个工作区始终挂载，登录瞬间 4 倍 API 请求 | 轻微性能 | 暂缓 |

### 7.4 Phase 3 遗留（已解决 / 明确跳过）

| # | 内容 | 状态 |
|---|------|------|
| 1 | ContentWorkspace 进一步拆分（`useArticleEditor` hook） | 跳过（纯重构，无 bug） |
| 2 | `browser_sessions` idle_timeout → `last_active_at`（需前端 heartbeat） | 跳过（设计上延迟，需前后端协同） |
| 3 | Chromium 全局并发上限 semaphore | ✅ `_global_publish_sem = Semaphore(5)`，`_publish_record` acquire/release |
| 4 | CRUD 样板代码抽取 | 跳过（纯重构，无 bug） |

### 7.5 Phase 4 — 任务调度与人工介入（✅ 已完成）

| # | 内容 | 实现 | 状态 |
|---|------|------|------|
| 4.1 | 前端人工介入按钮 | `waiting_user_input`：「操作完成」→ resolve-user-input；`waiting_manual_publish`：「确认发布」/「标记失败」→ manual-confirm | ✅ |
| 4.2 | 失败截图展示 | 日志列表中在消息下方直接渲染截图（`screenshot_asset_id` → `/api/assets/{id}`） | ✅ |
| 4.3 | 浏览器僵尸进程清理 | `_cleanup_zombie_sessions()` 每 30s 巡检，进程 `poll() != None` 即清理 session 及关联 record | ✅ |
| 4.4 | 异常场景分类 | `ToutiaoUserInputRequired.error_type`：`login_required` / `captcha_required` / `qr_scan_required`；分类结果写入日志标签 | ✅ |
| 4.5 | 重启恢复 + 日志 | `recover_stuck_records()` 重置 record 同时写 `TaskLog(level=warn)`，前端可见 | ✅ |
| 4.6 | 日志截图渲染 | 同 4.2（复用同一实现） | ✅ |

---

## 8. Phase 5 完成清单

| 功能 | 实现 | 测试 |
|------|------|------|
| **飞书通知** | `services/feishu.py`：`notify_task_finished` fire-and-forget daemon 线程；`config.py` 新增 `GEO_FEISHU_WEBHOOK_URL`；`_aggregate_task_status` 终态时触发 | `test_feishu.py` 11 个 |
| **分词搜索优化** | migration `0009_fts_add_plain_text`：SQLite FTS5 / MySQL FULLTEXT 加入 `plain_text`；`articles.py` MySQL match 和 LIKE fallback 均加 `plain_text` | `test_search.py` 6 个 |
| **并发稳定性** | `test_concurrent_publish.py`：验证 semaphore 初始值、异常后无泄漏、成功后归还、跨任务最大并发 ≤ 5 | 5 个 |

## 9. 已知未完成能力

- 管理后台（未排期）
- 多平台扩展（toutiao 之外，未排期）

---

## 10. 建议下一步顺序

1. **下一步** 管理后台（用户管理 UI）或多平台扩展

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
