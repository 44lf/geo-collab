# 部署迁移锁安全 —— deploy.sh 顺序缺陷修复

- 日期：2026-07-15
- 分支：`fix/deploy-migrate-lock-safety`
- 触发事故：`release-1.0.8` 的 deploy job（pipeline 622 / job 1527）在 `alembic upgrade head` 卡死近 1 小时，占死唯一 runner，濒临自动重试雪崩。

## 背景与根因

服务器端部署脚本 `deploy/deploy.sh` 的执行顺序是**先迁移、后 `up -d`**：

```
docker compose run --rm migrate      # alembic upgrade head —— 此时旧 app/worker 仍在运行
docker compose up -d --remove-orphans
```

迁移运行时，上一版本的 `app` / `worker` 容器还活着、还连着 MySQL、持有 `articles` 表的**元数据锁（MDL）**。`0062_adversarial_review` 的 `ALTER TABLE articles ADD COLUMN` 拿不到 MDL 只能等，而 MySQL `lock_wait_timeout` 默认 ≈ 31536000s（一年），实际就是永久挂起。`worker` 因历史上的僵死线程（见 `worker-wedged` 事故）尤其容易钉住长事务。

事故当次是靠一次旁路重启（`up -d --remove-orphans`）恰好释放旧连接、迁移趁机跑完而侥幸自愈——不可依赖。

## 目标

1. 迁移能**确定性地**拿到 `articles` 的 MDL，正常跑完。
2. 万一仍被别的连接钉住，**快速失败**（loud red）而非静默挂一年 + CI 自动重试雪崩。
3. 尽量**零用户停服**。
4. 修复能**真正到达 prod** 并被发版流水线验证（prod 的 `/root/geo-collab/` 非 git 管理，CI 原本只推镜像、不推部署脚本 —— 存在"仓库≠prod"交付缺口）。

## 设计决策（已与用户确认）

- **停服容忍度**：尽量零停服 —— 迁移前只停 `worker`（已知长事务元凶，停它无用户影响），`app` 保持在线；给迁移加 `lock_wait_timeout≈60s` 兜底。不停 `app`（0062/0063 均为加列/建表、向后兼容，旧 app 可短暂跑在旧 schema 上）。
- **交付方式**：CI 自动推送 —— deploy job 在跑远端 `deploy.sh` **之前**先 `scp` 仓库版 `deploy/deploy.sh` 到 prod。永久堵住"仓库≠prod"漂移，且让发版流水线真正验证的是仓库脚本。

## 改动清单（4 处）

### 1. `server/alembic/env.py` —— 迁移连接的会话级锁等待兜底
`run_migrations_online()` 拿到 connection 后，若环境变量 `GEO_MIGRATE_LOCK_WAIT_TIMEOUT` 存在且方言为 MySQL，执行 `SET SESSION lock_wait_timeout = <int>`。

- 用 `int()` 强转防注入；变量不设 = 行为完全不变（测试 / 本地 / 进程内迁移零影响）。
- 交付：`env.py` 在 server 镜像里，随新镜像**自动上线**，无需手动同步。

### 2. `deploy/deploy.sh` —— 迁移前停 worker + 兜底超时 + 失败恢复
迁移块（仅 `$deploy_server`）改为：

```bash
docker compose -f "$COMPOSE_FILE" stop worker || true        # 释放 articles 锁；app 在线
if ! docker compose -f "$COMPOSE_FILE" run --rm \
       -e GEO_MIGRATE_LOCK_WAIT_TIMEOUT=60 migrate; then
  echo "❌ 迁移失败，恢复 worker 后退出"
  docker compose -f "$COMPOSE_FILE" up -d worker || true      # 失败也把 worker 拉回，不留残局
  exit 1
fi
```

- 超时通过 `docker compose run -e` 注入，**不改 compose 文件**（避免覆盖 prod 手改的 compose）。
- 随后的 `up -d --remove-orphans` 会把停掉的 worker 重新拉起（新镜像）。
- 同时把 prod 现存的 **nginx-reload 块**（后端重启后 `nginx -s reload` 重解析 DNS 实现零停机）上游进仓库版 deploy.sh，消除该处漂移。

### 3. `ci/deploy.gitlab-ci.yml` —— deploy job 先投递脚本再执行
在 `deploy` job 跑远端 `bash deploy.sh` 之前，加一步：

```bash
scp "$CI_PROJECT_DIR/deploy/deploy.sh" "deploy:$DEPLOY_PATH/deploy.sh"
```

- 复用 job 里已配置的 `deploy` SSH host（before_script 已装 openssh-clients，含 scp）。
- 本次发版 job 会先 scp 新脚本、再执行 —— 同一轮即生效。

### 4. compose：不动
超时走 `run -e`，`docker-compose.prod.yml` 无需改动。

## 行为对比

| | 修复前 | 修复后 |
|---|---|---|
| 迁移拿锁 | 旧 app+worker 持锁 → ALTER 挂 ~1 年 | worker 先停、app 在线 → 正常拿锁跑完 |
| 极端情况（app 仍持锁） | 静默挂起 + CI 超时自动重试雪崩 | 60s 后报 1205 快速失败、job 立刻红、worker 被拉回 |
| 用户停服 | 无（但迁移永不完成） | 无（仅 worker 短暂停、发布任务暂缓） |
| 脚本交付 | 仓库改动不到 prod | CI 每次发版自动 scp、仓库=prod |

## 验证方式（正常发版全流程）

合并 MR → 打 `release-1.0.9` → 观察 tag 流水线的 deploy job：
1. `scp deploy.sh` 成功；
2. `stop worker` → `migrate` 在**几秒内**跑完（不再 hang）；
3. `up -d` → 健康检查通过、job 绿；
4. prod：`alembic_version` 到新 head、app/worker `restarts=0`、日志 200 OK。

## 风险与回滚

- 若 `scp` 失败：deploy job 在投递步早失败（不会用半新半旧脚本跑），可重试。
- 若迁移 1205 快速失败：说明仍有连接钉锁 —— 按提示排查（多为 worker 未停干净或 app 长事务），修掉再重跑 tag。
- 回滚：本改动纯增量（env.py 门控默认关、deploy.sh 顺序调整、CI 加一步 scp），revert commit 即恢复旧行为。
