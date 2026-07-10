---
name: geo-article-writer
description: Use when spawned as a writer subagent by /goal, or when manually
  composing one GEO article. Writes markdown and calls save_article; when
  spawned by /goal, orchestrator handles illustration afterward — manual
  standalone use needs an extra explicit ai_illustrate_article call. Returns
  article_id.
---

# Role

你**只写一篇**文章并入库。不要循环、不要评分、不要碰其它 article。
输入由 orchestrator 在 prompt 里给你；输出按最后约定的 JSON 单行回主对话。

# Required Checklist (per spawn)

0. **看 input 有没有 `rewrite_feedback`**（`none` 以外即有）—— 有就是"重写模式"：
   这是对同一问题的重写，上一版没过审。按下面「重写模式」段有的放矢地改进，别照抄上一版
1. get question / template — **直接用 input 里给的 `question_text` / `template_content`**（orchestrator
   已经查过一次候选池和模板列表，这两个字段是命中结果，不用你再查一遍）。仅当这两个字段
   **确实缺失**时才退回去查一次作为兜底：`list_question_items(pool_id=...)` 反查 `question_text` /
   `list_prompt_templates(scope="generation")` 反查 `template_content`
2. **打点，动笔前，独立执行**
   `report_event(source_module="geo_article_writer", event_type="companion_games_selection_started", level="info", message=f"开始筛选陪衬游戏：{question_text}", source_type="question_item", source_id=qid, payload={"qid": qid, "tpl_id": tpl_id, "question_text": question_text})`
   标记"开始按 template 的『真实游戏库』要求筛选陪衬游戏、动笔写正文"这一时刻，让
   orchestrator / 运营不用等文章写完才知道这一步真的发生过。
3. 写 markdown body（约束见下；重写模式下先读 `rewrite_feedback` 再动笔）。
4. `save_article(question_item_id, prompt_template_id, title, markdown_content,
   model_label, prompt_template_name=<input 里的 template_name>,
   question_text_preview=<input 里的 question_text 前 ~40 字>)` —
   后两个**展示参数**是给 Claude Code UI 看的：传了之后工具调用渲染会显示
   `prompt_template_id: 13, prompt_template_name: "游戏情绪清单"`，运营在主对话里
   一眼就知道用了哪个模板 / 哪个问题，不用回头查数字。后端会丢弃这两字段
   （Pydantic `extra='ignore'`），传错不报错——但**务必传**，否则 UI 只显示数字
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

> **不是 `/goal` loop、而是被人工单独 `Skill geo-article-writer` 手动叫起来写一篇时**：
> 没有 orchestrator 主循环在后面接手配图，`main_category_id`/`game_positions` 写进返回 JSON
> 也不会有人读它。这种手动模式下，写完之后要配图需要用户另外显式说一句让 Claude 调
> `ai_illustrate_article(article_id, main_category_id=<矩阵特例段的值>, web_fallback=True, game_positions=<上面判断的那份或 None>)`，
> 不会自动发生。

# 重写模式（input 带 `rewrite_feedback` 时）

orchestrator 在评分不过、且用户**锁定了这个问题词**时，会带着上一版的评审反馈再 spawn 你一次，
让你就**同一个问题**重写一版（问题词被锁死，不能换成别的问题）。此时 `rewrite_feedback` 形如：

```
{"last_article_id": 811, "last_decision": "needs_rewrite", "last_score": 58,
 "weak_dims": ["readability", "style"], "reasoning": "开篇空洞、与模板轻松语气不符"}
```

规则：
- **必须针对 `weak_dims` + `reasoning` 实质改进**：薄弱在 readability 就重排结构 / 删空洞引入；
  薄弱在 style 就贴紧模板语气；薄弱在 factuality 就删掉不可验证的数字 / 引述；薄弱在
  policy_safety 就清理合规风险点。**不要照抄上一版**、也不要只改标题糊弄。
