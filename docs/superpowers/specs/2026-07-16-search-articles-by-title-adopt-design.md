# 按标题搜索自有文章入库 + 采纳可标问题词（高质量库入库便利化）设计

- 日期：2026-07-16
- 分支：`feat-high`
- 关联：`docs/superpowers/specs/2026-07-13-adversarial-review-quality-gate-design.md`（对抗评审质量门主设计）

## 一、背景与目标

高质量库（`quality_reference`）已有两条 MCP 入库/读取能力：

- `adopt_quality_reference(article_id, category?)` —— 按**文章 ID**采纳一篇已审文章进库。
- `get_article(article_id)` —— 读单篇文章。

痛点：MCP 侧**无法按标题找到文章 ID**。现有 `list_articles` 工具对应的 `GET /api/mcp/articles`
端点把 `query` 写死成 `None`，所以要采纳一篇文章必须先知道它的数字 ID，才能调 adopt。

**目标**：让"按标题快速把自有文章加进高质量库"成为顺畅的两步流程：

```
search_articles_by_title(title="周年庆")   → 候选 [{id:42,…}, {id:71,…}]
adopt_quality_reference(article_id=42)      → 入高质量库 ✓
```

顺带补齐一处能力缺口：让"采纳"这一步也能标**问题词（question_texts）**，而不只是文章类型。

## 二、现状与缺口（已核实）

### 2.1 标题搜索

- 服务层 `articles/services/feed.py:list_articles(db, query=…)` **已支持**标题搜索
  （MySQL ngram FTS，`len(query)>=3` 走 FTS、否则/失败回落 LIKE，搜 title+author+plain_text）。
- 但 MCP 端点 `mcp_catalog/router.py:mcp_list_articles` **硬编码 `query=None`**，能力没暴露给 MCP。

结论：搜索逻辑本就存在，只是没有一条 MCP 通路能按标题定位文章。

### 2.2 采纳时的标签能力（`adopt_article`）

`quality_reference/service.py:adopt_article` 的关联逻辑：

| 场景 | 文章类型（category） | 问题词（question_texts） |
|---|---|---|
| 文章**有溯源** `source_question_category`（/goal 存的文章） | ✅ 自动取 `source_question_category`（`category` 参数被忽略） | ✅ 自动取 `source_question_texts` |
| 文章**无溯源**（普通文章） | ✅ 用回落 `category` 参数 | ❌ 写死 `question_texts=None` |

- `adopt_quality_reference` MCP 工具签名只有 `category`，**没有 `question_texts`**。
- 对比 `import_external`（外部录入）**有** `question_texts`（`ImportRequest.question_texts`）——
  这是 adopt 相对 import 的一处不对称。
- MCP 侧无"编辑参考类目"的工具，所以无溯源文章经 MCP 采纳后，问题词只能靠人到 Web
  「高质量库」多类型 PATCH 编辑补——对 MCP 流等于问题词缺失。

## 三、选定方案与被否方案

**选定**：新增一个**专用只读搜索工具**（`search_articles_by_title`）+ **增强现有 `adopt_quality_reference`**
加可选 `question_texts`。搜索与采纳解耦、可组合、安全（不会误采纳）。

被否/被比较的方案：

- **一步式"按标题直接采纳"**：标题重名时会误把错的文章注入参考池（污染对抗判分），
  与既有 adopt 的谨慎设计（只收 approved、MCP 不能注入外部内容）相悖 —— 否。
- **仅给 `list_articles` 加 `query` 参数（不新增工具）**：最省、计数不变，但通用工具的
  docstring 引导不到"采纳"工作流，且不好默认只搜 approved / 标 already_adopted —— 不采用，
  改用专用工具。

## 四、详细设计

### 4.1 新 MCP 工具（catalog 只读组）

文件：`server/mcp/tools/catalog.py`，沿用该模块 `async def + anyio.to_thread` 惯例（自调用死锁规避）。

