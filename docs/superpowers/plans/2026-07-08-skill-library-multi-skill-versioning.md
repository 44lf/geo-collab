# Skill 库多 skill × 多版本 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `loop_skills` 从「单 bundle 多版本」升级为「多 skill × 多版本」的 Skill 库：任意登录用户可上传自定义 skill、每个 skill 独立多版本 + 当前指针 + 回滚 + 删除，前端 Panel ⑤ 改造为 Skill 库工作台。

**Architecture:** 新增两表 `skills` + `skill_versions`（`storage_backend` 判别位隔离 DB/MinIO 存储），新 `/api/mcp/skills/*` 接口族，旧 `/loop-skill-bundle/*` 只读端点保留成指向官方 skill 的废弃别名。文件默认存 DB JSON、MinIO 后端建好不触发。一次性 seed 脚本把现有单 bundle 数据挂进官方 skill。

**Tech Stack:** FastAPI + SQLAlchemy 2.0（Mapped/mapped_column）+ Alembic（MySQL only）+ pytest（MySQL）；前端 React 19 + TypeScript strict + Vite（无单测框架，门禁=typecheck+build）。

## Global Constraints

- **MySQL only**：迁移/测试都跑真 MySQL；测试 DB 名必须含 `test`；测试用 `build_test_app(monkeypatch)` + `finally: cleanup()`。
- **FK/PK 用 `Integer`** 对齐 `users.id`（BIGINT→INT 触发 MySQL errno 150）。
- **service 层抛命名异常**：`ValidationError`（→400）、`ConflictError`（→409）；MCP 端点未捕获异常走 `core/mcp_errors.mcp_exception_response`。不抛裸 `ValueError`。
- **鉴权边界**：Web 端 `Depends(get_current_user)`；MCP 端 `Depends(require_mcp_token)`；admin 用 `Depends(require_admin)`。均在 `server/app/core/security.py` / `core/mcp_auth.py`。
- **上传约束**：单 skill 总解压 ≤ **5 MB**；含 ≥1 个 `SKILL.md`（任意层级）；同名 skill 追加新版本不覆盖。
- **存储不变式**：`storage_backend='db'` ⇒ `files` 非空 & `storage_key` 空；`='minio'` ⇒ 反之。上传本期一律写 `'db'`。
- **官方 skill**：`name="/goal loop skills"`、`slug="goal"`、`is_official=true`、`created_by=NULL`。
- **前端不算 SHA、不引 zip 库**：SHA 后端算；上传走 multipart（一个 `.zip` 或多文件数组）。
- **alembic 当前 head = `0057_merge_heads`**；新迁移 `revision="0058_skill_library"`、`down_revision="0057_merge_heads"`。
- **MCP 工具总数保持 25**（不新增 MCP 工具；`MCP_TOOLS_COUNT` 不改）。
- 提交信息末尾加 `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`。
- 设计依据：`docs/superpowers/specs/2026-07-08-skill-library-multi-skill-versioning-design.md`。

---

### Task 1: 两表 ORM 模型 + Alembic 迁移

**Files:**
- Modify: `server/app/modules/loop_skills/models.py`
- Create: `server/alembic/versions/0058_skill_library.py`
- Test: `server/tests/test_skill_library_models.py`

**Interfaces:**
- Produces: `Skill`（表 `skills`）、`SkillVersion`（表 `skill_versions`）ORM 类，字段见下。

- [ ] **Step 1: 写失败测试** — `server/tests/test_skill_library_models.py`

```python
import pytest

pytestmark = pytest.mark.mysql


def test_skill_and_version_roundtrip(monkeypatch):
    from server.tests.utils import build_test_app
    from server.app.modules.loop_skills.models import Skill, SkillVersion

    app = build_test_app(monkeypatch)
    try:
        from server.app.db.session import SessionLocal

        db = SessionLocal()
        try:
            sk = Skill(name="/goal loop skills", slug="goal", is_official=True, created_by=None)
            db.add(sk)
            db.flush()
            v = SkillVersion(
                skill_id=sk.id,
                version_label="v1",
                bundle_sha256="a" * 64,
                file_count=1,
                total_bytes=10,
                storage_backend="db",
                files=[{"path": "SKILL.md", "content": "x", "sha256": "a" * 64, "size": 1}],
                storage_key=None,
                uploaded_by=None,
            )
            db.add(v)
            db.flush()
            sk.current_version_id = v.id
            db.commit()

            got = db.get(Skill, sk.id)
            assert got.slug == "goal"
            assert got.current_version_id == v.id
            assert db.get(SkillVersion, v.id).files[0]["path"] == "SKILL.md"
        finally:
            db.close()
    finally:
        app.cleanup()


def test_name_active_unique_allows_reupload_after_softdelete(monkeypatch):
    """未删记录内 name 唯一；软删后可重新建同名（生成列落 NULL 不冲突）。"""
    from server.tests.utils import build_test_app
    from server.app.modules.loop_skills.models import Skill

    app = build_test_app(monkeypatch)
    try:
        from server.app.db.session import SessionLocal

        db = SessionLocal()
        try:
            a = Skill(name="dup", slug="dup", is_official=False, created_by=None, is_deleted=True)
            db.add(a)
            db.commit()
            # 软删的同名可再建活跃记录
            b = Skill(name="dup", slug="dup", is_official=False, created_by=None, is_deleted=False)
            db.add(b)
            db.commit()
            assert b.id != a.id
        finally:
            db.close()
    finally:
        app.cleanup()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest server/tests/test_skill_library_models.py -q`
Expected: FAIL（`ImportError: cannot import name 'Skill'`）

- [ ] **Step 3: 加模型** — 在 `server/app/modules/loop_skills/models.py` 末尾追加（保留现有 `LoopSkillBundleVersion` 不动）

```python
from sqlalchemy import UniqueConstraint  # 加到顶部 import


class Skill(Base):
    __tablename__ = "skills"
    __table_args__ = (
        UniqueConstraint("name_active", name="uq_skills_name_active"),
        UniqueConstraint("slug_active", name="uq_skills_slug_active"),
        Index("ix_skills_deleted", "is_deleted"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128))
    slug: Mapped[str] = mapped_column(String(128))
    is_official: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    current_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    # 生成列由迁移创建；ORM 只读映射，插入/更新时不写它（MySQL 自动算）
    name_active: Mapped[str | None] = mapped_column(
        String(128), Computed("CASE WHEN is_deleted THEN NULL ELSE name END"), nullable=True
    )
    slug_active: Mapped[str | None] = mapped_column(
        String(128), Computed("CASE WHEN is_deleted THEN NULL ELSE slug END"), nullable=True
    )


class SkillVersion(Base):
    __tablename__ = "skill_versions"
    __table_args__ = (
        UniqueConstraint("skill_id", "version_label", name="uq_skill_versions_label"),
        Index("ix_skill_versions_skill", "skill_id", "is_deleted"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    skill_id: Mapped[int] = mapped_column(ForeignKey("skills.id"))
    version_label: Mapped[str] = mapped_column(String(32))
    bundle_sha256: Mapped[str] = mapped_column(String(64))
    file_count: Mapped[int] = mapped_column(Integer)
    total_bytes: Mapped[int] = mapped_column(Integer)
    storage_backend: Mapped[str] = mapped_column(String(8), default="db", nullable=False)
    files: Mapped[list | None] = deferred(mapped_column(JSON, nullable=True))
    storage_key: Mapped[str | None] = mapped_column(String(256), nullable=True)
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
```

在 `models.py` 顶部 import 补 `Computed`：`from sqlalchemy import JSON, Boolean, Computed, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint`。

- [ ] **Step 4: 写迁移** — `server/alembic/versions/0058_skill_library.py`

```python
"""skills + skill_versions 两表（多 skill 库）

Revision ID: 0058_skill_library
Revises: 0057_merge_heads
Create Date: 2026-07-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0058_skill_library"
down_revision: str | Sequence[str] | None = "0057_merge_heads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "skills",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("slug", sa.String(128), nullable=False),
        sa.Column("is_official", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("current_version_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column(
            "name_active",
            sa.String(128),
            sa.Computed("CASE WHEN is_deleted THEN NULL ELSE name END"),
            nullable=True,
        ),
        sa.Column(
            "slug_active",
            sa.String(128),
            sa.Computed("CASE WHEN is_deleted THEN NULL ELSE slug END"),
            nullable=True,
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_unique_constraint("uq_skills_name_active", "skills", ["name_active"])
    op.create_unique_constraint("uq_skills_slug_active", "skills", ["slug_active"])
    op.create_index("ix_skills_deleted", "skills", ["is_deleted"])

    op.create_table(
        "skill_versions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("skill_id", sa.Integer(), sa.ForeignKey("skills.id"), nullable=False),
        sa.Column("version_label", sa.String(32), nullable=False),
        sa.Column("bundle_sha256", sa.String(64), nullable=False),
        sa.Column("file_count", sa.Integer(), nullable=False),
        sa.Column("total_bytes", sa.Integer(), nullable=False),
        sa.Column("storage_backend", sa.String(8), nullable=False, server_default="db"),
        sa.Column("files", sa.JSON(), nullable=True),
        sa.Column("storage_key", sa.String(256), nullable=True),
        sa.Column("uploaded_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("uploaded_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("skill_id", "version_label", name="uq_skill_versions_label"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index("ix_skill_versions_skill", "skill_versions", ["skill_id", "is_deleted"])


def downgrade() -> None:
    op.drop_table("skill_versions")
    op.drop_table("skills")
```

