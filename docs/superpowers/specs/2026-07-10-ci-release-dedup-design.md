# CI 发版链路去冗余 + 退役 GitHub CI —— 设计

- 日期：2026-07-10
- 分支：`feat/ci-release-dedup`
- 关联：`docs/superpowers/specs/2026-07-08-tag-driven-deploy-design.md`（tag 驱动部署原设计）、`.claude/skills/geo-release/SKILL.md`（发版 skill）

## 背景与动机

当前 CI 现状（基于 `.gitlab-ci.yml`、`ci/deploy.gitlab-ci.yml`、`.github/workflows/*` 的实际 rules）：

1. **同一份代码的 `backend-lint` + `frontend` 门禁被跑了 3 遍**：MR 流水线 → 合并进 main 的 push 流水线 → 打 tag 的发版流水线。其中 **tag 那一遍是纯重复**——release tag 永远打在"已经过完 main gate 的那个 main commit"上，树与 main gate 时完全一致。
2. **仓库挂着两套 CI，但团队只用 GitLab（`hlgit`）**。GitHub Actions（`.github/workflows/ci.yml` + `coverage.yml`）只在 push 到 GitHub / 在 GitHub 开 PR 时触发，而这在当前工作流里从不发生——是永不触发的死配置，还让"CI 门禁在哪"产生歧义（多处文档仍指向 GitHub）。

### 为什么 tag 那遍是冗余、而 main 那遍不是

- GitLab 的 MR 流水线**默认跑在源分支 HEAD 上，不是 merge 结果**（除非项目开启 "Merged results pipelines"，本仓库 YAML 未体现、按未开处理）。因此 "MR 绿" ≠ "合进当前 main 后的 merge commit 也绿"——main 可能在开 MR 后动过。**main-push 那遍 gate 能抓这种合并漂移，是有效的第二道线，不能砍。**
- release tag 落在那个已被 main gate 验证过的 commit 上，再跑一遍 lint+frontend 才是真正的重复劳动。

## 目标

- ① 砍掉 tag 流水线里对 main 已验证内容的重复 `backend-lint` + `frontend`。
- ② 退役从不触发的 GitHub Actions，让 GitLab 成为唯一 CI 真值，并同步会说谎的文档引用。

## 非目标（本次明确不做）

- **不修复 / 不恢复 `backend-test`**：它当前因一个早红的无关测试（`test_old_write_endpoints_are_removed`，405/404 断言不符）被整体禁用（`.gitlab-ci.yml` 里 job 名前加点隐藏）。这是已知的、被接受的现状，属独立议题，不在本次范围。
- **不动 MR gate、main gate**：两道门禁一次不减，安全垫不变。
- **不加 runner / 不改并发**：单 runner 串行是根本瓶颈，属基础设施投入，本次不涉及。
- **不采用激进方案 B**（连 main gate 一起砍、全链路只留 MR 一道）：会丢掉抓合并漂移与直推 main 的唯一自动检查，叠加 backend-test 已禁用等于裸奔，评估后否决。

## 设计

### 改动 ①：tag 流水线只做 build+deploy（`.gitlab-ci.yml`）

把 `backend-lint`、`frontend` 共享的 rules 锚点从"仅跳过 schedule"扩成"**跳过 schedule + tag**"，并给 `security-audit` 也加 tag→never。改完后 tag 流水线里不再实例化这三个 job，纯粹只剩 `build:*` + `deploy`。

```yaml
# 锚点重命名：.skip-on-schedule → .gate-only
# 语义 = 只在 MR / 分支 push 时作为"合并前门禁"跑；schedule 与 tag 都让位。
.gate-only: &gate-only
  - if: '$CI_PIPELINE_SOURCE == "schedule"'
    when: never
  - if: '$CI_COMMIT_TAG'          # 新增：tag 已在 main 上绿过，不重跑门禁
    when: never
  - when: on_success
```

具体改动点：

- `backend-lint.rules`：`*skip-on-schedule` → `*gate-only`
- `frontend.rules`：`*skip-on-schedule` → `*gate-only`
- `security-audit.rules`：在现有规则开头加一条 `- if: '$CI_COMMIT_TAG'` / `when: never`，使 tag 流水线彻底不含它。
- **不改** `ci/deploy.gitlab-ci.yml`：`build:base/server/web` 与 `deploy` 没有 `needs:`，靠 stage 顺序执行；前面的 lint/frontend job 不实例化时，GitLab 自动跳过空 stage，build/deploy 照常从 build 阶段起跑。

> 说明：kaniko 的 `build:web` 是在 `Dockerfile.nginx` 里**真正 `vite build` 出生产镜像**，删掉的只是 CI `frontend` job 里那次**重复的 typecheck+build 检查**；production 镜像仍每次现构，不是复用旧产物。

### 改动 ②：删除 GitHub Actions + 文档同步

- 删除 `.github/workflows/ci.yml` 与 `.github/workflows/coverage.yml`（`.github/workflows/` 目录清空）。
- 同步 3 处指向 GitHub CI 的文档，改成 GitLab 真值（GitLab 硬门禁 = `backend-lint` + `frontend`；tag 驱动构建部署在 `ci/deploy.gitlab-ci.yml`）：
  - `CLAUDE.md`（描述 CI 门禁那段，约第 73 行）
  - `docs/project/08-testing.md`（`## 5. CI 门禁` 那节，约第 86 行）
  - `docs/project/10-handover-runbook.md`（交接清单里 GitHub 仓库权限 / `.github/workflows/ci.yml` 两项，约第 22、24 行）

## 净效果（结合 geo-release skill 的发版要求）

geo-release 前提：server/web/release 发版是"零提交、只在 main 上打 tag"；只有改 base 才需要提交 `deploy/VERSION` 走一次 MR。

| 场景 | 改前 lint+frontend 次数 | 改后 |
|---|---|---|
| 不动 base 发版（合并 MR 1 + main 1 + tag 1） | 3 | **2**（打 tag 那步 0） |
| 动 base 发版（额外 VERSION bump 的 MR + main + base tag + app tag） | 6 | **4**（两个 tag 都 0） |

MR gate 与 main gate 次数不变，砍掉的全是 tag 层的纯重复。

## 风险与回滚

- **唯一残留风险**：有人对"没走过 main gate 的 commit"打 tag（例如直推 main 后立刻打 tag）→ 无 pre-gate 直接 build+deploy。
  - **缓解**：main 受保护 + geo-release skill 强制 `on main + clean + pulled`，tag 只落在已绿 main commit 上；这条运营约束不随本次改动改变。
- **回滚**：纯 YAML / 文件改动，`git revert` 单个 commit 即恢复原行为；GitHub 两个 workflow 如需复活，从 git 历史 checkout 回来即可。

## 验证方式

1. 开一个碰 `server/**` 的小 MR → 确认 MR 流水线仍跑 `backend-lint` + `frontend`（gate 未破）。
2. 合并到 main → 确认 main 流水线仍跑二者（兜底未破）。
3. 打一个演练 tag（如 `server-1.0.3`）→ 确认该流水线**只有 `build:server` + `deploy`、无 lint/frontend**，且部署成功、健康检查通过。
4. GitHub 端确认 Actions 不再有新 run（本就不该有）。

> 本分支（`feat/ci-release-dedup`）本身即可当作"分支 → MR → 合并 → 发版"全流程的一次真实演练：MR/main 上验证 gate 仍在，随后打 tag 验证发版流水线已瘦身。
