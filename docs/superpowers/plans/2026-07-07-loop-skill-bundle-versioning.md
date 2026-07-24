# Loop Skill 包版本化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `/goal` Loop skill 包正本从"扫 `templates/` 文件夹"改成"上传 zip 入库、DB 版本管理(启用/回退/逻辑删除),Claude Code 或网页按版本投递到本机 `.claude/skills/`",三个出口端点只换一个读取函数、用户端零改。

**Architecture:** 新表 `loop_skill_bundle_versions` 存**解压后**的文件列表(JSON 列),`get_active_bundle(session)` 有启用版用之、否则回落 `build_bundle()` 扫种子。三个出口端点(`/info`、`/download.zip`、`/install-payload`)改调新读取函数并注入 `Depends(get_db)`。**上传开放给所有登录用户,启用/删除/软删收归 admin(`require_admin`)**——启用版内容会被写进每个同事的 `.claude/skills/` 并由 Claude Code 执行,属供应链面,写权限收窄;每步写审计。启用单例靠 MySQL `GET_LOCK` 应用级锁保证,且**锁的 GET/RELEASE 必须钉在同一条 `Connection` 上**(见 Task 5)。

> **评审合入(rev 2026-07-07b)**:本版相对初稿吸收了三方评审的三个必修 + 两个决策:
> **(A)** GET_LOCK 连接亲和——原计划 `session.commit()` 归还连接后再 `RELEASE_LOCK` 会落到别的连接、原锁泄漏 → 改为独占一条 `Connection` 完成 GET_LOCK→updates→commit→RELEASE(Task 5)。
> **(B)** `version_label` 非 ASCII(spec 例子就是中文"严格版")直接进 `Content-Disposition`/`X-Bundle-Version` 会 500 / header 注入 → 下载名用 ASCII slug + RFC 5987 `filename*`,label 入库前 strip+限长+拒控制字符(Task 4/6)。
> **(C)** zip 校验缺条目数/累计解压体积/重名限制 → 补 `MAX_ENTRIES` + 累计上限 + 重名拒绝(Task 4)。
> **决策1**:上传开放、enable/delete 收归 admin(供应链面)。**决策2**:`get_bundle_by_id` 按 id 下载不存在/已删 → **404**(不再 400);`install-payload?version=` 找不到 → `{ok:false, data:{available:[...]}}`(兑现 spec §L168,不再"本期不做")。

**Tech Stack:** FastAPI · SQLAlchemy 2.0(`Mapped`/`mapped_column`)· Alembic · MySQL(`GET_LOCK`/JSON 列)· React 19 + TypeScript(前端无单测,typecheck+build 为门禁)· FastMCP(`install_loop_skills` 工具)。

## Global Constraints

- **MySQL only**;后端 DB 测试需 `GEO_TEST_DATABASE_URL`(DB 名必须含 `test`)。命令形如:
  `GEO_TEST_DATABASE_URL="mysql+pymysql://<user>:<pass>@<host>:<port>/geo_test" python -m pytest <file> -q`。
  本机(Windows)先 `conda activate geo_xzpt`;若 `pytest` 未在 PATH,用该环境的 `python -m pytest`(见记忆 run-tests-env)。无 DB 时 `@pytest.mark.mysql` 用例自动 skip。
- **service 层抛命名异常**(`ValidationError`/`ConflictError`/`ClientError`,`server/app/shared/errors.py`),**不抛裸 `ValueError`**。`ValidationError`→400、`ConflictError`→409(`main.py` 全局 handler)。
- **FK/PK 用 `Integer`**(`users.id` 是 INT;BIGINT FK→INT 主键触发 MySQL errno 150)。
- **bundle sha 算法不变**:`build_bundle_from_file_map` 必须按 **posix 字符串排序** + 逐文件 `h.update(path)+\x00+filesha+\x00`,保证 `build_bundle()` 的 sha 与现有 `KNOWN_BUNDLE_SHAS` 一致(回归测试 `test_bundle_sha_is_known` 必须仍绿)。
- **`MCP_TOOLS_COUNT` 保持 21**(`mcp_catalog/connect_router.py`):`install_loop_skills` 只加可选参数、不加新工具。
- **前端门禁 = `pnpm --filter @geo/web typecheck` + `pnpm --filter @geo/web build`**(无 vitest/jest)。worktree 里 Bash cwd 会漂,统一用 `pnpm --filter @geo/web ...`(见记忆 gotcha-worktree-bash-cwd)。
- **分支**:当前在 `main`。执行前先 `git checkout -b feat/loop-skill-bundle-versioning`,每个 Task 结束提交(遵守 repo「不在 main 直接提交」)。
- **回落语义保命**:`/info` 与 `/install-payload` 的两个既有测试硬断言 `len(files)==5`,改造后靠"测试库无启用版→回落种子=5"通过 → 保持回落 + `/install-payload` 响应壳 `{ok,data:{version,bundle_sha256,install_hint,files},error}` 不变。
- **锁连接亲和(A,必守)**:`GET_LOCK` 是 MySQL **连接级**锁;`Session.commit()` 会把连接归还池、下条语句可能换连接。**enable/软删的 GET_LOCK→updates→commit→RELEASE_LOCK 必须全部在同一条独占 `Connection` 上完成**(`session.get_bind().connect()`),严禁在 `session` 上 commit 后再 RELEASE(会把锁泄漏在池里的旧连接上、后续启用持续 10s→409)。`Connection.commit()` 不归还物理连接(与 `Session.commit()` 关键区别),故同 conn 安全。
- **写权限收窄(决策1)**:`upload` 走 `get_current_user`(所有登录用户);`enable`/`delete` 走 `require_admin`(`server/app/core/security.py:105`)。`require_admin` 既鉴权又回传 admin User 供审计。
- **下载头 ASCII 安全(B,必守)**:任何把 `version`(=用户上传的 `version_label`,可含中文/控制字符)放进响应头的地方,文件名用 ASCII slug(`geo-loop-skills-<sha12>.zip`)+ RFC 5987 `filename*=UTF-8''<pct-encoded>`;`X-Bundle-Version` 用 `urllib.parse.quote` 百分号编码;`X-Bundle-Sha256`(hex)天然 ASCII 安全。
- **zip 资源上限(C,必守)**:压缩总字节 `LOOP_SKILL_MAX_ZIP_BYTES`(2MB)+ 条目数 `LOOP_SKILL_MAX_ENTRIES`(50)+ 单条目解压 `LOOP_SKILL_MAX_ENTRY_BYTES`(2MB)+ **累计解压** `LOOP_SKILL_MAX_TOTAL_UNCOMPRESSED`(4MB)+ **重名路径拒绝**(白名单不限数量,靠这几道挡海量小文件/高压缩比 zip bomb)。`version_label≤200` / `notes≤500` 且拒控制字符。

## File Structure

| 文件 | 责任 | 动作 |
|---|---|---|
| `server/app/modules/loop_skills/models.py` | `LoopSkillBundleVersion` ORM 表 | **建** |
| `server/app/modules/loop_skills/service.py` | 纯文件打包逻辑;抽 `build_bundle_from_file_map` 供上传/扫描共用 | 改 |
| `server/app/modules/loop_skills/versions_service.py` | DB 读写:读取 seam + 上传/启用/删除/列表 | **建** |
| `server/app/modules/loop_skills/schemas.py` | 版本元信息 Pydantic 响应模型 | **建** |
| `server/app/modules/loop_skills/router.py` | 改 3 出口端点 + 加 5 管理端点 | 改 |
| `server/tests/utils.py` | `_model_modules()` 加新模块 import(**否则测试库不建表**) | 改 |
| `server/alembic/env.py` | 逐模块 import 加新模块(autogenerate 一致性) | 改 |
| `server/alembic/versions/0056_loop_skill_bundle_versions.py` | 建表迁移 | **建** |
| `server/mcp/tools/action.py` | `install_loop_skills` 加可选 `version` + 改 docstring 文案 | 改 |
| `web/src/api/mcp.ts` | 版本管理 API 客户端函数 | 改 |
| `web/src/features/mcp/McpConnectWorkspace.tsx` | Section ⑤ 下加「版本管理」块 | 改 |
| `server/tests/test_loop_skill_bundle_versions.py` | 新特性全部后端测试 | **建** |

---

### Task 1: DB 模型 + 注册 + 迁移

**Files:**
- Create: `server/app/modules/loop_skills/models.py`
- Modify: `server/tests/utils.py`(`_model_modules()`)、`server/alembic/env.py`
- Create: `server/alembic/versions/0056_loop_skill_bundle_versions.py`
- Test: `server/tests/test_loop_skill_bundle_versions.py`

**Interfaces:**
- Produces: `LoopSkillBundleVersion`(ORM),列:`id:int` `version_label:str` `bundle_sha256:str` `files:list(deferred)` `file_count:int` `total_size:int` `is_enabled:bool` `is_deleted:bool` `uploaded_by_user_id:int|None` `notes:str|None` `created_at` `updated_at`。

- [ ] **Step 1: 写失败测试**(建 `server/tests/test_loop_skill_bundle_versions.py`)

```python
"""loop_skills 版本化(上传入库 + 版本管理)测试。"""
from __future__ import annotations

import io
import zipfile

import pytest


def _make_zip(files: dict[str, str]) -> bytes:
    """把 {posix_path: text} 打成 zip bytes。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, text in files.items():
            zf.writestr(path, text)
    return buf.getvalue()


def _valid_files() -> dict[str, str]:
    """一份满足结构校验的最小合法 skill 包。"""
    return {
        "README.md": "# test bundle\n",
        "commands/goal.md": "# goal\n内容\n",
        "skills/geo-goal-orchestrator/SKILL.md": "---\nname: x\n---\norchestrator\n",
    }


@pytest.mark.mysql
def test_can_persist_bundle_version(monkeypatch):
    """能建一行 LoopSkillBundleVersion 并读回 —— 证明表已建 + 模型已注册。"""
    from server.tests.utils import build_test_app

    test_app = build_test_app(monkeypatch)
    try:
        from server.app.db.session import SessionLocal
        from server.app.modules.loop_skills.models import LoopSkillBundleVersion

        with SessionLocal() as db:
            row = LoopSkillBundleVersion(
                version_label="v-test",
                bundle_sha256="0" * 64,
                files=[{"path": "README.md", "content": "x", "sha256": "0" * 64, "size": 1}],
                file_count=1,
                total_size=1,
                is_enabled=False,
                is_deleted=False,
                uploaded_by_user_id=None,
                notes=None,
            )
            db.add(row)
            db.commit()
            got = db.get(LoopSkillBundleVersion, row.id)
            assert got is not None
            assert got.version_label == "v-test"
            assert got.is_enabled is False
    finally:
        test_app.cleanup()
```

