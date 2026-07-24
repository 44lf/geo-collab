# Skill 库「追加新版本到已有 skill」Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让运营/作者能从网页给已有 skill（尤其官方 `/goal loop skills` 包）追加新版本，绕开靠文件名派生 skill name 的死路。

**Architecture:** 新增一个按 id 定位的 REST 端点 `POST /api/mcp/skills/{skill_id}/versions`（user JWT）；service 层新增 `add_version`，与现有 `create_version` 共用抽出的 `_append_version` 行构造。前端每张 skill 卡片加「上传新版本」按钮 + 内联 `VersionUploader` 面板。追加语义是纯完全替换，顶部「新建」上传路径完全不动。

**Tech Stack:** FastAPI + SQLAlchemy（后端）、React 19 + TypeScript + Vite（前端）、pytest（`@pytest.mark.mysql`，MySQL only）。

## Global Constraints

- **追加 = 纯完全替换**：新版本 files = 本次上传全集。不做少传保护、不做 auto-inherit。
- **权限**：官方包（`is_official`）追加仅 admin（`ClientError` → 403）；非官方包任何登录用户可追加，无属主校验。
- **category 不改**：category 是 `Skill` 包级属性；追加版本不带 category、不改它。
- **复用 `UploadResult` schema**（`{skill_id, slug, version_label}`）；不新增 MCP tool、不动 `mcp_catalog/connect_router.py:MCP_TOOLS_COUNT`。
- **service 层只抛命名异常**：`ValidationError` / `ClientError` / `ConflictError`，不抛裸 `ValueError`（无全局兜底）。
- **错误映射与现有 `/skills/upload` 路由一致**：`ConflictError`(409)/`ValidationError`(400) 走全局兜底、路由内不改写；`ClientError`(权限) 在路由内转 403。
- **后端测试**：`@pytest.mark.mysql`，`build_test_app(monkeypatch)` + `finally: app.cleanup()`；DB 名含 `"test"`。
- **前端无单测框架**：`pnpm --filter @geo/web typecheck` + `build` 是门禁。
- **分支**：`feat/skill-upload-append-mode`（已创建，设计文档已提交 f370499）。频繁提交。

---

## File Structure

- `server/app/modules/loop_skills/skill_service.py` — 修改：抽 `_append_version` 行构造；新增 `add_version(session, *, skill_id, entries, uploaded_by, is_admin)`；`create_version` 复用 `_append_version`（行为不变）。
- `server/app/modules/loop_skills/skill_router.py` — 修改：新增 `POST /skills/{skill_id}/versions` 端点。
- `server/tests/test_skill_library_service.py` — 修改：加 `add_version` 的 service 层用例。
- `server/tests/test_skill_library_api.py` — 修改：加 `POST /skills/{id}/versions` 的 HTTP 用例。
- `web/src/features/mcp/skill-library/fileDrop.ts` — 新建：从 `UploadZone.tsx` 抽出拖拽文件夹的 DataTransfer→File 辅助（`withRelativePath` / `filesFromDataTransferItems`），供 UploadZone 与 VersionUploader 共用。
- `web/src/features/mcp/skill-library/UploadZone.tsx` — 修改：改从 `fileDrop.ts` import 那几个辅助（行为不变）。
- `web/src/api/skills.ts` — 修改：加 `uploadSkillVersion(skillId, files)`。
- `web/src/features/mcp/skill-library/VersionUploader.tsx` — 新建：内联轻量上传面板（无 category、无 name 派生）。
- `web/src/features/mcp/skill-library/SkillCard.tsx` — 修改：加「上传新版本」按钮 + 内联 VersionUploader（官方卡非 admin 禁用）。

---

## Task 1: Service 层 —— 抽 `_append_version` + 新增 `add_version`

**Files:**
- Modify: `server/app/modules/loop_skills/skill_service.py`
- Test: `server/tests/test_skill_library_service.py`

