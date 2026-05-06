# Geo 协作平台 MVP 设计文档

## 1. 背景与目标

Geo 协作平台是一个面向内部提效的本地 Web 应用，首期服务约 5 人团队中的内容/产品同事。核心目标是把文章管理、头条号账号授权、发布任务编排和半自动发布流程集中到一个本地工作台中，降低重复发文和账号切换成本。

首期定位为 Windows 本地 MVP：用户不需要安装 Docker、conda、Python、Node、MySQL 等偏技术环境，只需运行一个 Windows exe，应用自动启动本地服务并打开浏览器。

后期方向是迁移到服务器部署，因此技术架构需要保留 Web 服务、数据库迁移、对象存储和账号授权导出的扩展路径。

## 2. 已确认需求边界

### 2.1 应用形态

- 首期采用本地 Web 一键启动形态。
- 交付 Windows exe。
- exe 启动 FastAPI 本地服务，并自动打开默认浏览器访问本地地址。
- React 前端构建为静态文件，由后端托管。
- 首期只支持 Windows 10/11。
- 首期不做应用自身登录，依赖本机 Windows 账户和文件权限。

### 2.2 平台范围

- 首期只支持头条号/今日头条真实发布。
- 网易号、搜狐号等平台不进入首期真实自动化范围。
- 数据模型保留多平台扩展能力。
- UI 首期建议只开放头条号，避免误以为其他平台可发布。

### 2.3 账号授权

- 首期不保存平台账号密码。
- 添加账号时打开可见浏览器窗口，由用户人工登录或扫码。
- 登录成功后保存浏览器状态/Cookie。
- 后续发布任务复用保存的登录状态。
- 支持校验 Cookie 是否仍有效。
- 支持重新登录刷新授权状态。
- Cookie/浏览器状态首期明文保存。
- 首期实现账号授权状态导出，后续用于迁移到服务器。

### 2.4 文章管理

- 文章编辑采用富文本为主。
- 富文本编辑器采用 TipTap/ProseMirror。
- 支持标题、作者、封面、正文。
- 正文支持基础富文本：段落、标题、加粗、斜体、列表、引用、链接、图片。
- 支持从 Word 复制粘贴，尽量保留基础格式和图片。
- Markdown 作为导入/粘贴能力，转为富文本；首期不做完整双向互转。
- 文章正文主存 TipTap JSON，同时保存 HTML 渲染结果和纯文本摘要。
- 封面和正文图片需要在业务模型中区分。
- 正文图片允许 0 张，也需要保留图片出现顺序。

### 2.5 文章分组

- 分组可创建、改名、删除。
- 分组内可添加/移除文章。
- 分组内文章有顺序字段，用于发布轮询。
- 首期可以不做拖拽排序，但模型保留 `sort_order`。
- 分组用于任务创建时批量选择文章。

### 2.6 发布任务

- 首期任务创建后手动执行，不做定时发布。
- 支持两种任务模式：
  - 单篇文章 + 单个头条号账号。
  - 文章分组 + 多个头条号账号顺序轮询。
- 不支持一篇文章在一个任务中发布到多个账号。
- 分组轮询规则：文章 1 -> 账号 1，文章 2 -> 账号 2，文章 3 -> 账号 3，文章 4 -> 账号 1。
- 任务内部串行执行。
- 单篇发布失败不阻塞后续文章，任务继续执行。
- 支持失败记录一键重试。
- 任务执行过程展示实时日志。
- 支持手动终止任务。

### 2.7 发布自动化

- 首期采用可见浏览器半自动模式。
- 系统自动打开头条号发布页。
- 系统自动填充标题、正文和封面。
- 系统停在最终发布前，不自动点击“发布”按钮。
- 用户人工检查并点击最终发布。
- 用户点击发布后，系统尝试自动检测发布结果。
- 自动检测失败时，提供人工兜底确认：
  - 标记成功并填写发布链接。
  - 标记失败并填写失败原因。
  - 稍后处理。

### 2.8 导出能力

- 首期只实现账号授权状态导出。
- 导出内容包括账号元信息、平台标识、Cookie/storage_state/profile、导出时间和应用版本。
- 导出形式建议为 zip 包。
- 首期导出不包含文章、图片、任务日志、完整 SQLite 数据库。
- 设计上保留完整工作区导出能力：数据库 + assets + cookies + logs。

## 3. 技术选型

### 3.1 总体选型

