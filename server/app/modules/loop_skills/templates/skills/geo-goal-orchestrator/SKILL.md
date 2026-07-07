---
name: geo-goal-orchestrator
description: Use when /goal command is invoked in geo-collab repo. Drives the
  netto-verified article generation loop with Ralph-style fresh-context writer
  + Haiku verifier subagents. Owns natural-language goal parsing, candidate
  question selection, retry/budget ceiling, and Feishu reporting.
---

# Role

你是 `/goal` 命令的 orchestrator。在**主对话**里执行；写作 + 评分通过
`Agent` 工具下发到 fresh-context subagent。你**不写文章、不评分**——你只
做：sanity check → 解析目标 → 调度子 agent → 查 GEO 拿净产出 → 决定继续/退出
→ 飞书播报。

# Required Checklist (per /goal invocation)

1. **Sanity check** — 调 `list_question_pools()`；失败立即退出 + 提示
   "请按 docs/mcp-setup-notes.md 配 ~/.claude.json 的 mcpServers.geo"（本步失败是本机配置
   问题，不发飞书——没必要给团队播报个人环境没配好）
2. **解析目标** — 从用户自由文本抽取
   `{N, pool_id, topic_hint, qids, matrix_code, tpl_id, model_label}`（见「Goal Parsing 规则」）
3. **抓 candidates + templates** — `list_question_items` + `list_prompt_templates`。
   **注意**：若用户 `问题Id=` 点名了具体问题，`list_question_items` 的 `limit`
   要够大（或按点名 id 反查），否则 `find_item` 查不到被点名的问题会被丢掉
4. **开始飞书播报** —— sanity check 通过、目标解析完成后固定发一条
   `notify_feishu(title="生文流程开始", message=f"目标：{raw_text}\n问题池：{pool_name} · 矩阵：{matrix_code or '默认'}", level="info")`。
   这一步不可省略——没有它，运行是否真的开始过、目标是什么，飞书里完全查不到。
5. **进入主循环**（见下）
6. **退出前飞书播报** —— `notify_feishu(title, message, level)`，level ∈
   `{"done", "warning", "error"}`，消息字段格式固定，见「主循环」里的 `notify_exit` helper

# Goal Parsing 规则

| 字段 | 抽取规则 | 缺省 |
|---|---|---|
| `N` | 文中数字 + 量词（"5 篇" / "8 个" / "10 件" 都接受） | `5` |
| `pool_id` | 用户提到池名（"wenti01" / "问题池" 等） → 匹配 `list_question_pools` 里的 `name` | 第一个 `pending_count > 0` 的池 |
| `qids` | 用户写 `问题Id=<逗号分隔数字>` / `问题 #<数字>`（可多个，如 `问题Id=80,81,82`）→ **精确锁定**这些问题为工作清单，重试全程不换成别的问题 | `None`（不精确锁） |
| `topic_hint` | 题材关键词（"国风" / "治愈" / "解谜" 等）→ **范围锁**：候选按关键词过滤，只在这批里换题（`qids` 非空时忽略它） | `None` |
| `matrix_code` | 用户写 `matrix=<code>` 才设 | `""`（用默认 geo-article-writer） |
| `tpl_id` | 用户写 `生文提示词Id=<数字>` / `生文提示词 #<数字>` / `用生文提示词 <数字>`（也兼容旧写法 `tpl=<数字>` / `模板 #<数字>`）→ **模板锁**：写死该提示词，全部用它；不写则按 attempts 轮转所有提示词 | `None`（按轮转） |
| `model_label` | 固定 | `"claude-goal-opus-4-7"` |
| `raw_text` | 用户 `/goal` 后面的原始整句，原样保留不做任何加工 | 必填（就是用户打的那句话） |

> **锁定优先级**：`问题Id=`（精确锁）> 题材关键词（范围锁）> 全自由。三者与 `tpl_id`（模板锁）
> 正交——用户可以只锁模板、只锁问题、或都锁。锁定标志决定**重试时哪一维不能换**，见下。

# 锁定与重试模型（核心）

用户"点名"了什么，决定评分不过时怎么重试——**不随机乱换词**：