- [ ] **Step 5: 跑测试确认通过**（`build_test_app` 建 schema 时会按 models 建表；此测试不依赖迁移文件本身）

Run: `pytest server/tests/test_skill_library_models.py -q`
Expected: PASS（2 passed）

- [ ] **Step 6: 验证迁移可 upgrade/downgrade**

Run: `alembic upgrade head && alembic downgrade -1 && alembic upgrade head`
Expected: 无报错，`0058_skill_library` 在链上单头。

- [ ] **Step 7: Commit**

```bash
git add server/app/modules/loop_skills/models.py server/alembic/versions/0058_skill_library.py server/tests/test_skill_library_models.py
git commit -m "feat(skill-library): skills + skill_versions 两表 + 迁移(生成列唯一)"
```

---

### Task 2: 存储 seam（DB / MinIO 分叉）

**Files:**
- Create: `server/app/modules/loop_skills/storage.py`
- Test: `server/tests/test_skill_library_storage.py`

**Interfaces:**
- Consumes: `service.SkillFile` / `SkillBundle`（现有 `loop_skills/service.py`）、`SkillVersion`（Task 1）、`image_library.store`（`upload_image` / `get_object_bytes` / `delete_object` / `ensure_bucket`）。
- Produces:
  - `SKILL_BUCKET = "geo-skill-bundles"`
  - `load_version_files(row: SkillVersion) -> list[SkillFile]` — 按 `storage_backend` 分叉返回文件列表。
  - `save_files_to_minio(files: list[SkillFile]) -> str` — 打 zip 传 MinIO，返回 `storage_key`。
  - `delete_minio_object(storage_key: str) -> None`

- [ ] **Step 1: 写失败测试** — `server/tests/test_skill_library_storage.py`

```python
import io
import zipfile


def test_load_files_db_backend():
    from server.app.modules.loop_skills.storage import load_version_files

    class Row:
        storage_backend = "db"
        files = [{"path": "SKILL.md", "content": "hi", "sha256": "x", "size": 2}]
        storage_key = None

    out = load_version_files(Row())
    assert len(out) == 1
    assert out[0].path == "SKILL.md"
    assert out[0].content == "hi"


def test_minio_roundtrip_monkeypatched(monkeypatch):
    """save→load 走 MinIO 分支，用假 store 打通读写不依赖真 MinIO。"""
    import server.app.modules.loop_skills.storage as st
    from server.app.modules.loop_skills.service import SkillFile

    blob = {}

    def fake_upload(bucket, key, data, content_type):
        blob[key] = data

    def fake_get(bucket, key):
        return blob[key]

    monkeypatch.setattr(st, "_store_upload", fake_upload)
    monkeypatch.setattr(st, "_store_get", fake_get)
    monkeypatch.setattr(st, "_ensure_bucket", lambda b: None)

    files = [SkillFile(path="a/SKILL.md", size=3, sha256="s", content="abc")]
    key = st.save_files_to_minio(files)
    assert key

    class Row:
        storage_backend = "minio"
        files = None
        storage_key = key

    out = st.load_version_files(Row())
    assert out[0].path == "a/SKILL.md"
    assert out[0].content == "abc"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest server/tests/test_skill_library_storage.py -q`
Expected: FAIL（`ModuleNotFoundError: ...storage`）

- [ ] **Step 3: 实现** — `server/app/modules/loop_skills/storage.py`

```python
"""Skill 版本文件存储 seam：DB(JSON) 默认，MinIO 后端保留（本期上传不触发）。

storage_backend='db' → 读 row.files；'minio' → 从 image_library MinIO 拉 zip 解包。
save_files_to_minio 已实现但上传路径本期一律走 db（见 skill_service.create_version）。
"""

from __future__ import annotations

import hashlib
import io
import zipfile

from server.app.modules.loop_skills.service import SkillFile

# 复用 image_library 的 MinIO 封装；包成模块级别名便于测试 monkeypatch
from server.app.modules.image_library.store import (
    delete_object as _store_delete,
)
from server.app.modules.image_library.store import (
    ensure_bucket as _ensure_bucket,
)
from server.app.modules.image_library.store import (
    get_object_bytes as _store_get,
)
from server.app.modules.image_library.store import (
    upload_image as _store_upload,
)

SKILL_BUCKET = "geo-skill-bundles"


def load_version_files(row) -> list[SkillFile]:
    if row.storage_backend == "minio":
        return _load_from_minio(row.storage_key)
    return [SkillFile(**f) for f in (row.files or [])]


def _load_from_minio(storage_key: str) -> list[SkillFile]:
    data = _store_get(SKILL_BUCKET, storage_key)
    zf = zipfile.ZipFile(io.BytesIO(data))
    files: list[SkillFile] = []
    for name in sorted(zf.namelist()):
        raw = zf.read(name)
        content = raw.decode("utf-8")
        files.append(
            SkillFile(path=name, size=len(raw), sha256=hashlib.sha256(raw).hexdigest(), content=content)
        )
    return files


def save_files_to_minio(files: list[SkillFile]) -> str:
    _ensure_bucket(SKILL_BUCKET)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.writestr(f.path, f.content)
    data = buf.getvalue()
    key = f"{hashlib.sha256(data).hexdigest()}.zip"
    _store_upload(SKILL_BUCKET, key, data, "application/zip")
    return key


def delete_minio_object(storage_key: str) -> None:
    _store_delete(SKILL_BUCKET, storage_key)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest server/tests/test_skill_library_storage.py -q`
Expected: PASS（2 passed）

- [ ] **Step 5: Commit**

```bash
git add server/app/modules/loop_skills/storage.py server/tests/test_skill_library_storage.py
git commit -m "feat(skill-library): 存储 seam(DB/MinIO 分叉,MinIO 建好不触发)"
```

---

### Task 3: 上传解析与校验

**Files:**
- Create: `server/app/modules/loop_skills/upload.py`
- Test: `server/tests/test_skill_library_upload.py`

**Interfaces:**
- Consumes: `service.build_bundle_from_file_map`。
- Produces:
  - 常量 `MAX_TOTAL_BYTES = 5 * 1024 * 1024`、`MAX_ENTRIES = 200`、`MAX_ENTRY_BYTES = 5 * 1024 * 1024`。
  - `parse_upload(entries: list[tuple[str, bytes]]) -> dict[str, bytes]` — 归一多来源（zip 解开 / 文件数组）为 `{posix_path: bytes}` 并做安全校验。
  - `validate_file_map(raw: dict[str, bytes]) -> None` — 校验 ≥1 SKILL.md、总 ≤5MB、UTF-8；失败抛 `ValidationError`。

- [ ] **Step 1: 写失败测试** — `server/tests/test_skill_library_upload.py`

```python
import io
import zipfile

import pytest

from server.app.shared.errors import ValidationError


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for k, v in files.items():
            zf.writestr(k, v)
    return buf.getvalue()


def test_parse_zip_entry():
    from server.app.modules.loop_skills.upload import parse_upload

    z = _zip({"skills/foo/SKILL.md": b"hello"})
    raw = parse_upload([("bundle.zip", z)])
    assert raw == {"skills/foo/SKILL.md": b"hello"}


def test_parse_file_array():
    from server.app.modules.loop_skills.upload import parse_upload

    raw = parse_upload([("SKILL.md", b"hi"), ("refs/a.md", b"x")])
    assert set(raw) == {"SKILL.md", "refs/a.md"}


def test_validate_requires_skill_md():
    from server.app.modules.loop_skills.upload import validate_file_map

    with pytest.raises(ValidationError):
        validate_file_map({"README.md": b"x"})


def test_validate_rejects_oversize():
    from server.app.modules.loop_skills.upload import validate_file_map

    with pytest.raises(ValidationError):
        validate_file_map({"SKILL.md": b"a" * (5 * 1024 * 1024 + 1)})


def test_parse_rejects_zip_slip():
    from server.app.modules.loop_skills.upload import parse_upload

    z = _zip({"../evil.md": b"x"})
    with pytest.raises(ValidationError):
        parse_upload([("bundle.zip", z)])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest server/tests/test_skill_library_upload.py -q`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现** — `server/app/modules/loop_skills/upload.py`

