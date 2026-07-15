# 对抗评审质量门（同步·in-loop 版）· 设计稿

- 日期：2026-07-13（初稿，异步方案）→ **2026-07-14 重写为同步方案（需求已确认）**
- 状态：**需求已确认，待写实现计划（writing-plans）**
- 范围：**phase-1 可落地**——在 `/goal` 生文 loop 内，**同步**给每篇新生成文章加一道「对抗判分」：verifier 子代理在同一次运行里读 1~3 篇同类高质量参考、判一个相对分作为**参考值**附到文章上；文章照常进未审核库，人审仍是最终 truth。
- 思路来源：`Downloads/2026-07-13-gan-discriminator-scoring-design.md`（借 GAN 判别范式）。本稿只借「拿真品当参照系」的思路，落地按本仓库现状裁剪。
- **重写说明**：初稿设计的是「入库后服务端异步 runner + scheduler + `review_status='adversarial_pending'` 状态机」。用户确认改为**同步**：生文和审核在同一次 Claude Code 运行里做完，不引入服务端异步机器、不加 review_status 值、不建待对抗审核库。旧异步方案见文末「与初稿（异步方案）的差异」。

---

## 一、背景与目标

**现状**：`/goal` loop 生文后由 `geo-article-verifier` 子代理内联做 4 维自评（factuality / readability / style / policy_safety，`total≥70` 过线），过线即 `save_article` → `review_status=pending` 进未审核库。绝对分无参照系、会漂移、奖励平庸。

**目标**：在**保留 4 维自评**的前提下，给 verifier **后置**加一段「对抗判分」——拿这篇 vs 1~3 篇同类**高质量真品**判一个相对分（realness）。分只是**给人审多一个信号的参考值**，不改文章去向（照进未审核库）。

**关键约束（用户已确认）**：
- **同步 in-loop**：对抗判分在 verifier 子代理里跑完，通过 MCP 工具 + skill 实现；**无服务端异步 runner / scheduler / per-save 触发**。
- **对抗分纯 advisory**：文章无论对抗分高低都进未审核库；loop 的 retry / 停止条件**仍挂在 4 维自评**上（不改今天的循环逻辑）。
- **不动 `review_status`**：不加 `adversarial_pending` 值、不建待对抗审核库（`save` 仍置 `pending`）。
- **高质量库自包含**：外部参考文章可**不入 articles 表**，`quality_reference` 表**违反范式**存三份正文快照（含 Tiptap `content_json`，为将来判图保住图节点）；自产文章采纳时也快照进来（不留活引用）。
- **判分零配置**：判分由 host（Claude 子代理）做，不调服务端 LiteLLM；判分规则写在 skill markdown 里。
- **问题溯源只做 /goal**：文章加 `source_question_category` + `source_question_texts`，只在 MCP save 路径填；scheme / pipeline 生文路径不碰。

---

## 二、数据流总览（同步）

```
/goal orchestrator（每篇一个全新上下文的 verifier 子代理）
   │
   ▼
verifier 子代理：
   ① get_article 读候选（+ source_question_category / source_question_texts）
   ② 4 维自评（前置，保留）——不过线：短路，不判对抗，按 4 维走 retry/换题
   ③ 过线 → 循环 N 次：
        pick_quality_references(category, k)  # 服务端随机取 k 篇、正文截断
        依 skill 内的对抗判分规则给一个 realness 分
   ④ N 个分在 skill 内求平均
   ⑤ record_adversarial_score(article_id, 平均分)   # 只写一个数
   │
   ▼
文章照常在未审核库（review_status=pending），列表多显一个对抗分字段，人审最终裁定
```

**不变式**：
1. 人审仍是最终 truth——对抗分只是附加信号，不做任何自动闸。
2. 参考库空 / 判分异常 → 降级：跳过对抗判分（`adversarial_score` 留 NULL），不阻塞、不影响 4 维走向。
3. loop 计数 / 停止 / retry 全挂 4 维自评，与今天一致；对抗判分是纯附加步。