**Interfaces:**
- Consumes: 现有 `_next_label`、`_MAX_LABEL_RETRY_ATTEMPTS`、`build_bundle_from_file_map`、`SkillBundle`、`Skill`、`SkillVersion`、`ValidationError`/`ClientError`/`ConflictError`、`upload.parse_upload`/`upload.validate_file_map`（均已在本文件 import）。
- Produces:
  - `add_version(session, *, skill_id: int, entries: list[tuple[str, bytes]], uploaded_by: int | None, is_admin: bool = False) -> tuple[Skill, SkillVersion]`
  - `_append_version(session, skill: Skill, bundle: SkillBundle, uploaded_by: int | None) -> SkillVersion`（私有）

- [ ] **Step 1: 写失败测试**

在 `server/tests/test_skill_library_service.py` 末尾追加（文件顶部已有 `_zip` / `_db` / `pytestmark = pytest.mark.mysql`）：

```python
def test_add_version_appends_and_switches_current(monkeypatch):
    from server.app.modules.loop_skills import skill_service as svc
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        db = _db()
        try:
            sk, v1 = svc.create_version(
                db, entries=[("b.zip", _zip({"SKILL.md": b"one"}))], name="w-add", uploaded_by=None
            )
            db.commit()

            sk2, v2 = svc.add_version(
                db,
                skill_id=sk.id,
                entries=[("b.zip", _zip({"SKILL.md": b"two"}))],
                uploaded_by=None,
            )
            db.commit()
            assert sk2.id == sk.id
            assert v2.version_label == "v2"
            assert sk2.current_version_id == v2.id
            assert svc.get_current_bundle(db, sk.slug).files[0].content == "two"
        finally:
            db.close()
    finally:
        app.cleanup()


def test_add_version_official_requires_admin(monkeypatch):
    from server.app.modules.loop_skills import skill_service as svc
    from server.app.shared.errors import ClientError
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        db = _db()
        try:
            sk, _ = svc.create_version(
                db, entries=[("b.zip", _zip({"SKILL.md": b"1"}))], name="off-add", uploaded_by=None
            )
            sk.is_official = True
            db.commit()

            # 非 admin 追加官方包 → ClientError
            with pytest.raises(ClientError):
                svc.add_version(
                    db,
                    skill_id=sk.id,
                    entries=[("b.zip", _zip({"SKILL.md": b"2"}))],
                    uploaded_by=None,
                    is_admin=False,
                )
            # admin 追加官方包 → OK
            _, v2 = svc.add_version(
                db,
                skill_id=sk.id,
                entries=[("b.zip", _zip({"SKILL.md": b"2"}))],
                uploaded_by=None,
                is_admin=True,
            )
            db.commit()
            assert v2.version_label == "v2"
        finally:
            db.close()
    finally:
        app.cleanup()


def test_add_version_missing_skill_raises(monkeypatch):
    from server.app.modules.loop_skills import skill_service as svc
    from server.app.shared.errors import ValidationError
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        db = _db()
        try:
            with pytest.raises(ValidationError):
                svc.add_version(
                    db,
                    skill_id=999999,
                    entries=[("b.zip", _zip({"SKILL.md": b"x"}))],
                    uploaded_by=None,
                )
        finally:
            db.close()
    finally:
        app.cleanup()


def test_add_version_complete_replacement(monkeypatch):
    """追加时是完全替换：上传 1 文件到原 3 文件包 → 新版本只有 1 文件（无 auto-inherit）。"""
    from server.app.modules.loop_skills import skill_service as svc
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        db = _db()
        try:
            sk, _ = svc.create_version(
                db,
                entries=[
                    (
                        "b.zip",
                        _zip(
                            {
                                "SKILL.md": b"a",
                                "commands/goal.md": b"b",
                                "README.md": b"c",
                            }
                        ),
                    )
                ],
                name="repl-add",
                uploaded_by=None,
            )
            db.commit()

            _, v2 = svc.add_version(
                db,
                skill_id=sk.id,
                entries=[("b.zip", _zip({"SKILL.md": b"only"}))],
                uploaded_by=None,
            )
            db.commit()
            assert v2.file_count == 1
            bundle = svc.get_current_bundle(db, sk.slug)
            assert [f.path for f in bundle.files] == ["SKILL.md"]
        finally:
            db.close()
    finally:
        app.cleanup()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest server/tests/test_skill_library_service.py -q -k add_version`
