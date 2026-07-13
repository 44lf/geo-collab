# 对抗评审前置门（异步质量闸门）· 设计稿

- 日期：2026-07-13
- 状态：设计已确认，待写实现计划（writing-plans）
- 范围：**phase-1 可落地**——在现有 `/goal` 生文 loop 与内容管理之间，插入一道服务端**异步对抗质量门**；不训练模型、判别走 LiteLLM。
- 思路来源：`Downloads/2026-07-13-gan-discriminator-scoring-design.md`（借 GAN 判别范式）。本稿**只借思路**，数据建模与接入方式按本仓库现状重新裁剪，与来源稿多处不同（见「与来源稿的差异」）。

---

## 一、背景与目标

**现状**：`/goal` loop 生文后由 `geo-article-verifier` 子助手**内联自评绝对分**（4 维 0-100，门槛 `total≥70`），过线即 `review_status=pending` 进待审核库。绝对分无参照系、会漂移、奖励平庸。

**目标**：把「生成 → 直接进待审核」改为「生成 → **待对抗评审** → 过对抗门 → 待审核 → 人审」。对抗门用「这篇 vs 1~3 篇同类高质量真品」判 `realness` 相对分作为进人审队列的自动闸；**人审仍是最终 truth**。

**关键约束（用户已拍板）**：
- 生文 loop 的**写作机制不动**（writer 子助手、选题、生成量为准）。
- 对抗门是**唯一自动门**：删除 verifier 内联自评。
- 对抗门**异步**跑（定时 / 手动；不阻塞 loop）。
- 高质量库是**会员表 FK→articles**，不复用来源稿的 snapshot 建模。
- 分类用**源问题分类去规范化字符串**，不新建 taxonomy。

---

## 二、数据流总览

```
loop 生文（writer 子助手不变）
   │  save-from-mcp：落 source_question_category 快照 + review_status = adversarial_pending
   ▼
┌──────────── 待对抗评审 review_status = adversarial_pending ────────────┐
│  异步对抗门 runner（定时线程 / 手动端点触发）                             │
│   ① 取一篇 adversarial_pending 文章                                     │
│   ② 按 source_question_category 召回 1~3 篇高质量参考（万能字段兜底）    │
│   ③ LLM 判别 → realness / gap_diagnosis / policy_safety                │
│   ④ 写 adversarial_review_results 一行                                  │
│   ⑤ policy_safety ≥ 硬线 且 realness ≥ 过线 → review_status = pending   │
│       否则留 adversarial_pending（前置页可见分数/诊断、人工干预）        │
└────────────────────────────────────────────────────────────────────┘
   │  过门
   ▼
待审核库 review_status = pending → 人工审核 → approved → 可分发
```

**三不变式**：
1. 人审仍是最终 truth——对抗门只在 `pending` 之前挡，不动 `approved`。
2. 检索/判别失败或库空 → **降级不阻塞**（记 `degraded` 直接放行）。
3. 计数事实查 DB——loop 停止条件按 DB 生成量，不信子助手自报。

---

## 三、数据模型改动

### 3.1 `articles` 表加 3 列（迁移）

| 列 | 类型 | 用途 |
|---|---|---|
| `source_question_category` | `varchar(200)` null, index | 去规范化存源问题分类（`QuestionItem.category` 快照）；对抗门检索的 join key |
| `is_reference` | `bool` default 0, server_default `'0'`, index | 参考/竞品文章标志；内容列表 & 分发查询过滤掉 |
| `review_status` 枚举**加值** | — | 迁移改 CHECK `ck_articles_review_status`：`review_status in ('adversarial_pending','pending','approved')` |

- `review_status` 默认值仍 `approved`（既有 + 手工内容语义不变）；仅 loop 的 save 路径显式置 `adversarial_pending`。
- `source_question_category` 沿用现有 `source_agent_name` / `source_template_name` 的「去规范化仅展示/检索、不做外键」惯例。

### 3.2 新表 `quality_reference`（会员表，FK→articles）

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | int PK | |
| `article_id` | int FK→articles(ON DELETE CASCADE) NOT NULL, **unique** | 金标准成员；自产精品 + 手录竞品都先入 `articles` 再标成员 |
| `category` | `varchar(200)` null, index | 分类覆写；空则回退文章的 `source_question_category` |
| `source` | `varchar(30)` | `own_approved` / `competitor_manual` / `competitor_crawled`（预留，phase-1 不做） |
| `source_url` | `varchar(1000)` null | 竞品来源链接（可选） |
| `platform` | `varchar(100)` null | 竞品来源平台（可选） |
| `added_by` | `varchar(120)` | 采纳/录入人（MCP 路径取 operator id 字样） |
| `is_active` | `bool` default 1, index | 下架开关（劣质样本停用不删，防污染 real 分布） |
| `created_at` | datetime | |

