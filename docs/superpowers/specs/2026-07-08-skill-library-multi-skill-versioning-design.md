# Skill 库:多 skill × 多版本上传与版本管理 — 设计文档

- 日期：2026-07-08
- 来源需求：飞书 PRD《GEO 平台 · Skill 上传与版本管理》(苏鑫，2026-07-06)
  <https://eau5q7pdkqn.feishu.cn/docx/L7nUd3UIlosPQ3xrNWCcbdlqnxb>
- 前置实现：`2026-07-07-loop-skill-bundle-versioning-design.md`(单 bundle 多版本，本设计在其之上升级）

## 1. 背景与目标

当前 `loop_skills` 模块只支持**一个 bundle（官方 `/goal` 包）的多版本管理**：单表
`loop_skill_bundle_versions`、文件存 DB JSON 列、全局单一 `is_enabled` 指针、启用/删除收归
admin、端点在 `/api/mcp/loop-skill-bundle/*`、前端是一个「版本管理」面板。

PRD 要把它升级为「**多 skill × 多版本的 Skill 库**」：任意登录用户可上传自定义 skill（不止官方
`/goal` 包），每个 skill 独立多版本、独立当前指针、独立版本历史 + 一键回滚 + 删除；前端在
「MCP 接入」页 Panel ⑤ 改造为 Skill 库工作台。核心价值：**skill 与代码解耦发布**——改 SKILL.md
不必改代码、不必重新部署云服务器。

### 非目标（本期不做）

- 统计报表 UI（PRD §3.1）：延后，只补审计埋点，报表另开一单。
- MinIO 实际启用：存储层建好 MinIO 后端代码，但本期上传一律落 DB；MinIO 路径留给未来「多级目录 /
  带脚本 / 带二进制素材」的大 skill。
- 新增 MCP 工具：不新增，MCP 工具总数保持 **25**。

## 2. 关键决策（brainstorming 已与用户逐条确认）

| # | 决策 | 结论 |
|---|---|---|
| 1 | 文件存储 | **默认存 DB，MinIO 后端保留**。`skill_versions` 加 `storage_backend` 判别位 + `files`(JSON) + `storage_key` 两列共存；上传写 db，读按判别位分叉。未来大 skill 只改上传侧 `storage_backend=minio`，表不再迁移。 |
| 2 | 现有版本迁移 | **挂进新表**。一次性 seed 脚本把现有 `loop_skill_bundle_versions` 未删行搬进官方 skill 的 `skill_versions`；旧表**保留休眠不 drop**。 |
| 3 | 官方 skill 标识 | `name="/goal loop skills"`、`slug="goal"`、`is_official=true`。 |
| 4 | 前端还原度 | **像素级还原 PRD 深色设计图**（实现时先用飞书 CLI `docs +media-download` 拉 4 张截图当基准）。 |
| 5 | 端点兼容 | 旧 `/loop-skill-bundle/*` 保留成**废弃别名一个版本**，内部解析到官方 skill 当前版本；`install_loop_skills` 工具签名不变、内部重指新路径（对客户端透明）。稳定后再删旧端点。 |
| 6 | 回滚权限 | 从现在的 admin-only **放宽给所有登录用户**（与问题池「全员共享」一致）。 |
| 7 | `version.py` 人肉 SHA 纪律 | **退场**。删 `KNOWN_BUNDLE_SHAS` + CI `test_bundle_sha_is_known`，SHA 改上传时算入库。 |
| 8 | 统计 | **延后**，本期只补 upload/set-current/delete 审计埋点。 |
| 9 | 上传格式 | multipart，收**一个 `.zip` 或多文件数组**（文件夹 `webkitdirectory` / 单 SKILL.md）；前端不引 zip 库、不算 SHA。 |

## 3. 数据模型

两张新表，挂 `loop_skills` 模块。FK/PK 用 `Integer` 对齐 `users.id`（BIGINT→INT 会触发 MySQL
errno 150，现表已踩过）。

### 3.1 `skills`（skill 主表）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | Integer PK | |
| `name` | String(128), unique | skill 名，同名上传=追加版本 |
| `slug` | String(128), unique | URL/命令/install 用短标识 |
| `is_official` | Boolean | `/goal` 包=true，仅徽章 + 删除保护用 |
| `current_version_id` | Integer FK→skill_versions.id, nullable | 当前版本指针，回滚=改此字段 |
| `created_by` | Integer FK→users.id | |
| `is_deleted` | Boolean | 软删整个 skill |
| `created_at` / `updated_at` | DateTime | |

- 唯一约束：`name`、`slug` 在**未删记录内**唯一（PRD 4.1：删整个 skill 后可重新上传同名）。
  MySQL 无 partial index，用**生成列**实现：`name_active = CASE WHEN is_deleted THEN NULL ELSE name END`
  （`slug_active` 同理），在两个生成列上建唯一索引——软删行落 NULL、NULL 之间不冲突，故"未删的
  name/slug 唯一、软删的不占键"。上传同名命中的是**未删** skill → 追加版本；命中已软删的忽略、当新建。
- 索引：`ix_skills_deleted (is_deleted)`；唯一：`uq_skills_name_active (name_active)`、
  `uq_skills_slug_active (slug_active)`。

### 3.2 `skill_versions`（版本表）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | Integer PK | |
| `skill_id` | Integer FK→skills.id | |
| `version_label` | String(32) | skill 内递增 `v1/v2…`，`(skill_id, version_label)` 唯一 |
| `bundle_sha256` | CHAR(64) | 上传时算入库 |
| `file_count` | Integer | |
| `total_bytes` | Integer | 校验 ≤ 5 MB |
| `storage_backend` | String(8) | `'db'` \| `'minio'` 判别位 |
| `files` | JSON, deferred, nullable | `storage_backend='db'` 时存 `[{path,content,sha256,size}]` |
| `storage_key` | String(256), nullable | `storage_backend='minio'` 时存 ZIP 的 MinIO key |
| `uploaded_by` | Integer FK→users.id, nullable | 官方初始版=null |
| `is_deleted` | Boolean | 软删单版本 |
| `uploaded_at` | DateTime | |

- 唯一约束：`(skill_id, version_label)`。
- 索引：`ix_skill_versions_skill (skill_id, is_deleted)`。
- 删除规则：**不允许删 `skills.current_version_id` 指向的版本**（服务层校验→409 ConflictError；
  DB 侧不加级联，避免指针悬空）。
- 不变式：`storage_backend='db'` ⇒ `files` 非空、`storage_key` 空；`='minio'` ⇒ 反之。

### 3.3 存储读写路径

- **写**（上传）：一律 `storage_backend='db'`，打包/SHA 沿用现成
  `service.build_bundle_from_file_map`，`files` 存 JSON。
- **读**：`_version_to_bundle(row)` 按 `storage_backend` 分叉——
  - `db`：解 `files` JSON（现逻辑）。
  - `minio`：用 `image_library/store.py` 同款 MinIO 客户端拉 `storage_key` 的 ZIP、解出 file map。
- MinIO 后端代码建好（`_load_files_from_minio` / `_save_files_to_minio`）但本期不被上传触发。

## 4. 后端接口

均挂 `/api/mcp` 前缀。沿用鉴权边界：Web 端 user JWT、MCP 端 `GEO_MCP_TOKEN`（隔离）。
未捕获异常走 `core/mcp_errors.mcp_exception_response`。

| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| GET | `/api/mcp/skills` | user JWT | 列所有未删 skill（当前版本元信息 + is_official），列表页用 |
| POST | `/api/mcp/skills/upload` | user JWT | multipart 上传 → 校验 → 新版本 → 设当前；同名追加、无则新建。返回 skill_id + 新 version_label |
| GET | `/api/mcp/skills/{id}/versions` | user JWT | 版本历史（元信息，不含正文） |
| POST | `/api/mcp/skills/{id}/set-current` | user JWT | 回滚，body `{version_id}` |
| DELETE | `/api/mcp/skills/{id}/versions/{vid}` | user JWT + 属主/admin | 拒删当前版本→409 |
| DELETE | `/api/mcp/skills/{id}` | user JWT + admin | 软删整个 skill |
| GET | `/api/mcp/skills/{id}/download.zip` | user JWT | 当前版本 ZIP |
| GET | `/api/mcp/skills/{slug}/install-payload` | MCP token | 给 `install_loop_skills` 用，返回当前版本 file dict |

### 4.1 上传接口校验顺序（对应前端三步 tracker）

1. 接收文件（multipart：一个 `.zip` 或多文件数组）。
2. 解压 & 归一成 file map & 校验：含 ≥1 个 `SKILL.md`（任意层级）、总 ≤ 5 MB、条目数 ≤ 上限、
   单条目 ≤ 上限、zip-slip / zip-bomb 防护、UTF-8 文本；**任一失败即 400 ValidationError，不占版本号**。
   - 去掉旧的 `/goal` 专属必需项（`commands/goal.md` + `geo-goal-orchestrator/SKILL.md`）。
   - 白名单放宽：不再限定 `README.md / commands/ / skills/` 顶层；仅保留路径安全校验。
3. 计算 `bundle_sha256`。
4. 落库：命中已存在 `skills.name` → 追加新版本（`version_label` = 该 skill 内 `v{max+1}`）；
   不存在 → 新建 skill 记录（`slug` 由 name 归一生成，冲突加短后缀）。写 `skill_versions`
   （`storage_backend='db'`）→ 更新 `skills.current_version_id` 指向新版本。

### 4.2 并发

per-skill `current_version_id` 指针，set-current = 一条 `UPDATE skills SET current_version_id=?
WHERE id=?`，天然按行串行，**去掉旧模型的全局 `GET_LOCK` 单例锁**。上传同名并发追加版本：
`(skill_id, version_label)` 唯一约束兜底，冲突重试取下一个 label。

### 4.3 权限（服务端强制，前端置灰仅体验，越权 403）

| 操作 | 谁 |
|---|---|
| 上传 / 回滚(set-current) / 下载 / 装机 | 所有登录用户 |
| 删单版本 | `skill_versions.uploaded_by == 当前用户` 或 `role==admin`；官方包（`is_official`）版本删除=仅 admin |
| 删整个 skill | 仅 admin（复用 `require_admin`） |

## 5. 兼容与迁移

1. **Alembic 迁移**（纯 DDL）：建 `skills` + `skill_versions` 两表 + 索引/约束。**不在迁移里做
   MinIO/文件 IO**。
2. **一次性 seed 脚本**（`server/scripts/seed_skill_library.py`，像 `seed_users`，部署跑一次、幂等）：
   - 建官方 skill（`name="/goal loop skills"`, `slug="goal"`, `is_official=true`, `created_by=null`）。
   - 把现有 `loop_skill_bundle_versions` 未删行逐条搬进 `skill_versions`（`storage_backend='db'`，
     `files` 直接搬，`version_label` 沿用原值，`uploaded_by` 沿用）。
   - 原 `is_enabled=true` 的行 → 设为官方 skill 的 `current_version_id`；一条都没有则从
     `templates/` 灌一个 v1（`version_label` 用 `LOOP_SKILL_BUNDLE_VERSION`）。
3. **旧端点处理**：只读端点 `/loop-skill-bundle/info|download.zip|versions|install-payload` 保留成
   废弃别名一版，内部解析到 `slug=goal` 的当前版本。旧**写**端点（`POST /versions` 上传、
   `enable`、`DELETE /versions/{id}`）**直接下线**（前端已切新 `/skills/*`、无外部调用者），不做别名。
   稳定后另一单删除全部旧别名。
4. **`install_loop_skills` MCP 工具**：签名不变，内部 `_aget` 改指
   `/api/mcp/skills/goal/install-payload`；对 Claude Code 客户端透明。
5. **`version.py`**：删 `KNOWN_BUNDLE_SHAS` 与 CI `test_bundle_sha_is_known`；`LOOP_SKILL_BUNDLE_VERSION`
   仅在 seed 兜底 v1 时引用，保留常量。
6. 旧表 `loop_skill_bundle_versions` 保留休眠不 drop。

## 6. 前端（Panel ⑤ → Skill 库工作台）

像素级对齐 PRD 深色设计图，沿用 `web/src/features/mcp/McpConnectWorkspace.tsx` 行内样式体系。
新增 API 客户端方法在 `web/src/api/mcp.ts`（对应新 `/skills/*` 端点）。

- **上传区**：虚线紫框 + `cloud-upload` 图标 +「拖拽 ZIP 或文件夹到此上传」+「选择文件」；下方约束
  提示（≤5MB / 含 ≥1 SKILL.md / 同名追加不覆盖）。拖拽 + `webkitdirectory` + `.zip` 三入口。
- **四态**：idle → 上传中（文件名 + 进度条 + 三步 tracker：接收文件→校验 SKILL.md→生成版本/入库）
  → 成功 toast（绿勾 + skill 名/新版本/已设当前/旧版保留 + 文件数·SHA 徽章 +「查看版本历史→」，
  ~5s 自动消失）/ 失败红卡（缺 SKILL.md / 超 5MB 两类文案）。
- **空态**：`package-open` +「还没有任何 Skill」引导回上传区。
- **列表** `已入库 SKILL·N`：行卡=名称 + 徽章（官方紫/自定义灰）+ 当前版本 + 文件数 + 大小 +
  更新时间 + 上传人；右侧：版本历史(展开) / ZIP(下载) / 删除。官方 `/goal` 包置顶。
- **版本历史展开**：缩进深底表（版本·时间·上传人·SHA-256·操作）；当前行绿「当前/已是当前」(禁)，
  历史行紫「设为当前」；底部附可复制「让 Claude Code 自己装」命令块（命令随 skill slug 变）。
- **二次确认**：回滚（紫、可逆、current→target diff、强调"不删版本/可再切回/影响下载·Claude 安装·
  install_loop_skills 三下游"）/ 删单版本（红、当前版不可单删、"当前版本不受影响"）/ 删整个 skill
  （红、"连同全部 N 版本永久删除、已装本机副本不受影响"）。
- **按权限置灰**删除按钮：非属主 + 非 admin 置灰；官方包删除对非 admin 全灰；回滚/上传/下载/装机不置灰。

## 7. 审计与统计

- **审计埋点**（本期做）：`skill.upload` / `skill.set_current` / `skill.delete_version` /
  `skill.delete` 写 `add_audit_entry`（`target_type="skill"` / `"skill_version"`）。
- **统计报表**（延后另开一单）：上传 PV/UV、成功率、回滚数、删除数、装机数从 `audit_logs` 事后派生；
  "复制命令次数"需前端埋点，一并延后。

## 8. 测试

- 后端 pytest（MySQL）：
  - 上传：合法 zip / 多文件 / 单 SKILL.md 入库；缺 SKILL.md→400；超 5MB→400；zip-slip/bomb 拒；
    校验失败不占版本号。
  - 同名追加：第二次上传同 name → 新版本 `v2`、`current_version_id` 移到 v2、v1 保留。
  - set-current：回滚到历史版本、再切回；非当前版本可删、当前版本删→409。
  - **删除权限矩阵**：operator 删自己上传的版本 OK / 删他人的→403 / 删官方包版本→403 /
    删整个 skill(operator)→403 / admin 全通。
  - seed 迁移：有既有行→搬入并设当前；空→templates 灌 v1。
  - 兼容：`install_loop_skills` install-payload 走 `slug=goal` 返回当前版本；旧别名端点仍回内容。
- 前端：沿用 `typecheck` + `build` 门禁（无单测框架）。

## 9. 影响面 / 风险

- 存储从 DB→未来 MinIO 的切换点集中在 `_version_to_bundle` / 上传落库两处，判别位隔离，风险低。
- 权限放宽（回滚全员、删除属主级）是安全敏感面，服务端强制 + 权限矩阵测试兜底。
- 旧端点别名期：`install_loop_skills` 与前端切换需同批上线，避免"新端点未上、旧逻辑已删"的窗口。
- seed 脚本必须幂等（可重跑）：以 `slug=goal` 是否已存在为闸。
