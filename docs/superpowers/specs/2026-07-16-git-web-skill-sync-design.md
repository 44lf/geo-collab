# git → GEO web Skill 库 秒级自动同步（方案 A）设计

> 状态：设计已与需求方口头确认，待其 review 本文后进入 writing-plans。
> 跨两仓：`geo`（后端 + 前端，主要改动）与 `geo-claude-plugins`（插件市场，CI 一段）。

## 1. 背景与问题

同一批 `/goal` loop skill 内容目前有 **3 份拷贝**，且两条分发管线互不同步：

| # | 位置 | 谁消费 | 更新方式 |
|---|---|---|---|
| ① | `geo-claude-plugins/plugins/geo-goal/{skills,commands,README.md}` | `/plugin marketplace` 安装 | MR 合 main |
| ② | `geo/server/app/modules/loop_skills/templates/{skills,commands,README.md}` | `build_bundle()` 扫它做 seed 兜底 | 有人手改后端仓 |
| ③ | `geo` DB 表 `skill_library_versions`（当前 bundle） | `install_loop_skills` MCP 工具 / 网页下载 zip | 跑 `publish_goal_skill.py` |

① 与 ② 目录结构逐字一致，但改 ① 不会驱动 ③。结果：走 `/plugin` 的用户拿到新内容，走 `install_loop_skills` 的用户拿到旧内容——**静默 drift**。

**目标**：让 ① git main 成为 ③ DB 当前 bundle 的**唯一驱动**，合 main 后 `install_loop_skills` **秒级**拿到同一份内容；`install_loop_skills` 等原有 MCP 工具**行为零改动**继续可用。

## 2. 方案选型（已决：A 推模式）

- **A 推模式（选中）**：`geo-claude-plugins` 的 GitLab CI 在合 main 时，把 skill 文件 POST 到 GEO 新增的发布端点。新代码最少，复用已被 `publish_goal_skill.py` 验证过的发布逻辑，失败在 CI 里可见。
- B 拉模式（否）：GEO 暴露 webhook 自己去 GitLab 拉。后端代码更多、失败不易见。
- C 半自动（否）：CI 触发后端跑脚本，耦合更紧。

## 3. 端到端数据流

```
维护者改 plugins/geo-goal/** → MR → 评审 → 合 main
      │  (GitLab CI, geo-claude-plugins 仓，publish stage)
      ▼  收集 plugins/geo-goal 下除 .claude-plugin/ 外的文件，去掉 plugins/geo-goal/ 前缀
   POST https://<GEO_BASE>/api/mcp/skills/goal/publish
      │  Header: X-Skill-Publish-Token: <专用 token，非共享 MCP token>
      ▼
   GEO 发布端点：
      1) build_bundle_from_file_map(收到文件) → 算 sha
      2) 与 get_current_bundle("goal").sha 比对
         - 相同 → 200 {skipped:true}，不入库（幂等）
         - 不同 → add_version(is_admin=True, uploaded_by=None, source="git_ci") 追加版本 + 切 current + 审计
      ▼
   DB skill_library_versions 当前 bundle 变新
      ▼
   install_loop_skills / 网页下载 zip 下次拉即新内容（秒级）
```

一句话：**git main 成为 DB 当前 bundle 的唯一驱动，`/plugin` 与 `install_loop_skills` 两条分发路发同一份内容。**

## 4. 关键设计决策（含"会不会长垃圾"的回答）