---

## 三、数据模型改动

### 3.1 `articles` 表加 3 列（全 nullable，迁移）

| 列 | 类型 | 用途 |
|---|---|---|
| `source_question_category` | `varchar(200)` null, index | 去规范化快照：这篇来自哪个问题类别（`QuestionItem.category`）。参考匹配键 + 采纳时自动带分类 |
| `source_question_texts` | `JSON` null | 去规范化快照：这篇用了哪些提问词（`["提问词A","提问词B"]`，一个类别可选 1~N 词）。仅记录/后续按词筛选用 |
| `adversarial_score` | `int` null | 对抗判分（N 次求平均）。NULL=未判 / 降级 |

- **`review_status` 一个字不改**（不加值、CHECK 不动、model `__table_args__` 不动）。文章照旧默认 `approved`、loop save 路径置 `pending`。
- 两个 `source_question_*` 沿用 `source_agent_name` / `source_template_name` 的「去规范化仅展示/检索、不做外键」惯例——`question_items` 是飞书镜像（会软删/重同步），FK 会烂，故只快照。
- `source_question_texts` 用 JSON 数组而非分隔符串：提问词是自由中文、本身含顿号/逗号，任何分隔符都会撞内容；JSON 支持后续 `JSON_CONTAINS` 按问题词精确筛选。

### 3.2 新表 `quality_reference`（自包含快照，非纯 FK 会员表）

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | int PK | |
| `origin` | `enum('own','external')` NOT NULL, index | **清晰的来源标志，服务端按入口盖章、绝不手填**（见 §4） |
| `article_id` | int FK→articles(ON DELETE **SET NULL**), null, **UNIQUE** | 自产溯源 + 防同一篇重复采纳；外部文章 = NULL。SET NULL：删源文章不删已冻结的参考快照 |
| `title` | `varchar(300)` NOT NULL | 快照 |
| `content_json` | `Text` | Tiptap 结构快照（含 image 节点）→ 只读渲染 + 将来判图的图源 |
| `content_html` | `Text` | 渲染 HTML 快照（转换器免费产） |
| `plain_text` | `Text` NOT NULL | 判分文本材料 + `content_hash` 归一化源 |
| `content_hash` | `char(64)` NOT NULL, index | `sha256(归一化(title + plain_text))`，外部粘贴查重 |
| `category` | `varchar(200)` null, index | 参考匹配键。**可空**（外部允许不填）；空 = 通用兜底池 |
| `source_url` | `varchar(1000)` null | 外部溯源（可选） |
| `platform` | `varchar(100)` null | 外部来源平台（可选） |
| `added_by_user_id` | int FK→users, null | 采纳/录入人（接口走 user JWT，存真实用户 FK） |
| `is_active` | bool default 1, index | 下架开关（劣质样本停用不删，防污染 real 分布） |
| `created_at` / `updated_at` | datetime | |

外加 `FULLTEXT(plain_text) WITH PARSER ngram`（近似查重软提示，phase-1 做）。

**三份正文是故意的快照冗余**：参考不可变（只读不改），CLAUDE.md「三份要同步」的坑只在编辑时成立，这里插入即冻结、无同步负担。自产采纳时**拷贝**源文章三份（不留活 FK 引用），源文章日后改/删不影响这份「金标准」。

> 刻意**不复用 `AutoReviewDecision`**、也**不建对抗判分历史表**：对抗分只存一个平均数在文章上（§7），参考明细 / 每次用哪几篇都不记（N 次随机取参考、太多且用不上）。

---

## 四、高质量库 CRUD + 查重（user JWT）

**可见性**：高质量库是**全员共享的组织级金标准池**（沿用问题池的共享语义）——任何登录用户可 CRUD，`pick_quality_references`（MCP，loop 侧）看全部 `is_active=True`、**不按用户过滤**；`adopt` 仅校验源文章对本人可见（admin 不限）。`added_by_user_id` 仅作溯源、不作可见性过滤。

