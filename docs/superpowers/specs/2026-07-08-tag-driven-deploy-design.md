# Tag 驱动自动部署设计（GEO Collab）

> 日期：2026-07-08 · 状态：设计定稿待实现
> 关联参考：`.gitlab-ci-hr.yml`（HrSystem 的 tag→kaniko→Harbor→SSH 参考骨架）、`deploy/`（现有手动发布脚本 build.sh/deploy.sh）、`.gitlab-ci.yml`（GEO 现有 CI）

## 1. 目标与背景

把 GEO 的部署从「本地 `build.sh` 出 tar.gz → 手动 scp → 服务器 `deploy.sh`」升级为 **打 tag 即全自动构建并上线**。复用两套已在本环境验证过的东西：GEO 自己的 `deploy.sh` 部署逻辑，HrSystem 的 tag→kaniko→Harbor→SSH CI 骨架。

四条硬约束（用户确认）：

1. **内网 Harbor 不可达外网服务器**：`harbor.5518game.com:6443` 是内网地址，生产服务器在公网、连不到它。必须有「内网→外网」的镜像搬运机制，且不把 Harbor 对公网暴露。
2. **零硬编码**：所有 host / 账号 / 路径都用 CI 变量占位，后续在 GitLab UI（Settings → CI/CD → Variables）配置。
3. **镜像瘦身**：慢变的基础栈（无头浏览器等）与快变的代码解耦，改代码只重建薄层。
4. **只更新改动**：改动的部分才重建 / 重传 / 重启，不动的部分不碰。

### 1.1 部署形态：新老并行，互不干扰（最高优先级）

**用户决策**：本方案（A 方案）作为一套**全新的并行栈**上线，**与线上正在运行的老栈完全隔离、可长期共存**；验证通过后再考虑切换，老栈全程不动。

**线上老栈现状（经 2026-07-08 只读摸底确认，勿动）**：

- 生产机 = **`47.115.134.13`**（root SSH，hostname `iZwz9hh3ruzf9cr60oxxhoZ`；注意 `.mcp.json` 里 ssh MCP 的 `default` 连接实际指向 `8.219.98.35` 是**另一台无关机器**，交互摸底要用本地 `ssh -i ~/.ssh/id_rsa root@47.115.134.13`）。
- 老栈部署在 **`/root/geo`**，compose 项目名 `geo`，用 repo 根 `docker-compose.yml`+`docker-compose.override.yml`；**部署方式是 git-push-to-server：本地 `deploy.sh` 做 `git push deploy main:main` → 服务器裸仓库 hook 触发 `docker compose up --build -d`，在服务器上现构建**。
- 老栈容器：`geo-app-1` / `geo-worker-1`（各 3.26GB 重镜像）/ `geo-nginx-1`（**占宿主 80**）/ `geo-mysql-1`（127.0.0.1:3306）/ `geo-minio-1`（127.0.0.1:9000-9001）/ `geo-dailyhot-api-1`，外加独立 `geo-https-nginx`（443 TLS 前置）。
- 磁盘：单盘 `/`（`/dev/vda3` 99G，剩 **24G**，**无 `/data` 盘**）；内存 7.4G。

**新栈非冲突硬约束**（实现时逐条满足）：

| 维度 | 老栈（不碰） | 新栈（本方案，全用变量占位） |
|---|---|---|
| compose 项目名 | `geo` | `geo-ci`（`COMPOSE_PROJECT_NAME`，容器名 `geo-ci-*` 不撞 `geo-*`） |
| 部署目录 | `/root/geo` | 独立目录如 `/root/geo-collab`（`$DEPLOY_PATH`；本机无 `/data`，勿写根盘外） |
| 对外端口 | nginx 占 80、TLS 占 443 | 新 nginx 绑**空闲高端口**如 `$HTTP_PORT=8081`，不碰 80/443 |
| 本地 registry | 无 | `127.0.0.1:5000`（空闲，本方案新增，仅回环） |
| 数据卷 | `geo_*` 卷 | `geo-ci_*` 独立卷（新库从空起，与老库物理隔离） |
| 镜像名 | `geo-app`/`geo-worker`/`geo-nginx` | `geo-collab-server`/`geo-collab-web`/`geo-collab-base`（不撞） |

