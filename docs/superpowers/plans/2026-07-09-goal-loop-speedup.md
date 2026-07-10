# /goal Loop 生文提速 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `/goal` 生文 loop 里"配图在验证之前做"和"writer/verifier 各自重查候选池/模板列表"这两处结构性浪费去掉，在不引入并发、不删减现有 `report_event` 埋点的前提下缩短单轮耗时。

**Architecture:** 三个 Markdown 提示词文件（`geo-article-writer` / `geo-article-verifier` / `geo-goal-orchestrator` 的 `SKILL.md`）互相协作，靠 `Agent()` 调用的 prompt 文本和约定的 JSON 返回格式传递契约。本次改动：(1) writer 不再调用 `ai_illustrate_article`，只把判断出的 `main_category_id`/`game_positions` 通过返回 JSON 交给 orchestrator；orchestrator 在拿到评审结果、判断"这份稿子是否会被立即重写取代"之后才决定要不要现在调配图工具。(2) orchestrator 把已经查到的 `question_text`/模板内容直接写进传给 writer/verifier 的 prompt，两个 subagent 不再各自反查整个候选池/模板列表。

**Tech Stack:** 纯 Markdown 提示词文件（无编译/无运行时框架），受 `server/app/modules/loop_skills/service.py:build_bundle()` 的文件清单约束（`server/tests/test_loop_skill_bundle.py` 断言这三个文件路径固定存在、sha256 随内容变化）。

## Global Constraints

- **不引入并发**：主循环保持严格一次处理一个问题（写完评完才换下一个）——这是用户在 brainstorming 阶段明确排除的方向，任何改动都不能把 worklist 处理变成批量/并行。
- **保留所有现有 `report_event` 调用**：不删减、不合并已有埋点粒度，只允许新增或搬运调用位置。
- **不改变现有阈值/判定逻辑**：`REWRITE_CAP=2`、`pass_line=70`、`policy_safety>=80`、退出闸门（netto/attempt_cap/token 预算/MCP 连续失败）、评分维度和门槛，一律不动。
- **YAML frontmatter（`name`/`description`）三个文件都不改**——只改正文。
- **不需要 bump `server/app/modules/loop_skills/version.py`**：确认过 `LOOP_SKILL_BUNDLE_VERSION` 现在只是 DB 首次 seed 用的种子标签（Task 7 起，旧的"改模板必同步 sha 白名单"纪律已退场，见 `version.py` 文件头注释），不影响本次改动。
- **官方 Skill 库包更新是运维步骤，不在本计划范围内**：改完 `templates/` 下的文件后，线上 `/goal` 用户要吃到新版本，需要有人手动在 Skill 库 UI 里给官方包点「上传新版本」——这是本计划完成之后的人工操作，不写自动化步骤。
- **对照的设计文档**：`docs/superpowers/specs/2026-07-09-goal-loop-speedup-design.md`，任何实现细节和这份文档冲突时以文档决策为准，如需偏离要在 commit message 里说明原因。

---

## Task 1: 改 `geo-article-writer/SKILL.md` —— 不再调用配图工具，只回传判断结果

**Files:**
- Modify: `server/app/modules/loop_skills/templates/skills/geo-article-writer/SKILL.md`

**Interfaces:**
- Consumes：`Agent()` 调用传入的 input 文本里新增两个字段 `question_text`（字符串）、`template_content`（字符串，模板正文，三引号包裹）——Task 3 会让 orchestrator 传这两个字段。旧的 `pool_id` 隐式依赖不再需要。
- Produces：返回 JSON 的字段从 `{"article_id": int, "title": str, "illustration_warnings": [...]}` 改为 `{"article_id": int, "title": str, "main_category_id": int, "game_positions": [...] | null}`——Task 3 的 orchestrator pseudocode 会读取 `main_category_id` / `game_positions` 这两个新字段去调用配图工具。

- [ ] **Step 1: Read 文件确认当前内容**

用 Read 工具读一遍 `server/app/modules/loop_skills/templates/skills/geo-article-writer/SKILL.md`，确认下面几处 old_string 定位准确（文件此前没有被并行改动过）。

- [ ] **Step 2: 替换 Required Checklist 第 1、2 步为「直接用 input 给的数据」**

```
old_string:
1. get question — `list_question_items(pool_id=<from input>)` 拿到 qid 对应条目；
   或直接用 input 里给的 question_text 兜底（如果 orchestrator 已经带过来）
2. get template — `list_prompt_templates(scope="generation")` 找到 tpl_id 的 content
3. **打点，动笔前，独立执行**

new_string:
1. get question / template — **直接用 input 里给的 `question_text` / `template_content`**（orchestrator
   已经查过一次候选池和模板列表，这两个字段是命中结果，不用你再查一遍）。仅当这两个字段
   **确实缺失**时才退回去查一次作为兜底：`list_question_items(pool_id=...)` 反查 `question_text` /
   `list_prompt_templates(scope="generation")` 反查 `template_content`
2. **打点，动笔前，独立执行**
```

- [ ] **Step 3: 后续步骤编号跟着往前挪一位（原 3→2 已在 Step 2 处理，原 4/5 → 3/4）**

