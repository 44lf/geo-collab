# 对抗评审质量门（同步版）实现计划 · v2

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.
> **v2 说明**：经两轮 Codex 对抗审核 + 用户 3 项决策后重写。所有修正已折进各 Task 正文（不再用前置覆盖块）。设计稿见 `docs/superpowers/specs/2026-07-13-adversarial-review-quality-gate-design.md` §十三。

**Goal:** /goal loop 内同步给每篇文章加一道对抗判分（verifier 读同类高质量参考、判一个 advisory 分附到文章上）+ 一个自包含高质量参考库（CRUD + 只读 Tiptap）。

**Architecture:** host（Claude 子代理）在 skill 里判分（零配置）。服务端提供：自包含快照表 `quality_reference` + CRUD + 2 个 MCP 工具（GET 取参考 / POST 记分）+ 文章 3 个 nullable 列。**不动 `review_status`、无异步机器。**

**Tech Stack:** FastAPI + SQLAlchemy/Alembic（MySQL only，ngram FULLTEXT）、pydantic-settings、FastMCP、React 19 + Vite + TS（fetch，不是 axios）+ Tiptap。

## Global Constraints

- **MySQL only**；测试需 `GEO_TEST_DATABASE_URL`（库名含 `test`），DB 用例标 `@pytest.mark.mysql`。后端命令用 `env` 全路径 python（conda activate 在工具 shell 不生效）。
- service 层抛命名异常（`ClientError`/`ConflictError`/`ValidationError`），**不抛裸 `ValueError`**。
- MCP 端点走 `Depends(require_mcp_token)`；`except Exception` 一律 `raise mcp_exception_response(exc, context=...)`；**DB 查询也要在 try 内**。
- 正文三份（`content_json`/`content_html`/`plain_text`）：`content_json` 存**序列化字符串**（`dumps_content_json`，`markdown_to_tiptap` 返回 dict）；外部 `content_html` 必过 `nh3.clean`。
- **不改 `articles.review_status`**（CHECK / `__table_args__` / feed 两 tab / `_validate_articles_approved` 全不碰）。
- **只改 /goal 的 save 路径**；scheme_executor / pipeline 生文节点不碰。
- 前端**无单测框架**：`pnpm --filter @geo/web typecheck` + `build` 是门禁；`api` 是 `api<T>(path, RequestInit)` fetch（`web/src/api/core.ts:23`），**不是 axios**；页面走 `web/src/routes.tsx` lazy route（无 visitedTabs）。worktree 里 Bash cwd 会漂，前端命令用 `pnpm -C web` 或绝对路径。
- 测试脚手架：`from server.tests.utils import build_test_app`；`TestApp` 暴露 `.client` / `.session_factory` / `.engine` / `.cleanup()`；**无全局 `make_*` 工厂**——每文件自带局部 `_make_article`（照 `test_article_list_score.py:19-38`）；MCP 测试先 `monkeypatch.setenv("GEO_MCP_TOKEN","secret")` 再带 `headers={"X-MCP-Token":"secret"}`（照 `test_save_article_mcp.py`）。
- 迁移 head = `0061_report_events`（全 id 格式）；`MCP_TOOLS_COUNT` 27→29。
- 用户已接受的风险（不实现治理）：库全员可写；分不做时效失效；分是粗信号。缓解只做「pick 优先 external + 返回 origin + 前端显配比」。

---

## 文件结构

**新建 `server/app/modules/quality_reference/`：** `models.py` / `schemas.py` / `service.py` / `router.py`（user JWT CRUD + 详情）/ `mcp_router.py`（MCP token：pick GET + record-score POST）。
**改后端：** `0062_adversarial_review.py`（新迁移）· `articles/models.py`（+3 列）· `articles/schemas.py`（`ArticleRead` + `ArticleListRead` 加字段）· `articles/parser.py`（`to_article_read` 序列化新字段，位置见 Task 6）· `articles/services/feed.py`（load_only + serialize）· `articles/routers/mcp.py`（save 单题快照）· `core/config.py`（2 配置）· `main.py`（mount + import model）· `mcp_catalog/connect_router.py`（27→29）· `server/mcp/tools/catalog.py`（pick）· `server/mcp/tools/action.py`（record-score）· `server/tests/utils.py`（`_model_modules` + qref ngram）· `CLAUDE.md`（工具数同步）。
**前端：** `web/src/api/qualityReference.ts`（新）· `web/src/features/quality-reference/`（新）· `web/src/routes.tsx` + `web/src/types.ts` + `web/src/App.tsx`（导航/路由）· 文章列表项组件（对抗分）。
**Skill：** `server/app/modules/loop_skills/templates/skills/geo-article-verifier/SKILL.md` + `.../geo-article-writer/SKILL.md` + 发布新 `SkillVersion`。

---

## Task 1: 迁移 + ORM + 测试库注册 + 真迁移验证

**Files:**
- Create: `server/alembic/versions/0062_adversarial_review.py`
- Modify: `server/app/modules/articles/models.py`（Article +3 列）
- Create: `server/app/modules/quality_reference/__init__.py`（空）+ `models.py`
- Modify: `server/app/main.py`（`import ...quality_reference.models`）
- Modify: `server/tests/utils.py`（`_model_modules` 加 qref + reset 补 qref 的 ngram FULLTEXT）
- Test: `server/tests/test_adversarial_migration.py`（真跑 alembic）

**Interfaces — Produces:** `Article.source_question_category:str|None`、`Article.source_question_texts:list|None`、`Article.adversarial_score:int|None`；`QualityReference` ORM（表 `quality_reference`，`origin` CHECK、`content_hash` UNIQUE、`article_id` UNIQUE + FK SET NULL）。

- [ ] **Step 1: 确认单 head** — Run: `cd e:/geo && env python -m alembic heads 2>&1 | tail -3`；Expected: 单 head `0061_report_events (head)`。多 head 则停下报告。

- [ ] **Step 2: 写失败测试（真 alembic upgrade→downgrade→upgrade + create_all 结构）**

```python
# server/tests/test_adversarial_migration.py
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from server.tests.utils import build_test_app, get_test_database_url  # get_test_database_url 见 utils（若名不同按 utils 实名）

@pytest.mark.mysql
def test_alembic_upgrade_creates_qref_and_columns():
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", get_test_database_url())
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")           # 真跑迁移链——revision id 写错会在此崩
    insp = inspect(create_engine(get_test_database_url()))
    assert "quality_reference" in insp.get_table_names()
    cols = {c["name"] for c in insp.get_columns("articles")}
    assert {"source_question_category", "source_question_texts", "adversarial_score"} <= cols
    # review_status CHECK 未变
    rs = next(c for c in insp.get_check_constraints("articles") if c["name"] == "ck_articles_review_status")
    assert "adversarial_pending" not in rs["sqltext"]
    # 幂等回滚重升
    command.downgrade(cfg, "-1"); command.upgrade(cfg, "head")

@pytest.mark.mysql
def test_qref_constraints_present(monkeypatch):
    app_ctx = build_test_app(monkeypatch)
    try:
        insp = inspect(app_ctx.engine)
        uniques = {tuple(u["column_names"]) for u in insp.get_unique_constraints("quality_reference")}
        assert ("article_id",) in uniques and ("content_hash",) in uniques
    finally:
        app_ctx.cleanup()
```

