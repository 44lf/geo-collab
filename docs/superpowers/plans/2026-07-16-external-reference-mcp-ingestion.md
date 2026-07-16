# 站外文章 → 异步入高质量库外部参考（MCP 入口）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增一条 MCP 入口，把爬到的站外文章（markdown + 外链图）异步导入高质量库的「外部参考」池（`quality_reference.origin="external"`），图片下载回传进 MinIO 专桶并以站内内链引用、跨篇 sha256 去重共享，落库即 `is_active=True` 直接生效。

**Architecture:** 仿 `video/service.py` 的异步 job 模式（job 表 + daemon 线程 + 轮询），不引入 Celery/消息队列。正文走 markdown 路径复用既有 `import_external()`；给 `converter._TiptapBuilder` 补 `<img>` 分支使图片保真（`save_article` 顺带受益）。图片经 SSRF 校验后下载 → MinIO 专桶（`geo-qref-images`）→ 正文改写为站内内链 `/api/quality-reference/images/{id}`。三张新表：图片资源表 / reference↔image 关联表 / 导入 job 表。

**Tech Stack:** FastAPI + SQLAlchemy/Alembic（MySQL）、MinIO（`image_library/store` 封装）、httpx（图片下载）、python-markdown + HTMLParser（converter）、FastMCP（MCP 工具）。

## Global Constraints

- **MySQL only**：迁移用 `mysql_engine="InnoDB"`、`mysql_charset="utf8mb4"`；带 DB 的测试标 `@pytest.mark.mysql`、DB 名含 `"test"`。
- **service 层只抛命名异常**（`ClientError`/`ValidationError`/`ConflictError`），绝不抛裸 `ValueError`——无全局 `ValueError` 兜底。
- **MCP 端点**：走 `Depends(require_mcp_token)`，未捕获异常一律经 `core/mcp_errors.mcp_exception_response(exc, context=...)` 包装，不直接抛裸 Exception。
- **正文三份并行结构**：`content_json`（序列化字符串）/ `content_html`（nh3 清洗）/ `plain_text`，由 `import_external()` 统一生成，本期不改该函数。
- **路由前缀统一单数** `/api/quality-reference`（对齐既有 `quality_reference_router` / `quality_reference_mcp_router`）。内链 URL 必须与图片代理路由逐字一致：`/api/quality-reference/images/{image_id}`。
- **MinIO 专桶常量** `QREF_BUCKET = "geo-qref-images"`；`internal_url(image_id)` 返回 `/api/quality-reference/images/{image_id}`。
- **SSRF 档位（本期定档）**：scheme 白名单（http/https）+ 解析所有 A/AAAA、任一命中私网/环回/link-local/保留/元数据即拒 + 逐跳重定向重校验 + `max_bytes` 上限 + 短超时。**IP 钉连接抗 DNS 重绑定列入未来工作**（输入是运营喂的真 URL、威胁低）。
- **worker 事务顺序（避坑）**：图片先各自幂等入库并 `commit` → 再建 reference（`import_external`）+ `commit` → 再写 image_link + `commit`。避开 `_insert_idempotent` 在 content_hash 撞车时 `db.rollback()` 冲掉未提交的关联行（`service.py:91`）。
- **并发闸（护连接池，非互斥锁）**：`run_import_job` 先抢 `BoundedSemaphore`（`GEO_QREF_IMPORT_MAX_CONCURRENT`，默认 3）再开 session；排队 daemon 线程 park 住、不占 DB 连接。仿 pipelines 的 `GEO_PIPELINE_MAX_CONCURRENT_RUNS`，防异步扇出打爆连接池（历史事故：连接池耗尽演示崩溃）。**数据层并发安全已由各表 UNIQUE + insert-or-get 重试保证（内容寻址 MinIO key 令并发上传幂等），本闸只限吞吐、不串行化、不做互斥。**
- **MCP_TOOLS_COUNT 真值**：`mcp_catalog/connect_router.py:27`，本期 `31 → 33`。
- 迁移 `down_revision` 落定前跑 `alembic heads` 确认头（历史有过三 `0056` 重号，务必核对）；本计划按当前头 `0063_qref_multi_category` 编写。

---

## File Structure

- **Create** `server/alembic/versions/0064_qref_external_ingestion.py` — 建 3 张表。
- **Modify** `server/app/modules/quality_reference/models.py` — 增 3 个 ORM 模型（同文件，`main.py:41` 已 import 触发注册，无需额外接线）。
- **Modify** `server/app/modules/quality_reference/schemas.py` — 增 `ImportExternalReferenceRequest`、`ImportJobStatus`。
- **Create** `server/app/modules/quality_reference/fetch.py` — 外链图下载 + SSRF 校验。
- **Create** `server/app/modules/quality_reference/image_store.py` — qref 图片 MinIO 存取 + sha256 去重建行 + 内链拼装 + 孤儿查询。
- **Create** `server/app/modules/quality_reference/import_job.py` — job 生命周期 + 后台 worker（镜像 `video/service.py`）。
- **Create** `server/app/modules/quality_reference/import_router.py` — MCP token：`POST /import-external` + `GET /import-jobs/{job_id}`。
- **Create** `server/app/modules/quality_reference/images_router.py` — 公开只读图片代理：`GET /images/{image_id}`。
- **Modify** `server/app/modules/ai_generation/converter.py` — `_TiptapBuilder` 增 `<img>` 顶层化分支。
- **Modify** `server/app/main.py` — import + 挂载两个新 router + 注入 `import_job.bg_session_factory`。
- **Modify** `server/mcp/tools/action.py` — 增 `import_external_reference` / `get_external_reference_status` 两个工具。
- **Modify** `server/app/modules/mcp_catalog/connect_router.py` — `MCP_TOOLS_COUNT` `31 → 33`。
- **Test** `server/tests/test_qref_converter_img.py`、`test_qref_fetch_ssrf.py`、`test_qref_image_store.py`、`test_qref_import_job.py`、`test_qref_import_endpoints.py`。

---

## Task 1: converter `<img>` 顶层化补丁

**Files:**
- Modify: `server/app/modules/ai_generation/converter.py:46-96`
- Test: `server/tests/test_qref_converter_img.py`

**Interfaces:**
- Consumes: 无（纯函数）。
- Produces: `markdown_to_tiptap(md: str) -> dict` 签名不变，现在会为 markdown 图片产出**顶层** `image` 节点：`{"type":"image","attrs":{"src":str,"alt":str,"title":"","width":"30%","assetId":None}}`。

- [ ] **Step 1: 写失败测试**

```python
# server/tests/test_qref_converter_img.py
from server.app.modules.ai_generation.converter import markdown_to_tiptap


def _types(doc):
    return [n["type"] for n in doc["content"]]


def test_single_image_is_top_level():
    doc = markdown_to_tiptap("![cat](http://x/c.png)")
    assert doc["content"] == [
        {
            "type": "image",
            "attrs": {
                "src": "http://x/c.png",
                "alt": "cat",
                "title": "",
                "width": "30%",
                "assetId": None,
            },
        }
    ]


def test_image_between_paragraphs_keeps_order():
    doc = markdown_to_tiptap("before\n\n![a](u1)\n\nafter")
    assert _types(doc) == ["paragraph", "image", "paragraph"]


def test_inline_image_splits_paragraph_in_order():
    doc = markdown_to_tiptap("hello ![a](u) world")
    assert _types(doc) == ["paragraph", "image", "paragraph"]


def test_multiple_block_images_preserve_src_order():
    doc = markdown_to_tiptap("![a](u1)\n\n![b](u2)")
    assert _types(doc) == ["image", "image"]
    assert [n["attrs"]["src"] for n in doc["content"]] == ["u1", "u2"]


def test_no_stray_empty_paragraph_around_image():
    doc = markdown_to_tiptap("![only](u)")
    assert all(n["type"] != "paragraph" for n in doc["content"])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest server/tests/test_qref_converter_img.py -v`
Expected: FAIL —— 当前 `_TiptapBuilder` 无 `img` 分支，图片被吞，`doc["content"]` 里没有 `image` 节点。

- [ ] **Step 3: 给 `handle_starttag` 增 `img` 分支（flush-and-reopen 保序）**

在 `converter.py` 的 `handle_starttag` 末尾（`code`/`tt` 分支之后）追加：