```
old_string:
4. 写 markdown body（约束见下；重写模式下先读 `rewrite_feedback` 再动笔）。
5. `save_article(question_item_id, prompt_template_id, title, markdown_content,
   model_label, prompt_template_name=<step 2 拿到的 tpl.name>,
   question_text_preview=<step 1 拿到的 question_text 前 ~40 字>)` —

new_string:
3. 写 markdown body（约束见下；重写模式下先读 `rewrite_feedback` 再动笔）。
4. `save_article(question_item_id, prompt_template_id, title, markdown_content,
   model_label, prompt_template_name=<input 里的 template_name>,
   question_text_preview=<input 里的 question_text 前 ~40 字>)` —
```

- [ ] **Step 4: 改第 6 步——只判断 `game_positions`，去掉调用配图工具的部分**

```
old_string:
6. **配图前先判断正文结构,决定要不要传显式游戏清单 `game_positions`:**
   - 若你写的是**每款游戏各占一个 `##` 小标题**的推荐 / 盘点类文章 → **逐款收一份
     `game_positions`**:每个出现的游戏一项,`game` 用它在小标题里的规范中文名(和小标题
     保持一致;后端会自动去掉《》「」弯引号与「游戏N、」前缀再匹配),顺序按正文小标题顺序。
     例:`game_positions=[{"game": "原神"}, {"game": "明日方舟"}, {"game": "鸣潮"}]`。
     这样走**确定性落图**:每款精准配到自己的小标题下、图库没有的走联网兜底、
     `requested / missed / missed_games` 计数精确(不再有"漏点游戏"盲区)。
   - 若是**没有分款小标题的散文 / 综述**(游戏名只散在正文、无各自小标题)→ **不要传**
     `game_positions`(设 None),回退现有 AI 模型识别路径(否则游戏匹配不到小标题会落不了图)。
   确定这份清单后（或确定为 None）打一条：
  `report_event(source_module="geo_article_writer", event_type="games_selected", level="info",       message=f"文章 #{article_id} 确定配图游戏清单", source_type="article", source_id=article_id, payload={"qid": qid, "tpl_id": tpl_id, "game_positions": game_positions})`
   `game_positions` 为 `None` 时也要打（payload 里如实记 `null`），这样"这篇是散文、没传清单"
   本身也是一条可查的事实，不是沉默。
   然后调:
   `ai_illustrate_article(article_id, main_category_id=<从矩阵特例段拿>, web_fallback=True, game_positions=<上面那份;散文则 None>)` —
   AI 智能配图 + 自动封面。`web_fallback=True` 让图库里没有对应栏目的游戏也能
   联网补图（见矩阵特例段;传了 `game_positions` 时确定性路径内部已写死联网兜底,
   该参数只对回退路径生效）。**必须**收集这 5 类信号进 `illustration_warnings`
   数组（任一非空 / 命中即记录，不抛错、不阻塞返回）：
   - `format_error` 非空 → 加 `"format_error: <值>"`
   - `cover_error` 非空 → 加 `"cover_error: <值>"`
   - `warning` 非空 → 加 `"warning: <值>"`（典型值：`ai_returned_no_positions` /
     `no_match_in_categories` / `no_valid_categories` / `already_has_images` /
     `partial_images: ...`）
   - `images_inserted == 0` → 额外加 `"images_inserted=0"`（即便上面三个都为空，
     也要让 orchestrator 看到"AI 决定不插图"这一事实）
   - `missed > 0` → 加 `"partial: 应配 {requested} 张、实配 {images_inserted} 张，缺 {missed} 张（{missed_games}）"`
     （部分配图失败：图库 + 联网都没补齐。**即便 images_inserted 非 0 也要记**——
     别因为有图就当完全成功；missed_games 指出是哪几款游戏没配上）
   `ai_illustrate_article` 返回后，不管有没有 warning，都打一条：
  `report_event(source_module="geo_article_writer", event_type="illustration_result", level="info", message=f"文章 #{article_id} 配图完成：{images_inserted} 张", source_type="article", source_id=article_id, payload={"qid": qid, "images_inserted": images_inserted, "cover_status": cover_status, "illustration_warnings": illustration_warnings})`

   这样"最终用了几张图、封面是不是新设的"能在打点里查到，不用等 orchestrator 转述。
7. 返回 `{"article_id": int, "title": str, "illustration_warnings": [...]}` 作为
   **最后一条消息**，**只输出 JSON 一行**；`illustration_warnings` 字段始终存在
   （没有 warning 时为 `[]`），让 orchestrator 可以统一解析

