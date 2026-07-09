# Skill 库「追加新版本到已有 skill」设计

> 日期：2026-07-09 · 承接 `2026-07-08-skill-library-multi-skill-versioning-design.md`

## 背景与问题

Skill 库（`loop_skills` 多 skill 版本管理，前端「MCP 接入」Panel⑤）上线后，暴露一个**官方包更新缺口**：官方 `/goal loop skills` 包在当前上传 UI 下**无法更新**。

双重根因：

1. 上传区（`web/src/features/mcp/skill-library/UploadZone.tsx`）**没有名称输入框**，skill name 由 `deriveSkillName` 从 zip 名 / 文件夹名自动派生。官方 name = `/goal loop skills`（带前导 `/` + 空格），而文件名 / 文件夹名**不能含 `/`** → 永远派生不出这个名 → 后端 `create_version` 按 `Skill.name == name` 精确匹配永远落空 → 走「新建」分支，建出一个 `is_official=False` 的**独立平行包**。线上误建的 `skills`（id=2）包正是这么来的。
2. 即便名字能对上，官方包（`is_official`）追加版本在 `create_version` 里**需要 admin**，否则 403。

换言之：web 端目前**根本没有「更新官方包」这条路**——不是被拦，是这条路压根没修出来。

## 决策：C 方案（保留新建 + 新增「追加到已有 skill」）

在方案 A（部署脚本从仓库 `templates/` 升版）/ B（上传兜底补全）/ C（UI 加追加入口）中，用户选定 **C**，并要求**保留现有「直接上传新 skill」能力**。

用户已拍板的边界（brainstorming 澄清结论）：

- **入口形态**：每张 skill 卡片加「上传新版本」按钮；顶部上传区保持「只新建」语义不变。
- **追加语义**：**纯完全替换**。新版本 = 本次上传的文件全集。少传即丢文件、传多了照单全收（唯一校验仍是 `validate_file_map`：至少一个 SKILL.md、总 ≤5MB、UTF-8 可解码，文件数量无上限）。**不加**少传保护，**不做** auto-inherit。
- **权限**：官方包仅 admin；非官方包任何登录用户都能追加（沿用现状，非官方无属主校验）。
- **后端接线取向**：新增 REST 端点 `POST /api/mcp/skills/{skill_id}/versions`，按路径 id 精确定位，绕开名字派生；`/skills/upload` 保持纯新建不动。

## 详细设计

### ① 后端接口契约

新端点 `POST /api/mcp/skills/{skill_id}/versions`（user JWT，挂在 `skills_user_router`）：

- **请求**：`multipart/form-data`，只有 `files: list[UploadFile]`——**不带 name、不带 category**（两者都从目标包继承）。
- **行为**：按 id 找活跃包 → 官方包非 admin 拦 403 → 复用 `upload.parse_upload` + `upload.validate_file_map` → 追加一个新 `SkillVersion`（`_next_label` 递增）→ 设为 `current_version_id`。完全替换，`files` = 本次上传全集。
- **响应**：复用现有 `UploadResult { skill_id, slug, version_label }`。
- **审计**：`add_audit_entry(action="skill.upload", target_type="skill", target_id=skill_id, payload={"version": version.version_label})`，与现有上传一致。
- **错误映射**（与现有 `/skills/upload` 路由完全一致）：`ConflictError` / `ValidationError` 走全局兜底（409 / 400）；`ClientError`（权限）→ 在路由内转 403。

### ② Service 层

把 `create_version` 里「build bundle + 插版本 + 撞号重试 + 设 current」这段内循环抽成私有 helper：

```
_append_version(session, skill, entries, uploaded_by) -> tuple[Skill, SkillVersion]
```

两个入口共用，**保持 create_version 现有行为不变**：

- `create_version(...)`（老路）：按 name 找 / 建包（含官方 admin 门禁）后调 `_append_version`。
- 新 `add_version(session, *, skill_id, entries, uploaded_by, is_admin)`：
  - `sk = session.get(Skill, skill_id)`；`None` 或 `sk.is_deleted` → `ValidationError("skill 不存在: {skill_id}")`。
  - `sk.is_official and not is_admin` → `ClientError("官方包仅管理员可上传新版本")`。
  - 否则 `_append_version(session, sk, entries, uploaded_by)`。
  - `category` 不动——它是包级属性（`Skill.category`），追加版本不改。