```python
        elif tag == "img":
            a = dict(attrs)
            img_node = {
                "type": "image",
                "attrs": {
                    "src": a.get("src") or "",
                    "alt": a.get("alt") or "",
                    "title": "",
                    "width": "30%",  # 编辑器 CustomImage 默认显示宽度
                    "assetId": None,  # 内链已在 src 里，非站内 Asset
                },
            }
            # image 是块级节点，不能嵌在 paragraph/list 里。若正处于段落中：
            # 先把已累积的段落文本收尾（保序），再把 image 落到顶层，最后重开一个
            # 空段落承接图片后面的行内文本。python-markdown 把 ![](url) 包成 <p><img/></p>，
            # 独占一行时该段落为空、会在 </p> 处被丢弃。
            reopen = False
            if self._stack and self._stack[-1].get("type") == "paragraph":
                para = self._stack.pop()
                if para.get("content"):
                    self._commit(para)
                reopen = True
            self._root.append(img_node)
            if reopen:
                self._stack.append({"type": "paragraph", "content": []})
```

- [ ] **Step 4: 改 `handle_endtag`，把 `p` 单独拆出以丢弃空段落**

将现有：

```python
    def handle_endtag(self, tag: str) -> None:
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6", "p", "ul", "ol"):
            if self._stack:
                self._pop_commit()
        elif tag == "li":
```

改为：

```python
    def handle_endtag(self, tag: str) -> None:
        if tag == "p":
            # 只弹 paragraph；空段落（如仅承接过被提升的图片）直接丢弃，不产出空节点。
            if self._stack and self._stack[-1].get("type") == "paragraph":
                node = self._stack.pop()
                if node.get("content"):
                    self._commit(node)
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol"):
            if self._stack:
                self._pop_commit()
        elif tag == "li":
```

（`li` 及以下 marks 分支保持不变。）

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest server/tests/test_qref_converter_img.py -v`
Expected: PASS（5 个用例全绿）。

- [ ] **Step 6: 回归——确认既有 converter 行为不破**

Run: `pytest server/tests/ -q -k "converter or markdown" -p no:cacheprovider`
Expected: PASS（无既有转换用例回归；`li` 紧凑列表包 paragraph 逻辑未动）。

- [ ] **Step 7: 提交**

```bash
git add server/app/modules/ai_generation/converter.py server/tests/test_qref_converter_img.py
git commit -m "feat(converter): markdown_to_tiptap 支持图片顶层化（qref 外部参考 + save_article 顺带受益）"
```

---

## Task 2: 三张新表（ORM 模型 + alembic 迁移）

**Files:**
- Modify: `server/app/modules/quality_reference/models.py`（文件末尾追加 3 个类）
- Create: `server/alembic/versions/0064_qref_external_ingestion.py`
- Test: `server/tests/test_qref_import_job.py`（本任务先只放模型 round-trip 用例）

**Interfaces:**
- Produces:
  - `QualityReferenceImage`（表 `quality_reference_image`）：`id, sha256(UNIQUE), minio_key, bucket, mime_type, size, width?, height?, created_at`。
  - `QualityReferenceImageLink`（表 `quality_reference_image_link`）：`id, reference_id(FK→quality_reference ON DELETE CASCADE), image_id(FK→quality_reference_image)`，`UNIQUE(reference_id, image_id)`。
  - `QualityReferenceImportJob`（表 `quality_reference_import_job`）：`id, job_id(str32 UNIQUE), status, progress, title, markdown, platform?, source_url, category?, question_texts?(JSON), reference_id?, images_total, images_rehosted, images_skipped, error?, created_at, updated_at`。

- [ ] **Step 1: 确认迁移头**

Run: `cd /e/geo && python -m alembic -c server/alembic.ini heads 2>/dev/null || echo "用 GEO_DATABASE_URL 环境跑：alembic heads"`
Expected: 单一头 `0063_qref_multi_category`。若非单头或已前进，把下方 `down_revision` 改成真实头。

- [ ] **Step 2: 追加 3 个 ORM 模型到 `models.py`**

在 `server/app/modules/quality_reference/models.py` 末尾追加（import 区补 `Integer`）：

```python
class QualityReferenceImage(Base):
    """去重共享的图片资源（不属于单篇；归属由 QualityReferenceImageLink 表达）。"""

    __tablename__ = "quality_reference_image"
    __table_args__ = (
        UniqueConstraint("sha256", name="uq_qref_image_sha256"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64))  # 去重键（内容哈希）
    minio_key: Mapped[str] = mapped_column(String(500))  # 专桶内对象 key（sha256+ext）
    bucket: Mapped[str] = mapped_column(String(100))  # 冗余记桶名，便于将来迁桶
    mime_type: Mapped[str] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class QualityReferenceImageLink(Base):
    """reference ↔ image 关联（多对多，供孤儿清理）。"""

    __tablename__ = "quality_reference_image_link"
    __table_args__ = (
        UniqueConstraint("reference_id", "image_id", name="uq_qref_image_link_ref_img"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    reference_id: Mapped[int] = mapped_column(
        ForeignKey("quality_reference.id", ondelete="CASCADE"), nullable=False, index=True
    )
    image_id: Mapped[int] = mapped_column(
        ForeignKey("quality_reference_image.id"), nullable=False, index=True
    )


class QualityReferenceImportJob(Base):
    """异步导入 job（仿 VideoJob）：建 job 秒回 → 后台线程跑 → 轮询状态。"""

    __tablename__ = "quality_reference_import_job"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), server_default="pending")  # pending/running/done/failed
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    title: Mapped[str] = mapped_column(String(300))
    markdown: Mapped[str] = mapped_column(Text)  # 输入快照
    platform: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_url: Mapped[str] = mapped_column(String(1000))  # 必填
    category: Mapped[str | None] = mapped_column(String(200), nullable=True)
    question_texts: Mapped[list | None] = mapped_column(JSON, nullable=True)
    reference_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 成功回填
    images_total: Mapped[int] = mapped_column(Integer, default=0)
    images_rehosted: Mapped[int] = mapped_column(Integer, default=0)
    images_skipped: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
