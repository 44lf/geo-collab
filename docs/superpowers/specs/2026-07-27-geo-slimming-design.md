# GEO 主链路收敛与历史能力清理设计

日期：2026-07-27

状态：已确认设计，待实施计划

## 1. 背景

GEO 当前同时存在三类内容生成入口：

- `/agents`：Pipeline / 智能体工作流；
- `/ai`：问题池驱动的方案生文；
- MCP Loop：由外部 Agent 驱动的生成、配图、评审和发布闭环。

生产使用证据显示 Pipeline 已成为站内主生产链路，MCP Loop 仍在活跃运行，而方案生文
长期没有新运行。另一方面，方案生文页面仍承担问题池的创建、飞书绑定、同步和维护，
Pipeline 的 `question_source` 节点又依赖这些问题池；Pipeline 代码还直接复用了
`scheme_service` 和 `scheme_executor` 中的 helper。

因此，本次不能按目录直接删除 `/ai` 或整个 `ai_generation` 模块。本设计采用两阶段拆除：
先把问题池提升为 Pipeline 的独立数据源、解除 Pipeline 对方案模块的代码依赖并停止新方案
运行；观察稳定后，再归档数据并删除方案、旧直连生成和其他已确认的历史能力。

## 2. 目标

### 2.1 产品目标

- 站内只保留 Pipeline / 智能体作为正式生成入口。
- MCP Loop 继续作为外部 Agent 自动化入口。
- 下线 `/ai` 方案生文，不影响问题池、Pipeline 或 MCP。
- 问题池在“智能体管理”中成为正式的“问题源管理”能力。
- 保留游戏库、小红书、视频、飞书、公众号、质量参考、自动评审、内容和发布链路。

### 2.2 工程目标

- Pipeline 不再 import 任何 `scheme_*` 模块。
- 删除方案生文、旧 `/generation/sessions`、旧 Skill 和旧消费队列代码及数据表。
- 删除未接线的旧图片库页面，但保留仍在使用的图片库 API 和数据模型。
- 删除模板表现统计 stub，但保留真实的账号表现统计和发布指标回写。
- 删除每个目标前都有明确的运行证据、代码依赖检查和数据归档。
- 最终让页面、路由、模块、表和文档与实际产品能力一一对应。

## 3. 非目标

- 不重命名 `/api/generation/question-pools/*`。路径虽然带 `generation`，但改名会给 Pipeline、
  MCP 和外部调用方制造无业务收益的兼容成本。
- 不删除或重写历史 Alembic migration 文件，只新增向前迁移。
- 不删除 `QuestionPool`、`QuestionItem`、飞书同步或问题池自动同步 scheduler。
- 不删除 `article_writer`、Markdown 转换、模型解析和提示词运行时等共享生成能力。
- 不删除 `image-library` API、`StockCategory` 或 `StockImage`。
- 不把 Pipeline 与 MCP 合并成一套运行时；两者的模型来源、交互方式和故障边界不同。
- 不在本次顺带重构其他活跃业务模块。

## 4. 已确认的关键事实

### 4.1 Pipeline 已覆盖方案生文的主要业务结果

Pipeline 的 `question_source` 支持：

- 选择问题池；
- 按问题类型组织问题；
- 指定具体问题；
- 为每种问题类型配置允许的提示词模板；
- 为每种问题类型配置生成文章数。

下游 `ai_compose`、`ai_illustrate`、`to_review` 和 `distribute` 节点可以完成生文、配图、
送审和分发。

### 4.2 两条链路的运行语义并非完全相同

方案保存时会冻结问题文本快照；Pipeline 运行时会从 `QuestionItem` 读取当前有效文本。
方案运行还会隐式执行自动排版、配图、成组和送审，Pipeline 则通过显式节点完成这些动作。

本次接受这些差异，不迁移方案的“固定问题快照”语义。历史方案只离线归档，不自动转换成
Pipeline。

### 4.3 问题池当前不是独立能力

问题池唯一的创建、修改、删除和手工同步 UI 是方案页面中的 `PoolManagerModal`。
Pipeline 编辑器只能读取问题池和问题类型。因此，下线 `/ai` 前必须先迁移问题池管理入口。

### 4.4 Pipeline 与方案代码存在反向依赖

- `ai_compose` 和存量 `ai_generate` 直接复用
  `scheme_executor._pick_valid_template`。
- 问题类型 API 通过 `scheme_service.question_types()` 聚合数据。

删除方案模块前必须先把这两项能力移到共享模块。

## 5. 目标架构

