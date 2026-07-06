# 内容列表真·服务端分页 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把「内容管理」列表从"一次全量拉 1600+ 篇"改成真·服务端分页(一次只查一页),同时忠实保留现状 UX(文章+分组按创建时间混排、分组可展开、搜索、待审/已审角标)。

**Architecture:** 新增后端合并分页接口 `GET /api/articles/feed`,在数据库里把 `articles`(散篇、不属于任何分组)与 `article_groups`(按成员审核状态纳入)按 `created_at` 倒序 `UNION ALL` 后切页返回;group 项内嵌组员摘要,使分组的展开/分发不再依赖"全量文章"。前端 `ContentWorkspace` 把全量 `for` 循环改成一页一查,主列表直接渲染服务端返回的混排页。

**Tech Stack:** 后端 FastAPI + SQLAlchemy 2.x Core(`select`/`union_all`/`exists`)+ MySQL;前端 React 19 + TypeScript(strict);后端测试 pytest(需 MySQL,`GEO_TEST_DATABASE_URL`)。

## Global Constraints

- 后端 **MySQL only**;service 层抛命名异常(`ClientError`/`ConflictError`),**不抛裸 `ValueError`**。
- 排序键统一用 **`Article.created_at` / `ArticleGroup.created_at` 倒序**(对齐现有可见顺序,不是 `updated_at`)。
- **per-user 作用域**:非 admin 只看自己的文章**和**自己的分组;admin(`user_id=None`)看全部。文章分支、分组分支、计数都要带此约束。
- feed 搜索用 **LIKE 子串**(`title`/`author`/`plain_text`;组名 `name`),不接 FTS。
- 每页默认 `limit=10`(对齐现有 `LIST_PAGE_SIZE = 10`);`limit` 上限 100、`skip>=0`。
- 后端测试 DB 名必须含 `"test"`;用 `build_test_app(monkeypatch)`,`finally` 里 `test_app.cleanup()`。
- 前端**无单测框架**:门禁 = `pnpm --filter @geo/web typecheck` + `build`。
- lint:后端 `ruff check server/` + `ruff format`;改动后都要过。
- 老接口 `/api/articles`(全量)与 MCP 路径**不动**。
- commit message 结尾附:`Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`。

---

## File Structure

**后端**
- `server/app/modules/articles/service.py` — 抽出 `serialize_article_summaries()` 复用 helper(Task 1);新增 `list_article_feed()` 合并分页 + 计数(Task 3)。
- `server/app/modules/articles/schemas.py` — 新增 `ArticleGroupReadWithMembers` / `FeedCounts` / `ArticleFeedItem` / `ArticleFeedResponse`(Task 2)。
- `server/app/modules/articles/router.py` — `read_articles` 改用 helper(Task 1);新增 `GET /api/articles/feed`(Task 4)。
- `server/tests/test_articles_feed.py` — 新增(Task 3、Task 4)。

**前端**
- `web/src/types.ts` — 新增 `ArticleGroupWithMembers` / `ArticleFeedItem` / `ArticleFeedResponse`(Task 5)。
- `web/src/api/articles.ts` — 新增 `listArticleFeed()`(Task 5)。
- `web/src/features/content/ContentWorkspace.tsx` — 改 `refreshArticles`、主列表渲染、计数、`articleById`、分页(Task 6)。

---

## Task 1: 抽出文章摘要序列化 helper(后端重构,零行为变化)

现状 `read_articles`(`router.py:167-207`)内联算 `published_count`(succeeded 发布数)+ `auto_review_score`(最新决策分)再拼 `ArticleListRead`。feed 也要对散篇文章 + 分组组员做同样序列化,故先抽成可复用、按 id 建 map 的 helper。

**Files:**
- Modify: `server/app/modules/articles/service.py`(新增函数)
- Modify: `server/app/modules/articles/router.py:167-207`(改用 helper)
- Test: `server/tests/test_articles_api.py`(复跑既有列表测试验证零回归)

**Interfaces:**
- Produces: `serialize_article_summaries(db: Session, articles: list[Article]) -> dict[int, ArticleListRead]` —— 输入一批 Article ORM,返回 `{id: ArticleListRead}`。两条 batch 查询算 `published_count`/`auto_review_score`;空输入返回 `{}`。

- [ ] **Step 1: 在 service.py 顶部确认已 import 所需符号**

`service.py` 需要 `PublishRecord`、`AutoReviewDecision`、`func`、`ArticleListRead`。检查文件头 import,缺则补:

```python
from sqlalchemy import bindparam, func, select, text  # func 可能已在
from server.app.modules.articles.schemas import ArticleListRead
from server.app.modules.tasks.models import PublishRecord
from server.app.modules.auto_review.models import AutoReviewDecision
```

> 若这些 import 会造成循环依赖(auto_review / tasks 反向依赖 articles),改成在函数体内**懒导入**(见 CLAUDE.md「bg_session_factory 懒导入」惯例)。先按顶层写,`ruff`/启动报循环再降级为函数内 import。