new_string:
5. **判断正文结构,决定要不要传显式游戏清单 `game_positions`（只判断，不调用配图工具——
   配图现在由 orchestrator 在拿到评审结果之后决定要不要做）:**
   - 若你写的是**每款游戏各占一个 `##` 小标题**的推荐 / 盘点类文章 → **逐款收一份
     `game_positions`**:每个出现的游戏一项,`game` 用它在小标题里的规范中文名(和小标题
     保持一致;后端会自动去掉《》「」弯引号与「游戏N、」前缀再匹配),顺序按正文小标题顺序。
     例:`game_positions=[{"game": "原神"}, {"game": "明日方舟"}, {"game": "鸣潮"}]`。
     这样走**确定性落图**:每款精准配到自己的小标题下、图库没有的走联网兜底、
     `requested / missed / missed_games` 计数精确(不再有"漏点游戏"盲区)。
   - 若是**没有分款小标题的散文 / 综述**(游戏名只散在正文、无各自小标题)→ **不要传**
     `game_positions`(设 None),回退现有 AI 模型识别路径(否则游戏匹配不到小标题会落不了图)。
   确定这份清单后（或确定为 None）打一条：
  `report_event(source_module="geo_article_writer", event_type="games_selected", level="info",       message=f"文章 #{article_id} 确定配图游戏清单", source_type="article", source_id=article_id, payload={"qid": qid, "tpl_id": tpl_id, "game_positions": game_positions})`
   `game_positions` 为 `None` 时也要打（payload 里如实记 `null`），这样"这篇是散文、没传清单"
   本身也是一条可查的事实，不是沉默。
6. 返回 `{"article_id": int, "title": str, "main_category_id": int, "game_positions": [...] | None}` 作为
   **最后一条消息**，**只输出 JSON 一行**。`main_category_id` 取「矩阵特例」段里定义的值；
   `game_positions` 就是上一步确定的那份清单（或 `None`）。orchestrator 会在拿到评审结果之后
   自己决定要不要、什么时候调配图工具——你不再需要关心配图这件事
```

- [ ] **Step 5: 改「矩阵特例」段落——把「调用约定」那段挪成一句指向 orchestrator 的说明**

```
old_string:
> 调用约定：
> `ai_illustrate_article(article_id=<>, main_category_id=<上面那个值>, web_fallback=True, game_positions=<见 Checklist step 6：每款一标题的文章传清单、散文传 None>)`
> 其余布尔参数（include_companion / aggressive_images / set_cover）走默认即可；
> `web_fallback` 建议显式带 `True`，让图库里没有的游戏也能联网补图。
> 传了 `game_positions` 时走**确定性落图**（按游戏名匹配小标题、计数精确、不调配图模型）；
> 这是修「弱模型漏点游戏导致缺图」的主路径，每款一标题的推荐 / 盘点文优先用它。
>
> **务必**检查返回的 `format_error` / `cover_error` / `warning` / `images_inserted`
> 四个字段，按上面 step 6 规则进 `illustration_warnings`——历史 bug：silent
> zero（AI 返了 0 张图，服务端 warning=`ai_returned_no_positions`，writer 不报警
> → 文章 0 图入库无人感知）。

new_string:
> 这一段的 `main_category_id` 值和上面几条默认参数是**给 orchestrator 用的**——
> 你只需要在返回 JSON 里带上这个 `main_category_id`；实际调用 `ai_illustrate_article`
> 的时机和参数组装（`web_fallback=True`、`game_positions` 等）由 orchestrator 决定，
> 详见 `geo-goal-orchestrator/SKILL.md` 主循环里的配图决策块。
```

- [ ] **Step 6: 改「失败处理」——去掉 illustrate_article 那条**

```
old_string:
# 失败处理

- `save_article` 失败（如 415 / 标题超长 / DB 冲突）
  → 输出 `{"error": "<message>"}` 退出；orchestrator 会跳过这条 qid 不再重试
- `list_question_items` / `list_prompt_templates` 失败 → 同上
- `illustrate_article` 失败 → 内吞、不上抛；文章已落库无配图也算交付

new_string:
# 失败处理

- `save_article` 失败（如 415 / 标题超长 / DB 冲突）
  → 输出 `{"error": "<message>"}` 退出；orchestrator 会跳过这条 qid 不再重试
- `list_question_items` / `list_prompt_templates`（兜底路径，仅 input 缺字段时才会用到）失败 → 同上
```

- [ ] **Step 7: 改「返回格式（强制）」的示例**

```
old_string:
成功（无配图警告）：
```
{"article_id": 824, "title": "国风游戏 2026 推荐 10 选", "illustration_warnings": []}
```

成功但配图缺失（AI 返 0 张位置）：
```
{"article_id": 824, "title": "国风游戏 2026 推荐 10 选", "illustration_warnings": ["warning: ai_returned_no_positions", "images_inserted=0"]}
```

失败：
```
{"error": "save_article 415: unsupported markdown element"}
```

不要在 JSON 前后加任何解释 / markdown 包裹 / "我写完了" 之类的话。
orchestrator 用正则匹配最后一行 JSON 拿结果；`illustration_warnings` 字段始终存在
（无 warning 时为 `[]`），让 orchestrator 统一解析逻辑不用 `.get()` 兜空。

new_string:
成功（每款游戏各占一个小标题，传了游戏清单）：
```
{"article_id": 824, "title": "国风游戏 2026 推荐 10 选", "main_category_id": 12, "game_positions": [{"game": "原神"}, {"game": "明日方舟"}]}
```

成功（散文/综述，没有可用的游戏清单）：
```
{"article_id": 824, "title": "国风游戏 2026 生态观察", "main_category_id": 12, "game_positions": null}
```

失败：
```
{"error": "save_article 415: unsupported markdown element"}
```