- [ ] **Step 2: 运行,确认失败**

Run: `GEO_TEST_DATABASE_URL="..." python -m pytest server/tests/test_loop_skill_bundle_versions.py::test_can_persist_bundle_version -q`
Expected: FAIL —— `ModuleNotFoundError`/`ImportError: cannot import name 'LoopSkillBundleVersion'`(模型尚不存在)。

- [ ] **Step 3: 建模型** `server/app/modules/loop_skills/models.py`

```python
"""loop_skills ORM 模型 —— skill 包版本(上传入库 + 版本管理)。

一行 = 一个上传的 skill 包版本;files 存解压后文件列表(JSON),下载时由
build_zip 现组 zip。is_enabled 全局唯一(GET_LOCK 应用级锁保证),is_deleted 逻辑删除。
FK/PK 用 Integer 对齐 users.id(BIGINT→INT 会触发 MySQL errno 150)。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, deferred, mapped_column

from server.app.core.time import utcnow
from server.app.db.base import Base


class LoopSkillBundleVersion(Base):
    __tablename__ = "loop_skill_bundle_versions"
    __table_args__ = (
        Index("ix_loop_skill_bundle_enabled", "is_enabled", "is_deleted"),
        Index("ix_loop_skill_bundle_deleted", "is_deleted"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version_label: Mapped[str] = mapped_column(String(200))
    bundle_sha256: Mapped[str] = mapped_column(String(64))
    # files 是大 JSON([{path,content,sha256,size}]);列表查询不需要它 → deferred 惰性加载,
    # 只有组包给具体版本时才拉。file_count/total_size 去规范化冗余,让列表零成本。
    files: Mapped[list] = deferred(mapped_column(JSON))
    file_count: Mapped[int] = mapped_column(Integer)
    total_size: Mapped[int] = mapped_column(Integer)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    uploaded_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
```

- [ ] **Step 4: 注册模型**——在 `server/tests/utils.py` 的 `_model_modules()` 里(其它 `import ...models` 之间,按字母序放在 `image_library` 后)加:

```python
    import server.app.modules.loop_skills.models  # noqa: F401
```

同样在 `server/alembic/env.py` 顶部逐模块 import 区(`image_library` 那行后)加同一行:

```python
import server.app.modules.loop_skills.models  # noqa: F401
```

- [ ] **Step 5: 写迁移** `server/alembic/versions/0056_loop_skill_bundle_versions.py`

```python
"""loop_skill_bundle_versions: skill 包版本入库 + 版本管理.

新表存放上传的 /goal skill 包版本(解压后文件列表存 JSON),支持启用/回退/逻辑删除。
get_active_bundle 有启用版用之、否则回落 templates/ 种子。

Revision ID: 0056
Revises: 0055
Create Date: 2026-07-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0056"
down_revision: str | None = "0055"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "loop_skill_bundle_versions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("version_label", sa.String(length=200), nullable=False),
        sa.Column("bundle_sha256", sa.String(length=64), nullable=False),
        sa.Column("files", sa.JSON(), nullable=False),
        sa.Column("file_count", sa.Integer(), nullable=False),
        sa.Column("total_size", sa.Integer(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "uploaded_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_loop_skill_bundle_enabled",
        "loop_skill_bundle_versions",
        ["is_enabled", "is_deleted"],
    )
    op.create_index(
        "ix_loop_skill_bundle_deleted",
        "loop_skill_bundle_versions",
        ["is_deleted"],
    )


def downgrade() -> None:
    op.drop_index("ix_loop_skill_bundle_enabled", table_name="loop_skill_bundle_versions")
    op.drop_index("ix_loop_skill_bundle_deleted", table_name="loop_skill_bundle_versions")
    op.drop_table("loop_skill_bundle_versions")
```

- [ ] **Step 6: 运行,确认通过**

Run: `GEO_TEST_DATABASE_URL="..." python -m pytest server/tests/test_loop_skill_bundle_versions.py::test_can_persist_bundle_version -q`
Expected: PASS。

- [ ] **Step 7: 跑迁移回归**(确认新迁移能从空库升到 head)

Run: `GEO_TEST_DATABASE_URL="..." python -m pytest server/tests/test_fts_and_migrations.py -q`
Expected: PASS(`test_alembic_upgrade_from_empty_mysql_to_head` 用 issubset 断言,对新表宽容)。

- [ ] **Step 8: Commit**

```bash
git add server/app/modules/loop_skills/models.py server/tests/utils.py server/alembic/env.py server/alembic/versions/0056_loop_skill_bundle_versions.py server/tests/test_loop_skill_bundle_versions.py
git commit -m "feat(loop-skills): loop_skill_bundle_versions 表 + 注册 + 迁移"
```

---

### Task 2: `service.py` 抽取 `build_bundle_from_file_map`(sha 算法复用)

**Files:**
- Modify: `server/app/modules/loop_skills/service.py`
- Test: `server/tests/test_loop_skill_bundle.py`(现有回归)、`test_loop_skill_bundle_versions.py`(新)

**Interfaces:**
- Produces: `build_bundle_from_file_map(raw: dict[str, bytes], *, version: str) -> SkillBundle` —— 给定 `{posix_path: raw_bytes}` 产出排序好的 bundle,与 `build_bundle()` 同 sha 算法。
- Consumes(不变):`SkillFile(path,size,sha256,content)`、`SkillBundle(version,bundle_sha256,files)`。

- [ ] **Step 1: 写失败测试**(追加到 `test_loop_skill_bundle_versions.py`)

```python
def test_build_bundle_from_file_map_matches_algorithm():
    """from_file_map 对同一批字节,算出的 sha 与手工按算法算的一致 + 文件按 posix 序。"""
    import hashlib

    from server.app.modules.loop_skills.service import build_bundle_from_file_map

    raw = {
        "commands/goal.md": b"g",
        "README.md": b"r",
        "skills/geo-goal-orchestrator/SKILL.md": b"o",
    }
    bundle = build_bundle_from_file_map(raw, version="v-x")
    # 文件按 posix 串排序:README.md < commands/... < skills/...
    assert [f.path for f in bundle.files] == [
        "README.md",
        "commands/goal.md",
        "skills/geo-goal-orchestrator/SKILL.md",
    ]
    # 手工复算 bundle sha
    h = hashlib.sha256()
    for f in bundle.files:
        h.update(f.path.encode("utf-8"))
        h.update(b"\x00")
        h.update(f.sha256.encode("ascii"))
        h.update(b"\x00")
    assert bundle.bundle_sha256 == h.hexdigest()
    assert bundle.version == "v-x"
```

- [ ] **Step 2: 运行,确认失败**

Run: `python -m pytest server/tests/test_loop_skill_bundle_versions.py::test_build_bundle_from_file_map_matches_algorithm -q`
Expected: FAIL —— `ImportError: cannot import name 'build_bundle_from_file_map'`。

- [ ] **Step 3: 重构 `service.py`**——把 `build_bundle()` 拆成"读字节"+"打包",打包核心可复用。替换现有 `build_bundle()` 函数体为:

```python
def build_bundle_from_file_map(raw: dict[str, bytes], *, version: str) -> SkillBundle:
    """给定 {posix_path: raw_bytes} 构造排序好的 bundle。上传路径与文件夹扫描共用同一 sha 算法。

    非 utf-8 抛 ValueError —— 上传路径必须在调用前自行校验 utf-8 并抛 ValidationError,
    此处只当内部不变式(种子模板必是文本)。
    """
    files: list[SkillFile] = []
    for rel in sorted(raw.keys()):  # 按 posix 字符串排序(跨 OS 确定)
        data = raw[rel]
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"loop_skills template not UTF-8: {rel}") from exc
        files.append(
            SkillFile(
                path=rel,
                size=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                content=content,
            )
        )

    h = hashlib.sha256()
    for f in files:
        h.update(f.path.encode("utf-8"))
        h.update(b"\x00")
        h.update(f.sha256.encode("ascii"))
        h.update(b"\x00")

    return SkillBundle(version=version, bundle_sha256=h.hexdigest(), files=files)


def build_bundle() -> SkillBundle:
    """扫描 templates/ 下所有文件,返回 bundle(种子/兜底路径)。"""
    raw: dict[str, bytes] = {}
    for path in _TEMPLATES_DIR.rglob("*"):
        if not path.is_file():
            continue
        raw[path.relative_to(_TEMPLATES_DIR).as_posix()] = path.read_bytes()
    return build_bundle_from_file_map(raw, version=LOOP_SKILL_BUNDLE_VERSION)
```

(保留原有 `import hashlib` 等 import、`SkillFile`/`SkillBundle` 定义、`build_zip` 不动。)

- [ ] **Step 4: 运行新测试 + 全量回归**

Run: `python -m pytest server/tests/test_loop_skill_bundle_versions.py::test_build_bundle_from_file_map_matches_algorithm server/tests/test_loop_skill_bundle.py -q`
Expected: PASS,尤其 `test_bundle_sha_is_known` 与 `test_build_bundle_lists_all_template_files`(5 文件)仍绿 —— 证明重构没改变种子 sha。

- [ ] **Step 5: Commit**

```bash
git add server/app/modules/loop_skills/service.py server/tests/test_loop_skill_bundle_versions.py
git commit -m "refactor(loop-skills): 抽 build_bundle_from_file_map 复用打包/sha 算法"
```

---

### Task 3: `versions_service` 读取 seam + 列表

**Files:**
- Create: `server/app/modules/loop_skills/versions_service.py`
- Test: `server/tests/test_loop_skill_bundle_versions.py`

