# Loop Skill 包版本化(上传入库 + 版本管理 + 按版本投递)设计

> 日期:2026-07-07 · 状态:待评审 · 模块:`server/app/modules/loop_skills`

## 背景与问题

`/goal` Loop skill 的正本目前**硬写在 repo 的 `templates/` 文件夹**里,通过 `build_bundle()`(运行时扫文件夹)经三个端点分发:

- `GET /api/mcp/loop-skill-bundle/info`(user JWT)—— Web「MCP 接入」Section ⑤ 显示版本/校验
- `GET /api/mcp/loop-skill-bundle/download.zip`(user JWT)—— 下载 zip
- `GET /api/mcp/loop-skill-bundle/install-payload`(MCP token)—— `install_loop_skills` 工具后端入口

痛点:每次调整重试规则 / 提示词都要**改文件 → git commit → 部署 → docker rebuild**,且改任何 `templates/` 文件都会触发 `bundle_sha` 跨 OS 契约(`KNOWN_BUNDLE_SHAS`,v8~v12 反复栽 5 次)。

**目标**:把正本改成**上传 zip 入库、DB 做版本管理(启用 / 回退 / 逻辑删除)**,中间加一层读取函数;Claude Code 端拿到的是"启用版",且能**点名任意版**投递到本机 `.claude/skills/`;不强制用户更新。

## 已定决策(评审依据)

| 维度 | 决策 |
|---|---|
| 存储形态 | **解压后 + DB JSON 列**(`files` 存 `[{path,content,sha256,size}]`)。不留原始 zip(可由 `build_zip()` 确定性重建) |
| 存储地点 | 数据库(文件小、几十 KB;免磁盘↔DB 一致性与共享盘假设;读最快) |
| 管理权限 | **上传**开放给所有登录用户(`get_current_user`);**启用 / 删除 / 软删收归 admin**(`require_admin`)——启用版内容会写进每个同事的 `.claude/skills/` 并被 Claude Code 执行,属供应链面,写权限收窄(评审决策 2026-07-07b)。每个写操作写 `AuditLog` 溯源 |
| 下载粒度 | **启用版为默认,可点名任意版**;`install_loop_skills` / `download.zip` / `install-payload` 均加可选 `version` |
| 删启用版 | **禁止**;必须先启用别的版再删 |
| 软删清理 | 不自动(YAGNI);逻辑删除只隐藏 |
| 版本重名 | `version_label` 不强制唯一;按名匹配取最新未删除的一条,精确用 id |
| 契约退休 | `test_bundle_sha_is_known` 缩到只管 `templates/` 种子路径;上传版不受 sha 契约约束 |

## 架构总览

**核心接缝**:三个出口端点从直接调 `build_bundle()`(扫文件夹)改为调 `get_active_bundle(session)`——有 DB 启用版就返回它,否则回落扫 `templates/`(种子/兜底)。**用户端零改动**即拿到 DB 版本。

```
上传 zip ──► 解压 + 校验 + 算 sha ──► 存 DB 行(默认不启用)
                                            │
Web/CC 装 ◄── build_zip/文件列表 ◄── get_active_bundle(session)
                                            ├─ DB 启用版(is_enabled=True, is_deleted=False)
                                            └─ 回落 build_bundle()  ← templates/ 种子
```

## 组件设计

### 1. 数据模型 — `models.py`(新)

表 `loop_skill_bundle_versions`:

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | **INT 不是 BIGINT** —— `users.id` 是 INT(`system/models.py`),下面 FK 引用它需类型一致 |
| `version_label` | VARCHAR(200) | 版本名/说明;上传不填则默认时间戳串。**不唯一** |
| `bundle_sha256` | CHAR(64) | 上传时算,与 `build_bundle` **同算法**(posix 串排序 + `path\x00 filesha \x00`) |
| `files` | JSON(**deferred**) | `[{path, content, sha256, size}]`,映射 `SkillFile`。本项目**首次用 `deferred()`**,需自测列表查询不触发正文加载 |
| `is_enabled` | BOOL default false | 全局唯一启用(**应用级锁**保证,见下) |
| `is_deleted` | BOOL default false | 逻辑删除 |
| `uploaded_by_user_id` | INTEGER FK users.id null | 溯源;**INT** 对齐 `users.id`,BIGINT→INT 会触发 MySQL errno 150(FK 类型不匹配、迁移失败) |
| `notes` | VARCHAR(500) null | 变更说明 |
| `created_at` / `updated_at` | DATETIME | |