```python
"""上传归一 + 校验：把「一个 zip」或「多文件数组」统一成 file map 并安全校验。

放宽自旧 versions_service：不再限定 README.md/commands/skills/ 顶层白名单、不再要求
/goal 专属必需文件，改为「含 ≥1 个 SKILL.md（任意层级）+ 总 ≤5MB」的通用规则。
"""

from __future__ import annotations

import io
import zipfile

from server.app.shared.errors import ValidationError

MAX_TOTAL_BYTES = 5 * 1024 * 1024
MAX_ENTRY_BYTES = 5 * 1024 * 1024
MAX_ENTRIES = 200


def _safe_name(name: str) -> str:
    n = name.replace("\\", "/")
    if n.startswith("/") or ".." in n.split("/"):
        raise ValidationError(f"非法路径(zip-slip): {name}")
    return n


def parse_upload(entries: list[tuple[str, bytes]]) -> dict[str, bytes]:
    """entries=[(filename, bytes)]。单个 .zip → 解开；否则按文件数组原样收。"""
    raw: dict[str, bytes] = {}
    if len(entries) == 1 and entries[0][0].lower().endswith(".zip"):
        try:
            zf = zipfile.ZipFile(io.BytesIO(entries[0][1]))
        except zipfile.BadZipFile as exc:
            raise ValidationError("不是合法的 zip 文件") from exc
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > MAX_ENTRIES:
            raise ValidationError(f"文件过多: {len(infos)}(上限 {MAX_ENTRIES})")
        for info in infos:
            name = _safe_name(info.filename)
            if info.file_size > MAX_ENTRY_BYTES:
                raise ValidationError(f"单文件过大: {name}")
            if name in raw:
                raise ValidationError(f"重复路径: {name}")
            raw[name] = zf.read(info)
        return raw
    if len(entries) > MAX_ENTRIES:
        raise ValidationError(f"文件过多: {len(entries)}(上限 {MAX_ENTRIES})")
    for fname, data in entries:
        name = _safe_name(fname)
        if len(data) > MAX_ENTRY_BYTES:
            raise ValidationError(f"单文件过大: {name}")
        if name in raw:
            raise ValidationError(f"重复路径: {name}")
        raw[name] = data
    return raw


def validate_file_map(raw: dict[str, bytes]) -> None:
    if not raw:
        raise ValidationError("上传内容为空")
    total = sum(len(v) for v in raw.values())
    if total > MAX_TOTAL_BYTES:
        raise ValidationError(f"总大小 {total} 超过 {MAX_TOTAL_BYTES} 字节(5 MB)上限")
    has_skill_md = any(p.rsplit("/", 1)[-1] == "SKILL.md" for p in raw)
    if not has_skill_md:
        raise ValidationError("包内未找到任何 SKILL.md")
    for name, data in raw.items():
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationError(f"非 UTF-8 文本文件: {name}") from exc
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest server/tests/test_skill_library_upload.py -q`
Expected: PASS（5 passed）

- [ ] **Step 5: Commit**

```bash
git add server/app/modules/loop_skills/upload.py server/tests/test_skill_library_upload.py
git commit -m "feat(skill-library): 上传归一+校验(zip/文件数组,≥1 SKILL.md,≤5MB)"
```

---

### Task 4: skill_service —— 创建/追加版本 + 读取 + 列表

**Files:**
- Create: `server/app/modules/loop_skills/skill_service.py`
- Test: `server/tests/test_skill_library_service.py`

**Interfaces:**
- Consumes: `Skill` / `SkillVersion`（Task 1）、`upload.parse_upload` / `validate_file_map`（Task 3）、`service.build_bundle_from_file_map` / `SkillBundle`、`storage.load_version_files`。
- Produces（本 Task 实现前 4 个，Task 5 实现其余）：
  - `slugify(name: str, session) -> str`
  - `create_version(session, *, entries, name, uploaded_by) -> tuple[Skill, SkillVersion]` — 校验→建/追加 skill→写版本(db)→设当前。同名命中活跃 skill 追加，否则新建。
  - `get_current_bundle(session, slug: str) -> SkillBundle` — 官方/任意 skill 当前版本组包。
  - `list_skills(session) -> list[SkillListItem]`（dataclass：id/name/slug/is_official/current_version_label/file_count/total_bytes/updated_at/uploaded_by）
  - `get_skill_bundle_by_id(session, skill_id) -> SkillBundle`（下载用，当前版本）

- [ ] **Step 1: 写失败测试** — `server/tests/test_skill_library_service.py`

```python
import io
import zipfile

import pytest

pytestmark = pytest.mark.mysql


def _zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for k, v in files.items():
            zf.writestr(k, v)
    return buf.getvalue()


def _db():
    from server.app.db.session import SessionLocal

    return SessionLocal()


def test_create_then_append_version(monkeypatch):
    from server.tests.utils import build_test_app
    from server.app.modules.loop_skills import skill_service as svc

    app = build_test_app(monkeypatch)
    try:
        db = _db()
        try:
            sk, v1 = svc.create_version(
                db, entries=[("b.zip", _zip({"SKILL.md": b"one"}))], name="writer", uploaded_by=None
            )
            db.commit()
            assert v1.version_label == "v1"
            assert sk.current_version_id == v1.id

            sk2, v2 = svc.create_version(
                db, entries=[("b.zip", _zip({"SKILL.md": b"two"}))], name="writer", uploaded_by=None
            )
            db.commit()
            assert sk2.id == sk.id  # 同名追加
            assert v2.version_label == "v2"
            assert sk2.current_version_id == v2.id  # 当前指针移到 v2

            items = svc.list_skills(db)
            assert any(it.slug == sk.slug and it.current_version_label == "v2" for it in items)

            bundle = svc.get_current_bundle(db, sk.slug)
            assert bundle.files[0].content == "two"
        finally:
            db.close()
    finally:
        app.cleanup()


def test_create_rejects_missing_skill_md(monkeypatch):
    from server.tests.utils import build_test_app
    from server.app.modules.loop_skills import skill_service as svc
    from server.app.shared.errors import ValidationError

    app = build_test_app(monkeypatch)
    try:
        db = _db()
        try:
            with pytest.raises(ValidationError):
                svc.create_version(
                    db, entries=[("b.zip", _zip({"README.md": b"x"}))], name="bad", uploaded_by=None
                )
        finally:
            db.close()
    finally:
        app.cleanup()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest server/tests/test_skill_library_service.py -q`
Expected: FAIL（模块/函数不存在）

- [ ] **Step 3: 实现** — `server/app/modules/loop_skills/skill_service.py`（本 Task 部分）

```python
"""多 skill 库服务层：上传/追加版本、读取、列表、回滚、删除、权限。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.app.modules.loop_skills import storage, upload
from server.app.modules.loop_skills.models import Skill, SkillVersion
from server.app.modules.loop_skills.service import (
    SkillBundle,
    SkillFile,
    build_bundle_from_file_map,
)
from server.app.shared.errors import ConflictError, ValidationError


@dataclass(frozen=True)
class SkillListItem:
    id: int
    name: str
    slug: str
    is_official: bool
    current_version_label: str | None
    file_count: int
    total_bytes: int
    updated_at: datetime
    uploaded_by: int | None


def slugify(name: str, session: Session) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "skill"
    slug = base
    n = 1
    while session.execute(
        select(Skill.id).where(Skill.slug == slug, Skill.is_deleted.is_(False))
    ).first():
        n += 1
        slug = f"{base}-{n}"
    return slug


def _active_skill_by_name(session: Session, name: str) -> Skill | None:
    return (
        session.execute(select(Skill).where(Skill.name == name, Skill.is_deleted.is_(False)))
        .scalars()
        .first()
    )


def _next_label(session: Session, skill_id: int) -> str:
    rows = (
        session.execute(
            select(SkillVersion.version_label).where(SkillVersion.skill_id == skill_id)
        )
        .scalars()
        .all()
    )
    mx = 0
    for r in rows:
        m = re.match(r"v(\d+)$", r)
        if m:
            mx = max(mx, int(m.group(1)))
    return f"v{mx + 1}"


def create_version(
    session: Session, *, entries: list[tuple[str, bytes]], name: str, uploaded_by: int | None
) -> tuple[Skill, SkillVersion]:
    name = (name or "").strip()
    if not name:
        raise ValidationError("skill 名不能为空")
    raw = upload.parse_upload(entries)
    upload.validate_file_map(raw)
    bundle = build_bundle_from_file_map(raw, version="pending")

    skill = _active_skill_by_name(session, name)
    if skill is None:
        skill = Skill(name=name, slug=slugify(name, session), is_official=False, created_by=uploaded_by)
        session.add(skill)
        session.flush()

    version = SkillVersion(
        skill_id=skill.id,
        version_label=_next_label(session, skill.id),
        bundle_sha256=bundle.bundle_sha256,
        file_count=len(bundle.files),
        total_bytes=sum(f.size for f in bundle.files),
        storage_backend="db",
        files=[{"path": f.path, "content": f.content, "sha256": f.sha256, "size": f.size} for f in bundle.files],
        storage_key=None,
        uploaded_by=uploaded_by,
    )
    session.add(version)
    session.flush()
    skill.current_version_id = version.id
    session.flush()
    return skill, version


def _version_to_bundle(skill: Skill, row: SkillVersion) -> SkillBundle:
    files = storage.load_version_files(row)
    return SkillBundle(version=row.version_label, bundle_sha256=row.bundle_sha256, files=files)


def _active_skill_by_slug(session: Session, slug: str) -> Skill:
    sk = (
        session.execute(select(Skill).where(Skill.slug == slug, Skill.is_deleted.is_(False)))
        .scalars()
        .first()
    )
    if sk is None:
        raise ValidationError(f"skill 不存在: {slug}")
    return sk


def get_current_bundle(session: Session, slug: str) -> SkillBundle:
    sk = _active_skill_by_slug(session, slug)
    if sk.current_version_id is None:
        raise ValidationError(f"skill 无当前版本: {slug}")
    row = session.get(SkillVersion, sk.current_version_id)
    return _version_to_bundle(sk, row)


def get_skill_bundle_by_id(session: Session, skill_id: int) -> SkillBundle:
    sk = session.get(Skill, skill_id)
    if sk is None or sk.is_deleted or sk.current_version_id is None:
        raise ValidationError(f"skill 不存在或无当前版本: {skill_id}")
    row = session.get(SkillVersion, sk.current_version_id)
    return _version_to_bundle(sk, row)


def list_skills(session: Session) -> list[SkillListItem]:
    skills = (
        session.execute(
            select(Skill).where(Skill.is_deleted.is_(False)).order_by(Skill.is_official.desc(), Skill.id)
        )
        .scalars()
        .all()
    )
    items: list[SkillListItem] = []
    for sk in skills:
        cur = session.get(SkillVersion, sk.current_version_id) if sk.current_version_id else None
        items.append(
            SkillListItem(
                id=sk.id,
                name=sk.name,
                slug=sk.slug,
                is_official=sk.is_official,
                current_version_label=cur.version_label if cur else None,
                file_count=cur.file_count if cur else 0,
                total_bytes=cur.total_bytes if cur else 0,
                updated_at=sk.updated_at,
                uploaded_by=cur.uploaded_by if cur else None,
            )
        )
    return items
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest server/tests/test_skill_library_service.py -q`
Expected: PASS（2 passed）