不要在 JSON 前后加任何解释 / markdown 包裹 / "我写完了" 之类的话。
orchestrator 用正则匹配最后一行 JSON 拿结果；`main_category_id` / `game_positions`
字段始终存在（成功时），让 orchestrator 统一解析逻辑不用 `.get()` 兜空。
```

- [ ] **Step 8: 校验旧引用确实清干净了**

Run:
```bash
grep -n "ai_illustrate_article\|illustration_warnings" server/app/modules/loop_skills/templates/skills/geo-article-writer/SKILL.md
```
Expected: 无输出（这两个字符串在 writer 文件里应该已经完全消失）。

- [ ] **Step 9: Commit**

```bash
git add server/app/modules/loop_skills/templates/skills/geo-article-writer/SKILL.md
git commit -m "$(cat <<'EOF'
refactor(loop_skills): writer 不再自己配图，改为回传判断结果

配图（尤其 web_fallback 联网兜底）单次可能耗时 100-300s，但只有最终定稿
的稿子需要图——之前每次改写/重写都配一次纯属浪费。writer 现在只负责判断
main_category_id/game_positions 并回传，实际调用交给 orchestrator。
EOF
)"
```

---

## Task 2: 改 `geo-article-verifier/SKILL.md` —— 不再反查候选池/模板列表

**Files:**
- Modify: `server/app/modules/loop_skills/templates/skills/geo-article-verifier/SKILL.md`

**Interfaces:**
- Consumes：`Agent()` 调用传入的 input 新增 `question_text`（字符串）、`template_content`（字符串）字段——由 Task 3 的 orchestrator 提供。
- Produces：返回 JSON 格式不变（`{"decision", "score_total", "weak_dims", "reasoning"}`），本任务不改这部分。

- [ ] **Step 1: Read 文件确认当前内容**

用 Read 工具读一遍 `server/app/modules/loop_skills/templates/skills/geo-article-verifier/SKILL.md`。

- [ ] **Step 2: 替换 Required Checklist，去掉查询步骤，重新编号**

```
old_string:
# Required Checklist (per spawn)

1. `get_article(article_id)` — 拿完整内容 + qid + tpl_id（从 metrics 或 input）
2. `list_question_items(pool_id=...)` 反查 qid 对应 question_text
3. `list_prompt_templates(scope="generation")` 反查 tpl_id 对应 template
4. 按 4 维度评分（0-100，整数）
5. 计算 `score_total = round((factuality + readability + style + policy_safety) / 4)`
6. 决策（门槛见下）
7. `submit_review_decision(article_id, decision, score_total, score_breakdown,
   reasoning, decided_by="claude-goal-verifier", pass_line=<见下>)`
   - **`pass_line`（合格分数）**：当 `decision != "approved"`（即 `score_total < 70`
     或 `policy_safety < 80` 被拦下）时，**必须传 `pass_line=70`**——把「合格线 70」
     一并送给平台，平台会把分数渲染成「真实分 / 合格线」（如 `65 / 70`），运营一眼就知道
     差在哪、离达标还有多远。
   - 过审（`decision == "approved"`）时**省略 `pass_line`**（不传）：既然已达标，
     没必要再标一条合格线。
8. 返回 `{"decision": str, "score_total": int, "weak_dims": [str], "reasoning": str}`
   作为最后一条消息。其中：

new_string:
# Required Checklist (per spawn)

1. `get_article(article_id)` — 拿完整内容（落库后的真实标题 / 正文 / 配图状态）
2. **直接用 input 里给的 `question_text` / `template_content`**（orchestrator 已经查过一次候选池和
   模板列表，不用你再反查）。仅当这两个字段确实缺失时才退回去查一次作为兜底：
   `list_question_items(pool_id=...)` 反查 question_text /
   `list_prompt_templates(scope="generation")` 反查 template
3. 按 4 维度评分（0-100，整数）
4. 计算 `score_total = round((factuality + readability + style + policy_safety) / 4)`
5. 决策（门槛见下）
6. `submit_review_decision(article_id, decision, score_total, score_breakdown,
   reasoning, decided_by="claude-goal-verifier", pass_line=<见下>)`
   - **`pass_line`（合格分数）**：当 `decision != "approved"`（即 `score_total < 70`
     或 `policy_safety < 80` 被拦下）时，**必须传 `pass_line=70`**——把「合格线 70」
     一并送给平台，平台会把分数渲染成「真实分 / 合格线」（如 `65 / 70`），运营一眼就知道
     差在哪、离达标还有多远。
   - 过审（`decision == "approved"`）时**省略 `pass_line`**（不传）：既然已达标，
     没必要再标一条合格线。
7. 返回 `{"decision": str, "score_total": int, "weak_dims": [str], "reasoning": str}`
   作为最后一条消息。其中：
```

- [ ] **Step 3: 修正紧跟着的引用编号（原第 7 步 reasoning 说明段落里提到"第 7 步"要改成"第 6 步"）**

```
old_string:
   - `reasoning` = 和第 7 步写进 `submit_review_decision` 的同一句话（1-2 句）

new_string:
   - `reasoning` = 和第 6 步写进 `submit_review_decision` 的同一句话（1-2 句）