- [ ] **Step 2: 写 helper 函数(粘贴 router 现有逻辑,改成按 id map)**

在 `service.py` 里 `list_articles` 之后新增:

```python
def serialize_article_summaries(
    db: Session, articles: list["Article"]
) -> dict[int, ArticleListRead]:
    """批量把 Article 序列化成 ArticleListRead,按 id 建 map。

    published_count = 该文成功且未删的 PublishRecord 数;
    auto_review_score = 最新一条 AutoReviewDecision.score_total(仅 MCP 生文有)。
    """
    if not articles:
        return {}
    article_ids = [a.id for a in articles]
    count_rows = db.execute(
        select(PublishRecord.article_id, func.count().label("cnt"))
        .where(
            PublishRecord.article_id.in_(article_ids),
            PublishRecord.status == "succeeded",
            PublishRecord.is_deleted == False,  # noqa: E712
        )
        .group_by(PublishRecord.article_id)
    ).all()
    count_map = {row.article_id: row.cnt for row in count_rows}
    score_rows = db.execute(
        select(AutoReviewDecision.article_id, AutoReviewDecision.score_total)
        .where(AutoReviewDecision.article_id.in_(article_ids))
        .order_by(AutoReviewDecision.id.desc())
    ).all()
    score_map: dict[int, int | None] = {}
    for aid, score in score_rows:
        score_map.setdefault(aid, score)
    return {
        a.id: ArticleListRead(
            id=a.id,
            title=a.title,
            author=a.author,
            cover_asset_id=a.cover_asset_id,
            word_count=a.word_count,
            status=a.status,
            version=a.version,
            review_status=a.review_status,
            published_count=count_map.get(a.id, 0),
            source_agent_name=a.source_agent_name,
            source_template_name=a.source_template_name,
            source_template_id=a.source_template_id,
            auto_review_score=score_map.get(a.id),
            created_at=a.created_at,
            updated_at=a.updated_at,
        )
        for a in articles
    }
```

- [ ] **Step 3: 改 `read_articles` router 用 helper(保序)**

把 `router.py:167-207`(从 `article_ids = ...` 到 `for a in articles ]`)整段替换为:

```python
    summaries = serialize_article_summaries(db, articles)
    return [summaries[a.id] for a in articles]
```

并在 router 顶部 import 里加 `serialize_article_summaries`(与其它 service 函数同处 import)。若 `select`/`func`/`PublishRecord`/`AutoReviewDecision` 仅被这段用到,ruff 会报未用 import——删掉它们。

- [ ] **Step 4: 跑 lint + 既有列表测试验证零回归**

Run:
```bash
ruff check server/app/modules/articles/ && ruff format server/app/modules/articles/
GEO_TEST_DATABASE_URL=mysql+pymysql://geo_user:password@127.0.0.1:3306/geo_test \
  pytest server/tests/test_articles_api.py -q
```
Expected: ruff 通过;文章列表相关测试全 PASS(响应字段 `published_count`/`auto_review_score` 不变)。

- [ ] **Step 5: Commit**

```bash
git add server/app/modules/articles/service.py server/app/modules/articles/router.py
git commit -m "refactor(articles): 抽出 serialize_article_summaries 复用 helper

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: feed 响应 schemas

**Files:**
- Modify: `server/app/modules/articles/schemas.py`(在 `ArticleGroupRead` 之后新增)

**Interfaces:**
- Produces:
  - `ArticleGroupReadWithMembers(ArticleGroupRead)` + `members: list[ArticleListRead]`
  - `FeedCounts` `{pending: int, approved: int}`
  - `ArticleFeedItem` `{kind: str, article: ArticleListRead | None, group: ArticleGroupReadWithMembers | None}`
  - `ArticleFeedResponse` `{items: list[ArticleFeedItem], counts: FeedCounts}`

- [ ] **Step 1: 新增 schema 类**

在 `schemas.py` 里 `ArticleGroupRead`(约 `:185-193`)之后追加:

```python
class ArticleGroupReadWithMembers(ArticleGroupRead):
    """feed 用:分组 + 内嵌组员摘要(按 sort_order 排),使展开/分发不依赖全量文章。"""

    members: list[ArticleListRead] = Field(default_factory=list)


class FeedCounts(BaseModel):
    pending: int = 0
    approved: int = 0


class ArticleFeedItem(BaseModel):
    kind: str  # "article" | "group"
    article: ArticleListRead | None = None
    group: ArticleGroupReadWithMembers | None = None


class ArticleFeedResponse(BaseModel):
    items: list[ArticleFeedItem]
    counts: FeedCounts
