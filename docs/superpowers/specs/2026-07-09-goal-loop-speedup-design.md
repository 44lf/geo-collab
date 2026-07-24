# /goal Loop 生文提速设计（配图延后 + 去重复查询）

> 日期：2026-07-09 · 涉及文件：`server/app/modules/loop_skills/templates/skills/{geo-goal-orchestrator,geo-article-writer,geo-article-verifier}/SKILL.md`

## 背景与问题

`/goal` 的生文速度慢，用户体感是"整体都慢，分不清具体卡在哪一步"。逐字读完三份 skill 的完整 pseudocode 后，定位到两处结构性浪费（而不是最近新加的 `report_event` 埋点——那些是轻量 MCP 调用，量级上不太可能是主因）：

1. **配图在验证之前做**：writer 每次改写/重写都会调 `ai_illustrate_article(web_fallback=True)`，而联网兜底单次可能耗时 100~300 秒（历史实测数据，见 `project_mcp_illustrate_concurrency_limit.md`）。但只有**最终定稿**那一版的图有意义——评审不看图（4 个评分维度都是纯文本：factuality/readability/style/policy_safety），被打回重写的稿子的图完全是浪费。锁定问题词（`q_exact`）时最多重写 2 次（`REWRITE_CAP=2`），也就是说一题最坏情况下配图 3 次，只有 1 次有用。
2. **writer / verifier 各自重新查 `list_question_items` + `list_prompt_templates`**：orchestrator 在主循环开头（Required Checklist 第 3 步）已经查过一次候选问题池 + 模板列表，装在内存里的 `candidates` / `templates` 就是 subagent 需要的那一条数据的来源。但现在的 prompt 只传 `qid` / `tpl_id`（数字 id），迫使每个 fresh-context subagent 自己重新拉一遍**整个池子 / 整个模板列表**去匹配这一条——多两次 MCP 往返，还多拉了一堆用不上的数据进 subagent 的 context。

用户明确排除了并发方案（严格顺序执行、进度必须可逐条追踪），所以本设计**不改变现有的单问题顺序处理模型**，只优化每一轮内部的调用链；也明确要求**保留现有 `report_event` 埋点**，本设计只搬运埋点的调用位置，不删减埋点本身。

## 决策：方向 A（抠浪费 + 去重复查询，流程结构不变）

评估过的另一个方向（B：把 writer 和 verifier 合并成一个 subagent，进一步省一次 spawn 开销）被搁置——代价是牺牲"评分员独立于写作者"这条设计初衷（写作者某种程度上会自评），且重写循环要塞进同一个 spawn 里使 prompt 复杂化。先落地 A，如果验证后仍觉得不够快，再考虑 B。

## 详细设计

### ① 配图延后到"确定要留下这份稿子"之后

**Writer 不再调用 `ai_illustrate_article`**。Checklist 里"判断正文结构、决定要不要传 `game_positions`"这段判断逻辑完全保留，只是判断完之后不再自己下手配图，而是把判断结果（`main_category_id` + `game_positions`）带在返回的 JSON 里交回 orchestrator：

```
旧: {"article_id": 824, "title": "...", "illustration_warnings": []}
新: {"article_id": 824, "title": "...", "main_category_id": 12, "game_positions": [{"game": "原神"}, ...] | null}
```

**Orchestrator 直接调用 `ai_illustrate_article`**（不再 spawn 一个 subagent 去做——传参已是现成数据，不需要"写作判断力"，是纯机械调用）。调用时机由评审结果决定：

```pseudo
will_rewrite_same_question = q_exact and decision != "approved" and r < rewrite_budget
should_illustrate_now = (decision == "approved") or (not will_rewrite_same_question)
```

- **过审** → 配，和现在一样
- **没过审但这题已经放弃**（没锁定问题本来就要换题，或重写预算已耗尽）→ **依然配图**，因为这稿子会留在待审库里等人工复审，保持现在"人工看到的稿子都有图"的体验
- **没过审 + 问题词被锁定 + 马上要在同一问题上重写** → **跳过配图**，这份稿子注定被下一稿取代

插入位置：主循环里 `report_event(verify_passed/rejected)` 打完之后、`if decision == "approved"` 判断之前。

