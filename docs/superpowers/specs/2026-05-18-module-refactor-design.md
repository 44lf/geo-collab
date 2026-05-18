# 模块重构设计文档

**日期：** 2026-05-18
**目标：** 将 `server/app/services/` 重组为三个职责清晰的业务模块，降低耦合，为排查发文内容遗漏 bug 建立清晰的数据流追踪路径。

---

## 背景

现状问题：
- `services/` 下文件平铺，职责边界模糊
- `tasks.py` 直接 import 约 10 个模块，是全项目最重的耦合点
- 平台驱动（`toutiao.py`）自己 import `articles`/`assets`，导致发文数据流难以追踪
- 命名风格不统一，新成员难以快速定位代码

---

## 目标

- 以**长期可维护性**为主（不以短期修 bug 为主，但结构清晰后 bug 更易定位）
- 后端重构为主，前端 API 调用层补齐对齐
- 改动深度：文件重组 + 模块接口重设计（每模块只暴露 `__init__.py` 中声明的函数）

---

## 目录结构

```
server/app/
├── modules/
│   ├── articles/
│   │   ├── __init__.py          ← 对外唯一入口
│   │   ├── article_Crud.py      ← 文章 / 分组 CRUD
│   │   ├── tiptap_Parser.py     ← Tiptap JSON 解析、正文段落提取
│   │   └── asset_Store.py       ← 图片存储与路径解析
│   ├── accounts/
│   │   ├── __init__.py
│   │   ├── account_Crud.py      ← 账号 CRUD、import/export
│   │   ├── account_Auth.py      ← 登录会话、storage state 管理
│   │   └── browser_Session.py   ← Xvfb/VNC/noVNC session 生命周期
│   └── tasks/
│       ├── __init__.py
│       ├── task_Crud.py         ← Task / Record / Log CRUD
│       ├── task_Executor.py     ← ThreadPoolExecutor 调度
│       ├── publish_Runner.py    ← Playwright 编排 + PublishPayload 组装
│       └── drivers/
│           ├── __init__.py      ← 驱动注册表 + PlatformDriver Protocol
│           ├── driver_Base.py   ← PublishPayload / PublishResult / PublishError
│           └── toutiao.py       ← 头条号驱动
├── shared/
│   ├── errors.py                ← 从 services/errors.py 移来
│   ├── diagnostics.py           ← 从 services/publish_diagnostics.py 移来
│   └── feishu.py                ← 飞书通知
├── api/                         ← 不变
├── core/                        ← 不变
├── db/                          ← 不变
└── models/                      ← 不变
```

旧 `server/app/services/` 在所有模块迁移完成后删除。

---

## 模块依赖规则（硬约束）

```
articles   →  shared, core, db, models
accounts   →  shared, core, db, models
tasks      →  articles.__init__, accounts.__init__, shared, core, db, models

routes     →  任意模块的 __init__.py（禁止 import 模块内部文件）
drivers    →  driver_Base.py 中的数据类型（禁止 import articles / accounts）
```

`tasks` 是唯一可以同时依赖 `articles` 和 `accounts` 的模块。

---

## 核心数据契约：PublishPayload

定义于 `tasks/drivers/driver_Base.py`，是 `publish_Runner` 传给驱动的唯一数据结构。

```python
@dataclass(frozen=True)
class BodySegment:
    kind: str                       # "text" | "image"
    text: str = ""                  # kind="text" 时有值
    image_path: Path | None = None  # kind="image" 时为已解析本地绝对路径
    image_asset_id: str | None = None  # 仅用于日志追踪

@dataclass(frozen=True)
class PublishPayload:
    title: str
    author: str
    cover_image_path: Path | None   # None → 驱动应直接抛错
    body_segments: list[BodySegment]
    article_id: int                 # 用于日志追踪
    account_id: int
```

### 组装责任

```
tiptap_Parser.parse_body_segments(article)
  → list[BodySegment]（image_path 未解析）
                    ↓
publish_Runner（组装点）
  ├─ 调 articles.parse_body_segments
  ├─ 对每个 image segment 调 articles.resolve_asset_path → Path
  └─ 构造完整 PublishPayload（image_path 为本地绝对路径）
                    ↓
toutiao.publish(payload, page, context)
  └─ 纯页面操作，不访问 DB，不解析路径
```

在 `publish_Runner` 组装完后记录一行 debug log，可立即判断发文内容是在解析阶段（`tiptap_Parser`）还是操作阶段（`toutiao.py`）丢失。

---

## 驱动设计：各平台独立实现填充逻辑

各平台编辑器 DOM 结构完全不同，不共用填充逻辑，避免平台间 bug 互相干扰。