（需先 `export GEO_TEST_DATABASE_URL=mysql+pymysql://...`，DB 名含 `"test"`。）
Expected: FAIL —— `AttributeError: module ... has no attribute 'add_version'`。

- [ ] **Step 3: 抽 `_append_version` + 改 `create_version`**

在 `skill_service.py` 中，把 `create_version` 里构造 `SkillVersion` 那段抽成私有 helper。在 `create_version` 定义**之前**插入：

```python
def _append_version(
    session: Session,
    skill: Skill,
    bundle: SkillBundle,
    uploaded_by: int | None,
) -> SkillVersion:
    """构造并插入一个新 SkillVersion 行（version_label 由 _next_label 递增）。

    不设 current、不做重试——调用方负责把它包在 try/except IntegrityError 重试循环里，
    成功后自行设 skill.current_version_id。
    """
    version = SkillVersion(
        skill_id=skill.id,
        version_label=_next_label(session, skill.id),
        bundle_sha256=bundle.bundle_sha256,
        file_count=len(bundle.files),
        total_bytes=sum(f.size for f in bundle.files),
        storage_backend="db",
        files=[
            {"path": f.path, "content": f.content, "sha256": f.sha256, "size": f.size}
            for f in bundle.files
        ],
        storage_key=None,
        uploaded_by=uploaded_by,
    )
    session.add(version)
    session.flush()
    return version
```

然后把 `create_version` 里 `try:` 块内构造 version 的那段替换为调用 helper。改后 `create_version` 的循环体为：

```python
        try:
            if skill is None:
                skill = Skill(
                    name=name,
                    slug=slugify(name, session),
                    is_official=False,
                    created_by=uploaded_by,
                    category=category,
                )
                session.add(skill)
                session.flush()

            version = _append_version(session, skill, bundle, uploaded_by)
        except IntegrityError as exc:
            session.rollback()
            last_exc = exc
            continue

        skill.current_version_id = version.id
        session.flush()
        return skill, version
```

- [ ] **Step 4: 新增 `add_version`**

在 `create_version` 定义**之后**追加：

```python
def add_version(
    session: Session,
    *,
    skill_id: int,
    entries: list[tuple[str, bytes]],
    uploaded_by: int | None,
    is_admin: bool = False,
) -> tuple[Skill, SkillVersion]:
    """按 id 给已存在的 skill 追加一个新版本（绕开按 name 匹配的新建路径）。

    纯完全替换：新版本 files = 本次上传全集。官方包仅 admin。
    """
    raw = upload.parse_upload(entries)
    upload.validate_file_map(raw)
    bundle = build_bundle_from_file_map(raw, version="pending")

    last_exc: IntegrityError | None = None
    for _attempt in range(_MAX_LABEL_RETRY_ATTEMPTS):
        skill = session.get(Skill, skill_id)
        if skill is None or skill.is_deleted:
            raise ValidationError(f"skill 不存在: {skill_id}")
        if skill.is_official and not is_admin:
            raise ClientError("官方包仅管理员可上传新版本")

        try:
            version = _append_version(session, skill, bundle, uploaded_by)
        except IntegrityError as exc:
            # 并发/双击撞 uq_skill_versions_label：rollback 后下一轮重新查 skill + 递增 label。
            session.rollback()
            last_exc = exc
            continue

        skill.current_version_id = version.id
        session.flush()
        return skill, version

    raise ConflictError("版本号并发冲突，请重试上传") from last_exc
```

- [ ] **Step 5: 跑新测 + 现有 service 测试确认全绿**

Run: `pytest server/tests/test_skill_library_service.py -q`
Expected: PASS（新 4 个 + 现有 `test_create_then_append_version` 等全绿，证明 `create_version` 重构行为不变）。

- [ ] **Step 6: 提交**