| 模块 | 选型 | 说明 |
| --- | --- | --- |
| 后端 | FastAPI | 与后期服务器形态一致，用户熟悉 Python/FastAPI |
| ORM | SQLAlchemy 2.x | 屏蔽 SQLite/MySQL 差异，保留迁移路径 |
| 迁移 | Alembic | 管理数据库 schema 演进 |
| 本地数据库 | SQLite | 本地免安装，适合 MVP |
| 前端 | React + Vite + TypeScript | 可维护性强，适合复杂工作台 |
| 富文本 | TipTap/ProseMirror | 支持结构化内容、图片节点、HTML/JSON 存储 |
| 数据请求 | TanStack Query 或轻量 fetch 封装 | 管理异步请求、缓存和刷新 |
| 图标 | lucide-react | 与现代工作台 UI 匹配 |
| 自动化 | Playwright Python | 与 FastAPI/Python 栈一致，适合浏览器状态复用 |
| 本地文件 | LocalStorageService | 后期可替换为 MinIOStorage |
| 打包 | PyInstaller 或 Nuitka | 生成 Windows exe |

### 3.2 推荐架构原因

FastAPI + React 是服务器迁移友好的组合。首期虽然是本地应用，但仍然以 HTTP API 为边界，后期迁到服务器时主要调整部署、数据库和文件存储，不需要重写核心业务。

SQLite 满足本地免安装要求。通过 SQLAlchemy/Alembic 管理模型和迁移，后期切换 MySQL 时成本可控。

TipTap 适合保存结构化富文本。发布自动化不应只依赖最终 HTML 字符串，而应该能遍历文章中的标题、段落、列表、图片等节点，转换为头条号编辑器可接受的操作。

Playwright 负责登录状态保存、页面填充和结果检测。首期采用可见浏览器半自动模式，降低验证码、安全验证、平台弹窗带来的失败风险。

## 4. 系统架构

### 4.1 逻辑结构

```text
Windows exe
  |
  |-- FastAPI 本地服务
  |     |-- REST API
  |     |-- 静态前端托管
  |     |-- 文章/分组/任务业务服务
  |     |-- 资源文件服务
  |     |-- Playwright 发布引擎
  |     |-- 导出服务
  |
  |-- React 前端
  |     |-- 内容管理
  |     |-- 媒体矩阵
  |     |-- 分发任务
  |     |-- 系统状态
  |
  |-- 本地数据目录
        |-- geo.db
        |-- assets/
        |-- browser_states/
        |-- logs/
        |-- exports/
```

### 4.2 本地目录建议

```text
GeoAppData/
  geo.db
  assets/
    2026/
      05/
        <asset_id>.<ext>
  browser_states/
    toutiao/
      <account_id>/
        storage_state.json
        profile/
  logs/
    app.log
    tasks/
      <task_id>.log
  exports/
    geo-auth-export-YYYYMMDD-HHMMSS.zip
```

数据目录具体位置建议优先使用 Windows 用户数据目录，例如 `%LOCALAPPDATA%/GeoCollab`。如果需要便于迁移，也可以在 exe 同级目录提供 portable 模式。

## 5. 核心数据模型草案

### 5.1 platforms

平台表，首期只内置头条号。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | integer | 主键 |
| code | string | `toutiao` |
| name | string | 头条号 |
| base_url | string | 平台后台地址 |
| enabled | boolean | 是否启用 |
| created_at | datetime | 创建时间 |

### 5.2 accounts

平台账号表。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | integer | 主键 |
| platform_id | integer | 平台 ID |
| display_name | string | 账号昵称 |
| platform_user_id | string | 平台侧账号 ID，可为空 |
| status | string | `valid` / `expired` / `unknown` |
| last_checked_at | datetime | 最近校验时间 |
| last_login_at | datetime | 最近登录时间 |
| state_path | string | Cookie/storage_state/profile 路径 |
| note | text | 备注 |
| created_at | datetime | 创建时间 |
| updated_at | datetime | 更新时间 |

### 5.3 assets

统一资源文件表，只描述文件本身。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | string | 资源 ID，建议 UUID |
| filename | string | 原始文件名 |
| ext | string | 扩展名 |
| mime_type | string | MIME 类型 |
| size | integer | 文件大小 |
| sha256 | string | 文件哈希，用于去重/校验 |
| storage_key | string | 本地相对路径 |
| width | integer | 图片宽度，可为空 |
| height | integer | 图片高度，可为空 |
| created_at | datetime | 创建时间 |

### 5.4 articles