```text
问题源管理
  ├─ 问题池 CRUD
  ├─ 飞书绑定
  ├─ 手工同步
  └─ 自动同步
          |
          v
Pipeline / 智能体
  ├─ question_source
  ├─ ai_compose
  ├─ ai_illustrate
  ├─ to_review
  └─ distribute
          |
          v
文章、审核与发布

MCP Loop
  └─ 继续通过问题池只读接口和 MCP action tools 驱动外部自动化
```

### 5.1 共享运行时边界

保留 `ai_generation` 作为问题池和共享生成运行时，不做无收益的目录级重命名。

共享能力调整如下：

- 将模板候选校验和随机选择 helper 从 `scheme_executor.py` 移到中立的生成运行时模块。
- 将问题类型聚合从 `scheme_service.py` 移到 `question_bank.py`。
- Pipeline、问题池 API 和测试只依赖共享模块。
- `scheme_router.py`、`scheme_service.py`、`scheme_executor.py` 删除后，不得存在反向兼容 import。

## 6. 发布 A：解耦与停用

发布 A 不删除数据库表，目标是先证明主链路可以脱离方案模块稳定运行。

### 6.1 前端

- 在“智能体管理”顶栏增加“问题源管理”入口。
- 将 `PoolManagerModal` 移到 `features/pipelines/question-pools/`，改名为
  `QuestionPoolManagerModal`。
- Pipeline 编辑器的问题源节点提供“管理问题池”和“立即同步”入口。
- 将问题池客户端 API 从 `api/ai-generation.ts` 拆到独立的
  `api/question-pools.ts`。
- 从桌面导航、移动端导航和路由配置中移除 `/ai`。
- 旧 `/ai` 链接统一回到 `/agents`。
- 暂时保留方案组件源码，作为发布 A 的快速代码回滚路径。

### 6.2 后端

- 移出 `_pick_valid_template` 共享 helper，并更新 Pipeline 调用。
- 将 `question_types()` 移入 `question_bank.py`。
- 增加静态依赖测试，禁止 Pipeline import `scheme_service` 或 `scheme_executor`。
- 保留所有问题池 API、MCP 问题池只读接口和同步 scheduler。
- 方案创建、更新、删除和启动运行接口统一返回 `410 Gone`。
- 方案列表、详情和历史运行查询暂时保持只读。
- 返回 410 时明确引导调用方迁移到 Pipeline。

### 6.3 发布前闸门

- 数据库中不存在 `pending` 或 `running` 的方案运行。
- Pipeline 已经不再依赖任何 `scheme_*` 模块。
- 问题池管理能在“智能体管理”中完整操作。
- 前后端门禁和目标回归测试全部通过。

### 6.4 观察期

发布 A 上线后至少观察 7 个连续自然日，并同时满足：

- 方案启动接口没有真实调用。
- `generation_scheme_runs` 没有新增记录。
- 问题池创建、修改、同步和删除正常。
- Pipeline 的选题、生文、配图、送审和分发正常。
- MCP Loop 能继续读取问题池并保存文章。
- 没有出现与方案模块解耦相关的新错误日志。

未满足任一条件时，不得进入发布 B。

## 7. 发布 B：归档与删除

### 7.1 前端删除

- `AiGenerationWorkspace`
- `GenerateTab`
- `SchemeEditorModal`
- `RunDetailModal`
- 方案专属 API 和 TypeScript 类型
- 旧 `GenerationSession` API 和类型
- 未路由的 `ImageLibraryWorkspace`
- 上述组件绑定且无其他消费者的样式

### 7.2 后端删除

- `ai_generation/scheme_router.py`
- `ai_generation/scheme_service.py`
- `ai_generation/scheme_executor.py`
- 旧 `ai_generation/pipeline.py`
- 旧 `ai_generation/service.py`
- 旧 `modules/skills/*`
- `question_bank.py` 中只服务旧 `/sessions` 的消费队列逻辑
- `loop_skills/versions_service.py` 及旧 bundle 版本运行时兼容代码
- 模板表现统计 stub、对应 HTTP 端点、MCP tool 和 catalog 项

### 7.3 明确保留

- `QuestionPool`、`QuestionItem`
- 问题池 CRUD、同步、问题文本和问题类型聚合
- 问题池自动同步 scheduler
- `article_writer.py`、converter、模型解析和 Markdown 清洗
- Pipeline 全部节点、版本、运行和调度能力
- `skill_library_skills`、`skill_library_versions`
- 账号表现统计和发布 metrics 回写
- `image-library` API、`StockCategory`、`StockImage`
- 游戏库、小红书、视频、飞书、公众号、质量参考和自动评审

