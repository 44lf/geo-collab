# GEO 主链路收敛与历史能力清理设计

日期：2026-07-27

状态：审计定稿；Release A 待实施；Release B 在观察期完成前保持阻断

## 1. 背景

GEO 当前同时存在三类内容生成入口：

- `/agents`：Pipeline / 智能体工作流；
- `/ai`：问题池驱动的方案生文；
- MCP Loop：由外部 Agent 驱动的生成、配图、评审和发布闭环。

Pipeline 已成为站内主生产链路，MCP Loop 仍是正式的外部自动化入口。方案生文没有新的
运行记录，但 `/ai` 页面仍会读取方案、历史运行、问题池和模型列表；问题池的创建、飞书
绑定、同步和维护也仍只存在于该页面。

代码审计进一步确认，Pipeline 和其他保留模块仍直接或间接依赖若干“方案模块”符号：

- `ai_compose`、`ai_generate` import `scheme_executor._pick_valid_template`；
- 问题类型 API 调用 `scheme_service.question_types()`；
- Pipeline 模型下拉调用 `scheme_router.py` 中的 `/ai-engines`、`/format-engines`；
- `main.py` import、挂载、恢复并注入 `scheme_router` / `scheme_executor`；
- `Article.tags` ORM 关系仍动态引用拟删的 `article_tags` / `tags`；
- weekly-report Loop 仍调用模板表现 MCP tool；
- Alembic、运维脚本和测试建库入口仍 import 旧 `modules.skills.models`。

因此，本次不是目录级删除。方案采用两个可独立验收的发布：

1. Release A 迁移共享能力、搬迁问题源管理、停止方案写入并建立观察窗口；
2. Release B 先归档和演练，再删除旧 UI、代码、ORM 对象、表和列。

## 2. 目标

### 2.1 产品目标

- 站内只保留 Pipeline / 智能体作为正式生成入口。
- MCP Loop 继续作为正式的外部 Agent 自动化入口。
- `/ai` 退役，但不影响问题池、Pipeline、MCP、文章、技能安装或发布。
- 问题池在“智能体管理”中成为正式的“问题源管理”能力。
- 保留游戏库、小红书、视频、飞书、公众号、质量参考、自动评审、内容和发布链路。
- 保留现有问题池、模型列表、模板表现、技能安装等公开契约的可用性。

### 2.2 工程目标

- Pipeline 不再 import 任何 `scheme_*` 模块。
- 删除方案生文、旧 `/generation/sessions`、旧 Skill 和旧消费队列代码及数据表。
- 删除未接线的旧图片库页面，但保留图片库 API、类型、模型和活跃消费者。
- 将模板表现统计从 stub 改为真实实现，不删除现有 HTTP/MCP 能力。
- 删除旧 loop bundle 存储，但保留已经转读新技能库的兼容只读路径。
- 删除每个目标前都有代码依赖证明、生产调用证据、数据归档和迁移演练。
- 当前代码、ORM metadata、数据库、路由、MCP catalog 和文档保持一致。

## 3. 非目标

- 不重命名 `/api/generation/question-pools/*`。
- 不修改或重写历史 Alembic migration，只新增向前 migration。
- 不删除 `QuestionPool`、`QuestionItem`、飞书同步或问题池自动同步 scheduler。
- 不删除 `article_writer`、converter、模型解析、提示词解析或 Markdown 清洗。
- 不删除 `image-library` API、`StockCategory`、`StockImage` 或其前端客户端。
- 不删除 `skill_library_skills`、`skill_library_versions` 或官方 `goal` skill。
- 不删除 `/api/mcp/loop-skill-bundle/*` 兼容只读路由；它们已读取新技能库。
- 不删除 `get_template_performance`、`get_account_performance` 或 metrics 回写工具。
- 不把 Pipeline 与 MCP 合并为同一运行时。
- 不在本次修复 dev 全库的历史 schema 漂移；只把它作为迁移演练前置事项隔离处理。

## 4. 审计事实

### 4.1 Pipeline 已覆盖方案生文的主要业务结果

Pipeline 的 `question_source` 已支持选择问题池、问题类型、具体问题、每类模板和文章数。
下游 `ai_compose`、`ai_illustrate`、`to_review`、`distribute` 可以显式完成生文、配图、
送审和分发。

方案保存时冻结问题文本快照；Pipeline 运行时读取当前 `QuestionItem`。本次接受语义差异，
不把历史方案自动转换为 Pipeline，只做离线归档。

