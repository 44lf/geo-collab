# 微服务化拆分设计（模块化单体 → 六仓多服务）

- 日期：2026-07-10
- 状态：**架构方向已定，关键运行时与契约 ADR 待定** —— **不可直接进入 Phase 1**，须先补齐 §9 的 6 项阻塞 ADR。
- 评审结论（2026-07-10）：**架构方向 8/10，落地完整度 5/10**。方向正确，但 `PublishPayload` 契约、多副本执行模型、跨库 FK 清单、授权透传、CQRS 投影、回滚/noVNC runbook 六项未解；现在直接实施大概率卡在**多副本执行、跨库 FK、授权透传**。
- 方案：**方案 A · 绞杀者渐进拆分（Strangler Fig）**，终态 **多仓（Polyrepo）**。
- 前置背景：当前是模块化单体（`server/app/modules/*` 共享一个 MySQL + 一个 `geo-collab-server` 镜像跑 `app`/`worker` 两进程），详见 `CLAUDE.md`。

> ⚠️ 这是一份**方向性设计**，不是逐行实现计划。每个 Phase 落地前应各自走一遍 writing-plans 产出该 Phase 的实现计划。§9 的 6 项 ADR 是 Phase 0 的核心交付物。

---

## 0. 已确认保留的决策（评审认可，不再动摇）

- 只拆 generation 和 distribution，core 继续模块化单体。
- accounts 与 tasks 一起走、pipelines 留 core 当编排器、articles 作为内容中枢——边界判断合理。
- 先"同实例分所有权"，再按真实压力物理分库（Phase 3 按需）。
- 先同仓验证 remote 稳定，再物理拆仓（§5.3）。
- 明确承认 Polyrepo 的版本协调成本（§7）。

---

## 1. 背景与动机

### 1.1 现状（已核实，非文档复述）

- 全部业务逻辑在同一个 `geo-collab-server` 镜像，跑成两进程：`app`（FastAPI，所有模块 + SPA fallback + `/mcp` mount）+ `worker`（`server/worker/executor.py`，发布 + 浏览器自动化 + 账号登录处理器）。
- 周边：共享 `mysql` / `minio` / `nginx`，外加已独立的 `dailyhot-api`（热榜）、`registry`。
- **单机单实例**：生产单台阿里云 ECS，一个 compose 栈；`app` 1 个 + `worker` 1 个，且被约束绑定无法水平扩容——
  - worker 单实例（账号登录处理器非多实例安全）；
  - web 也不能多实例（`pipelines/recovery.py` 无租约全量复位，第二实例会误杀第一实例在跑的 run）。
- 模块耦合（`grep` 核实的关键环）：
  - `tasks.service` ↔ `accounts.service` 互相 import（任务派号 `PublishTaskAccount` + 账号锁）——**一个整体**。
  - `articles.service` import `tasks.models`（算"已分发/在途"），`tasks.service` 又 import `articles.models`（取正文发布）——**Article ↔ PublishRecord 环形依赖**。
  - `ai_generation.scheme_executor` / `article_writer` import `articles.service.create_article` / `mark_pending_and_group` + `image_library`（生文写文章 + 配图）。

### 1.2 动机（用户确认：四项全要）

团队协作/发布解耦、扩展性/性能、稳定性/故障隔离、技术异构/组织边界。

### 1.3 约束

- 团队 3-5 人，会 CI/CD，**无专职运维**。
- 基础设施走向：**上 k8s（自建或阿里云 ACK）**——这是"真拆能拿到独立扩容/故障隔离收益"的前提。
- 因此拒绝方案 B（一次性全拆 + 每模块独立 DB）：对当前团队几乎必然延期 + 引入大量分布式故障。

---

## 2. 目标架构与服务边界

### 2.1 终态拓扑（3 逻辑服务）

只剥离真正有独立诉求的两块，core 保持模块化单体（YAGNI）。**注意：一个"逻辑服务"在 k8s 上对应多个 workload**（见 §6.2），不是一个 Pod。

