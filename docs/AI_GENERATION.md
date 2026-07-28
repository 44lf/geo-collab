# AI 生文模块当前说明

## 当前入口与边界

Release A 中，**Pipeline（前端名称「智能体管理」）是站内唯一的生文入口**：在
`/api/pipelines/*` 配置并运行 `question_source`、`ai_generate` / `ai_compose` 等节点，再将文章送审或分发。
Pipeline 的运行快照冻结，后台执行在 API 进程内进行；它不是发布 worker 的职责。

**MCP 是站外入口**。Claude Code Loop 通过 39 个原子 MCP tools 读取问题、生成 Markdown 并调用
`save_article` 写入站内；这条路径不要求 GEO 再调用 LiteLLM。Loop skill / ZIP / SHA 安装兼容性必须保留。

`/ai` 只做前端兼容重定向至 `/agents`，不再提供站内生文界面。旧的
`POST /api/generation/sessions`（LangGraph 会话创建）为 410；已存在会话仍可通过
`GET /api/generation/sessions/{session_id}` 按原有鉴权读取历史。模块和历史表保留但不再运行。

## 保留的兼容接口

问题池仍是共享的飞书镜像：`/api/generation/question-pools/*` 可列出、创建、改名和同步，删除仅 admin。
问题项的可用性以 `source_active` 为准；响应 DTO 仍返回历史兼容字段 `status="pending"`、`article_id=null`。

- `status` 缺省或 `pending`：返回 `source_active=true` 项。
- `status=all`：返回全部源项。
- `status=consumed`：返回空列表（兼容旧调用）。
- 其它 `status`：HTTP 400。

AI engine 下拉接口 `/api/generation/ai-engines` 与 `/api/generation/format-engines` 仍供 Pipeline 和兼容客户端读取。

方案（scheme）历史仍可只读审计：list、detail、run history 和 run detail 的 GET 继续挂载；
`POST /schemes`、`PUT/PATCH/DELETE /schemes/{id}`、`POST /schemes/{id}/runs` 全部返回 410，且必须零 DB、审计或后台线程副作用。

## 模板表现

`GET /api/prompt-templates/{template_id}/performance?window_days=7` 是 MCP token 保护的 HTTP 接口。
它按 `Article.source_template_id` 和窗口内 `created_at` 真实聚合文章，而非展示字段或 stub：
`article_count` 是匹配文章数；缺少或无效指标的 `avg_views` / `avg_likes` 为 `null`；有文章但 approved 数为零时
`approval_rate` 为 `0.0`；窗口无文章时 `approval_rate` 为 `null`。

## 实施约束

- 所有站内模型调用走 LiteLLM，不直接导入 `anthropic` 或 `openai` SDK。
- Markdown 入库时同时转换为 Tiptap JSON、HTML 和纯文本所需结构。
- MCP 与 Pipeline 是并列入口，不得重新接回 retired sessions 或 scheme 写路径。
- 生产观察、回滚与 Release B 解锁规则见
  [`docs/runbooks/geo-slimming-release-a-observation.md`](runbooks/geo-slimming-release-a-observation.md)。