```

- [ ] **Step 2: lint + import 冒烟**

Run:
```bash
ruff check server/app/modules/articles/schemas.py
python -c "from server.app.modules.articles.schemas import ArticleFeedResponse, ArticleFeedItem, ArticleGroupReadWithMembers, FeedCounts; print('ok')"
```
Expected: 输出 `ok`,ruff 无错。

- [ ] **Step 3: Commit**

```bash
git add server/app/modules/articles/schemas.py
git commit -m "feat(articles): feed 响应 schema(混排项 + 内嵌组员 + 计数)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `list_article_feed()` service —— 合并分页 + 计数(核心)

**Files:**
- Modify: `server/app/modules/articles/service.py`(新增函数)
- Test: `server/tests/test_articles_feed.py`(新增)

**Interfaces:**
- Consumes: `serialize_article_summaries`(Task 1);`ArticleFeedResponse`/`ArticleFeedItem`/`ArticleGroupReadWithMembers`/`FeedCounts`/`ReviewSummary`(Task 2 + 既有)。
- Produces: `list_article_feed(db, *, review_status: str, query: str | None = None, skip: int = 0, limit: int = 10, user_id: int | None = None) -> ArticleFeedResponse`。`review_status ∈ {"pending","approved"}`;`user_id=None` 表示 admin(不按用户过滤)。

- [ ] **Step 1: 写失败测试(建两篇散文 + 一个分组 + 混排顺序)**

新建 `server/tests/test_articles_feed.py`:

```python
import pytest

from server.app.modules.articles.service import (
    create_article,
    create_group,
    list_article_feed,
    replace_group_items,
)
from server.app.modules.articles.schemas import (
    ArticleCreate,
    ArticleGroupCreate,
    ArticleGroupItemInput,
    ArticleGroupItemsUpdate,
)

pytestmark = pytest.mark.mysql


def _mk_article(db, user_id, title, review_status="pending"):
    art = create_article(db, user_id, ArticleCreate(title=title, plain_text=title))
    art.review_status = review_status
    db.flush()
    return art


def test_feed_merges_articles_and_groups_by_created_at_desc(build_test_app_session):
    # build_test_app_session: 见 Step 1b —— 提供 (db, admin_user_id)
    db, uid = build_test_app_session
    a1 = _mk_article(db, uid, "文章一", "pending")
    grp = create_group(db, uid, ArticleGroupCreate(name="分组甲"))
    a2 = _mk_article(db, uid, "组员", "pending")
    replace_group_items(
        db, grp, ArticleGroupItemsUpdate(items=[ArticleGroupItemInput(article_id=a2.id)])
    )
    a3 = _mk_article(db, uid, "文章三", "pending")
    db.commit()

    resp = list_article_feed(db, review_status="pending", skip=0, limit=10, user_id=None)

    kinds = [(it.kind, it.article.id if it.kind == "article" else it.group.id) for it in resp.items]
    # 期望:a3(最新散文) → 分组甲 → a1;组员 a2 不作为散文出现(它在分组里)
    assert ("article", a3.id) in kinds
    assert ("group", grp.id) in kinds
    assert ("article", a1.id) in kinds
    assert ("article", a2.id) not in kinds
    # 混排按 created_at 倒序:a3 在 a1 前
    ids_in_order = [k[1] for k in kinds]
    assert ids_in_order.index(a3.id) < ids_in_order.index(a1.id)
    # 分组内嵌组员
    grp_item = next(it for it in resp.items if it.kind == "group")
    assert [m.id for m in grp_item.group.members] == [a2.id]
    # 计数:3 篇 pending 内容项(a1 散文 + 分组甲 + a3 散文),approved=0
    assert resp.counts.pending == 3
    assert resp.counts.approved == 0
```

> **Step 1b —— fixture:** 若 `conftest.py` 没有现成的 `build_test_app_session`,在 `test_articles_feed.py` 顶部本地写一个,复用 `build_test_app`:
> ```python
> @pytest.fixture
> def build_test_app_session(monkeypatch):
>     from server.tests.conftest import build_test_app  # 或既有工厂位置
>     app_ctx = build_test_app(monkeypatch)
>     db = app_ctx.session_factory()
>     try:
>         yield db, None  # user_id=None 走 admin 全量;需按用户时用 app_ctx.admin_user_id
>     finally:
>         db.close()
>         app_ctx.cleanup()
> ```
> 落地时先 `grep -n "def build_test_app" server/tests/conftest.py` 确认真实工厂签名与返回属性名(`session_factory` / `admin_user_id` 等),对齐即可。

- [ ] **Step 2: 跑测试确认失败**

Run:
```bash
GEO_TEST_DATABASE_URL=mysql+pymysql://geo_user:password@127.0.0.1:3306/geo_test \
  pytest server/tests/test_articles_feed.py -q
```
Expected: FAIL —— `ImportError: cannot import name 'list_article_feed'`。

- [ ] **Step 3: 实现 `list_article_feed`**

在 `service.py` 新增(紧跟 `serialize_article_summaries` 之后)。需要的 import:`from sqlalchemy import and_, exists, literal, or_, select, union_all`(缺什么补什么)+ `from server.app.modules.articles.models import Article, ArticleGroup, ArticleGroupItem`(多数已在)。