```

- [ ] **Step 4: 校验旧引用确实清干净了**

Run:
```bash
grep -n "list_question_items\|list_prompt_templates" server/app/modules/loop_skills/templates/skills/geo-article-verifier/SKILL.md
```
Expected: 只在 Step 2 兜底路径那一行出现，不再作为默认路径的第 2、3 步单独列出。

- [ ] **Step 5: Commit**

```bash
git add server/app/modules/loop_skills/templates/skills/geo-article-verifier/SKILL.md
git commit -m "$(cat <<'EOF'
refactor(loop_skills): verifier 不再反查候选池/模板列表

question_text/template_content 由 orchestrator 直接传入 prompt（orchestrator
已经查过一次），verifier 只在这两个字段缺失时才退回去查，去掉默认路径上的
两次 MCP 往返。
EOF
)"
```

---

## Task 3: 改 `geo-goal-orchestrator/SKILL.md` —— 直传数据给 subagent + 新增配图决策

**Files:**
- Modify: `server/app/modules/loop_skills/templates/skills/geo-goal-orchestrator/SKILL.md`

**Interfaces:**
- Consumes：Task 1 的 writer 新返回契约 `{"article_id", "title", "main_category_id", "game_positions"}`；Task 2 的 verifier 新输入契约（`question_text`/`template_content` 字段名）。
- Produces：无对外契约变化（`/goal` 的用户可见输出——进度 echo + 飞书播报——格式不变）。

- [ ] **Step 1: Read 文件确认当前内容**

用 Read 工具读一遍 `server/app/modules/loop_skills/templates/skills/geo-goal-orchestrator/SKILL.md`。

- [ ] **Step 2: Role 段落加一句职责**

```
old_string:
你是 `/goal` 命令的 orchestrator。在**主对话**里执行；写作 + 评分通过
`Agent` 工具下发到 fresh-context subagent。你**不写文章、不评分**——你只
做：sanity check → 解析目标 → 调度子 agent → 查 GEO 拿净产出 → 决定继续/退出
→ 飞书播报。

new_string:
你是 `/goal` 命令的 orchestrator。在**主对话**里执行；写作 + 评分通过
`Agent` 工具下发到 fresh-context subagent。你**不写文章、不评分**——你只
做：sanity check → 解析目标 → 调度子 agent → 查 GEO 拿净产出 → 决定继续/退出
→ 对留下的稿子调配图工具 → 飞书播报。
```

- [ ] **Step 3: 可用工具清单新增 `ai_illustrate_article` 和 `report_event`**

```
old_string:
- `list_question_pools()` / `list_question_items(pool_id, ...)` — 抓候选选题
- `list_prompt_templates(scope="generation")` — 抓可用生文提示词
- `list_today_loop_articles(decided_by, decision, since_hours, model_label)` — 查净产出（累计通过数），退出闸门唯一事实来源
- `notify_feishu(title, message, level)` — 开始播报 + 退出前批量汇总（见「主循环」`notify_exit`）

new_string:
- `list_question_pools()` / `list_question_items(pool_id, ...)` — 抓候选选题
- `list_prompt_templates(scope="generation")` — 抓可用生文提示词
- `list_today_loop_articles(decided_by, decision, since_hours, model_label)` — 查净产出（累计通过数），退出闸门唯一事实来源
- `ai_illustrate_article(article_id, main_category_id, web_fallback, game_positions)` — 只在「确定要留下这份稿子」时调用（见「主循环」里的 `should_illustrate_now` 判断），参数由 writer 返回的判断结果直接套用，不需要写作判断力
- `notify_feishu(title, message, level)` — 开始播报 + 退出前批量汇总（见「主循环」`notify_exit`）
- `report_event(source_module, event_type, message, level, source_type, source_id, payload)` — 贯穿全流程的打点，见「Required Checklist」和「主循环」里各处调用
```

- [ ] **Step 4: 主循环里 `tpl_id` 的计算改成同时保留完整 `tpl` 对象**

```
old_string:
        # 生文提示词：锁了恒用 target.tpl_id；没锁则轮转（重写时也换下一个，多一层变化）
        tpl_id = target.tpl_id if tpl_locked else templates[attempts % len(templates)].id
        attempts += 1

new_string:
        # 生文提示词：锁了恒用 target.tpl_id；没锁则轮转（重写时也换下一个，多一层变化）
        tpl = find_template(templates, target.tpl_id) if tpl_locked else templates[attempts % len(templates)]
        tpl_id = tpl.id
        attempts += 1
```

- [ ] **Step 5: Writer 的 `Agent()` 调用 prompt 里加 `question_text` / `template_content`，返回示例改成新契约**

```
old_string:
            prompt=f"""Read .claude/skills/geo-article-writer{matrix_suffix}/SKILL.md and follow it strictly.

Input: qid={qid}, tpl_id={tpl_id}, model_label={target.model_label}, rewrite_feedback={prior_feedback or "none"}

Output: ONLY a single-line JSON object as the final message, like:
  {{"article_id": 824, "title": "...", "illustration_warnings": []}}
or on failure:
  {{"error": "..."}}
No other text.""",