索引:`(is_enabled, is_deleted)`(启用版查找)、`is_deleted`(列表)。`files` 列用 SQLAlchemy `deferred()` —— **列表查询绝不加载正文**,只有组包给具体版本时才拉。

**唯一启用不变式**:不靠 DB 约束(MySQL 无 partial-unique),靠**应用级锁**。`enable(id)`:取应用级锁 `SELECT GET_LOCK('geo_loop_skill_enable', 10)` → `UPDATE ... SET is_enabled=0 WHERE is_enabled=1` → `SET is_enabled=1 WHERE id=:id AND is_deleted=0` → **提交** → 释放锁。

> ⚠️ **连接亲和(评审必修,rev 2026-07-07b)**:`GET_LOCK` 是 MySQL **连接级**锁,而 `Session.commit()` 会把连接归还池、下条语句可能换连接 → 若在 `session` 上 commit 再 `RELEASE_LOCK`,释放会落到**别的连接**、原锁泄漏在池里(后续 enable 持续 10s→409)。因此 **GET_LOCK → updates → commit → RELEASE 必须全部钉在同一条独占 `Connection`** 上(`session.get_bind().connect()`);`Connection.commit()` 不归还物理连接(与 `Session.commit()` 的关键区别)。实现见 plan Task 5,并有"跑完用全新连接 `GET_LOCK(name,0)` 应立即返回 1"的锁释放断言。

> ⚠️ **不能只靠"先清后置"**(评审实测竞态):**从零启用态**(种子/首次使用即 0 启用行,`get_active_bundle` 回落种子正是此态)下并发 `enable(A)`/`enable(B)`,两者的 `SET is_enabled=0 WHERE is_enabled=1` 都命中 **0 行、无行锁可争**,随后各置自己那行 → **双启用、不变式破裂**。必须显式加应用级锁串行化。
> `soft_delete` 的"拒删当前启用版"是 check-then-act,**同锁保护**(或对目标行 `FOR UPDATE`),避免"删后又被并发 enable"留下 0/2 启用。
> **审计边界(rev 2026-07-07b 订正)**:原述"审计放在两条 UPDATE 之后、锁未释放前、三者同一事务"**已作废**。因写+提交+释放锁改在独立 `Connection` 上完成(见上),审计改由 router 用传入的 `session` 在锁**释放后**单独写(`add_audit_entry` 自 commit)——审计只是日志、后写无碍,且锁必须尽早释放。不要为了"同事务"把 `add_audit_entry` 塞进锁内。

### 2. 读取 seam + 打包复用 — `service.py` / `versions_service.py`(新)

保持 `service.py`「无 DB 访问」的纯度:把 `build_bundle()` 的核心抽成
`build_bundle_from_file_map(raw: dict[str, bytes]) -> SkillBundle`(给定 posix 路径→字节,产出排序好的 bundle),**文件夹扫描与 zip 上传共用同一 sha 算法**。

DB 相关放**新文件** `versions_service.py`:

```python
def get_active_bundle(session) -> SkillBundle:
    # order_by(id.desc()).first() —— 不用 .one():容忍并发窗口内瞬时双启用,取最新一条
    row = <select where is_enabled and not is_deleted, order_by id desc, first>
    return _row_to_bundle(row) if row else build_bundle()   # 回落种子

def get_bundle_by_id(session, version_id) -> SkillBundle       # 点名任意版(未删)
def resolve_bundle_for_install(session, version: str | None)   # None→启用版;数字→id;否则 label 最新未删
def list_versions(session) -> list[VersionMeta]                # 元信息,不含 files
def upload_version(session, zip_bytes, label, notes, user_id) -> VersionMeta
def enable_version(session, version_id, user_id) -> None       # 事务;拒已删
def soft_delete_version(session, version_id, user_id) -> None  # 拒当前启用版

def _row_to_bundle(row) -> SkillBundle:
    return SkillBundle(row.version_label, row.bundle_sha256,
                       [SkillFile(**f) for f in row.files])
```