```

同时把 `models.py` 顶部 import 补上 `Integer` 和 `Float`：

```python
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
```

- [ ] **Step 3: 写迁移文件**

```python
# server/alembic/versions/0064_qref_external_ingestion.py
"""qref 外部参考异步入库：图片资源表 + reference↔image 关联表 + 导入 job 表。

站外文章爬取 → 异步入高质量库外部参考（MCP 入口）。图片跨篇 sha256 去重共享（无
reference_id，归属走 link 表）；job 表仿 video_jobs 支撑建 job 秒回 + 轮询。

Revision ID: 0064_qref_external_ingestion
Revises: 0063_qref_multi_category
Create Date: 2026-07-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0064_qref_external_ingestion"
down_revision: str | None = "0063_qref_multi_category"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "quality_reference_image",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("minio_key", sa.String(500), nullable=False),
        sa.Column("bucket", sa.String(100), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("sha256", name="uq_qref_image_sha256"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_table(
        "quality_reference_image_link",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("reference_id", sa.Integer(), nullable=False),
        sa.Column("image_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["reference_id"], ["quality_reference.id"],
            name="fk_qref_image_link_reference_id", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["image_id"], ["quality_reference_image.id"],
            name="fk_qref_image_link_image_id",
        ),
        sa.UniqueConstraint("reference_id", "image_id", name="uq_qref_image_link_ref_img"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index("ix_qref_image_link_image_id", "quality_reference_image_link", ["image_id"])
    op.create_table(
        "quality_reference_import_job",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.String(32), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("progress", sa.Float(), nullable=False, server_default="0"),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("markdown", sa.Text(), nullable=False),
        sa.Column("platform", sa.String(100), nullable=True),
        sa.Column("source_url", sa.String(1000), nullable=False),
        sa.Column("category", sa.String(200), nullable=True),
        sa.Column("question_texts", sa.JSON(), nullable=True),
        sa.Column("reference_id", sa.Integer(), nullable=True),
        sa.Column("images_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("images_rehosted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("images_skipped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("job_id", name="uq_qref_import_job_job_id"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index("ix_qref_import_job_job_id", "quality_reference_import_job", ["job_id"])


def downgrade() -> None:
    op.drop_table("quality_reference_import_job")
    op.drop_table("quality_reference_image_link")
    op.drop_table("quality_reference_image")
```

- [ ] **Step 4: 应用迁移（对测试库）**

Run:
```bash
GEO_DATABASE_URL="$GEO_TEST_DATABASE_URL" python -m alembic -c server/alembic.ini upgrade head
```
Expected: 无报错；`0064_qref_external_ingestion` 应用成功。

- [ ] **Step 5: 写模型 round-trip 测试**

```python
# server/tests/test_qref_import_job.py
import pytest

pytestmark = pytest.mark.mysql


def test_models_round_trip(monkeypatch):
    from server.app.modules.quality_reference.models import (
        QualityReferenceImage,
        QualityReferenceImageLink,
        QualityReferenceImportJob,
    )
    from server.tests.conftest import build_test_app

    test_app = build_test_app(monkeypatch)
    try:
        db = test_app.SessionLocal()
        try:
            img = QualityReferenceImage(
                sha256="a" * 64, minio_key=f"{'a' * 64}.jpg",
                bucket="geo-qref-images", mime_type="image/jpeg", size=123,
            )
            db.add(img)
            db.flush()
            job = QualityReferenceImportJob(
                job_id="job123", status="pending", title="t",
                markdown="![x](u)", source_url="https://e/x",
            )
            db.add(job)
            db.commit()
            assert db.query(QualityReferenceImage).filter_by(sha256="a" * 64).one().size == 123
            assert db.query(QualityReferenceImportJob).filter_by(job_id="job123").one().status == "pending"
        finally:
            db.close()
    finally:
        test_app.cleanup()
```

> 注：`build_test_app` 的 import 路径以本仓库 `server/tests/conftest.py` 实际导出为准；若 conftest 以 fixture 暴露则改用 fixture。先跑 `pytest server/tests/test_qref_import_job.py -v` 若 import 失败，`grep -n "def build_test_app" server/tests/conftest.py` 校正。

- [ ] **Step 6: 跑测试确认通过**

Run: `pytest server/tests/test_qref_import_job.py::test_models_round_trip -v`
Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add server/app/modules/quality_reference/models.py server/alembic/versions/0064_qref_external_ingestion.py server/tests/test_qref_import_job.py
git commit -m "feat(qref): 外部参考异步入库三张表（图片/关联/job）+ 迁移 0064"
```

---

## Task 3: `fetch.py` — 外链图下载 + SSRF 校验

**Files:**
- Create: `server/app/modules/quality_reference/fetch.py`
- Test: `server/tests/test_qref_fetch_ssrf.py`

**Interfaces:**
- Produces:
  - `class ImageFetchError(ClientError)`；`class SsrfBlockedError(ImageFetchError)`。
  - `download_image(url: str, *, timeout_connect: float = 5.0, timeout_read: float = 15.0, max_bytes: int = 20 * 1024 * 1024, max_redirects: int = 3) -> tuple[bytes, str]`（返回 `(data, mime)`；任何拒绝/失败抛 `ImageFetchError` 子类）。
  - `assert_public_host(host: str) -> None`（解析并校验，命中私网抛 `SsrfBlockedError`；单独导出供测试）。

- [ ] **Step 1: 写失败测试**

```python
# server/tests/test_qref_fetch_ssrf.py
import pytest

from server.app.modules.quality_reference import fetch


@pytest.mark.parametrize("host_ip", ["127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "::1", "fd00::1"])
def test_private_and_metadata_hosts_blocked(monkeypatch, host_ip):
    monkeypatch.setattr(
        fetch.socket, "getaddrinfo",
        lambda *a, **k: [(None, None, None, "", (host_ip, 0))],
    )
    with pytest.raises(fetch.SsrfBlockedError):
        fetch.assert_public_host("evil.example")


def test_non_http_scheme_blocked():
    with pytest.raises(fetch.SsrfBlockedError):
        fetch.download_image("file:///etc/passwd")


def test_public_host_passes(monkeypatch):
    monkeypatch.setattr(
        fetch.socket, "getaddrinfo",
        lambda *a, **k: [(None, None, None, "", ("93.184.216.34", 0))],
    )
    fetch.assert_public_host("example.com")  # 不抛即通过
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest server/tests/test_qref_fetch_ssrf.py -v`
Expected: FAIL —— `ModuleNotFoundError: server.app.modules.quality_reference.fetch`。

- [ ] **Step 3: 写 `fetch.py`**

```python
"""外链图下载 + SSRF 校验。

SSRF 档位（见 plan Global Constraints）：scheme 白名单 + 解析所有 A/AAAA、任一命中
私网/环回/link-local/保留/元数据即拒 + 逐跳重定向重校验 + max_bytes + 短超时。
IP 钉连接抗 DNS 重绑定属未来工作（运营喂真 URL、威胁低）。
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlparse

from server.app.shared.errors import ClientError


class ImageFetchError(ClientError):
    """图片下载失败（超时/超限/网络错/非图）。worker 计 skipped、剔除该图节点。"""


class SsrfBlockedError(ImageFetchError):
    """目标解析到私网/环回/元数据地址，或 scheme 不允许 → 拒绝。"""


def assert_public_host(host: str) -> None:
    """解析 host 的所有地址，任一为私网/环回/link-local/保留/元数据即拒。"""
    if not host:
        raise SsrfBlockedError("empty host")
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise ImageFetchError(f"DNS 解析失败: {host}: {exc}") from exc
    for info in infos:
        addr = info[4][0]
        ip = ipaddress.ip_address(addr)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local  # 含 169.254.169.254 云元数据
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise SsrfBlockedError(f"目标地址不允许（私网/环回/元数据）: {host} → {addr}")


def download_image(
    url: str,
    *,
    timeout_connect: float = 5.0,
    timeout_read: float = 15.0,
    max_bytes: int = 20 * 1024 * 1024,
    max_redirects: int = 3,
) -> tuple[bytes, str]:
    """下载一张外链图，返回 (data, mime)。手动跟随重定向、逐跳重校验 host。"""
    import httpx

    timeout = httpx.Timeout(
        connect=timeout_connect, read=timeout_read, write=timeout_read, pool=timeout_connect
    )
    current = url
    with httpx.Client(follow_redirects=False, timeout=timeout) as client:
        for _ in range(max_redirects + 1):
            parsed = urlparse(current)
            if parsed.scheme not in ("http", "https"):
                raise SsrfBlockedError(f"scheme 不允许: {parsed.scheme or '(none)'}")
            assert_public_host(parsed.hostname or "")
            try:
                with client.stream("GET", current) as resp:
                    if resp.is_redirect:
                        loc = resp.headers.get("location")
                        if not loc:
                            raise ImageFetchError("重定向缺 Location")
                        current = urljoin(current, loc)
                        continue
                    resp.raise_for_status()
                    chunks = bytearray()
                    for chunk in resp.iter_bytes():
                        chunks += chunk
                        if len(chunks) > max_bytes:
                            raise ImageFetchError(f"图片超过 {max_bytes} 字节上限")
                    data = bytes(chunks)
            except httpx.HTTPError as exc:
                raise ImageFetchError(f"下载失败: {exc}") from exc
            mime = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
            if not mime.startswith("image/"):
                mime = _sniff_mime(data)
                if mime is None:
                    raise ImageFetchError(f"非图片内容: content-type={resp.headers.get('content-type')}")
            return data, mime
    raise ImageFetchError(f"重定向超过 {max_redirects} 跳")