### 3.3 新表 `adversarial_review_results`（判别结果，FK→articles）

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | int PK | |
| `article_id` | int FK→articles(ON DELETE CASCADE), index | 被判文章（一对多：每跑一次写一行，保留历史） |
| `realness` | int null | 0-100 主判分；`degraded` 时为 null |
| `policy_safety` | int null | 合规分（硬否决用）；`degraded` 时为 null |
| `verdict` | `varchar(20)` | `passed` / `failed` / `degraded`（无参考/判别失败的放行降级） |
| `gap_diagnosis` | text null | 与真品差距诊断（回流下一轮参考） |
| `ref_article_ids` | JSON | 本次用了哪几篇参考（`[]` 时说明降级） |
| `model_label` | `varchar(120)` null | 判别所用 LiteLLM 模型串 |
| `trigger_source` | `varchar(20)` | `scheduled` / `manual` / `per_save` |
| `created_at` | datetime | |

> 刻意**不复用 `AutoReviewDecision`**（用户明确要单独建表）；两者语义、生命周期、消费方均不同。

---

## 四、save 路径 & 分类落库

- `SaveArticleFromMcpPayload` / MCP `save_article` **工具签名不变**——后端已有 `question_item_id`，在 `save_article_from_mcp` 内查 `item.category` 直接快照进 `article.source_question_category`。
- 同一函数把 `review_status` 由 `"pending"` 改为 **`"adversarial_pending"`**。
- 竞品手录路径（见 §六）建 article 时置 `is_reference=True` + `review_status='approved'`（终态，**不落 adversarial_pending 队列、也不落待审队列**），发布安全靠 `is_reference` 过滤兜底（§八），并写 `source_question_category = category`。

> 结论：比最初设想的「改 MCP 签名」更省——分类是后端从 `question_item_id` 派生的一列快照，MCP 层零改。

---

## 五、对抗评审 runner（核心新模块）

新模块 `server/app/modules/adversarial_review/`：`models.py` + `service.py` + `router.py` + `scheduler.py`，各文件单一职责。

### 5.1 判别一篇（纯函数式 + 短生命周期 session）

```pseudo
review_one(article_id, trigger_source):
    a = get_article(article_id)                       # plain_text + source_question_category
    refs = pick_refs(category=a.source_question_category, k=TOPK)   # §5.2
    if len(refs) == 0:                                # 防御性：万能库也空（理论上不出现）
        write_result(verdict="degraded", refs=[]); promote_to_pending(a); return
    v = llm_judge(refs, a)                            # §5.4，LiteLLM ai_format 模型
    write_result(realness=v.realness, policy_safety=v.policy_safety,
                 gap_diagnosis=v.gap, ref_article_ids=[r.id for r in refs],
                 verdict=("passed" if _passes(v) else "failed"),
                 trigger_source=trigger_source)
    if _passes(v):                                    # policy 硬线 且 realness 过线
        promote_to_pending(a)                         # review_status: adversarial_pending → pending
    # 否则留 adversarial_pending（前置页人工处置）

_passes(v) := v.policy_safety >= POLICY_HARD_MIN and v.realness >= REALNESS_PASS
promote_to_pending(a) := a.review_status = "pending"（仅当当前为 adversarial_pending 时）
```

### 5.2 检索（phase-1 简单版）

- 按 `source_question_category` 过滤 `quality_reference`（`is_active=True` join `articles`），取**最近 1~3 篇**（`created_at` desc，`limit=TOPK`）。
- **万能字段兜底**：先按真实类目召回；命中 0 篇则回落「万能」类目（哨兵串，见 §六）再召回。**有多少用多少**（1~3 篇），不设 `MIN_REFS` 硬地板——万能库种子保证恒 ≥1。
- **不做 ngram / 向量**（留 phase-2）——早期语料小、全万能类目，类目过滤足够。

### 5.3 触发（三种形式）

- **手动**：`POST /api/adversarial-review/run`（批量跑当前 `adversarial_pending`，可带 `limit`）+ `POST /api/articles/{id}/adversarial-review`（跑单篇）。MCP token 与 user JWT 各一份（前置页按钮 / Loop 侧各用）。
- **定时**：`scheduler.py` 后台线程，开关 `GEO_ADVERSARIAL_SCHEDULER_ENABLED`（默认关）、周期 `GEO_ADVERSARIAL_SCHEDULER_INTERVAL_SECONDS`、时区 `GEO_SCHEDULER_TZ`。复用 pipeline scheduler 的**条件 UPDATE claim** 范式防重叠；批量扫 `adversarial_pending`。**仅同 web 进程后台线程**（与 generation/pipeline 一致，无独立 worker），`bg_session_factory=SessionLocal`。
- **per-save 异步（可选，默认关）**：save 成功后 best-effort spawn 一次 `review_one(trigger_source="per_save")`，实现「刚生成马上判」。开关 `GEO_ADVERSARIAL_PER_SAVE_ENABLED`。