### 3. 上传 / 解压 / 校验

`upload_version` 流程:
1. **压缩体积上限** `LOOP_SKILL_MAX_ZIP_BYTES`(2MB;超限 → `ValidationError`)。
2. **条目数上限** `LOOP_SKILL_MAX_ENTRIES`(50;跳目录项后统计——白名单不限数量,靠它挡"海量小文件")。
3. `zipfile.ZipFile(io.BytesIO(data))` 遍历成员(评审必修:补条目/累计/重名三道,照搬 `accounts` 范式):
   - **防 zip-slip**:名字含 `..` / 绝对路径 / 以 `/` 开头 → 拒。
   - 跳过目录项。
   - **白名单前缀**:仅 `README.md`、`commands/`、`skills/`;其它 → 拒并列出。
   - **重名路径拒绝**:zip 允许同名条目,静默覆盖会让 sha 与内容脱节 → 见到 `name in raw` 直接拒。
   - **单条目解压上限** `LOOP_SKILL_MAX_ENTRY_BYTES`(2MB)。
   - **累计解压上限** `LOOP_SKILL_MAX_TOTAL_UNCOMPRESSED`(4MB;防高压缩比 zip bomb:2MB 压缩包可膨胀到 GB 级)。
   - 每个成员 `utf-8` decode 失败 → 拒(模板必是文本)。
4. **结构校验**:必须含 `commands/goal.md` + `skills/geo-goal-orchestrator/SKILL.md`;缺 → `ValidationError` 列出缺哪个。
5. **`version_label` / `notes` 清洗**(评审必修):`strip` + 长度上限(`≤200` / `≤500`)+ **拒控制字符**(`\r\n\t` / DEL)——它俩会被下游放进 HTTP 响应头,含 `\r\n` = header 注入。中文本身放行(下载头由 `_zip_response` percent-encode 兜底)。`label` strip 后为空 → 回落时间戳。
6. `build_bundle_from_file_map(raw)` → files + `bundle_sha256`。
7. 存行:`is_enabled=False`(**传完不自动生效**)、`uploaded_by_user_id`、清洗后 `label` / `notes`。
8. `AuditLog`:`add_audit_entry(db, user=current_user, action="loop_skill_bundle.upload", target_type="loop_skill_bundle", target_id=str(new_id), ...)`(`audit/service.py:55`;`action≤80`/`target_type≤40`/`target_id` 转 str)。**上传后单独 commit(或依赖 `add_audit_entry` 的 commit),别让审计失败回滚掉上传**——审计对异常 rollback 会连带丢弃未提交的上传行。

> **复用现成范式**(评审加分项):zip 校验直接照搬 `accounts/router.py:404-437`(条目数上限 + 逐条目 `file_size` 解压炸弹防护 + 路径白名单正则 + `BadZipFile→400`)+ `accounts/auth.py` 的 `is_relative_to` 防 zip-slip;前端 multipart 复用 `api/core.ts` 的 FormData 自动分支(同 `accounts.ts:importAccountPackage`)。

### 4. HTTP 端点 — `router.py`

**改造已有 3 端点**(注入 `Depends(get_db)`;`download.zip` / `install-payload` 加可选 `version`):

| 方法 | 路径 | 变化 |
|---|---|---|
| GET | `/loop-skill-bundle/info` | 改调 `get_active_bundle(session)` |
| GET | `/loop-skill-bundle/download.zip?version=` | 可选 `version`,默认启用版 |
| GET(MCP) | `/loop-skill-bundle/install-payload?version=` | 可选 `version`,`resolve_bundle_for_install`;找不到→`ok:false`+可选版本列表 |