new_string:
            prompt=f"""Read .claude/skills/geo-article-writer{matrix_suffix}/SKILL.md and follow it strictly.

Input:
  qid={qid}
  question_text={item.question_text}
  tpl_id={tpl_id}
  template_name={tpl.name}
  template_content=\"\"\"{tpl.content}\"\"\"
  model_label={target.model_label}
  rewrite_feedback={prior_feedback or "none"}

Output: ONLY a single-line JSON object as the final message, like:
  {{"article_id": 824, "title": "...", "main_category_id": 12, "game_positions": [{{"game": "原神"}}] or null}}
or on failure:
  {{"error": "..."}}
No other text.""",
```

- [ ] **Step 6: 写作结果解析里提取 `main_category_id` / `game_positions`**

```
old_string:
        consecutive_mcp_fail = 0
        article_id = parsed["article_id"]
        article_title = parsed["title"]
        report_event(
            source_module="goal_orchestrator", event_type="write_succeeded", level="info",
            message=f"问题 #{qid} {stage}成功，文章 #{article_id}《{article_title}》",
            payload={
                "qid": qid, "tpl_id": tpl_id, "attempts": attempts,
                "article_id": article_id, "article_title": article_title,
            },
        )

new_string:
        consecutive_mcp_fail = 0
        article_id = parsed["article_id"]
        article_title = parsed["title"]
        main_category_id = parsed["main_category_id"]
        game_positions = parsed.get("game_positions")
        report_event(
            source_module="goal_orchestrator", event_type="write_succeeded", level="info",
            message=f"问题 #{qid} {stage}成功，文章 #{article_id}《{article_title}》",
            payload={
                "qid": qid, "tpl_id": tpl_id, "attempts": attempts,
                "article_id": article_id, "article_title": article_title,
            },
        )
```

- [ ] **Step 7: Verifier 的 `Agent()` 调用 prompt 里加 `question_text` / `template_content`**

```
old_string:
            prompt=f"""Read .claude/skills/geo-article-verifier/SKILL.md and follow it strictly.

Input: article_id={article_id}, qid={qid}, tpl_id={tpl_id}

Output: ONLY a single-line JSON object as the final message, like:
  {{"decision": "approved", "score_total": 82, "weak_dims": [], "reasoning": "..."}}
No other text.""",

new_string:
            prompt=f"""Read .claude/skills/geo-article-verifier/SKILL.md and follow it strictly.

Input:
  article_id={article_id}
  qid={qid}
  question_text={item.question_text}
  tpl_id={tpl_id}
  template_name={tpl.name}
  template_content=\"\"\"{tpl.content}\"\"\"

Output: ONLY a single-line JSON object as the final message, like:
  {{"decision": "approved", "score_total": 82, "weak_dims": [], "reasoning": "..."}}
No other text.""",
```

- [ ] **Step 8: 评审技术性失败分支也要配图（这份稿子同样会留给人工复审，不能因为分支不同就漏配）**

> 自查发现：评审员本身解析失败（`parsed_v` 里有 `"error"`，不是评出了低分，而是压根没评出结果）这条分支会在
> `decision = parsed_v.decision` 之前就 `break` 掉，绕开 Step 9 要插入的配图决策块——这会导致这类留给人工
> 复审的文章配不到图，和"已经放弃的稿子照常配图"的原则矛盾。这一步把配图调用也接到这条分支里。

```
old_string:
            report_event(
                source_module="goal_orchestrator", event_type="verify_failed", level="warning",
                message=f"问题 #{qid} 文章 #{article_id} 评审失败：{parsed_v.error}",
                payload={
                    "qid": qid, "tpl_id": tpl_id, "attempts": attempts,
                    "article_id": article_id, "error": parsed_v.error,
                },
            )
            break   # 评审失败不重试同题（无从判断该不该重写），换下一题

new_string:
            report_event(
                source_module="goal_orchestrator", event_type="verify_failed", level="warning",
                message=f"问题 #{qid} 文章 #{article_id} 评审失败：{parsed_v.error}",
                payload={
                    "qid": qid, "tpl_id": tpl_id, "attempts": attempts,
                    "article_id": article_id, "error": parsed_v.error,
                },
            )
            # 评审没评出结果，这份稿子和"决策已知但放弃重写"一样是终态（留人工复审），照常配图
            illu = ai_illustrate_article(
                article_id=article_id, main_category_id=main_category_id,
                web_fallback=True, game_positions=game_positions,
            )
            illustration_warnings = collect_illustration_warnings(illu)
            report_event(
                source_module="goal_orchestrator", event_type="illustration_result", level="info",
                message=f"问题 #{qid} 文章 #{article_id} 配图完成",
                payload={
                    "qid": qid, "article_id": article_id,
                    "illustration_warnings": illustration_warnings,
                },
            )
            break   # 评审失败不重试同题（无从判断该不该重写），换下一题
```

- [ ] **Step 9: 在评审结果之后插入配图决策块（核心改动，覆盖正常评出 decision 的路径）**

```
old_string:
        report_event(
            source_module="goal_orchestrator",
            event_type="verify_passed" if decision == "approved" else "verify_rejected",
            level="info" if decision == "approved" else "warning",
            message=f"问题 #{qid} 文章 #{article_id} 评审：{decision} · {score_total} 分",
            payload={
                "qid": qid, "tpl_id": tpl_id, "attempts": attempts, "article_id": article_id,
                "decision": decision, "score_total": score_total,
                "weak_dims": parsed_v.get("weak_dims", []),
            },
        )

        if decision == "approved":
            passed = True
            break   # 过审：不再重写，换下一个问题