- **重大操作动手前必须人工确认**：任何 `docker compose up/down/restart`、新建目录/容器、动 `.env`。只读探查（`docker ps` / `compose ls` / `df -h`）可先做。
- **绝不** `down -v`、绝不删任何 `*_data` 卷或数据目录；操作前先看清是老栈还是新栈的资源。
- 磁盘只剩 24G：新栈 base 镜像 ~2GB + registry blob + 新 mysql/minio 数据都算进去，上线前 `df -h` 复核，别把根盘写满拖垮老栈。

## 2. 关键前提事实

- **GitLab runner 是「桥」**：runner 在内网能连 Harbor，同时又必须 SSH 到外网服务器才能部署 → 天然横跨两侧，是搬运镜像的唯一通道。
- **runner 非特权、无 docker daemon**：HrSystem 已证明本 runner 只能跑 kaniko（用户态构建）+ daemonless 工具；不能 `docker build` / `docker push`。因此搬运用 **skopeo**（无守护进程、blob 级增量）。
- **两个应用镜像**：`geo-collab-server`（app + worker 共用）和 `geo-collab-web`（nginx 静态 + `/api` 反代 + noVNC）。基础设施 mysql / minio / dailyhot 已是独立容器，不进应用镜像。
- **生产始终有 nginx 在前**：`docker-compose.prod.yml` 恒含 nginx serve 前端 + 反代，app 不 serve SPA。

## 3. 决策汇总

| 维度 | 决策 |
|---|---|
| 镜像仓库 | 内网 Harbor 新建 **`geo` 项目** + 独立 robot 机器人账号 |
| 内网→外网搬运 | **skopeo 经 SSH 隔离隧道，增量推入外网服务器本地 `registry:2`（`127.0.0.1:5000`）** |
| 部署触发 | **全自动**：tag → 构建 → 搬运 → 上线一气到底 |
| tag 方案 | 分端：`base-<ver>` / `server-<ver>` / `web-<ver>` / `release-<ver>`(=all) |
| 镜像瘦身 | **基础镜像分层**：`geo-collab-base`（重、慢变）+ `geo-collab-server`（薄、代码）；server 镜像去掉前端 dist + node 构建 stage |
| kaniko 基础镜像源 | build-arg `BASE_REGISTRY`，CI 传 `public.ecr.aws/docker/library`（runner 已可达），本地默认 `docker.io/library` |
| 迁移 | 复用 `deploy.sh` 的一次性 `migrate` 服务，`up -d` 之前单点执行 |

## 4. 镜像分层架构（约束 3、4）

### 4.1 `Dockerfile.base` → `geo-collab-base`

包含**慢变重活**：`python:3.12-slim` + apt 浏览器全家桶（xvfb / x11vnc / websockify / novnc / chromium / fonts-noto-cjk + 运行时库）+ `pip install -r requirements.txt` + `playwright install chromium`。

- `FROM ${BASE_REGISTRY:-docker.io/library}/python:3.12-slim`（build-arg 参数化，解决 runner 连不上 Docker Hub）。
- **仅在 `requirements.txt` 或浏览器栈变化时重建**，由 `base-<ver>` tag 触发。
- 推送 `${HARBOR}/${PROJECT}/geo-collab-base:<ver>` + `:latest`。

### 4.2 `Dockerfile`（server）→ `geo-collab-server`

只剩**快变的代码层**：

```dockerfile
ARG BASE_IMAGE=geo-collab-base:latest
FROM ${BASE_IMAGE}
WORKDIR /app
COPY . .
EXPOSE 8000
CMD ["sh","-c","uvicorn server.app.main:app --host 0.0.0.0 --port 8000"]
```