**新增管理端点**(鉴权见「管理权限」:上传 user JWT,启用/删除 `require_admin`):

| 方法 | 路径 | 鉴权 | 作用 |
|---|---|---|---|
| GET | `/loop-skill-bundle/versions` | user | 列表(元信息,排除软删,含 `is_enabled` / 上传人 / 时间 / 体积 / notes) |
| POST | `/loop-skill-bundle/versions` | user | 上传 zip(multipart:`file` + 可选 `version_label` / `notes`) |
| POST | `/loop-skill-bundle/versions/{id}/enable` | **admin** | 启用(=回退);已删 → 400 |
| DELETE | `/loop-skill-bundle/versions/{id}` | **admin** | 逻辑删除;当前启用版 → 409「先切别的版」 |
| GET | `/loop-skill-bundle/versions/{id}/download.zip` | user | 下载指定版;不存在/已删 → **404** |

> **下载头 ASCII 安全(评审必修)**:三个下载端点(`download.zip` / `download.zip?version=` / `versions/{id}/download.zip`)共用 helper `_zip_response`:文件名用 ASCII slug(`geo-loop-skills-<sha12>.zip`)+ RFC 5987 `filename*=UTF-8''<pct-encoded>`;`X-Bundle-Version` 用 `urllib.parse.quote` 百分号编码。否则用户上传的中文 `version_label`(spec 例子"严格版")进 Starlette latin-1 头会 500 / header 注入。

错误一律走命名异常(`ValidationError`/`ClientError` → 400,`ConflictError` → 409),不抛裸 `ValueError`。

### 5. MCP 工具 — `server/mcp/tools/action.py`

`install_loop_skills(version: str | None = None)`:透传到 `/install-payload?version=`。不填=启用版;填了=该版直接写进本机 `.claude/skills/`;找不到→后端回可选版本列表让 CC 帮挑。**工具总数不变(仍 21)**,只加可选参数,不碰 `MCP_TOOLS_COUNT` 与 bundle sha 契约。

### 6. 前端 — `web/src/features/mcp/McpConnectWorkspace.tsx` + `web/src/api/mcp.ts`

「MCP 接入」tab 现有 Section ⑤ 下加「版本管理」块:
- 拖拽 / 选择 `.zip` 上传框 + 可选版本名 / 说明。
- 版本列表表格:版本名 / sha 短 / 文件数 / 体积 / 上传人 / 时间 / `启用中`徽标 / 操作(启用 · 下载 · 删除)。当前启用版高亮。
- `api/mcp.ts` 加:`listBundleVersions` / `uploadBundleVersion`(multipart)/ `enableBundleVersion` / `deleteBundleVersion` / `downloadBundleVersion(id)`。

### 7. 迁移 — Alembic

新 revision 建表 `loop_skill_bundle_versions`,down 为 drop。**无数据迁移**:首次无 DB 行时 `get_active_bundle` 自动回落 `templates/` 种子,平滑零停机。(可选后续加一键「把当前 templates/ 种入 DB 并启用」——本期不做。)

### 8. 契约退休

`test_bundle_sha_is_known`(`test_loop_skill_bundle.py`)**本就只断言 `build_bundle()`(种子路径)、不涉上传版,故无需改动**——所谓"契约退休"实为 no-op(评审订正,原表述"缩到只管种子"是错的、它一直就只管种子)。意义仍在:repo `templates/` 继续守「改种子必 bump」纪律,DB 上传版的 sha 无法预登记、天然不受约束。效果:跨 OS sha 坑从「每次改 md 都栽」降到「只有动 repo 种子才需管」。

> **另注(评审新发现,须保命)**:`/info` 与 `/install-payload` 两端点测试硬断言 `len(files)==5`(`test_loop_skill_bundle.py:144/192`)。改造后它们**穿过新的 `get_db` seam**,靠"测试库无启用版 → `get_active_bundle` 回落种子=5 文件"通过 → **必须保持回落语义 + `/install-payload` 响应壳 `{ok, data:{version, bundle_sha256, install_hint, files}, error}` 原样不变**。