def _sniff_mime(data: bytes) -> str | None:
    """content-type 缺失/非 image 时按魔数兜底判 mime。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:2] == b"\xff\xd8":
        return "image/jpeg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest server/tests/test_qref_fetch_ssrf.py -v`
Expected: PASS（私网/元数据/非 http scheme 被拒；公网 host 通过）。

- [ ] **Step 5: 提交**

```bash
git add server/app/modules/quality_reference/fetch.py server/tests/test_qref_fetch_ssrf.py
git commit -m "feat(qref): 外链图下载 + SSRF 校验（私网/元数据拒绝、逐跳重定向重校验）"
```

---

## Task 4: `image_store.py` — MinIO 存取 + sha256 去重 + 内链 + 孤儿查询

**Files:**
- Create: `server/app/modules/quality_reference/image_store.py`
- Test: `server/tests/test_qref_image_store.py`

**Interfaces:**
- Consumes: `image_library.store`（`ensure_bucket`/`upload_image`/`get_object_bytes`）、`articles.store.guess_image_size`、Task 2 的 `QualityReferenceImage` / `QualityReferenceImageLink` / `QualityReference`。
- Produces:
  - `QREF_BUCKET = "geo-qref-images"`
  - `ensure_qref_bucket() -> None`
  - `ingest_image(db, data: bytes, mime: str) -> QualityReferenceImage`（算 sha256 → 命中复用、未命中传 MinIO+建行；**不 commit**，由调用方控制事务）
  - `internal_url(image_id: int) -> str`（返回 `/api/quality-reference/images/{image_id}`）
  - `read_image_bytes(db, image_id: int) -> tuple[bytes, str]`（供代理端点：返回 `(data, mime)`）
  - `find_orphan_reference_images(db) -> list[int]`（无任何 active reference 关联的 image id）

- [ ] **Step 1: 写失败测试（mock MinIO）**

```python
# server/tests/test_qref_image_store.py
import pytest

pytestmark = pytest.mark.mysql


def test_ingest_dedups_by_sha256(monkeypatch):
    from server.app.modules.quality_reference import image_store
    from server.app.modules.quality_reference.models import QualityReferenceImage
    from server.tests.conftest import build_test_app

    uploads: list[tuple] = []
    monkeypatch.setattr(image_store.image_store_lib, "ensure_bucket", lambda b: None)
    monkeypatch.setattr(
        image_store.image_store_lib, "upload_image",
        lambda bucket, key, data, content_type: uploads.append((bucket, key)),
    )

    test_app = build_test_app(monkeypatch)
    try:
        db = test_app.SessionLocal()
        try:
            png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
            first = image_store.ingest_image(db, png, "image/png")
            db.commit()
            second = image_store.ingest_image(db, png, "image/png")
            db.commit()
            assert first.id == second.id  # 同图第二次复用同行
            assert db.query(QualityReferenceImage).count() == 1
            assert len(uploads) == 1  # 只上传一次
            assert first.minio_key.endswith(".png")
        finally:
            db.close()
    finally:
        test_app.cleanup()


def test_internal_url_shape():
    from server.app.modules.quality_reference import image_store

    assert image_store.internal_url(7) == "/api/quality-reference/images/7"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest server/tests/test_qref_image_store.py -v`
Expected: FAIL —— 模块不存在。

- [ ] **Step 3: 写 `image_store.py`**

```python
"""qref 图片的 MinIO 存取 + sha256 去重建行 + 站内内链 + 孤儿查询。

图片跨篇共享：同 sha256 只存一份、只建一行（UNIQUE(sha256) 兜底并发）。归属由
quality_reference_image_link 表表达；孤儿 = 无任何 active reference 关联的 image。
"""

from __future__ import annotations

import hashlib

from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from server.app.modules.articles.store import guess_image_size
from server.app.modules.image_library import store as image_store_lib
from server.app.modules.quality_reference.models import (
    QualityReference,
    QualityReferenceImage,
    QualityReferenceImageLink,
)
from server.app.shared.errors import ClientError

QREF_BUCKET = "geo-qref-images"

_MIME_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def ensure_qref_bucket() -> None:
    image_store_lib.ensure_bucket(QREF_BUCKET)


def internal_url(image_id: int) -> str:
    return f"/api/quality-reference/images/{image_id}"


def ingest_image(db: Session, data: bytes, mime: str) -> QualityReferenceImage:
    """算 sha256 → 命中复用、未命中传 MinIO 专桶 + 建行。不 commit（调用方控事务）。"""
    sha = hashlib.sha256(data).hexdigest()
    existing = db.query(QualityReferenceImage).filter_by(sha256=sha).first()
    if existing is not None:
        return existing

    ext = _MIME_EXT.get(mime, ".jpg")
    key = f"{sha}{ext}"
    ensure_qref_bucket()
    image_store_lib.upload_image(QREF_BUCKET, key, data, mime)
    width, height = guess_image_size(data)
    img = QualityReferenceImage(
        sha256=sha, minio_key=key, bucket=QREF_BUCKET, mime_type=mime,
        size=len(data), width=width, height=height,
    )
    try:
        db.add(img)
        db.flush()
        return img
    except IntegrityError:
        db.rollback()  # 并发已插同 sha256 → 回滚重查
        again = db.query(QualityReferenceImage).filter_by(sha256=sha).first()
        if again is not None:
            return again
        raise


def read_image_bytes(db: Session, image_id: int) -> tuple[bytes, str]:
    """代理端点用：按 id 读 MinIO 字节 + mime。不接受任意 key，避免越权读桶。"""
    img = db.get(QualityReferenceImage, image_id)
    if img is None:
        raise ClientError(f"quality_reference_image not found: {image_id}")
    data = image_store_lib.get_object_bytes(img.bucket, img.minio_key)
    return data, img.mime_type


def find_orphan_reference_images(db: Session) -> list[int]:
    """无任何 active reference 关联的 image id（供手动/周期孤儿清理；v1 不自动 GC）。"""
    stmt = select(QualityReferenceImage.id).where(
        ~exists(
            select(QualityReferenceImageLink.id)
            .join(QualityReference, QualityReference.id == QualityReferenceImageLink.reference_id)
            .where(
                QualityReferenceImageLink.image_id == QualityReferenceImage.id,
                QualityReference.is_active == True,  # noqa: E712
            )
        )
    )
    return list(db.execute(stmt).scalars().all())
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest server/tests/test_qref_image_store.py -v`
Expected: PASS（去重只建一行、只上传一次；内链形状正确）。

- [ ] **Step 5: 提交**

```bash
git add server/app/modules/quality_reference/image_store.py server/tests/test_qref_image_store.py
git commit -m "feat(qref): 图片 MinIO 专桶存取 + sha256 去重共享 + 内链 + 孤儿查询"
```

---

## Task 5: `import_job.py` + 请求 schema — job 生命周期 + 后台 worker

**Files:**
- Modify: `server/app/modules/quality_reference/schemas.py`（追加 2 个 schema）
- Create: `server/app/modules/quality_reference/import_job.py`
- Test: `server/tests/test_qref_import_job.py`（追加 worker 全流程用例）

**Interfaces:**
- Consumes: Task 1 converter（经 `import_external` 间接用）、Task 3 `fetch.download_image`、Task 4 `image_store.ingest_image/internal_url`、`service.import_external`、`bg_session_factory`。
- Produces:
  - schema `ImportExternalReferenceRequest`：`title:str`、`markdown:str`、`source_url:str`（必填）、`category:str|None`、`question_texts:list|None`、`platform:str|None`。
  - schema `ImportJobStatus`（响应用）。
  - `bg_session_factory`（模块变量，`create_app` 注入）。
  - `_IMPORT_SEMAPHORE = BoundedSemaphore(GEO_QREF_IMPORT_MAX_CONCURRENT，默认 3)`（并发闸护连接池；`run_import_job` 抢闸后再开 session）。
  - `create_import_job(db, req: ImportExternalReferenceRequest) -> QualityReferenceImportJob`（校验 + 插 pending + commit，秒回）。
  - `spawn_import_job(job_id: str) -> None`（daemon 线程）。
  - `run_import_job(job_id: str, session_factory) -> None`（worker）。
  - `get_import_job(db, job_id: str) -> QualityReferenceImportJob | None`。
  - `extract_image_urls(markdown: str) -> list[str]`、`rewrite_image_urls(markdown: str, mapping: dict[str, str]) -> str`（纯函数，单独可测）。

- [ ] **Step 1: 追加请求/响应 schema 到 `schemas.py`**

```python
class ImportExternalReferenceRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    markdown: str = Field(min_length=1)
    source_url: str = Field(min_length=1, max_length=1000)  # 必填：每条可溯源
    category: str | None = Field(default=None, max_length=200)
    question_texts: list | None = None
    platform: str | None = Field(default=None, max_length=100)


class ImportJobStatus(BaseModel):
    job_id: str
    status: str
    progress: float
    reference_id: int | None = None
    images_total: int = 0
    images_rehosted: int = 0
    images_skipped: int = 0
    error: str | None = None

    class Config:
        from_attributes = True
```

- [ ] **Step 2: 写纯函数失败测试（URL 抽取/改写）**

```python
# 追加到 server/tests/test_qref_import_job.py（顶部已有 pytestmark = pytest.mark.mysql；
# 纯函数用例不需要 DB，但同文件共享 marker 无妨——它们不建 app）
def test_extract_image_urls():
    from server.app.modules.quality_reference import import_job

    md = "a\n\n![x](http://h/1.png)\n\ntext ![y](https://h/2.jpg \"t\") end"
    assert import_job.extract_image_urls(md) == ["http://h/1.png", "https://h/2.jpg"]


def test_rewrite_image_urls():
    from server.app.modules.quality_reference import import_job

    md = "![x](http://h/1.png) and ![y](http://h/2.png)"
    out = import_job.rewrite_image_urls(
        md, {"http://h/1.png": "/api/quality-reference/images/1"}
    )
    assert "/api/quality-reference/images/1" in out
    assert "http://h/2.png" in out  # 未映射的原样保留
```

- [ ] **Step 3: 写 `import_job.py`**

```python
"""qref 外部参考异步导入：job 生命周期 + 后台 worker（镜像 video/service.py）。