- 问题词 / 生文提示词都由 orchestrator 传入且已锁定——你**不换问题、不换模板**，只把这一篇写得更好。
- 仍走正常 `save_article` 落**一篇新文章**（新 article_id），照常配图、照常返回 JSON。
  你不需要删旧文章、不需要读 `last_article_id` 的正文（它已在库里，人工兜底）。
- `rewrite_feedback == none` = 首稿，正常写即可，不用管本段。

# title vs markdown_content 约束（重要）

- `title` 是单字段，<= 300 字符，**不要**在 `markdown_content` 顶部再写 `# 标题`
- `markdown_content` 从正文第一段开始；用 `## / ###` 做次级标题；列表 / 加粗按需
- `markdown_content` 是正文自然文本，不是 JSON 字符串源码；**不要**把正文引号写成 `\"`
- 中文文章优先用 `“”` / `「」`；确实需要 ASCII 引号时直接写 `"`，不要转义
- 后端 `save_article` 会把 markdown 转 Tiptap + HTML，重复标题会进段落里污染显示

# 通用写作约束

- 内容紧扣 `question_text`
- 参考 template content 的语气 / 结构指引（template 是给你看的指令，**不是给读者看的**——不要把 template 的指令性句子写进文章）
- 不胡编事实；不可验证的数字 / 引述删除或改写
- 不触发平台合规风险（政治 / 医疗 / 灰产宣传等）

## 矩阵特例：餐厅养成记官方矩阵（默认）

- 风格：轻松实用，避免「开篇一段宏大引入」，直接进主题
- 偏好题材：游戏推荐 / 攻略 / 玩法解析 / 国风游戏综述
- 配图主推栏目：`main_category_id = <REPLACE_ME>`  # ← 安装时填，**不知道 id 就
  对 Claude 说「帮我查下主推栏目，我用<矩阵名>」**，它会调 list_stock_categories
  MCP 工具列候选并用 Edit 工具帮你写到这里。也可以去 GEO 后台「图库管理」→
  主推栏目手抄 id
- 配图风格：默认 `aggressive_images=True`（积极配图，每个明确出现的游戏都插）
- 封面：默认 `set_cover=True`（从主推栏目随机取一张做封面，已有封面则跳过）
- 陪衬：默认 `include_companion=True`（AI 同时从所有陪衬栏目选）
- 联网兜底：默认 `web_fallback=True`（图库里【没有】对应栏目的游戏，AI 用规范中文名
  点名后，GEO 自动建陪衬栏目 + 走百度（千帆 AI 搜索）联网搜一张横版图补上——这样
  图库里还没有的新游戏也配得上图。best-effort：需容器配 `GEO_BAIDU_API_KEY`，
  key 缺失 / 网络失败时静默不补、不报错，绝不阻塞交付）

> 这一段的 `main_category_id` 值和上面几条默认参数是**给 orchestrator 用的**——
> 你只需要在返回 JSON 里带上这个 `main_category_id`；实际调用 `ai_illustrate_article`
> 的时机和参数组装（`web_fallback=True`、`game_positions` 等）由 orchestrator 决定，
> 详见 `geo-goal-orchestrator/SKILL.md` 主循环里的配图决策块。

## 加新矩阵的方法（给团队同事）

1. 在你本机 `~/.claude/skills/` 或 `<repo>/.claude/skills/`（取决于装在哪一级）
   下复制本目录为 `geo-article-writer-<matrix-code>/`
2. **只改本文件「矩阵特例」这一节**；其它段落不动
3. 调用时 `/goal matrix=<matrix-code> ...`，orchestrator 会装载对应目录的 SKILL.md

> 服务端正本（`server/app/modules/loop_skills/templates/`）默认只有
> 餐厅养成记矩阵；新增矩阵建议在本机做，避免污染共享分发包。

# 失败处理

- `save_article` 失败（如 415 / 标题超长 / DB 冲突）
  → 输出 `{"error": "<message>"}` 退出；orchestrator 会跳过这条 qid 不再重试
- `list_question_items` / `list_prompt_templates`（兜底路径，仅 input 缺字段时才会用到）失败 → 同上

# 返回格式（**强制**）

最后一条消息只能是单行 JSON：

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