> 若 `utils.py` 没有 `get_test_database_url`，用 `os.environ["GEO_TEST_DATABASE_URL"]`（build_test_app 依赖同一个）。

- [ ] **Step 3: 跑确认失败** — Run: `cd e:/geo && GEO_TEST_DATABASE_URL=mysql+pymysql://geo_user:password@127.0.0.1:3306/geo_test env python -m pytest server/tests/test_adversarial_migration.py -q`；Expected: FAIL。

- [ ] **Step 4: Article +3 列**（`models.py` Article 类内，`source_template_id` 之后）

```python
    # 生文溯源（仅 /goal MCP save 填；scheme/pipeline 留 NULL）
    source_question_category: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    source_question_texts: Mapped[list | None] = mapped_column(JSON, nullable=True)   # phase-1 存 [单条]
    # 对抗判分（N 次平均，verifier skill 后置写）；纯 advisory，不做闸
    adversarial_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
```

- [ ] **Step 5: QualityReference ORM**（origin 走 CHECK、content_hash UNIQUE——与仓库 review_status 同款 enum-via-check 惯例）

```python
# server/app/modules/quality_reference/models.py
from datetime import datetime
from sqlalchemy import (Boolean, CheckConstraint, DateTime, ForeignKey, Integer,
                        String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column
from server.app.core.time import utcnow
from server.app.db.base import Base


class QualityReference(Base):
    __tablename__ = "quality_reference"
    __table_args__ = (
        CheckConstraint("origin in ('own','external')", name="ck_quality_reference_origin"),
        UniqueConstraint("article_id", name="uq_quality_reference_article_id"),
        UniqueConstraint("content_hash", name="uq_quality_reference_content_hash"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    origin: Mapped[str] = mapped_column(String(16), index=True)          # own | external，服务端盖章
    article_id: Mapped[int | None] = mapped_column(
        ForeignKey("articles.id", ondelete="SET NULL"), nullable=True)   # 外部=NULL；软删不触发 SET NULL（见 §13.3）
    title: Mapped[str] = mapped_column(String(300))
    content_json: Mapped[str] = mapped_column(Text, default="{}")        # 序列化字符串
    content_html: Mapped[str] = mapped_column(Text, default="")          # 外部已 nh3 清洗
    plain_text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))                # sha256(归一化 title+plain_text)
    category: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)  # 空=通用兜底
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    platform: Mapped[str | None] = mapped_column(String(100), nullable=True)
    added_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
```

- [ ] **Step 6: 迁移**（revision 全 id 格式；ngram raw SQL）

```python
# server/alembic/versions/0062_adversarial_review.py
from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

revision: str = "0062_adversarial_review"
down_revision: str | None = "0061_report_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("articles", sa.Column("source_question_category", sa.String(200), nullable=True))
    op.create_index("ix_articles_source_question_category", "articles", ["source_question_category"])
    op.add_column("articles", sa.Column("source_question_texts", sa.JSON(), nullable=True))
    op.add_column("articles", sa.Column("adversarial_score", sa.Integer(), nullable=True))

    op.create_table(
        "quality_reference",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("article_id", sa.Integer(), sa.ForeignKey("articles.id", ondelete="SET NULL"), nullable=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.Column("content_html", sa.Text(), nullable=False),
        sa.Column("plain_text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("category", sa.String(200), nullable=True),
        sa.Column("source_url", sa.String(1000), nullable=True),
        sa.Column("platform", sa.String(100), nullable=True),
        sa.Column("added_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("origin in ('own','external')", name="ck_quality_reference_origin"),
        sa.UniqueConstraint("article_id", name="uq_quality_reference_article_id"),
        sa.UniqueConstraint("content_hash", name="uq_quality_reference_content_hash"),
        mysql_engine="InnoDB", mysql_charset="utf8mb4",
    )
    for col in ("origin", "category", "is_active"):
        op.create_index(f"ix_quality_reference_{col}", "quality_reference", [col])
    op.execute("ALTER TABLE quality_reference "
               "ADD FULLTEXT INDEX ftx_quality_reference_plain (plain_text) WITH PARSER ngram")


def downgrade() -> None:
    op.drop_table("quality_reference")
    op.drop_index("ix_articles_source_question_category", table_name="articles")
    op.drop_column("articles", "adversarial_score")
    op.drop_column("articles", "source_question_texts")
    op.drop_column("articles", "source_question_category")
```

- [ ] **Step 7: 让测试库建 qref + ngram**（`server/tests/utils.py`）
  - 把 `server.app.modules.quality_reference.models` 加进 `_model_modules`（utils.py:98 那份列表），否则 `create_all` 不建 qref 表。
  - 在 `reset_test_database` 补 `ft_articles` 那段之后（utils.py:150 附近）加一条：
    ```python
    conn.exec_driver_sql("ALTER TABLE quality_reference ADD FULLTEXT INDEX "
                         "ftx_quality_reference_plain (plain_text) WITH PARSER ngram")
    ```
  - `server/app/main.py` 模型 import 区加 `import server.app.modules.quality_reference.models  # noqa: F401`。

- [ ] **Step 8: 跑确认通过** — Run: 同 Step 3；Expected: PASS（2 passed）。

- [ ] **Step 9: Commit**

```bash
git add server/alembic/versions/0062_adversarial_review.py server/app/modules/articles/models.py server/app/modules/quality_reference/ server/app/main.py server/tests/utils.py server/tests/test_adversarial_migration.py
git commit -m "feat(adversarial): 迁移+ORM+测试库注册+真迁移验证"
```

---

## Task 2: quality_reference service

**Files:** Create `server/app/modules/quality_reference/service.py` + `schemas.py`；Test `server/tests/test_quality_reference_service.py`。

**Interfaces — Produces:**
- `compute_content_hash(title, plain_text) -> str`
- `adopt_article(db, *, user_id, article_id) -> QualityReference`（仅 approved；全员可写、不校验 owner）
- `import_external(db, *, user_id, title, markdown, category, source_url, platform) -> tuple[QualityReference, list[dict]]`（返回 (ref, similar)）
- `list_references(...) -> list[QualityReference]` · `get_reference(db, ref_id) -> QualityReference` · `patch_reference(...)`
- `pick_references(db, *, category, k, truncate_chars) -> list[dict]`（每项含 `origin`）
- `find_similar(db, *, plain_text, limit) -> list[dict]`

- [ ] **Step 1: 写失败测试**