建 job 秒回（不受 MCP 30s 约束）→ daemon 线程自开 session 跑：抠图 URL → 逐图
下载(SSRF+超时)+去重入 MinIO → 改写 markdown 为内链 → import_external 落库 → 写
image_link → done。事务顺序见 plan Global Constraints，避开 _insert_idempotent 回滚。
"""

from __future__ import annotations

import logging
import os
import re
import threading
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from server.app.modules.quality_reference import image_store, service
from server.app.modules.quality_reference.fetch import ImageFetchError, download_image
from server.app.modules.quality_reference.models import (
    QualityReferenceImageLink,
    QualityReferenceImportJob,
)
from server.app.modules.quality_reference.schemas import ImportExternalReferenceRequest
from server.app.shared.errors import ClientError, ValidationError

logger = logging.getLogger(__name__)

bg_session_factory: Callable[[], Any] | None = None

_OPERATOR_USER_ID = 1  # Loop 身份（admin），added_by_user_id 可空、admin 兜底

# 并发闸（护连接池）：同时至多 N 个导入 job 真跑，其余 daemon 线程 park 排队、不占 DB
# 连接。仿 pipelines 的 GEO_PIPELINE_MAX_CONCURRENT_RUNS；非互斥锁，只限吞吐不串行化。
_MAX_CONCURRENT = int(os.environ.get("GEO_QREF_IMPORT_MAX_CONCURRENT", "3"))
_IMPORT_SEMAPHORE = threading.BoundedSemaphore(_MAX_CONCURRENT)

# markdown 图片：![alt](url) 或 ![alt](url "title")；取 url token（到空白/右括号止）
_IMG_RE = re.compile(r"!\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+[\"'][^\"']*[\"'])?\s*\)")


def extract_image_urls(markdown: str) -> list[str]:
    """按出现顺序去重返回 markdown 内的图片 URL（仅 inline ![](url) 语法）。"""
    seen: dict[str, None] = {}
    for m in _IMG_RE.finditer(markdown or ""):
        seen.setdefault(m.group(1), None)
    return list(seen.keys())


def rewrite_image_urls(markdown: str, mapping: dict[str, str]) -> str:
    """把 markdown 里命中 mapping 的图片 URL 替换为内链；未命中原样保留。"""

    def _sub(m: re.Match) -> str:
        url = m.group(1)
        new = mapping.get(url)
        return m.group(0).replace(url, new) if new else m.group(0)

    return _IMG_RE.sub(_sub, markdown or "")


def create_import_job(db: Session, req: ImportExternalReferenceRequest) -> QualityReferenceImportJob:
    """校验 + 插 pending + commit，秒回。"""
    if not req.source_url.strip():
        raise ValidationError("source_url 必填")
    job = QualityReferenceImportJob(
        job_id=uuid.uuid4().hex,
        status="pending",
        progress=0.0,
        title=req.title,
        markdown=req.markdown,
        platform=req.platform,
        source_url=req.source_url,
        category=req.category,
        question_texts=req.question_texts,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def get_import_job(db: Session, job_id: str) -> QualityReferenceImportJob | None:
    return db.query(QualityReferenceImportJob).filter_by(job_id=job_id).first()


def _link_image(db: Session, reference_id: int, image_id: int) -> None:
    """幂等建 reference↔image 关联（UNIQUE(reference_id,image_id) 兜底）。"""
    exists_row = (
        db.query(QualityReferenceImageLink)
        .filter_by(reference_id=reference_id, image_id=image_id)
        .first()
    )
    if exists_row is not None:
        return
    db.add(QualityReferenceImageLink(reference_id=reference_id, image_id=image_id))
    try:
        db.flush()
    except IntegrityError:
        db.rollback()  # 并发已建同关联 → 忽略


def run_import_job(job_id: str, session_factory) -> None:
    """后台线程入口：先抢并发闸 → 自开 session 跑整条 pipeline。异常兜底写 failed。

    抢闸在开 session 之前：排队线程只被 park、不占 DB 连接（护连接池）。job 行此时仍是
    pending，轮询看到 pending→running→done，语义正确。释放在 finally，与 db.close() 同处。
    """
    _IMPORT_SEMAPHORE.acquire()
    db = session_factory()
    try:
        job = db.query(QualityReferenceImportJob).filter_by(job_id=job_id).one()
        job.status = "running"
        db.commit()

        urls = extract_image_urls(job.markdown)
        job.images_total = len(urls)
        db.commit()

        # ── 阶段 A：逐图下载+去重入库，各自 commit（先落地，避免后续回滚冲掉）──
        mapping: dict[str, str] = {}
        image_ids: list[int] = []
        rehosted = skipped = 0
        for i, url in enumerate(urls):
            try:
                data, mime = download_image(url)
                img = image_store.ingest_image(db, data, mime)
                db.commit()
                mapping[url] = image_store.internal_url(img.id)
                image_ids.append(img.id)
                rehosted += 1
            except ImageFetchError as exc:
                db.rollback()
                skipped += 1
                logger.warning("qref 图片跳过 job=%s url=%s: %s", job_id, url, exc)
            job.images_rehosted = rehosted
            job.images_skipped = skipped
            job.progress = round((i + 1) / (len(urls) + 1), 3) if urls else 0.5
            db.commit()

        # ── 阶段 B：改写 markdown → import_external 落库 + commit ──
        rewritten = rewrite_image_urls(job.markdown, mapping)
        ref, _similar = service.import_external(
            db,
            user_id=_OPERATOR_USER_ID,
            title=job.title,
            category=job.category,
            source_url=job.source_url,
            platform=job.platform,
            question_texts=job.question_texts,
            markdown=rewritten,
        )
        db.commit()  # import_external 只 flush，这里提交（dup 复活也返回有效 ref）

        # ── 阶段 C：写 image_link（含 dup 复活场景，关联挂到已有 ref）+ commit ──
        for image_id in image_ids:
            _link_image(db, ref.id, image_id)
        job.reference_id = ref.id
        job.progress = 1.0
        job.status = "done"
        db.commit()
    except Exception as exc:  # noqa: BLE001 — 后台线程兜底
        logger.exception("qref 外部参考导入失败: job_id=%s", job_id)
        db.rollback()
        try:
            job = db.query(QualityReferenceImportJob).filter_by(job_id=job_id).one()
            job.status = "failed"
            job.error = f"{type(exc).__name__}: {str(exc)[:500]}"
            db.commit()
        except Exception:
            logger.exception("写 failed 状态也失败: job_id=%s", job_id)
    finally:
        db.close()
        _IMPORT_SEMAPHORE.release()


def spawn_import_job(job_id: str) -> None:
    if bg_session_factory is None:
        raise ClientError("bg_session_factory 未注入（create_app 未执行？）")
    factory = bg_session_factory
    threading.Thread(target=run_import_job, args=(job_id, factory), daemon=True).start()
```

- [ ] **Step 4: 跑纯函数测试确认通过**

Run: `pytest server/tests/test_qref_import_job.py -v -k "extract or rewrite"`
Expected: PASS。

- [ ] **Step 5: 写 worker 全流程测试（mock download + mock MinIO）**

```python
def test_run_import_job_full_flow(monkeypatch):
    from server.app.modules.quality_reference import image_store, import_job
    from server.app.modules.quality_reference.models import (
        QualityReference,
        QualityReferenceImageLink,
        QualityReferenceImportJob,
    )
    from server.app.modules.quality_reference.schemas import ImportExternalReferenceRequest
    from server.tests.conftest import build_test_app

    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
    # mock 下载：1.png 成功、bad.png 抛 ImageFetchError（走 skipped）
    def fake_download(url, **kw):
        if "bad" in url:
            raise import_job.ImageFetchError("boom")
        return png, "image/png"

    monkeypatch.setattr(import_job, "download_image", fake_download)
    monkeypatch.setattr(image_store.image_store_lib, "ensure_bucket", lambda b: None)
    monkeypatch.setattr(
        image_store.image_store_lib, "upload_image",
        lambda *a, **k: None,
    )

    test_app = build_test_app(monkeypatch)
    try:
        import_job.bg_session_factory = test_app.SessionLocal
        db = test_app.SessionLocal()
        try:
            req = ImportExternalReferenceRequest(
                title="站外真品",
                markdown="导语\n\n![ok](http://h/1.png)\n\n![x](http://h/bad.png)",
                source_url="https://ext.example/post/1",
                category="测评",
            )
            job = import_job.create_import_job(db, req)
            job_id = job.job_id
        finally:
            db.close()

        # 同步跑 worker（不 spawn 线程，直接调，便于断言）
        import_job.run_import_job(job_id, test_app.SessionLocal)

        db = test_app.SessionLocal()
        try:
            done = db.query(QualityReferenceImportJob).filter_by(job_id=job_id).one()
            assert done.status == "done"
            assert done.images_total == 2
            assert done.images_rehosted == 1
            assert done.images_skipped == 1
            assert done.reference_id is not None
            ref = db.get(QualityReference, done.reference_id)
            assert ref.origin == "external"
            assert ref.is_active is True
            assert ref.source_url == "https://ext.example/post/1"
            # 正文 content_json 含内链 image 节点、外链已改写
            assert "/api/quality-reference/images/" in ref.content_json
            assert "http://h/1.png" not in ref.content_json
            # image_link 挂到该 ref（成功那张）
            links = db.query(QualityReferenceImageLink).filter_by(reference_id=ref.id).all()
            assert len(links) == 1
        finally:
            db.close()
    finally:
        test_app.cleanup()
```

- [ ] **Step 6: 跑 worker 测试确认通过**

Run: `pytest server/tests/test_qref_import_job.py::test_run_import_job_full_flow -v`
Expected: PASS（pending→done、统计正确、外链改内链、部分 skipped 不判失败、link 落库）。

- [ ] **Step 6b: 写并发闸测试（信号量护连接池）**

```python
import time
import threading


def test_import_jobs_respect_concurrency_bound(monkeypatch):
    from server.app.modules.quality_reference import image_store, import_job
    from server.app.modules.quality_reference.schemas import ImportExternalReferenceRequest
    from server.tests.conftest import build_test_app

    # 收紧到 2 并发，便于断言（覆盖模块级信号量）
    monkeypatch.setattr(import_job, "_IMPORT_SEMAPHORE", threading.BoundedSemaphore(2))

    inside = 0
    peak = 0
    lock = threading.Lock()
    gate = threading.Event()
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40

    def blocking_download(url, **kw):
        nonlocal inside, peak
        with lock:
            inside += 1
            peak = max(peak, inside)
        gate.wait(timeout=5)  # 卡住制造重叠
        with lock:
            inside -= 1
        return png, "image/png"

    monkeypatch.setattr(import_job, "download_image", blocking_download)
    monkeypatch.setattr(image_store.image_store_lib, "ensure_bucket", lambda b: None)
    monkeypatch.setattr(image_store.image_store_lib, "upload_image", lambda *a, **k: None)

    test_app = build_test_app(monkeypatch)
    try:
        import_job.bg_session_factory = test_app.SessionLocal
        db = test_app.SessionLocal()
        job_ids = []
        try:
            for i in range(4):
                req = ImportExternalReferenceRequest(
                    title=f"t{i}",
                    markdown=f"![a](http://h/{i}.png)",
                    source_url=f"https://e/{i}",
                )
                job_ids.append(import_job.create_import_job(db, req).job_id)
        finally:
            db.close()

        threads = [
            threading.Thread(target=import_job.run_import_job, args=(jid, test_app.SessionLocal))
            for jid in job_ids
        ]
        for t in threads:
            t.start()
        time.sleep(0.5)  # 给线程抢闸并卡在 download
        assert peak <= 2, f"并发闸失效：峰值 {peak} > 2"
        gate.set()  # 放行跑完
        for t in threads:
            t.join(timeout=10)
    finally:
        test_app.cleanup()
```

- [ ] **Step 6c: 跑并发闸测试确认通过**

Run: `pytest server/tests/test_qref_import_job.py::test_import_jobs_respect_concurrency_bound -v`
Expected: PASS（4 个 job 同发，任一时刻至多 2 个进到 download）。

- [ ] **Step 7: 提交**

```bash
git add server/app/modules/quality_reference/schemas.py server/app/modules/quality_reference/import_job.py server/tests/test_qref_import_job.py
git commit -m "feat(qref): 外部参考异步导入 job + worker + 并发闸（下载去重/改内链/import_external/link）"
```

---

## Task 6: 两个 router + main.py 接线

**Files:**
- Create: `server/app/modules/quality_reference/import_router.py`
- Create: `server/app/modules/quality_reference/images_router.py`
- Modify: `server/app/main.py`（import + 挂载 + 注入 bg_session_factory）
- Test: `server/tests/test_qref_import_endpoints.py`

**Interfaces:**
- Consumes: Task 4 `image_store.read_image_bytes`、Task 5 `import_job.*`、`require_mcp_token`、`mcp_exception_response`。
- Produces（HTTP 端点，均在 `prefix="/api"` 下）：
  - `POST /quality-reference/import-external`（MCP token，202）→ `{ok,data:ImportJobStatus,error}`
  - `GET /quality-reference/import-jobs/{job_id}`（MCP token）→ `{ok,data:ImportJobStatus,error}`
  - `GET /quality-reference/images/{image_id}`（公开）→ `Response(bytes)`

- [ ] **Step 1: 写 `import_router.py`（MCP token）**

```python
"""qref 外部参考异步导入 MCP 端点：POST /import-external（建 job）+ GET /import-jobs/{id}（轮询）。