```bash
git add server/app/modules/loop_skills/skill_service.py server/tests/test_skill_library_service.py
git commit -m "feat(loop_skills): add_version 按 id 给已有 skill 追加版本（service 层）"
```

---

## Task 2: HTTP 端点 `POST /api/mcp/skills/{skill_id}/versions`

**Files:**
- Modify: `server/app/modules/loop_skills/skill_router.py`
- Test: `server/tests/test_skill_library_api.py`

**Interfaces:**
- Consumes: `svc.add_version(...)`（Task 1）、`UploadResult`（已 import）、`add_audit_entry`、`get_current_user`、`require`… 现有 import 齐全。
- Produces: HTTP `POST /api/mcp/skills/{skill_id}/versions`，请求 `multipart/form-data` 仅 `files`，响应 `UploadResult`。

- [ ] **Step 1: 写失败测试**

在 `server/tests/test_skill_library_api.py` 末尾追加（顶部已有 `_zip` / `pytestmark`）：

```python
def test_add_version_endpoint_appends(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        c = app.client  # admin
        r = c.post(
            "/api/mcp/skills/upload",
            files={"files": ("b.zip", _zip({"SKILL.md": b"one"}), "application/zip")},
            data={"name": "endpoint-add"},
        )
        assert r.status_code == 200, r.text
        sid = r.json()["skill_id"]

        r = c.post(
            f"/api/mcp/skills/{sid}/versions",
            files={"files": ("b.zip", _zip({"SKILL.md": b"two"}), "application/zip")},
        )
        assert r.status_code == 200, r.text
        assert r.json()["version_label"] == "v2"
        assert r.json()["skill_id"] == sid

        labels = [v["version_label"] for v in c.get(f"/api/mcp/skills/{sid}/versions").json()["versions"]]
        assert labels == ["v2", "v1"]
    finally:
        app.cleanup()


def test_add_version_endpoint_official_non_admin_403(monkeypatch):
    from server.app.modules.loop_skills import skill_service as svc
    from server.tests.utils import build_test_app, create_extra_user

    app = build_test_app(monkeypatch)
    try:
        from server.app.db.session import SessionLocal

        db = SessionLocal()
        try:
            sk, _ = svc.create_version(
                db,
                entries=[("b.zip", _zip({"SKILL.md": b"1"}))],
                name="off-endpoint",
                uploaded_by=None,
            )
            sk.is_official = True
            db.commit()
            sid = sk.id
        finally:
            db.close()

        _uid, op_client = create_extra_user(app, "op-add-official")
        r = op_client.post(
            f"/api/mcp/skills/{sid}/versions",
            files={"files": ("b.zip", _zip({"SKILL.md": b"2"}), "application/zip")},
        )
        assert r.status_code == 403, r.text
    finally:
        app.cleanup()


def test_add_version_endpoint_non_official_any_user(monkeypatch):
    from server.tests.utils import build_test_app, create_extra_user

    app = build_test_app(monkeypatch)
    try:
        c = app.client  # admin 建一个非官方包
        sid = c.post(
            "/api/mcp/skills/upload",
            files={"files": ("b.zip", _zip({"SKILL.md": b"one"}), "application/zip")},
            data={"name": "shared-pkg"},
        ).json()["skill_id"]

        _uid, op_client = create_extra_user(app, "op-add-shared")
        r = op_client.post(
            f"/api/mcp/skills/{sid}/versions",
            files={"files": ("b.zip", _zip({"SKILL.md": b"two"}), "application/zip")},
        )
        assert r.status_code == 200, r.text
        assert r.json()["version_label"] == "v2"
    finally:
        app.cleanup()


def test_add_version_endpoint_missing_skill_md_400(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        c = app.client
        sid = c.post(
            "/api/mcp/skills/upload",
            files={"files": ("b.zip", _zip({"SKILL.md": b"one"}), "application/zip")},
            data={"name": "no-skillmd-add"},
        ).json()["skill_id"]

        r = c.post(
            f"/api/mcp/skills/{sid}/versions",
            files={"files": ("b.zip", _zip({"README.md": b"x"}), "application/zip")},
        )
        assert r.status_code == 400, r.text
    finally:
        app.cleanup()


def test_add_version_endpoint_skill_not_found_400(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        c = app.client
        r = c.post(
            "/api/mcp/skills/999999/versions",
            files={"files": ("b.zip", _zip({"SKILL.md": b"x"}), "application/zip")},
        )
        assert r.status_code == 400, r.text
    finally:
        app.cleanup()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest server/tests/test_skill_library_api.py -q -k add_version_endpoint`