```
                    ┌─────────────────────────────────────┐
   浏览器/SPA ──────▶│  core-api  (模块化单体 · 前门 BFF)    │
   Claude Code/MCP ─▶│  articles(内容中枢) image_library    │
                    │  templates ai_models system/auth     │
                    │  audit video report feishu mcp        │
                    │  pipelines(编排器) + 托管 SPA/MCP     │
                    └──────┬──────────────────┬─────────────┘
                           │ API/事件          │ API/事件
                  ┌────────▼───────┐   ┌──────▼──────────────┐
                  │ generation-svc │   │ distribution-svc     │
                  │ ai_generation  │   │ accounts + tasks     │
                  │ 写作/配图 compute│   │ + 浏览器 worker      │
                  │ (LLM 突发扩容)  │   │ (Xvfb/VNC/OOM 隔离)  │
                  └────────────────┘   └─────────────────────┘

   共享基础设施: MySQL(逐步 database-per-service) · MinIO · Redis(新增,队列/事件)
```

### 2.2 服务边界（bounded context）

| 服务 | 拥有的模块 | 为什么独立 | 数据归属 |
|---|---|---|---|
| **core-api** | articles、image_library、prompt_templates、ai_models、system/auth/users、audit、video、report、feishu、mcp_catalog/loop_skills、**pipelines(编排器)**、SPA/MCP mount | 前门，事务耦合紧，**无独立扩容需求** | Article(内容中枢)、素材、模板、用户 |
| **distribution-svc** | **accounts + tasks + 发布 worker** | 浏览器自动化重、会 OOM(隔离)、要 Xvfb/VNC 整套栈、单实例→可扩容 | PublishTask/Record、Account、登录态 |
| **generation-svc** | ai_generation(问题池/方案/scheme run/article_writer) + 配图/排版 compute | LLM 调用重、突发，想独立扩容 | GenerationSession、问题池、方案 |

### 2.3 三个关键边界决策

1. **accounts 跟着 tasks 走进 distribution-svc**（不留 core）——它俩互相 import，且登录态/VNC/keepalive 本就跑在 worker 里，是"把内容发到外部平台"的完整上下文。core 需要账号列表看板时走只读 API。
2. **pipelines 留在 core 当"编排器"**——它同时协调生文(`ai_compose`)与分发(`distribute`)两端，拆开后是调下游两服务 API 的工作流引擎。
3. **articles 是内容中枢，始终归 core 写**——generation-svc 通过 API 创建文章写入，distribution-svc 通过快照/引用消费。

---

## 3. 通信、数据归属与接缝破解

### 3.1 服务间通信：两条通道，复用现有肌肉

- **同步 · 内部 HTTP+JSON**：复用现有 MCP 内部调用模式——`GEO_MCP_INTERNAL_API_URL` 风格内部地址（钉死回环避免 hairpin NAT）。鉴权见 §3.4（不是简单的静态万能 token）。不引入 gRPC/服务网格。
- **异步 · 事件**：新增 **Redis Streams** + **outbox 表 + 轮询投递**（同 worker 现有"轮询 DB"模式），用于破解环形依赖。消费端必须幂等（§3.2 投影表的 inbox）。

### 3.2 破解 Article ↔ PublishRecord 环（核心 · 评审后修正）

**写方向（发起发布）—— 新增跨服务 DTO，不复用 `PublishPayload`**

`PublishPayload`（`drivers/base.py`）含 `cover_asset_path: Path`、`state_path: Path`、`account_key`、`BodySegment`、`temp_files: tuple[Path]`——**本质是 distribution 内部驱动参数**（本机路径 + 账号信息），不是可 JSON 传输的 DTO。跨服务契约**必须另立**：

```text
CreatePublishTaskRequest              (core/pipelines → distribution-svc, JSON)
├── request_id            幂等键（复用 client_request_id 套路）
├── actor                 委托身份（user_id/role，来自 §3.4 delegation token）
├── article_ref           { article_id, version }  ← 只作引用/回查读模型用
├── article_snapshot      { title, content_html, content_json, asset_object_keys[] }
│                         ← 发布所需正文全部内联/或 MinIO object key，distribution 不回查 Article
└── distribution          { account_ids[], 发布策略(round-robin/停在预览/...) }
```

distribution-svc 收到后，**在本服务内**结合账号凭据（自己的 accounts 表）+ 本机 profile 路径，构造现有 `PublishPayload` 交给驱动。→ distribution-svc **允许持有 `article_ref` 做溯源，但禁止回查 core 的 articles 表取正文**（正文只认 snapshot）。

> 修正评审指出的矛盾：原稿"不传 article_id" 与"保存 article 软引用"自相矛盾。正解＝**传 `article_ref`（软引用，用于溯源与读模型），但正文一律走 `article_snapshot`，distribution 不回查 Article**。

