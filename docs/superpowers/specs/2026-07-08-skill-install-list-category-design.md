# Skill 安装/列举 + Category 分类 设计

- **日期**: 2026-07-08
- **状态**: Draft（待用户 review）
- **作者**: lufeng（brainstorming with Claude）
- **关联**: 承接 `2026-07-08-skill-library-multi-skill-versioning-design.md`（多 skill×多版本 Skill 库已上线 main，merge 93ef3e9）

## 1. 背景与目标

Skill 库已支持任意登录用户上传多 skill × 多版本、web 端版本管理/回滚。但 Claude Code 侧的消费链路仍停留在单包时代：

- `install_loop_skills` MCP 工具**写死 slug=goal**，库里就算传了别的 skill，Claude Code 也看不到、装不了，只能靠 web 手动下 ZIP 解压丢进 `.claude/skills/`。
- MCP 侧**没有列举工具**（前端的 list 是 user JWT，MCP token 够不到）。
- 数据库**没有任何业务分类字段**，无法防止「把发文类 skill 装进生文场景」的功能错配。

**目标：** 让 Claude Code 能「列出库里所有 skill（可按业务类别筛）→ 按 slug 安装任意整包」，不再写死 goal；并用 `category` 业务标签挡住装错类别。

**成功判据：**
- Claude Code 调 `list_skills()` 能看到库里所有 skill 及其 category、当前版本、包内 skill 单元。
- Claude Code 调 `install_loop_skills(slug="<任意>")` 能装非 goal 的整包；不传 slug 仍装 goal（向后兼容现有 4 个 loop 配方）。
- `list_skills(category="generation")` 只返回生文类。
- 保存机制零改动。

## 2. 现状与缺口

| 现状 | 位置 | 缺口 |
|---|---|---|
| `install_loop_skills` 打死 `/skills/goal/install-payload` | `server/mcp/tools/action.py:284` | 无法装别的 skill |
| MCP 侧只有 `install-payload` 一个端点 | `skill_router.py:169-202` (`skills_mcp_router`) | 无列举端点 |
| `Skill` 表无分类字段 | `loop_skills/models.py:52-84` | 无法按业务类别筛/防错配 |
| 前端 list 走 user JWT | `skill_router.py:45-47` (`skills_user_router`) | MCP token 请求用不了 |

## 3. 设计原则（前几轮已定共识）

1. **整包原子。** 一条 `Skill` 记录 = 一个包（可含多个 SKILL.md），阶段（orchestrator/writer/verifier）耦合不拆、不单独寻址/安装。阶段仅作**只读展示**。
2. **保存机制零改动。** 多级文件存取（扁平 path 数组 + `files` JSON）现状已满足，`storage_backend` seam、版本管理、上传解析、`build_zip` 全不碰。
3. **防错配靠 `category` 业务标签**（打在包上），不是拆阶段、不是来源归属（来源已有 `skill_id`/`is_official`/`uploaded_by`）。

## 4. 架构总览（4 个改动单元）

```
┌─ DB ──────────────────────────────────────────────┐
│ Skill 表 + category 列（默认 general）             │
│ 迁移：加列 + data migration 把官方 goal 设 generation │
└───────────────────────────────────────────────────┘
┌─ 后端 MCP list 端点 ──────────────────────────────┐
│ skills_mcp_router 加 GET /skills（MCP token）      │
│ 复用 svc.list_skills + category 筛 + 提取 units    │
└───────────────────────────────────────────────────┘
┌─ MCP 工具（server/mcp/tools/）────────────────────┐
│ ① 新增 list_skills(category?)                      │
│ ② install_loop_skills 收 slug 参数（不传=goal）    │
└───────────────────────────────────────────────────┘
┌─ 前端（web/src/features/mcp/）────────────────────┐
│ 上传加 category 选择器；SkillCard 展示 category 徽章 │
│ svc.list_skills / schema / api 客户端带上 category  │
└───────────────────────────────────────────────────┘
```

## 5. 详细设计

### 5.1 DB：`category` 列 + 迁移

`Skill` 模型（`loop_skills/models.py`）新增：

```python
category: Mapped[str] = mapped_column(String(32), default="general", nullable=False)
```

**枚举取值（固定，非自由标签）：** `generation`(生文) / `distribute`(发文) / `video`(视频) / `general`(通用)。校验在 service/schema 层（不入 DB CHECK，便于后续加值）。非法值 → `ValidationError`。