走 MCP token 鉴权（与 user JWT 隔离），供 Claude Code 侧 import_external_reference 工具调用。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from server.app.core.mcp_auth import require_mcp_token
from server.app.core.mcp_errors import mcp_exception_response
from server.app.db.session import get_db
from server.app.modules.quality_reference import import_job as ij
from server.app.modules.quality_reference.schemas import (
    ImportExternalReferenceRequest,
    ImportJobStatus,
)
from server.app.shared.errors import ClientError, ValidationError

quality_reference_import_router = APIRouter(dependencies=[Depends(require_mcp_token)])


@quality_reference_import_router.post("/quality-reference/import-external", status_code=202)
def import_external_reference(
    req: ImportExternalReferenceRequest, db: Session = Depends(get_db)
) -> dict:
    """[MCP] 建异步导入 job + 起后台线程。202 立即返回 job_id（规避 MCP 30s 超时）。"""
    try:
        job = ij.create_import_job(db, req)
    except (ValidationError, ClientError):
        raise
    except HTTPException:
        raise
    except Exception as exc:
        raise mcp_exception_response(exc, context="create_import_job") from exc
    ij.spawn_import_job(job.job_id)
    return {"ok": True, "data": ImportJobStatus.model_validate(job).model_dump(), "error": None}


@quality_reference_import_router.get("/quality-reference/import-jobs/{job_id}")
def get_external_reference_status(job_id: str, db: Session = Depends(get_db)) -> dict:
    """[MCP] 查导入 job 状态 + 统计。"""
    job = ij.get_import_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="导入任务不存在")
    return {"ok": True, "data": ImportJobStatus.model_validate(job).model_dump(), "error": None}