**共享（`driver_Base.py`）：**
- `PublishPayload` / `BodySegment` 数据结构
- `PublishResult` / `PublishError` / `UserInputRequired` 异常类型
- 图片压缩/缩放工具函数（平台无关）

**各平台独立实现：**
- `fill_title()` / `fill_body()` / `upload_cover()` / `confirm_publish()`

---

## 各模块公开接口（`__init__.py` 导出清单）

### `articles/__init__.py`

| 来源 | 导出函数 |
|------|----------|
| `article_Crud` | `get_article` / `list_articles` / `create_article` / `update_article` / `delete_article` |
| `article_Crud` | `get_group` / `list_groups` / `create_group` / `delete_group` |
| `tiptap_Parser` | `parse_body_segments(article) → list[BodySegment]` |
| `tiptap_Parser` | `has_publishable_body(article) → bool` |
| `asset_Store` | `store_asset` / `get_asset` / `resolve_asset_path` / `delete_asset` |

### `accounts/__init__.py`

| 来源 | 导出函数 |
|------|----------|
| `account_Crud` | `get_account` / `list_accounts` / `create_account` / `delete_account` |
| `account_Crud` | `export_account` / `import_account` |
| `account_Auth` | `get_profile_dir(account) → Path` |
| `account_Auth` | `get_launch_options(account) → dict` |
| `browser_Session` | `get_or_create_session(account) → BrowserSessionHandle` |
| `browser_Session` | `stop_session(session_id)` / `associate_record` / `disassociate_record` |
| 类型 | `BrowserSessionHandle`（dataclass，含 page/context/session_id/novnc_url） |

### `tasks/__init__.py`

| 来源 | 导出函数 |
|------|----------|
| `task_Crud` | `get_task` / `list_tasks` / `create_task` / `cancel_task` |
| `task_Crud` | `list_records` / `list_logs` / `preview_assignment` |
| `task_Crud` | `manual_confirm_record` / `resolve_user_input_record` |
| `task_Executor` | `execute_task(db, task)` / `recover_stuck_records(db)` |

`publish_Runner` 和 `drivers/` 不对外导出，仅由 `task_Executor` 内部调用。

---

## 调用路径总览

```
routes/tasks.py
  → tasks.create_task / execute_task / list_tasks
      → task_Executor → publish_Runner
                            ├─ articles.parse_body_segments
                            ├─ articles.resolve_asset_path
                            ├─ accounts.get_profile_dir
                            ├─ accounts.get_or_create_session
                            └─ drivers/toutiao.publish(PublishPayload)
```

---

## 迁移策略

### 原则

迁移期间**只搬代码，不改逻辑**。每步完成后运行 `pytest`，全绿才继续。功能 bug 在结构清晰后集中排查。

### 迁移顺序

| 步骤 | 目标 | 来源文件 |
|------|------|----------|
| 1 | `shared/` | `services/errors.py` / `publish_diagnostics.py` / `feishu.py` |
| 2 | `articles/` | `services/articles.py` / `article_groups.py` / `assets.py` |
| 3 | `accounts/` | `services/accounts.py` / `browser_sessions.py` |
| 4 | `tasks/` | `services/tasks.py` / `publish_runner.py` / `drivers/` |
| 5 | 删除 | `server/app/services/`（全部迁移完后） |

### 每步操作模板

```
1. 创建新模块目录和文件，剪切旧代码
2. 写 __init__.py，只导出接口清单中的函数
3. 在旧文件顶部加兼容垫片（临时）：
       from server.app.modules.xxx import *  # noqa
4. 跑 pytest，全绿
5. 将 routes/ 中的 import 改为指向新模块
6. 删除垫片文件
```

### `tasks.py` 拆分子任务（最高风险）

| 子任务 | 目标文件 | 主要内容 |
|--------|----------|----------|
| 1 | `task_Crud.py` | 纯 DB 读写，无线程、无 Playwright |
| 2 | `task_Executor.py` | `execute_task` + ThreadPoolExecutor 调度 |
| 3 | `publish_Runner.py` | Playwright 编排 + `PublishPayload` 组装（唯一需要新写逻辑之处） |

### 迁移完成标志

- `server/app/services/` 目录已删除
- 所有 import 指向 `modules/` 或 `shared/`
- `pytest` 全绿

---

## 前端补齐（后端迁移完成后）

`web/src/api/client.ts` 按模块拆分为：

```
web/src/api/
  articles.ts
  accounts.ts
  tasks.ts
  assets.ts
```

与后端三模块对应，便于前端开发者按业务域定位 API 调用。