## 数据流

- **装启用版**(默认):CC `install_loop_skills()` → `/install-payload`(无 version)→ `get_active_bundle` → DB 启用版 or 种子 → 文件写入 `.claude/skills/`。
- **装指定版**:`install_loop_skills(version="2026-07-08 严格版")` → `resolve_bundle_for_install` 按 label 取最新未删 → 该版文件写入。
- **网页下载任意版**:版本列表 → `GET .../versions/{id}/download.zip` → 解压到 `.claude/`。
- **回退**:启用旧版 = 事务切 `is_enabled`;下次任何人装默认即得旧版。

## 错误处理

| 情况 | 响应 |
|---|---|
| zip 超限 / 非 zip / 解压失败 | 400 `ValidationError` |
| 非 utf-8 成员 / zip-slip / 非白名单路径 | 400 列出问题项 |
| 缺 `goal.md` 或 orchestrator SKILL | 400 列出缺失 |
| 启用已删版 | 400 |
| 删当前启用版 | 400「先启用别的版再删」 |
| `install-payload?version=` 找不到 | `{ok:false, error, data:{available:[...]}}` 透传给工具 |
| 指定 `version_id` 下载不存在/已删 | 404 |

## 测试(`server/tests/test_loop_skill_bundle_versions.py`,新)

- 上传合法 zip → 建行、默认不启用、`bundle_sha256` == `build_bundle_from_file_map` 值。
- 上传拒斥:非 utf-8 / zip-slip / 缺必需文件 / 非白名单路径 / 超体积。
- 启用 → 恰好一个 `is_enabled`;启用清掉前一个;**从零启用态并发 `enable(A)`/`enable(B)` 后仍恰好一个**(验应用级锁,守评审所指双启用竞态)。
- `get_active_bundle`:有启用版返回它;无则回落 `build_bundle()`。
- 软删隐藏于列表;删当前启用版被拒。
- `download.zip?version=` / `install-payload?version=` 返回指定版;不存在→404 / `ok:false`。
- 列表只返回元信息(不含 `files` 正文)、排除软删。
- MCP `install_loop_skills(version=...)` 端到端(mysql 标记)。
- **回归**:`test_bundle_sha_is_known` 种子路径仍绿(`templates/` 未变);`/info` 与 `/install-payload` 的 `len(files)==5` 用例改造后穿 seam、靠回落种子仍绿。
- **前置**:新表已登记进 `server/tests/utils.py:_model_modules()`,否则本文件所有 mysql 用例因"表不存在"红。

## 范围外(YAGNI)

- 软删版本自动清理 / 永久删除按钮。
- 保留原始上传 zip 字节(逐字节复现下载)。
- 每用户私有版本(所有版本共享;"每人拿自己想要的" = 各自挑选要装哪个共享版)。
- UI 里版本内容 diff / 预览。
- `templates/` 从 build 中移除(仍留作种子/兜底,不移除)。

## 触点清单

`loop_skills/models.py`(新,**并显式登记进 `server/tests/utils.py:_model_modules()` 与 `server/alembic/env.py` 的逐模块 import**——评审头号踩空点:漏了则测试库不建表、`drop_all` 漏清、新测试全挂)· `versions_service.py`(新)· `schemas.py`(新)· `service.py`(抽 `build_bundle_from_file_map`)· `router.py`(改 3 端点 + 加 5 管理端点)· 1 Alembic 迁移 · `server/mcp/tools/action.py`(`install_loop_skills` 加可选 `version` + 改 docstring "5 template files" 文案)· `web/.../McpConnectWorkspace.tsx`(含 Section ⑤ 两处 "5 files" UI 文案)+ `api/mcp.ts` · 测试新文件 · 可选:新表加进 `test_fts_and_migrations.py` 的 `expected_tables` 增强显式性。**不碰发布 / 生成核心链路。**