- [ ] **Step 5: Commit**

```bash
git add server/app/modules/loop_skills/skill_service.py server/tests/test_skill_library_service.py
git commit -m "feat(skill-library): skill_service 创建/追加版本+读取+列表"
```

---

### Task 5: skill_service —— 版本历史 / 回滚 / 删除 / 权限

**Files:**
- Modify: `server/app/modules/loop_skills/skill_service.py`
- Test: `server/tests/test_skill_library_service.py`（追加）

**Interfaces:**
- Produces：
  - `list_versions(session, skill_id) -> list[VersionItem]`（dataclass：id/version_label/bundle_sha256/file_count/total_bytes/uploaded_by/uploaded_at/is_current）
  - `set_current(session, skill_id, version_id) -> None`（回滚，任何登录用户）
  - `delete_version(session, skill_id, version_id, *, user_id, is_admin) -> None`（拒当前版本→409；属主/admin/官方包 admin-only 校验）
  - `delete_skill(session, skill_id) -> None`（软删整个 skill，router 侧已 require_admin）

- [ ] **Step 1: 追加失败测试**（写在 `test_skill_library_service.py` 末尾）

```python
def test_set_current_and_delete_matrix(monkeypatch):
    from server.tests.utils import build_test_app
    from server.app.modules.loop_skills import skill_service as svc
    from server.app.shared.errors import ConflictError, ValidationError

    app = build_test_app(monkeypatch)
    try:
        db = _db()
        try:
            sk, v1 = svc.create_version(
                db, entries=[("b.zip", _zip({"SKILL.md": b"1"}))], name="w", uploaded_by=7
            )
            _, v2 = svc.create_version(
                db, entries=[("b.zip", _zip({"SKILL.md": b"2"}))], name="w", uploaded_by=9
            )
            db.commit()

            # 回滚到 v1
            svc.set_current(db, sk.id, v1.id)
            db.commit()
            db.refresh(sk)
            assert sk.current_version_id == v1.id

            # 当前版本(v1)不可删 → 409
            with pytest.raises(ConflictError):
                svc.delete_version(db, sk.id, v1.id, user_id=7, is_admin=False)

            # 非属主删非当前版本(v2 由 user9 传) → 403 语义(ValidationError/PermissionError)
            with pytest.raises(Exception):
                svc.delete_version(db, sk.id, v2.id, user_id=7, is_admin=False)

            # admin 删非当前版本 OK
            svc.delete_version(db, sk.id, v2.id, user_id=1, is_admin=True)
            db.commit()
            assert all(x.version_label != "v2" for x in svc.list_versions(db, sk.id))
        finally:
            db.close()
    finally:
        app.cleanup()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest server/tests/test_skill_library_service.py::test_set_current_and_delete_matrix -q`
Expected: FAIL（`AttributeError: ...set_current`）

- [ ] **Step 3: 追加实现**（`skill_service.py` 末尾）— 顶部 import 补 `from server.app.shared.errors import ClientError`

```python
@dataclass(frozen=True)
class VersionItem:
    id: int
    version_label: str
    bundle_sha256: str
    file_count: int
    total_bytes: int
    uploaded_by: int | None
    uploaded_at: datetime
    is_current: bool


def list_versions(session: Session, skill_id: int) -> list[VersionItem]:
    sk = session.get(Skill, skill_id)
    if sk is None or sk.is_deleted:
        raise ValidationError(f"skill 不存在: {skill_id}")
    rows = (
        session.execute(
            select(SkillVersion)
            .where(SkillVersion.skill_id == skill_id, SkillVersion.is_deleted.is_(False))
            .order_by(SkillVersion.id.desc())
        )
        .scalars()
        .all()
    )
    return [
        VersionItem(
            id=r.id,
            version_label=r.version_label,
            bundle_sha256=r.bundle_sha256,
            file_count=r.file_count,
            total_bytes=r.total_bytes,
            uploaded_by=r.uploaded_by,
            uploaded_at=r.uploaded_at,
            is_current=(r.id == sk.current_version_id),
        )
        for r in rows
    ]


def set_current(session: Session, skill_id: int, version_id: int) -> None:
    sk = session.get(Skill, skill_id)
    if sk is None or sk.is_deleted:
        raise ValidationError(f"skill 不存在: {skill_id}")
    v = session.get(SkillVersion, version_id)
    if v is None or v.is_deleted or v.skill_id != skill_id:
        raise ValidationError(f"版本不存在: {version_id}")
    sk.current_version_id = version_id
    session.flush()


def delete_version(
    session: Session, skill_id: int, version_id: int, *, user_id: int | None, is_admin: bool
) -> None:
    sk = session.get(Skill, skill_id)
    if sk is None or sk.is_deleted:
        raise ValidationError(f"skill 不存在: {skill_id}")
    v = session.get(SkillVersion, version_id)
    if v is None or v.is_deleted or v.skill_id != skill_id:
        raise ValidationError(f"版本不存在: {version_id}")
    if sk.current_version_id == version_id:
        raise ConflictError("不能删除当前版本,请先切到别的版本再删")
    # 权限：官方包版本仅 admin；否则 属主 或 admin
    if sk.is_official and not is_admin:
        raise ClientError("官方包版本仅管理员可删")
    if not is_admin and v.uploaded_by != user_id:
        raise ClientError("只能删除自己上传的版本")
    v.is_deleted = True
    session.flush()


def delete_skill(session: Session, skill_id: int) -> None:
    """软删整个 skill（router 侧已 require_admin）。"""
    sk = session.get(Skill, skill_id)
    if sk is None or sk.is_deleted:
        raise ValidationError(f"skill 不存在: {skill_id}")
    sk.is_deleted = True
    session.flush()
```

> 注：`ClientError`→400。权限越权在 router 层用 `is_admin`/`user_id` 组合已判；若要严格 403，router 捕获 `ClientError` 且消息含"仅"/"只能"时可转 `HTTPException(403)`（见 Task 6 Step 3 的 `_forbidden` 包装）。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest server/tests/test_skill_library_service.py -q`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add server/app/modules/loop_skills/skill_service.py server/tests/test_skill_library_service.py
git commit -m "feat(skill-library): 版本历史/回滚/删除/权限矩阵"
```

---

### Task 6: 新 `/api/mcp/skills/*` 路由 + schemas + main.py 挂载

**Files:**
- Create: `server/app/modules/loop_skills/skill_router.py`
- Modify: `server/app/modules/loop_skills/schemas.py`（追加响应模型）
- Modify: `server/app/main.py`（import + mount）
- Test: `server/tests/test_skill_library_api.py`

**Interfaces:**
- Consumes: `skill_service`（Task 4/5）、`service.build_zip`、`get_current_user` / `require_admin` / `require_mcp_token`、`add_audit_entry`。
- Produces: `skills_user_router`（user JWT）、`skills_mcp_router`（MCP token）。

- [ ] **Step 1: 写失败测试** — `server/tests/test_skill_library_api.py`

