# 自评分「真实分 / 合格线」显示 设计

> 日期：2026-07-08
> 状态：设计已确认，待写实现计划
> 影响面：内容列表卡片的自评分显示 + auto_review 决策落库 + verifier skill

## 背景 / 问题

`/goal` 生文 Loop 的 verifier（`geo-article-verifier` skill）给每篇文章打自评分，经
`submit_review_decision` 落到 `auto_review_decisions.score_total`（`Integer` 列）。内容列表卡片
取每篇最新一条 `score_total` 显示成「评分：84」，按 ≥70绿 / ≥40黄 / <40红 上色。

当 skill.md 里的**合格线设置过高**时，一批文章达不到线、被判 `needs_rewrite` / `rejected`，
但**仍然入库**（review_status 保持 pending，人审兜底）。此时卡片只显示一个光秃秃的分数
（如「65」），运营看不出"它其实没过这次设的高线"。

**目标**：没过线的文章，评分显示成「真实分 / 合格线」（如 `65 / 80`），一眼可辨；过线的文章
仍显示单个数字。

## 关键约束 / 现状事实

- 合格线现在**完全不落库** —— 只活在 verifier `SKILL.md` 的「决策门槛」里
  （`score_total >= 70` 那个 `70`）。数据库不知道合格线是多少。
- `score_total` 在多处被当 int 用：LLM 评分（`score_recent_articles`）、loop 停止条件
  （`list_today_loop_articles`）、`submit_review_decision` 入参。**不能**把这个 int 列直接改成字符串。
- 前端 `auto_review_score` 目前只出现在内容列表卡片（`ArticleListItem.tsx`），文章详情 / feed
  的 `ArticleRead` 里没有这个字段。本次范围只涉及列表卡片。
- 评分失败哨兵：`auto_review/service.py` 在 LLM 评分失败时写 `score_total = -1`；前端现在靠
  `>= 0` 守卫把它藏掉。本次要保留这个"藏掉"行为。

## 核心设计决策

**不改数据库那个 `Integer` 列的类型。** 数据库仍存两个独立的 int：真实分 `score_total`（不变）
+ 新增合格线 `pass_line`（可空 int）。字符串 `"65 _ 80"` 只在**后端序列化那一刻**拼出来给前端，
**永不落库**。这样：

- 老数据零迁移：旧行没有 `pass_line` → 序列化走 else 分支，`84`(int) → `"84"`(str)。
- `score_total` 作为 int 的所有既有消费路径不受影响。
- 只有对前端暴露的 `ArticleListRead.auto_review_score` 从 `int|None` 变 `str|None`。

内部编码用 ` _ `（空格+下划线+空格）当"没过线"标记，前端识别到就渲染成 `真实 / 合格`。

## 行为规格（序列化规则）

后端 `serialize_article_summaries` 取每篇**最新一条**决策（保留现有 `id desc + 只取第一条`
语义），按下列规则算 `auto_review_score`：

| 条件 | `auto_review_score` |
|---|---|
| `score_total` 为 None 或 `< 0`（评分失败哨兵） | `None`（前端不显示） |
| `pass_line` 非空 **且** `score_total < pass_line` | `"{score_total} _ {pass_line}"`，如 `"65 _ 80"` |
| 其它（过线 / 无合格线的老数据） | `str(score_total)`，如 `"84"` |

**边界**：若某文 `score_total >= pass_line` 但因 `policy_safety < 80` 没过审 → 命中"其它"分支，
显示纯数字。它确实过了分数线、栽在合规硬门，不适用"真实分/合格线"这个语义，符合预期。

## 前端显示规格

`ArticleListItem.tsx`：
- 判空：`article.auto_review_score != null`（去掉原 `>= 0` 数字比较——现在是字符串）。
- 含 ` _ ` → 拆成两段，渲染 `真实 / 合格`（如 `65 / 80`），**整块红色**（不管真实分多少，
  没过线统一红/"未通过"感——用户选定 B 方案）。