**读方向（"已分发/在途"判定）—— CQRS 投影表，不是单列**

一篇文章有**多条** PublishRecord（多账号 round-robin）；"某条 failed"不代表"没有另一条 succeeded/running"。事件还会重复/乱序。因此 core 不能只存 `Article.distribution_status` 单标量，而是建投影表：

```text
article_distribution_records          (core 侧读模型投影)
├── record_ref            distribution 侧 PublishRecord 主键（投影主键）
├── article_ref
├── status                pending/running/succeeded/failed/cancelled
├── is_deleted            软删标记
├── aggregate_version     防乱序（旧版本事件丢弃）
└── (event inbox 表)      event_id 幂等去重（at-least-once 投递的兜底）
```

distribution-svc 每次 PublishRecord 变更 → 发 `publish_record.changed` 事件 → core 幂等 upsert 投影 → **聚合计算**该文章是否仍"已分发/在途"（口径沿用：存在"未软删且 status 非 failed/cancelled"的记录）。`approved_content_source` / `article_group_source` 去重判定读这个聚合结果。

### 3.3 数据归属：database-per-service（渐进）+ Phase 0 必须先出完整归属矩阵

初期**共享一个 MySQL 实例，按服务分表所有权 + 独立 DB 账号授权**，需要时再物理拆实例。

| 服务 | 拥有的表 |
|---|---|
| **core** | `articles`、`article_*`、`assets`、`stock_*`、`prompt_templates`、`ai_models`、`users`、`platforms`、`audit_logs`、`video_jobs`、`report_events`、`auto_review_decisions`、`article_distribution_records`(新增投影) |
| **distribution-svc** | `publish_tasks`、`publish_records`、`publish_task_accounts`、`task_logs`、`accounts`、`account_members`、`browser_sessions`、`account_login_sessions`、`browser_profile_locks` |
| **generation-svc** | `generation_sessions`、`generation_schemes`、`generation_scheme_runs`、`question_pools`、`question_items`、`category_usage` |

**跨域 FK 远不止一条（评审核实，原稿严重不完整）**。实际存在：

- `publish_tasks` → `users`、`platforms`、`articles`、`article_groups`
- `publish_task_accounts` → `accounts`（+ `publish_tasks`）
- `accounts` → `users`、`platforms`、`assets`(头像)、`accounts`(merged_into 自引用)
- `task_logs` → `assets`
- generation → `users`、`prompt_templates`、`articles`
- `worker_heartbeats`（system 表，被 distribution 写）
- audit / performance 跨 accounts/tasks/articles 读

**若 Phase 1 直接做表级 GRANT，现有代码会立即失败。** 因此 → 见 **§9 ADR-3**：Phase 0 必须先产出一张完整的"**表 / FK / 调用 / 对象存储归属矩阵**"，每条跨域关系明确选择 **软引用 / 快照 / 读模型 / 内部 API** 之一，并定义**每个服务自己的 Alembic 迁移链**及 **expand/contract 顺序**。

### 3.4 鉴权：core 前门 + 内部 delegation token（评审后修正）

- SPA / Claude Code / MCP **只跟 core-api 说话**；core 校验 user JWT（`get_current_user`）后**签发短期内部 delegation token** 扇出。
- **静态 service token 只解决"服务认证"，不解决"用户授权"**：account 成员权限、task 归属、scheme 私有性都由下游领域数据 + `user_id`/`role` 决定（如 `scheme_router.py:_get_owned_scheme` 靠 `user_id`/`role`）。若只传万能 token 会丢失调用者身份。
- delegation token 至少含 `sub`(user_id) / `role` / `aud`(目标服务) / `scope` / `request_id` / `exp`（短期）。**下游继续执行领域授权**，不把授权逻辑上移到 core。
- service token 按**调用方 + scope 区分**，不是三服务共用一个万能 token。

### 3.5 共享基础设施 + 最小权限（评审后收紧）

- **加密密钥 `GEO_SECRET_KEY`（账号凭据密钥）只发给 distribution-svc**——只有它解密 `api_credentials`/`api_token_cache`。core/generation 不该持有这把（原稿"三服务共钥"权限过大）。core 自身若有其它加密列另立密钥。
- **MinIO 可共享基础设施，但用分服务账号 + bucket/prefix 权限**：core（素材/图库）、generation（配图写）、distribution（封面）各自 access key，按 prefix 授权，不共用 root 凭据。

---