重构是行为保持的（extract method），现有 `test_skill*` 用例应全绿。

### ③ 数据流

```
卡片「上传新版本」按钮 → 内联轻量上传面板（拖拽 / 选文件）
  → uploadSkillVersion(skillId, files)
  → POST /api/mcp/skills/{skill_id}/versions (multipart, files)
  → svc.add_version(skill_id, entries, uploaded_by, is_admin)
  → 新 SkillVersion 落库 + 设 current
  → 返回 {version_label} → toast「已追加 v{n} 并设为当前」→ 父组件 reload()
```

### ④ 权限与错误

| 情况 | 结果 |
|---|---|
| 官方包 + 非 admin | 403「官方包仅管理员可上传新版本」 |
| 非官方包 + 任意登录用户 | 允许追加（沿用现状，无属主校验） |
| 缺 SKILL.md / >5MB / 非 UTF-8 | 400，复用 `validate_file_map` 文案（前端 `classifyError` 已能识别「缺少 SKILL.md」「文件过大」） |
| 目标包不存在 / 已软删 | 400 |
| 并发撞版本号 | 409「版本号并发冲突，请重试上传」（`_MAX_LABEL_RETRY_ATTEMPTS` 重试逻辑覆盖） |

### ⑤ 前端交互

- `SkillCard` 加「上传新版本」按钮；官方卡且非 admin 时**禁用 + 提示**（复用已有 `isAdmin` prop）。
- 点击展开一个内联小上传面板：抽出 `<VersionUploader skillId onDone>`，复用 `UploadZone` 的接收 / 校验 / 进度 UI，**去掉 category 选择、去掉 name 派生**。
- `web/src/api/skills.ts` 加 `uploadSkillVersion(skillId, files)`：POST FormData（只 append `files`、保 `relativePath || file.name`），与现有 `uploadSkill` 对齐但不带 name / category。
- 成功后 toast + `onChanged` / `reload()` 刷新卡片版本号。

### ⑥ 顺带：误建的平行包（运维备注，不写进代码）

`skills`（id=2）是当初误建的重复包（内容是 goal 子集）。本功能上线后官方 goal 可正常更新，这个平行包**建议 admin 直接删掉**（现有 `DELETE /skills/{skill_id}` 入口已够）。不进代码改动，作为上线后一步运维清理。

## 测试

后端（pytest，`@pytest.mark.mysql`，`build_test_app`）：

- `test_add_version_to_official_as_admin` — admin 追加官方包新版本成功，`current_version_id` 切换，文件 = 上传集。
- `test_add_version_official_non_admin_403` — 非 admin 追加官方包 → 403。
- `test_add_version_non_official_any_user` — 非 admin 也能追加非官方包。
- `test_add_version_missing_skill_md_400` — 上传不含 SKILL.md → 400。
- `test_add_version_skill_not_found_400` — 目标 id 不存在 → 400。
- `test_add_version_complete_replacement` — 上传 3 文件追加到原 5 文件包 → 新版本只有 3 文件（证明无 auto-inherit / 完全替换）。

前端：无单测框架，靠 `typecheck` + `build` 门禁 + 手动过卡片按钮的权限禁用态。

## 不做（YAGNI 边界）

- 不动 `/skills/upload` 新建路径。
- 不做少传保护 / auto-inherit（B 方案）。
- 不做顶部「上传目标」下拉（入口只在卡片）。
- 不写 A 脚本 `bump_official_skill`（C 已够更新官方包）。
- 不动 MCP `install_loop_skills` / catalog 路径。
- 不动 `MCP_TOOLS_COUNT`（未新增 MCP tool，仅新增 user-JWT REST 端点）。

## 影响面清单

- 后端：`skill_router.py`（+1 端点）、`skill_service.py`（抽 `_append_version` + 新 `add_version`）。
- 前端：`api/skills.ts`（+`uploadSkillVersion`）、`skill-library/SkillCard.tsx`（+按钮 + 内联面板）、可能新增 `skill-library/VersionUploader.tsx`（从 `UploadZone` 抽核心）。
- 无 DB 迁移（复用现有 `skill_library_skills` / `skill_library_versions` 表）。
- 无 MCP 变更。
