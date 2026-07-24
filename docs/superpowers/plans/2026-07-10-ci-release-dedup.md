# CI 发版链路去冗余 + 退役 GitHub CI —— 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 tag 发版流水线只做 build+deploy（不重跑 main 已验证的 lint+frontend），并删除从不触发的 GitHub Actions、把文档 CI 真值改回 GitLab。

**Architecture:** 纯 CI 配置 + 文档改动。改 `.gitlab-ci.yml` 里 `backend-lint`/`frontend`/`security-audit` 三个 job 的 `rules`，令 tag 流水线不实例化它们；删 `.github/workflows/`；同步 3 处文档。MR gate 与 main gate 不动。

**Tech Stack:** GitLab CI YAML（`workflow:rules`、`rules:if`、YAML 锚点）、Markdown 文档。

**关联文档：** 设计见 `docs/superpowers/specs/2026-07-10-ci-release-dedup-design.md`。

## Global Constraints

- **只用 GitLab（`hlgit` remote）**：GitHub Actions 从不触发，删除即可，无需保留 workflow_dispatch。
- **不动 MR gate 与 main gate**：`backend-lint` + `frontend` 在 MR / 分支 push（含 main）上必须照旧跑，本次只让 tag 流水线不跑它们。
- **不碰 `backend-test`**：它当前被整体禁用（job 名前加点），属独立议题，本计划一行都不改。
- **不改 `ci/deploy.gitlab-ci.yml`**：`build:*` 与 `deploy` 无 `needs:`，靠 stage 顺序执行，前面 job 不实例化时 GitLab 自动跳过空 stage。
- **锚点重命名要全量替换**：`&skip-on-schedule` 仅被 `backend-lint` 与 `frontend` 引用（`*skip-on-schedule`）；改名 `gate-only` 时两处引用都要改，不能遗漏。
- **本机 Windows**：shell 命令优先 PowerShell；git / python 跨 shell 一致。YAML 校验用 `python -c`（本仓库有 conda 环境 `geo_xzpt`，或系统 python 皆可，仅解析不导包）。
- **提交信息结尾**加：`Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`。

---

## File Structure

| 文件 | 动作 | 责任 |
|---|---|---|
| `.gitlab-ci.yml` | Modify | 锚点 `skip-on-schedule`→`gate-only`（加 tag→never）；`backend-lint`/`frontend` 引用改名；`security-audit.rules` 加 tag→never |
| `.github/workflows/ci.yml` | Delete | 退役 GitHub 主 CI |
| `.github/workflows/coverage.yml` | Delete | 退役 GitHub 夜间覆盖率 |
| `CLAUDE.md` | Modify | 第 73 行 CI 描述改回 GitLab 真值 |
| `docs/project/08-testing.md` | Modify | `## 5. CI 门禁` 那节（86–99 行）改回 GitLab 真值 |
| `docs/project/10-handover-runbook.md` | Modify | 交接清单 22、24 行去掉 GitHub CI 指向 |

两个 Task：Task 1 = `.gitlab-ci.yml` 去冗余；Task 2 = 删 GitHub + 文档同步（同属"GitHub 退役"这一件事，改动内聚）。

---

### Task 1: `.gitlab-ci.yml` —— tag 流水线只做 build+deploy

**Files:**
- Modify: `.gitlab-ci.yml`（锚点定义约 87–91 行；`backend-lint.rules` 约 132 行；`security-audit.rules` 约 205–208 行；`frontend.rules` 约 224 行）

**Interfaces:**
- Produces: 新 YAML 锚点 `&gate-only`（规则序列：schedule→never、tag→never、on_success），供 `backend-lint`、`frontend` 引用。Task 2 不依赖本 Task。

- [ ] **Step 1: 先确认锚点引用范围（防遗漏）**

Run（PowerShell 用 `Select-String`，或用 Grep 工具）：
```
grep -n "skip-on-schedule" .gitlab-ci.yml
```
Expected：恰好 3 处——1 处定义（`.skip-on-schedule: &skip-on-schedule`）+ 2 处引用（`backend-lint` 与 `frontend` 的 `rules: *skip-on-schedule`）。若多于 3 处，逐一记录，Step 3 全部改到。

- [ ] **Step 2: 改锚点定义（加 tag→never + 重命名）**