```python
# server/tests/test_quality_reference_service.py
import pytest
from server.tests.utils import build_test_app
from server.app.modules.quality_reference import service as svc
from server.app.modules.quality_reference.models import QualityReference
from server.app.modules.articles.models import Article

def _make_article(db, *, review_status="approved", plain="正文正文正文", title="T", cat=None):
    a = Article(user_id=1, title=title, content_json="{}", content_html="",
                plain_text=plain, word_count=3, status="draft", review_status=review_status,
                source_question_category=cat)
    db.add(a); db.flush(); return a

def test_hash_normalizes():
    assert svc.compute_content_hash("标题", "正 文  内容") == svc.compute_content_hash("标题", "正 文 内容")

@pytest.mark.mysql
def test_adopt_rejects_non_approved(monkeypatch):
    app_ctx = build_test_app(monkeypatch); db = app_ctx.session_factory()
    try:
        a = _make_article(db, review_status="pending"); db.commit()
        from server.app.shared.errors import ValidationError
        with pytest.raises(ValidationError):
            svc.adopt_article(db, user_id=1, article_id=a.id)
    finally:
        db.close(); app_ctx.cleanup()

@pytest.mark.mysql
def test_import_sanitizes_html_and_is_idempotent(monkeypatch):
    app_ctx = build_test_app(monkeypatch); db = app_ctx.session_factory()
    try:
        ref, _ = svc.import_external(db, user_id=1, title="T",
            markdown="正文<script>alert(1)</script>内容", category="通用", source_url=None, platform=None)
        db.commit()
        assert "<script>" not in ref.content_html          # nh3 清洗
        ref2, _ = svc.import_external(db, user_id=1, title="T",
            markdown="正文<script>alert(1)</script>内容", category="通用", source_url=None, platform=None)
        db.commit()
        assert ref2.id == ref.id and db.query(QualityReference).count() == 1   # 幂等
    finally:
        db.close(); app_ctx.cleanup()

@pytest.mark.mysql
def test_pick_prefers_external_then_universal(monkeypatch):
    app_ctx = build_test_app(monkeypatch); db = app_ctx.session_factory()
    try:
        # 同类目下 own + external，应优先 external
        a = _make_article(db); db.commit()
        svc.adopt_article(db, user_id=1, article_id=a.id)      # own，category=None（文章无 source_question_category）
        svc.import_external(db, user_id=1, title="X", markdown="乙"*50, category="餐厅", source_url=None, platform=None)
        db.commit()
        hit = svc.pick_references(db, category="餐厅", k=3, truncate_chars=10)
        assert hit and hit[0]["origin"] == "external" and len(hit[0]["plain_text"]) <= 10
    finally:
        db.close(); app_ctx.cleanup()
```

- [ ] **Step 2: 跑确认失败** — Run: `cd e:/geo && GEO_TEST_DATABASE_URL=... env python -m pytest server/tests/test_quality_reference_service.py -q`；Expected: FAIL。

- [ ] **Step 3: schemas**

```python
# server/app/modules/quality_reference/schemas.py
from datetime import datetime
from pydantic import BaseModel, Field

class AdoptRequest(BaseModel):
    article_id: int
    category: str | None = Field(default=None, max_length=200)   # 文章无 source_question_category 时前端补选

class ImportRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    markdown: str = Field(min_length=1)
    category: str | None = Field(default=None, max_length=200)
    source_url: str | None = Field(default=None, max_length=1000)
    platform: str | None = Field(default=None, max_length=100)

class PatchRequest(BaseModel):
    is_active: bool | None = None
    category: str | None = None

class QualityReferenceRead(BaseModel):        # 列表用，轻量，不含正文
    id: int; origin: str; article_id: int | None; title: str
    category: str | None; source_url: str | None; platform: str | None
    is_active: bool; created_at: datetime
    class Config: from_attributes = True

class QualityReferenceDetail(QualityReferenceRead):   # 详情用，带三份正文（只读 Tiptap 渲染）
    content_json: str; content_html: str; plain_text: str

class ImportResponse(BaseModel):
    reference: QualityReferenceRead
    similar: list[dict]                       # near-dup 疑似重复（不硬挡）
```

- [ ] **Step 4: service**

```python
# server/app/modules/quality_reference/service.py
from __future__ import annotations
import hashlib, random, re, unicodedata
from sqlalchemy import bindparam, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from server.app.modules.articles.models import Article
from server.app.modules.quality_reference.models import QualityReference
from server.app.shared.errors import ClientError, ValidationError

_UNSET = object()

def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", s or "")).strip()

def compute_content_hash(title: str, plain_text: str) -> str:
    return hashlib.sha256((_normalize(title) + "\n" + _normalize(plain_text)).encode()).hexdigest()

def _by_hash(db, h): return db.query(QualityReference).filter(QualityReference.content_hash == h).first()

def _reactivate(db, ref):
    if not ref.is_active: ref.is_active = True; db.flush()
    return ref

def _insert_idempotent(db: Session, ref: QualityReference) -> QualityReference:
    """content_hash UNIQUE 做并发兜底：预查未命中也可能撞车 → 捕 IntegrityError、重查返回已有。"""
    existing = _by_hash(db, ref.content_hash)
    if existing is not None:
        return _reactivate(db, existing)
    try:
        with db.begin_nested():
            db.add(ref); db.flush()
        return ref
    except IntegrityError:
        db.rollback()  # 回退 savepoint 后重查（另一并发已插入）
        again = _by_hash(db, ref.content_hash)
        if again is not None:
            return _reactivate(db, again)
        raise

def adopt_article(db, *, user_id: int, article_id: int) -> QualityReference:
    a = db.query(Article).filter(Article.id == article_id, Article.is_deleted == False).first()  # noqa: E712
    if a is None:
        raise ClientError(f"article not found: {article_id}")
    if a.review_status != "approved":
        raise ValidationError("只能采纳已审核（approved）文章")
    dup = db.query(QualityReference).filter(QualityReference.article_id == article_id).first()
    if dup is not None:
        return _reactivate(db, dup)          # 同篇不重复采纳（article_id UNIQUE）
    plain = a.plain_text or ""
    ref = QualityReference(
        origin="own", article_id=article_id, title=a.title,
        content_json=a.content_json or "{}", content_html=a.content_html or "",
        plain_text=plain, content_hash=compute_content_hash(a.title, plain),
        category=a.source_question_category, added_by_user_id=user_id,
    )
    return _insert_idempotent(db, ref)

def import_external(db, *, user_id, title, markdown, category, source_url, platform):
    import nh3
    from server.app.modules.ai_generation.converter import markdown_to_html, markdown_to_tiptap
    from server.app.modules.ai_generation.markdown_sanitizer import normalize_markdown_content
    from server.app.modules.articles.parser import dumps_content_json

    md = normalize_markdown_content(markdown)
    similar = find_similar(db, plain_text=md, limit=5)     # near-dup 软提示（不硬挡）
    ref = QualityReference(
        origin="external", article_id=None, title=title,
        content_json=dumps_content_json(markdown_to_tiptap(md)),   # dict→str
        content_html=nh3.clean(markdown_to_html(md)),              # 防存储型 XSS
        plain_text=md, content_hash=compute_content_hash(title, md),
        category=category, source_url=source_url, platform=platform, added_by_user_id=user_id,
    )
    return _insert_idempotent(db, ref), similar

def list_references(db, *, origin=None, category=None, is_active=None, skip=0, limit=50):
    q = db.query(QualityReference)
    if origin is not None: q = q.filter(QualityReference.origin == origin)
    if category is not None: q = q.filter(QualityReference.category == category)
    if is_active is not None: q = q.filter(QualityReference.is_active == is_active)
    return q.order_by(QualityReference.created_at.desc()).offset(skip).limit(min(limit, 200)).all()

def get_reference(db, ref_id):
    ref = db.query(QualityReference).filter(QualityReference.id == ref_id).first()
    if ref is None: raise ClientError(f"quality_reference not found: {ref_id}")
    return ref

def patch_reference(db, ref_id, *, is_active=None, category=_UNSET):
    ref = get_reference(db, ref_id)
    if is_active is not None: ref.is_active = is_active
    if category is not _UNSET: ref.category = category
    db.flush(); return ref

def pick_references(db, *, category, k, truncate_chars) -> list[dict]:
    """精确类目→不足回落 category IS NULL 通用池；每池优先 external、own 补足；返回带 origin。"""
    def prefer_ext(rows, n):
        ext = [r for r in rows if r.origin == "external"]; own = [r for r in rows if r.origin != "external"]
        random.shuffle(ext); random.shuffle(own); return (ext + own)[:n]
    picked = []
    if category:
        picked = prefer_ext(db.query(QualityReference).filter(
            QualityReference.is_active == True, QualityReference.category == category).all(), k)  # noqa: E712
    if len(picked) < k:
        picked += prefer_ext(db.query(QualityReference).filter(
            QualityReference.is_active == True, QualityReference.category.is_(None)).all(), k - len(picked))  # noqa: E712
    return [{"id": r.id, "title": r.title, "category": r.category, "origin": r.origin,
             "plain_text": (r.plain_text or "")[:truncate_chars]} for r in picked[:k]]

def find_similar(db, *, plain_text, limit) -> list[dict]:
    import logging
    probe = _normalize(plain_text)[:200]
    if len(probe) < 3: return []
    try:
        stmt = text("SELECT id, title FROM quality_reference "
                    "WHERE MATCH(plain_text) AGAINST (:q) > 0 AND is_active = 1 LIMIT :lim"
                    ).bindparams(bindparam("q", probe), bindparam("lim", limit))
        return [{"id": r.id, "title": r.title} for r in db.execute(stmt).all()]
    except Exception:
        logging.getLogger(__name__).warning("qref find_similar FTS unavailable", exc_info=True)  # 不静默吞
        return []
```