```pseudo
if should_illustrate_now:
    illu = ai_illustrate_article(
        article_id=article_id, main_category_id=parsed["main_category_id"],
        web_fallback=True, game_positions=parsed.get("game_positions"),
    )
    illustration_warnings = collect_illustration_warnings(illu)  # 同旧 5 信号规则（见下）
    report_event(source_module="goal_orchestrator", event_type="illustration_result", level="info",
                 message=f"问题 #{qid} 文章 #{article_id} 配图完成",
                 payload={"qid": qid, "article_id": article_id, "illustration_warnings": illustration_warnings})
else:
    report_event(source_module="goal_orchestrator", event_type="illustration_skipped", level="info",
                 message=f"问题 #{qid} 稿件即将重写，跳过本稿配图",
                 payload={"qid": qid, "article_id": article_id})
```

**`collect_illustration_warnings` helper**（原样从 writer 搬到 orchestrator 的 Helper 定义表，规则不变）：

- `format_error` 非空 → `"format_error: <值>"`
- `cover_error` 非空 → `"cover_error: <值>"`
- `warning` 非空 → `"warning: <值>"`（如 `ai_returned_no_positions` / `no_match_in_categories` / `no_valid_categories` / `already_has_images` / `partial_images: ...`）
- `images_inserted == 0` → 额外加 `"images_inserted=0"`
- `missed > 0` → 加 `"partial: 应配 {requested} 张、实配 {images_inserted} 张，缺 {missed} 张（{missed_games}）"`

**容错原则**（呼应原 writer「失败处理」里"illustrate 失败内吞不上抛"的精神，明确写进 orchestrator）：`ai_illustrate_article` 调用本身失败（超时/网络错误）→ 只记 `illustration_warnings=["call_failed: <msg>"]`，**不计入 `consecutive_mcp_fail`、不计入 `failed_rounds`**——配图是 best-effort，文章已写完过审（或已定稿待人工复审），不因配图失败重跑或中止整个 loop。

对应 writer SKILL 的改动：
- 「矩阵特例」段落里 `main_category_id` 的**取值定义**保留在 writer（这是"装新矩阵改这一节"机制的基础），但"调用约定"那段（具体怎么调 `ai_illustrate_article`）整段移到 orchestrator；writer 这边改成"把这个值和判断出的 `game_positions` 原样放进返回 JSON"
- 「失败处理」里删掉"illustrate_article 失败 → 内吞不上抛"这条（不再是 writer 的职责）
- 返回格式示例（成功/成功但配图缺失/失败三种）里的 `illustration_warnings` 字段整体去掉，换成 `main_category_id` + `game_positions`

### ② 去重复查询：orchestrator 直接把命中数据喂给 subagent

Orchestrator 在循环开头已查过整个候选池（`candidates`）和模板列表（`templates`），`worklist[wl_idx]`（即 `item`）本身就是从 `candidates` 里取出来的那一条，当前轮用的 `tpl`（`templates[...]` 或按 `target.tpl_id` 精确定位）也是现成对象。不用再让 subagent 拿着 id 去反查一遍整个池子 / 整个列表。

```pseudo
# 新：orchestrator 传给 writer 的 prompt
Input:
  qid={qid}
  question_text={item.question_text}
  tpl_id={tpl_id}
  template_name={tpl.name}
  template_content="""{tpl.content}"""
  model_label={target.model_label}
  rewrite_feedback={prior_feedback or "none"}
```

```pseudo
# 新：orchestrator 传给 verifier 的 prompt
Input:
  article_id={article_id}
  qid={qid}
  question_text={item.question_text}
  tpl_id={tpl_id}
  template_name={tpl.name}
  template_content="""{tpl.content}"""
```

对应改动：
- **writer** Checklist 第 1、2 步（`list_question_items` / `list_prompt_templates`）改为"直接用 input 给的 `question_text` / `template_content`"；仅当这两个字段**确实缺失**时才退回去查一次作为兜底（安全网，非默认路径）
- **verifier** Checklist 第 2、3 步直接删除查询动作，只保留第 1 步 `get_article(article_id)`——这一步不能省，拿的是**落库后的真实内容**（标题、正文、Tiptap 转换结果），不是写作时的草稿意图
- 顺带清理：writer 原 Checklist 第 1 步写的 `list_question_items(pool_id=<from input>)` 依赖一个 orchestrator 实际从未显式传过的 `pool_id`（现有 prompt 模板里没有这个字段）——这是现状里一个潜在小 gap，本次因为整条查询路径被去掉而顺带消失，不需要单独修