## 8. 数据库清理

不使用“若干空表”或“6 张空表”之类模糊范围。只允许删除本节显式列出的对象。

### 8.1 方案表

按外键依赖顺序删除：

1. `generation_scheme_run_tasks`
2. `generation_scheme_runs`
3. `generation_scheme_line_questions`
4. `generation_scheme_lines`
5. `generation_schemes`

### 8.2 旧直连生成链路

1. `generation_sessions`
2. `skills`
3. `category_usages`
4. `question_items.status`
5. `question_items.article_id`

`question_items.status/article_id` 虽不再参与活跃业务，当前问题池 HTTP/MCP DTO 仍暴露这两个
字段，部分列表和 `pending_count` 也沿用旧 `status="pending"` 过滤。删除数据库列时必须
同步改为以下确定语义，不能让 ORM 校验或 MCP 返回在迁移后崩溃：

- 问题是否可用只以 `source_active` 为准；
- `pending_count` 改为统计 `source_active=True` 的问题数；
- 问题池内部查询从 `status` 参数改为显式的 active/all 选择；
- HTTP 兼容查询 `status=pending`（含省略）返回 active 问题；
- HTTP 兼容查询 `status=all` 返回全部问题；
- HTTP 兼容查询 `status=consumed` 返回空列表；
- 其他 `status` 值返回 400；
- `QuestionItemRead` 暂时保留兼容字段，显式映射
  `status="pending"`、`article_id=null`，不再从 ORM 列读取；
- 前端内部 `QuestionItem` 类型删除这两个无业务语义的字段；
- MCP tool 的公开参数不增加 `status`，继续只提供 pool、limit 和 category。

这两个响应字段仅是旧 HTTP 调用方的兼容投影，不代表消费队列仍被保留。

### 8.3 其他已确认遗留

1. `article_tags`
2. `tags`
3. `loop_skill_bundle_versions`

删除 `loop_skill_bundle_versions` 前还必须满足：

- 新技能库存在 slug 为 `goal` 的官方 skill；
- 其当前版本可以完整下载并校验；
- 旧表中所有有效行已经迁移或写入离线归档；
- 旧 seed 兼容脚本不再作为生产升级路径使用。

### 8.4 Alembic 迁移拆分

新增两条独立向前迁移，不修改历史 migration：

- 迁移一：删除方案和旧直连生成链路。
- 迁移二：删除标签、旧 bundle 版本表和遗留列。

拆分目的是让失败定位、审查和临时库演练具有明确的领域边界。

## 9. 离线归档

### 9.1 归档位置

```text
${GEO_DATA_DIR}/exports/slimming/<UTC时间戳>/
```

归档不设置自动过期时间，只能通过后续人工明确决定删除。

### 9.2 归档内容

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

JSONL 用 UTF-8 编码，保留 JSON、Text、时间和可空字段的原始值，不把嵌套 JSON
二次字符串化。

### 9.3 Manifest

`manifest.json` 必须记录：

- 数据库名称；
- 当前 Alembic revision；
- 当前部署 commit SHA；
- 归档命令版本和执行时间；
- 每张表的精确行数；
- 可用时记录最早和最晚时间；
- 每个归档文件的字节数和 SHA-256。

### 9.4 归档验收

- 每个 JSONL 文件行数必须等于对应表的 `COUNT(*)`。
- 所有文件 SHA-256 必须复算一致。
- 归档中涉及的外键引用必须闭合，或在 manifest 中明确标记为指向保留表。
- 在一次性 MySQL 库中完成一次恢复演练并核对行数。
- 任一校验失败时，禁止执行删表迁移。

归档是发布 B 的运维门禁，不在 Alembic migration 内执行外部文件写入。

## 10. 错误处理与阻断规则

以下任一情况必须停止发布 B：

- 存在活跃方案运行；
- 归档行数或 SHA-256 不一致；
- 新技能库没有可安装的官方 `goal` 当前版本；
- Pipeline 或 MCP 仍 import 已计划删除的模块；
- 观察期内仍有方案写接口调用；
- 生产 Alembic revision 与归档 manifest 不一致；
- Alembic 出现多 head；
- 临时库迁移或恢复演练失败。

发布 A 中的方案写请求返回 410，不创建新数据，也不部分执行。问题池 API 的路径和响应
结构保持不变。

发布 B 后删除方案和旧 session 路由，不保留空壳 service、schema 或 ORM；旧调用得到 404。