- 不含 ` _ ` → `Number(x)` 后走现有分档上色（≥70绿 / ≥40黄 / <40红）。

## 组件级改动清单

### ① 数据模型 + 迁移
- `server/app/modules/auto_review/models.py`：`AutoReviewDecision` 加
  `pass_line: Mapped[int | None] = mapped_column(Integer, nullable=True)`。
- 新迁移 `server/alembic/versions/0060_auto_review_pass_line.py`（接当前 head `0059` 之后）：
  `op.add_column("auto_review_decisions", sa.Column("pass_line", sa.Integer(), nullable=True))`，
  `downgrade` drop 该列。加法式、可空，对老数据无影响。

### ② 后端
- `auto_review/schemas.py`：
  - `AutoReviewSubmitRequest` 加 `pass_line: int | None = None`。
  - `AutoReviewDecisionRead` 加 `pass_line: int | None`。
- `auto_review/service.py:submit_decision`：构造 `AutoReviewDecision(..., pass_line=req.pass_line)`。
- `articles/service.py:serialize_article_summaries`：score 子查询多 select `pass_line`；把
  `score_map` 的值从 int 改成"按行为规格算好的字符串或 None"；`auto_review_score=score_map.get(a.id)`。
- `articles/schemas.py`：`ArticleListRead.auto_review_score` 由 `int | None` 改 `str | None`
  （注释同步更新）。

### ③ MCP 工具
- `server/mcp/tools/action.py:submit_review_decision`：加入参 `pass_line: int | None = None`，
  非空时 `body["pass_line"] = pass_line`。docstring 补一行说明。

### ④ 前端
- `web/src/types.ts`：`auto_review_score: number | null` → `string | null`。
- `web/src/components/ArticleListItem.tsx`：按"前端显示规格"改判空 + 渲染逻辑。

### ⑤ Skill（verifier）+ 部署注意 ⚠️
- `server/app/modules/loop_skills/templates/skills/geo-article-verifier/SKILL.md`：
  - 把「决策门槛」里硬编码的 `70` 提成一个显式**合格线**值（operator 改这一处）。
  - 第 7 步 `submit_review_decision(...)` 调用增加 `pass_line=<合格线>` 参数。
  - 明确：`pass_line` 传的是分数线（approval 的 `score_total` 门槛），不是 `policy_safety` 那道门。
- **部署坑（记忆中栽过）**：`/goal` 实际读的是 `~/.claude/skills/geo-article-verifier/` 里的
  **安装版**，不是仓库模板。改完模板必须：① 同步覆盖安装版；② `loop_skills/version.py` bump +
  跨 OS 一致 SHA（按 posix 串排序 + LF，跑 CI 取真值），否则 bundle 校验 / pipeline 变红。
  实现计划里单列这一步。

### ⑥ 测试
- `server/tests/test_article_list_score.py`：
  - 现有断言 `auto_review_score == 85` → `== "85"`。
  - 新增：`score_total=65, pass_line=80` 的文章 → `"65 _ 80"`。
  - 新增：`score_total=-1` 的文章 → `None`。
  - 新增：`score_total=85, pass_line=80`（过线）→ `"85"`（不带尾巴）。
- `server/tests/test_auto_review*.py`：加一例 `submit_decision` 带 `pass_line` 能存能读回
  （`AutoReviewDecisionRead.pass_line`）。

## 非目标（YAGNI）
- 不改数据库 `score_total` 列类型。
- 不给文章详情页 / feed 加自评分显示（当前就没有）。
- 不改 `list_today_loop_articles` / `score_recent_articles`（它们继续按 int 用 `score_total`，
  `pass_line` 与它们无关）。
- 不做历史分数展示 / 分数变化趋势。

## 回滚
- 迁移可 `downgrade`（drop `pass_line` 列）。
- 前后端字段类型回退到 int 即恢复原行为；`pass_line` 列留空时序列化天然走纯数字分支，
  故迁移已上、代码未上时也不炸（读不到 `pass_line` 只是不显示尾巴）。