- CI 传 `--build-arg BASE_IMAGE=${HARBOR}/${PROJECT}/geo-collab-base:<baseVer>`；本地 `build.sh` 传本地 base tag。
- **删除**原 Dockerfile 里的 node `web-build` stage 与 `COPY --from=web-build web/dist`：生产由 nginx serve 前端，app 不 serve SPA。→ server 镜像不再因改前端重建、不装 node，改代码只重建 `COPY . .` 薄层（几 MB）。
- 迁移不再写死进 CMD（现状 `alembic upgrade head && uvicorn`）；迁移统一交给 `deploy.sh` 的 `migrate` 服务单点执行（与现有 prod compose 一致）。

> **与已有 `Dockerfile.app` 的关系**：仓库已有一份 `Dockerfile.app`（精瘦后端、无浏览器，注释写明「worker 仍用重型 Dockerfile」）——那是「拆 app/worker 两个镜像」的思路，但线上老栈 compose 里 app/worker 仍都 `build: .`（未接上线）。本新栈选**基础镜像分层**（base + code）而非两镜像拆分：app 与 worker 共用 `geo-collab-server`（共享 base 层 → skopeo 只传一次），比两镜像各自传更省。`Dockerfile.app` 的 slim 思路作为「app 是否需要更瘦的无浏览器变体」的备选，在实现计划阶段最终定；默认走 base 分层。

### 4.3 `Dockerfile.nginx` → `geo-collab-web`（基本不变）

- 仅把三处 `FROM` 加 `${BASE_REGISTRY:-docker.io/library}/` 前缀，保证 kaniko 可拉基础镜像。
- 前端改动只重建此镜像（`web-<ver>` tag），不触达 server。

### 4.4 为什么不把浏览器拆成独立 sidecar

worker 内 Playwright 直接拉起本地 Chromium，且 noVNC / x11vnc 必须与 Chromium 共用同一个 X server（远程人工接管）。拆成独立容器需改成 CDP remote-connect + 跨容器共享 X，改造面过大、收益低。因此「分离」用**分层**实现（慢层 base / 快层 code），而非拆服务。app 与 worker 继续共用 `geo-collab-server`（共享 base 层，浏览器层只随镜像传一次、两者复用）。

## 5. 内网→外网搬运（约束 1、4）

**skopeo 经 SSH 隔离隧道，把镜像 blob 级增量推入外网服务器上的本地 registry。**

### 5.1 服务器端新增 `registry:2`（在**新栈** compose 里，不碰老栈）

在**新栈**自己的 compose（`$DEPLOY_PATH/docker-compose.prod.yml`，项目 `geo-ci`）里增一个服务：

```yaml
  registry:
    image: registry:2
    restart: unless-stopped
    ports:
      - "127.0.0.1:5000:5000"      # 仅回环，不对公网暴露
    volumes:
      - ./data/registry:/var/lib/registry
```

- 体积 ~30MB；数据落 `./data/registry`，blob 常驻 → 跨次部署增量复用。
- 绑 `127.0.0.1` → 公网不可见；docker 对 `127.0.0.1:5000` 默认免 TLS，无需改 daemon 配置。

### 5.2 CI bridge 阶段（deploy job 内，运行在 skopeo 镜像里）

1. 铺 SSH key（同 HrSystem `.deploy` before_script：写 `DEPLOY_SSH_KEY` → `~/.ssh` → `StrictHostKeyChecking accept-new`）。
2. 开隧道：`ssh -fN -L 5000:127.0.0.1:5000 $DEPLOY_SSH_USER@$DEPLOY_SSH_HOST`。
3. 增量搬运（每个待发端一条）：
   ```
   skopeo copy --src-authfile $DOCKER_AUTH_CONFIG --dest-tls-verify=false \
     docker://$HARBOR_REGISTRY/$HARBOR_PROJECT/geo-collab-server:<ver> \
     docker://localhost:5000/$HARBOR_PROJECT/geo-collab-server:<ver>
   ```
   skopeo 只推目标 registry 缺失的 blob：base 层首次传后常驻，**改代码只过顶层几 MB**。skopeo 会在缺 base 层时自动从 Harbor 拉齐，无需单独搬 base。

> skopeo 镜像本身需 runner 可达；若 quay.io 不可达，一次性 seed 进 Harbor geo 项目后从 Harbor 拉。