文章表。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | integer | 主键 |
| title | string | 标题 |
| author | string | 作者 |
| cover_asset_id | string | 封面资源 ID，可为空 |
| content_json | json/text | TipTap JSON |
| content_html | text | 渲染 HTML |
| plain_text | text | 纯文本内容，用于搜索/摘要 |
| word_count | integer | 字数 |
| status | string | `draft` / `ready` / `archived` |
| created_at | datetime | 创建时间 |
| updated_at | datetime | 更新时间 |

### 5.5 article_body_assets

正文图片关联表。封面不放在这里。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | integer | 主键 |
| article_id | integer | 文章 ID |
| asset_id | string | 图片资源 ID |
| position | integer | 正文图片出现顺序，从 0 开始 |
| editor_node_id | string | TipTap 图片节点 ID，可为空 |
| created_at | datetime | 创建时间 |

说明：

- 正文图片可以为 0 张。
- 正文图片顺序以 TipTap 内容中的图片节点顺序为准。
- 关联表用于发布前校验、资源清理、导出和快速查询。

### 5.6 article_groups

文章分组表。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | integer | 主键 |
| name | string | 分组名称 |
| description | text | 备注，可为空 |
| created_at | datetime | 创建时间 |
| updated_at | datetime | 更新时间 |

### 5.7 article_group_items

分组文章明细。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | integer | 主键 |
| group_id | integer | 分组 ID |
| article_id | integer | 文章 ID |
| sort_order | integer | 分组内顺序 |
| created_at | datetime | 创建时间 |

### 5.8 publish_tasks

发布任务表。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | integer | 主键 |
| name | string | 任务名称 |
| task_type | string | `single` / `group_round_robin` |
| status | string | `pending` / `running` / `succeeded` / `partial_failed` / `failed` / `cancelled` |
| platform_id | integer | 首期为头条号 |
| article_id | integer | 单篇任务使用 |
| group_id | integer | 分组任务使用 |
| stop_before_publish | boolean | 首期固定为 true |
| created_at | datetime | 创建时间 |
| started_at | datetime | 开始时间 |
| finished_at | datetime | 结束时间 |

### 5.9 publish_task_accounts

任务选择的账号。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | integer | 主键 |
| task_id | integer | 任务 ID |
| account_id | integer | 账号 ID |
| sort_order | integer | 轮询顺序 |

### 5.10 publish_records

发布记录表。文章发布状态由此聚合得到。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | integer | 主键 |
| task_id | integer | 任务 ID |
| article_id | integer | 文章 ID |
| platform_id | integer | 平台 ID |
| account_id | integer | 账号 ID |
| status | string | `pending` / `running` / `waiting_manual_publish` / `succeeded` / `failed` / `cancelled` |
| publish_url | string | 发布链接，可为空 |
| error_message | text | 失败原因 |
| retry_of_record_id | integer | 重试来源记录，可为空 |
| started_at | datetime | 开始时间 |
| finished_at | datetime | 结束时间 |

### 5.11 task_logs

任务日志表。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | integer | 主键 |
| task_id | integer | 任务 ID |
| record_id | integer | 发布记录 ID，可为空 |
| level | string | `info` / `warn` / `error` |
| message | text | 日志内容 |
| screenshot_asset_id | string | 截图资源 ID，可为空 |
| created_at | datetime | 创建时间 |

## 6. 主要业务流程

### 6.1 添加头条号账号

```text
用户点击添加授权
  -> 选择平台：头条号
  -> 后端启动可见 Chromium
  -> 用户人工登录/扫码
  -> 系统检测登录成功
  -> 保存 storage_state/profile
  -> 创建或更新 accounts 记录
  -> 返回账号有效状态
```

首期登录成功检测可以访问头条号后台首页，检查是否出现账号昵称、发布入口或其他稳定登录态标识。

### 6.2 校验账号状态

```text
用户点击校验 Cookie
  -> Playwright 加载账号浏览器状态
  -> 访问头条号后台
  -> 判断是否仍登录
  -> 更新账号状态 valid/expired/unknown
  -> 记录日志
```

### 6.3 编辑文章

```text
用户新建/编辑文章
  -> 输入标题、作者
  -> 上传或更换封面
  -> 使用 TipTap 编辑正文
  -> 粘贴 Word/Markdown/图片
  -> 后端保存图片资源
  -> 保存文章 content_json/content_html/plain_text
  -> 同步 article_body_assets 正文图片顺序
```

### 6.4 创建单篇任务

```text
选择单篇文章
  -> 选择一个头条号账号
  -> 生成 publish_task
  -> 生成一条 publish_record
  -> 状态 pending
```

### 6.5 创建分组轮询任务

