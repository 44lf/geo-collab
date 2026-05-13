# Geo 协作发布平台

> 最后更新：2026-05-13 | 当前进度：Phase 1–5 全部完成，142 个测试全绿

## 1. 目标

从本地 exe MVP 升级为云端 Web 发布系统，支持多用户通过 Web 界面完成头条号文章发布。业务核心：文章编辑 → 选择账号 → 创建任务 → 自动发布（Playwright 模拟），遇到登录失效/验证码/扫码时**人工远程操作服务器浏览器**处理。

### 关键边界

- 先只做**头条号**，后续扩展其他平台
- 多用户模型：admin（全部权限）+ operator（增改不可删，不可管理账号/系统）
- 并发上限：5 个发布浏览器同时工作（全局 semaphore 强制），超出排队
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

### 环境变量

| 变量 | 说明 | 必填 |
|------|------|------|
| `GEO_JWT_SECRET` | JWT 签名密钥 | ✅ |
| `GEO_SEED_USERS` | 初始用户 JSON `[{"username":"admin","password":"...","role":"admin"}]` | 首次部署 |
| `GEO_DB_HOST` | MySQL 主机名 | 生产 |
| `GEO_DB_PORT` | MySQL 端口（默认 3306） | |
| `GEO_DB_USER` | MySQL 用户名 | 生产 |
| `GEO_DB_PASS` | MySQL 密码（自动 URL-encode，支持特殊字符） | 生产 |
| `GEO_DB_NAME` | MySQL 数据库名 | 生产 |
| `GEO_DATABASE_URL` | 完整 MySQL URL（备用，优先级高于上面五个） | |
| `GEO_DATA_DIR` | 数据目录（覆盖默认路径） | |
| `GEO_PUBLISH_REMOTE_BROWSER_ENABLED` | 启用 Xvfb + noVNC（生产必须设 true） | 生产 |
| `GEO_FEISHU_WEBHOOK_URL` | 飞书机器人 Webhook，不设则静默跳过 | |
| `GEO_SECURE_COOKIE` | HTTPS 时设为 true，启用 Cookie Secure 标志 | 生产 |
| `GEO_JWT_EXPIRE_HOURS` | JWT 过期时间（默认 8h） | |
| `GEO_PUBLISH_MAX_CONCURRENT_RECORDS` | 最大并发发布数（上限 5） | |

### 本地开发命令

```powershell
conda activate geo_xzpt
uvicorn server.app.main:app --reload --host 127.0.0.1 --port 8000
pnpm --filter @geo/web dev           # 前端 (port 5173, proxy /api → :8000)
python -m pytest server/tests/ -v    # 测试（142 个）
alembic upgrade head                  # 数据库迁移
```

### 服务器部署命令

```bash
git pull
docker-compose build
docker-compose up -d
docker-compose exec app alembic upgrade head
```

---

## 3. 项目结构

```
server/app/
  api/routes/     auth / accounts / articles / article-groups / assets / publish-records / tasks / system
  core/           config / paths / security / time
  db/             session / base
  models/         user / article / account / article_group / asset / publish (+ platform)
  services/       articles / accounts / tasks / assets / article_groups /
                  toutiao_publisher / feishu /
                  browser / browser_sessions / clipboard / serializers / errors
  schemas/        task
web/src/
  features/       auth / accounts / content / tasks / system
  components/     Modal / Toast / Pagination / ErrorBoundary
  api/            client.ts
  types.ts
server/alembic/versions/   0001–0009（SQLite+MySQL 双方言）
docker-compose.yml / Dockerfile / .env.example / launcher.py
server/tests/    142 个测试，test_phase4 / test_feishu / test_search / test_concurrent_publish 等
```

---

## 4. 排期与进度

| 阶段 | 完成时间 | 主题 | 状态 |
|------|----------|------|------|
| Phase 1 | 5/11 | 本地 MVP：FastAPI + React + SQLite + Playwright 基础发布链 | ✅ |
| Phase 2 | 5/12 | 云端浏览器：Xvfb + noVNC + 远程人工介入 + 部署文档 | ✅ |
| Phase 3 | 5/12 | 账号鉴权 + MySQL + 数据隔离 + Docker 部署 | ✅ |
| Phase 4 | 5/13 | 任务调度、人工介入完善、恢复机制 | ✅ |
| 债务清理 | 5/13 | R12 独立 DB 凭据 / 全局并发 semaphore / async→def | ✅ |
| Phase 5 | 5/13 | 飞书通知 + 分词搜索优化 + 并发稳定性测试 | ✅ |

---

## 5. 功能完成清单

### 5.1 核心业务

| 功能 | 实现位置 |
|------|---------|
| 文章增删改查 + 富文本编辑 | `routes/articles.py` + Tiptap 编辑器 |
| 文章分组（批量发布用） | `routes/article_groups.py` |
| 账号管理（登录/检查/导出/导入） | `routes/accounts.py` + `services/accounts.py` |
| 素材上传（封面/正文图） | `routes/assets.py` + `services/assets.py` |
| 任务创建 + 发布执行（single / group_round_robin） | `routes/tasks.py` + `services/tasks.py` |
| 头条号 Playwright 自动发布 | `services/toutiao_publisher.py` |
| noVNC 远程浏览器人工介入 | `services/browser_sessions.py` |

