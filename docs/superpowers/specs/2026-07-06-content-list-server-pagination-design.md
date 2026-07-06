# 内容列表真·服务端分页(路线 A)

- 日期:2026-07-06
- 状态:设计已认可,待实现计划
- 影响范围:`server/app/modules/articles/`(新增 feed 接口)、`web/src/features/content/ContentWorkspace.tsx`、`web/src/api/articles.ts`

## 背景与问题

打开「内容管理」tab 时,前端 `ContentWorkspace.refreshArticles()` 会用一个 `for` 循环
按每页 200 条**把全部文章一次性拉到前端**(`ARTICLE_FETCH_LIMIT = 200`),直到某批不足 200 条为止。
当前生产库有 1600+ 篇文章,首屏因此发出 8+ 个**串行** `GET /api/articles?skip=…&limit=200` 请求,
首屏随文章量线性变慢。

之所以必须全量拉,是因为列表把两类实体**按创建时间混排、一起翻页**:

- **散篇文章**(不属于任何分组,按审核 tab 过滤)
- **分组**(`ArticleGroup`,按成员审核状态决定出现在哪个 tab)

而且分组会引用**任意**一篇文章:前端展开/分发分组时,组员是靠 `articleById`
(由已加载的全量 `articles` 构成)反查的。只查一页的话,组员大多不在这一页里,分组会展开成空。
此外,待审/已审的**角标计数**也是从全量数据里数出来的。

需求:**维持现在的界面观感(文章+分组按时间混排、分组可展开、搜索、角标),但一次只从服务器查一页。**

纯前端做不到(无法对"没拉下来的数据"排序/翻页,也无法在只查一页时凑齐分组组员),
必须由后端提供一个**合并分页**接口。

## 现状事实(实现前已核实)

- 后端 `GET /api/articles` 已支持 `q` / `skip` / `limit`(≤200)/ `review_status`,
  但**不返回总数**,排序按 `Article.updated_at.desc()`。
  (`server/app/modules/articles/service.py:list_articles`)
- 前端 `unifiedList` 把 articles + groups 合并后**按 `created_at` 倒序**重排,再客户端切页
  `LIST_PAGE_SIZE = 10`。**可见顺序 = `created_at` 倒序**(与后端 `updated_at` 排序不一致,
  因为全量拉下来后前端重排覆盖了它)。
  (`web/src/features/content/ContentWorkspace.tsx:509` 起)
- 分组按 tab 纳入的规则 `groupHasStatus`:approved tab = 有已审成员;
  pending tab = 有未审成员**或空组**(total==0 记 pending)。
  分组自带 `review_summary`(`compute_group_review_summary`),角标不必依赖全量文章。
- 分组组员在前端由 `articleById` 反查(`groupArticleSummaries`,`ContentWorkspace.tsx:1025`),
  展开、分发、勾选都用它。
- 「加入分组」选择器 `groups.map`(`ContentWorkspace.tsx:1455`)需要**全部分组**列表。
- 搜索 `q` 现状:文章走后端过滤;分组按组名在前端 `includes` 过滤;
  角标计数中**文章受 q 过滤、分组不受**(极细微不一致)。

## 方案对比

- **路线 A(采纳)**:新增合并分页接口 `GET /api/articles/feed`,后端把 articles+groups
  按 `created_at` 混排、切页返回,并内嵌分组组员摘要 + 两 tab 计数。忠实保留现状 UX,真·一页一查。
  代价:后端一个 UNION 合并查询 + 计数;前端改动集中在 `refreshArticles` 与列表渲染。
- **路线 B(否决)**:只给 `/api/articles` 加 `exclude_grouped` + 总数,散篇文章服务端分页,
  分组仍全量加载。等价于"分组置顶",与"维持现状混排"的诉求冲突,已被用户否决。

## 详细设计

### ① 新接口 `GET /api/articles/feed`

返回**已合并、已排序、已切页**的一页,加两 tab 计数。

**入参**

| 参数 | 说明 |
|------|------|
| `review_status` | `pending` / `approved`,决定 tab |
| `q` | 可选搜索词 |
| `skip` | 偏移 |
| `limit` | 每页混排项数,默认 `10`(对齐现有每页 10 行) |

**返回**

```jsonc
{
  "items": [
    { "kind": "article", "article": <ArticleListRead> },
    { "kind": "group",   "group": { /* ...ArticleGroupRead */ "members": [<ArticleListRead>] } }
  ],
  "counts": { "pending": 123, "approved": 45 }
}
```

- `items` 顺序即最终展示顺序(`created_at` 倒序混排)。
- **group 项内嵌 `members`(组员 `ArticleListRead` 数组,按 `sort_order` 排)**——
  这是"只查一页也能展开/分发分组"的关键。