```python
def _feed_article_branch(review_status, user_id, like):
    """散篇文章分支的 WHERE 片段生成器,返回 (kind, id, created_at) 的 select。"""
    grouped_ids = (
        select(ArticleGroupItem.article_id)
        .join(ArticleGroup, ArticleGroup.id == ArticleGroupItem.group_id)
        .where(ArticleGroup.is_deleted == False)  # noqa: E712
    )
    stmt = select(
        literal("article").label("kind"),
        Article.id.label("entity_id"),
        Article.created_at.label("sort_time"),
    ).where(
        Article.is_deleted == False,  # noqa: E712
        Article.review_status == review_status,
        Article.id.notin_(grouped_ids),
    )
    if user_id is not None:
        stmt = stmt.where(Article.user_id == user_id)
    if like is not None:
        stmt = stmt.where(
            (Article.title.like(like))
            | (Article.author.like(like))
            | (Article.plain_text.like(like))
        )
    return stmt


def _feed_group_match(review_status):
    """分组是否纳入当前 tab 的 correlated 条件(关联 ArticleGroup.id)。"""
    member = (
        select(1)
        .select_from(ArticleGroupItem)
        .join(
            Article,
            and_(Article.id == ArticleGroupItem.article_id, Article.is_deleted == False),  # noqa: E712
        )
        .where(ArticleGroupItem.group_id == ArticleGroup.id)
    )
    if review_status == "approved":
        return exists(member.where(Article.review_status == "approved"))
    # pending:无未删成员(空组) 或 有非 approved 成员
    has_any = exists(member)
    has_pending = exists(member.where(Article.review_status != "approved"))
    return or_(~has_any, has_pending)


def _feed_group_branch(review_status, user_id, like):
    stmt = select(
        literal("group").label("kind"),
        ArticleGroup.id.label("entity_id"),
        ArticleGroup.created_at.label("sort_time"),
    ).where(
        ArticleGroup.is_deleted == False,  # noqa: E712
        _feed_group_match(review_status),
    )
    if user_id is not None:
        stmt = stmt.where(ArticleGroup.user_id == user_id)
    if like is not None:
        stmt = stmt.where(ArticleGroup.name.like(like))
    return stmt


def _feed_counts(db, user_id, like):
    """两 tab 各自 (散篇 + 分组) 合计。"""
    counts = {}
    for status in ("pending", "approved"):
        art_n = db.execute(
            select(func.count()).select_from(_feed_article_branch(status, user_id, like).subquery())
        ).scalar_one()
        grp_n = db.execute(
            select(func.count()).select_from(_feed_group_branch(status, user_id, like).subquery())
        ).scalar_one()
        counts[status] = int(art_n) + int(grp_n)
    return counts


def list_article_feed(
    db: Session,
    *,
    review_status: str,
    query: str | None = None,
    skip: int = 0,
    limit: int = 10,
    user_id: int | None = None,
) -> ArticleFeedResponse:
    if review_status not in ("pending", "approved"):
        raise ClientError(f"Invalid review_status: {review_status}")
    like = f"%{query}%" if query else None

    merged = union_all(
        _feed_article_branch(review_status, user_id, like),
        _feed_group_branch(review_status, user_id, like),
    ).subquery()
    page_rows = db.execute(
        select(merged.c.kind, merged.c.entity_id)
        .order_by(merged.c.sort_time.desc())
        .offset(skip)
        .limit(limit)
    ).all()

    article_ids = [r.entity_id for r in page_rows if r.kind == "article"]
    group_ids = [r.entity_id for r in page_rows if r.kind == "group"]

    # hydrate 散篇文章
    art_objs = (
        list(
            db.execute(
                select(Article)
                .options(*_list_summary_load_options())
                .where(Article.id.in_(article_ids))
            ).scalars().all()
        )
        if article_ids
        else []
    )

    # hydrate 分组 + 组员
    grp_objs = (
        list(db.execute(select(ArticleGroup).where(ArticleGroup.id.in_(group_ids))).scalars().all())
        if group_ids
        else []
    )
    grp_by_id = {g.id: g for g in grp_objs}
    # 组员 article_id(按 sort_order),批量拉成员文章
    member_order: dict[int, list[int]] = {}
    all_member_ids: set[int] = set()
    for g in grp_objs:
        ordered = [it.article_id for it in sorted(g.items, key=lambda i: i.sort_order)]
        member_order[g.id] = ordered
        all_member_ids.update(ordered)
    member_objs = (
        list(
            db.execute(
                select(Article)
                .options(*_list_summary_load_options())
                .where(Article.id.in_(all_member_ids), Article.is_deleted == False)  # noqa: E712
            ).scalars().all()
        )
        if all_member_ids
        else []
    )

    # 一次性序列化所有出现过的文章(散篇 + 组员)
    summaries = serialize_article_summaries(db, art_objs + member_objs)

    items: list[ArticleFeedItem] = []
    for r in page_rows:
        if r.kind == "article":
            summary = summaries.get(r.entity_id)
            if summary is not None:
                items.append(ArticleFeedItem(kind="article", article=summary))
        else:
            g = grp_by_id.get(r.entity_id)
            if g is None:
                continue
            ordered_ids = member_order.get(g.id, [])
            members = [summaries[mid] for mid in ordered_ids if mid in summaries]
            approved = sum(1 for m in members if m.review_status == "approved")
            group_read = ArticleGroupReadWithMembers(
                id=g.id,
                name=g.name,
                description=g.description,
                version=g.version,
                items=[
                    ArticleGroupItemRead(article_id=it.article_id, sort_order=it.sort_order)
                    for it in sorted(g.items, key=lambda i: i.sort_order)
                ],
                review_summary=ReviewSummary(total=len(members), approved=approved),
                created_at=g.created_at,
                updated_at=g.updated_at,
                members=members,
            )
            items.append(ArticleFeedItem(kind="group", group=group_read))

    counts = _feed_counts(db, user_id, like)
    return ArticleFeedResponse(
        items=items, counts=FeedCounts(pending=counts["pending"], approved=counts["approved"])
    )
```