### 5.4 LLM 判别

- 走 LiteLLM `ai_format` 模型（同 `auto_review.score_articles` 解析路径，须短生命周期 session）。
- Prompt 结构：给「1~3 篇真实高质量文（real）+ 1 篇待判文（fake）」，要求输出 JSON `{realness:0-100, policy_safety:0-100, gap_diagnosis:str}`，判「更像真品高质量，还是像典型 AI 水文」。
- 异常一律走 `core/mcp_errors.mcp_exception_response`（MCP 端点）/ 记 `verdict="degraded"` 放行（定时/批量路径 best-effort，单篇失败不拖累整批）。

### 5.5 冷启动 / 降级

- 正常路径：万能库种子保证恒有参考，`review_one` 走完整判别。
- 防御性降级（理论上不触发）：召回 0 篇或 `llm_judge` 抛错 → 记 `verdict="degraded"` + **直接放行进 pending**，不阻塞。`degraded` 计数供运营判断「该补哪个类目的库」。
- **上线前置**：给「万能」类目高质量库人工灌 ≥5 篇种子。

---

## 六、高质量库管理（CRUD 模块）

`quality_reference` 接口（user JWT，前端 / 运营用）：

- `POST /api/quality-reference/adopt` — 采纳一篇 `approved` 自产文章：`{article_id}` → 建 `quality_reference` 行（`source=own_approved`）。文章本体不动。
- `POST /api/quality-reference/import-competitor` — 手录竞品：`{title, markdown, category, source_url?, platform?}` → 建 article（`is_reference=True` + `review_status='approved'` + `markdown_to_tiptap/html` + `source_question_category=category`）+ 建 `quality_reference` 行（`source=competitor_manual`）。
- `GET /api/quality-reference` — 列表（按 `category` / `source` / `is_active` 过滤 + 分页）。
- `PATCH /api/quality-reference/{id}` — 下架 / 改类目（`is_active` / `category`）。

**万能字段**：手录一批历史文章时 `category="通用"`（哨兵串，配置化 `GEO_QUALITY_UNIVERSAL_CATEGORY`）统一填充；后续普通生文各自带真实 `source_question_category`。检索先按真实类目、命中不足回落「通用」。

---

## 七、Loop skills 改动（对抗门唯一自动门 + 生成量停）

- **删 verifier**：orchestrator 主循环删掉 verifier subagent 段（`geo-goal-orchestrator/SKILL.md:255-316` 区块）；`geo-article-verifier` skill 从 loop 流程下线（保留文件休眠或删，实现计划再定）。
- **删 loop 内重写**：`REWRITE_CAP` / `prior_feedback` / `q_exact` 重写整套移除——失败即换下一题（重写预算恒 0）。`问题Id=` / 题材 / 模板锁**仍决定 worklist 选取**，但不再触发同题重写。重写职责让位给「对抗门不过 → 前置页人工触发重写 / 换题」。
- **停止条件改生成量**：退出闸门由「数 verifier approved 决策」改为「数今日 loop 生成量」——按 `source_agent_name="loop"` + `metrics.writer_model==model_label` + `created_at` 窗口数 `Article` 行。`netto.count >= N_eff` 即停。
  - 后端：`auto_review/service.py` 的 `list_recent_decisions` 旁加 `count_loop_generated(model_label, since_hours)`（数 Article：`source_agent_name="loop"` + `metrics.writer_model` + `created_at` 窗口，不数 AutoReviewDecision）。
  - MCP：**不改** `list_today_loop_articles` 既有 `decided_by` 语义（其它 loop 配方可能依赖），**新增姊妹端点/工具** `list_today_generated`（MCP token）供 orchestrator 停止条件调用。
- **writer 不变**：仍写 markdown + `save_article`；配图时机由 orchestrator 保留（save 后 review_status 自动为 adversarial_pending，writer 无感）。
- **叙述规范**：orchestrator 主对话叙述新增术语映射（「待对抗评审」「对抗门」中文表述），沿用现有「禁英文/内部术语」纪律。

---

## 八、前端前置页

- 内容管理**前面加「待对抗评审」页**（`web/src/features/content/` 新增视图 + `web/src/api/` 对应客户端）：
  - 列 `review_status='adversarial_pending'` 文章 + 最新一条 `adversarial_review_results`（realness / verdict / gap_diagnosis）。
  - 操作：手动触发判别（单篇 / 批量）、直接放行进待审核、查看差距诊断。