1. **只追加版本，永不新建 skill**：端点按固定 slug=`goal` 调 `add_version(skill_id, ...)`，往同一个 `Skill` 追加版本。Skill 库列表**永远只有一张「/goal loop skills · 官方」卡**。（会新建 skill 的 `POST /skills/upload` 那条按名字 slugify 的路，本功能不走。）
2. **不回灌历史**：CI 只发 main **当前状态 = 一个版本**，不重放历史 commit。上线后是「每合一次 main 追一版」，与过去 git 提交数无关。
3. **查重（对齐 `publish_goal_skill.py`）**：新 bundle sha 与**当前版本** sha 相同 → 跳过、不产生新版本（CI 重跑、只改 `plugin.json`/文档的合并都不灌版本）。边界：内容 A→B→A 时第二个 A ≠ 当前 B，仍记一版（非全局内容寻址去重，属正常版本历史）。
4. **版本累积收纳**：真改动才累积版本（每版仅 5 文件、几十 KB，存储不敏感）。UI 收纳采用 **(a) 前端折叠/分页**（默认显示近 10 版，"展开更多"看全部，留全历史 + 可回滚）+ **(c) 现有手动删除**。自动"保留近 N 版软删旧版"(b) **本期不做**，真长得难看再加。
5. **覆盖语义（git 赢）**：git 与人工都能给 `goal` 追加版本、都能抢 current。人工手传一版后，**下次合 main 的 git 发布会再追一版并抢回 current**。这是"git 为唯一真源"的应有之义；「git 官方版」徽标向管理员传达此治理规则（别手动传版本盖它）。
6. **专用发布凭据**：`GEO_MCP_TOKEN` 是所有测试者共用的同一根（部署手册里明文），**绝不**用它授权发布。新增独立的 `GEO_SKILL_PUBLISH_TOKEN`，仿 `mcp_auth.py` 常数时间比对，空配置=功能关闭返回 401。
7. **② templates 定位**：保留 `templates/` 仅作"全新部署 bootstrap 种子"（`seed_skill_library` 首次建库用），**停止手改**；git 插件仓成为唯一活跃真源，`templates/` 若与 git 有出入，下次合 main 的 CI publish 会自动覆盖 DB current（自愈）。两仓 README 各加一句说明。

## 5. 后端改动（geo 仓，纯新增 + 一处小迁移）

### 5.1 版本溯源列（迁移 0060）
`skill_library_versions` 加两列，用于前端徽标与溯源：
- `source` `String(16)` NOT NULL default `"web_upload"`，取值 `web_upload` | `git_ci` | `seed`。
- `source_ref` `String(64)` NULL：git 发布时存 commit sha 或 `plugin.json` 版本（如 `v1.2.0`），其余为空。
- 既有行 backfill 为 `web_upload`（best-effort：`uploaded_by IS NULL` 的官方版本可置 `seed`，非强需求）。

### 5.2 模型 / 服务层
- `loop_skills/models.py`：`SkillVersion` 加 `source` / `source_ref` 映射。
- `loop_skills/skill_service.py`：
  - `add_version(...)` 增参 `source: str = "web_upload"`、`source_ref: str | None = None`，写入 `_append_version`。
  - `VersionItem` dataclass + `list_versions` 带出 `source` / `source_ref`。
  - 新增 `publish_from_files(session, *, slug, entries, source_ref)`：把 `publish_goal_skill.py::main()` 逻辑服务化——`build_bundle_from_file_map` → 比 current sha → 相同返回 `(skill, None)`（skipped）→ 不同 `add_version(is_admin=True, uploaded_by=None, source="git_ci", source_ref=...)`。skill 不存在抛 `ValidationError`（不建 skill）。
  - `publish_goal_skill.py` 改为调用 `publish_from_files`（保留 CLI 手动入口，逻辑同源、不重复）。
- `loop_skills/schemas.py`：`SkillVersionMeta` 加 `source` / `source_ref`。

### 5.3 鉴权
- `core/config.py`：settings 加 `skill_publish_token`（读 env `GEO_SKILL_PUBLISH_TOKEN`）。
- 新增 `require_publish_token`（放 `core/mcp_auth.py` 或同目录小模块）：读 header `X-Skill-Publish-Token`，仿 `verify_mcp_token` 常数时间比对；未配置或不匹配 → 401。

### 5.4 端点
- `loop_skills/skill_router.py` 新增 `skills_publish_router = APIRouter(dependencies=[Depends(require_publish_token)])`：
  - `POST /skills/{slug}/publish`，body 同 `/upload`（multipart 文件数组或单 zip），可选 form 字段 `source_ref`。
  - 调 `svc.publish_from_files(...)`；返回 `{ok, skipped: bool, skill_id, slug, version_label|null, bundle_sha256}`。
  - 复用现有 `upload.parse_upload` + `validate_file_map`（≤5MB / ≤200 文件 / 需 SKILL.md / UTF-8）与 `add_audit_entry`（`action="skill.publish"`）。
- `main.py`：`app.include_router(skills_publish_router, prefix="/api/mcp", tags=["skills-publish"])`。

**不动**：现有 `install_loop_skills`、`/upload`、`/versions`、`/set-current`、`download.zip`、`install-payload` 全部行为不变。

## 6. 前端改动（geo/web，小）