- [ ] **Step 5: 跑确认通过** — Expected: PASS（4 passed）。
- [ ] **Step 6: Commit** — `git add server/app/modules/quality_reference/service.py server/app/modules/quality_reference/schemas.py server/tests/test_quality_reference_service.py && git commit -m "feat(adversarial): qref service（hash/幂等/nh3/pick-origin/near-dup）"`

---

## Task 3: quality_reference CRUD 路由（含详情端点）

**Files:** Create `server/app/modules/quality_reference/router.py`；Modify `server/app/main.py`；Test `server/tests/test_quality_reference_api.py`。

**Interfaces — Produces:** `quality_reference_router` 前缀 `/api/quality-reference`：`POST /adopt`、`POST /import`（→ ImportResponse）、`GET ""`、`GET /{id}`（→ Detail 三份正文）、`PATCH /{id}`、`GET /categories`。

- [ ] **Step 1: 写失败测试**（局部 `_make_article`；adopt 拒非 approved；import 返回 similar；detail 带正文）

```python
# server/tests/test_quality_reference_api.py
import pytest
from server.tests.utils import build_test_app
from server.app.modules.articles.models import Article

def _make_article(db, **kw):
    a = Article(user_id=1, title=kw.get("title","T"), content_json="{}", content_html="",
                plain_text="正文", word_count=2, status="draft",
                review_status=kw.get("review_status","approved"))
    db.add(a); db.flush(); return a

@pytest.mark.mysql
def test_import_returns_similar_and_detail_has_body(monkeypatch):
    app_ctx = build_test_app(monkeypatch)
    try:
        c = app_ctx.client
        r = c.post("/api/quality-reference/import", json={"title":"外部好文","markdown":"正文正文正文","category":"通用"})
        assert r.status_code == 200 and "similar" in r.json()
        rid = r.json()["reference"]["id"]
        d = c.get(f"/api/quality-reference/{rid}")
        assert d.status_code == 200 and "content_json" in d.json() and d.json()["plain_text"]
    finally:
        app_ctx.cleanup()
```

- [ ] **Step 2: 跑确认失败**（404）。

- [ ] **Step 3: 路由**

```python
# server/app/modules/quality_reference/router.py
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session
from server.app.core.security import get_current_user      # 核实：security.py:81
from server.app.db.session import get_db
from server.app.modules.ai_generation.models import QuestionItem
from server.app.modules.quality_reference import service as svc
from server.app.modules.quality_reference.models import QualityReference
from server.app.modules.quality_reference.schemas import (
    AdoptRequest, ImportRequest, ImportResponse, PatchRequest,
    QualityReferenceDetail, QualityReferenceRead)

quality_reference_router = APIRouter()

@quality_reference_router.post("/adopt", response_model=QualityReferenceRead)
def adopt(p: AdoptRequest, db: Session = Depends(get_db), user=Depends(get_current_user)):
    ref = svc.adopt_article(db, user_id=user.id, article_id=p.article_id)
    if p.category and ref.category is None:      # 文章无分类时用前端补选
        ref.category = p.category
    db.commit(); db.refresh(ref); return ref

@quality_reference_router.post("/import", response_model=ImportResponse)
def import_ext(p: ImportRequest, db: Session = Depends(get_db), user=Depends(get_current_user)):
    ref, similar = svc.import_external(db, user_id=user.id, title=p.title, markdown=p.markdown,
                                       category=p.category, source_url=p.source_url, platform=p.platform)
    db.commit(); db.refresh(ref)
    return ImportResponse(reference=ref, similar=similar)

@quality_reference_router.get("", response_model=list[QualityReferenceRead])
def list_refs(origin: str | None = None, category: str | None = None, is_active: bool | None = None,
              skip: int = 0, limit: int = 50, db: Session = Depends(get_db), user=Depends(get_current_user)):
    return svc.list_references(db, origin=origin, category=category, is_active=is_active, skip=skip, limit=limit)

# ⚠️ 静态路径 /categories /stats 必须注册在动态 /{ref_id:int} 之前；详情/patch 用 :int 转换器双保险
# （否则 GET /categories 会命中 /{ref_id} 做 int 校验 → 422；现有文章路由同款纪律 articles.py:137）
@quality_reference_router.get("/categories", response_model=list[str])
def categories(db: Session = Depends(get_db), user=Depends(get_current_user)):
    q = db.execute(select(QuestionItem.category).where(QuestionItem.category.isnot(None)).distinct()).scalars().all()
    r = db.execute(select(QualityReference.category).where(QualityReference.category.isnot(None)).distinct()).scalars().all()
    return sorted({*q, *r})

@quality_reference_router.get("/stats", response_model=list[dict])
def stats(db: Session = Depends(get_db), user=Depends(get_current_user)):
    # 按类目聚合 external/own 计数 → 前端显配比 + 「某类目无 external」告警（命门风险的可见化缓解）
    return svc.category_origin_stats(db)

@quality_reference_router.get("/{ref_id:int}", response_model=QualityReferenceDetail)
def detail(ref_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    ref = svc.get_reference(db, ref_id)
    out = QualityReferenceDetail.model_validate(ref)
    out.source_article_deleted = svc.is_source_article_deleted(db, ref)  # 软删不触发 SET NULL，需显式判
    return out

@quality_reference_router.patch("/{ref_id:int}", response_model=QualityReferenceRead)
def patch(ref_id: int, p: PatchRequest, db: Session = Depends(get_db), user=Depends(get_current_user)):
    ref = svc.patch_reference(db, ref_id, is_active=p.is_active,
                              category=p.category if p.category is not None else svc._UNSET)
    db.commit(); db.refresh(ref); return ref
```