```

- [ ] **Step 2: 写 `images_router.py`（公开只读代理）**

```python
"""qref 图片公开只读代理：GET /images/{id}。仿 stock-images/{id}/file。

嵌入参考正文的内链需公开可访问（reader 里 <img src> 由浏览器直接拉）。只按 id 读
MinIO 字节、不接受任意 key，避免越权读桶（见 plan §9）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from server.app.db.session import get_db
from server.app.modules.quality_reference import image_store

quality_reference_images_router = APIRouter()


@quality_reference_images_router.get("/quality-reference/images/{image_id}")
def serve_qref_image(image_id: int, db: Session = Depends(get_db)) -> Response:
    try:
        data, mime = image_store.read_image_bytes(db, image_id)
    except Exception:
        raise HTTPException(status_code=404, detail="图片不存在") from None
    return Response(content=data, media_type=mime or "image/jpeg")
```

- [ ] **Step 3: main.py 接线——import**

在 `server/app/main.py` 顶部 import 区（`from server.app.modules.quality_reference.router import quality_reference_router` 附近，约 84 行）追加：

```python
from server.app.modules.quality_reference.import_router import quality_reference_import_router
from server.app.modules.quality_reference.images_router import quality_reference_images_router
```

- [ ] **Step 4: main.py 接线——挂载两个 router**

在现有 `quality_reference_mcp_router` 挂载块之后（约 304 行）追加：

```python
    # qref 外部参考异步导入（MCP token）：POST /import-external + GET /import-jobs/{id}
    app.include_router(
        quality_reference_import_router,
        prefix="/api",
        tags=["quality-reference-mcp"],
    )
    # qref 图片公开只读代理：GET /images/{id}（内链渲染用，无鉴权，仿 stock-images）
    app.include_router(
        quality_reference_images_router,
        prefix="/api",
        tags=["quality-reference-images"],
    )
```

- [ ] **Step 5: main.py 接线——注入 bg_session_factory**

在现有 `_video_service.bg_session_factory = SessionLocal`（约 448 行）之后追加：

```python
    # qref 外部参考导入后台线程（spawn_import_job 读 import_job 里这个变量）
    import server.app.modules.quality_reference.import_job as _qref_import_job

    _qref_import_job.bg_session_factory = SessionLocal
```

- [ ] **Step 6: 写端点测试**

```python
# server/tests/test_qref_import_endpoints.py
import pytest

pytestmark = pytest.mark.mysql


def test_import_endpoint_requires_mcp_token(monkeypatch):
    from server.tests.conftest import build_test_app

    test_app = build_test_app(monkeypatch)
    try:
        # 无 MCP token → 401
        resp = test_app.client.post(
            "/api/quality-reference/import-external",
            json={"title": "t", "markdown": "x", "source_url": "https://e/1"},
        )
        assert resp.status_code == 401
    finally:
        test_app.cleanup()


def test_import_endpoint_creates_job_and_polls(monkeypatch):
    from server.app.modules.quality_reference import import_job
    from server.tests.conftest import build_test_app

    # 不真正跑 worker：spawn 置空，仅验证建 job + 轮询
    monkeypatch.setattr(import_job, "spawn_import_job", lambda job_id: None)

    test_app = build_test_app(monkeypatch)
    try:
        headers = {"X-MCP-Token": test_app.mcp_token}  # 以 conftest 实际暴露的 token 为准
        resp = test_app.client.post(
            "/api/quality-reference/import-external",
            json={"title": "t", "markdown": "![a](http://h/1.png)", "source_url": "https://e/1"},
            headers=headers,
        )
        assert resp.status_code == 202
        job_id = resp.json()["data"]["job_id"]
        assert resp.json()["data"]["status"] == "pending"

        poll = test_app.client.get(
            f"/api/quality-reference/import-jobs/{job_id}", headers=headers
        )
        assert poll.status_code == 200
        assert poll.json()["data"]["job_id"] == job_id
    finally:
        test_app.cleanup()


def test_image_proxy_serves_bytes(monkeypatch):
    from server.app.modules.quality_reference import image_store
    from server.app.modules.quality_reference.models import QualityReferenceImage
    from server.tests.conftest import build_test_app

    monkeypatch.setattr(
        image_store.image_store_lib, "get_object_bytes",
        lambda bucket, key: b"\x89PNG\r\n\x1a\nDATA",
    )
    test_app = build_test_app(monkeypatch)
    try:
        db = test_app.SessionLocal()
        try:
            img = QualityReferenceImage(
                sha256="b" * 64, minio_key=f"{'b' * 64}.png",
                bucket="geo-qref-images", mime_type="image/png", size=10,
            )
            db.add(img)
            db.commit()
            image_id = img.id
        finally:
            db.close()
        # 公开端点，无需 token
        resp = test_app.client.get(f"/api/quality-reference/images/{image_id}")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/png")
        assert resp.content == b"\x89PNG\r\n\x1a\nDATA"

        assert test_app.client.get("/api/quality-reference/images/999999").status_code == 404
    finally:
        test_app.cleanup()
```

> 注：`test_app.mcp_token` / `test_app.client` / `test_app.SessionLocal` 的确切属性名以 `server/tests/conftest.py` 的 `build_test_app` 实现为准。若 MCP token 走环境变量注入，改成 `monkeypatch.setenv("GEO_MCP_TOKEN", ...)` + 对应 header。跑测试前先 `grep -n "MCP_TOKEN\|def build_test_app\|self.client\|SessionLocal" server/tests/conftest.py` 校正。

- [ ] **Step 7: 跑端点测试确认通过**

Run: `pytest server/tests/test_qref_import_endpoints.py -v`
Expected: PASS（401 鉴权、202 建 job、轮询、图片代理返回字节 + 404）。

- [ ] **Step 8: 提交**

```bash
git add server/app/modules/quality_reference/import_router.py server/app/modules/quality_reference/images_router.py server/app/main.py server/tests/test_qref_import_endpoints.py
git commit -m "feat(qref): 外部参考导入 MCP 端点 + 图片公开代理 + main.py 接线"
```

---

## Task 7: 两个 MCP 工具 + MCP_TOOLS_COUNT

**Files:**
- Modify: `server/mcp/tools/action.py`（末尾追加 2 个工具）
- Modify: `server/app/modules/mcp_catalog/connect_router.py:27`（`31 → 33`）
- Test: `server/tests/test_mcp_tools_count.py`（若已有该守卫则更新断言；否则新增最小断言）

**Interfaces:**
- Consumes: Task 6 的两个 MCP 端点（`_apost`/`_aget`）。
- Produces（MCP 工具）：
  - `import_external_reference(title, markdown, source_url, category=None, question_texts=None, platform=None) -> dict`
  - `get_external_reference_status(job_id) -> dict`

- [ ] **Step 1: 追加两个工具到 `action.py`**

在 `server/mcp/tools/action.py` 末尾（`adopt_quality_reference` 之后）追加：

```python
@mcp.tool()
async def import_external_reference(
    title: str,
    markdown: str,
    source_url: str,
    category: str | None = None,
    question_texts: list[str] | None = None,
    platform: str | None = None,
) -> dict[str, Any]:
    """把爬到的**真·站外文章**异步导入高质量库的「外部参考」池（origin=external）。

    与 adopt_quality_reference（只收站内已审文章）互补：本工具**专收站外真品**——别人
    在其它平台写的真实内容，经 Crawl4AI 等爬虫抓成 markdown（含外链图）。落库即
    is_active=True 直接生效，作对抗判分的参考真品。

    **务必只灌真实站外内容**：source_url 必填、服务端盖 origin=external。不要拿 AI
    自产内容伪装成外部真品灌进来——那会毒化参考池、架空对抗判分。

    异步：本工具建 job 秒回 job_id（图片下载慢，规避 30s 超时）。之后轮询
    get_external_reference_status(job_id) 直到 status=done/failed。图片会被下载回传进
    站内专桶、正文改写为站内内链（不留外链）、跨篇 sha256 去重共享；单图抓不到会
    best-effort 跳过（计入 images_skipped，不判整体失败）。

    Args:
        title: 文章标题（1–300 字）。
        markdown: 正文 markdown（含 ![](外链图) 语法即可，图片会被自动 rehost）。
        source_url: 来源 URL（**必填**，保证可溯源）。
        category: 可选。问题类型（对应 QuestionItem.category），用于对抗判分按类目挑参考。
        question_texts: 可选。该类型下的问题词列表，与 category 配对。
        platform: 可选。来源平台名（如 "知乎" / "小红书"）。

    Returns:
        {"ok": True, "data": {"job_id": str, "status": "pending", ...}, "error": None}
        建 job 后轮询 get_external_reference_status(job_id)。
    """
    body: dict[str, Any] = {"title": title, "markdown": markdown, "source_url": source_url}
    if category:
        body["category"] = category
    if question_texts:
        body["question_texts"] = question_texts
    if platform:
        body["platform"] = platform
    return await _apost("/api/quality-reference/import-external", json=body)


@mcp.tool()
async def get_external_reference_status(job_id: str) -> dict[str, Any]:
    """轮询 import_external_reference 建的异步导入 job 状态。

    Args:
        job_id: import_external_reference 返回的 job_id。

    Returns:
        {"ok": True, "data": {"job_id": str, "status": "pending"|"running"|"done"|"failed",
         "progress": float, "reference_id": int|null, "images_total": int,
         "images_rehosted": int, "images_skipped": int, "error": str|null}, "error": None}
    """
    return await _aget(f"/api/quality-reference/import-jobs/{job_id}")
```

- [ ] **Step 2: 改 MCP_TOOLS_COUNT `31 → 33`**

`server/app/modules/mcp_catalog/connect_router.py:27`：

```python
MCP_TOOLS_COUNT = 33
```

同步更新该文件里工具分组注释 / 清单（若有列举 action 组工具名，把 `import_external_reference` / `get_external_reference_status` 加进 action 组，13 → 15）。

- [ ] **Step 3: 跑计数守卫测试**

Run: `pytest server/tests/ -q -k "mcp_tools_count or tools_count or connect"`
Expected: PASS。若无现成守卫且工具注册可离线断言，新增：

```python
# server/tests/test_mcp_tools_count.py（若不存在）
def test_new_qref_tools_registered():
    import server.mcp.tools.action as action  # 触发 @mcp.tool 注册
    from server.mcp.server import mcp

    names = set(getattr(mcp, "_tool_manager").list_tools_names()) if hasattr(mcp, "_tool_manager") else set()
    # 以本仓库 FastMCP 实际暴露注册表 API 为准；至少断言函数存在
    assert callable(action.import_external_reference.fn) or callable(action.import_external_reference)
    assert callable(action.get_external_reference_status.fn) or callable(action.get_external_reference_status)
```

> FastMCP 的工具注册表内省 API 以本仓库版本为准；若难以离线枚举，退化为"两个函数可 import 且 MCP_TOOLS_COUNT==33"即可。

- [ ] **Step 4: 提交**

```bash
git add server/mcp/tools/action.py server/app/modules/mcp_catalog/connect_router.py server/tests/test_mcp_tools_count.py
git commit -m "feat(mcp): import_external_reference/get_external_reference_status 工具 + 计数 31→33"
```

---

## Task 8: 全量校验 + lint

**Files:** 无新增；跑门禁。

- [ ] **Step 1: 后端 lint / format / mypy**

Run:
```bash
ruff check server/ && ruff format --check server/ && mypy server/app
```
Expected: 全绿。不绿就地修（不改行为）。

- [ ] **Step 2: 跑本期全部新测试**

Run:
```bash
pytest server/tests/test_qref_converter_img.py server/tests/test_qref_fetch_ssrf.py \
  server/tests/test_qref_image_store.py server/tests/test_qref_import_job.py \
  server/tests/test_qref_import_endpoints.py -q
```
Expected: 全绿。

- [ ] **Step 3: 迁移 up/down 往返自检**

Run:
```bash
GEO_DATABASE_URL="$GEO_TEST_DATABASE_URL" python -m alembic -c server/alembic.ini downgrade -1
GEO_DATABASE_URL="$GEO_TEST_DATABASE_URL" python -m alembic -c server/alembic.ini upgrade head
```
Expected: downgrade 删 3 表、upgrade 重建，无报错。

- [ ] **Step 4: 相关模块回归**

Run: `pytest server/tests/ -q -k "quality_reference or qref or converter or adversarial"`
Expected: 无回归。

- [ ] **Step 5: 提交（如有 lint 修补）**

```bash
git add -A
git commit -m "chore(qref): lint 修补 + 外部参考 MCP 入库全量校验通过"
```

---

## Self-Review

**1. Spec coverage（逐节核对 `docs/superpowers/specs/2026-07-16-external-reference-mcp-ingestion-design.md`）**

- §2 目标：MCP 入口异步导入 → Task 5/6/7；直接生效 is_active=True → Task 5 worker（`import_external` 默认 external+模型默认 is_active）；category/question_texts/platform/source_url(必填) → Task 5 schema + create_import_job 校验；图片 MinIO 专桶 + 内链 + sha256 去重 → Task 4；图文结构保留不吞图 → Task 1；异步 job + 单图超时 best-effort → Task 3/5。✅
- §5.1/5.2/5.3 三张表 → Task 2。✅
- §6 组件划分：image_store → Task 4；fetch → Task 3；import_job → Task 5；service.import_external 不改（Task 5 直接调）✅；routers/mcp + routers/images → Task 6（扁平命名 import_router/images_router，语义等价）；converter 补丁 → Task 1；action.py +2 工具 → Task 7；迁移 → Task 2。✅
- §7 converter img 顶层化 + 编辑器 30% 宽度 + assetId null + 单元测试（单图/交错/多图）→ Task 1（并补空段落剔除 + inline 保序）。✅
- §8 建 job 秒回 / 后台线程 / 单图超时 / 僵尸 job（v1 不做）→ Task 5（僵尸 sweep 归未来工作，与 spec 一致）。✅
- §9 安全：source_url 必填 + origin 服务端盖 + nh3（import_external 已有）+ sha256 去重 + SSRF（scheme/私网/元数据/重定向跳数/max_bytes/短超时）+ 内链只读按 id → Task 3 + Task 4 `read_image_bytes` + Task 6 代理。✅
- §10 错误处理：建 job 校验失败同步 4xx（Task 6 命名异常）；worker 失败 → failed+error（Task 5）；部分 skipped 不判失败（Task 5 测试断言）；content_hash 幂等复活 job 照样 done（Task 5 阶段 B/C 处理 dup ref 挂 link）。✅
- §11 测试策略：converter/image_store/fetch-SSRF/import_job/端点/MCP 工具/孤儿清理 → Task 1/4/3/5/6/7 + 孤儿 `find_orphan_reference_images`（Task 4 已实现，附最小断言可在 Task 4 补一条）。⚠️ 补充见下。

**2. Placeholder scan**：无 TBD/TODO；每步含真代码与预期输出。`build_test_app` 属性名（`.client`/`.SessionLocal`/`.mcp_token`）与 conftest 实际导出需实现者用 grep 校正——已在对应 Step 显式标注，非占位。

**3. Type consistency**：`ingest_image(db, data, mime) -> QualityReferenceImage`、`internal_url(image_id) -> str`、`download_image(url,...) -> tuple[bytes,str]`、`create_import_job/get_import_job/run_import_job/spawn_import_job` 跨 Task 5/6 一致；内链 `/api/quality-reference/images/{id}` 在 Task 4（生成）/Task 6（路由）/Task 7（工具路径）三处逐字一致；`QREF_BUCKET="geo-qref-images"` 单一来源。✅

**补丁（Self-Review 发现）**：孤儿清理 `find_orphan_reference_images` 已在 Task 4 实现但无用例。在 Task 4 Step 3 之后可加一条最小断言测试（建 image + 无 link → 命中孤儿；建 active ref + link → 不命中）。执行 Task 4 时一并补上（同一提交），避免 §11 "孤儿清理"无覆盖。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-16-external-reference-mcp-ingestion.md`. 两种执行方式：

1. **Subagent-Driven（推荐）** — 每个 Task 派一个新 subagent，Task 之间两段式 review，快速迭代。
2. **Inline Execution** — 本会话内用 executing-plans 批量执行 + checkpoint review。

选哪种?