**迁移**（新 alembic revision，down_revision = 当前 head）：
1. `ALTER TABLE skill_library_skills ADD COLUMN category VARCHAR(32) NOT NULL DEFAULT 'general';`
2. data migration：`UPDATE skill_library_skills SET category='generation' WHERE slug='goal' AND is_official=1;`
   —— 生产已 seed 的 goal 也会被就地修正（seed 是「存在即 skip」，不能依赖它补 category）。
3. downgrade：`DROP COLUMN category;`

**seed 脚本**（`seed_skill_library.py`）：全新库建 goal 时设 `category="generation"`（覆盖全新部署路径；已 seed 库靠上面的 data migration 补）。

### 5.2 后端 MCP list 端点

`skill_router.py` 的 `skills_mcp_router`（已带 `Depends(require_mcp_token)`）新增：

```
GET /api/mcp/skills?category=<optional>
```

返回结构：

```json
{
  "ok": true,
  "data": {
    "skills": [
      {
        "id": 1,
        "slug": "goal",
        "name": "/goal loop skills",
        "category": "generation",
        "is_official": true,
        "current_version_label": "v1",
        "file_count": 5,
        "total_bytes": 40000,
        "units": ["geo-goal-orchestrator", "geo-article-writer", "geo-article-verifier"]
      }
    ]
  },
  "error": null
}
```

- `category` 参数为空 = 返回全部；非空 = 服务端过滤。
- `units`（包内 skill 单元清单）提取规则：取当前版本 `files` 的 path，正则匹配 `^skills/([^/]+)/SKILL\.md$` 收集中间目录名；另有顶层 `SKILL.md`（单文件包）时，units 追加该包 slug 本身。**仅当前版本、纯只读展示**。
- **units 不进 `SkillListItem`：** `svc.list_skills` 只提供基础字段（含新增的 `category`），units 由 **MCP list 端点单独组装**（端点内对每个 skill 的当前版本 files 跑一次提取 helper）。这样前端 user JWT 的 list（`skills_user_router`）复用 `svc.list_skills`、**不带 units、不拉 files**，保持轻量；只有 MCP 端点付这份提取成本。
- **性能取舍：** 提取 units 需拉当前版本的 `files`（deferred 大字段），构成 N 次读。Claude Code 列举属低频操作，可接受；若将来包体变大再去规范化到 `SkillVersion` 冗余列。
- 异常统一走 `mcp_exception_response`（与 install-payload 一致）。

### 5.3 MCP 工具改造（`server/mcp/tools/`）

**① 新增 `list_skills`**（catalog 组，`server/mcp/tools/catalog.py`）：

```python
@mcp.tool()
async def list_skills(category: str | None = None) -> dict:
    """List installable skill packages in GEO's Skill library.

    Args:
        category: Optional business-category filter
            (generation / distribute / video / general).
    Returns:
        {"ok": True, "data": {"skills": [{slug, name, category, is_official,
         current_version_label, file_count, units:[...]}]}, "error": None}
    """
```

打 `GET /api/mcp/skills`（带 params），沿用 catalog.py 的 `async + anyio.to_thread` 自调用防死锁模式。

**② `install_loop_skills` 收 slug**（`server/mcp/tools/action.py`）：

```python
@mcp.tool()
async def install_loop_skills(slug: str | None = None, version: str | None = None) -> dict:
    # slug=None → "goal"（向后兼容 4 个现有 loop 配方）；version 仍废弃/忽略
    target = slug or "goal"
    raw = await _aget(f"/api/mcp/skills/{target}/install-payload")
    ...
```

- 后端 `/skills/{slug}/install-payload` 端点**已支持任意 slug**（`skill_router.py:172-202`），无需改。
- 保留 `version` 废弃参数（老 caller 不破）；文档字符串更新说明 slug 用法。

**工具总数：** 25 → **26**（新增 `list_skills`；install 是改造非新增）。同步改 `mcp_catalog/connect_router.py:MCP_TOOLS_COUNT` 及相关断言测试。

### 5.4 前端（`web/src/features/mcp/`）

- **上传** (`skill-library/UploadZone.tsx` + `skills.ts` 的 `uploadSkill`)：加 category 下拉（固定 4 值），随 FormData 上传；后端 `upload_skill` 端点加 `category: str = Form("general")` 参数透传给 `create_version`。
- **展示** (`skill-library/SkillCard.tsx`)：在 name 旁加 category 徽章（复用现有 badge 样式，官方/自定义徽章同排）。
- **类型/客户端** (`api/skills.ts`)：`Skill` interface 加 `category: string`。
- `svc.list_skills`（`skill_service.py:SkillListItem`）+ `SkillMeta` schema 加 `category` 字段，前端 user JWT list 一并带上（用于徽章）。