### 5.2 任务状态机

```
pending → running → succeeded
                 → failed
                 → waiting_user_input  ← 需要扫码/验证码/登录，人工操作后继续
                 → waiting_manual_publish  ← stop_before_publish 模式，人工确认
                 → cancelled
```

### 5.3 安全与鉴权

| 项目 | 实现 |
|------|------|
| JWT httpOnly cookie，8h 过期 | `security.py` |
| 角色权限（admin/operator） | `require_admin` 依赖，DELETE 和管理端点需 admin |
| 数据隔离（user_id 过滤） | 所有 service 层查询 |
| Cookie Secure（生产 HTTPS） | `GEO_SECURE_COOKIE=true` |
| 登录失效强制改密 | `must_change_password` 字段 + 403 拦截 |
| 跨用户数据泄露防护 | export / client_request_id / tasks/preview 均加 user_id 过滤 |
| JWT 角色变更绕过防护 | `/users` 从 DB 重新读取角色 |

### 5.4 运维能力

| 功能 | 实现 |
|------|------|
| 启动崩溃恢复 | `recover_stuck_records()`：lease 过期的 running 记录重置为 pending + 写 warn 日志 |
| 僵尸浏览器清理 | `_cleanup_zombie_sessions()`：每 30s 巡检进程 poll() |
| 飞书任务完成通知 | `services/feishu.py` fire-and-forget，配 `GEO_FEISHU_WEBHOOK_URL` |
| 全局 Chromium 并发上限 | `_global_publish_sem = Semaphore(5)`，`_publish_record` acquire/release |
| 异常类型分类 | `error_type`: login_required / captcha_required / qr_scan_required |
| 失败截图存档 | `ToutiaoPublishError.screenshot` → `assets` 表，日志内联渲染 |

### 5.5 搜索

| 路径 | 实现 |
|------|------|
| SQLite FTS5 | `articles_fts` 虚拟表，索引 title + author + plain_text，trigram tokenizer |
| MySQL FULLTEXT | `ft_articles` 索引，title + author + plain_text，ngram parser |
| LIKE fallback | 查询 < 3 字符或 FTS 失败时，LIKE 匹配 title \| author \| plain_text |

---

## 6. 数据库迁移历史

| 版本 | 内容 |
|------|------|
| 0001 | platforms 表 |
| 0002 | accounts 表 |
| 0003 | articles + assets 表 |
| 0004 | article_groups 表 |
| 0005 | publish_tasks / publish_records / task_logs 表 |
| 0006 | users 表 |
| 0007 | FTS5 虚拟表（SQLite）/ FULLTEXT（MySQL），复合索引 |
| 0008 | 所有表加 user_id 字段 |
| 0009 | FTS5 / FULLTEXT 加入 plain_text 字段 |

---

## 7. 已知债务（不修）

| # | 问题 | 决策 |
|---|------|------|
| R7 | `_read_or_generate_token` 死代码（launcher.py） | 不影响功能，不修 |
| R15 | `AuthContext` login 后多一次 `/me` 请求 | 轻微性能，暂缓 |
| R16 | `LoginPage` 错误消息英语字符串匹配 | 脆弱但可用，暂缓 |
| R17 | `App.tsx` 4 个工作区始终挂载 | 轻微性能，暂缓 |
| — | `GET /api/assets/{id}` 无认证 | 设计决策：公开 CDN 式行为，asset_id 为 UUID |
| — | ContentWorkspace 拆分 / CRUD 样板抽取 | 纯重构，无 bug，跳过 |
| — | browser_sessions heartbeat | 需前后端协同，设计上延迟 |

---

## 8. 关键设计决策

| 决策 | 方案 | 原因 |
|------|------|------|
| 鉴权方式 | JWT httpOnly cookie（非 session） | 避免 Redis 依赖，5 用户规模够用 |
| 有头浏览器 | Chromium + Xvfb + noVNC（非无头） | 头条反爬虫 + 需人工扫码/验证码 |
| 数据库 | SQLite 开发 + MySQL 生产 | 开发零配置，生产稳定 |
| 任务执行 | 同步阻塞 + 后台线程（非 Celery） | 5 用户规模，不引入队列依赖 |
| 并发控制 | `_global_publish_sem = Semaphore(5)` | 防止多任务并发超出 5 个浏览器进程 |
| 飞书通知 | Webhook + urllib daemon 线程（fire-and-forget） | 不阻塞任务执行，不引入新依赖 |
| 部署 | Docker Compose | 5 用户规模，不需要 K8s |

---

## 9. 代码约定

- **AGENTS.md** — 权威：启动命令 / 架构 / Playwright selectors / 测试 quirks
- 所有 API 错误：`ValueError` → 400，`ConflictError(ValueError)` → 409
- 头条 DOM：ByteDance 设计系统（`byte-btn`），**非** Ant Design
- 发布两步："预览并发布" → "确认发布"
- 测试：`build_test_app(monkeypatch)` 建 in-memory SQLite，monkeypatch `build_publisher_for_record` 替换 Playwright

---

## 10. 待办

- [ ] 上服务器部署验证（服务器 SSH 配置待确认）
- [ ] 管理后台 UI（用户管理页面，未排期）
- [ ] 多平台扩展（头条之外，未排期）