**Interfaces:**
- Produces:
  - `VersionMeta`(dataclass:`id,version_label,bundle_sha256,file_count,total_size,is_enabled,is_deleted,uploaded_by_user_id,notes,created_at`)
  - `get_active_bundle(session) -> SkillBundle`(有启用版返回它、否则 `build_bundle()`)
  - `get_bundle_by_id(session, version_id:int) -> SkillBundle`
  - `resolve_bundle_for_install(session, version: str|None) -> SkillBundle`
  - `list_versions(session) -> list[VersionMeta]`
  - 常量:`LOOP_SKILL_MAX_ZIP_BYTES` / `LOOP_SKILL_MAX_ENTRY_BYTES` / `LOOP_SKILL_MAX_TOTAL_UNCOMPRESSED` / `LOOP_SKILL_MAX_ENTRIES` / `LOOP_SKILL_MAX_LABEL_LEN` / `LOOP_SKILL_MAX_NOTES_LEN` / `_ENABLE_LOCK` / 白名单 / 必需文件(Task 4/5 复用)

- [ ] **Step 1: 写失败测试**(追加)

```python
@pytest.mark.mysql
def test_get_active_bundle_falls_back_to_seed(monkeypatch):
    """无启用版 → get_active_bundle 回落种子 build_bundle()(5 文件)。"""
    from server.tests.utils import build_test_app

    test_app = build_test_app(monkeypatch)
    try:
        from server.app.db.session import SessionLocal
        from server.app.modules.loop_skills import versions_service as vs

        with SessionLocal() as db:
            active = vs.get_active_bundle(db)
            assert len(active.files) == 5  # 种子
            assert vs.list_versions(db) == []  # 库里还没有上传版
    finally:
        test_app.cleanup()
```

- [ ] **Step 2: 运行,确认失败**

Run: `python -m pytest server/tests/test_loop_skill_bundle_versions.py::test_get_active_bundle_falls_back_to_seed -q`
Expected: FAIL —— `ModuleNotFoundError: ...versions_service`。

- [ ] **Step 3: 建 `versions_service.py`**(本 Task 只放读取 seam + 列表 + 常量;上传/启用/删除 Task 4/5 追加)

```python
"""loop_skills 版本管理 —— DB 读写(与纯文件逻辑的 service.py 分离)。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.app.modules.loop_skills.models import LoopSkillBundleVersion
from server.app.modules.loop_skills.service import (
    SkillBundle,
    SkillFile,
    build_bundle,
    build_bundle_from_file_map,  # noqa: F401  (Task 4 用)
)
from server.app.shared.errors import ValidationError

# 上传 zip 压缩后体积上限:几个 md 几十 KB 足够,2MB 防滥用
LOOP_SKILL_MAX_ZIP_BYTES = 2 * 1024 * 1024
# 单条目解压后上限(照搬 accounts import 范式)
LOOP_SKILL_MAX_ENTRY_BYTES = 2 * 1024 * 1024
# 解压后累计体积上限(防高压缩比 zip bomb:2MB 压缩包可膨胀到 GB 级)
LOOP_SKILL_MAX_TOTAL_UNCOMPRESSED = 4 * 1024 * 1024
# 条目数上限(白名单不限数量,靠它挡"海量小文件"膨胀)
LOOP_SKILL_MAX_ENTRIES = 50
# version_label / notes 长度上限(且拒控制字符,防 header 注入 + DB 膨胀)
LOOP_SKILL_MAX_LABEL_LEN = 200
LOOP_SKILL_MAX_NOTES_LEN = 500
# zip 路径白名单
_ALLOWED_TOP = ("README.md",)
_ALLOWED_PREFIXES = ("commands/", "skills/")
_REQUIRED = ("commands/goal.md", "skills/geo-goal-orchestrator/SKILL.md")
# 启用/删除共用的应用级锁名
_ENABLE_LOCK = "geo_loop_skill_enable"


@dataclass(frozen=True)
class VersionMeta:
    id: int
    version_label: str
    bundle_sha256: str
    file_count: int
    total_size: int
    is_enabled: bool
    is_deleted: bool
    uploaded_by_user_id: int | None
    notes: str | None
    created_at: datetime


def _to_meta(row: LoopSkillBundleVersion) -> VersionMeta:
    # 只读非 deferred 列 —— 不触发 files 加载
    return VersionMeta(
        id=row.id,
        version_label=row.version_label,
        bundle_sha256=row.bundle_sha256,
        file_count=row.file_count,
        total_size=row.total_size,
        is_enabled=row.is_enabled,
        is_deleted=row.is_deleted,
        uploaded_by_user_id=row.uploaded_by_user_id,
        notes=row.notes,
        created_at=row.created_at,
    )


def _row_to_bundle(row: LoopSkillBundleVersion) -> SkillBundle:
    files = [SkillFile(**f) for f in row.files]  # 此处才触发 deferred files 加载
    return SkillBundle(
        version=row.version_label, bundle_sha256=row.bundle_sha256, files=files
    )


def get_active_bundle(session: Session) -> SkillBundle:
    # order_by(id.desc()).first() —— 不用 .one():容忍并发窗口内瞬时双启用,取最新一条
    row = (
        session.execute(
            select(LoopSkillBundleVersion)
            .where(LoopSkillBundleVersion.is_enabled.is_(True))
            .where(LoopSkillBundleVersion.is_deleted.is_(False))
            .order_by(LoopSkillBundleVersion.id.desc())
        )
        .scalars()
        .first()
    )
    return _row_to_bundle(row) if row is not None else build_bundle()


def get_bundle_by_id(session: Session, version_id: int) -> SkillBundle:
    row = session.get(LoopSkillBundleVersion, version_id)
    if row is None or row.is_deleted:
        raise ValidationError(f"skill 包版本不存在或已删除: {version_id}")
    return _row_to_bundle(row)


def resolve_bundle_for_install(session: Session, version: str | None) -> SkillBundle:
    """install/download 的版本解析:空→启用版;纯数字→id;否则按 label 取最新未删。"""
    if not version:
        return get_active_bundle(session)
    if version.isdigit():
        return get_bundle_by_id(session, int(version))
    row = (
        session.execute(
            select(LoopSkillBundleVersion)
            .where(LoopSkillBundleVersion.version_label == version)
            .where(LoopSkillBundleVersion.is_deleted.is_(False))
            .order_by(LoopSkillBundleVersion.id.desc())
        )
        .scalars()
        .first()
    )
    if row is None:
        raise ValidationError(f"找不到 skill 包版本: {version}")
    return _row_to_bundle(row)


def list_versions(session: Session) -> list[VersionMeta]:
    rows = (
        session.execute(
            select(LoopSkillBundleVersion)
            .where(LoopSkillBundleVersion.is_deleted.is_(False))
            .order_by(LoopSkillBundleVersion.id.desc())
        )
        .scalars()
        .all()
    )
    return [_to_meta(r) for r in rows]
```

- [ ] **Step 4: 运行,确认通过**

Run: `python -m pytest server/tests/test_loop_skill_bundle_versions.py::test_get_active_bundle_falls_back_to_seed -q`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add server/app/modules/loop_skills/versions_service.py server/tests/test_loop_skill_bundle_versions.py
git commit -m "feat(loop-skills): versions_service 读取 seam + 列表(回落种子)"
```

---

### Task 4: `upload_version`(解压 + 校验 + 入库)

**Files:**
- Modify: `server/app/modules/loop_skills/versions_service.py`
- Test: `server/tests/test_loop_skill_bundle_versions.py`

**Interfaces:**
- Produces: `upload_version(session, zip_bytes: bytes, *, version_label: str|None, notes: str|None, uploaded_by_user_id: int|None) -> VersionMeta`(默认 `is_enabled=False`;`add`+`flush`,不 commit)。

- [ ] **Step 1: 写失败测试**(追加;含合法上传 + 4 类拒斥)

```python
@pytest.mark.mysql
def test_upload_version_persists_unenabled(monkeypatch):
    from server.tests.utils import build_test_app

    test_app = build_test_app(monkeypatch)
    try:
        from server.app.db.session import SessionLocal
        from server.app.modules.loop_skills import versions_service as vs

        with SessionLocal() as db:
            meta = vs.upload_version(
                db, _make_zip(_valid_files()),
                version_label="v1", notes="note", uploaded_by_user_id=None,
            )
            db.commit()
            assert meta.is_enabled is False
            assert meta.version_label == "v1"
            assert meta.file_count == 3
            assert meta.total_size > 0
            assert len(meta.bundle_sha256) == 64
            # 未启用 → 列表可见、get_active 仍回落种子
            assert [m.id for m in vs.list_versions(db)] == [meta.id]
            assert len(vs.get_active_bundle(db).files) == 5
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_upload_version_rejections(monkeypatch):
    from server.tests.utils import build_test_app
    from server.app.shared.errors import ValidationError

    test_app = build_test_app(monkeypatch)
    try:
        from server.app.db.session import SessionLocal
        from server.app.modules.loop_skills import versions_service as vs

        with SessionLocal() as db:
            # 缺必需文件
            with pytest.raises(ValidationError):
                vs.upload_version(db, _make_zip({"README.md": "x"}),
                                  version_label=None, notes=None, uploaded_by_user_id=None)
            # 非白名单路径
            bad = dict(_valid_files()); bad["evil.sh"] = "rm -rf"
            with pytest.raises(ValidationError):
                vs.upload_version(db, _make_zip(bad),
                                  version_label=None, notes=None, uploaded_by_user_id=None)
            # zip-slip
            slip = dict(_valid_files()); slip["../escape.md"] = "x"
            with pytest.raises(ValidationError):
                vs.upload_version(db, _make_zip(slip),
                                  version_label=None, notes=None, uploaded_by_user_id=None)
            # 非 zip
            with pytest.raises(ValidationError):
                vs.upload_version(db, b"not a zip",
                                  version_label=None, notes=None, uploaded_by_user_id=None)
            # 非 utf-8 成员
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                for p, t in _valid_files().items():
                    zf.writestr(p, t)
                zf.writestr("skills/x/bin.md", b"\xff\xfe\x00binary")
            with pytest.raises(ValidationError):
                vs.upload_version(db, buf.getvalue(),
                                  version_label=None, notes=None, uploaded_by_user_id=None)
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_upload_version_resource_and_label_limits(monkeypatch):
    """C:条目数 / 累计解压体积 / 重名;B:label 控制字符 / 超长 —— 全部 ValidationError。"""
    from server.tests.utils import build_test_app
    from server.app.shared.errors import ValidationError

    test_app = build_test_app(monkeypatch)
    try:
        from server.app.db.session import SessionLocal
        from server.app.modules.loop_skills import versions_service as vs

        with SessionLocal() as db:
            # 条目数超限:required + 60 个 skills/ 小文件 > 50
            too_many = _valid_files()
            for i in range(60):
                too_many[f"skills/pad/{i}.md"] = "x"
            with pytest.raises(ValidationError):
                vs.upload_version(db, _make_zip(too_many),
                                  version_label=None, notes=None, uploaded_by_user_id=None)

            # 累计解压体积超限:3 个 1.5MB 高压缩比文件(压缩后仍 < 2MB zip 上限,但解压累计 4.5MB > 4MB)
            big = _valid_files()
            for i in range(3):
                big[f"skills/big/{i}.md"] = "a" * (1_500_000)
            with pytest.raises(ValidationError):
                vs.upload_version(db, _make_zip(big),
                                  version_label=None, notes=None, uploaded_by_user_id=None)

            # 重名路径:zip 允许同名条目,dict-based _make_zip 造不出,直接用 ZipFile 写两次
            dup = io.BytesIO()
            with zipfile.ZipFile(dup, "w") as zf:
                for p, t in _valid_files().items():
                    zf.writestr(p, t)
                zf.writestr("commands/goal.md", "第二份 goal")  # 与 required 同名
            with pytest.raises(ValidationError):
                vs.upload_version(db, dup.getvalue(),
                                  version_label=None, notes=None, uploaded_by_user_id=None)

            # label 含换行(header 注入面)
            with pytest.raises(ValidationError):
                vs.upload_version(db, _make_zip(_valid_files()),
                                  version_label="a\r\nInjected: x", notes=None,
                                  uploaded_by_user_id=None)
            # label 超长
            with pytest.raises(ValidationError):
                vs.upload_version(db, _make_zip(_valid_files()),
                                  version_label="x" * 201, notes=None,
                                  uploaded_by_user_id=None)

            # 中文 label 合法(latin-1 由 router 头部 percent-encode 兜底,service 层放行)
            m = vs.upload_version(db, _make_zip(_valid_files()),
                                  version_label="2026-07-08 严格版", notes=None,
                                  uploaded_by_user_id=None)
            assert m.version_label == "2026-07-08 严格版"
    finally:
        test_app.cleanup()