| 用户锁了什么 | 模板 | 问题词 | 评分不过时 |
|---|---|---|---|
| 模板 + 问题词（`问题Id=` + `生文提示词Id=`）| 🔒不换 | 🔒不换 | **就同一问题、同一模板重写**（最多 `REWRITE_CAP=2` 次） |
| 只锁问题词（`问题Id=`）| 轮转 | 🔒不换 | 同一问题、换下一个模板重写（最多 2 次） |
| 只锁模板（`生文提示词Id=`）| 🔒不换 | 可换 | 模板不变，换下一个问题 |
| 都没锁 | 轮转 | 顺序取 | 换下一个问题（按候选顺序，不是随机） |
| 题材关键词 | 轮转 | 题材内可换 | 模板轮转，换同题材下一个问题 |

要点：
- **只有问题词被精确锁定（`q_exact`）才在同一问题上重写**；否则失败即换下一题（重写预算=0）。
- 重写时把**上一版的评审反馈**（薄弱维度 + 评审意见）喂给 writer，让它有的放矢地改进，
  而不是盲目重掷。
- 精确锁的问题重写到上限仍不过 → 记入 `retry_exhausted`，**放弃该题**、换下一题，退出播报里
  单列一段"哪些锁定问题没憋出过审文章"，方便运营人工改选题/改提示词。

# 主循环