来源由**入口**决定、服务端盖章，用户永不手填 `origin`（同 `save_article_from_mcp` 服务端盖 `source_agent_name` 的纪律）：

- `POST /api/quality-reference/adopt` — **采纳站内文章**（自产快速录入）：`{article_id}`。
  - 服务端硬校验 `review_status='approved'` + 属本人 + 未删（高质量文章必已过审）；
  - 快照文章三份正文 + title + `category = article.source_question_category`（无则前端回落下拉选）；
  - 置 `origin='own'`、填 `article_id`、算 `content_hash`；
  - 前端「采纳」入口 = 搜索框（**全数字→按 id 精确 `get_article`；否则→标题/正文 FTS `list_articles(query=, review_status='approved', user_id=me)`**）+ 结果列表（已采纳的靠 `article_id UNIQUE` 置灰/幂等）+ 一键采纳。
- `POST /api/quality-reference/import` — **录入外部文章**：`{title, markdown, category?, source_url?, platform?}`。
  - `markdown_to_tiptap` / `markdown_to_html`（复用 save 路径那对转换器）出三份正文；`plain_text` = 归一化 markdown；
  - 置 `origin='external'`、`article_id=NULL`、算 `content_hash`；
  - 表单极简：title + category（下拉，可留空）+ 一个 markdown 粘贴框 + 可选 url/platform。
- `GET /api/quality-reference` — 列表（按 `origin` / `category` / `is_active` 过滤 + 分页）。
- `PATCH /api/quality-reference/{id}` — 下架 / 改类目（`is_active` / `category`）。

**category 下拉候选源** = `QuestionItem.category`（问题分类）∪ 已有 `quality_reference.category`（去重）。**选不打**——`pick_refs` 靠 category 字符串精确匹配，手打会把同一类目劈成好几个、参考永远匹配不上候选。

**查重两层**：
- **exact（硬，幂等）**：`article_id UNIQUE`（自产不重复采纳）+ 插入前查 `content_hash`；命中 → **返回/复活已有那条**（含撞到 `is_active=False` 的旧行时提示重启用），不报错、不建重复行。
- **near-dup（软，给人判）**：`import` 时拿粘贴正文前 N 字跑 `MATCH...AGAINST`（ngram），top-K 命中列为「疑似重复」让人眼判，**不硬挡**（模糊匹配有误报）。

---

## 五、MCP 工具（+2，`MCP_TOOLS_COUNT` 27→29）

- `pick_quality_references(category, k)` — 服务端按 category 取 `is_active=True` 参考：**先精确类目、不足回落 `category IS NULL` 通用池**；**每池内优先取 `origin='external'`（人写/竞品）、`own` 只补足**——命门：不让池子被自产 AI 稿占满退化成「AI 判 AI」；随机取 ≤k 篇（不用 recency——非质量信号、小库过拟合）；**返回时每篇 `plain_text` 服务端截断**（`GEO_ADVERSARIAL_REF_TRUNCATE_CHARS`，复用 auto_review `[:4000]` 惯例）保护子代理上下文，**返回项带 `origin`** 让 skill/人审看得到混合比例。phase-1 返回 `plain_text`（判文字）；`content_json`/图 URL 留 phase-2 判图时再返。
- `record_adversarial_score(article_id, score)` — 只写 `articles.adversarial_score`（skill 已在 skill 内对 N 次判分求平均，传一个数）。不写明细。

两工具都走 `Depends(require_mcp_token)` 子路由 + `mcp_exception_response` 兜底。`save_article` / `get_article` 复用现有。

---

## 六、save 路径 & 问题溯源（只做 /goal）