## 4. 迁移路径（4 阶段 · 每阶段结束系统完整可用可回滚）

### Phase 0 · 硬化边界 + 出 ADR（零新服务、零风险、纯 core 内重构）

1. 消除跨模块 ORM import（§1.1 那些环），改走对方 service 接口函数。
2. 正式上移 `PublishPayload` 边界（payload 在发布侧构建，core 侧只产出 §3.2 的 `CreatePublishTaskRequest`）。
3. "已分发判定"收敛到单一入口 `get_distribution_status(article_ref)`（内部先查 PublishRecord，将来换读投影表只改一处）。
4. **产出 §9 的 6 项 ADR**——尤其 **ADR-3 完整归属矩阵**（表/FK/调用/对象存储）+ 每服务 Alembic 链 + expand/contract 顺序。这是 Phase 0 的核心交付物。
5. **把 pipeline / scheme run 执行从 daemon thread + 无租约全量复位，改成 durable claim/lease**（条件 UPDATE 抢占 + 租约续期 + 启动只复位过期租约，参照发布 worker 的 `worker_lease_until` 模型）。**这是 core / generation 能多副本的前提**（否则 §6.2 的多 workload 不成立）。
6. 引入 §3.4 delegation token + outbox 事件脚手架（先在单体内自发自收跑通契约）。
7. CI 加"跨模块 import 门禁"（import-linter 或自定义 gate），锁住边界防退化。

### Phase 1 · 剥离 distribution-svc（第一个真服务）

- accounts + tasks + worker 打独立镜像/Deployment；独立 DB 账号只授权发布相关表（**前提：ADR-3 归属矩阵已把跨域 FK 处理完**）。
- core 调发布：进程内函数 → 内部 HTTP + `CreatePublishTaskRequest`（§3.2）。
- distribution-svc 发 `publish_record.changed` 事件 → core 幂等维护 `article_distribution_records` 投影。
- **noVNC 不是"Ingress → distribution-svc"一句话**（`browser.py` 每会话动态分配 display/vnc/novnc 端口）→ 需固定 **WebSocket gateway + session→pod 路由**，浏览器 profile 需**加密 PVC + 节点亲和**。见 §9 ADR-6。
- k8s 起初 browser-worker 仍单实例（单实例约束），但"发布 API 层"与"浏览器执行层"已可分离扩容——为"拆登录处理器 → 多 worker（按账号分片）"性能路线铺路。
- **回滚闸**：`GEO_DISTRIBUTION_MODE=inproc|remote`（详见 §4 回滚 runbook）。

### Phase 2 · 剥离 generation-svc

- ai_generation + 写作/配图 compute 拆成 **generation-api + generation-worker**（§6.2）；**按队列深度扩 worker**，不是简单扩 web Pod。
- pipelines 的 `ai_compose`/`ai_generate` 节点 → 调 generation-svc 内部 API；generation-svc 通过 core 的"创建文章" API 写回。
- 配图 compute（千帆/百度联网并发）随之进 generation-worker，**独立限流/扩容**（缓解记忆里"配图 QPS/并发超时"）。
- **回滚闸**：`GEO_GENERATION_MODE=inproc|remote`。

### Phase 3 · 物理分库（可选，按需触发）

只有当某服务 DB 负载/隔离需求**真实出现**时，才把它的表迁到独立 MySQL 实例。此前一直"同实例分表授权"。**没痛就不做**。

### 回滚 runbook（评审后补 · `inproc|remote` 不是单靠开关秒回滚）

切换/回滚一个已剥离服务，runbook 至少包含：

1. 保留旧镜像 + 其依赖（inproc 路径的代码在 remote 验证稳定 N 周前**不删**，§5.3 物理拆仓在此之后）。
2. DB 权限与**向后兼容迁移**（expand/contract：先扩后缩，回滚窗口内新旧列/表并存）。
3. 切换前：**停入口（drain ingress）→ fence/drain remote worker**（等在途任务收尾或标记）→ 再切开关，**避免 inproc/remote 两边重复执行**同一发布/生成。
4. 事件消费幂等（§3.2 inbox）保证切换期重复投递不产生重复副作用。

---

## 5. 仓库策略：终态多仓（Polyrepo，用户确认）

### 5.1 六仓拓扑