> **配套补到 Task 2**：`schemas.py` 的 `QualityReferenceDetail` 加 `source_article_deleted: bool = False`；`service.py` 加两函数：
> ```python
> def is_source_article_deleted(db, ref) -> bool:
>     if ref.article_id is None:
>         return ref.origin == "own"          # own 但 FK 已 NULL = 源被物理删；external 本无源文章
>     return bool(db.query(Article.is_deleted).filter(Article.id == ref.article_id).scalar())  # 软删源 article_id 仍指向它
> def category_origin_stats(db) -> list[dict]:
>     from sqlalchemy import case, func
>     rows = db.query(
>         QualityReference.category,
>         func.sum(case((QualityReference.origin == "external", 1), else_=0)),
>         func.sum(case((QualityReference.origin == "own", 1), else_=0)),
>         func.count(),
>     ).filter(QualityReference.is_active == True).group_by(QualityReference.category).all()  # noqa: E712
>     return [{"category": c, "external": int(e), "own": int(o), "total": int(t)} for c, e, o, t in rows]
> ```

- [ ] **Step 4: mount** — `server/app/main.py`：`app.include_router(quality_reference_router, prefix="/api/quality-reference", tags=["quality-reference"])`。
- [ ] **Step 5: 跑确认通过**。
- [ ] **Step 6: Commit** — `git commit -m "feat(adversarial): qref CRUD 路由 + 详情端点"`。

---

## Task 4: MCP 端点（pick GET / record-score POST）+ 配置

**Files:** Create `server/app/modules/quality_reference/mcp_router.py`；Modify `server/app/main.py` + `server/app/core/config.py`；Test `server/tests/test_quality_reference_mcp.py`。

**Interfaces — Produces:** `GET /api/quality-reference/pick?category=&k=` → `{references:[{id,title,category,origin,plain_text}]}`；`POST /api/articles/{id}/adversarial-score` `{score}` → `{article_id, adversarial_score}`。

- [ ] **Step 1: 配置**（`core/config.py` Settings 内）

```python
    adversarial_topk: int = 3                       # GEO_ADVERSARIAL_TOPK
    adversarial_ref_truncate_chars: int = 4000      # GEO_ADVERSARIAL_REF_TRUNCATE_CHARS
```

- [ ] **Step 2: 写失败测试**（MCP token；record 拒软删；pick GET 截断）

```python
# server/tests/test_quality_reference_mcp.py
import pytest
from server.tests.utils import build_test_app
from server.app.modules.articles.models import Article

def _make_article(db, **kw):
    a = Article(user_id=1, title="T", content_json="{}", content_html="", plain_text="正文",
                word_count=2, status="draft", review_status=kw.get("review_status","pending"),
                is_deleted=kw.get("is_deleted", False))
    db.add(a); db.flush(); return a

@pytest.mark.mysql
def test_pick_requires_mcp_token(monkeypatch):
    app_ctx = build_test_app(monkeypatch)
    try:
        assert app_ctx.client.get("/api/quality-reference/pick?category=通用").status_code == 401
    finally:
        app_ctx.cleanup()

@pytest.mark.mysql
def test_record_score_writes_and_rejects_deleted(monkeypatch):
    monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
    app_ctx = build_test_app(monkeypatch); db = app_ctx.session_factory()
    try:
        a = _make_article(db); db.commit()
        h = {"X-MCP-Token": "secret"}
        assert app_ctx.client.post(f"/api/articles/{a.id}/adversarial-score", json={"score":72}, headers=h).json()["adversarial_score"] == 72
        d = _make_article(db, is_deleted=True); db.commit()
        assert app_ctx.client.post(f"/api/articles/{d.id}/adversarial-score", json={"score":50}, headers=h).status_code == 404
    finally:
        db.close(); app_ctx.cleanup()
```

- [ ] **Step 3: MCP 路由**（DB 查询全进 try；过滤 is_deleted）

```python
# server/app/modules/quality_reference/mcp_router.py
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from server.app.core.config import get_settings
from server.app.core.mcp_auth import require_mcp_token
from server.app.core.mcp_errors import mcp_exception_response
from server.app.db.session import get_db
from server.app.modules.articles.models import Article
from server.app.modules.quality_reference import service as svc
from server.app.shared.errors import ClientError, ValidationError

quality_reference_mcp_router = APIRouter()

@quality_reference_mcp_router.get("/quality-reference/pick", dependencies=[Depends(require_mcp_token)])
def pick(category: str | None = None, k: int | None = None, db: Session = Depends(get_db)):
    s = get_settings()
    try:
        refs = svc.pick_references(db, category=category, k=(k or s.adversarial_topk),
                                   truncate_chars=s.adversarial_ref_truncate_chars)
    except Exception as exc:
        raise mcp_exception_response(exc, context=f"pick category={category}") from exc
    return {"references": refs}

class ScorePayload(BaseModel):
    score: int = Field(ge=0, le=100)

@quality_reference_mcp_router.post("/articles/{article_id}/adversarial-score", dependencies=[Depends(require_mcp_token)])
def record_score(article_id: int, payload: ScorePayload, db: Session = Depends(get_db)):
    try:
        a = db.query(Article).filter(Article.id == article_id, Article.is_deleted == False).first()  # noqa: E712
        if a is None:
            raise HTTPException(status_code=404, detail="article not found")
        a.adversarial_score = payload.score
        db.commit()
    except HTTPException:
        raise
    except (ClientError, ValidationError):
        db.rollback(); raise
    except Exception as exc:
        db.rollback(); raise mcp_exception_response(exc, context=f"record_score article={article_id}") from exc
    return {"article_id": article_id, "adversarial_score": payload.score}
```

- [ ] **Step 4: mount** — `app.include_router(quality_reference_mcp_router, prefix="/api", tags=["quality-reference-mcp"])`。
- [ ] **Step 5: 跑确认通过**。
- [ ] **Step 6: Commit** — `git commit -m "feat(adversarial): MCP pick(GET)+record-score(try/is_deleted)+2 配置"`。

---

## Task 5: MCP 工具注册 + 计数（改对文件）