- `save_article_from_mcp`（[articles/routers/mcp.py](../../server/app/modules/articles/routers/mcp.py)）payload 加**可选** `question_item_ids: list[int]`（不传 → 回落 `[question_item_id]`，向后兼容）。
- 服务端按这批 item 快照：`source_question_category`（同批共有的类别；一个类别一篇文章）+ `source_question_texts = [item.question_text ...]`。
- 工具签名相应扩展（`save_article` 加可选 `question_item_ids`）；`/goal` writer/orchestrator skill 传它用了的问题项列表。
- **scheme_executor / pipeline（ai_generate·ai_compose·question_source）一律不碰**——那些路径的 `source_question_*` 留 NULL（列 nullable，无副作用）。将来要全铺是独立增强，本期不做。

---

## 七、verifier skill 改动（`geo-article-verifier` + orchestrator）

- **保留 4 维自评**（前置）：完全不动今天的评分 + `submit_review_decision`；retry / netto 停止条件**仍挂 4 维**（初稿「删 verifier + 改数生成量」的方案**作废**）。
- **后置加对抗判分**：
  1. **短路**——4 维不过线就不判对抗（烂稿不付加载参考的上下文，代价是这类稿 `adversarial_score` 留 NULL，可接受）；
  2. 过线 → 循环 N 次：`pick_quality_references(category, k)`（每次随机取，参考各异）→ 依 skill 内判分规则给一个 realness；
  3. N 个分**在 skill 内求平均** → `record_adversarial_score(article_id, 平均分)`；
  4. 参考库空 / 判分异常 → 跳过（不写分、不阻塞）。
- **对抗判分规则 + 提示词写在 skill markdown 里**（host 判分、零配置；不建服务端 prompt_templates scope、不做 jinja2 tab）。N 的大小、判分规则都在 skill 里调，服务端不动。
- **writer**：`save_article` 时带 `question_item_ids` 列表（§6）。
- **category 从哪来**：verifier 靠 `article_id` → `get_article` 读 `source_question_category`，凭 id 即自足、不用外部再喂。

---

## 八、前端

- **新增「高质量库」管理页**（`web/src/features/` 下新 feature + `web/src/api/` 客户端）：
  - 列表（按 origin/category/is_active 过滤）；
  - 「采纳站内文章」入口（搜 id/标题 + 一键）；「录入外部文章」入口（markdown 粘贴表单）；
  - 点某条 → **只读 Tiptap 查看器**（`editable:false`、无工具栏/保存键，加载 `content_json`）。**遵守 Tiptap v3 纪律**：StarterKit 已内置 link/underline，不重复注册（否则含链接文档 setContent 会空白，见 `bug-permalink-deeplink-blank-tiptap-v3`）。
- **文章列表**：现有四维分（`auto_review_score`）旁**新增对抗分字段**（`adversarial_score`）——`ArticleListRead` 加一列、[feed.py serialize_article_summaries](../../server/app/modules/articles/services/feed.py) 照 `auto_review_score` 那样多拼一个。

---

## 九、配置（`GEO_` 前缀）

| 配置 | 默认 | 用途 |
|---|---|---|
| `GEO_ADVERSARIAL_TOPK` | 3 | `pick_quality_references` 单次召回上限（有多少用多少，≤ 此值） |
| `GEO_ADVERSARIAL_REF_TRUNCATE_CHARS` | 4000 | 单篇参考 `plain_text` 返回截断长度（保护子代理上下文） |

> 判分阈值 / N 次数 / policy 硬线**不在服务端配**——判分是 host（skill）做的，这些都在 skill markdown 里；对抗分纯 advisory、无服务端闸门。

---

## 十、分期