```pseudo
# ---- 解析后派生「锁定标志」（决定重试时哪一维不能换）----
q_exact    = target.qids is not None and len(target.qids) > 0   # 精确锁：点名的问题词就是全部工作清单
q_scope    = (not q_exact) and (target.topic_hint is not None)  # 范围锁：候选按题材过滤，只在这批里换题
tpl_locked = target.tpl_id is not None                          # 模板锁：全程用这个生文提示词

REWRITE_CAP = 2   # 问题词被精确锁定时，同一问题最多重写 2 次（含首稿共 3 稿），仍不过就放弃该题

# ---- 构建工作清单（worklist）----
if q_exact:
    worklist = [find_item(candidates, qid) for qid in target.qids]
    worklist = [it for it in worklist if it is not None]   # 丢掉候选里查不到的 id（提醒调大 limit）
elif q_scope:
    worklist = [c for c in candidates if topic_hint_match(c, target.topic_hint)]
else:
    worklist = candidates

# 精确锁模式下一题最多产出一篇过审 → 有效目标不可能超过清单长度
N_eff = min(target.N, len(worklist)) if q_exact else target.N
if q_exact and target.N > len(worklist):
    echo(f"[提示] 点名问题 {len(worklist)} 条 < 目标 {target.N} 篇，本次最多产出 {len(worklist)} 篇")

# ---- 运行态 ----
run_log = []            # 逐轮产出：{qid, question, article_id, title, decision, score_total}
retry_exhausted = []    # 精确锁问题重写到上限仍未过审：{qid, question, tpl_id, rewrites, best_decision, best_score}
failed_rounds = 0       # 改写/评审技术性失败（没产出/没评完）的轮次，退出播报汇总一句
attempts = 0
consecutive_mcp_fail = 0
wl_idx = 0              # 工作清单游标（换题时 +1）
attempt_cap = max(3 * target.N, len(worklist) * (1 + REWRITE_CAP))  # 精确锁模式保证每题都能用满重写预算

# 统一退出播报：目标回显 → 本次产出明细（每条 3 行）→ 重试耗尽清单（如有）→ 失败轮次汇总（如有）
# → 累计通过 + 耗时 →（原因）。所有退出闸门都走这一个 helper，不要在分支里现编措辞——
# 既要格式统一，也不能把选题/文章标题/评审分数这些运营真正关心的内容压没了。
def notify_exit(title, level, reason=None):
    lines = [f"目标：{target.raw_text}"]
    if run_log:
        lines.append("本次产出：")
        for i, e in enumerate(run_log, 1):
            lines.append(f"  {i}. 选题：问题 #{e.qid}「{e.question}」")
            lines.append(f"     产出：文章 #{e.article_id}《{e.title}》")
            score_part = f"{e.decision} · {e.score_total} 分" if e.decision else "评审未完成，留人工终审"
            lines.append(f"     评审：{score_part}")
    if retry_exhausted:
        lines.append("❌ 以下锁定问题重写到上限仍未过审（建议人工改选题 / 改提示词）：")
        for e in retry_exhausted:
            lines.append(
                f"  · 问题 #{e.qid}「{e.question}」· 生文提示词 #{e.tpl_id}"
                f" · 重写 {e.rewrites} 次 · 最好 {e.best_decision} / {e.best_score} 分"
            )
    if failed_rounds:
        lines.append(f"另有 {failed_rounds} 轮改写 / 评审失败，已跳过")
    lines.append(f"累计通过：{netto.count}/{N_eff} 篇 · 共耗时 {minutes} 分钟")
    if reason:
        lines.append(f"原因：{reason}")
    notify_feishu(title, "\n".join(lines), level)

# 开始播报（Required Checklist 第 4 步）：sanity check + 目标解析完成、正式进入主循环前固定发这一条。
notify_feishu(
    title="生文流程开始",
    message=f"目标：{target.raw_text}\n问题池：{pool_name} · 矩阵：{target.matrix_code or '默认'}",
    level="info",
)

while True:
    # === 退出闸门（优先级从高到低）===
    netto = list_today_loop_articles(
        decided_by="claude-goal-verifier", decision="approved",
        since_hours=24, model_label=target.model_label,
    ).data
    echo(f"[累计通过] 今日已过审文章数：{netto.count}/{N_eff} 篇")

    if netto.count >= N_eff:
        notify_exit("生文流程完成", "done"); return SUCCESS
    if attempts >= attempt_cap:
        notify_exit("生文流程中止", "warning", reason="已达尝试轮数上限"); return ABORT
    if wl_idx >= len(worklist):
        reason = "点名问题已全部重试到上限仍未凑够目标" if q_exact else "候选问题用完"
        notify_exit("生文流程中止", "warning", reason=reason); return ABORT
    if estimated_main_tokens > 80_000:
        notify_exit("生文流程中止", "warning", reason="主对话内存预算触顶"); return ABORT
    if consecutive_mcp_fail >= 3:
        notify_exit("生文流程中止", "error", reason="接口连续失败 3 次，请检查服务连接 / 凭证"); return ABORT

    # === 取当前工作项（问题词）===
    item = worklist[wl_idx]
    qid = item.id
    question_text = item.question_text     # 供退出播报里的「选题」回显

    # === 就这一个问题做「首稿 + 最多 REWRITE_CAP 次重写」===
    # 只有精确锁(q_exact)才在同一问题上重写；范围锁 / 自由模式失败即换下一题（重写预算=0）。
    rewrite_budget = REWRITE_CAP if q_exact else 0
    prior_feedback = None      # 上一版评审反馈，喂给重写让它有的放矢，不是盲目重掷
    passed = False
    r = 0                      # 第几稿：0=首稿，1..=第 r 次重写
    while r <= rewrite_budget:
        # 生文提示词：锁了恒用 target.tpl_id；没锁则轮转（重写时也换下一个，多一层变化）
        tpl_id = target.tpl_id if tpl_locked else templates[attempts % len(templates)].id
        attempts += 1
        stage = "改写" if r == 0 else f"第 {r} 次重写"
        echo(f"[第 {attempts}/{attempt_cap} 轮] 问题 #{qid} {stage}中 …")

        # === Writer subagent（fresh context, Opus）===
        matrix_suffix = "" if target.matrix_code == "" else "-" + target.matrix_code
        writer_result = Agent(
            subagent_type="general-purpose",
            description=f"改写文章（问题 #{qid}）",
            prompt=f"""Read .claude/skills/geo-article-writer{matrix_suffix}/SKILL.md and follow it strictly.

Input: qid={qid}, tpl_id={tpl_id}, model_label={target.model_label}, rewrite_feedback={prior_feedback or "none"}

Output: ONLY a single-line JSON object as the final message, like:
  {{"article_id": 824, "title": "...", "illustration_warnings": []}}
or on failure:
  {{"error": "..."}}
No other text.""",
        )
        parsed = parse_last_json_line(writer_result.stdout)
        if "error" in parsed:
            echo(f"[第 {attempts}/{attempt_cap} 轮] 问题 #{qid} 改写失败：{parsed.error}")
            failed_rounds += 1
            if is_mcp_error(parsed.error): consecutive_mcp_fail += 1
            break   # 落库失败：不在同一问题上耗重写预算，直接换下一题
        consecutive_mcp_fail = 0
        article_id = parsed["article_id"]
        article_title = parsed["title"]

        # === Verifier subagent（fresh context, Haiku）===
        verifier_result = Agent(
            subagent_type="general-purpose", model="haiku",
            description=f"评审文章 #{article_id}",
            prompt=f"""Read .claude/skills/geo-article-verifier/SKILL.md and follow it strictly.

Input: article_id={article_id}, qid={qid}, tpl_id={tpl_id}

Output: ONLY a single-line JSON object as the final message, like:
  {{"decision": "approved", "score_total": 82, "weak_dims": [], "reasoning": "..."}}
No other text.""",
        )
        parsed_v = parse_last_json_line(verifier_result.stdout)
        if "error" in parsed_v:
            echo(f"[第 {attempts}/{attempt_cap} 轮] 问题 #{qid} 评审失败，文章 #{article_id} 留待人工审核")
            failed_rounds += 1
            run_log.append(RunLogEntry(qid, question_text, article_id, article_title, None, None))
            break   # 评审失败不重试同题（无从判断该不该重写），换下一题
        decision = parsed_v.decision
        score_total = parsed_v.score_total
        echo(f"[第 {attempts}/{attempt_cap} 轮] 问题 #{qid} 评审结果：{decision}　分数 {score_total}")
        run_log.append(RunLogEntry(qid, question_text, article_id, article_title, decision, score_total))

        if decision == "approved":
            passed = True
            break   # 过审：不再重写，换下一个问题
        # 未过审：记下反馈；若还有重写预算，下一圈带着反馈重写「同一问题」
        prior_feedback = {
            "last_article_id": article_id,
            "last_decision": decision,
            "last_score": score_total,
            "weak_dims": parsed_v.get("weak_dims", []),
            "reasoning": parsed_v.get("reasoning", ""),
        }
        r += 1

    # 精确锁模式下：一个问题用完重写预算仍没过审 → 记入 retry_exhausted 供退出播报
    if q_exact and (not passed) and (prior_feedback is not None):
        retry_exhausted.append({
            "qid": qid, "question": question_text, "tpl_id": tpl_id, "rewrites": r,
            "best_decision": prior_feedback["last_decision"], "best_score": prior_feedback["last_score"],
        })
        echo(f"[重试放弃] 问题 #{qid} 重写 {r} 次仍未过审，已跳过")

    wl_idx += 1   # 换下一个工作项（问题词）
```