```python
@mcp.tool()
async def search_articles_by_title(
    title: str,
    review_status: str | None = "approved",
    limit: int = 20,
) -> dict[str, Any]:
    """按标题关键词搜自有（站内）文章，主要用于挑一篇已审文章去 adopt_quality_reference 入高质量库。

    Args:
        title: 标题关键词（子串匹配，只搜标题）。
        review_status: 默认 "approved"（只返回能直接采纳的候选）；传 "pending" 或 None（全部）放开。
        limit: 1–100，默认 20。

    Returns:
        {"ok": True, "data": {"items": [
            {"id", "title", "review_status", "word_count",
             "already_adopted": bool, "snippet": str, "created_at": str}
        ]}, "error": None}

    典型用法：先本工具拿到 id → 再 adopt_quality_reference(article_id=id)。
    """
```

- `title` 为必填。空/纯空白 title → 返回空 `items`（不等同于列全部文章，避免和 `list_articles` 语义重叠）。
- `already_adopted`：该文是否已在高质量库（避免重复采纳/困惑）。
- `snippet`：正文前 ~100 字，给同标题文章做区分（本平台批量生文常同标题）。

### 4.2 后端端点

`GET /api/mcp/articles/search`，挂 `mcp_catalog/router.py`，`Depends(require_mcp_token)`（走 catalog 现有 router 的 MCP 鉴权）。

Query 参数：`title: str`、`review_status: str | None = "approved"`、`limit: int = Query(20, ge=1, le=100)`。

**路由排序坑**：现有 `@router.get("/articles/{article_id}")` **未加 `:int` 转换器**，`/articles/search`
会被它当作 `article_id="search"` 命中 → 422。修法：给 `mcp_get_article` 的路由改成
`@router.get("/articles/{article_id:int}")`（符合仓库既有纪律，见 `quality_reference/router.py` 的
`/{ref_id:int}`、`articles.py:137` 注释）。加 `:int` 后 `search` 不再误命中，`/search` 与 `/{article_id:int}`
注册先后都安全。

响应：为带 `snippet` / `already_adopted` 自定义一个轻量结构（不复用 `ArticleListRead`，它无
`plain_text`/无这两个衍生字段）。端点返回 `{"items": [...]}`，由工具层包 `{ok,data,error}` 信封。

未捕获异常按 MCP 规约走 `core/mcp_errors.mcp_exception_response`（见 CLAUDE.md MCP 章节）。

### 4.3 服务函数

在 `articles/services/feed.py` 新增聚焦函数，**不改** `list_articles` 语义：

```python
def search_by_title(
    db, *, title: str, review_status: str | None = None, limit: int = 20
) -> list[Article]:
    """标题子串搜索（LIKE on articles.title，转义 % _ \\），按 updated_at 倒序，
    load_only 摘要列 + plain_text（供 snippet 截断）。title 为空返回 []。"""
```

- 只搜**标题**（贴合"依据标题搜索"），转义通配符（同 `quality_reference.list_references` 的 `q` 处理）。
- `load_only(title, author, review_status, word_count, created_at, updated_at, plain_text)` + `lazyload(tags)`，
  避开 `content_json`/`content_html` 两个大 Text 列；`plain_text` 结果集小（≤100 行）可接受。
- `user_id` 一律 service 视角（不按属主过滤），与现有 `mcp_list_articles`、`adopt_article`（不校验 owner）一致。
  "自有文章"指**站内**文章（区别于外部导入参考），非 per-account 属主。

端点侧算 `already_adopted`：`SELECT article_id FROM quality_reference WHERE article_id IN (:ids)` → set，
命中即 `True`（含已下架的历史行，任何行都代表"采纳过"）。`snippet = (plain_text or "")[:100]`。

### 4.4 `adopt_quality_reference` 增强：可选 `question_texts`

打标签发生在 adopt 步，不在搜索步。为让"按标题/按 ID 入库"都能同时标类型+问题词：

改动点：