**Files:** Modify `server/mcp/tools/catalog.py`（pick，复用 `_aget`）+ `server/mcp/tools/action.py`（record，复用 `_apost`）+ `server/app/modules/mcp_catalog/connect_router.py:27` + `server/tests/test_mcp_status_count.py` + `server/tests/test_mcp_tools_registration.py` + `CLAUDE.md`。

- [ ] **Step 1: 计数 27→29** — `connect_router.py:27` `MCP_TOOLS_COUNT = 29`；`CLAUDE.md` 里 26/27 处同步 29。
- [ ] **Step 2: 工具**

`catalog.py` 追加（GET，复用本文件 `_aget`）：
```python
@mcp.tool()
async def pick_quality_references(category: str | None = None, k: int | None = None) -> dict:
    """取 1~k 篇同类高质量参考（服务端优先 external、随机、正文截断），供对抗判分对比。"""
    params = {"category": category}
    if k is not None: params["k"] = k
    r = await _aget("/api/quality-reference/pick", params=params)
    return r.get("data", r)
```
`action.py` 追加（POST，复用本文件 `_apost`）：
```python
@mcp.tool()
async def record_adversarial_score(article_id: int, score: int) -> dict:
    """把对抗判分（N 次求平均后的 0-100 整数）记到文章上。"""
    r = await _apost(f"/api/articles/{article_id}/adversarial-score", json={"score": score})
    return r.get("data", r)
```

- [ ] **Step 3: 计数断言改 29** — `test_mcp_status_count.py`（`== 27` → `== 29`，函数名 `test_mcp_tools_count_is_27` 改 `_is_29`）；`test_mcp_tools_registration.py`（floor `>= 27` → `>= 29`）。
- [ ] **Step 4: 跑** — Run: `... env python -m pytest server/tests/test_mcp_status_count.py server/tests/test_mcp_tools_registration.py -q`；Expected: PASS。
- [ ] **Step 5: Commit** — `git commit -m "feat(adversarial): 注册 2 MCP 工具 + 计数 27→29（改对断言文件）"`。

---

## Task 6: get_article 暴露溯源字段 + 文章列表对抗分

**Files:** Modify `server/app/modules/articles/schemas.py`（`ArticleRead` + `ArticleListRead`）+ `articles/parser.py`（`to_article_read`，若序列化在别处按实名定位）+ `articles/services/feed.py`；Test `server/tests/test_article_read_provenance.py` + 追加 `test_articles_feed.py`。

**Interfaces — Produces:** `ArticleRead.source_question_category:str|None` + `.source_question_texts:list|None`；`ArticleListRead.adversarial_score:int|None`。

- [ ] **Step 1: 写失败测试**（MCP get_article 带出 category）

```python
# server/tests/test_article_read_provenance.py
import pytest
from server.tests.utils import build_test_app
from server.app.modules.articles.models import Article

@pytest.mark.mysql
def test_get_article_exposes_source_question_category(monkeypatch):
    monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
    app_ctx = build_test_app(monkeypatch); db = app_ctx.session_factory()
    try:
        a = Article(user_id=1, title="T", content_json="{}", content_html="", plain_text="正文",
                    word_count=2, status="draft", review_status="pending",
                    source_question_category="餐厅", source_question_texts=["怎么开店"])
        db.add(a); db.commit()
        r = app_ctx.client.get(f"/api/mcp/articles/{a.id}", headers={"X-MCP-Token":"secret"})  # 按 mcp_catalog get 路径实名
        assert r.status_code == 200 and r.json()["source_question_category"] == "餐厅"
    finally:
        db.close(); app_ctx.cleanup()
```

> get_article 的真实 MCP 路径见 `mcp_catalog/router.py:93`——按该文件实名对齐 URL。

- [ ] **Step 2: 跑确认失败**（字段缺失）。
- [ ] **Step 3: schemas + 序列化**
  - `ArticleRead` 加 `source_question_category: str | None = None` + `source_question_texts: list | None = None`。
  - `to_article_read`（构造 `ArticleRead` 的地方；grep `ArticleRead(` 定位，spec §13.3 指向 schemas.py:226 区域）里补两字段赋值。
  - `ArticleListRead` 加 `adversarial_score: int | None = None`。
- [ ] **Step 4: feed** — `_list_summary_load_options()` 的 `load_only(...)` 加 `Article.adversarial_score`；`serialize_article_summaries` 构造 `ArticleListRead(...)` 处加 `adversarial_score=a.adversarial_score`。
- [ ] **Step 5: 追加 feed 测试**（照 Task 6 局部 `_make_article` 置 `adversarial_score=88`，断 feed item 带出）。
- [ ] **Step 6: 跑确认通过**。
- [ ] **Step 7: Commit** — `git commit -m "feat(adversarial): get_article 暴露溯源字段 + 列表带对抗分"`。

---

## Task 7: save 路径单题溯源（W2，仅 /goal，单题）

**Files:** Modify `server/app/modules/articles/routers/mcp.py`（`save_article_from_mcp`）；Test 追加 `server/tests/test_save_article_mcp.py`。

**决策：本期只存单题**——**不加** `question_item_ids` 参数、不改 orchestrator。

**Interfaces — Produces:** 文章 `source_question_category = item.category` + `source_question_texts = [item.question_text]`。

- [ ] **Step 1: 写失败测试**（用现有 `question_item_id`，快照单题）

```python
# server/tests/test_save_article_mcp.py —— 追加（沿用本文件既有 setenv/header 与建 question/template 方式）
@pytest.mark.mysql
def test_save_snapshots_single_question_provenance(monkeypatch):
    monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
    app_ctx = build_test_app(monkeypatch); db = app_ctx.session_factory()
    try:
        qid = _make_question_item(db, category="餐厅", question_text="怎么开店")   # 照本文件既有工厂
        tid = _make_template(db)
        r = app_ctx.client.post("/api/articles/save-from-mcp", headers={"X-MCP-Token":"secret"},
            json={"question_item_id": qid, "prompt_template_id": tid, "user_id": 1,
                  "title":"T", "markdown_content":"正文"})
        aid = r.json()["article_id"]
        db.expire_all(); art = db.query(Article).get(aid)
        assert art.source_question_category == "餐厅" and art.source_question_texts == ["怎么开店"]
    finally:
        db.close(); app_ctx.cleanup()
```

- [ ] **Step 2: 跑确认失败**（字段 None）。
- [ ] **Step 3: 快照逻辑** — `save_article_from_mcp` 里，`_create_article` 之后、`db.commit()` 之前（`item` 已在 mcp.py:237 查得）：

```python
    article.source_question_category = item.category
    article.source_question_texts = [item.question_text] if item.question_text else None
```

- [ ] **Step 4: 跑确认通过**。
- [ ] **Step 5: Commit** — `git commit -m "feat(adversarial): save 快照单题问题类别+提问词（仅 /goal）"`。

---

## Task 8: 前端——高质量库页 + 文章列表对抗分