```

- [ ] **Step 2: 运行,确认失败**

Run: `python -m pytest server/tests/test_loop_skill_bundle_versions.py::test_upload_version_persists_unenabled -q`
Expected: FAIL —— `AttributeError: module ...versions_service has no attribute 'upload_version'`。

- [ ] **Step 3: 加 `upload_version`**（在 `versions_service.py` 顶部补 import,再追加函数）

顶部 import 区加:

```python
import io
import zipfile

from server.app.core.time import utcnow
```

追加函数:

```python
def _clean_text(value: str, *, field: str, max_len: int) -> str:
    """strip + 长度上限 + 拒控制字符(换行/回车/制表 / DEL)。

    version_label 会被下游放进 HTTP 响应头(Content-Disposition / X-Bundle-Version);
    含 \\r\\n 会造成 header 注入,含中文会让 latin-1 编码崩(500)。这里先把控制字符挡掉、
    限长;非 ASCII(中文)本身允许,由 router 侧 percent-encode / RFC5987 兜底(见 Task 6)。
    """
    v = value.strip()
    if len(v) > max_len:
        raise ValidationError(f"{field} 过长(上限 {max_len} 字符)")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in v):
        raise ValidationError(f"{field} 含非法控制字符")
    return v


def upload_version(
    session: Session,
    zip_bytes: bytes,
    *,
    version_label: str | None,
    notes: str | None,
    uploaded_by_user_id: int | None,
) -> VersionMeta:
    if len(zip_bytes) > LOOP_SKILL_MAX_ZIP_BYTES:
        raise ValidationError(f"zip 超过压缩体积上限 {LOOP_SKILL_MAX_ZIP_BYTES} 字节")
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise ValidationError("不是合法的 zip 文件") from exc

    # C:条目数上限(白名单不限数量,先挡"海量小文件")
    infos = [i for i in zf.infolist() if not i.is_dir()]
    if len(infos) > LOOP_SKILL_MAX_ENTRIES:
        raise ValidationError(f"zip 条目过多: {len(infos)}(上限 {LOOP_SKILL_MAX_ENTRIES})")

    raw: dict[str, bytes] = {}
    total = 0
    for info in infos:
        name = info.filename.replace("\\", "/")
        # zip-slip / 绝对路径防护
        if name.startswith("/") or ".." in name.split("/"):
            raise ValidationError(f"非法路径(zip-slip): {info.filename}")
        # 白名单前缀(startswith 接受 tuple)
        if not (name in _ALLOWED_TOP or name.startswith(_ALLOWED_PREFIXES)):
            raise ValidationError(
                f"不允许的文件路径: {name}(仅 README.md / commands/ / skills/)"
            )
        # C:重名路径静默覆盖会让 sha 与实际内容脱节(zip 允许同名条目)→ 直接拒
        if name in raw:
            raise ValidationError(f"zip 含重复路径: {name}")
        # C:单条目解压上限
        if info.file_size > LOOP_SKILL_MAX_ENTRY_BYTES:
            raise ValidationError(f"解压后单文件过大: {name}")
        data = zf.read(info)
        # C:累计解压体积上限(防高压缩比 zip bomb)
        total += len(data)
        if total > LOOP_SKILL_MAX_TOTAL_UNCOMPRESSED:
            raise ValidationError(
                f"解压后累计体积超上限 {LOOP_SKILL_MAX_TOTAL_UNCOMPRESSED} 字节"
            )
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationError(f"非 UTF-8 文本文件: {name}") from exc
        raw[name] = data

    missing = [r for r in _REQUIRED if r not in raw]
    if missing:
        raise ValidationError(f"缺少必需文件: {', '.join(missing)}")

    # B:label / notes 清洗(strip + 限长 + 拒控制字符);label strip 后空则回落时间戳
    label = (
        _clean_text(version_label, field="version_label", max_len=LOOP_SKILL_MAX_LABEL_LEN)
        if version_label
        else ""
    )
    if not label:
        label = utcnow().strftime("%Y-%m-%d %H:%M:%S")
    clean_notes = (
        _clean_text(notes, field="notes", max_len=LOOP_SKILL_MAX_NOTES_LEN) if notes else None
    )
    bundle = build_bundle_from_file_map(raw, version=label)

    row = LoopSkillBundleVersion(
        version_label=label,
        bundle_sha256=bundle.bundle_sha256,
        files=[
            {"path": f.path, "content": f.content, "sha256": f.sha256, "size": f.size}
            for f in bundle.files
        ],
        file_count=len(bundle.files),
        total_size=sum(f.size for f in bundle.files),
        is_enabled=False,
        is_deleted=False,
        uploaded_by_user_id=uploaded_by_user_id,
        notes=clean_notes,
    )
    session.add(row)
    session.flush()  # 拿到 row.id;commit 交给 get_db / add_audit_entry
    return _to_meta(row)
```

- [ ] **Step 4: 运行,确认通过**

Run: `python -m pytest server/tests/test_loop_skill_bundle_versions.py::test_upload_version_persists_unenabled server/tests/test_loop_skill_bundle_versions.py::test_upload_version_rejections -q`
Expected: PASS(两个)。

- [ ] **Step 5: Commit**

```bash
git add server/app/modules/loop_skills/versions_service.py server/tests/test_loop_skill_bundle_versions.py
git commit -m "feat(loop-skills): upload_version 解压+校验+入库(默认不启用)"
```

---

### Task 5: `enable_version` + `soft_delete_version`(应用级锁 + 单例不变式)

**Files:**
- Modify: `server/app/modules/loop_skills/versions_service.py`
- Test: `server/tests/test_loop_skill_bundle_versions.py`

**Interfaces:**
- Produces(均在**独占的一条 Connection** 上跑 GET_LOCK→检查→UPDATE→commit→RELEASE,不动传入 session):
  - `enable_version(session, version_id: int) -> None`(锁内清其他 + 置本行 + commit)
  - `soft_delete_version(session, version_id: int) -> None`(拒删当前启用版)

- [ ] **Step 1: 写失败测试**(追加:顺序单例 + 拒删启用版 + 从零并发单例)

```python
@pytest.mark.mysql
def test_enable_is_singleton_and_switch(monkeypatch):
    from server.tests.utils import build_test_app

    test_app = build_test_app(monkeypatch)
    try:
        from server.app.db.session import SessionLocal
        from server.app.modules.loop_skills import versions_service as vs
        from server.app.modules.loop_skills.models import LoopSkillBundleVersion

        with SessionLocal() as db:
            a = vs.upload_version(db, _make_zip(_valid_files()), version_label="A",
                                  notes=None, uploaded_by_user_id=None)
            b = vs.upload_version(db, _make_zip(_valid_files()), version_label="B",
                                  notes=None, uploaded_by_user_id=None)
            db.commit()
            vs.enable_version(db, a.id)
            vs.enable_version(db, b.id)  # 切到 B
            enabled = db.execute(
                __import__("sqlalchemy").select(LoopSkillBundleVersion.id).where(
                    LoopSkillBundleVersion.is_enabled.is_(True)
                )
            ).scalars().all()
            assert enabled == [b.id]  # 恰好一个,且是 B
            assert vs.get_active_bundle(db).version == "B"
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_soft_delete_rejects_enabled(monkeypatch):
    from server.tests.utils import build_test_app
    from server.app.shared.errors import ConflictError

    test_app = build_test_app(monkeypatch)
    try:
        from server.app.db.session import SessionLocal
        from server.app.modules.loop_skills import versions_service as vs

        with SessionLocal() as db:
            a = vs.upload_version(db, _make_zip(_valid_files()), version_label="A",
                                  notes=None, uploaded_by_user_id=None)
            db.commit()
            vs.enable_version(db, a.id)
            with pytest.raises(ConflictError):
                vs.soft_delete_version(db, a.id)  # 当前启用版,拒删
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_enable_from_zero_concurrent_stays_singleton(monkeypatch):
    """从零启用态并发 enable(A)/enable(B) —— GET_LOCK 保证最终恰好一个启用版。"""
    import threading

    from server.tests.utils import build_test_app

    test_app = build_test_app(monkeypatch)
    try:
        from server.app.db.session import SessionLocal
        from server.app.modules.loop_skills import versions_service as vs
        from server.app.modules.loop_skills.models import LoopSkillBundleVersion

        with SessionLocal() as db:
            a = vs.upload_version(db, _make_zip(_valid_files()), version_label="A",
                                  notes=None, uploaded_by_user_id=None)
            b = vs.upload_version(db, _make_zip(_valid_files()), version_label="B",
                                  notes=None, uploaded_by_user_id=None)
            db.commit()
            ids = [a.id, b.id]

        barrier = threading.Barrier(2)
        errors: list[Exception] = []  # 捕获而非吞:DeepSeek 建议的 except:pass 会放大假绿

        def _worker(vid: int) -> None:
            s = SessionLocal()
            try:
                barrier.wait()
                vs.enable_version(s, vid)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                s.close()

        threads = [threading.Thread(target=_worker, args=(i,)) for i in ids]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 锁正确时二者应串行成功(各自临界区极短、10s 超时足够)——不应有 ConflictError
        assert errors == [], f"并发 enable 不应抛错(锁应让二者串行成功): {errors}"

        with SessionLocal() as db:
            enabled = db.execute(
                __import__("sqlalchemy").select(LoopSkillBundleVersion.id).where(
                    LoopSkillBundleVersion.is_enabled.is_(True)
                )
            ).scalars().all()
            assert len(enabled) == 1  # 恰好一个,不是两个

        # A 的杀手锏:证明锁**真释放**(初稿的 session.commit 后 RELEASE 落错连接=假绿抓不到)。
        # 用全新连接 GET_LOCK(name, 0) 立即取:若前面泄漏了锁,这里会返回 0。
        from sqlalchemy import text as _text

        from server.app.db.session import engine

        with engine.connect() as probe:
            got = probe.execute(
                _text("SELECT GET_LOCK(:k, 0)"), {"k": "geo_loop_skill_enable"}
            ).scalar()
            assert got == 1, "锁泄漏:enable 完成后应能立即再取同名锁"
            probe.execute(_text("SELECT RELEASE_LOCK(:k)"), {"k": "geo_loop_skill_enable"})
    finally:
        test_app.cleanup()