### 4.2 共享依赖不能随方案删除

必须在 Release A 迁移的共享能力：

| 能力 | 当前位置 | 保留消费者 | 目标位置 |
|---|---|---|---|
| 模板候选选择 | `scheme_executor._pick_valid_template` | `ai_compose`、`ai_generate` | `ai_generation/runtime_templates.py` |
| 问题类型聚合 | `scheme_service.question_types` | 问题类型 HTTP API、Pipeline 编辑器 | `question_bank.question_types` |
| 写作模型列表 | `scheme_router:/ai-engines` | Pipeline 编辑器 | `ai_generation/router.py` |
| 格式模型列表 | `scheme_router:/format-engines` | Pipeline 编辑器 | `ai_generation/router.py` |
| `AiEngineRead` | 方案 DTO 区 | 两个保留模型端点 | 继续保留为共享 DTO |

模型列表 URL 保持不变，不制造无收益的前端和外部契约迁移。

### 4.3 Article 标签表存在 ORM 运行时依赖

`tags`、`article_tags` 当前数据为空，但不是“直接 drop 即安全”：

- `Tag`、`ArticleTag` 仍在 `articles/models.py` 注册；
- `Article.tags` 使用 `secondary="article_tags"` 和 `lazy="selectin"`；
- `articles/services/feed.py` 三处构造 `lazyload(Article.tags)`。

Release B 必须在同一代码发布中删除 ORM 类、关系、query option 和注释，再 drop 表。
游戏标签、图片标签、视频标签和 Pipeline 标签是其他表或 JSON 字段，不属于本次目标。

### 4.4 模板表现不是可无影响删除的 tool

`claude-loops/weekly-report-loop.md` 仍调用 `get_template_performance`。直接删除会破坏可执行
Loop、改变 MCP 工具数并触发注册守卫。

`articles.source_template_id` 已由 Pipeline 和 MCP 写入，因此本次把 stub 改为真实聚合：

- 时间窗口按 `Article.created_at >= utcnow() - window_days`；
- 按 `Article.source_template_id == template_id` 过滤；
- `article_count` 为文章数；
- `avg_views`、`avg_likes` 从非空 `Article.metrics` 聚合；
- `approval_rate` 为 `review_status == "approved"` 的文章占比；
- 无文章时平均值和通过率返回 `null`。

HTTP 路径、MCP tool 名、catalog 项和 `MCP_TOOLS_COUNT=39` 保持不变。

### 4.5 旧 Skill 和 loop bundle 的真实边界

旧 `modules/skills/*` 已无业务路由，但 Alembic、`seed_users`、`encrypt_secrets`、
`repair_article_escaped_quotes` 和测试建库入口仍做 ORM 注册 import。删除模块时必须同步
移除这些 import。

旧 `loop_skill_bundle_versions` 的线上路由已经转读 `skill_library_*`。因此：

- 删除 `LoopSkillBundleVersion` ORM 类、`versions_service.py`、旧表和旧测试；
- 改写 `seed_skill_library.py`，全新库直接从 `build_bundle()` 创建官方 `goal`；
- 保留 `loop_skills/router.py` 的兼容只读路由；
- 保留 `skill_service.py`、`skill_router.py`、`Skill`、`SkillVersion`。

### 4.6 dev 与生产数据库对账

用户指定 dev 的物理关系作为删除设计真值；生产只用于兼容校验。

目标对象在 dev 与生产的外键图一致，唯一结构差异是：

- dev 不存在 `loop_skill_bundle_versions`；
- 生产存在该表，但精确行数为 0。

关键存量：

| 对象 | dev | 生产 |
|---|---:|---:|
| `generation_schemes` | 13 | 13 |
| `generation_scheme_lines` | 143 | 143 |
| `generation_scheme_line_questions` | 464 | 464 |
| `generation_scheme_runs` | 17 | 17 |
| `generation_scheme_run_tasks` | 154 | 154 |
| `generation_sessions` | 7 | 7 |
| `skills` | 6 | 6 |
| `category_usages` | 0 | 0 |
| `tags` | 0 | 0 |
| `article_tags` | 0 | 0 |
| `loop_skill_bundle_versions` | 不存在 | 0 |

两库均无活跃方案 run：`done=15`、`failed=2`。两库都有 5 个 `pending` task，其父
`run_id=14` 已失败；归档必须原样保留该历史不一致，不把它误判为正在执行。