```text
选择文章分组
  -> 选择多个头条号账号
  -> 按分组文章 sort_order 排序
  -> 按账号 sort_order 顺序轮询
  -> 每篇文章生成一条 publish_record
  -> 状态 pending
```

示例：

```text
文章 A, B, C, D
账号 1, 2, 3

A -> 账号 1
B -> 账号 2
C -> 账号 3
D -> 账号 1
```

### 6.6 执行任务

```text
用户点击执行
  -> 任务进入 running
  -> 串行处理 publish_records
  -> 对每条记录打开头条号发布页
  -> 自动填标题、正文、封面
  -> 状态变为 waiting_manual_publish
  -> 用户人工点击最终发布
  -> 系统尝试自动检测结果
  -> 检测失败则让用户手动确认
  -> 成功/失败写入 publish_record
  -> 当前记录失败也继续后续记录
  -> 所有记录结束后聚合任务状态
```

### 6.7 失败重试

```text
用户点击重试失败记录
  -> 为失败记录创建新的 publish_record
  -> retry_of_record_id 指向原记录
  -> 执行同样发布流程
  -> 原失败记录保留
```

## 7. API 草案

### 7.1 文章

```text
GET    /api/articles
POST   /api/articles
GET    /api/articles/{id}
PUT    /api/articles/{id}
DELETE /api/articles/{id}
POST   /api/articles/{id}/cover
GET    /api/articles/{id}/publish-summary
```

### 7.2 资源

```text
POST /api/assets
GET  /api/assets/{id}
GET  /api/assets/{id}/meta
```

### 7.3 分组

```text
GET    /api/article-groups
POST   /api/article-groups
GET    /api/article-groups/{id}
PUT    /api/article-groups/{id}
DELETE /api/article-groups/{id}
PUT    /api/article-groups/{id}/items
```

### 7.4 账号

```text
GET    /api/accounts
POST   /api/accounts/toutiao/login
POST   /api/accounts/{id}/check
POST   /api/accounts/{id}/relogin
DELETE /api/accounts/{id}
POST   /api/accounts/export
```

### 7.5 任务

```text
GET    /api/tasks
POST   /api/tasks
GET    /api/tasks/{id}
POST   /api/tasks/{id}/execute
POST   /api/tasks/{id}/cancel
GET    /api/tasks/{id}/logs
GET    /api/tasks/{id}/records
POST   /api/publish-records/{id}/retry
POST   /api/publish-records/{id}/manual-confirm
```

### 7.6 系统状态

```text
GET /api/system/status
GET /api/system/events
```

实时日志可以先用轮询实现。后续如需要更顺滑的执行控制台，可升级为 WebSocket 或 Server-Sent Events。

## 8. 发布自动化设计

### 8.1 自动化模块拆分

```text
publisher/
  base.py
  toutiao.py
  browser_state.py
  result_detector.py
```

建议定义平台发布器接口：

```python
class Publisher:
    platform_code: str

    async def check_login(self, account) -> LoginCheckResult:
        ...

    async def open_login(self, account) -> LoginResult:
        ...

    async def prepare_publish(self, record) -> PreparePublishResult:
        ...

    async def detect_publish_result(self, record) -> PublishDetectResult:
        ...
```

首期只实现 `ToutiaoPublisher`。

### 8.2 填充策略

优先按稳定选择器操作。如果头条号编辑器选择器不稳定，需要封装多候选定位策略，并在失败时截图和记录当前 URL。

标题、正文、封面建议分别独立步骤：

```text
1. 加载账号状态
2. 打开发布页
3. 填标题
4. 填正文
5. 上传封面
6. 等待平台预处理完成
7. 停在发布前
8. 等待用户点击发布
9. 检测结果或人工确认
```

### 8.3 人工介入

遇到以下情况，任务进入等待人工处理状态：

- 登录失效。
- 扫码/验证码/安全验证。
- 页面出现无法识别弹窗。
- 封面裁剪或格式校验需要确认。
- 自动检测发布结果失败。

前端需要展示当前记录、账号、文章、浏览器操作提示，并提供：

- 继续检测。
- 标记成功。
- 标记失败。
- 终止任务。

## 9. 前端页面设计

前端沿用 `demo-editorial.html` 的 4 个主导航：

- 内容管理
- 媒体矩阵
- 分发引擎
- 系统状态

### 9.1 内容管理

核心能力：

- 文章列表。
- 搜索文章标题/作者。
- 新建图文。
- 编辑文章。
- 删除文章。
- 上传封面。
- 正文图片插入。
- 多选文章创建分组。
- 查看文章发布聚合状态。