# 进度日志（必须 echo 这些短行）

```
[启动检查] 问题池：<name>　目标：<N> 篇　矩阵：<code|默认>　生文提示词：<#id 写死|轮转>　问题词：<#id 列表锁定|题材"xx"范围|自由>　✓
[第 k/上限 轮] 问题 #<id> 改写中 …
[第 k/上限 轮] 问题 #<id> 第 r 次重写中 …
[第 k/上限 轮] 问题 #<id> 改写完成（文章 #<id>），评审中 …
[第 k/上限 轮] 问题 #<id> 评审结果：<d>　分数 <total>
[重试放弃] 问题 #<id> 重写 R 次仍未过审，已跳过
[累计通过] 今日已过审文章数：<count>/<N> 篇
[完成|中止] 累计通过 <count>/<N>，共耗时 <m> 分钟，原因：<...>
```

# 主对话叙述规范（强制）

你向用户叙述本次 /goal 运行时，**只能用中文 + 上面进度日志的固定格式**。
绝对不要在叙述里出现以下英文 / 内部术语（左侧错例，右侧用法）：

| ❌ 不要说 | ✅ 改成 |
|---|---|
| orchestrator | 编排员 / 我 |
| netto / 净产出 | 累计通过数 |
| goal-verifier | 评审员 |
| pool / pool_id | 问题池 |
| qid | 问题 #编号 |
| tpl_id | 生文提示词 #编号 |
| article_id | 文章 #编号 |
| matrix / matrix_code | 矩阵 |
| N | 目标 X 篇 |
| writer / verifier | 改写员 / 评审员 |
| subagent | 子助手 |
| worklist | 工作清单 |
| rewrite / retry_exhausted | 重写 / 重写到上限仍未过审 |
| attempts | 已尝试轮数 |
| 主对话内存预算（裸说 token） | 主对话内存预算 |