所有已声明 FK 和 JSON/Text ID 引用均闭合，没有保留表通过 FK 指向拟删表。两库都没有
相关视图、触发器、存储过程或事件。

### 4.7 问题状态的确定语义变化

dev：100 条问题全部 active，其中 78 pending、22 consumed。

生产：150 条问题全部 active，其中 128 pending、22 consumed。

Pipeline 当前已按 `source_active=True` 读取全部 active 问题；HTTP/MCP 默认仍按旧
`status="pending"` 过滤。切换后，dev 默认可见数从 78 变 100，生产从 128 变 150。
这不是 Pipeline 行为变化，而是 HTTP/MCP 与 Pipeline 语义对齐。22 条历史 consumed 问题
会重新成为外部 Loop 候选，本次明确接受该结果。

### 4.8 生产只读校验证据

生产 ECS：`47.115.134.13`，主机名与部署文档一致。校验时：

- 应用镜像 `geo-collab-server:1.0.35`；
- 数据库 `geo_collab`；
- Alembic 为仓库当前 head `0072_planb_discovery_patrol`；
- 官方 `goal` skill 为 `v2`，5 个文件，ZIP 条目与文件清单一致，逐文件 SHA-256 通过；
- 方案最后一次 run 创建于 `2026-06-15 02:26:42`；
- 最近 7 天无方案写审计；最近 30 天只有 7 次 `generation_scheme.patch`，最后一次
  `2026-07-02 09:27:44`，没有新 run。

当前 nginx 容器仅覆盖最近约 3 天日志。该窗口内有：

- `GET /schemes` 94 次；
- `GET /scheme-runs` 2 次；
- `GET /ai-engines` 11 次；
- `GET /format-engines` 2 次；
- `GET /question-pools` 11 次；
- Pipeline GET 34 次、POST 8 次；
- 没有方案写请求。

这证明读流量仍存在，Release A 必须先隐藏 `/ai` 并保留共享模型/问题池端点；当前证据不能
替代 Release A 上线后的 7 天观察期。

### 4.9 dev Alembic 漂移不能盲目 stamp

dev 的 `alembic_version` 为仓库不存在的 `0067_game_cull_and_manual`，不能直接运行
`alembic current`。与生产 head 的全表 schema 指纹相比，dev 还存在：

- `loop_skill_bundle_versions`、`xhs_render_jobs` 缺表；
- `articles`、`game_ingest_config`、`prompt_templates`、`report_events`、`video_jobs`
  结构指纹不同。

因此禁止把 dev 直接 stamp 到 head。本次 migration 在全新 MySQL 临时库和生产 schema
克隆上演练；dev 的全库修复作为独立运维事项处理。删除目标的关系和数据对账仍以 dev
物理结构为基线。

## 5. 受保护契约

以下能力在 Release A、Release B 前后必须保持：

| 契约 | 保持要求 |
|---|---|
| `/api/generation/question-pools/*` | 路径、鉴权和 CRUD/同步能力不变 |
| `/question-pools/{id}/question-types` | active 聚合、顺序和 DTO 不变 |
| `/api/generation/ai-engines` | URL 和 `{label, model}` 不变 |
| `/api/generation/format-engines` | URL 和 `{label, model}` 不变 |
| MCP `list_question_pools` / `list_question_items` | tool 名和公开参数不变 |
| MCP `save_article` | 仍可按 question id 读取文本和分类 |
| MCP `get_template_performance` | tool 名和返回字段不变，数据改为真实聚合 |
| MCP `get_account_performance` / `record_publish_metrics` | 行为不变 |
| `/api/mcp/loop-skill-bundle/*` | 兼容只读路径继续从新技能库返回 |
| `/api/mcp/skills/*` | 新技能库全部能力不变 |
| Article CRUD、feed、MCP get/save | 不再访问旧标签表，响应不变 |
| Pipeline 节点注册表 | `question_source`、`ai_compose`、`ai_generate` 等全部保留 |
| image-library API | 路径、类型和活跃消费者不变 |

## 6. Release A：解耦与停用

Release A 不删除表或历史数据。

### 6.1 后端共享能力

- 新建 `ai_generation/runtime_templates.py`，承载 `pick_valid_template()`。
- Pipeline 两条节点路径改为只 import 共享 runtime。
- `question_types()` 移入 `question_bank.py`。
- `/ai-engines`、`/format-engines` 移入保留的 `ai_generation/router.py`。
- 增加 AST 静态测试，禁止 Pipeline import `scheme_router/service/executor`。
- 保留两个模型 URL 和 `AiEngineRead`。