**Files:** Create `web/src/api/qualityReference.ts` + `web/src/features/quality-reference/QualityReferenceWorkspace.tsx`（+ 抽 `buildReadonlyExtensions()` 共享文件）；Modify `web/src/routes.tsx`（lazy route）+ `web/src/App.tsx`（KNOWN_NAV + TAB_TITLES）+ `web/src/components/MobileMorePage.tsx`（移动端入口）+ `web/src/types.ts`（Article 加 `source_question_category`/`source_question_texts` + 摘要加 `adversarial_score`）+ `web/src/components/ArticleListItem.tsx`（对抗分）+ `web/src/features/content/ContentWorkspace.tsx`（改用共享扩展工厂）。

> 前端无单测：门禁 = `pnpm -C web typecheck && pnpm -C web build`。**api 是 fetch**（`api<T>(path, RequestInit)`），路由走 `routes.tsx`（无 visitedTabs）。

- [ ] **Step 1: api 客户端（fetch 风格）**

```ts
// web/src/api/qualityReference.ts
import { api } from "./core";
export interface QualityReference { id:number; origin:string; article_id:number|null; title:string; category:string|null; source_url:string|null; platform:string|null; is_active:boolean; created_at:string; }
export interface QualityReferenceDetail extends QualityReference { content_json:string; content_html:string; plain_text:string; }
export const adoptReference = (b:{article_id:number; category?:string|null}) =>
  api<QualityReference>("/api/quality-reference/adopt", {method:"POST", body:JSON.stringify(b)});
export const importReference = (b:{title:string;markdown:string;category?:string|null;source_url?:string|null;platform?:string|null}) =>
  api<{reference:QualityReference; similar:{id:number;title:string}[]}>("/api/quality-reference/import", {method:"POST", body:JSON.stringify(b)});
export const listReferences = (q:{origin?:string;category?:string;is_active?:boolean}) => {
  const s = new URLSearchParams(Object.entries(q).filter(([,v])=>v!=null).map(([k,v])=>[k,String(v)]));
  return api<QualityReference[]>(`/api/quality-reference?${s}`);
};
export const getReference = (id:number) => api<QualityReferenceDetail>(`/api/quality-reference/${id}`);
export const patchReference = (id:number, b:{is_active?:boolean;category?:string}) =>
  api<QualityReference>(`/api/quality-reference/${id}`, {method:"PATCH", body:JSON.stringify(b)});
export const referenceCategories = () => api<string[]>("/api/quality-reference/categories");
// 采纳搜索复用现有文章检索 api（review_status=approved）
```

- [ ] **Step 2: Workspace 组件**（要点，含三轮修正）
  - 列表：`listReferences` + origin/category/is_active 过滤 + 「下架」（`patchReference{is_active:false}`），行内显 origin。
  - **配比告警用 `qualityReferenceStats()`（新 stats 端点），不要用列表数组长度算**——列表默认只回 50 条、过滤后算比例会失真。页顶显各类目 external/own 配比 + 「external=0」类目红字告警（命门风险可见化）。
  - 「采纳站内文章」→ 搜索：**纯数字走 `getArticle(id)`**（不能塞进 `q`）、否则 `listArticles(new URLSearchParams({q, review_status:"approved"}))`（FTS 命中标题+作者+正文、非仅标题）→ 结果 → 点采纳前**先 `getArticle(id)` 取详情**：`source_question_category` 为空才弹分类下拉，再 `adoptReference({article_id, category})`。
  - 「录入外部文章」→ title + category 下拉（`referenceCategories`，可空）+ markdown `<textarea>` + 可选 url/platform → `importReference`；返回 `similar` 非空时提示「疑似重复」（不阻断）。
  - 「查看」→ 抽屉 `getReference(id)`；`source_article_deleted` 为真时来源链接显「原文已删」而非死链。只读 Tiptap 见 Step 2b。

- [ ] **Step 2b: 抽共享只读扩展 + 正确挂载**（ContentWorkspace 编辑器靠显式 `setContent`（:630）加载、正文样式靠 `editorSurface`（styles.css:1158））
  - 抽 `buildReadonlyExtensions()`：**每次返回新实例**（StarterKit.configure({link:{openOnClick:false}}) + CustomImage + TextStyle/Color/Highlight/TextAlign，照 ContentWorkspace.tsx:387/392 那套；别重复注册 Link/Underline）。ContentWorkspace 与 reader 两处复用同一工厂。
  - reader：`useEditor({ editable:false, extensions:buildReadonlyExtensions(), editorProps:{attributes:{class:"editorSurface"}} })`，外层包 `editorWrap paper-scope`；在 `detail.id`/`detail.content_json` 变化时 `editor.commands.setContent(JSON.parse(detail.content_json))`（只写初始 `content` 不会随切换更新）。

- [ ] **Step 3: 路由 + 导航 + 类型 + 移动端**
  - `routes.tsx`：加 `React.lazy(() => import(...).then(m => ({default: m.QualityReferenceWorkspace})))` 常量 + route `element`（照 routes.tsx:12/109 现有形态，无 loader/provider）。
  - `App.tsx`：`KNOWN_NAV` + `TAB_TITLES`（App.tsx:18）都加「高质量库」键；不在底栏的 key 归入「更多」（App.tsx:31）。
  - **`web/src/components/MobileMorePage.tsx`（必须改，Task 8 Files 已列）**：其「内容工具」硬编码列表（:23）加高质量库项（NavKey+label+icon），否则手机端进不去。
  - `types.ts`：`ArticleSummary` 加 `adversarial_score:number|null`；**`Article` 加 `source_question_category:string|null` + `source_question_texts:string[]|null`**（采纳流程要读，Task 6 后端已在 `ArticleRead` 暴露）。
  - 文章列表项（`ArticleListItem.tsx:76`，散篇+组员共用）：`auto_review_score` 旁加 `adversarial_score`，`null` 恒显「对抗分：—」。
  - `qualityReference.ts` 加 `qualityReferenceStats = () => api<{category:string|null;external:number;own:number;total:number}[]>("/api/quality-reference/stats")`。

- [ ] **Step 4: 门禁** — Run: `pnpm -C web typecheck && pnpm -C web build`；Expected: 均通过。
- [ ] **Step 5: Commit** — `git commit -m "feat(adversarial): 前端高质量库页(只读Tiptap)+列表对抗分"`。

---

## Task 9: verifier / writer skill + 真发布 SkillVersion

**Files:** Modify `server/app/modules/loop_skills/templates/skills/geo-article-verifier/SKILL.md` + `.../geo-article-writer/SKILL.md`；发布新 `SkillVersion` 设 `goal.current_version_id`。

- [ ] **Step 1: 把对抗判分嵌进 verifier 的硬 Required Checklist（不是文末追加段落）**

verifier 的 Role 明说「你只做 4 维打分」（SKILL.md:11），Required Checklist 第 7 步已返回终态 JSON（SKILL.md:14-40）——**只在文末加段落，skill 按 checklist 跑完就返回、永远不判对抗**。必须改三处：

- **Role（SKILL.md:11）** 加一句：「过审文章还要 best-effort 做一次对抗判分（advisory；失败可跳过、不影响评审）」。
- **在第 6 步（submit_review_decision）后、第 7 步（返回 JSON）前，插入新第 6.5 步**：