```
geo-core          (core-api: articles/auth/pipelines 编排/SPA/MCP)
geo-distribution  (accounts + tasks + 浏览器 worker)
geo-generation    (ai_generation + 写作/配图 compute)
geo-common        (发布到 GitLab 私有包仓的版本化库：shared/ + core/ + 服务间契约 DTO)
geo-deploy        (k8s manifests / compose / ingress / DB 迁移编排)
geo-skills        (SKILL.md 包：planner/writer/verifier、video、release… 独立迭代)
```

### 5.2 `geo-common`：多仓不塌的前提

- `shared/`（errors/crypto/feishu）、`core/`（config/security/mcp_auth/encrypted_types）、**服务间 HTTP DTO（含 `CreatePublishTaskRequest`）+ 事件 schema** 全收进此包，**发到 GitLab 自带 PyPI 包仓**，三服务仓 `pip install geo-common==x.y.z` 按版本依赖。
- **禁止 copy-paste 共享代码**（正是记忆里"三处副本漂移""跨 OS skew"的坑本体）。
- 契约放 `geo-common` 并版本化；契约测试在各服务仓对 pinned 版本校验，防跨仓静默破约。

### 5.3 物理拆仓的时序（与回滚开关的冲突化解）

`inproc|remote` 回滚前提是**单体代码还和 core 同仓**。因此：

> **把"物理拆仓"放到每个 Phase 的最后一步，而不是第一步。**

- Phase 1 先在**当前仓**里把 distribution 拆成独立可部署单元 + `inproc|remote` 开关，先在生产验证 remote 稳定 N 周；
- **确认稳了，才 `git filter-repo` 把这块历史切出去独立成 `geo-distribution`，同时删掉 core 里 inproc 老路径。**
- generation 同理。物理切仓用 `git filter-repo`/subtree split 保留历史。

### 5.4 `geo-skills`：内容不进 server 镜像，CI 自动发布（用户选 A）

根治记忆里 skill 包**三处副本漂移**（仓库 `templates/`、`~/.claude/skills` 安装版、`E:\1\skills`）+ "官方包更新缺口需手动点上传"：

- **单一真源 = `geo-skills` 仓**。tag/release 时 **CI 用 service token 调 GEO Skill 库上传 API（`POST /api/mcp/skills/{id}/versions`）自动推进运行中的平台**（全自动，无人肉）。
- 平台侧 `install_loop_skills` 照旧拉最新版 → **三处副本收敛成"一个真源 + 自动分发"**。
- Skill 库 API 端点仍留 geo-core；`loop_skills` 模块**从此不含任何 SKILL.md 内容**，只剩打包/版本/校验逻辑。
- bundle sha 跨 OS/行尾纪律（v8/v9/v10 连栽）收敛到 `geo-skills` 一个仓的 CI 算真值。
- CI 上传 token 权限边界见 §9 ADR-4（上传当前 admin 收口，是否放开给 CI token 待定）。

---

## 6. 横切关注点

### 6.1 可观测性（轻量三件套，无专职运维可扛）

- **Correlation ID 贯穿**：core 生成 `request_id`（并塞进 delegation token），内部 HTTP + 事件全程透传，"生成→配图→送审→分发"整链可串。
- **结构化日志集中**：三服务 JSON 日志汇一处（k8s 上 Loki / 阿里云 SLS）。
- **指标复用现有 `resource_metrics`**：每服务各跑一份，超阈值走**现成飞书告警通道**。不上 Prometheus 全家桶。

### 6.2 部署 / CI：一个逻辑服务 = 多个 k8s workload（评审后修正）

原稿"core 2 副本 + generation HPA"与当前**进程内 daemon thread 执行 + 无租约全量复位**的模型冲突，**目前不可直接成立**（须先做 Phase 0 第 5 项 durable lease）。正确的 workload 划分：

| 逻辑服务 | k8s workload |
|---|---|
| **core** | core-api（无状态 web，可多副本）+ orchestrator-worker/scheduler（pipeline 执行 + 定时，durable lease 后可多副本） |
| **generation** | generation-api + generation-worker（**按队列深度扩 worker**，不是扩 web Pod） |
| **distribution** | distribution-api + browser-worker（单实例→按账号分片）+ noVNC gateway |