### 6.2 问题池语义先行

在数据库列仍存在时先切换到最终语义并观察：

- `source_active` 成为唯一可用性真值；
- `pending_count` 统计 active；
- HTTP `status=pending` 或省略返回 active；
- `status=all` 返回全部；
- `status=consumed` 返回空；
- 其他值返回 400；
- `QuestionItemRead` 显式投影 `status="pending"`、`article_id=null`；
- MCP tool 不增加 `status` 参数。

Release B 只删除不再被读取的列，不再引入新的 API 行为变化。

### 6.3 前端问题源管理

- 新建 `api/question-pools.ts`，只承载问题池、items 和 question-types。
- 新建 `api/generation-engines.ts`，承载两个共享模型列表。
- `PipelineEditor` 不再 import `api/ai-generation.ts`。
- 将 `PoolManagerModal` 搬到 `features/pipelines/question-pools/` 并改名
  `QuestionPoolManagerModal`。
- 管理 UI 覆盖创建、飞书重绑、重命名、手工同步、自动同步开关和 admin 删除。
- `QuestionPoolRead` / 前端 `QuestionPool` 增加 `auto_sync_enabled`。
- 智能体顶栏和问题源节点都提供管理/同步入口。
- `/ai` 从导航移除，并显式重定向 `/agents`。
- Release A 暂时保留旧方案组件源码，但路由不可达。
- 保留 Pipeline 和迁移后 Modal 使用的 `scheme*` / `ai*` 共享样式。

### 6.4 停止方案写入

以下接口统一返回 `410 Gone`，不产生审计或数据库写入：

- POST `/schemes`
- PUT `/schemes/{id}`
- PATCH `/schemes/{id}`
- DELETE `/schemes/{id}`
- POST `/schemes/{id}/runs`

列表、详情和运行历史 GET 暂时保留只读。旧 POST `/sessions` 的 410 文案改为引导
Pipeline，不再引导即将退役的方案流。

### 6.5 消灭模板表现 stub

保留 HTTP、MCP 和 weekly-report Loop，按 4.4 的确定口径实现真实聚合。该改动属于
Release A，因为它消除 Release B 删除代码时对外部 Loop 的误伤风险。

### 6.6 Release A 门禁

- 没有 `pending/running` 方案 run。
- Pipeline 代码不再 import `scheme_*`。
- 问题池管理所有操作可从 `/agents` 完成。
- 模型列表 URL 在移动后仍返回相同 DTO。
- active/all/consumed/非法 status 契约测试通过。
- Article、技能库、MCP、Pipeline、前后端门禁通过。

## 7. Release A 观察期

生产上线后观察至少 7 个连续自然日，并同时满足：

- 方案写接口没有真实调用；410 数量单独统计。
- `generation_scheme_runs`、run tasks、方案定义没有新增或修改。
- 隐藏 `/ai` 后，方案列表和运行历史 GET 不再有非人工验收调用。
- `/ai-engines`、`/format-engines`、问题池和 Pipeline 流量正常。
- 问题池创建、重绑、同步、自动同步和删除正常。
- Pipeline 选题、生文、配图、送审、分发正常。
- MCP 能读取问题池、保存文章、生成 weekly report。
- 官方 `goal` skill 可以列表、下载、校验和安装。
- Article feed、详情和发布链路没有标签表相关错误。
- 没有 `scheme_*` import、旧表、Pydantic DTO 或 MCP 注册异常。

任一条件不满足，Release B 保持阻断。

## 8. Release B：归档与删除

### 8.1 前端删除

- `AiGenerationWorkspace`
- `GenerateTab`
- `SchemeEditorModal`
- `RunDetailModal`
- 原 `features/ai-generation/PoolManagerModal`
- 方案 API、方案类型和旧 `GenerationSession` API/类型
- `api/ai-generation.ts` 在共享 API 拆出后的剩余内容
- 未路由的 `ImageLibraryWorkspace`
- 仅由上述组件消费的样式

不能删除：

- `api/image-library.ts`
- `QuestionPool`、`QuestionType`、`AiEngine`
- Pipeline 使用的 `schemeEmpty`、`schemeLine*`、`schemeChip*`、`schemeLink`
- 问题源 Modal 使用的 `schemePanel*`、`schemeCard*`

### 8.2 后端删除