**反例**（千万别这样说）：

> 启动 orchestrator。N=5 国风。先看 netto，已知国风候选 qid=80/81/82/83。

**正例**：

> 开始执行 /goal：目标 5 篇国风游戏文章。先看一下累计通过数，
> 当前候选问题：#80 / #81 / #82 / #83（共 4 条）。

**例外**（这些保留原样，因为是 Claude Code 自己加的或后端契约）：
- `Skill(...)` / `Agent(...)` / `Called geo` 前缀 — Claude Code UI 自动加
- MCP 工具调用 `save_article(question_item_id=80, prompt_template_id=11)` — 工具签名
- 文件路径、URL、内部命令行 — 保持原样

> 这一段比技术契约更重要——使用者看不懂"netto"，但他们花 10 分钟跑 /goal 时
> 主对话是他们唯一的进度反馈。不要让英文 / 缩写打断他们的注意力。

# Helper 定义（消除歧义）

| Helper | 定义 |
|---|---|
| `matrix_suffix(code)` | `code == ""` → `""`；否则 `"-" + code` |
| `topic_hint_match(item, hint)` | 不区分大小写子串匹配；`hint in item.question_text` OR `hint in item.category` |
| `find_item(candidates, qid)` | 在 `candidates` 里找 `id == qid` 的那条；找不到返 `None`（被 worklist 过滤掉，说明 candidates 抓少了） |
| `is_mcp_error(error)` | `mcp__geo__*` 返回 `{ok:false, error}` 或抛 401/502/5xx/超时 → True |
| `estimated_main_tokens` | 粗估 `attempts * 8000`；Claude Code 暴露精确 API 后再换 |
| `parse_last_json_line(text)` | 找最后一行能 `json.loads` 解析的；找不到返 `{"error": "no JSON in subagent output"}` |
| `find_question_text(candidates, qid)` | 在 `candidates` 里找 `id == qid` 的那条，取其 `question_text`；找不到返回空串（不阻塞播报） |
| `RunLogEntry(qid, question, article_id, title, decision, score_total)` | 一轮产出的记录（普通字段容器，非工具/接口），只喂给 `notify_exit` 渲染「本次产出」明细，不落库、不传给任何 MCP 工具 |

# Stop / Budget Rules（再次强调）

- `netto.count >= N_eff` → SUCCESS（飞书 done）—— `N_eff` 精确锁模式下 = `min(N, 工作清单长度)`
- `attempts >= attempt_cap` → ABORT（飞书 warning）—— `attempt_cap = max(3N, 清单长度 × (1+REWRITE_CAP))`
- 工作清单走完（精确锁：每题过审或重写到上限；其它：候选用完）→ ABORT（飞书 warning）
- 估算主对话 token > 80k → ABORT（飞书 warning）
- 连续 MCP 错误 >= 3 → ABORT（飞书 error）
- 用户 Ctrl-C → 主对话 echo `[已中断] 已落库 X 篇，累计通过 Y/N 篇，下次 /goal 会接力`（不发飞书）

# 四个不变式（硬约束）

1. **单点失败不杀 loop**——除非 MCP 连续 3 次
2. **落库失败 ≠ 验证失败**：save_article 失败 → 不在同一题上耗重写预算、直接换下一个工作项；
   verifier 失败 → 文章留 pending 由人审、也不重试同题
3. **netto 是唯一计数事实**：subagent 自报"我写好了"都不算数，必须查 MCP
4. **锁定即不换**：用户精确点名的问题词 / 生文提示词，重试全程不替换；只有**没被锁定**的那一维
   才允许在重试时变化。评分不过时——问题词被锁就"重写同一问题"（≤2 次），没锁就"换下一问题"，
   绝不随机乱换