## 6. 数据流（装一个 skill 的完整链路）

```
Claude Code: list_skills(category="generation")
  → MCP tool → GET /api/mcp/skills?category=generation  (require_mcp_token)
  → svc.list_skills(db) 过滤 category + 每包提取 units
  → [{slug:"goal", name, category, current_version_label:"v1",
      units:["geo-goal-orchestrator","geo-article-writer","geo-article-verifier"]}, ...]

Claude Code: install_loop_skills(slug="goal")
  → MCP tool → GET /api/mcp/skills/goal/install-payload  (端点已存在,已支持任意 slug)
  → svc.get_current_bundle(db,"goal") → files[]
  → Claude Code 按 path 写入 .claude/  (多级目录自然重建)
```

## 7. API 契约汇总

| 方法 | 端点 | 鉴权 | 变更 |
|---|---|---|---|
| GET | `/api/mcp/skills?category=` | MCP token | **新增** |
| GET | `/api/mcp/skills/{slug}/install-payload` | MCP token | 不变（已支持任意 slug） |
| POST | `/api/mcp/skills/upload` | user JWT | +`category` Form 参数 |
| GET | `/api/mcp/skills`（user JWT list） | user JWT | 返回 +`category` 字段 |

MCP 工具：`list_skills(category?)` 新增；`install_loop_skills(slug?, version?)` 加 slug。

## 8. 向后兼容

- `install_loop_skills()` 不传参 = 装 goal，现有 `generation-loop.md` 等 4 个配方零改动。
- `version` 参数保留（废弃/忽略），老 caller 不报错。
- 已 seed 的生产库：迁移的 data migration 就地把 goal 设为 `generation`，无需重跑 seed。
- 前端旧调用：`uploadSkill` 加的 category 有默认值（`general`），不传也不炸。

## 9. 不在范围内（明确排除）

- **保存机制**：上传解析、DB `files` 存储、MinIO seam、读取、`build_zip`、版本管理/回滚 —— 全不动。
- **阶段级安装**：不拆包、不做「只装某阶段」（论证过：阶段耦合，拆分制造版本兼容矩阵）。
- **Claude Code → GEO 回写上传**：本期不做（install 仍只读；上传只走 web UI）。
- **二进制文件支持**：`content` 仍是 UTF-8 文本；带二进制需启用 MinIO + 改编码，非本期。
- **N+1 优化**：`list_skills` 的 N 次 files 读本期接受（低频），去规范化留 follow-up。

## 10. 测试计划（TDD）

后端（`server/tests/`，MySQL）：
1. 迁移后 `category` 列存在、默认 `general`；data migration 把已存在的官方 goal 设 `generation`。
2. `create_version(category="distribute")` 存入正确；非法 category → `ValidationError`。
3. MCP `GET /skills` 无 MCP token → 401；带 token → 200。
4. MCP `GET /skills?category=generation` 只返回生文类。
5. MCP `GET /skills` 返回 `units`：goal → 3 个单元；单文件包 → 1 个。
6. `install_loop_skills(slug="<非goal>")` 装到该包；`install_loop_skills()` 不传 → goal（向后兼容）。
7. `MCP_TOOLS_COUNT == 26` 断言。
8. seed 全新库：goal 的 category = `generation`。

前端：`pnpm --filter @geo/web typecheck` + `build` 绿（无单测框架，这是门禁）。

## 11. 纪律清单（易漏项）

- `MCP_TOOLS_COUNT` 25 → 26，改 `connect_router.py` + 断言测试。
- 迁移 down_revision 对齐当前 head（写时 grep `server/alembic/versions/` 最新文件，不写死版本号）。
- 新 MCP 工具 `async def` + `anyio.to_thread`（自调用防死锁纪律，见 catalog.py docstring）。
- MCP 端点异常走 `mcp_exception_response`。
- 表名/类名沿用现有（`skill_library_skills` 物理名，勿撞休眠 `skills` 表）。

## 12. Follow-up（非本期）

- `list_skills` N+1 / units 去规范化（量大再做）。
- `uploaded_by` 显「用户#id」→ 补用户名（承接上一 spec 的遗留）。
- category 值若需扩展（如 `research`/`ops`），改 service 校验枚举 + 前端下拉。