Expected: FAIL —— 端点不存在，POST 返回 405/404（非 200/403/400）。

- [ ] **Step 3: 实现端点**

在 `skill_router.py` 里、现有 `GET /skills/{skill_id}/versions`（`list_versions`）**之后**插入：

```python
@skills_user_router.post("/skills/{skill_id}/versions", response_model=UploadResult)
async def upload_skill_version(
    skill_id: int,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UploadResult:
    entries = [(f.filename or "file", await f.read()) for f in files]
    try:
        skill, version = svc.add_version(
            db,
            skill_id=skill_id,
            entries=entries,
            uploaded_by=current_user.id,
            is_admin=(current_user.role == "admin"),
        )
    except (ConflictError, ValidationError):
        # 冲突(409) / 不存在或校验失败(400) 走全局兜底，不在此处改写
        raise
    except ClientError as exc:
        # 权限类(官方包非 admin 追加) → 403
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    add_audit_entry(
        db,
        user=current_user,
        action="skill.upload",
        target_type="skill",
        target_id=str(skill.id),
        payload={"version": version.version_label},
    )
    return UploadResult(skill_id=skill.id, slug=skill.slug, version_label=version.version_label)
```

（`APIRouter` / `File` / `UploadFile` / `HTTPException` / `Depends` / `Session` / `get_db` / `get_current_user` / `User` / `svc` / `add_audit_entry` / `UploadResult` / `ClientError` / `ConflictError` / `ValidationError` 均已在文件顶部 import，无需新增。）

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest server/tests/test_skill_library_api.py -q`
Expected: PASS（新 5 个 + 现有全绿）。

- [ ] **Step 5: 提交**

```bash
git add server/app/modules/loop_skills/skill_router.py server/tests/test_skill_library_api.py
git commit -m "feat(loop_skills): POST /skills/{id}/versions 端点（按 id 追加版本）"
```

---

## Task 3: 前端重构 —— 抽 `fileDrop.ts` + 加 `uploadSkillVersion` API

**Files:**
- Create: `web/src/features/mcp/skill-library/fileDrop.ts`
- Modify: `web/src/features/mcp/skill-library/UploadZone.tsx`（改 import）
- Modify: `web/src/api/skills.ts`

**Interfaces:**
- Produces:
  - `fileDrop.ts` 导出 `withRelativePath(file, relativePath): File` 与 `filesFromDataTransferItems(items): Promise<File[]>`
  - `skills.ts` 导出 `uploadSkillVersion(skillId: number, files: File[]): Promise<{ skill_id: number; slug: string; version_label: string }>`

- [ ] **Step 1: 新建 `fileDrop.ts`（从 UploadZone 原样搬辅助函数）**

`web/src/features/mcp/skill-library/fileDrop.ts`：

```ts
// 拖拽文件夹：递归读取 DataTransferItem → FileSystemEntry。
// 从 UploadZone.tsx 抽出，供 UploadZone 与 VersionUploader 共用。

export function withRelativePath(file: File, relativePath: string): File {
  try {
    Object.defineProperty(file, "webkitRelativePath", { value: relativePath, configurable: true });
  } catch {
    // 极少数浏览器禁止重定义只读属性——忽略即可，调用方会退回用文件名
  }
  return file;
}