- `ai_generation/scheme_router.py`
- `ai_generation/scheme_service.py`
- `ai_generation/scheme_executor.py`
- 旧 `ai_generation/pipeline.py`
- 旧 `ai_generation/service.py`
- 旧 session POST/GET 路由和 schema
- 旧 `modules/skills/*`
- `question_bank.py` 中只服务旧 session 的消费队列函数
- `loop_skills/versions_service.py`
- `LoopSkillBundleVersion` ORM 类
- 旧方案、session、Skill、CategoryUsage ORM 类
- `main.py` 中方案 import、恢复、挂载和 session factory 注入
- 方案并发配置项与已失真的资源注释
- Alembic、脚本和测试建库入口中的旧 Skill 注册 import
- `Tag`、`ArticleTag`、`Article.tags` 和 feed 的标签 query option

### 8.3 明确保留

- 问题池 ORM、CRUD、同步、scheduler 和共享 DTO
- `runtime_templates.py`、`article_writer.py`、converter、模型解析
- Pipeline 全部节点、版本、运行和 scheduler
- `skill_library_skills`、`skill_library_versions`
- loop bundle 兼容只读 router
- 三个 performance 工具及真实实现
- image-library API、模型和客户端
- 游戏库、小红书、视频、飞书、公众号、质量参考、自动评审

## 9. 数据库清理

### 9.1 方案表删除顺序

1. `generation_scheme_run_tasks`
2. `generation_scheme_runs`
3. `generation_scheme_line_questions`
4. `generation_scheme_lines`
5. `generation_schemes`

### 9.2 旧直连链路

1. `generation_sessions`
2. `skills`
3. `category_usages`

`generation_sessions` 必须先于 `skills`，因为 `generation_sessions.skill_id -> skills.id`。

### 9.3 `question_items` 遗留列

删除：

- `ck_question_items_status`
- `ix_question_items_status`
- `article_id -> articles.id` 的实际 FK
- `article_id` 的索引
- `status`
- `article_id`

新 migration 通过 inspector 按 constrained columns 查找 FK，不依赖 MySQL 自动约束名。
`ix_question_items_source_active` 保留。

### 9.4 其他遗留

1. `article_tags`
2. `tags`
3. `loop_skill_bundle_versions`（若存在）

只有 `loop_skill_bundle_versions` 允许 `has_table()` 分支：dev 不存在、生产存在且为空。
其他目标表或列缺失视为 schema 漂移并阻断，不用条件删除掩盖问题。

### 9.5 Migration 拆分

- Migration 1：方案表、旧 session、旧 skills、category usages、问题遗留列。
- Migration 2：文章标签表和可选旧 loop bundle 表。

两条 migration 都只做数据库结构变更，不在 migration 内导出文件。

## 10. 离线归档

归档位置：

```text
${GEO_DATA_DIR}/exports/slimming/<UTC时间戳>/
```

内容：

```text
manifest.json
schema.sql
generation_schemes.jsonl
generation_scheme_lines.jsonl
generation_scheme_line_questions.jsonl
generation_scheme_runs.jsonl
generation_scheme_run_tasks.jsonl
generation_sessions.jsonl
skills.jsonl
category_usages.jsonl
tags.jsonl
article_tags.jsonl
loop_skill_bundle_versions.jsonl
README.md
```

若表不存在，仍创建 0 行 JSONL，并在 manifest 记录 `source_state="absent"`；存在空表则记录
`source_state="present"` 和 `row_count=0`。

Manifest 必须包含：

- 数据库名称、Alembic revision、部署 commit 和镜像版本；
- 导出时间和归档脚本版本；
- 每张表的存在状态、精确行数、最早/最晚时间；
- 文件字节数和 SHA-256；
- FK 闭合结果；
- 5 个 pending task 的父 run 已 failed 这一已知历史状态。

验收：

- JSONL 行数等于 `COUNT(*)`；
- 所有 SHA-256 复算一致；
- JSON/Text 字段不二次字符串化；
- 在一次性 MySQL 库完成恢复；
- 恢复后行数、JSON 引用和 FK 闭合一致。

任一失败均阻断 Release B。

## 11. 不影响其他模块的验证矩阵