```python
import io
import zipfile

import pytest

pytestmark = pytest.mark.mysql


def _zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for k, v in files.items():
            zf.writestr(k, v)
    return buf.getvalue()


def test_upload_list_setcurrent_download(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        c = app.client  # TestClient，已带 admin JWT cookie
        # 上传 v1
        r = c.post(
            "/api/mcp/skills/upload",
            files={"files": ("b.zip", _zip({"SKILL.md": b"one"}), "application/zip")},
            data={"name": "writer"},
        )
        assert r.status_code == 200, r.text
        sid = r.json()["skill_id"]
        assert r.json()["version_label"] == "v1"

        # 列表含官方 goal(seed 后) + writer；此处至少含 writer
        r = c.get("/api/mcp/skills")
        assert r.status_code == 200
        assert any(s["slug"] for s in r.json()["skills"])

        # 上传 v2 → 版本历史 2 条
        c.post(
            "/api/mcp/skills/upload",
            files={"files": ("b.zip", _zip({"SKILL.md": b"two"}), "application/zip")},
            data={"name": "writer"},
        )
        r = c.get(f"/api/mcp/skills/{sid}/versions")
        labels = [v["version_label"] for v in r.json()["versions"]]
        assert labels == ["v2", "v1"]
        v1_id = [v["id"] for v in r.json()["versions"] if v["version_label"] == "v1"][0]

        # 回滚到 v1
        r = c.post(f"/api/mcp/skills/{sid}/set-current", json={"version_id": v1_id})
        assert r.status_code == 204

        # 下载当前版本 zip
        r = c.get(f"/api/mcp/skills/{sid}/download.zip")
        assert r.status_code == 200
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        assert zf.read("SKILL.md") == b"one"

        # 删当前版本 → 409
        r = c.delete(f"/api/mcp/skills/{sid}/versions/{v1_id}")
        assert r.status_code == 409
    finally:
        app.cleanup()
```

> 若 `build_test_app` 未暴露 `app.client`，用其现有 TestClient 获取方式替换（参考 `test_loop_skill_bundle_versions.py` 的 client 用法，保持一致）。

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest server/tests/test_skill_library_api.py -q`
Expected: FAIL（404，路由未挂）

- [ ] **Step 3: 加 schemas** — `server/app/modules/loop_skills/schemas.py` 追加

```python
class SkillMeta(BaseModel):
    id: int
    name: str
    slug: str
    is_official: bool
    current_version_label: str | None
    file_count: int
    total_bytes: int
    updated_at: datetime
    uploaded_by: int | None


class SkillList(BaseModel):
    skills: list[SkillMeta]


class SkillVersionMeta(BaseModel):
    id: int
    version_label: str
    bundle_sha256: str
    file_count: int
    total_bytes: int
    uploaded_by: int | None
    uploaded_at: datetime
    is_current: bool


class SkillVersionList(BaseModel):
    versions: list[SkillVersionMeta]


class UploadResult(BaseModel):
    skill_id: int
    slug: str
    version_label: str


class SetCurrentBody(BaseModel):
    version_id: int
```

- [ ] **Step 3b: 加路由** — `server/app/modules/loop_skills/skill_router.py`

```python
"""多 skill 库 HTTP 路由：/api/mcp/skills/*（user JWT）+ install-payload（MCP token）。"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from sqlalchemy.orm import Session

from server.app.core.mcp_auth import require_mcp_token
from server.app.core.mcp_errors import mcp_exception_response
from server.app.core.security import get_current_user, require_admin
from server.app.db.session import get_db
from server.app.modules.audit.service import add_audit_entry
from server.app.modules.loop_skills import skill_service as svc
from server.app.modules.loop_skills.schemas import (
    SetCurrentBody,
    SkillList,
    SkillMeta,
    SkillVersionList,
    SkillVersionMeta,
    UploadResult,
)
from server.app.modules.loop_skills.service import SkillBundle, build_zip
from server.app.modules.system.models import User
from server.app.shared.errors import ClientError

skills_user_router = APIRouter()


def _zip_response(b: SkillBundle) -> Response:
    ascii_name = f"geo-skill-{b.bundle_sha256[:12]}.zip"
    utf8_name = quote(f"geo-skill-{b.version}.zip")
    return Response(
        content=build_zip(b),
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{utf8_name}",
        },
    )


@skills_user_router.get("/skills", response_model=SkillList)
def list_skills(db: Session = Depends(get_db)) -> SkillList:
    return SkillList(skills=[SkillMeta(**it.__dict__) for it in svc.list_skills(db)])


@skills_user_router.post("/skills/upload", response_model=UploadResult)
async def upload_skill(
    files: list[UploadFile] = File(...),
    name: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UploadResult:
    entries = [(f.filename or "file", await f.read()) for f in files]
    skill, version = svc.create_version(db, entries=entries, name=name, uploaded_by=current_user.id)
    add_audit_entry(
        db, user=current_user, action="skill.upload", target_type="skill",
        target_id=str(skill.id), payload={"version": version.version_label},
    )
    return UploadResult(skill_id=skill.id, slug=skill.slug, version_label=version.version_label)


@skills_user_router.get("/skills/{skill_id}/versions", response_model=SkillVersionList)
def list_versions(skill_id: int, db: Session = Depends(get_db)) -> SkillVersionList:
    return SkillVersionList(versions=[SkillVersionMeta(**v.__dict__) for v in svc.list_versions(db, skill_id)])


@skills_user_router.post("/skills/{skill_id}/set-current", status_code=204)
def set_current(
    skill_id: int, body: SetCurrentBody, db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    svc.set_current(db, skill_id, body.version_id)
    add_audit_entry(
        db, user=current_user, action="skill.set_current", target_type="skill",
        target_id=str(skill_id), payload={"version_id": body.version_id},
    )
    return Response(status_code=204)


@skills_user_router.delete("/skills/{skill_id}/versions/{version_id}", status_code=204)
def delete_version(
    skill_id: int, version_id: int, db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    try:
        svc.delete_version(
            db, skill_id, version_id,
            user_id=current_user.id, is_admin=(current_user.role == "admin"),
        )
    except ClientError as exc:
        # 权限类 ClientError → 403（越权），其余 400 由全局兜底
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    add_audit_entry(
        db, user=current_user, action="skill.delete_version", target_type="skill_version",
        target_id=str(version_id),
    )
    return Response(status_code=204)


@skills_user_router.delete("/skills/{skill_id}", status_code=204)
def delete_skill(
    skill_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_admin),
) -> Response:
    svc.delete_skill(db, skill_id)
    add_audit_entry(
        db, user=current_user, action="skill.delete", target_type="skill", target_id=str(skill_id),
    )
    return Response(status_code=204)


@skills_user_router.get("/skills/{skill_id}/download.zip")
def download_skill(skill_id: int, db: Session = Depends(get_db)) -> Response:
    return _zip_response(svc.get_skill_bundle_by_id(db, skill_id))


skills_mcp_router = APIRouter(dependencies=[Depends(require_mcp_token)])


@skills_mcp_router.get("/skills/{slug}/install-payload")
def install_payload(slug: str, db: Session = Depends(get_db)) -> dict:
    try:
        b = svc.get_current_bundle(db, slug)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": str(exc),
            "data": {"available": [{"slug": s.slug, "name": s.name} for s in svc.list_skills(db)]},
        }
    return {
        "ok": True,
        "data": {
            "version": b.version,
            "bundle_sha256": b.bundle_sha256,
            "install_hint": (
                "Write each file to the user's .claude/ directory, preserving the relative "
                "path. Prefer project-level <repo>/.claude/ over ~/.claude/ inside a git repo. "
                "If a file exists, show diff and ask before overwriting."
            ),
            "files": [
                {"path": f.path, "content": f.content, "sha256": f.sha256, "size": f.size}
                for f in b.files
            ],
        },
        "error": None,
    }
```

> `SkillMeta(**it.__dict__)` 依赖 dataclass 字段名与 schema 完全一致（已对齐）。

- [ ] **Step 3c: 挂载** — `server/app/main.py`

在 import 段（loop_skills import 附近）加：
```python
from server.app.modules.loop_skills.skill_router import (
    skills_mcp_router,
    skills_user_router,
)
```
在 `create_app()` 里 loop_skills 两个 include_router 之后加：
```python
    app.include_router(
        skills_user_router, prefix="/api/mcp", tags=["skills"],
        dependencies=[Depends(get_current_user)],
    )
    app.include_router(skills_mcp_router, prefix="/api/mcp", tags=["skills-mcp"])
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest server/tests/test_skill_library_api.py -q`
Expected: PASS（1 passed）

- [ ] **Step 5: Commit**

```bash
git add server/app/modules/loop_skills/skill_router.py server/app/modules/loop_skills/schemas.py server/app/main.py server/tests/test_skill_library_api.py
git commit -m "feat(skill-library): /api/mcp/skills/* 路由+schemas+挂载"
```

---

### Task 7: 旧端点兼容别名 + install_loop_skills 重指 + version.py 退场

**Files:**
- Modify: `server/app/modules/loop_skills/router.py`（旧读端点指向官方 skill；下线旧写端点）
- Modify: `server/mcp/tools/action.py`（`install_loop_skills` 重指新路径）
- Modify: `server/app/modules/loop_skills/version.py`（删 `KNOWN_BUNDLE_SHAS`）
- Modify: `server/tests/test_loop_skill_bundle.py`（删 `test_bundle_sha_is_known`）
- Modify: `server/tests/test_loop_skill_bundle_versions.py`（旧写端点用例改/删）
- Test: `server/tests/test_skill_library_compat.py`

**Interfaces:**
- Consumes: `skill_service.get_current_bundle(db, "goal")`。

- [ ] **Step 1: 写兼容测试** — `server/tests/test_skill_library_compat.py`

```python
import pytest

pytestmark = pytest.mark.mysql


def test_old_install_payload_alias_resolves_to_official(monkeypatch):
    """旧 /loop-skill-bundle/install-payload 仍返回官方 skill 当前版本内容。"""
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        # 需先 seed 官方 skill（Task 8 的 seed）；此处直接用 skill_service 建官方包
        from server.app.db.session import SessionLocal
        from server.app.modules.loop_skills import skill_service as svc
        import io, zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("commands/goal.md", "x")
            zf.writestr("skills/geo-goal-orchestrator/SKILL.md", "y")
        db = SessionLocal()
        try:
            sk, _ = svc.create_version(db, entries=[("b.zip", buf.getvalue())], name="/goal loop skills", uploaded_by=None)
            sk.slug = "goal"
            sk.is_official = True
            db.commit()
        finally:
            db.close()

        r = app.mcp_client.get("/api/mcp/loop-skill-bundle/install-payload")  # 带 MCP token 的 client
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert any(f["path"] == "commands/goal.md" for f in r.json()["data"]["files"])
    finally:
        app.cleanup()
```

> 用 `test_loop_skill_bundle_versions.py` 里现成的「带 MCP token 的请求」写法替换 `app.mcp_client`（保持与既有测试一致）。

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest server/tests/test_skill_library_compat.py -q`
Expected: FAIL（旧端点仍返回旧单 bundle 内容 / seed 未接）

- [ ] **Step 3: 旧读端点重指** — `server/app/modules/loop_skills/router.py`

把 `get_loop_skill_bundle_info` / `download_loop_skill_bundle_zip` / `get_loop_skill_install_payload` 内部改为读官方 skill：
```python
from server.app.modules.loop_skills import skill_service as _svc

# info：
b = _svc.get_current_bundle(db, "goal")
# download.zip：
b = _svc.resolve...  # version 空→官方当前；其余保持 400。改为：b = _svc.get_current_bundle(db, "goal")
# install-payload（mcp_router）：内部 b = _svc.get_current_bundle(db, "goal")，其余 payload 结构不变
```
删除旧写端点：`list_bundle_versions`(可留)、`upload_bundle_version`、`enable_bundle_version`、`delete_bundle_version`、`download_bundle_version` 中的**写端点**（upload/enable/delete）整段删除；只读的 info/download.zip/versions/install-payload 保留为别名。

- [ ] **Step 3b: MCP 工具重指** — `server/mcp/tools/action.py`

```python
# install_loop_skills 内部：
params = {"slug": "goal"}  # 固定官方 slug
raw = await _aget("/api/mcp/skills/goal/install-payload")
```
保留 `version` 参数签名不变（本期忽略或映射；docstring 说明"当前读官方 slug=goal 的当前版本"）。

- [ ] **Step 3c: version.py 退场** — 删 `KNOWN_BUNDLE_SHAS` 整块，保留 `LOOP_SKILL_BUNDLE_VERSION`；删 `server/tests/test_loop_skill_bundle.py::test_bundle_sha_is_known` 整个函数。

- [ ] **Step 3d: 修旧版本管理测试** — `server/tests/test_loop_skill_bundle_versions.py` 中断言旧 `POST /versions` / `enable` / `DELETE` 的用例：删除或改为断言 404/405（端点已下线）。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest server/tests/test_skill_library_compat.py server/tests/test_loop_skill_bundle.py -q`
Expected: PASS（compat 绿；`test_bundle_sha_is_known` 已不存在）

- [ ] **Step 5: Commit**

```bash
git add server/app/modules/loop_skills/router.py server/mcp/tools/action.py server/app/modules/loop_skills/version.py server/tests/test_loop_skill_bundle.py server/tests/test_loop_skill_bundle_versions.py server/tests/test_skill_library_compat.py
git commit -m "feat(skill-library): 旧端点指向官方skill别名+install工具重指+version.py SHA纪律退场"
```

---

### Task 8: seed 脚本（挂进新表 / 迁移现有 bundle）

**Files:**
- Create: `server/scripts/seed_skill_library.py`
- Test: `server/tests/test_skill_library_seed.py`

**Interfaces:**
- Consumes: `LoopSkillBundleVersion`（旧表）、`Skill`/`SkillVersion`、`service.build_bundle`（templates 兜底）。
- Produces: `seed_skill_library(session) -> None`（幂等：以官方 `slug="goal"` 是否存在为闸）。

- [ ] **Step 1: 写失败测试** — `server/tests/test_skill_library_seed.py`

```python
import pytest

pytestmark = pytest.mark.mysql


def test_seed_from_existing_bundle_rows(monkeypatch):
    from server.tests.utils import build_test_app
    from server.app.db.session import SessionLocal
    from server.app.modules.loop_skills.models import LoopSkillBundleVersion, Skill
    from server.scripts.seed_skill_library import seed_skill_library

    app = build_test_app(monkeypatch)
    try:
        db = SessionLocal()
        try:
            db.add(LoopSkillBundleVersion(
                version_label="2026-07-07-v12", bundle_sha256="a" * 64,
                files=[{"path": "commands/goal.md", "content": "x", "sha256": "a" * 64, "size": 1}],
                file_count=1, total_size=1, is_enabled=True, is_deleted=False,
                uploaded_by_user_id=None, notes=None,
            ))
            db.commit()

            seed_skill_library(db)
            db.commit()

            goal = db.query(Skill).filter(Skill.slug == "goal").one()
            assert goal.is_official is True
            assert goal.current_version_id is not None

            # 幂等：再跑一次不重复建
            seed_skill_library(db)
            db.commit()
            assert db.query(Skill).filter(Skill.slug == "goal").count() == 1
        finally:
            db.close()
    finally:
        app.cleanup()


def test_seed_empty_falls_back_to_templates(monkeypatch):
    from server.tests.utils import build_test_app
    from server.app.db.session import SessionLocal
    from server.app.modules.loop_skills.models import Skill, SkillVersion
    from server.scripts.seed_skill_library import seed_skill_library

    app = build_test_app(monkeypatch)
    try:
        db = SessionLocal()
        try:
            seed_skill_library(db)
            db.commit()
            goal = db.query(Skill).filter(Skill.slug == "goal").one()
            v = db.get(SkillVersion, goal.current_version_id)
            assert v.file_count >= 1  # templates 灌入
        finally:
            db.close()
    finally:
        app.cleanup()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest server/tests/test_skill_library_seed.py -q`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现** — `server/scripts/seed_skill_library.py`

```python
"""把现有单 bundle 迁进新 Skill 库的官方 skill（slug=goal）。幂等：已存在即跳过。