```

- [ ] **Step 2: 运行,确认失败**

Run: `python -m pytest server/tests/test_loop_skill_bundle_versions.py::test_enable_is_singleton_and_switch -q`
Expected: FAIL —— `AttributeError: ...has no attribute 'enable_version'`。

- [ ] **Step 3: 加 `enable_version` + `soft_delete_version`**（`versions_service.py` 顶部 import 补 `text, update`;`errors` import 补 `ConflictError`）

顶部 import 调整:

```python
from sqlalchemy import select, text, update
from server.app.shared.errors import ConflictError, ValidationError
```

追加函数:

```python
def enable_version(session: Session, version_id: int) -> None:
    """启用某版本(=回退)。单例不变式靠 MySQL GET_LOCK 应用级锁保证。

    ⚠️ 连接亲和(A,评审必修):GET_LOCK 是**连接级**锁,而 `Session.commit()` 会把连接
    归还池、下一条语句可能换连接 → 若在 session 上 commit 再 RELEASE_LOCK,释放会落到
    **别的连接**、原锁泄漏在池里(后续任何 enable 都 10s→409)。因此显式独占一条 `Connection`,
    GET_LOCK → 存在性检查 → 两条 UPDATE → commit → RELEASE_LOCK **全在同一 conn**;
    `Connection.commit()` 不归还物理连接(与 `Session.commit()` 的关键区别),故安全。
    传入的 `session` 本函数不用于写,只供 router 事后 add_audit_entry。
    """
    with session.get_bind().connect() as conn:
        got = conn.execute(text("SELECT GET_LOCK(:k, 10)"), {"k": _ENABLE_LOCK}).scalar()
        if got != 1:
            raise ConflictError("启用操作繁忙,请稍后重试")
        try:
            row = conn.execute(
                select(
                    LoopSkillBundleVersion.id, LoopSkillBundleVersion.is_deleted
                ).where(LoopSkillBundleVersion.id == version_id)
            ).first()
            if row is None or row.is_deleted:
                raise ValidationError(f"版本不存在或已删除: {version_id}")
            # 从零启用态并发也安全:两者都在锁内串行,后者 UPDATE 前能看到前者已提交的启用行
            conn.execute(
                update(LoopSkillBundleVersion)
                .where(LoopSkillBundleVersion.is_enabled.is_(True))
                .values(is_enabled=False)
            )
            conn.execute(
                update(LoopSkillBundleVersion)
                .where(LoopSkillBundleVersion.id == version_id)
                .values(is_enabled=True)
            )
            conn.commit()  # 同一 conn 上提交,锁仍持有;下一个 GET_LOCK 持有者看到最新状态
        finally:
            conn.execute(text("SELECT RELEASE_LOCK(:k)"), {"k": _ENABLE_LOCK})
    # with 退出 → conn.close() 兜底释放该连接上的所有命名锁(双保险)


def soft_delete_version(session: Session, version_id: int) -> None:
    """逻辑删除。拒删当前启用版(与 enable 同锁,消 check-then-act 竞态)。

    同 enable:GET_LOCK/updates/commit/RELEASE 全钉在独占的一条 Connection 上(见上)。
    """
    with session.get_bind().connect() as conn:
        got = conn.execute(text("SELECT GET_LOCK(:k, 10)"), {"k": _ENABLE_LOCK}).scalar()
        if got != 1:
            raise ConflictError("操作繁忙,请稍后重试")
        try:
            row = conn.execute(
                select(
                    LoopSkillBundleVersion.id,
                    LoopSkillBundleVersion.is_deleted,
                    LoopSkillBundleVersion.is_enabled,
                ).where(LoopSkillBundleVersion.id == version_id)
            ).first()
            if row is None or row.is_deleted:
                raise ValidationError(f"版本不存在或已删除: {version_id}")
            if row.is_enabled:
                raise ConflictError("不能删除当前启用版,请先启用别的版本再删")
            conn.execute(
                update(LoopSkillBundleVersion)
                .where(LoopSkillBundleVersion.id == version_id)
                .values(is_deleted=True)
            )
            conn.commit()
        finally:
            conn.execute(text("SELECT RELEASE_LOCK(:k)"), {"k": _ENABLE_LOCK})
```

> 注:`enable`/`soft_delete` 在**独立 conn** 上完成写+提交+释放锁,**不动传入的 `session`**;返回后 router 用同一 `session` 调 `add_audit_entry`(自带 commit),读到的是已提交的新状态。`get_db` 收尾 commit 对 session 是 no-op。审计写在锁外(spec §1「审计在锁内同事务」的原述已被本 rev 取代——审计只是日志、后写无碍,且锁必须尽早释放;spec 已同步订正)。

- [ ] **Step 4: 运行,确认通过**

Run: `python -m pytest server/tests/test_loop_skill_bundle_versions.py -q -k "enable or soft_delete"`
Expected: PASS(含从零并发单例)。

- [ ] **Step 5: Commit**

```bash
git add server/app/modules/loop_skills/versions_service.py server/tests/test_loop_skill_bundle_versions.py
git commit -m "feat(loop-skills): enable/soft_delete + GET_LOCK 单例不变式"
```

---

### Task 6: `schemas` + `router`(改 3 出口 + 加 5 管理端点)

**Files:**
- Create: `server/app/modules/loop_skills/schemas.py`
- Modify: `server/app/modules/loop_skills/router.py`
- Test: `server/tests/test_loop_skill_bundle_versions.py`、`test_loop_skill_bundle.py`(回归)

**Interfaces:**
- Produces(HTTP):
  - `GET /api/mcp/loop-skill-bundle/versions` → `{versions:[BundleVersionMeta]}`(user JWT)
  - `POST /api/mcp/loop-skill-bundle/versions`(multipart `file`+`version_label?`+`notes?`)→ `BundleVersionMeta`(**user JWT,所有登录用户**)
  - `POST /api/mcp/loop-skill-bundle/versions/{id}/enable` → 204(**require_admin**)
  - `DELETE /api/mcp/loop-skill-bundle/versions/{id}` → 204(**require_admin**)
  - `GET /api/mcp/loop-skill-bundle/versions/{id}/download.zip` → 不存在/已删 **404**
  - 改造:`/info`、`/download.zip?version=`、`/install-payload?version=` 走 `versions_service`;`install-payload` 找不到版本 → `{ok:false, data:{available:[...]}}`

- [ ] **Step 1: 写失败测试**(追加:端到端上传→列表→启用→info 变化→下载指定版)

```python
@pytest.mark.mysql
def test_version_endpoints_end_to_end(monkeypatch):
    from server.tests.utils import build_test_app

    test_app = build_test_app(monkeypatch)
    c = test_app.client
    try:
        # 初始:info 回落种子(5 文件)、versions 空
        assert c.get("/api/mcp/loop-skill-bundle/info").json()["version"]
        assert c.get("/api/mcp/loop-skill-bundle/versions").json()["versions"] == []

        # 上传一版
        zip_bytes = _make_zip(_valid_files())
        r = c.post(
            "/api/mcp/loop-skill-bundle/versions",
            files={"file": ("bundle.zip", zip_bytes, "application/zip")},
            data={"version_label": "v-e2e"},
        )
        assert r.status_code == 200, r.text
        vid = r.json()["id"]
        assert r.json()["is_enabled"] is False

        # 列表可见
        listed = c.get("/api/mcp/loop-skill-bundle/versions").json()["versions"]
        assert [v["id"] for v in listed] == [vid]

        # 启用 → info 切到 v-e2e(3 文件)
        assert c.post(f"/api/mcp/loop-skill-bundle/versions/{vid}/enable").status_code == 204
        info = c.get("/api/mcp/loop-skill-bundle/info").json()
        assert info["version"] == "v-e2e"
        assert len(info["files"]) == 3

        # 点名下载指定版:content-type + 文件名必须 ASCII 安全(B)
        z = c.get(f"/api/mcp/loop-skill-bundle/versions/{vid}/download.zip")
        assert z.status_code == 200
        assert z.headers["content-type"] == "application/zip"
        cd = z.headers["content-disposition"]
        assert 'filename="geo-loop-skills-' in cd and ".zip" in cd
        # 头值必须能 latin-1 编码(Starlette 已发出即证明,冗余断言防回归)
        z.headers["content-disposition"].encode("latin-1")
        z.headers["x-bundle-version"].encode("latin-1")

        # 中文 version_label 上传 + 启用 + 下载:绝不能 500(B 的核心回归)
        rz = c.post(
            "/api/mcp/loop-skill-bundle/versions",
            files={"file": ("b.zip", _make_zip(_valid_files()), "application/zip")},
            data={"version_label": "2026-07-08 严格版"},
        )
        assert rz.status_code == 200, rz.text
        cn_id = rz.json()["id"]
        zcn = c.get(f"/api/mcp/loop-skill-bundle/versions/{cn_id}/download.zip")
        assert zcn.status_code == 200  # 不是 500
        zcn.headers["content-disposition"].encode("latin-1")  # RFC5987 已把中文挪进 filename*

        # by-id 下载不存在 → 404(不是 400)
        assert c.get("/api/mcp/loop-skill-bundle/versions/999999/download.zip").status_code == 404

        # install-payload 点名找不到 → ok:false + available 列表(MCP token 路由)
        monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
        from server.app.core import config

        config.get_settings.cache_clear()
        pr = c.get(
            "/api/mcp/loop-skill-bundle/install-payload",
            params={"version": "查无此版"},
            headers={"X-MCP-Token": "secret"},
        )
        assert pr.status_code == 200
        body = pr.json()
        assert body["ok"] is False
        assert isinstance(body["data"]["available"], list) and body["data"]["available"]

        # 删当前启用版 → 409(admin client + 启用版冲突)
        assert c.delete(f"/api/mcp/loop-skill-bundle/versions/{vid}").status_code == 409
    finally:
        test_app.cleanup()