function readAllDirectoryEntries(reader: FileSystemDirectoryReader): Promise<FileSystemEntry[]> {
  return new Promise((resolve, reject) => {
    const all: FileSystemEntry[] = [];
    const readBatch = () => {
      reader.readEntries((batch) => {
        if (batch.length === 0) {
          resolve(all);
          return;
        }
        all.push(...batch);
        readBatch();
      }, reject);
    };
    readBatch();
  });
}

async function walkEntry(entry: FileSystemEntry, prefix: string, out: File[]): Promise<void> {
  if (entry.isFile) {
    const fileEntry = entry as FileSystemFileEntry;
    const file = await new Promise<File>((resolve, reject) => fileEntry.file(resolve, reject));
    out.push(withRelativePath(file, `${prefix}${entry.name}`));
  } else if (entry.isDirectory) {
    const dirEntry = entry as FileSystemDirectoryEntry;
    const reader = dirEntry.createReader();
    const children = await readAllDirectoryEntries(reader);
    for (const child of children) {
      await walkEntry(child, `${prefix}${entry.name}/`, out);
    }
  }
}

export async function filesFromDataTransferItems(items: DataTransferItemList): Promise<File[]> {
  const entries: FileSystemEntry[] = [];
  for (let i = 0; i < items.length; i++) {
    const entry = items[i]?.webkitGetAsEntry?.();
    if (entry) entries.push(entry);
  }
  const out: File[] = [];
  for (const entry of entries) {
    await walkEntry(entry, "", out);
  }
  return out;
}
```

- [ ] **Step 2: UploadZone 改用 fileDrop（删本地重复定义）**

在 `web/src/features/mcp/skill-library/UploadZone.tsx`：删掉本地的 `withRelativePath` / `readAllDirectoryEntries` / `walkEntry` / `filesFromDataTransferItems` 四个函数定义（即第 58–112 行「拖拽文件夹」整段），改在文件顶部 import 处加：

```ts
import { filesFromDataTransferItems } from "./fileDrop";
```

（UploadZone 内只用到 `filesFromDataTransferItems`；`withRelativePath` 等已随搬走。其余逻辑不动。）

- [ ] **Step 3: skills.ts 加 `uploadSkillVersion`**

在 `web/src/api/skills.ts` 的 `uploadSkill` 之后追加：

```ts
export function uploadSkillVersion(
  skillId: number,
  files: File[],
): Promise<{ skill_id: number; slug: string; version_label: string }> {
  const form = new FormData();
  for (const file of files) {
    const relativePath = (file as File & { webkitRelativePath?: string }).webkitRelativePath;
    form.append("files", file, relativePath || file.name);
  }
  return api<{ skill_id: number; slug: string; version_label: string }>(
    `/api/mcp/skills/${skillId}/versions`,
    { method: "POST", body: form },
  );
}
```

- [ ] **Step 4: typecheck 确认无破坏**

Run（在仓库根 `E:\geo`）：`pnpm --filter @geo/web typecheck`
Expected: PASS（无类型错误；UploadZone 仍编译通过）。

- [ ] **Step 5: 提交**

```bash
git add web/src/features/mcp/skill-library/fileDrop.ts web/src/features/mcp/skill-library/UploadZone.tsx web/src/api/skills.ts
git commit -m "refactor(web): 抽 fileDrop 辅助 + 加 uploadSkillVersion API"
```

---

## Task 4: 前端功能 —— `VersionUploader` + `SkillCard` 卡片按钮

**Files:**
- Create: `web/src/features/mcp/skill-library/VersionUploader.tsx`
- Modify: `web/src/features/mcp/skill-library/SkillCard.tsx`

**Interfaces:**
- Consumes: `uploadSkillVersion`（Task 3）、`filesFromDataTransferItems`（Task 3）、`useToast`。
- Produces: `<VersionUploader skillId={number} onDone={() => void} onCancel={() => void} />`；SkillCard 头部多一个「上传新版本」按钮。

- [ ] **Step 1: 新建 `VersionUploader.tsx`**

`web/src/features/mcp/skill-library/VersionUploader.tsx`：

```tsx
import { useState } from "react";
import { FolderOpen, Loader2, UploadCloud, X } from "lucide-react";
import { uploadSkillVersion } from "../../../api/skills";
import { useToast } from "../../../components/Toast";
import { filesFromDataTransferItems } from "./fileDrop";