文章发布状态从 `publish_records` 聚合：

- 未发布。
- 已发布。
- 部分失败。
- 发布失败。
- 待人工确认。

### 9.2 媒体矩阵

首期只展示头条号账号。

核心能力：

- 添加授权。
- 校验 Cookie。
- 重新登录。
- 移除账号。
- 导出账号授权包。
- 展示账号状态、最近校验时间、备注。

### 9.3 分发引擎

核心能力：

- 新建单篇任务。
- 新建分组轮询任务。
- 选择账号。
- 查看分配预览。
- 手动执行。
- 执行中看日志。
- 查看记录级结果。
- 失败项重试。
- 手动终止。

### 9.4 系统状态

核心能力：

- 本地服务状态。
- 数据库状态。
- Playwright 浏览器状态。
- 文章数、账号数、任务数。
- 最近事件。
- 应用版本和数据目录。

## 10. 打包与启动

### 10.1 启动流程

```text
用户双击 GeoCollab.exe
  -> 初始化数据目录
  -> 执行数据库迁移
  -> 检查 Playwright 浏览器依赖
  -> 启动 FastAPI
  -> 自动选择可用端口
  -> 打开默认浏览器
```

### 10.2 打包注意事项

- 前端先执行 Vite build，输出静态文件。
- FastAPI 挂载静态文件目录。
- PyInstaller/Nuitka 打包 Python 后端和静态文件。
- Playwright Chromium 体积较大，需要确定是随包分发还是首次初始化下载。
- 为了产品同事友好，建议随包分发或提供完整离线包。
- 日志和数据库不要写入 exe 安装目录，优先写用户数据目录。

## 11. 风险与约束

### 11.1 平台自动化风险

头条号页面结构、风控策略、登录流程可能变化。首期使用半自动模式，并保留人工介入和人工确认，降低发布失败和误发风险。

### 11.2 Cookie 明文风险

首期 Cookie/浏览器状态明文保存，等同登录凭据。需要明确：

- 不要把数据目录放入共享盘或网盘。
- 不要把导出的授权包发给无关人员。
- 迁移服务器前应补充加密、权限控制和操作审计。

### 11.3 富文本格式风险

Word 粘贴、Markdown 导入和头条号编辑器之间可能存在格式损耗。MVP 目标是保留基础结构和图片，不承诺复杂排版完全一致。

### 11.4 打包体积风险

Playwright Chromium 会显著增加安装包体积。首期为了免安装体验可以接受较大体积，后续可优化为共享浏览器或按需初始化。

## 12. 推荐实施阶段

### 阶段 1：工程骨架

- FastAPI 项目结构。
- React + Vite + TypeScript 前端。
- SQLite + SQLAlchemy + Alembic。
- 静态文件托管。
- 本地数据目录初始化。

### 阶段 2：文章与资源

- 文章 CRUD。
- TipTap 编辑器。
- 封面上传。
- 正文图片上传和顺序建模。
- 分组 CRUD。

### 阶段 3：账号授权

- 头条号人工登录。
- 保存 storage_state/profile。
- Cookie 校验。
- 重新登录。
- 授权包导出。

### 阶段 4：任务与日志

- 单篇任务。
- 分组轮询任务。
- 发布记录。
- 任务日志。
- 失败重试。

### 阶段 5：头条号半自动发布

- 打开发布页。
- 填标题。
- 填正文。
- 上传封面。
- 停在发布前。
- 结果自动检测和人工兜底确认。

### 阶段 6：Windows 打包

- 前端构建整合。
- PyInstaller/Nuitka 打包。
- Playwright 依赖处理。
- 一键启动和自动打开浏览器。
- 在干净 Windows 环境验收。

## 13. 后续扩展预留

- 服务器部署。
- MySQL 替换 SQLite。
- MinIO 替换本地 assets。
- 多用户登录和权限。
- Cookie 加密和审计。
- 更多平台发布器。
- 定时发布。
- 自动点击最终发布。
- 分组拖拽排序。
- 完整工作区导出/导入。
- WebSocket 实时任务日志。

## 14. MVP 非目标

首期不做以下能力：

- 应用自身登录。
- 多人协作编辑。
- 服务器部署。
- Docker/conda 安装流程。
- 头条号账号密码自动登录。
- 验证码/短信自动识别。
- 自动点击最终发布。
- 多平台真实发布。
- 一篇文章批量发布到多个账号。
- 定时发布。
- 复杂 Word 排版完全保真。
- Cookie 加密保存。