### 5.3 服务器端拉取（`deploy.sh` registry 模式）

`deploy.sh` 新增 `IMAGE_SOURCE=registry` 分支：把原 `docker load < tar.gz` 换成
`docker pull $LOCAL_REGISTRY/$HARBOR_PROJECT/geo-collab-<img>:<ver>` + `docker tag` 成裸名 `geo-collab-<img>:<ver>`（compose 保持 registry 无关）。**其余逻辑——`.env` 预检、一次性 `migrate`、`versions.env` 记账、`up -d --remove-orphans`——原样复用，单一真值源。**

## 6. 流水线与 tag → 端映射

```
打 tag → GitLab pipeline：
  stage test    复用现有 backend-lint + 前端检查（门禁；后端 pytest 恢复后纳入）
  stage build   kaniko 构建 → 推内网 Harbor geo 项目
  stage bridge  skopeo 经 SSH 隧道增量推入外网服务器 127.0.0.1:5000
  stage deploy  SSH → deploy.sh(registry 模式) → migrate → up -d 变动服务 → 健康检查 → 飞书
```

| tag | 构建 | 部署端 | 迁移 |
|---|---|---|---|
| `base-<ver>` | geo-collab-base | 不部署（仅推 Harbor 供 server FROM） | — |
| `server-<ver>` | geo-collab-server | app + worker | ✅ |
| `web-<ver>` | geo-collab-web | nginx | ❌ |
| `release-<ver>` | server + web | all | ✅ |

- 版本号取自 tag 后缀（`server-1.2.0` → `1.2.0`）；base 版本 pin 在 `deploy/VERSION` 的 `BASE=`，server 构建以此传 `BASE_IMAGE`。
- 其它前缀 tag / 分支裸推 → 不建部署流水线。
- 全自动：build/bridge/deploy 均 `on_success` 自动流转；deploy `interruptible:false`。

## 7. CI 变量（约束 2 · 全部 GitLab UI 配置）

| 变量 | 用途 | 类型 |
|---|---|---|
| `HARBOR_REGISTRY` | 如 `harbor.5518game.com:6443` | 普通 |
| `HARBOR_PROJECT` | 如 `geo` | 普通 |
| `DOCKER_AUTH_CONFIG` | kaniko / skopeo 拉推 Harbor 鉴权 JSON | Masked |
| `HARBOR_USER` / `HARBOR_TOKEN` | geo robot 账号（预留，如脚本内 login 需要） | Masked |
| `DEPLOY_SSH_HOST` / `DEPLOY_SSH_USER` | 外网生产服务器 | 普通 |
| `DEPLOY_SSH_KEY` | CI 免密私钥 | File / Masked |
| `DEPLOY_PATH` | 服务器部署目录，如 `/data/geo-collab` | 普通 |
| `LOCAL_REGISTRY` | 服务器本地 registry，如 `127.0.0.1:5000` | 普通 |
| `BASE_REGISTRY` | kaniko 基础镜像源，如 `public.ecr.aws/docker/library` | 普通 |

## 8. CI 接线

- GEO `.gitlab-ci.yml` 的 `workflow:rules` 增一条：`if: '$CI_COMMIT_TAG =~ /^(base|server|web|release)-/'`。
- 新增 `stages: [build, deploy]`（在现有 lint/test/frontend 之后）。
- build / bridge / deploy 三段用 `include: local: ci/deploy.gitlab-ci.yml` 单独成文件，主 CI 文件保持清爽。
- 复用 HrSystem 的 `.kaniko` 模板（`DOCKER_AUTH_CONFIG` → `/kaniko/.docker/config.json`）。

## 9. 全自动的安全网