- `api/skills.ts`：`SkillVersion` 接口加 `source: string`、`source_ref: string | null`。
- `skill-library/VersionHistory.tsx`：
  - 版本行渲染徽标：`source === "git_ci"` → 「git 官方版」徽标（复用 `badgeStyle`，加 GitBranch/ShieldCheck 图标）；有 `source_ref` 则附显（如 `v1.2.0` / `commit abc1234`）。回落：`source === "seed"` → 「系统内置」。
  - 收纳：版本列表默认显示近 10 行，超出折叠「展开全部（N）」。
- 其余（`SkillCard` 的 skill 级「官方」徽标、上传/回滚/删除）不变。

## 7. 插件仓改动（geo-claude-plugins，CI 一段 + README）

- `.gitlab-ci.yml` 加 `publish` stage（在现有 `validate` 之后）：
  ```yaml
  stages: [validate, publish]
  publish_skill:
    stage: publish
    rules:
      - if: '$CI_COMMIT_BRANCH == "main"'
        when: manual   # 首轮灰度手动触发；稳定后改为 on_success 自动
    script:
      - >-
        cd plugins/geo-goal &&
        FILES=$(find README.md commands skills -type f) &&
        ARGS=$(for f in $FILES; do printf ' -F files=@%s;filename=%s' "$f" "$f"; done) &&
        curl -fsS -X POST "$GEO_BASE/api/mcp/skills/goal/publish"
          -H "X-Skill-Publish-Token: $GEO_SKILL_PUBLISH_TOKEN"
          -F "source_ref=$CI_COMMIT_SHORT_SHA"
          $ARGS
  ```
  （`-f` 让非 2xx 时 job 变红；发的文件集 = `README.md` + `commands/**` + `skills/**`，**不含** `.claude-plugin/` / `plugin.json`。相对名即去前缀后的 bundle 路径。）
- GitLab CI/CD 变量（masked）：`GEO_BASE`、`GEO_SKILL_PUBLISH_TOKEN`。
- 两仓 README 各加一句：skill 正本在 `geo-claude-plugins/plugins/geo-goal/`，改动只走那条 MR；GEO 后端 `templates/` 不再手改。

## 8. 测试

- **后端单测**（`server/tests`）：
  1. 无 / 错 `X-Skill-Publish-Token` → 401。
  2. 合法 token + 内容与 current 相同 → `skipped:true`，版本数不增。
  3. 改一字节 → 新版本 + current 切到它 + `source="git_ci"` + `source_ref` 落库。
  4. 缺 SKILL.md / 超 5MB → 400。
  5. slug 不存在（未 seed）→ 400。
- **前端**：`source==="git_ci"` 渲染「git 官方版」徽标；版本超 10 行折叠。
- **联调（staging）**：手动触发一次 CI publish → `GET install-payload` 返回的 `bundle_sha256` == 新内容 sha，`list_skills` 的 `current_version_label` 前进一格。
- **回滚**：`POST /skills/{id}/set-current` 切回旧版本（现成，零新增）。

## 9. 上线与灰度

1. GEO 后端上线迁移 0060 + 端点 + 前端；配 `GEO_SKILL_PUBLISH_TOKEN`。
2. 插件仓配 CI 变量，`publish` job 先 `when: manual`。
3. 手动触发跑通一轮、按第 8 节联调打勾。
4. 稳定后把 `when: manual` 改为 `on_success`（合 main 自动发）。

## 10. 待需求方确认（open items）

1. **Runner 可达性**：`geo-claude-plugins` 的 GitLab runner 能否访问 GEO？`GEO_BASE` 用公网 `geo.huanchanghuyu.com` 还是某内网地址？（A 方案前提，需事实确认。）
2. **自动 vs 手动闸门**：长期是合 main 自动发（`on_success`），还是保留 `when: manual` 人工确认？（默认：先 manual 灰度 → 转 auto。）
3. **收纳档位**：确认 (a) 前端折叠够用，暂不做 (b) 自动保留近 N 版？
4. **覆盖语义**：确认「git 赢、人工手传版会被下次合 main 覆盖」这一语义？（默认认可。）

## 11. 非目标（YAGNI）

- 不做 B/C 方案。
- 不做 git ← web 反向回写。
- 不做自动版本保留/裁剪 (b)（留待需要）。
- 不消灭 ② templates（保留作 bootstrap 种子）。
- 不改任何现有端点/工具的行为。