- 内容列表主页查询（`articles/services/feed.py` 的 list 查询）过滤掉 `is_reference=True` 与 `adversarial_pending`（只显 `pending`/`approved` 的可运营内容）；分发相关查询同样排除 `is_reference`。

---

## 九、MCP 工具 & 端点清单

- 新后端端点：
  - `adversarial-review/*`：批量 run / 单篇 run / list pending（MCP token + user JWT）。
  - `quality-reference/*`：adopt / import-competitor / list / patch（user JWT）。
- 新 MCP 工具：`list_today_generated`（orchestrator 停止条件必需，见 §七）；其余按 Loop 侧是否需要（实现计划再定最小集）：候选 `run_adversarial_review` / `adopt_quality_reference` / `import_competitor_reference`。
- **`MCP_TOOLS_COUNT`（`mcp_catalog/connect_router.py`）随新增工具数同步更新**。
- `save_article` 路径行为改（§四），**工具签名不变**。

---

## 十、阈值配置（`GEO_` 前缀）

| 配置 | 默认 | 用途 |
|---|---|---|
| `GEO_ADVERSARIAL_REALNESS_PASS` | 65 | 主闸门过线分 |
| `GEO_ADVERSARIAL_POLICY_HARD_MIN` | 80 | 合规硬否决线 |
| `GEO_ADVERSARIAL_TOPK` | 3 | 召回参考上限（有多少用多少，≤ 此值） |
| `GEO_ADVERSARIAL_SCHEDULER_ENABLED` | false | 定时对抗门开关 |
| `GEO_ADVERSARIAL_SCHEDULER_INTERVAL_SECONDS` | 300 | 定时周期 |
| `GEO_ADVERSARIAL_PER_SAVE_ENABLED` | false | per-save 异步开关 |
| `GEO_QUALITY_UNIVERSAL_CATEGORY` | `通用` | 万能字段哨兵串 |

---

## 十一、分期

- **Phase-1（本次）**：3 列迁移（含 CHECK 改值）+ 两新表 + save 路由改 + 对抗 runner（检索 / 判别 / promote / 降级）+ 手动 & 定时触发 + 库 CRUD + loop 停止条件改 & 删 verifier/重写 + 前置页 + 万能库灌种子。
- **Phase-2（later，本稿不做，仅标注）**：ngram / 语义检索、per-save 内联默认开、竞品自动采集（`competitor_crawled`）、判别通过率 / 一致性看板、对抗门不过的自动重写回流、成对 ELO。

---

## 十二、测试

- 迁移：CHECK 约束新值可写 `adversarial_pending`、旧值仍合法；两新表建成 + FK/唯一约束生效（`@pytest.mark.mysql`）。
- `review_one` 纯函数：过线 / 不过线 / 0 参考降级三分支；`promote_to_pending` 仅在 adversarial_pending 时迁移。
- 检索 `pick_refs`：真实类目命中 / 回落万能 / 取 1~3 篇上限。
- scheduler：条件 UPDATE claim 防重叠（同 pipeline scheduler 测法）。
- save 路由：`save_article_from_mcp` 落 `adversarial_pending` + `source_question_category` 快照。
- 内容列表：`is_reference=True` 与 `adversarial_pending` 被主列表 / 分发查询过滤。
- netto：`count_loop_generated` 按 `source_agent_name` + `writer_model` + 时间窗计数正确。
- 库 CRUD：adopt（自产）/ import-competitor（建 is_reference article + 成员行）/ 下架。

---

## 十三、与来源稿的差异（借思路不照搬）

| 维度 | 来源稿 | 本稿（按仓库现状） |
|---|---|---|
| 分类字段 | 新 `quality_reference.category` 自由串 + 文章侧未定 | **源问题分类 `QuestionItem.category` 去规范化快照**进 `articles.source_question_category` |
| 高质量库 | snapshot 表（自带 title/plain_text） | **会员表 FK→articles**（竞品也入 articles + `is_reference`） |
| 判别定位 | 替换 verifier 的**同步**主闸门 | **异步前置门**：生成 → adversarial_pending →（异步过门）→ pending |
| 判别结果表 | 复用 `AutoReviewDecision` | **单独新表 `adversarial_review_results`** |
| loop 停止 | 沿用 netto 过审数 | **改数生成量**（真异步，删 verifier + loop 内重写） |
| 检索 | ngram FTS | **类目过滤取最近 1~3**（ngram 留 phase-2） |
| 冷启动 | `< MIN_REFS` 退回绝对分 | **有多少用多少**（万能兜底恒 ≥1）；0 参考才防御性 degraded 放行 |
| review_status | 不动 | **加 `adversarial_pending` 值**（迁移改 CHECK） |