- run 必须有 **durable claim/lease**（Phase 0 前置改造）。
- **Ingress**：`/api`→core-api、`/mcp`→core-api、noVNC→distribution 的 **WS gateway**（非直连 browser-worker，§9 ADR-6）。
- 每个服务仓一条 GitLab CI 各自 build+push；沿用 `registry` + skopeo + tag 触发部署。
- **`geo-deploy` 是唯一拼三镜像版本上线的地方**；`geo-release` skill 演进为"在 `geo-deploy` bump 三服务镜像版本 + 各服务仓独立 tag-release"。
- **跨服务契约变更 = 跨仓协调 MR**（先发 `geo-common` 新版 → 服务仓升依赖）。

### 6.3 服务间错误处理

- 内部 HTTP 失败 → 有限重试 + `inproc` 回滚开关兜底（不引入重量级熔断器）。
- 上游 LLM/平台错误 → 复用现成 `mcp_exception_response`（litellm/httpx → 502 可重试语义），推广到 generation-svc 内部返回。
- 事件可靠性 → outbox + at-least-once + 消费端幂等（§3.2 inbox + `client_request_id` 套路）。

### 6.4 测试与验证

- **契约测试**：`geo-common` 里的 HTTP DTO + 事件 schema 各一组 schema 校验，防跨服务静默破约。
- **现有 pytest 全保留**：靠 `inproc` 模式，现有集成测试继续打单体路径跑绿；`build_test_app` 每服务可独立测。
- **端到端**：一个 compose-based e2e 起全部 3 服务 + 依赖，跑通"生成→配图→送审→分发"整链——每个 Phase 验收门禁。
- **CI 门禁**：沿用现有硬门禁（ruff/mypy/typecheck/build）+ Phase 0 的"跨模块 import 门禁"。

---

## 7. 净变化与代价（睁眼选）

多仓相比单仓，多了 `geo-common` 私有包 + `geo-deploy` 编排仓 + `geo-skills` 内容仓、CI 从 1 条变 6 条、契约变更要跨仓协调发版；换来各服务仓彻底独立的代码/权限/发布边界（"组织边界"诉求这样才算真拿到）。

**主要风险**：版本 skew（记忆里反复栽的坑）——靠 `geo-common` 版本化 + 契约测试 + 单一真源自动分发压制，但不会归零，需团队纪律。

---

## 8. 待办 / 开放问题（落地时逐 Phase 细化）

- [ ] `CreatePublishTaskRequest` 里封面/素材走 MinIO object key 引用还是内联 bytes。
- [ ] outbox 表结构 + Redis Streams 消费组的幂等键 / `aggregate_version` 具体设计。
- [ ] `geo-common` 版本发布节奏与三服务仓升级协调（SemVer + 向后兼容窗口）。
- [ ] k8s 平台定型（自建 vs 阿里云 ACK）后，ingress / HPA / secret 管理具体清单。
- [ ] delegation token 用 JWT（复用现有 hmac）还是独立签发链路；`exp` 时长。

---

## 9. 阻塞 ADR 清单（Phase 1 前必须解决 · 评审产出）

每项在 Phase 0 各写一份 ADR 定稿后，本文档状态方可升为"设计已定稿"。

- **ADR-1 · 跨服务发布契约**：定义 `CreatePublishTaskRequest` DTO（§3.2），确认 `PublishPayload` 保持 distribution 内部、`article_ref` 只作溯源、正文走 snapshot。
- **ADR-2 · CQRS 分发投影**：定义 `article_distribution_records` 投影表 + event inbox 幂等 + `aggregate_version` 防乱序 + 聚合口径（§3.2）。
- **ADR-3 · 数据/FK 归属矩阵**：全量"表/FK/调用/对象存储"矩阵，每条跨域关系选定 软引用/快照/读模型/内部 API；每服务 Alembic 链 + expand/contract 顺序（§3.3）。
- **ADR-4 · 授权透传**：core 签发短期 delegation token（sub/role/aud/scope/request_id/exp），下游做领域授权；service token 按调用方+scope 区分；`GEO_SECRET_KEY` 只给 distribution；MinIO 分服务账号+prefix（§3.4/§3.5）。
- **ADR-5 · 执行模型 durable lease**：pipeline / scheme run 从 daemon thread + 无租约全量复位 → durable claim/lease；确立"逻辑服务 = 多 workload"（§4 Phase 0-5 / §6.2）。这是多副本的前提。
- **ADR-6 · 回滚 & noVNC runbook**：`inproc|remote` 切换的 fence/drain + expand/contract 迁移 + 旧镜像保留；noVNC 固定 WS gateway + session→pod 路由 + 浏览器 profile 加密 PVC/节点亲和（§4 回滚 / §6.2）。