new_string:
        report_event(
            source_module="goal_orchestrator",
            event_type="verify_passed" if decision == "approved" else "verify_rejected",
            level="info" if decision == "approved" else "warning",
            message=f"问题 #{qid} 文章 #{article_id} 评审：{decision} · {score_total} 分",
            payload={
                "qid": qid, "tpl_id": tpl_id, "attempts": attempts, "article_id": article_id,
                "decision": decision, "score_total": score_total,
                "weak_dims": parsed_v.get("weak_dims", []),
            },
        )

        # === 配图决策：只在「确定要留下这份稿子」时才配图 ===
        # 锁定问题词时最多重写 REWRITE_CAP 次；未过审且这不是最后一次机会 → 马上会被下一稿取代，
        # 配图纯属浪费（评审只看文本，不看图）。其余情况（过审 / 已经放弃这题）都照常配图。
        will_rewrite_same_question = q_exact and decision != "approved" and r < rewrite_budget
        should_illustrate_now = (decision == "approved") or (not will_rewrite_same_question)
        if should_illustrate_now:
            illu = ai_illustrate_article(
                article_id=article_id, main_category_id=main_category_id,
                web_fallback=True, game_positions=game_positions,
            )
            illustration_warnings = collect_illustration_warnings(illu)
            report_event(
                source_module="goal_orchestrator", event_type="illustration_result", level="info",
                message=f"问题 #{qid} 文章 #{article_id} 配图完成",
                payload={
                    "qid": qid, "article_id": article_id,
                    "illustration_warnings": illustration_warnings,
                },
            )
        else:
            report_event(
                source_module="goal_orchestrator", event_type="illustration_skipped", level="info",
                message=f"问题 #{qid} 稿件即将重写，跳过本稿配图",
                payload={"qid": qid, "article_id": article_id},
            )

        if decision == "approved":
            passed = True
            break   # 过审：不再重写，换下一个问题
```

- [ ] **Step 10: Helper 定义表加 `find_template` 和 `collect_illustration_warnings`**

```
old_string:
| `find_item(candidates, qid)` | 在 `candidates` 里找 `id == qid` 的那条；找不到返 `None`（被 worklist 过滤掉，说明 candidates 抓少了） |
| `is_mcp_error(error)` | `mcp__geo__*` 返回 `{ok:false, error}` 或抛 401/502/5xx/超时 → True |

new_string:
| `find_item(candidates, qid)` | 在 `candidates` 里找 `id == qid` 的那条；找不到返 `None`（被 worklist 过滤掉，说明 candidates 抓少了） |
| `find_template(templates, tpl_id)` | 在 `templates` 里找 `id == tpl_id` 的那条；找不到返 `None`（说明 `tpl_locked` 时用户传的模板 id 不在候选列表里，属输入错误） |
| `collect_illustration_warnings(illu)` | 若 `illu` 是调用失败（异常/超时）→ 返回 `["call_failed: <message>"]`，**不触发** `consecutive_mcp_fail` / `failed_rounds`（配图 best-effort）；否则按 5 类信号收集：`format_error` 非空→`"format_error: <值>"`；`cover_error` 非空→`"cover_error: <值>"`；`warning` 非空→`"warning: <值>"`；`images_inserted == 0`→额外加 `"images_inserted=0"`；`missed > 0`→加 `"partial: 应配 {requested} 张、实配 {images_inserted} 张，缺 {missed} 张（{missed_games}）"` |
| `is_mcp_error(error)` | `mcp__geo__*` 返回 `{ok:false, error}` 或抛 401/502/5xx/超时 → True |
```

- [ ] **Step 11: 「四个不变式」第 2 条加上配图失败域**

```
old_string:
2. **落库失败 ≠ 验证失败**：save_article 失败 → 不在同一题上耗重写预算、直接换下一个工作项；
   verifier 失败 → 文章留 pending 由人审、也不重试同题

new_string:
2. **落库失败 ≠ 验证失败 ≠ 配图失败**：save_article 失败 → 不在同一题上耗重写预算、直接换下一个工作项；
   verifier 失败 → 文章留 pending 由人审、也不重试同题；`ai_illustrate_article` 失败（best-effort）→
   只记 `illustration_warnings`，不计入 `consecutive_mcp_fail` / `failed_rounds`，不影响文章已经写完过审这个事实
```

- [ ] **Step 12: 校验改动完整性**

Run:
```bash
grep -n "find_template\|collect_illustration_warnings\|should_illustrate_now\|will_rewrite_same_question" server/app/modules/loop_skills/templates/skills/geo-goal-orchestrator/SKILL.md
```
Expected: 每个符号至少出现 2 次（定义一次 + 使用至少一次）。

```bash
grep -n "question_text=\|template_content=" server/app/modules/loop_skills/templates/skills/geo-goal-orchestrator/SKILL.md
```
Expected: 各出现 2 次（writer 一次、verifier 一次）。

- [ ] **Step 13: Commit**

```bash
git add server/app/modules/loop_skills/templates/skills/geo-goal-orchestrator/SKILL.md
git commit -m "$(cat <<'EOF'
feat(loop_skills): orchestrator 直传数据给 subagent + 新增配图决策