| 模块 | 必须验证 |
|---|---|
| 应用启动 | `server.app.main` 可 import，所有保留 router 可挂载 |
| Pipeline | 节点注册完整；question source 到 distribute 全链路 |
| 问题池 | CRUD、飞书同步、auto sync、active 语义 |
| MCP | 工具数仍为 39；问题池、保存文章、performance、skill 安装 |
| Articles | feed、详情、MCP get/save、AI 配图、分组、发布不查旧标签表 |
| Skills | goal 列表、版本、ZIP、install payload、兼容 bundle 路径 |
| Performance | template/account 聚合和 metrics 写回 |
| Image library | Pipeline 类别选择、编辑器存图、游戏素材上传 |
| 游戏/小红书/视频 | 现有 API、scheduler、MCP tools 不变 |
| Worker | 发布任务、账号登录和保活不 import 删除模块 |
| Alembic/脚本 | env、seed、encrypt、repair 可 import 和运行 |
| 前端 | route、导航、Pipeline 编辑器、内容、MCP、游戏页面 build |

静态门禁扫描 `server/app`、`server/mcp`、`server/scripts`、`server/worker`、
`server/alembic/env.py`、`server/tests/utils.py`，排除历史 migration，禁止 import：

- `ai_generation.scheme_router`
- `ai_generation.scheme_service`
- `ai_generation.scheme_executor`
- `ai_generation.pipeline`
- `ai_generation.service`
- `modules.skills`
- `loop_skills.versions_service`

ORM 门禁断言 `Base.metadata.tables` 不含目标旧表，`question_items` 不含遗留列，
`Article` mapper 不含 `tags`；同时断言所有保留表仍存在。

## 12. 迁移演练策略

dev 不能直接 upgrade 或 stamp。本次使用三类环境：

1. **全新 MySQL 测试库**：从仓库 migration 0 升到当前 head，再升到新 head；
2. **目标数据夹具库**：按 dev 关系和行状态构造方案/session/question/skill/tag 数据；
3. **生产 schema 临时克隆**：只复制 schema 和目标表脱敏数据，在非生产实例完成
   upgrade、downgrade 和归档恢复。

生产库只在正式 Release B 执行已经演练通过的 migration。任何测试不得把
`GEO_ALLOW_NON_TEST_DATABASE_FOR_TESTS=1` 指向生产。

dev 全库修复另开运维任务：备份、比对差异、按缺失 migration 补结构，验证后才修正
`alembic_version`，本次不盲目 stamp。

## 13. 回滚

### 13.1 Release A

Release A 无破坏性数据库变更。代码回滚可以恢复 `/ai` 路由和方案写接口。active 问题语义
属于目标语义，不随 UI 回滚自动恢复旧消费队列。

### 13.2 Release B

Alembic downgrade 只能重建空结构，不能恢复数据。真实回滚依赖离线归档。

Release B 前必须验证：

1. upgrade 完成；
2. downgrade 重建结构；
3. 归档可恢复；
4. 恢复后行数、FK 和 JSON 引用一致；
5. 保留模块回归测试在 upgrade 与 downgrade 后均可运行。

## 14. 门禁与上线验收

后端：

```bash
ruff check server/
ruff format --check server/
mypy server/app
```

前端：

```bash
pnpm --filter @geo/web lint
pnpm --filter @geo/web typecheck
pnpm --filter @geo/web format:check
pnpm --filter @geo/web build
```

数据库测试必须使用 MySQL。若全量 pytest 有既有失败，必须记录基线并证明本次没有新增失败。

Release B 后生产验收：

1. `alembic current` 等于新 head；
2. 目标表/列不存在，保留表精确行数符合预期；
3. MCP 工具数仍为 39；
4. 完成一次真实 Pipeline 测试运行；
5. 问题池同步和 MCP 选题正常；
6. goal skill 下载、SHA 和 install payload 正常；
7. Article feed、详情、发布和配图正常；
8. weekly report 能返回真实模板和账号指标；
9. app、worker、scheduler 健康；
10. 日志没有旧表、旧模块、DTO 或路由异常。

## 15. 完成标准

只有同时满足以下条件才算完成：

- 站内只剩 Pipeline，外部保留 MCP；
- 问题池成为 Pipeline 的独立问题源能力；
- `/ai` 方案 UI、方案 API、方案代码和方案表删除；
- 旧 sessions、旧 Skill、旧消费队列删除；
- 旧图片库页面删除，图片库能力保留；
- 模板表现不再是 stub，weekly-report Loop 不受损；
- 旧 loop bundle 存储删除，兼容下载和安装不受损；
- Article 不再注册或访问旧标签表；
- 受保护契约和验证矩阵全部通过；
- 生产观察期、归档、迁移演练和上线验收全部通过；
- 当前代码不存在对已删除模块、ORM 对象或表的引用；
- `CLAUDE.md` 和现行架构文档与新事实一致。