```markdown
6.5. 对抗判分（best-effort，**仅当第 5 步 `decision == "approved"`**；本步任何异常一律吞掉、不影响评审）：
   a. `get_article(article_id)` 取 `source_question_category` 作 category。
   b. 循环 3 次：`pick_quality_references(category, k=2)`；对比候选 vs 参考，判「更像真品高质量 / 像 AI 水文」，给 0-100 realness。
      - `pick` 返回空 / 报错 → 立即停止本步、不记分（正常继续第 7 步）。
      - 参考多为 origin="own"（外部不足）→ 降低置信，reasoning 注明「参考多为自产、判别力有限」。
   c. 3 个分求平均、四舍五入 → `record_adversarial_score(article_id, 平均分)`。
   d. **pick / 判分 / record 任一异常都 swallow**：跳过、不写分、照常继续第 7 步返回原四维 JSON。绝不因对抗段失败而改 decision 或不返回。
```

- **短路条件用第 5 步的 `decision`**（`decision != "approved"` 整个 6.5 跳过），不是裸 `score_total`——这样 `policy_safety < 80` 被拦的也天然跳过。
- 理由：orchestrator 把 verifier 的报错/无 JSON 当「评审失败、停止本题」（orchestrator SKILL.md:274）；对抗段不隔离异常就会覆盖已完成的四维结果、违反 advisory 不变式。

- [ ] **Step 2: writer——只核验、不改**。writer 当前只传单个 `question_item_id`（SKILL.md:28），单题溯源由 Task 7 服务端快照，writer 无感。**本步只确认无需改动，不产生 writer diff**。

- [ ] **Step 3: 幂等发布脚本（服务层直调，避开「每次追加新版本」和「ZIP 根目录」两个坑）**

现成端点 `POST /api/mcp/skills/{id}/versions` **每次都追加新版本 + 切 current、不做 SHA 去重**（skill_service.py:169-191），重跑灌一堆同内容版本；Web 选 `templates/` 文件夹上传还会把根目录名塞进路径（skills.ts:53 / upload.py:47）→ 装错目录。所以写一个**服务层脚本**（不走 HTTP、不走文件夹上传），入口做 SHA 幂等：

```python
# server/scripts/publish_goal_skill.py（新）
from server.app.db.session import SessionLocal
from server.app.modules.loop_skills.service import build_bundle, get_current_bundle
from server.app.modules.loop_skills.skill_service import _active_skill_by_slug, append_version_by_id  # 按实名对齐

def main() -> None:
    db = SessionLocal()
    new = build_bundle()                                  # 扫 templates/，根路径正确
    try:
        if get_current_bundle(db, "goal").bundle_sha256 == new.bundle_sha256:
            print("goal bundle unchanged, skip"); return  # 幂等：SHA 相同不发新版
    except Exception:
        pass                                              # 无 current（首发）→ 继续
    skill = _active_skill_by_slug(db, "goal")
    entries = [(f.path, f.content.encode() if isinstance(f.content, str) else f.content) for f in new.files]
    _, ver = append_version_by_id(db, skill_id=skill.id, entries=entries, uploaded_by=None, is_admin=True)
    db.commit()
    print(f"published goal v={ver.version_label} sha={ver.bundle_sha256}")
```
> `append_version_by_id` = skill_service.py:159 那个函数（内部 `build_bundle_from_file_map` + `_append_version` + 自动切 `current_version_id`）。发布前后记录 `skill_id / current_version_id / bundle_sha256`。

- [ ] **Step 4: 干净环境冒烟（证明 DB current bundle 真驱动 /goal、不被本机旧模板污染）**
  1. 确认本机 `~/.claude/skills` 无本次新版（orchestrator 读**本机** SKILL.md，本机残留会让 Step 3 是否成功无法证伪）。
  2. `install_loop_skills(slug="goal")` 取 DB current bundle 文件——**该工具只返回文件、不自动写盘**（action.py:303），需手动落盘到 `~/.claude/skills`。
  3. 重开会话跑 `/goal` 单篇：文章入未审核库、`adversarial_score` 有值、4 维 retry/停止不变。
  4. 另用 MCP token 拉 `install-payload`，逐路径断言 verifier SKILL.md 含新第 6.5 步 + `pick_quality_references`/`record_adversarial_score`。
- [ ] **Step 5: Commit** — `git commit -m "feat(adversarial): verifier 后置对抗判分 + 发布新 goal SkillVersion"`。

---

## Self-Review（v2 对 spec 覆盖 + 二轮 Codex 阻断项闭环）

- spec §3.1 三列→T1；§3.2 qref（origin CHECK/hash UNIQUE/SET NULL）→T1；§4 CRUD+查重+详情→T2/T3；§5 pick(GET,origin)→T2/T4/T5；§6 单题溯源→T7；§7 verifier→T9；§8 前端→T8/T6；§13.3 全部集成契约修正→逐条落到 T1(迁移测试/utils)、T2(nh3/near-dup/UNIQUE)、T3(详情)、T4(GET/is_deleted)、T5(计数文件/CLAUDE)、T6(get_article 字段)、T8(fetch/routes)、T9(SkillVersion)。
- 二轮 12 项阻断闭环：skill 发布→T9；get_article 字段→T6；详情端点→T3；catalog `_apost`→T5(改 `_aget` GET)；前端 fetch/routes→T8；测试库/真迁移→T1；hash UNIQUE+并发→T1/T2；外部 XSS→T2(nh3)；治理/adopt 权限→**按用户决策维持全员可写**（不实现，spec §13.1/§13.2 记录）；分时效→**用户决定不加**（记录）；near-dup 接线→T2/T3；修订折进 Task→本 v2 已折。
- 三轮 Codex 阻断闭环：`/categories` 路由顺序（`:int` 转换器 + 静态先注册）→T3；verifier 只加段落会被跳过→T9(嵌进硬 Checklist 第 6.5 步 + 异常隔离 + 短路用 decision)；发布端点不幂等 + ZIP 根目录坑→T9(服务层幂等脚本)；本机模板污染冒烟→T9(干净环境 install_loop_skills 落盘)；前端读不到分类→T8(Article 加字段 + adopt 前 getArticle)；配比不能用 50 条列表算→T3(stats 端点)+T8；移动端入口→T8(MobileMorePage)；只读 editor setContent/样式→T8(Step 2b)；**软删原文「原文已删」→T3(`source_article_deleted` 端点)+T8(渲染)**（二轮此项曾漏、已补）。
- 用户接受的风险（不实现，仅记录）：命门未完全关闭（缓解=pick 优先 external + T3 stats 端点 + 前端配比/覆盖告警）、分可能陈旧、分为粗信号。

## Execution Handoff

计划 v2 已存 `docs/superpowers/plans/2026-07-14-adversarial-review-quality-gate.md`。执行二选一：
1. **Subagent-Driven（推荐）** — 每 Task 派新子代理、任务间 review；按 T1→T9 顺序（T6 依赖 T1 列；T9 依赖 T4/T5 工具就绪）。
2. **Inline** — 本会话 executing-plans 批量 + 检查点。

选哪种？