- **Phase-1（本次）**：
  - **W1 · 对抗审核核心**：3 列迁移中的 `adversarial_score` + `source_question_category` / 新表 `quality_reference`（三份快照 + content_hash + ngram）+ CRUD（adopt/import/list/patch）+ 查重（exact 幂等 + ngram 软提示）+ `pick_quality_references` / `record_adversarial_score` 两 MCP 工具 + verifier skill 后置对抗判分（短路 + N 次平均）+ 前端高质量库页（含只读 Tiptap）+ 文章列表加对抗分字段。
  - **W2 · 问题溯源（只 /goal）**：`source_question_texts` JSON 列 + `save_article_from_mcp` 加可选 `question_item_ids` 快照 category + texts + writer skill 传列表。
- **Phase-2（本稿不做，仅标注）**：判图（`pick_quality_references` 返 `content_json`/图 URL）、外部 URL 抓正文 / 批量导入、问题溯源全铺（scheme + pipeline 路径）、对抗判分历史表 / 通过率看板、成对 ELO。

---

## 十一、测试

- 迁移：3 列建成且 nullable、`review_status` CHECK **未变**；`quality_reference` 建成 + `article_id` UNIQUE / `origin` enum / FK SET NULL / ngram FULLTEXT 生效（`@pytest.mark.mysql`）。
- CRUD：`adopt`（仅 approved、快照三份、category 从 `source_question_category`、`origin='own'`）/ `import`（三份转换、`origin='external'`、category 可空）/ 下架 / 改类。
- 查重：`content_hash` 撞车走幂等复活（含已下架重启用）；`article_id` 重复采纳被 UNIQUE 挡；ngram 疑似重复返回 top-K 不硬挡。
- `pick_quality_references`：精确类目命中 / 回落 `category IS NULL` 通用池 / 随机取 ≤k / 每篇 `plain_text` 服务端截断到配置长度。
- `record_adversarial_score`：只写 `articles.adversarial_score`，不动其它。
- save 路径：`save_article_from_mcp` 传 `question_item_ids` → 快照 `source_question_category` + `source_question_texts`（JSON 列表）；不传时回落单个。
- 前端序列化：`ArticleListRead` 带出 `adversarial_score`；只读 Tiptap 加载 `content_json` 不重复注册扩展。
- 不回归：`review_status` 语义 / feed 两 tab / `_validate_articles_approved` 全不变（本设计不碰）。

---

## 十二、与初稿（异步方案）的差异

| 维度 | 初稿（异步） | 本稿（同步，已确认） |
|---|---|---|
| 判别时机 | 入库后服务端异步 runner + scheduler | **同步 in-loop**，verifier 子代理里跑（MCP + skill） |
| 状态机 | `review_status` 加 `adversarial_pending` + promote 到 pending | **不动 review_status**，文章照进未审核库 |
| 定位 | 进人审队列的**自动闸** | **纯 advisory 参考值**，不做闸 |
| 竞品参考 | 入 articles + `is_reference` / `review_status='reference'` 过滤 | **只入 `quality_reference`（三份快照）**，不进 articles → 无分发 footgun |
| 高质量库 | 纯 FK 会员表 | **自包含快照表**（三份正文，含 content_json 判图） |
| 判分执行 | 服务端 LiteLLM | **host 子代理判分**（零配置），规则在 skill |
| 判分结果 | 单独 `adversarial_review_results` 表（realness/policy/gap/ref_ids…） | **只一个 `articles.adversarial_score`**（N 次求平均），无明细无历史表 |
| verifier | 删除 | **保留 4 维（前置）**，后置加对抗判分 |
| loop 停止 | 改数生成量 | **仍挂 4 维**（不变） |
| 触发 | scheduler 默认开 + per-save | **无服务端触发**，skill 顺序跑 |
| 提示词管理 | 服务端 prompt_templates scope + jinja2 tab | **写在 skill markdown**（不建 scope / tab） |
| 问题溯源 | — | 加 `source_question_category` + `source_question_texts`，**仅 /goal 路径** |

---

## 十三、Codex 二轮审核后的决策与修正（2026-07-14）

两轮对抗审核后，以下为最终定案。前文与本节冲突时**以本节为准**。

### 13.1 用户拍板的 3 个设计决策