确保 `service.py` 顶部 import 了:`ClientError`(`from server.app.shared.errors import ClientError`,多半已在)、`ArticleFeedResponse`/`ArticleFeedItem`/`ArticleGroupReadWithMembers`/`FeedCounts`/`ArticleGroupItemRead`/`ReviewSummary`(从 `.schemas`)。

- [ ] **Step 4: 跑测试确认通过**

Run:
```bash
GEO_TEST_DATABASE_URL=mysql+pymysql://geo_user:password@127.0.0.1:3306/geo_test \
  pytest server/tests/test_articles_feed.py -q
```
Expected: PASS。

- [ ] **Step 5: 补边界测试(分页 / tab 过滤 / 空组 / 搜索)**

在 `test_articles_feed.py` 追加:

```python
def test_feed_pagination_and_limit(build_test_app_session):
    db, uid = build_test_app_session
    made = [_mk_article(db, uid, f"文章{i}", "pending") for i in range(5)]
    db.commit()
    p1 = list_article_feed(db, review_status="pending", skip=0, limit=2, user_id=None)
    p2 = list_article_feed(db, review_status="pending", skip=2, limit=2, user_id=None)
    assert len(p1.items) == 2 and len(p2.items) == 2
    ids1 = {it.article.id for it in p1.items}
    ids2 = {it.article.id for it in p2.items}
    assert ids1.isdisjoint(ids2)  # 页间不重叠
    assert p1.counts.pending == 5


def test_feed_tab_filter_and_empty_group_is_pending(build_test_app_session):
    db, uid = build_test_app_session
    approved_art = _mk_article(db, uid, "已审", "approved")
    empty_grp = create_group(db, uid, ArticleGroupCreate(name="空组"))
    db.commit()
    pending = list_article_feed(db, review_status="pending", user_id=None)
    approved = list_article_feed(db, review_status="approved", user_id=None)
    # 空组(total=0)归 pending
    assert ("group", empty_grp.id) in [
        (it.kind, it.group.id) for it in pending.items if it.kind == "group"
    ]
    # 已审文章只在 approved tab
    assert approved_art.id in [it.article.id for it in approved.items if it.kind == "article"]
    assert approved_art.id not in [it.article.id for it in pending.items if it.kind == "article"]


def test_feed_search_like_matches_title(build_test_app_session):
    db, uid = build_test_app_session
    hit = _mk_article(db, uid, "关键词命中", "pending")
    _mk_article(db, uid, "无关内容", "pending")
    db.commit()
    resp = list_article_feed(db, review_status="pending", query="关键词", user_id=None)
    got = [it.article.id for it in resp.items if it.kind == "article"]
    assert hit.id in got
    assert len(got) == 1
```

Run 同 Step 4 命令。Expected: 全 PASS。

- [ ] **Step 6: lint + commit**

```bash
ruff check server/app/modules/articles/service.py && ruff format server/app/modules/articles/service.py server/tests/test_articles_feed.py
git add server/app/modules/articles/service.py server/tests/test_articles_feed.py
git commit -m "feat(articles): list_article_feed 合并分页 service + 测试

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: `GET /api/articles/feed` 路由端点

**Files:**
- Modify: `server/app/modules/articles/router.py`(在 `read_articles` 之后新增)
- Test: `server/tests/test_articles_feed.py`(追加 API 级测试)

**Interfaces:**
- Consumes: `list_article_feed`(Task 3)、`ArticleFeedResponse`(Task 2)。
- Produces: `GET /api/articles/feed?review_status=&q=&skip=&limit=` → `ArticleFeedResponse`;鉴权 `get_current_user`;非 admin 传 `user_id=current_user.id`。

- [ ] **Step 1: 写失败的 API 测试**

在 `test_articles_feed.py` 追加(用既有 `build_test_app` 的 client fixture,对齐 `test_articles_api.py` 的用法):

```python
def test_feed_endpoint_returns_items_and_counts(client_and_ids):
    # client_and_ids: 复用 test_articles_api.py 里的 client 构造方式(TestClient + JWT cookie)
    client = client_and_ids
    resp = client.get("/api/articles/feed", params={"review_status": "pending", "limit": 10})
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body and "counts" in body
    assert set(body["counts"].keys()) == {"pending", "approved"}