把这段（约 87–91 行）：
```yaml
# 常规 job 共用的 rules：夜间 schedule 流水线是 security-audit 专场，其余 job 让位
.skip-on-schedule: &skip-on-schedule
  - if: '$CI_PIPELINE_SOURCE == "schedule"'
    when: never
  - when: on_success
```
替换为：
```yaml
# 常规 job 共用的 rules：只在 MR / 分支 push 时作为"合并前门禁"跑；
# 夜间 schedule（security-audit 专场）与 tag（已在 main 上绿过、发版流水线只构建部署）都让位。
.gate-only: &gate-only
  - if: '$CI_PIPELINE_SOURCE == "schedule"'
    when: never
  - if: '$CI_COMMIT_TAG'
    when: never
  - when: on_success
```

- [ ] **Step 3: 改两处引用**

`backend-lint`（约 132 行）与 `frontend`（约 224 行）里：
```yaml
  rules: *skip-on-schedule
```
均改为：
```yaml
  rules: *gate-only
```

- [ ] **Step 4: 给 `security-audit` 加 tag→never**

把 `security-audit` 的（约 205–208 行）：
```yaml
  rules:
    - if: '$CI_PIPELINE_SOURCE == "schedule"'
    - changes:
        - requirements*.txt
```
替换为：
```yaml
  rules:
    - if: '$CI_COMMIT_TAG'
      when: never
    - if: '$CI_PIPELINE_SOURCE == "schedule"'
    - changes:
        - requirements*.txt
```

- [ ] **Step 5: 校验 —— 无残留旧锚点名 + YAML 可解析**

Run：
```
grep -n "skip-on-schedule" .gitlab-ci.yml
python -c "import yaml,sys; yaml.safe_load(open('.gitlab-ci.yml',encoding='utf-8')); print('YAML OK')"
```
Expected：第一条**无任何输出**（旧名已清零）；第二条打印 `YAML OK`。
> 注：`include:` 的 `ci/deploy.gitlab-ci.yml` 是独立文件，`safe_load` 不会展开它，此处只验证主文件语法。

- [ ] **Step 6: 校验 —— 三个 job 的 rules 正确性（人工核对）**

打开 `.gitlab-ci.yml` 确认：
- `backend-lint.rules` 与 `frontend.rules` 均为 `*gate-only`。
- `security-audit.rules` 第一条是 `if: '$CI_COMMIT_TAG'` / `when: never`。
- `.backend-test`（隐藏 job）**未被改动**（仍以点开头、rules 原样）。
Expected：以上全部满足。

- [ ] **Step 7: Commit**

```bash
git add .gitlab-ci.yml
git commit -m "$(cat <<'EOF'
ci: tag pipelines skip lint+frontend (build+deploy only)

tag 落在已过 main gate 的 commit 上，重跑 lint+frontend 是纯冗余。
锚点 .skip-on-schedule → .gate-only（加 $CI_COMMIT_TAG→never），
backend-lint/frontend 引用改名，security-audit 也对 tag 让位。
MR gate 与 main gate 不变。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: 删除 GitHub Actions + 文档同步

**Files:**
- Delete: `.github/workflows/ci.yml`
- Delete: `.github/workflows/coverage.yml`
- Modify: `CLAUDE.md`（第 73 行）
- Modify: `docs/project/08-testing.md`（86–99 行）
- Modify: `docs/project/10-handover-runbook.md`（22、24 行）

**Interfaces:**
- Consumes: 无（不依赖 Task 1）。
- Produces: 无（终点，纯清理 + 文档）。

- [ ] **Step 1: 删两个 workflow 文件**

```bash
git rm .github/workflows/ci.yml .github/workflows/coverage.yml
```
Expected：两文件被移除；`.github/workflows/` 目录随之清空（Git 不追踪空目录，无需额外处理）。

- [ ] **Step 2: 改 `CLAUDE.md` 第 73 行**

把：
```
CI（`.github/workflows/ci.yml`，push 到 main 和所有 PR 触发）：**后端 ruff check / ruff format / mypy / pytest、前端 typecheck + build 都是硬门禁**；只剩前端 eslint 仍是 `continue-on-error` 的非阻塞步骤（存量 lint error 清完后再删掉 `continue-on-error` 变硬门禁）。CI 用 `mysql:8.0` service 起临时测试库 `geo_test`。
```
替换为：
```
CI（GitLab，`.gitlab-ci.yml`，MR 与分支 push 触发）：硬门禁是 **`backend-lint`（ruff check / ruff format --check / mypy）+ `frontend`（typecheck + build）**；`backend-test`（pytest，`mysql:8.0` service、库 `geo_test`）当前因一个早红的无关测试被整体禁用（job 名前加点隐藏）；`security-audit`（pip-audit）非阻塞、仅 requirements 变更或夜间 schedule 跑。**tag（`base-/server-/web-/release-`）流水线只做构建+部署、不重跑 lint+frontend**（tag 落在已过 main gate 的 commit 上；部署链在 `ci/deploy.gitlab-ci.yml`，设计见 `docs/superpowers/specs/2026-07-10-ci-release-dedup-design.md`）。GitHub Actions 已退役、团队只用 GitLab（`hlgit`）。
```

- [ ] **Step 3: 改 `docs/project/08-testing.md`（86–99 行）**

把整节（从 `## 5. CI 门禁` 那行到第 99 行 `- 配合分支保护…"红了不准 merge"。`）替换为：
```markdown
## 5. CI 门禁（GitLab，`.gitlab-ci.yml`）

MR 与分支 push（含 `main`）触发；纯文档改动不触发；新 push 自动取消旧流水线（interruptible）。

| Job | 步骤 | 门禁 |
|-----|------|------|
| `backend-lint` | `ruff check` / `ruff format --check` / `mypy` | **硬门禁** |
| `backend-test` | **pytest**（`mysql:8.0` service、库 `geo_test`、`-n 2 --dist loadfile --timeout=120`） | **当前整体禁用**（job 名前加点，见 `.gitlab-ci.yml` 注释；早红测试修好后恢复） |
| `frontend` | **typecheck**（`tsc -b`）+ **build**（`vite build`） | **硬门禁** |
| `frontend` | `pnpm audit` | 非阻塞 |
| `security-audit` | `pip-audit` | 非阻塞；仅 requirements 变更 / 夜间 schedule |

- 当前实际硬门禁 = **`backend-lint` + `frontend`**；`backend-test` 禁用期间无 pytest 兜底。
- **tag 流水线（`base-/server-/web-/release-`）只跑构建+部署**，不重跑 lint+frontend；部署链见 `ci/deploy.gitlab-ci.yml`。
- 配合分支保护把 CI 设为 `main` 的 required check，即可"红了不准 merge"。
- GitHub Actions（原 `.github/workflows/`）已退役，团队只用 GitLab（`hlgit`）。
```