## 11. 回滚

### 11.1 发布 A

发布 A 没有破坏性数据库变更。回滚方式是恢复 `/ai` 路由和导航，并恢复方案写接口。

### 11.2 发布 B

Alembic downgrade 只能重建空表结构，不能恢复已删除数据。真实数据恢复必须使用离线归档。
因此发布 B 属于不可自动回滚的数据变更。

发布 B 前必须在临时库验证：

1. upgrade 能完成；
2. downgrade 能重建结构；
3. 归档恢复能重新写入历史数据；
4. 恢复后行数和关键外键一致。

## 12. 测试设计

### 12.1 后端单元与集成测试

- 问题类型聚合迁移后，分类、未分类和空池行为不变。
- 共享模板选择 helper 保持用户权限、scope、启用状态和候选选择行为。
- `question_source -> ai_compose` 的逐类型模板和文章数继续生效。
- 静态依赖测试证明 Pipeline 不再 import `scheme_*`。
- 问题池 CRUD、同步和自动同步回归。
- 删除 `QuestionItem.status/article_id` 后，问题池 active/all/consumed 兼容查询和
  `pending_count` 语义回归。
- MCP `list_question_pools`、`list_question_items` 回归。
- 新技能库安装、版本切换和下载回归。
- Article feed 删除标签关系后不再访问对应表。
- 账号表现统计和 metrics 回写继续工作。
- 发布 A 的方案写接口统一返回 410。

### 12.2 数据库测试

- 在一次性 MySQL 库执行当前 head 到新 head。
- 验证目标表和遗留列已删除。
- 验证问题池、Pipeline、文章和新技能库数据不变。
- 执行 downgrade 并确认只能恢复结构。
- 从归档恢复到临时库并核对行数和外键。
- 验证 Alembic 只有一个 head。

### 12.3 前端门禁

```bash
pnpm --filter @geo/web typecheck
pnpm --filter @geo/web lint
pnpm --filter @geo/web format:check
pnpm --filter @geo/web build
```

手工或浏览器验收：

- `/ai` 不再出现在桌面侧栏、移动端底栏和“更多”页面；
- `/ai` 旧链接回到 `/agents`；
- 智能体页面可以完成问题池全套管理；
- Pipeline 能选择新建或刚同步的问题池；
- 旧图片库页面不再生成独立构建 chunk。

### 12.4 后端门禁

```bash
ruff check server/
ruff format --check server/
mypy server/app
```

问题池、Pipeline、MCP、文章 feed、技能库和迁移相关测试必须通过。若全量测试仍有仓库既有
无关失败，必须单列基线失败并证明本次没有新增失败。

## 13. 上线验收

### 13.1 发布 A

1. 在智能体页面打开问题源管理。
2. 读取现有问题池并完成一次同步。
3. 建立或复制测试 Pipeline。
4. 验证选题、生文、配图和送审。
5. 验证 MCP 能继续读取问题池并保存文章。
6. 确认 `/ai` 不再可见，方案写接口返回 410。
7. 连续观察至少 7 天。

### 13.2 发布 B 前

1. 确认没有活跃方案运行。
2. 生成并验证离线归档。
3. 记录生产 commit、镜像版本和 Alembic revision。
4. 在临时库完整演练 upgrade、downgrade 和归档恢复。
5. 通过正式发版流程部署，不从 feature 分支或游离 commit 直接打 tag。

### 13.3 发布 B 后

1. `alembic current` 等于新 head。
2. 目标表不存在，保留表行数符合预期。
3. Pipeline、MCP、发布 worker 和 scheduler 健康。
4. 完成一次真实 Pipeline 运行。
5. 错误日志中没有旧表、旧模块或旧路由异常。
6. 保留归档和 manifest，不自动清理。

## 14. 完成标准

只有同时满足以下条件才算完成：

- 产品只剩 Pipeline 和 MCP 两套正式生成入口；
- 问题池成为 Pipeline 的独立数据源能力；
- `/ai` 方案生文 UI、API 和表全部删除；
- 旧 `/sessions`、旧 Skill 和旧消费队列全部删除；
- 旧图片库页面和模板 performance stub 删除；
- 本设计列出的遗留表和字段完成迁移；
- 当前代码不存在对已删除模块或表的引用；
- `CLAUDE.md`、架构文档和运维说明与新事实一致；
- 前后端门禁、迁移演练和生产验收全部通过；
- 历史数据存在经过校验且完成恢复演练的离线归档。