```

> **权限回归(可选补)**:`build_test_app` 默认给 admin client,enable/delete 走 `require_admin` 直接通。若要锁 operator→403,需另建 operator 用户 + 登录取 cookie(参考 `test_audit_*` 的非 admin 用例),本 rev 标为可选,后端 `require_admin` 本身已被 audit 路由覆盖测。

- [ ] **Step 2: 运行,确认失败**

Run: `python -m pytest server/tests/test_loop_skill_bundle_versions.py::test_version_endpoints_end_to_end -q`
Expected: FAIL —— 404(新端点未建)。

- [ ] **Step 3: 建 `schemas.py`**

```python
"""loop_skills 版本管理响应模型。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class BundleVersionMeta(BaseModel):
    id: int
    version_label: str
    bundle_sha256: str
    file_count: int
    total_size: int
    is_enabled: bool
    uploaded_by_user_id: int | None
    notes: str | None
    created_at: datetime


class BundleVersionList(BaseModel):
    versions: list[BundleVersionMeta]
```

- [ ] **Step 4: 改 `router.py`**——顶部 import 补齐,改 3 出口端点,追加管理端点。

顶部 import 替换/补充为:

```python
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from server.app.core.mcp_auth import require_mcp_token
from server.app.core.security import get_current_user, require_admin
from server.app.db.session import get_db
from server.app.modules.audit.service import add_audit_entry
from server.app.modules.loop_skills import versions_service as vs
from server.app.modules.loop_skills.schemas import BundleVersionList, BundleVersionMeta
from server.app.modules.loop_skills.service import SkillBundle, build_zip
from server.app.modules.system.models import User
from server.app.shared.errors import ValidationError
```

在 import 后、端点前加一个**下载响应 helper**(B:头部 ASCII 安全,三个下载端点共用):

```python
def _zip_response(b: SkillBundle) -> Response:
    """把 bundle 打成 zip Response。version_label 可含中文/特殊字符 → 头部一律做 ASCII 安全化:
    - filename 用 ASCII slug(sha 短串);中文原名走 RFC 5987 filename*
    - X-Bundle-Version 用 percent-encode(否则 Starlette latin-1 编码非 ASCII 会 500 / header 注入)
    """
    ascii_name = f"geo-loop-skills-{b.bundle_sha256[:12]}.zip"
    utf8_name = quote(f"geo-loop-skills-{b.version}.zip")
    return Response(
        content=build_zip(b),
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{utf8_name}'
            ),
            "X-Bundle-Version": quote(b.version),
            "X-Bundle-Sha256": b.bundle_sha256,
        },
    )
```

把 `get_loop_skill_bundle_info` 改成:

```python
@router.get("/loop-skill-bundle/info", response_model=LoopSkillBundleInfo)
def get_loop_skill_bundle_info(db: Session = Depends(get_db)) -> LoopSkillBundleInfo:
    """[user] /goal Loop skill 包元信息 —— 当前启用版(无则回落种子)。"""
    b = vs.get_active_bundle(db)
    return LoopSkillBundleInfo(
        version=b.version,
        bundle_sha256=b.bundle_sha256,
        files=[LoopSkillFileMeta(path=f.path, size=f.size, sha256=f.sha256) for f in b.files],
        install_hint=(
            "解压到本机 ~/.claude/（全局，所有 Claude Code 会话可见）"
            " 或项目根 <repo>/.claude/（仅该项目可见）。"
        ),
    )
```

把 `download_loop_skill_bundle_zip` 改成(加可选 `version`):

```python
@router.get("/loop-skill-bundle/download.zip")
def download_loop_skill_bundle_zip(
    version: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> Response:
    """[user] 下载 zip。version 空=启用版;数字=id;否则 label。"""
    b = vs.resolve_bundle_for_install(db, version)  # 找不到 → ValidationError → 全局 400
    return _zip_response(b)
```

在 `router`(user JWT)里追加管理端点:

```python
@router.get("/loop-skill-bundle/versions", response_model=BundleVersionList)
def list_bundle_versions(db: Session = Depends(get_db)) -> BundleVersionList:
    """[user] 列出所有未删除版本(元信息,不含正文)。"""
    metas = vs.list_versions(db)
    return BundleVersionList(
        versions=[
            BundleVersionMeta(
                id=m.id,
                version_label=m.version_label,
                bundle_sha256=m.bundle_sha256,
                file_count=m.file_count,
                total_size=m.total_size,
                is_enabled=m.is_enabled,
                uploaded_by_user_id=m.uploaded_by_user_id,
                notes=m.notes,
                created_at=m.created_at,
            )
            for m in metas
        ]
    )


@router.post("/loop-skill-bundle/versions", response_model=BundleVersionMeta)
async def upload_bundle_version(
    file: UploadFile = File(...),
    version_label: str | None = Form(default=None),
    notes: str | None = Form(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> BundleVersionMeta:
    """[user] 上传 zip 建新版本(默认不启用)。"""
    data = await file.read()
    m = vs.upload_version(
        db, data, version_label=version_label, notes=notes,
        uploaded_by_user_id=current_user.id,
    )
    add_audit_entry(
        db, user=current_user, action="loop_skill_bundle.upload",
        target_type="loop_skill_bundle", target_id=str(m.id),
        payload={"version_label": m.version_label, "sha": m.bundle_sha256},
    )
    return BundleVersionMeta(
        id=m.id, version_label=m.version_label, bundle_sha256=m.bundle_sha256,
        file_count=m.file_count, total_size=m.total_size, is_enabled=m.is_enabled,
        uploaded_by_user_id=m.uploaded_by_user_id, notes=m.notes, created_at=m.created_at,
    )


@router.post("/loop-skill-bundle/versions/{version_id}/enable", status_code=204)
def enable_bundle_version(
    version_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),  # 决策1:启用=改所有人本地 skill,收归 admin
) -> Response:
    """[admin] 启用某版本(=回退)。"""
    vs.enable_version(db, version_id)
    add_audit_entry(
        db, user=current_user, action="loop_skill_bundle.enable",
        target_type="loop_skill_bundle", target_id=str(version_id),
    )
    return Response(status_code=204)


@router.delete("/loop-skill-bundle/versions/{version_id}", status_code=204)
def delete_bundle_version(
    version_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),  # 决策1:删除收归 admin
) -> Response:
    """[admin] 逻辑删除某版本(拒删当前启用版)。"""
    vs.soft_delete_version(db, version_id)
    add_audit_entry(
        db, user=current_user, action="loop_skill_bundle.delete",
        target_type="loop_skill_bundle", target_id=str(version_id),
    )
    return Response(status_code=204)


@router.get("/loop-skill-bundle/versions/{version_id}/download.zip")
def download_bundle_version(
    version_id: int, db: Session = Depends(get_db)
) -> Response:
    """[user] 下载指定版本 zip。不存在/已删 → 404(spec §L169)。"""
    try:
        b = vs.get_bundle_by_id(db, version_id)
    except ValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _zip_response(b)
```

把 MCP `get_loop_skill_install_payload` 改成注入 session + 可选 `version`:

```python
@mcp_router.get("/loop-skill-bundle/install-payload")
def get_loop_skill_install_payload(
    version: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    """[MCP] install_loop_skills 工具入口 —— version 空=启用版,否则点名。

    找不到点名版本 → 返回 {ok:false, data:{available:[...]}}(HTTP 200,非 400):让 MCP 工具
    拿到候选版本列表帮用户挑,兑现 spec §L168、支撑"点名任意版"的可用性。
    """
    try:
        b = vs.resolve_bundle_for_install(db, version)
    except ValidationError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "data": {
                "available": [
                    {"id": m.id, "version_label": m.version_label, "is_enabled": m.is_enabled}
                    for m in vs.list_versions(db)
                ]
            },
        }
    return {
        "ok": True,
        "data": {
            "version": b.version,
            "bundle_sha256": b.bundle_sha256,
            "install_hint": (
                "Write each file to the user's .claude/ directory, preserving "
                "the relative path. Prefer project-level <repo>/.claude/ over "
                "~/.claude/ when the user is currently inside a git repo. "
                "If a file already exists, show diff and ask user before overwriting."
            ),
            "files": [
                {"path": f.path, "content": f.content, "sha256": f.sha256, "size": f.size}
                for f in b.files
            ],
        },
        "error": None,
    }
```

> **注**:install-payload 走 MCP token 路由,返回 200 + `ok:false` 而非 400 —— MCP 工具 `_aget` 只对非 2xx 转 `_fail`,这里用 200 才能把 `available` 候选列表带回工具(见 spec §L168)。`download.zip?version=`(user 路由)保持 `ValidationError→400`;仅 `/versions/{id}/download.zip` 的"按 id 取资源不存在"用 404(spec §L169)——两者语义不同,不必强行统一。

- [ ] **Step 5: 运行新端到端 + 回归**

Run: `python -m pytest server/tests/test_loop_skill_bundle_versions.py::test_version_endpoints_end_to_end server/tests/test_loop_skill_bundle.py -q`
Expected: PASS。尤其 `test_user_info_endpoint_returns_bundle_when_authed`(len==5)、`test_mcp_install_payload_returns_full_files_when_authed`(len==5、响应壳)靠回落种子仍绿。

- [ ] **Step 6: mypy + ruff**

Run: `ruff check server/app/modules/loop_skills/ && ruff format --check server/app/modules/loop_skills/ && mypy server/app/modules/loop_skills/`
Expected: 全绿(有 format 差异先 `ruff format server/app/modules/loop_skills/`)。

- [ ] **Step 7: Commit**

```bash
git add server/app/modules/loop_skills/schemas.py server/app/modules/loop_skills/router.py server/tests/test_loop_skill_bundle_versions.py
git commit -m "feat(loop-skills): 出口端点走 get_active_bundle + 5 个版本管理端点"
```

---

### Task 7: MCP 工具 `install_loop_skills` 加可选 `version`

**Files:**
- Modify: `server/mcp/tools/action.py`
- Test: `server/tests/test_loop_skill_bundle_versions.py`

**Interfaces:**
- Produces: `install_loop_skills(version: str | None = None)` → 透传 `/install-payload?version=`。

- [ ] **Step 1: 写失败测试**(追加:MCP 工具带 version 拿到指定版)

```python
@pytest.mark.mysql
def test_install_loop_skills_tool_version_param(monkeypatch):
    """install_loop_skills(version=...) 透传 version,拿到点名的那版。"""
    import asyncio

    from server.tests.utils import build_test_app

    test_app = build_test_app(monkeypatch)
    try:
        from server.app.db.session import SessionLocal
        from server.app.modules.loop_skills import versions_service as vs

        with SessionLocal() as db:
            m = vs.upload_version(db, _make_zip(_valid_files()), version_label="v-tool",
                                  notes=None, uploaded_by_user_id=None)
            db.commit()
            vid = m.id

        # 用 monkeypatch 把工具内部 _aget 换成直接打测试 client(带 MCP token 语义)
        import server.mcp.tools.action as action

        def fake_aget(path, *, params=None):
            # 直接调后端 install-payload(测试 client 已带 admin,MCP 路由需 token)
            monkeypatch.setenv("GEO_MCP_TOKEN", "secret")
            from server.app.core import config
            config.get_settings.cache_clear()
            r = test_app.client.get(path, params=params or {}, headers={"X-MCP-Token": "secret"})
            return {"ok": True, "data": r.json(), "error": None}

        monkeypatch.setattr(action, "_aget", lambda p, *, params=None: fake_aget(p, params=params))

        out = asyncio.run(action.install_loop_skills(version="v-tool"))
        assert out["ok"] is True
        assert out["data"]["version"] == "v-tool"
        assert len(out["data"]["files"]) == 3
    finally:
        test_app.cleanup()
```

> 注:`_aget` 是 `async`;这里用同步 `fake_aget` 包一层即可(工具体内 `await _aget(...)` 对返回值 await —— 若 monkeypatch 成同步函数,把它再包成 async。更稳的写法见 Step 3 备注)。如运行环境对 async monkeypatch 敏感,可改为直接断言端点(已在 Task 6 覆盖),本测试作为工具签名冒烟。

- [ ] **Step 2: 运行,确认失败**

Run: `python -m pytest server/tests/test_loop_skill_bundle_versions.py::test_install_loop_skills_tool_version_param -q`
Expected: FAIL —— `install_loop_skills() got an unexpected keyword argument 'version'`。

- [ ] **Step 3: 改 `install_loop_skills`**(`server/mcp/tools/action.py`)

```python
@mcp.tool()
async def install_loop_skills(version: str | None = None) -> dict[str, Any]:
    """Fetch the /goal Loop skill bundle so Claude Code can install it locally.

    Args:
        version: Optional. Empty = current enabled version. A numeric id or a
            version label pins that specific version (e.g. "2026-07-08 严格版").

    Returns a dict containing the template files (README, slash command, SKILL.md
    files) of the selected version. The calling Claude Code session should then
    use its Write tool to write each file to the user's `.claude/` directory.

    Returns:
        {"ok": True, "data": {
            "version": str,
            "bundle_sha256": str,
            "install_hint": str,
            "files": [{"path": str, "content": str, "sha256": str, "size": int}, ...],
        }, "error": None}
    """
    params = {"version": version} if version else None
    raw = await _aget("/api/mcp/loop-skill-bundle/install-payload", params=params)
    if not raw.get("ok"):
        return raw  # 透传 _fail 结构
    inner = raw.get("data") or {}
    if isinstance(inner, dict) and "ok" in inner and "data" in inner:
        return inner
    return raw
```

- [ ] **Step 4: 运行,确认通过**

Run: `python -m pytest server/tests/test_loop_skill_bundle_versions.py::test_install_loop_skills_tool_version_param -q`
Expected: PASS。(若 async monkeypatch 环境问题导致此冒烟测试不稳,保留 Task 6 的端到端为准,标记该测试 `@pytest.mark.mysql` 已足够。)

- [ ] **Step 5: 确认 `MCP_TOOLS_COUNT` 未变**

Run: `python -c "import re,pathlib; print('21' in pathlib.Path('server/app/modules/mcp_catalog/connect_router.py').read_text(encoding='utf-8'))"`
Expected: `True`(仍 21,只加了可选参数)。

- [ ] **Step 6: Commit**

```bash
git add server/mcp/tools/action.py server/tests/test_loop_skill_bundle_versions.py
git commit -m "feat(mcp): install_loop_skills 加可选 version 参数(点名任意版)"
```

---

### Task 8: 前端 —— API 客户端 + Section ⑤ 版本管理 UI

**Files:**
- Modify: `web/src/api/mcp.ts`、`web/src/features/mcp/McpConnectWorkspace.tsx`
- Test: 无单测框架 → `pnpm --filter @geo/web typecheck` + `build` 为门禁。

**Interfaces:**
- Consumes(HTTP,Task 6):`/loop-skill-bundle/versions`(GET/POST)、`/{id}/enable`、`/{id}`(DELETE)、`/versions/{id}/download.zip`。
- Produces(前端):`BundleVersion` 类型、`listBundleVersions`/`uploadBundleVersion`/`enableBundleVersion`/`deleteBundleVersion`/`bundleVersionDownloadUrl`。

- [ ] **Step 1: 加 `api/mcp.ts` 函数**(文件末尾追加)

```typescript
// ─────────────────────────────────────────────────────────────────────────────
// Loop skill bundle 版本管理(Section ⑤ 版本管理块)
// ─────────────────────────────────────────────────────────────────────────────

export interface BundleVersion {
  id: number;
  version_label: string;
  bundle_sha256: string;
  file_count: number;
  total_size: number;
  is_enabled: boolean;
  uploaded_by_user_id: number | null;
  notes: string | null;
  created_at: string;
}

export function listBundleVersions(): Promise<{ versions: BundleVersion[] }> {
  return api<{ versions: BundleVersion[] }>("/api/mcp/loop-skill-bundle/versions");
}

export function uploadBundleVersion(
  file: File,
  versionLabel: string,
  notes: string,
): Promise<BundleVersion> {
  const form = new FormData();
  form.append("file", file);
  if (versionLabel) form.append("version_label", versionLabel);
  if (notes) form.append("notes", notes);
  return api<BundleVersion>("/api/mcp/loop-skill-bundle/versions", {
    method: "POST",
    body: form, // core.api 检测 FormData 自动免设 Content-Type
  });
}

export function enableBundleVersion(id: number): Promise<void> {
  return api<void>(`/api/mcp/loop-skill-bundle/versions/${id}/enable`, { method: "POST" });
}

export function deleteBundleVersion(id: number): Promise<void> {
  return api<void>(`/api/mcp/loop-skill-bundle/versions/${id}`, { method: "DELETE" });
}

export function bundleVersionDownloadUrl(id: number): string {
  return `/api/mcp/loop-skill-bundle/versions/${id}/download.zip`;
}
```

- [ ] **Step 2: 在 `McpConnectWorkspace.tsx` Section ⑤ 里加版本管理块**

先在文件顶部 import 区补:

```typescript
import {
  listBundleVersions,
  uploadBundleVersion,
  enableBundleVersion,
  deleteBundleVersion,
  bundleVersionDownloadUrl,
  type BundleVersion,
} from "../../api/mcp";
import { useAuth } from "../auth/AuthContext"; // 决策1:enable/delete 仅 admin,前端也门控隐藏
```

在组件内(与其它 `useState` 一起)加状态 + 加载/操作 handler:

```typescript
const { user } = useAuth();
const isAdmin = user?.role === "admin"; // 门控 启用/删除 按钮(后端 require_admin 兜底)
const [versions, setVersions] = useState<BundleVersion[] | null>(null);
const [versionsBusy, setVersionsBusy] = useState(false);
const [versionsErr, setVersionsErr] = useState<string | null>(null);
const [uploadLabel, setUploadLabel] = useState("");
const [uploadNotes, setUploadNotes] = useState("");

const refreshVersions = async () => {
  setVersionsErr(null);
  try {
    setVersions((await listBundleVersions()).versions);
  } catch (e) {
    setVersionsErr(e instanceof Error ? e.message : "加载失败");
  }
};

useEffect(() => {
  void refreshVersions();
}, []);

const onUploadZip = async (file: File) => {
  setVersionsBusy(true);
  setVersionsErr(null);
  try {
    await uploadBundleVersion(file, uploadLabel.trim(), uploadNotes.trim());
    setUploadLabel("");
    setUploadNotes("");
    await refreshVersions();
  } catch (e) {
    setVersionsErr(e instanceof Error ? e.message : "上传失败");
  } finally {
    setVersionsBusy(false);
  }
};

const onEnableVersion = async (id: number) => {
  setVersionsBusy(true);
  try {
    await enableBundleVersion(id);
    await refreshVersions();
    await refreshBundle(); // 让上方版本信息卡也刷新到新启用版
  } catch (e) {
    setVersionsErr(e instanceof Error ? e.message : "启用失败");
  } finally {
    setVersionsBusy(false);
  }
};

const onDeleteVersion = async (id: number) => {
  if (!window.confirm("确认逻辑删除该版本?(当前启用版不可删)")) return;
  setVersionsBusy(true);
  try {
    await deleteBundleVersion(id);
    await refreshVersions();
  } catch (e) {
    setVersionsErr(e instanceof Error ? e.message : "删除失败");
  } finally {
    setVersionsBusy(false);
  }
};
```

在 Section ⑤ 的 `</section>` 之前(方式 B 那块之后)插入版本管理 JSX(沿用本文件 inline style 习惯):

```tsx
{/* 版本管理:上传 zip + 列表启用/回退/删除/下载 */}
<div style={{ marginTop: 16, padding: 12, borderRadius: 6, background: "var(--bg-2)" }}>
  <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>
    版本管理 · 上传 / 启用 / 回退 / 删除
  </div>
  <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8, flexWrap: "wrap" }}>
    <input
      type="text"
      placeholder="版本名(可选,留空自动用时间戳)"
      value={uploadLabel}
      onChange={(e) => setUploadLabel(e.target.value)}
      style={{ flex: "1 1 200px", padding: "6px 10px", borderRadius: 4, border: "1px solid var(--border)" }}
    />
    <input
      type="text"
      placeholder="变更说明(可选)"
      value={uploadNotes}
      onChange={(e) => setUploadNotes(e.target.value)}
      style={{ flex: "1 1 200px", padding: "6px 10px", borderRadius: 4, border: "1px solid var(--border)" }}
    />
    <label
      style={{
        display: "inline-flex", alignItems: "center", gap: 6, padding: "6px 12px",
        background: "var(--accent)", color: "var(--bg)", borderRadius: 4,
        cursor: versionsBusy ? "not-allowed" : "pointer", fontSize: 13,
      }}
    >
      <Package size={14} /> 上传 zip
      <input
        type="file"
        accept=".zip,application/zip"
        disabled={versionsBusy}
        style={{ display: "none" }}
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) void onUploadZip(f);
          e.target.value = "";
        }}
      />
    </label>
  </div>

  {versionsErr && (
    <div style={{ color: "var(--fg-danger, #ef4444)", fontSize: 12, marginBottom: 8 }}>
      {versionsErr}
    </div>
  )}

  {versions && versions.length > 0 ? (
    <table style={{ fontSize: 12, width: "100%", borderCollapse: "collapse" }}>
      <thead>
        <tr style={{ textAlign: "left", color: "var(--fg-2)" }}>
          <th style={{ paddingRight: 8 }}>版本名</th>
          <th style={{ paddingRight: 8 }}>sha</th>
          <th style={{ paddingRight: 8 }}>文件</th>
          <th style={{ paddingRight: 8 }}>体积</th>
          <th style={{ paddingRight: 8 }}>上传人</th>
          <th style={{ paddingRight: 8 }}>时间</th>
          <th style={{ paddingRight: 8 }}>状态</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        {versions.map((v) => (
          <tr key={v.id}>
            <td style={{ paddingRight: 8 }} title={v.notes ?? ""}>{v.version_label}</td>
            <td style={{ paddingRight: 8 }}>
              <code style={inlineCode}>{v.bundle_sha256.slice(0, 12)}</code>
            </td>
            <td style={{ paddingRight: 8 }}>{v.file_count}</td>
            <td style={{ paddingRight: 8 }}>{Math.max(1, Math.round(v.total_size / 1024))} KB</td>
            <td style={{ paddingRight: 8 }}>{v.uploaded_by_user_id ?? "—"}</td>
            <td style={{ paddingRight: 8 }}>{new Date(v.created_at).toLocaleString()}</td>
            <td style={{ paddingRight: 8 }}>
              {v.is_enabled ? <strong style={{ color: "var(--accent)" }}>启用中</strong> : "—"}
            </td>
            <td style={{ display: "flex", gap: 8 }}>
              {isAdmin && !v.is_enabled && (
                <button type="button" disabled={versionsBusy} onClick={() => void onEnableVersion(v.id)}>
                  启用
                </button>
              )}
              <a href={bundleVersionDownloadUrl(v.id)} download>
                下载
              </a>
              {isAdmin && !v.is_enabled && (
                <button type="button" disabled={versionsBusy} onClick={() => void onDeleteVersion(v.id)}>
                  删除
                </button>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  ) : (
    <div style={{ fontSize: 12, color: "var(--fg-2)" }}>
      还没有上传版本 —— Claude Code 装到的是内置种子版。上传一版并启用即可切换。
    </div>
  )}
</div>
```

> **注**:「上传人」列直接显示 `uploaded_by_user_id`(后端 `BundleVersionMeta` 只带 id)。要展示用户名需后端 meta 补 `uploaded_by_username`(join `users`)——列为可选 follow-up,不阻塞本 rev。`isAdmin=false` 时启用/删除按钮隐藏,后端 `require_admin` 仍是权威兜底(直接调接口会 403,`core.api` 展示 detail)。

- [ ] **Step 3: 顺手改"5 个 skill"文案**(避免误导——现在文件数随版本变)

`McpConnectWorkspace.tsx` 把 Section ⑤ 里"需要先在本机 `.claude/` 装 5 个 skill 模板"改为"装 skill 模板";把方式 A 说明里"拿到 5 个文件"改为"拿到全部文件"。同样把 `server/mcp/tools/action.py` docstring 里"all 5 template files"改为"the template files"(Task 7 已改则跳过)。

- [ ] **Step 4: typecheck**

Run: `pnpm --filter @geo/web typecheck`
Expected: PASS(无类型错)。

- [ ] **Step 5: build**

Run: `pnpm --filter @geo/web build`
Expected: 构建成功。

- [ ] **Step 6: Commit**

```bash
git add web/src/api/mcp.ts web/src/features/mcp/McpConnectWorkspace.tsx
git commit -m "feat(web): MCP 接入 tab 加 loop skill 包版本管理(上传/启用/回退/删除/下载)"
```

---

### Task 9: 全量回归 + 收尾

**Files:** 无新增,只跑门禁。

- [ ] **Step 1: 后端 loop_skills 全量**

Run: `GEO_TEST_DATABASE_URL="..." python -m pytest server/tests/test_loop_skill_bundle.py server/tests/test_loop_skill_bundle_versions.py server/tests/test_fts_and_migrations.py -q`
Expected: 全 PASS(mysql 用例需 DB;无 DB 则 skip 但 `test_loop_skill_bundle.py` 的纯函数用例 + sha 契约仍跑)。

- [ ] **Step 2: 后端 lint/format/type**

Run: `ruff check server/ && ruff format --check server/ && mypy server/app`
Expected: 全绿。

- [ ] **Step 3: 前端门禁**

Run: `pnpm --filter @geo/web typecheck && pnpm --filter @geo/web build`
Expected: 全绿。

- [ ] **Step 4: 推分支**(不 -u,见记忆 gotcha-git-push-u-hook-blocked)

```bash
git push origin feat/loop-skill-bundle-versioning
```

---

## Self-Review(计划自查)

- **Spec 覆盖**:数据模型→Task1;读取 seam→Task3;上传/校验→Task4;启用单例+软删→Task5;端点(3 改+5 新)→Task6;MCP version→Task7;前端→Task8;契约 no-op(不改 `test_bundle_sha_is_known`)→Task2/6 回归覆盖;迁移→Task1。三条必修全落:模型注册(Task1 Step4)、INT FK(Task1 模型/迁移)、GET_LOCK 单例(Task5)。
- **Placeholder 扫描**:无 TBD;每个代码步给了完整代码。唯一"可选/加分"标注(install-payload 找不到回列表、软删自动清理、保留原始 zip)明确划出范围外。
- **类型一致**:`VersionMeta`(dataclass,含 `is_deleted`)vs `BundleVersionMeta`(Pydantic,不含 `is_deleted`,列表已排除软删)—— Task6 显式逐字段映射,不 `**dict`,避免字段错位。`build_bundle_from_file_map(raw, *, version)` 签名在 Task2 定义、Task3/4 一致引用。`resolve_bundle_for_install`/`get_active_bundle`/`get_bundle_by_id` 在 Task3 定义、Task6 一致引用。
- **偏差记录**:删启用版用 `ConflictError`→**409**(spec 错误表原写 400);409 语义更准(状态冲突),detail 携带"先启用别的版"指引,前端 `core.api` 会展示 detail。属有意改进。
- **rev 2026-07-07b 评审合入**:
  - **(A)锁连接亲和**:`enable`/`soft_delete` 从"`session.execute`+`session.commit`+`RELEASE`"改为独占一条 `Connection` 全程(`session.get_bind().connect()`),消除 commit 换连接导致的锁泄漏;并发测试加"全新连接 `GET_LOCK(name,0)` 立即返回 1"断言证明锁真释放。
  - **(B)下载头 ASCII 安全**:三个下载端点共用 `_zip_response`,ASCII slug + RFC 5987 `filename*` + `quote(version)`;`upload_version` 加 `_clean_text`(strip/限长/拒控制字符);e2e 补中文 label 下载不 500 回归。
  - **(C)zip 资源上限**:`MAX_ENTRIES=50` / `MAX_ENTRY_BYTES=2MB` / `MAX_TOTAL_UNCOMPRESSED=4MB` / 重名拒绝;补 `test_upload_version_resource_and_label_limits`。
  - **决策1 权限**:`enable`/`delete` 端点改 `require_admin`,`upload` 仍 `get_current_user`;前端 `useAuth` 门控启用/删除按钮。
  - **决策2 状态码**:`versions/{id}/download.zip` 不存在→**404**(router catch `ValidationError`→`HTTPException(404)`);`install-payload?version=` 找不到→`{ok:false,data:{available:[...]}}`(200,兑现 spec §L168)。
  - **审计边界**:审计改在锁释放后用 `session` 写(spec §1 原"审计在锁内同事务"已订正为作废),`upload` 审计注意别让失败 rollback 掉上传行。
  - **前端补齐**:notes 输入 + 体积/上传人/时间列(上传人暂显 id,username 解析列为可选 follow-up)。
- **仍为范围外/可选**:operator→403 权限回归用例、上传人 username 解析、纯数字 label 消歧(`isdigit` 仍优先当 id)、`deferred` 的 SQL 级不加载断言(靠"列表返回 VersionMeta 不含 files"间接覆盖)。