- **库治理：维持全员可写（接受风险）。** `import`/`adopt`/`patch` 任一登录用户可写，不设 admin/curator 门、不加 `is_trusted` 字段。
- **问题溯源：本期只存单题。** `source_question_texts` 存 `[单条 question_text]`（列仍为 JSON 数组，留 phase-2 多题）；`save_article` **不加** `question_item_ids` 参数、**不改 orchestrator**。
- **对抗分时效：不加时效字段。** 只 `articles.adversarial_score` 一列，不加 `scored_version`/`scored_at`；文章编辑后分不失效。

### 13.2 明确接受的风险（知情选择，非疏漏）

- **命门未完全关闭**：`external` 不是质量证明（任何登录用户可粘 AI 稿标成 external），`pick` 在 external 不足时会补 `own` → 语料退化时可能变「AI 判 AI」。**缓解**（必须做）：`pick` 每池**优先 external**、返回项带 `origin`；前端展示 external/own 配比 + 覆盖告警，让人看得见退化。**残留风险由用户接受。**
- **分可能陈旧**：文章编辑后 `adversarial_score` 不失效，人审可能看到对不上正文的旧分。纯 advisory、人审兜底，用户接受。
- **N 次平均是同模型同上下文连判**，非独立评委，属方差平滑而非校准；分是**粗信号**、非精确 0-100。文档层承认，不追求精确度。

### 13.3 集成契约修正（二轮 Codex 核实，plan v2 必须实现）

- **`get_article` 要暴露溯源字段**：`ArticleRead` + `to_article_read` 加 `source_question_category` + `source_question_texts`，否则 verifier 读不到分类去 `pick`（`ArticleRead` 现无此字段，schemas.py:121）。
- **参考详情端点**：新增 `GET /api/quality-reference/{id}` 返回三份正文，供只读 Tiptap 渲染（列表 schema 保持轻量、不含正文）。
- **`origin` 用 ENUM/CHECK**（非裸 `String`）；**`content_hash` 加 UNIQUE**（否则并发 check-then-insert 照插重复、且无冲突可 catch）。
- **外部 `content_html` 过 `nh3.clean`**（`markdown_to_html` 保留 raw HTML → 存储型 XSS；文章路径本就走 nh3，schemas.py:59）。
- **near-dup 真接线**：`import` 先查 `find_similar`、响应显式返回 `similar`；FTS 故障记日志（不静默吞成空）。
- **`pick` 端点做成 GET**，MCP 工具复用 catalog 的 `_aget`（catalog.py 无 `_apost`）。
- **软删语义**：文章软删（`is_deleted=True`）不触发 FK `SET NULL`，`article_id` 仍指向软删文章；前端来源链接按 `article.is_deleted` 显示「原文已删」，别把 `SET NULL` 描述成现有删除行为。
- **skill 发布走 DB**：改 verifier/writer 模板后，必须幂等建新 `SkillVersion` 并设 `goal.current_version_id`（seed 脚本见 `goal` 存在即 skip、本机覆盖只算冒烟；"未 bump CI 红"已过时）。
- **测试真跑迁移**：`build_test_app` 用 `create_all`（新模型要进 `utils.py` 的 `_model_modules`、qref 的 ngram 要补进 reset），**迁移链/FK/ENUM/FULLTEXT 只能靠真 `alembic upgrade head` 验**（`test_fts_and_migrations.py` 那条），别用 create_all 假绿。
- **前端是 fetch 不是 axios**：用 `api<T>(path, RequestInit)`（core.ts:23）；页面走 `routes.tsx` lazy route + `types.ts` NavKey/navItems + `App.tsx` 导航映射（**无 visitedTabs**）。
- **`MCP_TOOLS_COUNT` 27→29**：断言在 `test_mcp_status_count.py` + `test_mcp_tools_registration.py`（非 catalog）；`CLAUDE.md` 的 26/27 也同步 29。