1. `service.py:adopt_article` 签名加 `fallback_question_texts: list | None = None`；
   回落分支改为 `set_reference_categories(db, ref.id, [{"category": fallback_category,
   "question_texts": fallback_question_texts}])`。
2. `mcp_router.py:AdoptFromMcpPayload` 加 `question_texts: list | None = None`；`adopt_from_mcp`
   透传为 `fallback_question_texts`。
3. `server/mcp/tools/action.py:adopt_quality_reference` 签名加 `question_texts: list[str] | None = None`，
   非空时放进 body。
4. **前端 adopt 不动**（`AdoptRequest` / `router.py:adopt`）——前端已有多类型 PATCH 编辑 UI 覆盖问题词，
   保持范围最小。`adopt_article` 新参数带默认值，前端调用点无需改。

语义（写进 docstring）：

- `category` / `question_texts` 都是**回落参数**，仅在文章**无** `source_question_category` 时生效；
  文章有溯源时二者被忽略、走溯源自动关联（保留既有语义）。
- `question_texts` 必须与回落 `category` 配对；只传 `question_texts` 不传 `category` → 无类目可挂、
  静默忽略（不硬报错）。

## 五、计数、文档

- `mcp_catalog/connect_router.py:MCP_TOOLS_COUNT` 30 → **31**（catalog 13 → 14）。
- 断言同步：`test_mcp_status_count.py`（`== 30` 两处 + 函数名/注释）、`test_mcp_tools_registration.py`
  （`== 30` → 31），`test_mcp_connect.py` 读常量自动跟随。
- `CLAUDE.md` MCP 章节：catalog 清单加 `search_articles_by_title`、`adopt_quality_reference` 备注加
  `question_texts`、总数 30 → 31（含"Tool 三组（共 30 个）"标题与真值说明处）。

## 六、测试计划（TDD，先红后绿）

新增 `server/tests/test_articles_search_mcp.py`（`@pytest.mark.mysql`，用 `build_test_app`）：

- 建若干文章（含同标题、不同 review_status、其一已 `adopt`），`GET /api/mcp/articles/search`：
  - 命中标题子串；默认只回 approved；`review_status=None` 放开回全部。
  - `already_adopted` 对已采纳文章为 `True`、其余 `False`。
  - `snippet` 截断、`limit` 夹取。
  - `title=""` 回空 `items`。
  - 路由不撞 `GET /api/mcp/articles/{id}`（`/search` 不被 `:int` 吃、`/{id}` 仍正常取单篇）。
- MCP token 缺失 → 401（沿用现有鉴权断言风格）。

扩充 `server/tests/test_quality_reference_mcp.py`：

- `adopt-from-mcp` 传 `question_texts` + `category`（无溯源文章）→ 参考的 `categories[0].question_texts` 落库。
- 有溯源文章传 `question_texts`/`category` → 被忽略，仍取溯源类目+问题词（回归既有语义）。

计数守卫：更新三处 `== 30` → `31`。

## 七、非目标 / YAGNI

- 不做一步式"按标题直接采纳"。
- 不给 `list_articles` 通用工具加 `query`（本次只加专用搜索工具）。
- 不动前端 adopt 表单（问题词经既有多类型 PATCH 编辑）。
- 不加正文全文搜索（只搜标题）；不做 total 计数分页（curated 规模有限，limit 截断足够）。

## 八、风险与权衡

- **标题重名**：搜索返回多条候选是**特性**（让调用方/人二次确认后再 adopt），非缺陷；`snippet` +
  `already_adopted` + `created_at` 辅助区分。
- **改 `/articles/{article_id}` 加 `:int`**：非 int 的 article_id 由 422 变 404（更合理），现有调用方
  一直传 int，无破坏。
- **`plain_text` 载入**：仅本搜索端点、结果集 ≤100 行，成本可忽略；不进 `list_articles` 摘要通路。
- **能力对称性**：给 adopt 补 `question_texts` 后与 import 对齐；仍严格保持"溯源优先、回落其次"，
  不改变有溯源文章的既有行为。