- `counts` 供两个角标;当前页总数 = `counts[review_status]`,前端据此算总页数。
- 新增 Pydantic schema:`ArticleGroupReadWithMembers`(继承 `ArticleGroupRead` + `members`)、
  `FeedItem`(`kind` + 二选一负载)、`ArticleFeedResponse`(`items` + `counts`)。

### ② 后端查询逻辑(service 层新函数)

在数据库里合并两张表、按 `created_at` 倒序分页。**排序键统一用 `created_at`**(对齐现有可见顺序)。

- **散篇文章分支**:`Article.is_deleted == False` 且 `review_status == tab` 且
  **不属于任何未删分组**(对 `article_group_items` join `article_groups`(未删)做 `NOT EXISTS`);
  q 命中:≥3 字复用现有 FTS `MATCH(...) AGAINST`(不可用时回退 LIKE,与 `list_articles` 一致),
  <3 字走 LIKE。投影 `(kind='article', id, created_at)`。
- **分组分支**:`ArticleGroup.is_deleted == False` 且按 `groupHasStatus` 规则纳入当前 tab
  (approved = `EXISTS` 已审未删成员;pending = 无未删成员 **或** `EXISTS` 未审未删成员);
  q 命中组名 LIKE。投影 `(kind='group', id, created_at)`。
- 两分支 `UNION ALL` 后 `ORDER BY created_at DESC` + `LIMIT :limit OFFSET :skip` 得到本页
  `(kind, id)` 列表;再各自 hydrate:文章走 `_list_summary_load_options` 出 `ArticleListRead`,
  分组出 `ArticleGroupReadWithMembers`(组员用同样的 summary 序列化)。
- **计数**:对上面同一套过滤(含 q,文章与分组都受 q 约束——顺带修掉现状"分组计数不受 q"的不一致),
  分别数 pending / approved 的 `article + group` 合计,返回 `counts`。

### ③ 前端改动(集中在 `ContentWorkspace.tsx` + `api/articles.ts`)

- `api/articles.ts`:新增 `listArticleFeed(params)` → `GET /api/articles/feed`,
  以及类型 `FeedItem` / `ArticleFeedResponse` / `ArticleGroupWithMembers`。
- `refreshArticles()`:**删掉全量 `for` 循环**,改为一次 feed 请求,入参
  `(reviewTab, query, articlePage)`。翻页、切 tab、改搜索都触发重取。
- 主列表 `unifiedList` / `pagedUnifiedList`:**直接用服务端返回的 `items`**,
  删掉客户端的合并 + 排序 + 切片逻辑。
- `totalArticlePages`:用 `counts[reviewTab]` 与 `LIST_PAGE_SIZE` 算。
- `reviewCounts` 角标:改用服务端 `counts`。
- `articleById`:由**本页**的散篇文章 + 本页 group 项内嵌的 `members` 组装,
  供展开(`groupArticleSummaries`)、分发、勾选。
- `groups`(`/api/article-groups` 全量):**仅保留给「加入分组」选择器**,不再喂主列表。
- 各类写操作后的刷新(审核/删除/改组/新建)→ 只重取当前 feed 页 + counts。

### ④ 边界处理

- **删到当前页空**:重取后若 `items` 为空且 `skip > 0`,按 `counts[reviewTab]` 夹紧页码回退再取。
- **切 tab / 改搜索**:重置 `articlePage = 0` 再取。
- **老接口 `/api/articles`(全量)不动**:留给 MCP `list_articles` 等其它调用方,爆炸半径最小。
- **分页 vs 焦点自动刷新**:现有 focus/visibilitychange 自动重拉逻辑保留,只是重拉的是当前 feed 页。

## 测试

- **后端**(新增 `server/tests/test_articles_feed.py`,需 MySQL):
  - 混排顺序 = `created_at` 倒序,articles 与 groups 正确交错
  - 分组按 tab 纳入(approved/pending 规则)、空组归入 pending
  - grouped 文章不出现在散篇里(被 `NOT EXISTS` 排除)
  - `counts` 两 tab 正确、受 q 约束
  - 分页边界:`skip`/`limit`、最后一页、越界页返回空
  - group 项内嵌 `members` 完整且按 `sort_order` 排
  - q 命中文章正文与组名
- **前端**:`pnpm --filter @geo/web typecheck` + `build`(前端无单测框架);
  手动验证翻页、切 tab、搜索、分组展开/分发、删除后页码回退。

## 非目标(YAGNI)

- 不改分组的展现形态(不做分组置顶、不拆子页签)。
- 不引入 cursor/keyset 分页(沿用现有 numbered page + offset;数据量级 offset 足够)。
- 不动老 `/api/articles` 接口与 MCP 路径。
- 不改搜索的相关性排序(现状可见顺序本就是 `created_at` 倒序,非相关性)。
