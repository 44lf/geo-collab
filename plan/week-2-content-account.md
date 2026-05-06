# Week 2：文章、资源、分组、账号

目标：内容管理和账号授权可用，为 Week 3 的发布任务提供稳定数据。

## 交付物

- 核心数据模型和迁移。
- 资源上传、封面、正文图片访问。
- 文章 CRUD。
- TipTap 编辑器保存链路。
- 正文图片顺序同步。
- 文章分组。
- 头条号账号授权、校验、重新登录。
- 媒体矩阵和内容管理 UI 可用。

## W2-01 核心数据模型

- 优先级：P0
- 状态：Done
- 依赖：W1-02
- 覆盖模块：`BE-004`
- 目标：实现 MVP 需要的数据库表和关系。
- 范围：
  - `platforms`
  - `accounts`
  - `assets`
  - `articles`
  - `article_body_assets`
  - `article_groups`
  - `article_group_items`
  - `publish_tasks`
  - `publish_task_accounts`
  - `publish_records`
  - `task_logs`
- 验收：
  - Alembic migration 可生成所有表。
  - 外键、唯一约束、状态枚举有清晰定义。
- 验证结果：
  - 已新增 SQLAlchemy 模型：`Account`、`Asset`、`Article`、`ArticleBodyAsset`、`ArticleGroup`、`ArticleGroupItem`、`PublishTask`、`PublishTaskAccount`、`PublishRecord`、`TaskLog`。
  - 已新增迁移 `0002_create_core_models`，当前数据库版本为 `0002_create_core_models (head)`。
  - 已覆盖唯一约束：平台账号唯一、分组文章唯一、任务账号唯一、资源 `storage_key` 唯一。
  - 已覆盖状态约束：账号状态、文章状态、任务类型、任务状态、发布记录状态、日志级别。
  - `C:\Users\Administrator\miniconda3\envs\geo_xzpt\python.exe -m pytest server/tests`：4 passed。

## W2-02 资源上传和访问

- 优先级：P0
- 状态：Done
- 依赖：W1-03、W2-01
- 覆盖模块：`BE-005`
- 目标：封面和正文图片统一作为资源保存。
- 接口：
  - `POST /api/assets`
  - `GET /api/assets/{id}`
  - `GET /api/assets/{id}/meta`
- 验收：
  - 图片落盘到 `assets/YYYY/MM/`。
  - 返回资源 ID、访问 URL、MIME、大小、hash、宽高。
- 验证结果：
  - 已新增 `POST /api/assets`、`GET /api/assets/{id}`、`GET /api/assets/{id}/meta`。
  - 已新增统一存储服务，图片落盘到 `assets/YYYY/MM/<asset_id>.<ext>`。
  - 已记录资源 ID、原始文件名、扩展名、MIME、大小、SHA256、相对路径、图片宽高。
  - 已安装 `python-multipart` 支持 multipart 文件上传。
  - `C:\Users\Administrator\miniconda3\envs\geo_xzpt\python.exe -m pytest server/tests`：6 passed。

## W2-03 文章 CRUD 和正文图片顺序

- 优先级：P0
- 状态：Done
- 依赖：W2-01、W2-02
- 覆盖模块：`BE-006`、`BE-007`
- 目标：文章保存、读取、更新、删除可用，正文图片顺序以 TipTap 内容为准。
- 接口：
  - `GET /api/articles`
  - `POST /api/articles`
  - `GET /api/articles/{id}`
  - `PUT /api/articles/{id}`
  - `DELETE /api/articles/{id}`
  - `POST /api/articles/{id}/cover`
- 验收：
  - 可保存标题、作者、封面、TipTap JSON、HTML、纯文本。
  - 正文无图片时关联表为空。
  - 多张图片时 `position` 与正文出现顺序一致。
  - 封面不进入正文图片关联表。
- 验证结果：
  - 已新增 `GET /api/articles`、`POST /api/articles`、`GET /api/articles/{id}`、`PUT /api/articles/{id}`、`DELETE /api/articles/{id}`、`POST /api/articles/{id}/cover`。
  - 已保存标题、作者、封面、TipTap JSON、HTML、纯文本、字数、状态。
  - 已实现 TipTap JSON 图片节点扫描，支持 `assetId`、`asset_id`、`dataAssetId` 和 `/api/assets/{id}` src 解析。
  - 保存/更新文章时会重建 `article_body_assets`，`position` 按正文出现顺序生成。
  - 封面资源只写入 `cover_asset_id`，不会进入正文图片关联表。
  - `C:\Users\Administrator\miniconda3\envs\geo_xzpt\python.exe -m pytest server/tests`：10 passed。

## W2-04 文章分组

- 优先级：P0
- 状态：Done
- 依赖：W2-03
- 覆盖模块：`BE-008`
- 目标：支持任务创建前的文章分组管理。
- 接口：
  - `GET /api/article-groups`
  - `POST /api/article-groups`
  - `GET /api/article-groups/{id}`
  - `PUT /api/article-groups/{id}`
  - `DELETE /api/article-groups/{id}`
  - `PUT /api/article-groups/{id}/items`
- 验收：
  - 分组可创建、改名、删除。
  - 分组成员可添加、移除。
  - `sort_order` 保存稳定。