def test_feed_endpoint_rejects_bad_review_status(client_and_ids):
    resp = client_and_ids.get("/api/articles/feed", params={"review_status": "bogus"})
    assert resp.status_code == 400
```

> 落地前 `grep -n "TestClient\|build_test_app\|def client" server/tests/test_articles_api.py`,照抄它的 client fixture 写法(cookie/JWT 注入),命名对齐即可。

- [ ] **Step 2: 跑测试确认失败**

Run:
```bash
GEO_TEST_DATABASE_URL=mysql+pymysql://geo_user:password@127.0.0.1:3306/geo_test \
  pytest server/tests/test_articles_feed.py -q -k endpoint
```
Expected: FAIL —— 404(路由未定义)。

- [ ] **Step 3: 新增路由端点**

在 `router.py` 的 `read_articles`(`:207` 之后)新增。import 里加 `list_article_feed`、`ArticleFeedResponse`:

```python
@articles_router.get("/feed", response_model=ArticleFeedResponse)
def read_article_feed(
    q: str | None = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=10, ge=1, le=100),
    review_status: str = Query(default="pending"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ArticleFeedResponse:
    if review_status not in VALID_REVIEW_STATUSES:
        raise ClientError(f"Invalid review_status: {review_status}")
    return list_article_feed(
        db,
        review_status=review_status,
        query=q,
        skip=skip,
        limit=limit,
        user_id=None if current_user.role == "admin" else current_user.id,
    )
```

> **路由顺序**:`/feed` 是静态段,`read_articles` 挂 `""`、`get_article` 多半挂 `"/{article_id}"`。FastAPI 里静态 `/feed` 必须在 `/{article_id:int}` **之前**注册,否则 `feed` 会被当成 article_id。放在 `read_articles` 正下方即满足(它在 `/{article_id}` 之上)。落地后 `grep -n '@articles_router.get' router.py` 确认 `/feed` 在 `/{article_id}` 之前。

- [ ] **Step 4: 跑测试确认通过 + 全量后端测试**

Run:
```bash
GEO_TEST_DATABASE_URL=mysql+pymysql://geo_user:password@127.0.0.1:3306/geo_test \
  pytest server/tests/test_articles_feed.py -q
GEO_TEST_DATABASE_URL=mysql+pymysql://geo_user:password@127.0.0.1:3306/geo_test \
  pytest server/tests/test_articles_api.py -q
```
Expected: 全 PASS(feed 新测试 + 既有文章测试无回归)。

- [ ] **Step 5: lint + commit**

```bash
ruff check server/app/modules/articles/router.py && ruff format server/app/modules/articles/router.py
git add server/app/modules/articles/router.py server/tests/test_articles_feed.py
git commit -m "feat(articles): GET /api/articles/feed 合并分页端点

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: 前端类型 + `listArticleFeed` API 客户端

**Files:**
- Modify: `web/src/types.ts`(新增类型)
- Modify: `web/src/api/articles.ts`(新增函数)

**Interfaces:**
- Consumes: 既有 `ArticleSummary`、`ArticleGroup`。
- Produces:
  - 类型 `ArticleGroupWithMembers`(`ArticleGroup & { members: ArticleSummary[] }`)、`ArticleFeedItem`、`ArticleFeedResponse`。
  - `listArticleFeed(params: { review_status: "pending" | "approved"; q?: string; skip: number; limit: number }): Promise<ArticleFeedResponse>`。

- [ ] **Step 1: 在 types.ts 新增类型**

先 `grep -n "ArticleGroup\b\|ArticleSummary" web/src/types.ts` 定位既有定义,在其后追加:

```typescript
export interface ArticleGroupWithMembers extends ArticleGroup {
  members: ArticleSummary[];
}

export interface ArticleFeedItem {
  kind: "article" | "group";
  article: ArticleSummary | null;
  group: ArticleGroupWithMembers | null;
}

export interface ArticleFeedResponse {
  items: ArticleFeedItem[];
  counts: { pending: number; approved: number };
}
```

> 若既有 `ArticleGroup` 不含 `review_summary`/`items` 的完整字段,`extends` 仍成立(后端返回是超集)。确认 `ArticleGroup` 里 `items`/`review_summary` 字段名与后端一致。

- [ ] **Step 2: 在 api/articles.ts 新增客户端函数**

在 `listArticles` 附近追加,并在顶部 import 补 `ArticleFeedResponse`:

```typescript
export function listArticleFeed(params: {
  review_status: "pending" | "approved";
  q?: string;
  skip: number;
  limit: number;
}): Promise<ArticleFeedResponse> {
  const sp = new URLSearchParams({
    review_status: params.review_status,
    skip: String(params.skip),
    limit: String(params.limit),
  });
  if (params.q) sp.set("q", params.q);
  return api<ArticleFeedResponse>(`/api/articles/feed?${sp.toString()}`);
}
```

- [ ] **Step 3: typecheck**

Run:
```bash
pnpm --filter @geo/web typecheck
```
Expected: 通过(0 error)。

- [ ] **Step 4: Commit**

```bash
git add web/src/types.ts web/src/api/articles.ts
git commit -m "feat(web): listArticleFeed API 客户端 + feed 类型

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: ContentWorkspace 改用 feed(前端主改动)

把全量 `for` 循环换成一页一查,主列表直接渲染服务端混排页,计数/分页/`articleById` 全部改由 feed 驱动。

**Files:**
- Modify: `web/src/features/content/ContentWorkspace.tsx`

**Interfaces:**
- Consumes: `listArticleFeed`、`ArticleFeedItem`、`ArticleFeedResponse`、`ArticleGroupWithMembers`(Task 5);既有 `listArticleGroups`(保留给分组选择器)。

- [ ] **Step 1: 新增 feed 状态,改造 `refreshArticles`**

在组件状态区(约 `:332-340`)新增:

```typescript
const [feedItems, setFeedItems] = useState<ArticleFeedItem[]>([]);
const [feedCounts, setFeedCounts] = useState<{ pending: number; approved: number }>({ pending: 0, approved: 0 });
```

把 `refreshArticles`(`:535-550`)整体替换为一页一查(入参用当前 `reviewTab`/`query`/`articlePage`):

```typescript
async function refreshArticles(
  nextQuery = query,
  nextPage = articlePage,
  nextTab: ReviewStatus = reviewTab,
) {
  try {
    const resp = await listArticleFeed({
      review_status: nextTab,
      q: nextQuery || undefined,
      skip: nextPage * LIST_PAGE_SIZE,
      limit: LIST_PAGE_SIZE,
    });
    setFeedItems(resp.items);
    setFeedCounts(resp.counts);
    setArticlePage(nextPage);
  } catch {
    toast("加载文章列表失败", "error");
  }
}
```

> 顶部 import 补:`import { listArticleFeed } from "../../api/articles";`(或既有聚合 import 处)、类型 `ArticleFeedItem`、`ArticleGroupWithMembers` from `"../../types"`。保留 `listArticleGroups` / `refreshGroups`(分组选择器仍需全量 groups)。

- [ ] **Step 2: 主列表数据源改为 feed 项**

`unifiedList` 现在把 `articles`+`groups` 合并/排序/切片(`:509-527`)。改成直接用服务端返回项。把 `UnifiedListItem`(`:314-316`)与其 `useMemo`(`:509-524`)替换为从 `feedItems` 派生:

```typescript
type UnifiedListItem =
  | { type: "article"; article: ArticleSummary }
  | { type: "group"; group: ArticleGroupWithMembers };

const unifiedList: UnifiedListItem[] = useMemo(
  () =>
    feedItems.map((it) =>
      it.kind === "article"
        ? { type: "article" as const, article: it.article as ArticleSummary }
        : { type: "group" as const, group: it.group as ArticleGroupWithMembers },
    ),
  [feedItems],
);
```

`pagedUnifiedList`(`:527`)删掉切片,直接等于 `unifiedList`(服务端已切页):

```typescript
const pagedUnifiedList = unifiedList;
const totalArticlePages = Math.max(1, Math.ceil(feedCounts[reviewTab] / LIST_PAGE_SIZE));
```

> 渲染处若之前用 `item.sortTime` 或 `article`/`group` 以外的字段,同步去掉。`item.group` 现在是 `ArticleGroupWithMembers`(带 `members`)。

- [ ] **Step 2b: `articleById` 改由本页(散篇 + 组员)组装**

替换 `articleById`(`:469`):

```typescript
const articleById = useMemo(() => {
  const map: Record<number, ArticleSummary> = {};
  for (const it of feedItems) {
    if (it.kind === "article" && it.article) map[it.article.id] = it.article;
    if (it.kind === "group" && it.group) for (const m of it.group.members) map[m.id] = m;
  }
  return map;
}, [feedItems]);
```

`groupArticleSummaries`(`:1025`)仍用 `articleById` 反查——现在组员已在 `articleById` 里,无需改。`groupedArticleIdSet`(`:463-467`)不再用于主列表过滤(服务端已排除);若仅被 `unifiedList` 老逻辑引用,删掉;若他处仍用则保留。落地 `grep -n groupedArticleIdSet` 确认引用点。

- [ ] **Step 2c: 角标计数改用服务端 counts**

替换 `reviewCounts`(`:494-507`):

```typescript
const reviewCounts = feedCounts;
```

删除随之无用的 `groupReviewCounts` / `groupHasStatus`(`:471-491`)**若**它们只被 `reviewCounts`/老 `unifiedList` 使用。`grep -n "groupReviewCounts\|groupHasStatus"` 确认;若分组行渲染仍显示"已审 X/Y",改用 `group.review_summary`(feed 已带)。

- [ ] **Step 3: 切 tab / 搜索 / 翻页触发重取**

- 翻页按钮 `setArticlePage(n)` 的地方,改成 `void refreshArticles(query, n, reviewTab)`。
- 搜索输入变化(`setQuery`)后,改成重置到第 0 页并重取:`void refreshArticles(nextQuery, 0, reviewTab)`。`grep -n "setQuery\|setArticlePage" ContentWorkspace.tsx` 找到全部触发点逐一改。
- 切 review tab(`setReviewTab` / `onReviewTabChange`)后:`void refreshArticles(query, 0, nextTab)`。
- 现有 focus/visibility 自动刷新 `manualRefresh` 保留——它内部调 `refreshArticles()`(默认参=当前 tab/query/page),自然重取当前页。

- [ ] **Step 4: 各写操作后的刷新沿用 `manualRefresh` / `refreshArticles`**

审核、删除、加入分组、approve-all 等成功后现在多半调 `manualRefresh()` 或 `refreshArticles()+refreshGroups()`。保持不变即可(现在重取的是当前 feed 页)。**删到本页空**的兜底:`refreshArticles` 后若 `feedItems` 空且 `articlePage>0`,回退一页——在 `refreshArticles` 末尾加:

```typescript
    if (resp.items.length === 0 && nextPage > 0) {
      const lastPage = Math.max(0, Math.ceil(resp.counts[nextTab] / LIST_PAGE_SIZE) - 1);
      if (lastPage < nextPage) return refreshArticles(nextQuery, lastPage, nextTab);
    }
```
(放在 `setArticlePage(nextPage)` 之前。)

- [ ] **Step 5: typecheck + build**

Run:
```bash
pnpm --filter @geo/web typecheck && pnpm --filter @geo/web build
```
Expected: 均通过。逐一消灭因删除 `sortTime`/`groupHasStatus`/老 `articleById` 依赖引出的 TS 报错。

- [ ] **Step 6: 手动验证(启动本地全栈)**

Run(两个终端):
```bash
uvicorn server.app.main:app --reload --host 127.0.0.1 --port 8000
pnpm --filter @geo/web dev   # 5173
```
逐项确认:
1. 打开内容管理 → Network 里只有 **1 个** `articles/feed?...skip=0` 请求(不再是 8 个 `articles?skip=`)。
2. 翻页 → 每翻一页发一个 `feed?...skip=N` 请求,列表更新。
3. 待审/已审切换 → 角标数正确、列表刷新、回到第 1 页。
4. 搜索关键词 → 列表按词过滤、角标随之变、回到第 1 页。
5. 分组:出现在列表、可展开看到组员、可分发(组员数据来自内嵌 members)。
6. 删除当前页最后一篇 → 页码正确回退,不卡空页。

- [ ] **Step 7: Commit**

```bash
git add web/src/features/content/ContentWorkspace.tsx
git commit -m "feat(web): 内容列表改用 feed 服务端分页(一页一查)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review 结论(plan 作者已核对 spec)

- **spec ① feed 接口** → Task 2(schema)+ Task 4(端点)。✔
- **spec ② 后端查询(混排/排除 grouped/分组 tab 纳入/计数/per-user)** → Task 3。✔
- **spec ③ 前端改动(refreshArticles/unifiedList/counts/articleById/groups 仅留选择器)** → Task 6。✔
- **spec ④ 边界(删空回退/切 tab 重置/老接口不动)** → Task 6 Step 4 / Step 3;老 `/api/articles` 全程未改。✔
- **spec ⑤ 测试** → 后端 Task 3/4 的 `test_articles_feed.py`;前端 typecheck+build+手动 Task 6。✔
- **类型一致性**:`ArticleFeedResponse`/`ArticleFeedItem`/`ArticleGroupReadWithMembers`(后端)对应 `ArticleFeedResponse`/`ArticleFeedItem`/`ArticleGroupWithMembers`(前端);`serialize_article_summaries` 在 Task 1 定义、Task 3 消费,签名一致。✔
- **已知落地待确认点**(非占位符,是必须现场对齐的真实事实):测试 fixture 真实名(`build_test_app` 返回属性)、`types.ts` 里 `ArticleGroup` 字段名、`ContentWorkspace` 各 `setQuery/setArticlePage/setReviewTab` 触发点、路由 `/feed` vs `/{article_id}` 注册顺序——每处都给了 `grep` 定位指令。