### ③ Orchestrator 角色定义 / 工具清单 / Helper 表同步更新

- **Role 段落**加一句职责：原"...决定继续/退出 → 飞书播报"改为"...决定继续/退出 → **对留下的稿子调配图工具** → 飞书播报"
- **可用工具清单**新增：`ai_illustrate_article(article_id, main_category_id, web_fallback, game_positions)`——仅在 `should_illustrate_now` 为真时调用；顺带补上现有工具清单里漏列的 `report_event`（文档补漏，不影响行为，全篇一直在用只是没写进清单）
- **Helper 定义表**新增 `collect_illustration_warnings(illu)`（见①）
- **主循环 pseudocode** 按①②描述的位置插入新逻辑；「四个不变式」不新增条目，但在"落库失败 ≠ 验证失败"这条附近的叙述里补一句"配图失败 ≠ 验证失败"，说明三者是并列的独立失败域

## 行为变化 / 风险

| 变化点 | 影响 | 风险 |
|---|---|---|
| 被"确定要立刻重写替换"的稿子不再配图 | 减少浪费的配图调用（预计是主要提速来源） | 这些稿子本来就不会被人看到（马上被下一稿取代），无用户可见影响 |
| 过审 / 终态放弃的稿子配图时机不变 | 无 | 无——和现状行为一致 |
| writer/verifier 不再各自查询池子/模板列表 | 少 4 次 MCP 往返 + 少拉无关数据进 subagent context | 数据源和现状完全一致（同一次 orchestrator 查询结果），无新鲜度风险（/goal 单次运行在分钟级，池子/模板中途变更概率可忽略） |
| 配图工具调用位置从 subagent 转到 orchestrator 主对话 | 主对话里能直接看到配图工具调用与返回，观察性变好 | 无 |
| `ai_illustrate_article` 失败不计入 `consecutive_mcp_fail` | 配图故障不会误杀整个 loop | 需要确认这条容错原则在实现时被准确遵守，否则会退化成"配图慢查询失败也一样拖累退出闸门" |

## 不做（YAGNI 边界）

- 不引入并发调度（用户已明确排除，见 brainstorming 记录）
- 不合并 writer / verifier 为单一 subagent（方向 B，暂不采纳）
- 不削减现有 `report_event` 埋点粒度或数量（用户要求保留）
- 不改变 `REWRITE_CAP`、`pass_line`、退出闸门（netto/attempt_cap/token 预算/MCP 连续失败）等既有阈值和判定逻辑
- 不改动 verifier 的评分维度或门槛
- 不涉及后端代码 / MCP 工具签名改动——`ai_illustrate_article` 工具本身不变，只是调用方从 writer subagent 换成 orchestrator 主对话

## 影响面清单

- `server/app/modules/loop_skills/templates/skills/geo-goal-orchestrator/SKILL.md`：Role 段落、可用工具清单、Helper 定义表、主循环 pseudocode（插入配图决策块）
- `server/app/modules/loop_skills/templates/skills/geo-article-writer/SKILL.md`：Checklist（去掉查询步骤、去掉配图调用、改返回字段）、矩阵特例段（拆分"取值定义"留下 / "调用约定"移出）、失败处理、返回格式示例
- `server/app/modules/loop_skills/templates/skills/geo-article-verifier/SKILL.md`：Checklist（去掉查询步骤）——本文件涉及改动最小
- 无 DB 迁移、无后端路由改动、无 MCP 工具签名改动
- 官方 Skill 库里已发布的 goal 包（Skill 库「上传新版本」机制）之后需要重新上传这三份文件才能让线上 `/goal` 用户吃到新版本——这是运维步骤，不在本次改动范围内自动发生

## 验证方式

三份文件都是 Markdown 提示词，没有单元测试框架覆盖。验证靠：

1. 本机跑一次 `/goal`（锁定一个问题词，故意用容易被打回的提示词组合触发至少一次重写），观察：
   - 重写轮次里没有 `ai_illustrate_article` 工具调用出现在 orchestrator 主对话里
   - 最终过审那一轮有且仅有一次配图调用
   - `report_event` 埋点（`illustration_result` / `illustration_skipped`）按预期出现
2. 跑一次不会触发重写的正常流程，确认过审文章依然正常配图、`illustration_warnings` 收集规则和现状一致
3. 对比同样目标（如"5 篇国风"）优化前后的总耗时，确认有可观测的提速