- 验证结果：
  - 已新增 `GET /api/article-groups`、`POST /api/article-groups`、`GET /api/article-groups/{id}`、`PUT /api/article-groups/{id}`、`DELETE /api/article-groups/{id}`、`PUT /api/article-groups/{id}/items`。
  - 分组支持创建、改名、删除、详情、列表。
  - 分组成员更新采用整组替换，支持添加/移除，`sort_order` 按传入值保存；未传时按数组顺序生成。
  - 已校验重复文章、缺失文章和重复分组名。
  - `C:\Users\Administrator\miniconda3\envs\geo_xzpt\python.exe -m pytest server/tests`：12 passed。

## W2-05 TipTap 编辑器和内容管理 UI

- 优先级：P0
- 状态：Done
- 依赖：W1-01、W2-03、W2-04
- 覆盖模块：`FE-002`、`FE-003`、`FE-004`
- 目标：产品同事能在 UI 中完成文章日常操作。
- 范围：
  - 主布局和 4 个导航入口。
  - 文章列表、搜索、新建、编辑、删除。
  - 标题、作者、封面、正文编辑。
  - 加粗、斜体、标题、列表、引用、链接、图片插入。
  - 多选文章创建分组。
  - 分组编辑入口。
- 验收：
  - 新建文章后可重新打开编辑。
  - 正文图片可保存并显示。
  - Word 粘贴基础格式可用；图片粘贴失败时允许手动上传。
- 验证结果：
  - 已接入 TipTap：`@tiptap/react`、`@tiptap/starter-kit`、`@tiptap/extension-image`、`@tiptap/extension-link`。
  - 内容管理页面已支持文章列表、搜索、新建、编辑、删除、保存。
  - 已支持标题、作者、状态、封面上传、正文图片上传插入、基础富文本工具栏。
  - 正文图片插入时会把 `assetId` 写入 TipTap image attrs，后端可同步 `article_body_assets`。
  - 已支持多选文章创建分组；点击已有分组可加载成员并更新/删除分组。
  - 已修复后端静态兜底：未知 `/api/*` 返回 404，不再被前端 `index.html` 吞掉。
  - `pnpm --filter @geo/web typecheck`：通过。
  - `pnpm --filter @geo/web build`：通过；Vite 提示 TipTap 进入主包后 chunk 超过 500KB，MVP 暂不拆包。
  - `C:\Users\Administrator\miniconda3\envs\geo_xzpt\python.exe -m pytest server/tests`：12 passed。

## W2-06 账号授权、校验、重新登录

- 优先级：P0
- 状态：Done
- 依赖：W1-04、W2-01
- 覆盖模块：`AU-003`、`AU-004`、`FE-005`
- 目标：把登录 Spike 产品化，并在媒体矩阵页面可操作。
- 接口：
  - `POST /api/accounts/toutiao/login`
  - `POST /api/accounts/{id}/check`
  - `POST /api/accounts/{id}/relogin`
  - `DELETE /api/accounts/{id}`
- 验收：
  - 添加账号后数据库有记录。
  - 浏览器状态保存到账号目录。
  - 登录有效时状态为 `valid`。
  - 失效时状态为 `expired` 或 `unknown`。
  - UI 能发起授权、校验、重登、删除。
- 验证结果：
  - 已新增 `GET /api/accounts`、`POST /api/accounts/toutiao/login`、`POST /api/accounts/{id}/check`、`POST /api/accounts/{id}/relogin`、`DELETE /api/accounts/{id}`。
  - 默认登录/校验/重登会打开本机 Chrome 可见窗口，使用 `browser_states/toutiao/<account_key>/profile` 和 `storage_state.json`。
  - API 支持 `use_browser=false` 复用已有 `storage_state.json`，便于导入已有登录态和自动化测试。
  - 已在媒体矩阵页面支持添加授权、复用状态、校验 Cookie、重新登录、删除账号。
  - 已用默认数据目录中的 `chrome-spike` 状态注册账号并返回 `valid`。
  - `C:\Users\Administrator\miniconda3\envs\geo_xzpt\python.exe -m pytest server/tests`：15 passed。
  - `pnpm --filter @geo/web typecheck`：通过。
  - `pnpm --filter @geo/web build`：通过。

## W2-07 授权包导出最小闭环

- 优先级：P1
- 状态：Done
- 依赖：W2-06
- 覆盖模块：`AU-005`
- 目标：导出账号元信息和浏览器状态，至少完成后端能力。
- 接口：
  - `POST /api/accounts/export`
- 验收：
  - 生成 zip。
  - zip 包含 manifest 和账号状态文件。
  - 不包含文章、图片、任务日志、完整数据库。
- 验证结果：
  - 已新增 `POST /api/accounts/export`，返回 zip 文件。
  - zip 包含 `manifest.json`、`accounts/<platform>-<id>/account.json` 和 `accounts/<platform>-<id>/storage_state.json`。
  - manifest 明确记录 `excluded_scopes`，不导出文章、图片、任务、日志或数据库。
  - 媒体矩阵 UI 已新增“导出授权包”按钮，可下载当前账号的 zip 授权包。
  - `C:\Users\Administrator\miniconda3\envs\geo_xzpt\python.exe -m pytest server/tests`：16 passed。
  - `pnpm --filter @geo/web typecheck`：通过。
  - `pnpm --filter @geo/web build`：通过。

## 本周完成标准

- 文章、图片、分组、账号授权这些 Week 3 前置数据必须可用。
- P0 任务全部 Done 或明确 Blocked。
- 授权包导出如未完成，必须在 `progress.md` 标记为 Week 4 补齐项。