1. **迁移失败即熔断**：`migrate` 是 `up -d` 之前的独立一次性服务，`set -euo pipefail` 下非零退出即中止部署，旧容器继续跑，坏迁移不带崩线上。
2. **部署后健康检查**：`ssh` 内 `curl` 本机 `HTTP_PORT`（N 次重试）；不过 → 飞书告警（复用 `GEO_FEISHU_WEBHOOK_URL`）+ job 判红。
3. **确定性回滚**：deploy 永远 pin 显式 `<ver>`（不用 latest）；Harbor + 服务器本地 registry 按版本 tag 留旧镜像 → 回滚 = `IMAGE_SOURCE=registry bash deploy.sh server <旧版本>`。⚠️ alembic 不自动降级，回滚涉及 schema 变更需人工评估。
4. 数据 bind 挂载不动，**绝不 `down -v`**、不删 `data/`。

## 10. 一次性前置（上线前铺一次）

- **Harbor**：建 `geo` 项目 + `robot$geo+ci-rw` 读写机器人账号；生成 `DOCKER_AUTH_CONFIG`。
- **首次 seed base**：`base-<ver>` tag 跑一遍，确保 Harbor 有 base 供 server 构建 FROM。
- **外网服务器（新栈，全程不碰老栈 `geo`/`/root/geo`）**：
  - Docker + Compose v2 已装（老栈在用）；
  - **新建**独立目录 `$DEPLOY_PATH`（如 `/root/geo-collab`，本机无 `/data`）铺新栈 `docker-compose.prod.yml`（项目 `geo-ci`、`registry` 服务、`$HTTP_PORT` 高端口、`geo-ci_*` 独立卷）+ 填好的 `.env` + `deploy.sh`；
  - 首次 `docker load` 一次 `geo-dailyhot`（基础设施镜像，不进 tag 流水线）；
  - `data/registry` 目录随 registry 服务自动创建；
  - CI `DEPLOY_SSH_KEY` 对应公钥加入 `authorized_keys`（免密）；
  - 确认允许 SSH 本地端口转发（`AllowTcpForwarding yes`，默认开）；
  - **上线前 `df -h` 复核**：根盘仅剩 24G，base 镜像 + registry blob + 新库数据别写满拖垮老栈。
- **runner**：确认可达 Harbor 与 `public.ecr.aws`、可 SSH 到外网服务器；skopeo 镜像可达（否则 seed 进 Harbor）。

## 11. 待补 / 风险

- **`DEPLOY_SSH_HOST` = `47.115.134.13`、`DEPLOY_SSH_USER` = `root`**（值填 GitLab CI 变量，CI 文件内仍用 `$DEPLOY_SSH_HOST` 占位，不硬编码）。新栈独立并行、老栈不动，见 §1.1。
- **ECR Public 基础镜像 tag 覆盖**：`public.ecr.aws/docker/library` 需含 `python:3.12-slim` / `node:22-bookworm-slim` / `nginx:stable-alpine`；若某 tag 缺失，回退把基础镜像 seed 进 Harbor geo 项目、`BASE_REGISTRY` 指向它。
- **首次 base 传输 ~2GB**：第一次 `server-*`/`release-*` 部署会把 base 全量层传到服务器本地 registry（一次性），之后代码更新才增量。可先手动跑一次 base 搬运预热。磁盘只剩 24G，注意别塞满。
- **单 runner 串行**：build+bridge+deploy 占用唯一 runner 较久；发版时段避免与日常流水线抢。
- **新栈用什么域名/入口对外**：新 nginx 绑高端口（如 8081）先内部验证；若要正式对外（域名/HTTPS），后续再决定是否接老栈的 `geo-https-nginx` 或另配。不阻塞本方案。

## 12. 明确不做（YAGNI）

- **不碰线上老栈**（`geo` 项目 / `/root/geo` / git-push-build 流程）——新栈验证通过前，老栈是唯一生产。
- 不拆 app / worker 为两个镜像（共享 base 后瘦身收益已足；已有 `Dockerfile.app` 的 slim 思路留作备选，见 §4.2）。
- 不把浏览器做成独立 sidecar 容器（noVNC/X 耦合，改造过大）。
- 不把 `geo-dailyhot` 纳入 tag 流水线（基础设施镜像，一次性手动 seed）。
- 不上 Merge Trains / 云 ACR（本方案零新增外部依赖）。
- 不做新老栈的数据迁移 / 流量切换（新栈从空库起验证；真要切换是另一个独立任务）。