const hiddenInput = {
  position: "absolute",
  width: 1,
  height: 1,
  opacity: 0,
  pointerEvents: "none",
} as const;

export function VersionUploader({
  skillId,
  onDone,
  onCancel,
}: {
  skillId: number;
  onDone: () => void;
  onCancel: () => void;
}) {
  const { toast } = useToast();
  const [busy, setBusy] = useState(false);
  const [dragOver, setDragOver] = useState(false);

  async function submit(files: File[]) {
    if (!files.length || busy) return;
    setBusy(true);
    try {
      const r = await uploadSkillVersion(skillId, files);
      toast(`已追加 ${r.version_label} 并设为当前`, "success");
      onDone();
    } catch (e) {
      toast(e instanceof Error ? e.message : "追加失败", "error");
      setBusy(false);
    }
  }

  async function onDrop(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragOver(false);
    const items = e.dataTransfer.items;
    if (items && items.length > 0 && typeof items[0]?.webkitGetAsEntry === "function") {
      try {
        const files = await filesFromDataTransferItems(items);
        if (files.length) {
          void submit(files);
          return;
        }
      } catch {
        // 回落到普通 FileList
      }
    }
    const files = Array.from(e.dataTransfer.files);
    if (files.length) void submit(files);
  }

  function onPick(e: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? []);
    e.target.value = "";
    if (files.length) void submit(files);
  }

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={(e) => {
        e.preventDefault();
        setDragOver(false);
      }}
      onDrop={(e) => void onDrop(e)}
      style={{
        border: `1.5px dashed ${dragOver ? "var(--accent-deep)" : "var(--accent)"}`,
        borderRadius: "var(--r-lg)",
        background: dragOver ? "rgba(109,107,246,0.10)" : "rgba(109,107,246,0.05)",
        padding: "18px 16px",
        display: "grid",
        gap: 10,
        justifyItems: "center",
        textAlign: "center",
      }}
    >
      {busy ? (
        <div style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--accent-deep)", fontSize: 13 }}>
          <Loader2 size={15} className="spin" /> 正在追加新版本…
        </div>
      ) : (
        <>
          <UploadCloud size={22} color="var(--accent)" />
          <div style={{ fontSize: 13, color: "var(--fg-2)" }}>拖拽完整的 skill 文件夹 / ZIP 到此，或</div>
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", justifyContent: "center" }}>
            <label className="primaryButton" style={{ cursor: "pointer", height: 32, padding: "0 12px" }}>
              <FolderOpen size={13} /> 选择文件
              <input
                type="file"
                multiple
                accept=".zip,.md,application/zip,text/markdown"
                onChange={onPick}
                style={hiddenInput}
              />
            </label>
            <label style={{ cursor: "pointer", color: "var(--accent-deep)", fontSize: 12.5, textDecoration: "underline" }}>
              选择文件夹
              <input
                type="file"
                onChange={onPick}
                style={hiddenInput}
                // @ts-expect-error webkitdirectory 是非标准属性，主流浏览器均支持，但 React DOM 类型未收录
                webkitdirectory=""
              />
            </label>
            <button
              type="button"
              onClick={onCancel}
              style={{
                background: "none",
                border: "none",
                cursor: "pointer",
                color: "var(--fg-3)",
                fontSize: 12.5,
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
              }}
            >
              <X size={13} /> 取消
            </button>
          </div>
          <div style={{ fontSize: 11.5, color: "var(--fg-3)", lineHeight: 1.6 }}>
            新版本会<strong>完全替换</strong>——请上传该 skill 的<strong>全部文件</strong>，少传即丢文件
          </div>
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 2: SkillCard 加按钮 + 内联面板**

在 `web/src/features/mcp/skill-library/SkillCard.tsx`：

1. 顶部 import 增补（`Upload` 图标 + VersionUploader）：

```tsx
import { ChevronDown, ChevronUp, Download, FileText, ShieldCheck, Trash2, Upload, User } from "lucide-react";
import { VersionUploader } from "./VersionUploader";
```

2. 组件体内 `const [expanded, setExpanded] = useState(false);` 之后加：

```tsx
  const [showUploader, setShowUploader] = useState(false);
  const canUploadVersion = isAdmin || !skill.is_official;
```

3. 在头部按钮组里（`marginLeft: "auto"` 的那个 `<div>` 内，「版本历史」按钮**之前**）插入：

```tsx
          <button
            type="button"
            className="secondaryButton"
            style={{ height: 30, padding: "0 10px", fontSize: 12.5 }}
            title={canUploadVersion ? "上传新版本（完全替换）" : "官方包仅 admin 可上传新版本"}
            disabled={!canUploadVersion}
            onClick={() => setShowUploader((v) => !v)}
          >
            <Upload size={13} /> 上传新版本
          </button>
```

4. 在 `expanded` 版本历史块**之后**（组件根 `</div>` 之前）追加内联面板：

```tsx
      {showUploader ? (
        <div style={{ marginTop: 12, paddingTop: 12, borderTop: "1px solid var(--hair)" }}>
          <VersionUploader
            skillId={skill.id}
            onDone={() => {
              setShowUploader(false);
              onChanged();
            }}
            onCancel={() => setShowUploader(false)}
          />
        </div>
      ) : null}
```

- [ ] **Step 3: typecheck + build**

Run（在仓库根 `E:\geo`）：`pnpm --filter @geo/web typecheck && pnpm --filter @geo/web build`
Expected: PASS（两步都绿）。

- [ ] **Step 4: 手动核验（本地 dev，5173）**

启前端 dev（`pnpm --filter @geo/web dev`）+ 后端（`uvicorn server.app.main:app --reload --port 8000`），登录后进「MCP 接入」Panel⑤：
1. 非官方 skill 卡 →「上传新版本」可点 → 传一个含 SKILL.md 的文件夹/zip → toast「已追加 v2 并设为当前」，卡片当前版本号跳 v2。
2. admin 账号下官方 goal 卡 →「上传新版本」可点。
3. （若有非 admin 账号）官方 goal 卡的「上传新版本」按钮为 disabled、hover 提示「官方包仅 admin 可上传新版本」。

- [ ] **Step 5: 提交**

```bash
git add web/src/features/mcp/skill-library/VersionUploader.tsx web/src/features/mcp/skill-library/SkillCard.tsx
git commit -m "feat(web): skill 卡片「上传新版本」按钮 + 内联 VersionUploader"
```

---

## 收尾（非代码任务，运维备注）

- 上线后由 admin 在「MCP 接入」Panel⑤ 用官方 goal 卡的「上传新版本」把仓库 `templates/` 打包上传，验证官方包能正常升版。
- 误建的平行包 `skills`（id=2）确认无用后由 admin 用卡片删除入口移除。
- 本功能未新增 MCP tool，`MCP_TOOLS_COUNT` 不变。

## Self-Review

- **Spec coverage**：①接口契约→Task 2；②`_append_version`+`add_version`→Task 1；③数据流→Task 3+4；④权限矩阵→Task 1(service 校验)+Task 2(HTTP 403)+Task 4(前端禁用);⑤前端交互→Task 3+4;⑥平行包→收尾备注;⑦测试→Task 1/2 的 TDD 步骤全覆盖(官方 admin/非 admin、非官方、缺 SKILL.md、不存在、完全替换)。无遗漏。
- **Placeholder scan**：无 TBD/TODO；每个 code 步骤含完整代码与预期输出。
- **Type consistency**：`add_version(session, *, skill_id, entries, uploaded_by, is_admin)` 在 Task 1 定义、Task 2 按此签名调用；`_append_version(session, skill, bundle, uploaded_by)` 定义与两处调用一致；`uploadSkillVersion(skillId, files)` Task 3 定义、Task 4 调用一致；`UploadResult` 字段 `{skill_id, slug, version_label}` 全程一致。