- [ ] **Step 4: 改 `docs/project/10-handover-runbook.md`（22、24 行）**

把第 22 行：
```
- [ ] 仓库访问权限（GitHub）已转移/授予
```
改为：
```
- [ ] 仓库访问权限（GitLab `hlgit`；GitHub 仅历史备份）已转移/授予
```
把第 24 行：
```
- [ ] CI（`.github/workflows/ci.yml`）与分支保护设置已知悉
```
改为：
```
- [ ] CI（GitLab `.gitlab-ci.yml` + `ci/deploy.gitlab-ci.yml`）与分支保护设置已知悉
```

- [ ] **Step 5: 校验 —— 无悬挂引用、文件已删**

Run：
```
grep -rn "github/workflows" CLAUDE.md docs/ .gitlab-ci.yml
```
Expected：**无输出**（除本计划与 spec 文档本身对历史的叙述外，正文事实引用应清零；若命中 `docs/superpowers/` 下本次的 spec/plan，属预期叙述，可忽略）。

Run：
```
ls .github/workflows/ 2>/dev/null; echo "exit=$?"
```
Expected：目录不存在或为空。

- [ ] **Step 6: Commit**

```bash
git add -A CLAUDE.md docs/project/08-testing.md docs/project/10-handover-runbook.md .github
git commit -m "$(cat <<'EOF'
chore(ci): retire GitHub Actions; docs point CI to GitLab

团队只用 GitLab（hlgit），GitHub workflow 从不触发。删除
ci.yml + coverage.yml，并把 CLAUDE.md / 08-testing / 10-handover
三处 CI 描述改回 GitLab 真值（含 tag 流水线只构建部署）。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Rollout & 行为验证（合并前后）

计划内的校验只覆盖静态正确性（YAML 可解析、无悬挂引用）。**真实流水线行为在推送后确认**，正好用本分支演练全流程：

1. **推分支 + 开 MR 到 main**（本分支已含 `server/**`? 否——本次只改 CI/docs）。为触发 gate 验证，MR 里应包含对 `.gitlab-ci.yml` 的改动（属 `.code-paths`，会触发）→ 确认 MR 流水线**仍跑 `backend-lint` + `frontend`**（gate 未破）。
2. **合并到 main** → 确认 main push 流水线**仍跑 `backend-lint` + `frontend`**（兜底未破）。
3. **打演练 tag**（如 `server-1.0.3`，按 geo-release skill 流程）→ 确认该流水线**只有 `build:server` + `deploy`、无 lint/frontend**，且部署成功、健康检查通过。
4. GitHub 端确认 Actions 无新 run（本就不该有）。

**回滚**：`git revert` 对应 commit 即恢复；GitHub 两文件如需复活，从 git 历史 checkout 回来。