部署时跑一次：python -m server.scripts.seed_skill_library
"""

from __future__ import annotations

# 注册全模块 mapper（跨模块 relationship 需要）
import server.app.modules.accounts.models  # noqa: F401
import server.app.modules.articles.models  # noqa: F401
import server.app.modules.audit.models  # noqa: F401
import server.app.modules.image_library.models  # noqa: F401
import server.app.modules.tasks.models  # noqa: F401
from sqlalchemy import select
from sqlalchemy.orm import Session

from server.app.db.session import SessionLocal
from server.app.modules.loop_skills.models import LoopSkillBundleVersion, Skill, SkillVersion
from server.app.modules.loop_skills.service import build_bundle
from server.app.modules.loop_skills.version import LOOP_SKILL_BUNDLE_VERSION

OFFICIAL_NAME = "/goal loop skills"
OFFICIAL_SLUG = "goal"


def seed_skill_library(session: Session) -> None:
    exists = session.execute(select(Skill.id).where(Skill.slug == OFFICIAL_SLUG)).first()
    if exists:
        print(f"skill '{OFFICIAL_SLUG}' already seeded, skip")
        return

    skill = Skill(name=OFFICIAL_NAME, slug=OFFICIAL_SLUG, is_official=True, created_by=None)
    session.add(skill)
    session.flush()

    old_rows = (
        session.execute(
            select(LoopSkillBundleVersion)
            .where(LoopSkillBundleVersion.is_deleted.is_(False))
            .order_by(LoopSkillBundleVersion.id)
        )
        .scalars()
        .all()
    )

    current_id = None
    if old_rows:
        for old in old_rows:
            v = SkillVersion(
                skill_id=skill.id,
                version_label=old.version_label,
                bundle_sha256=old.bundle_sha256,
                file_count=old.file_count,
                total_bytes=old.total_size,
                storage_backend="db",
                files=old.files,
                storage_key=None,
                uploaded_by=old.uploaded_by_user_id,
            )
            session.add(v)
            session.flush()
            if old.is_enabled:
                current_id = v.id
        if current_id is None:
            current_id = v.id  # 无 enabled → 取最后一条
    else:
        bundle = build_bundle()
        v = SkillVersion(
            skill_id=skill.id,
            version_label=LOOP_SKILL_BUNDLE_VERSION,
            bundle_sha256=bundle.bundle_sha256,
            file_count=len(bundle.files),
            total_bytes=sum(f.size for f in bundle.files),
            storage_backend="db",
            files=[{"path": f.path, "content": f.content, "sha256": f.sha256, "size": f.size} for f in bundle.files],
            storage_key=None,
            uploaded_by=None,
        )
        session.add(v)
        session.flush()
        current_id = v.id

    skill.current_version_id = current_id
    session.flush()
    print(f"seeded official skill '{OFFICIAL_SLUG}' with current version id={current_id}")


def main() -> None:
    db = SessionLocal()
    try:
        seed_skill_library(db)
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest server/tests/test_skill_library_seed.py -q`
Expected: PASS（2 passed）

- [ ] **Step 5: 跑后端全量回归**

Run: `pytest server/tests/ -q -k "skill or loop"`
Expected: 全绿（新库 + 兼容 + 旧 bundle 保留用例）

- [ ] **Step 6: Commit**

```bash
git add server/scripts/seed_skill_library.py server/tests/test_skill_library_seed.py
git commit -m "feat(skill-library): 幂等 seed 脚本(挂现有 bundle 进官方 skill / templates 兜底)"
```

---

### Task 9: 前端 API 客户端 + 下载设计图基准

**Files:**
- Create: `web/src/api/skills.ts`
- Create（本地基准图，不入库）: `docs/superpowers/assets/skill-library/*.png`

**Interfaces:**
- Produces: `listSkills` / `uploadSkill` / `listSkillVersions` / `setCurrentVersion` / `deleteSkillVersion` / `deleteSkill` / `skillDownloadUrl` / `skillInstallCommand` + 类型 `Skill` / `SkillVersion`。

- [ ] **Step 1: 下载 PRD 设计图当像素基准**（实现前先取真值，不凭感觉）

Run（Git Bash）:
```bash
GROOT="$(npm root -g)"; L(){ node "$GROOT/@larksuite/cli/scripts/run.js" "$@"; }
L docs +media-download --doc "https://eau5q7pdkqn.feishu.cn/docx/L7nUd3UIlosPQ3xrNWCcbdlqnxb" --out docs/superpowers/assets/skill-library/
```
Expected: 主屏 / 空态 / 上传中 / 成功 toast / 失败 / 回滚确认 / 删除确认 等截图落地，作为颜色/间距/字号取值基准（紫=主色、绿=当前、红=删除）。

- [ ] **Step 2: 写 API 客户端** — `web/src/api/skills.ts`

```typescript
import { api } from "./client"; // 若现有封装在别处，按 mcp.ts 的 api<T> 用法对齐

export interface Skill {
  id: number;
  name: string;
  slug: string;
  is_official: boolean;
  current_version_label: string | null;
  file_count: number;
  total_bytes: number;
  updated_at: string;
  uploaded_by: number | null;
}

export interface SkillVersion {
  id: number;
  version_label: string;
  bundle_sha256: string;
  file_count: number;
  total_bytes: number;
  uploaded_by: number | null;
  uploaded_at: string;
  is_current: boolean;
}

export function listSkills(): Promise<{ skills: Skill[] }> {
  return api<{ skills: Skill[] }>("/api/mcp/skills");
}

export function uploadSkill(name: string, files: File[]): Promise<{ skill_id: number; slug: string; version_label: string }> {
  const fd = new FormData();
  fd.append("name", name);
  for (const f of files) fd.append("files", f, (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name);
  return api("/api/mcp/skills/upload", { method: "POST", body: fd });
}

export function listSkillVersions(skillId: number): Promise<{ versions: SkillVersion[] }> {
  return api(`/api/mcp/skills/${skillId}/versions`);
}

export function setCurrentVersion(skillId: number, versionId: number): Promise<void> {
  return api(`/api/mcp/skills/${skillId}/set-current`, { method: "POST", body: JSON.stringify({ version_id: versionId }) });
}

export function deleteSkillVersion(skillId: number, versionId: number): Promise<void> {
  return api(`/api/mcp/skills/${skillId}/versions/${versionId}`, { method: "DELETE" });
}

export function deleteSkill(skillId: number): Promise<void> {
  return api(`/api/mcp/skills/${skillId}`, { method: "DELETE" });
}

export function skillDownloadUrl(skillId: number): string {
  return `/api/mcp/skills/${skillId}/download.zip`;
}

export function skillInstallCommand(slug: string): string {
  return `在 Claude Code 里调用 install_loop_skills（当前官方包 slug=${slug}）`;
}
```

> `api<T>` 的确切签名以 `web/src/api/mcp.ts` 现有实现为准（`uploadBundleVersion` 已展示 FormData + `api<T>` 用法）；upload 传 FormData 时不要手设 `Content-Type`。

- [ ] **Step 3: typecheck**

Run（Git Bash，注意 worktree cwd 漂移，用 -C 绝对路径）: `pnpm -C /e/geo/web typecheck`
Expected: 无 TS 错误。

- [ ] **Step 4: Commit**

```bash
git add web/src/api/skills.ts docs/superpowers/assets/skill-library/
git commit -m "feat(skill-library): 前端 API 客户端 + PRD 设计图基准"
```

---

### Task 10: 前端 Skill 库工作台组件（像素级还原）

**Files:**
- Create: `web/src/features/mcp/SkillLibrary.tsx`（容器 + 状态编排）
- Create: `web/src/features/mcp/skill-library/UploadZone.tsx`（拖拽区 + 四态）
- Create: `web/src/features/mcp/skill-library/SkillCard.tsx`（行卡 + 徽章 + 动作）
- Create: `web/src/features/mcp/skill-library/VersionHistory.tsx`（版本历史表 + 装机命令）
- Create: `web/src/features/mcp/skill-library/ConfirmDialog.tsx`（回滚紫 / 删除红二次确认）
- Test: 无单测框架 → typecheck + build + 对照截图目视。

**Interfaces:**
- Consumes: `web/src/api/skills.ts`（Task 9）；当前用户 role（从现有 auth context / `web/src/api` 现成获取方式取，用于删除按钮置灰）。
- Produces: `export function SkillLibrary()`。

- [ ] **Step 1: UploadZone（拖拽 + 四态 + 三步 tracker）** — `skill-library/UploadZone.tsx`

```tsx
import { useRef, useState } from "react";
import { UploadCloud, CheckCircle2, XCircle } from "lucide-react";
import { uploadSkill } from "../../../api/skills";

type Phase = "idle" | "receiving" | "validating" | "storing" | "done" | "error";

export function UploadZone({ onUploaded }: { onUploaded: () => void }) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [fileName, setFileName] = useState("");
  const [errMsg, setErrMsg] = useState("");
  const [result, setResult] = useState<{ slug: string; version_label: string } | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  async function handleFiles(files: File[]) {
    if (!files.length) return;
    // skill 名：单文件夹取顶层目录名；单 zip 取去扩展名；单 SKILL.md 取父目录或提示输入
    const name = deriveSkillName(files);
    setFileName(files.length === 1 ? files[0].name : `${files.length} 个文件`);
    setPhase("receiving");
    try {
      setPhase("validating");
      const r = await uploadSkill(name, files);
      setPhase("storing");
      setResult({ slug: r.slug, version_label: r.version_label });
      setPhase("done");
      onUploaded();
      setTimeout(() => setPhase("idle"), 5000); // 成功 toast ~5s
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : "上传失败");
      setPhase("error");
    }
  }
  // 拖拽区(虚线紫框 + cloud-upload)、上传中(进度 + 三步 tracker)、done(绿 toast)、error(红卡)
  // JSX 结构对照 docs/superpowers/assets/skill-library/ 截图逐一还原：
  // - idle: 虚线 border var(--accent)、UploadCloud、"拖拽 ZIP 或文件夹到此上传" + 选择文件按钮
  //         + 约束提示（≤5MB / 含 ≥1 SKILL.md / 同名追加不覆盖）
  //         input 两个：type=file multiple(选文件) 和 type=file webkitdirectory(选文件夹)
  //         onDrop 用 DataTransferItemList 递归读 entries
  // - receiving/validating/storing: 文件名 + 进度条 + 三步 tracker(接收文件→校验 SKILL.md→生成版本/入库)
  // - done: 绿勾 + `${result.slug} 已入库,新版本 ${result.version_label} 已设为当前,旧版本保留可回滚`
  //         + 文件数·SHA 徽章 + 「查看版本历史 →」
  // - error: 红卡 + errMsg(缺 SKILL.md / 超 5MB) + 「重新选择文件」
  return null; // 用上述结构填充
}

function deriveSkillName(files: File[]): string {
  const withPath = files.find((f) => (f as File & { webkitRelativePath?: string }).webkitRelativePath);
  if (withPath) {
    const rel = (withPath as File & { webkitRelativePath?: string }).webkitRelativePath!;
    return rel.split("/")[0];
  }
  const f = files[0];
  return f.name.toLowerCase().endsWith(".zip") ? f.name.replace(/\.zip$/i, "") : f.name;
}
```

> 实现 JSX 时对照截图取色/间距/字号；紫=`var(--accent)`、绿=当前态、红=错误/删除。拖拽读文件夹用 `webkitGetAsEntry` 递归。

- [ ] **Step 2: VersionHistory（版本历史表 + 装机命令 + 回滚/删单版本入口）** — `skill-library/VersionHistory.tsx`

```tsx
import { useEffect, useState } from "react";
import { listSkillVersions, setCurrentVersion, deleteSkillVersion, skillInstallCommand, type SkillVersion } from "../../../api/skills";

export function VersionHistory({ skill, canDelete, onChanged }: {
  skill: { id: number; slug: string; is_official: boolean };
  canDelete: (v: SkillVersion) => boolean;
  onChanged: () => void;
}) {
  const [versions, setVersions] = useState<SkillVersion[]>([]);
  const [confirm, setConfirm] = useState<{ kind: "rollback" | "delete"; v: SkillVersion } | null>(null);
  useEffect(() => { listSkillVersions(skill.id).then((r) => setVersions(r.versions)); }, [skill.id]);
  // 表列：版本 · 上传时间 · 上传人 · SHA-256 · 操作
  // 当前行：绿「当前 / 已是当前」(禁用)；历史行：紫「设为当前」→ setConfirm({kind:'rollback'})
  // 操作列删除按钮：canDelete(v) 决定是否置灰；点击 setConfirm({kind:'delete'})
  // 底部：可复制「让 Claude Code 自己装」命令块 = skillInstallCommand(skill.slug)
  // ConfirmDialog 回调里调 setCurrentVersion / deleteSkillVersion，成功后 onChanged()
  return null;
}
```

- [ ] **Step 3: SkillCard（行卡）** — `skill-library/SkillCard.tsx`

```tsx
import { useState } from "react";
import { skillDownloadUrl, type Skill, type SkillVersion } from "../../../api/skills";
import { VersionHistory } from "./VersionHistory";

export function SkillCard({ skill, isAdmin, currentUserId, onChanged, onDeleteSkill }: {
  skill: Skill; isAdmin: boolean; currentUserId: number | null;
  onChanged: () => void; onDeleteSkill: (s: Skill) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  // 字段：名称 + 徽章(官方紫 official / 自定义灰) + 当前版本 + 文件数 + 大小 + 更新时间 + 上传人
  // 动作：版本历史(展开) / ZIP(a href=skillDownloadUrl) / 删除整个 skill(仅 isAdmin 可点,否则灰)
  const canDeleteVersion = (v: SkillVersion) =>
    isAdmin || (!skill.is_official && v.uploaded_by === currentUserId);
  return null; // 展开时渲染 <VersionHistory .../>
}
```

- [ ] **Step 4: ConfirmDialog（回滚紫 / 删除红）** — `skill-library/ConfirmDialog.tsx`

```tsx
export function ConfirmDialog({ tone, title, body, confirmLabel, onCancel, onConfirm }: {
  tone: "purple" | "red"; title: string; body: React.ReactNode; confirmLabel: string;
  onCancel: () => void; onConfirm: () => void;
}) {
  // 遮罩 + 卡片；确认按钮色随 tone(紫=可逆回滚 / 红=不可逆删除)
  // 回滚文案：current→target diff + "不删除任何版本、可再切回、影响下载ZIP/Claude安装/install_loop_skills 三下游"
  // 删单版本：+ "永久删除该版本、当前版本不受影响"；删整个：+ "连同全部 N 版本永久删除、已装本机副本不受影响"
  return null;
}
```

- [ ] **Step 5: SkillLibrary（容器）** — `SkillLibrary.tsx`

```tsx
import { useCallback, useEffect, useState } from "react";
import { PackageOpen } from "lucide-react";
import { listSkills, deleteSkill, type Skill } from "../../api/skills";
import { UploadZone } from "./skill-library/UploadZone";
import { SkillCard } from "./skill-library/SkillCard";

export function SkillLibrary({ isAdmin, currentUserId }: { isAdmin: boolean; currentUserId: number | null }) {
  const [skills, setSkills] = useState<Skill[] | null>(null);
  const reload = useCallback(() => { listSkills().then((r) => setSkills(r.skills)); }, []);
  useEffect(() => { reload(); }, [reload]);
  // 布局：UploadZone 在上；下方 `已入库 SKILL · N` 列表(官方置顶)。
  // 空态：skills.length===0 → PackageOpen + "还没有任何 Skill" 引导回上传区。
  return null;
}
```

- [ ] **Step 6: typecheck + build**

Run: `pnpm -C /e/geo/web typecheck && pnpm -C /e/geo/web build`
Expected: 均通过。

- [ ] **Step 7: 对照截图目视核对**（用 /run 或 vite dev 起前端，逐态比对 `docs/superpowers/assets/skill-library/`：上传区/空态/上传中/成功 toast/失败/回滚确认/删除确认）。

- [ ] **Step 8: Commit**

```bash
git add web/src/features/mcp/SkillLibrary.tsx web/src/features/mcp/skill-library/
git commit -m "feat(skill-library): 前端 Skill 库工作台(像素级还原 PRD)"
```

---

### Task 11: 接入 Panel ⑤ + 下线旧版本管理 UI

**Files:**
- Modify: `web/src/features/mcp/McpConnectWorkspace.tsx`（Panel ⑤ 替换为 `<SkillLibrary/>`，移除旧 bundle 版本管理 state/函数）
- Modify: `web/src/api/mcp.ts`（移除仅旧 UI 使用的 `listBundleVersions/uploadBundleVersion/enableBundleVersion/deleteBundleVersion/bundleVersionDownloadUrl` 及 `BundleVersion` 类型；保留 `getMcpStatus/pingMcpHealth` 等仍用的）
- Test: typecheck + build。

- [ ] **Step 1: 替换 Panel ⑤** — 在 `McpConnectWorkspace.tsx` 里，把「版本管理 · 上传 / 启用 / 回退 / 删除」整段（含 `versions/uploadLabel/uploadNotes` state、`onUploadZip/onEnableVersion/onDeleteVersion` 等）删除，改渲染：
```tsx
import { SkillLibrary } from "./SkillLibrary";
// ...在 Panel ⑤ 位置：
<SkillLibrary isAdmin={/* 从现有用户信息取 */} currentUserId={/* 同 */} />
```
当前用户 role/id 的取法沿用该文件已有的用户信息来源（若无，从 `getMcpStatus` 或 auth context 现成方式取；不新引依赖）。

- [ ] **Step 2: 清理 mcp.ts 死代码** — 删除仅旧 UI 用到的 bundle 版本管理导出（`listBundleVersions` 等）与 `BundleVersion` 类型。`getLoopSkillBundleInfo` / `LOOP_SKILL_BUNDLE_DOWNLOAD_URL` 若不再被引用一并删。

- [ ] **Step 3: typecheck + build**

Run: `pnpm -C /e/geo/web typecheck && pnpm -C /e/geo/web build`
Expected: 均通过，无未引用导入报错（strict + eslint）。

- [ ] **Step 4: Commit**

```bash
git add web/src/features/mcp/McpConnectWorkspace.tsx web/src/api/mcp.ts
git commit -m "feat(skill-library): Panel ⑤ 接入 SkillLibrary,下线旧单bundle版本管理UI"
```

---

## Self-Review

**Spec 覆盖核对**（逐条对 spec）：
- §3 数据模型 → Task 1（两表 + 生成列唯一 + 判别位）✅
- §3.3 存储读写路径（DB/MinIO 分叉）→ Task 2 ✅
- §4.1 上传校验顺序 + 放宽白名单 → Task 3 + Task 4 ✅
- §4 接口族 8 端点 → Task 6（skills 系列）+ Task 7（install-payload by slug 已在 Task 6 mcp_router；旧别名 Task 7）✅
- §4.2 并发去 GET_LOCK（per-skill 指针 UPDATE）→ Task 5 `set_current` ✅
- §4.3 权限矩阵（属主/admin/官方 admin-only/删整个 admin）→ Task 5 + Task 6（403 包装）✅
- §5 兼容与迁移（别名/工具重指/version.py 退场/旧表休眠）→ Task 7 ✅
- §5 seed（挂现有 bundle / templates 兜底 / 幂等）→ Task 8 ✅
- §6 前端工作台（四态/列表/徽章/版本历史/二次确认/置灰）→ Task 9-11 ✅
- §7 审计埋点（upload/set_current/delete）→ Task 6 `add_audit_entry` ✅；统计报表延后（非本计划）✅
- §8 测试（上传/追加/回滚/删除矩阵/seed/兼容）→ Task 1-8 各自测试 ✅

**类型一致性**：`SkillListItem` 字段 == `SkillMeta` schema 字段（id/name/slug/is_official/current_version_label/file_count/total_bytes/updated_at/uploaded_by）✅；`VersionItem` == `SkillVersionMeta` ✅；前端 `Skill`/`SkillVersion` 与后端 schema 字段名一致 ✅。

**占位符扫描**：前端 Task 10 组件体用 `return null` + 结构注释而非成品 JSX —— 这是像素级还原必须对照 Task 9 下载的截图取真值，故意留给实现者按截图填充（已在 Global Constraints / Task 9 明确基准来源），不是遗漏逻辑；state/handler/API 调用均已给全。

**已知裁剪**：MinIO 写路径（`save_files_to_minio`）本期建好且单测覆盖但上传不触发（spec 明确）；旧 `list_bundle_versions` 端点保留与否不影响功能，Task 7 默认保留只读别名。