writer/verifier 不再各自反查候选池/模板列表，orchestrator 把已查到的
question_text/模板内容直接塞进 prompt。新增配图决策块：只在稿子确定要
留下（过审，或已放弃不再重写）时才调 ai_illustrate_article，即将被同题
重写取代的稿子跳过配图。
EOF
)"
```

---

## Task 4: 跨文件一致性核对 + 手动冒烟验证

**Files:**
- Read-only 核对（不产生新的代码改动，除非发现不一致需要回头修）：全部三个 `SKILL.md`

**Interfaces:** 无新增；本任务验证 Task 1/2/3 之间的契约确实对得上。

- [ ] **Step 1: 三份文件里的字段名做一次交叉 grep 核对**

Run:
```bash
grep -rn "main_category_id\|game_positions" server/app/modules/loop_skills/templates/skills/
```
预期能看到：geo-article-writer 里出现在「返回格式」和「矩阵特例」段；geo-goal-orchestrator 里出现在 Agent prompt 模板、`parsed["main_category_id"]` 提取处、配图决策块。字段拼写全部一致（没有 `main_category`/`gamePositions` 之类的手误）。

- [ ] **Step 2: 确认 writer 返回契约里不再残留 illustration_warnings**

Run:
```bash
grep -rn "illustration_warnings" server/app/modules/loop_skills/templates/skills/geo-article-writer/SKILL.md
```
Expected: 无输出。

Run:
```bash
grep -rn "illustration_warnings" server/app/modules/loop_skills/templates/skills/geo-goal-orchestrator/SKILL.md
```
Expected: 只在新增的配图决策块 / Helper 定义表里出现（作为 orchestrator 侧的本地变量名，不是 writer 返回字段）。

- [ ] **Step 3: 跑现有 loop_skill bundle 测试确认文件结构没被破坏**

这个测试不需要 MySQL（不依赖 `build_test_app`），可以裸跑：

```bash
cd e:/geo && python -m pytest server/tests/test_loop_skill_bundle.py -q -k "not (mysql)" 2>&1 | tail -30
```

Expected: `test_bundle_lists_expected_files` / `test_bundle_sha256_is_stable` / `test_bundle_sha256_changes_when_template_changes` 等无 DB 依赖的用例通过（PASS），确认三个文件路径没变、内容改动确实让 `bundle_sha256` 变化（这正是本次改动应有的效果）。

- [ ] **Step 4: 手动 /goal 冒烟测试（人工执行，无法自动化）**

如果本机 `~/.claude.json` 已配好 `mcpServers.geo`（见 `docs/mcp-setup-notes.md`），跑一次锁定单个问题词、容易被打回的组合，观察：

1. 主对话里配图工具调用（`ai_illustrate_article`）**只在最终过审那一轮**（或问题被放弃那一轮）出现，重写中间轮次不出现
2. `report_event` 能看到 `illustration_skipped`（重写轮）和 `illustration_result`（终稿轮）都按预期出现
3. 最终文章确实带图，和改动前行为一致

这一步无法在当前会话里自动执行（需要真实 MCP 服务 + 完整 `/goal` 运行），记录为交给用户在部署验证阶段手动跑一次。

- [ ] **Step 5: 提醒运维步骤（不需要动代码）**

改完的 `templates/` 文件不会自动同步到已发布的官方 Skill 库包——需要之后手动在 Skill 库 UI 官方包卡片上点「上传新版本」，把这三个改过的文件重新传一遍，`/goal` 用户才能吃到新版本。这一步在本计划的 commit 之外，是后续的人工操作，此步骤本身不需要 commit。

---

## Self-Review Notes（写计划时已核对，供执行者参考）

- **Spec 覆盖**：设计文档三个部分（①配图延后、②去重复查询、③orchestrator 角色/工具/主循环更新）分别对应 Task 1、Task 2、Task 3 的 Step 5-11；「四个不变式」的更新对应 Task 3 Step 11；「验证方式」对应 Task 4。无遗漏。
- **占位符扫描**：全部 old_string/new_string 都是完整文本，没有 TBD/TODO。
- **类型一致性**：`tpl`（完整对象，含 `.id`/`.name`/`.content`）与 `tpl_id`（数字）在 Task 3 Step 4 起被同时维护，后续 Step 5/7 都用 `tpl.name`/`tpl.content`，命名前后一致。`main_category_id`/`game_positions` 在 Task 1（writer 产出）和 Task 3（orchestrator 消费）里拼写完全一致。
- **额外发现 1**（design doc 未提及，实现时补上的必要细节）：orchestrator 原 pseudocode 只算了 `tpl_id`（数字），没有保留完整 `tpl` 对象——但传 `template_content`/`template_name` 给 subagent 需要完整对象，Task 3 Step 4 补了 `find_template` helper 来解决这个缺口。
- **额外发现 2**（自查中定位到的分支遗漏，design doc 未覆盖）：评审员技术性失败（`parsed_v` 里有 `"error"`）那条分支在 `decision = parsed_v.decision` 之前就 `break`，会绕开插在「decision 已知」之后的配图决策块，导致这类留人工复审的文章配不到图。Task 3 Step 8 把配图调用也接到了这条分支里，保持"终态稿子都配图"的一致性。
