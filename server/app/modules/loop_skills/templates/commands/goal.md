---
description: Geo 协作平台生文 Loop 入口。自然语言目标 → Ralph 风格自动产出 N 篇过自评文章入未审核库 → 飞书播报。
---

# /goal — Geo 生文 Loop

你刚被 `/goal $ARGUMENTS` 调用。把这条命令当作 `geo-goal-orchestrator`
skill 的入口包装：

1. **立刻** invoke the `geo-goal-orchestrator` skill（用 Skill tool）来装载完整 playbook。
2. 装载后，按 skill 里的 Required Checklist 一项一项执行，把 `$ARGUMENTS` 当作用户的自由文本目标传给「Goal Parsing 规则」段。
3. **不要**在装载 skill 之前先自己解析目标或调 MCP；skill 内部第一步就是 sanity check，让它来跑。

## 同事第一次用 /goal 之前要看的

如果是你（同事）第一次用 `/goal`，先看 `.claude/README.md`（本地不入库）
完成 5 步 onboarding（MCP token 配置 + skill 文件本地放置）；不然 sanity
check 会立刻失败。

## 用法示例（锁定问题词 / 生文提示词，重试不乱换）

- `/goal 今天 5 篇国风游戏文章` — 全自由：按候选顺序取题、模板轮转，评分不过换下一题
- `/goal 用问题Id=80,81,82 各写一篇` — **精确锁问题词**：只做这 3 个问题，某篇不过就
  **就同一问题重写**（最多 2 次），仍不过才放弃并在飞书单列
- `/goal 5 篇 生文提示词Id=11` — **锁生文提示词**：全程用 #11，评分不过换问题、模板不变
- `/goal 问题Id=80 生文提示词Id=11` — **模板 + 问题词都锁**：就问题 #80 + 提示词 #11
  反复重写（最多 2 次），两者都不换
- `/goal 8 篇治愈系` — **题材范围锁**：候选按"治愈"过滤，只在这批里换题

> 语法：`问题Id=<逗号分隔数字>`（也认 `问题 #80`）、`生文提示词Id=<数字>`（也认
> `生文提示词 #11` / `用生文提示词 11`）。锁了哪一维，重试就不换哪一维——这就是"不随机乱换词"。

## 这条命令做什么 / 不做什么

**做**：
- 自然语言目标解析（"今天 5 篇国风游戏文章"）
- 自动选题（从问题池避重）
- 启动多个 fresh-context subagent 分别写文章 + 评分
- 把净产出查 GEO 拿 ground truth 作停止条件
- 完成后飞书群播报

**不做**：
- 不发布（分发走独立 loop）
- 不直接改 `article.review_status`（人审兜底）
- 不在主对话里写文章草稿（子 agent 干，不污染主 context）
