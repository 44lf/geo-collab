# Tag 驱动自动部署（A 方案·新老并行）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 打 tag → GitLab CI kaniko 构建 → 推内网 Harbor `geo` 项目 → skopeo 经 SSH 隧道增量搬入外网服务器本地 `registry:2` → `deploy.sh` registry 模式上线；作为独立并行栈 `geo-ci`，与线上老栈 `geo` 完全隔离。

**Architecture:** 镜像基础分层（`geo-collab-base` 慢层 + `geo-collab-server` 薄代码层）；CI 非特权 kaniko 构建、skopeo daemonless 增量搬运；服务器端复用并扩展 `deploy/deploy.sh`（新增 `IMAGE_SOURCE=registry` 拉取模式），一次性 `migrate` 服务单点迁移，健康检查 + 飞书告警。

**Tech Stack:** GitLab CI（self-hosted hlgit）、kaniko（用户态构建）、Harbor（内网私仓）、skopeo（增量搬运）、`registry:2`（服务器本地仓）、Docker Compose v2、bash。

## Global Constraints

- **新老并行、老栈不动**：老栈 = compose 项目 `geo` / 目录 `/root/geo`（git-push 服务器构建）。本方案新栈 = 项目 `geo-ci` / 目录 `/root/geo-collab` / nginx 高端口 `$HTTP_PORT=8081` / bind 挂载 `./data/` / 镜像 `geo-collab-*`。每一项都和老栈错开。
- **生产机 = `47.115.134.13`（root）**；交互连接**用本地** `ssh -i C:/Users/19427/.ssh/id_rsa root@47.115.134.13`（**不要**用 ssh MCP 的 default——它指向无关的 `8.219.98.35`）。
- **任何触碰服务器的写操作（建目录 / 起容器 / `docker compose up` / 动 `.env`）动手前必须找用户确认**。只读可先做。**绝不** `down -v`、绝不删数据。根盘仅剩 24G，注意别写满。
- **零硬编码**：host / 账号 / 路径全走 CI 变量占位（GitLab UI 配）。CI 文件里只用 `$VAR`。
- CI 变量（GitLab → Settings → CI/CD → Variables）：`HARBOR_REGISTRY`、`HARBOR_PROJECT`、`DOCKER_AUTH_CONFIG`(Masked)、`HARBOR_USER`/`HARBOR_TOKEN`(Masked)、`DEPLOY_SSH_HOST`、`DEPLOY_SSH_USER`、`DEPLOY_SSH_KEY`(File)、`DEPLOY_PATH`、`LOCAL_REGISTRY`、`BASE_REGISTRY`、`GEO_FEISHU_WEBHOOK_URL`。
- **kaniko 基础镜像源**：build-arg `BASE_REGISTRY`，CI 传 `public.ecr.aws/docker/library`，本地默认 `docker.io/library`。
- tag → 端：`base-<ver>`(仅建 base) / `server-<ver>`(app+worker,迁移) / `web-<ver>`(nginx,不迁移) / `release-<ver>`(=all)。版本号取 tag 后缀。
- 参考蓝本：`.gitlab-ci-hr.yml`（HrSystem 的 kaniko+Harbor+SSH deploy）、`deploy/dist/*`（现有 build.sh/deploy.sh/compose，本方案在其基础上演进）。
- 设计依据：`docs/superpowers/specs/2026-07-08-tag-driven-deploy-design.md`。

---

## 阶段总览

| 阶段 | 内容 | 是否碰服务器 |
|---|---|---|
| Phase 1 | 镜像基础分层：`Dockerfile.base` + 瘦身 `Dockerfile` + 参数化 `Dockerfile.nginx` | 否（repo） |
| Phase 2 | 新栈 compose + `.env.example` + `VERSION` | 否（repo） |
| Phase 3 | `deploy.sh` 加 `IMAGE_SOURCE=registry` 拉取模式 + `build.sh` 加 base 构建 | 否（repo） |
| Phase 4 | `ci/deploy.gitlab-ci.yml` + `.gitlab-ci.yml` 接线（tag 触发 + include） | 否（repo，CI lint 验证） |
| Phase 5 | Harbor `geo` 项目 + robot 账号 + GitLab CI 变量 | 否（Harbor/GitLab UI） |
| Phase 6 | 服务器一次性铺底：新目录 + `.env` + registry + dailyhot（**逐步确认**） | **是（确认）** |
| Phase 7 | 端到端：`base-*` 预热 → `release-*` 首发 → 验证高端口访问 + 健康检查 | **是（确认）** |

---

## Phase 1 — 镜像基础分层（repo，无服务器影响）

### Task 1: 新建 `Dockerfile.base`（慢变重层）

**Files:**
- Create: `Dockerfile.base`

**Interfaces:**
- Produces: 镜像 `geo-collab-base`，含 python3.12 + 浏览器全家桶 + `pip install -r requirements.txt` + `playwright install chromium`。供 `Dockerfile`（server）`FROM` 它。
- Consumes: build-arg `BASE_REGISTRY`（默认 `docker.io/library`）。

- [ ] **Step 1: 写 `Dockerfile.base`**

```dockerfile
# geo-collab-base —— 慢变重层：Python + 浏览器自动化栈 + 依赖。
# 仅在 requirements.txt / 浏览器栈变化时重建（base-<ver> tag 触发）。
# FROM 参数化：本地默认 docker.io，CI 传 public.ecr.aws/docker/library（内网 runner 可达）。
ARG BASE_REGISTRY=docker.io/library
FROM ${BASE_REGISTRY}/python:3.12-slim

# 阿里云 apt 镜像加速
RUN sed -i 's|http://deb.debian.org/debian|http://mirrors.aliyun.com/debian|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's|http://deb.debian.org/debian|http://mirrors.aliyun.com/debian|g' /etc/apt/sources.list 2>/dev/null || true

# 浏览器自动化系统依赖（Chromium / Xvfb / VNC / noVNC / 中文字体 / 运行时库）
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb x11vnc websockify novnc chromium \
    fonts-noto-cjk libnss3 libnspr4 libatk-bridge2.0-0 \
    libdrm2 libxkbcommon0 libgbm1 libasound2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python 依赖（清华镜像）
COPY requirements.txt .
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    -r requirements.txt

# Playwright Chromium（npmmirror 加速）。版本只取决于 playwright 包 → 极少变。
RUN PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright \
    playwright install chromium
```

- [ ] **Step 2: 本地验证语法（有 Docker 时）**

Run（本地装了 Docker Desktop 时）：`docker build --check -f Dockerfile.base .`
Expected: `Check complete, no warnings found`（或仅告警、无 error）。
无本地 Docker 时跳过——Phase 7 打 `base-*` tag 时由 kaniko 真构建验证。

- [ ] **Step 3: Commit**

```bash
git add Dockerfile.base
git commit -m "feat(deploy): add Dockerfile.base (heavy browser+deps layer)"
```

### Task 2: 瘦身 `Dockerfile`（server 薄代码层，FROM base）

**Files:**
- Modify: `Dockerfile`（整文件替换）

**Interfaces:**
- Consumes: build-arg `BASE_IMAGE`（默认 `geo-collab-base:latest`；CI 传 `$HARBOR_REGISTRY/$HARBOR_PROJECT/geo-collab-base:<baseVer>`）。
- Produces: 镜像 `geo-collab-server`（app + worker 共用）；不含 node 构建、不含前端 dist；迁移交由 compose `migrate` 服务，不写进 CMD。

- [ ] **Step 1: 用新内容替换 `Dockerfile`**

```dockerfile
# geo-collab-server —— 薄代码层：FROM geo-collab-base + 仅复制源码。
# 慢变的浏览器/依赖在 base 里；改代码只重建下面的 COPY 层（几 MB）。
# 前端由 nginx（geo-collab-web）serve，生产 app 不 serve SPA → 不再含 node 构建 / web/dist。
ARG BASE_IMAGE=geo-collab-base:latest
FROM ${BASE_IMAGE}

WORKDIR /app

# 源码（.dockerignore 已排除 node_modules/.git/data 等）
COPY . .

EXPOSE 8000

# 迁移不在此执行（由 compose 的一次性 migrate 服务单点跑）。此处只起 web。
CMD ["sh", "-c", "uvicorn server.app.main:app --host 0.0.0.0 --port 8000"]
```

- [ ] **Step 2: 确认 `.dockerignore` 排除大目录**

Run: `Get-Content .dockerignore`
Expected: 含 `node_modules`、`.git`、`data`、`web/node_modules` 等。若缺 `web/dist` 之外无碍（COPY . . 会带上，但 web/dist 由 nginx 镜像负责，server 用不到；如已存在本地 dist 可加进 .dockerignore 避免混入）。若缺关键项则补上并单独说明。

- [ ] **Step 3: Commit**

```bash
git add Dockerfile
git commit -m "refactor(deploy): slim Dockerfile to FROM base + code only (server)"
```

### Task 3: 参数化 `Dockerfile.nginx` 的 FROM

**Files:**
- Modify: `Dockerfile.nginx:2,14,21`（三处 FROM 加 `${BASE_REGISTRY}/` 前缀 + 顶部加 ARG）

**Interfaces:**
- Consumes: build-arg `BASE_REGISTRY`（默认 `docker.io/library`）。
- Produces: 镜像 `geo-collab-web`（不变逻辑，仅基础镜像来源可切换）。

- [ ] **Step 1: 顶部加 ARG，三处 FROM 加前缀**

改后前 21 行形如：

```dockerfile
# Nginx 镜像：构建 React 前端，由 Nginx 直接 serve 静态资源
ARG BASE_REGISTRY=docker.io/library
FROM ${BASE_REGISTRY}/node:22-bookworm-slim AS web-build

WORKDIR /app
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY web/package.json web/package.json
RUN corepack enable && corepack prepare pnpm@10.4.0 --activate
RUN npm config set registry https://registry.npmmirror.com
RUN pnpm install --frozen-lockfile
COPY web ./web
RUN pnpm --filter @geo/web build

FROM ${BASE_REGISTRY}/python:3.12-slim AS novnc-files
RUN sed -i 's|http://deb.debian.org/debian|http://mirrors.aliyun.com/debian|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's|http://deb.debian.org/debian|http://mirrors.aliyun.com/debian|g' /etc/apt/sources.list 2>/dev/null || true
RUN apt-get update && apt-get install -y --no-install-recommends novnc && \
    rm -rf /var/lib/apt/lists/*

FROM ${BASE_REGISTRY}/nginx:stable-alpine
COPY --from=web-build /app/web/dist /usr/share/nginx/html
COPY --from=novnc-files /usr/share/novnc /usr/share/novnc
COPY nginx.conf /etc/nginx/conf.d/default.conf
```

> ⚠️ 多 stage 都引用同一个 `BASE_REGISTRY` ARG。kaniko/docker 中，`ARG` 在第一个 `FROM` 前声明后，每个 stage 内若要复用需在该 stage 内再次 `ARG BASE_REGISTRY`——但因为这里 ARG 用在 `FROM` 行本身（全局 ARG），只需顶部声明一次即可对所有 `FROM` 生效。验证见 Step 2。

- [ ] **Step 2: 本地验证（有 Docker 时）**

Run: `docker build --check -f Dockerfile.nginx .`
Expected: 无 error。无本地 Docker 则 Phase 7 由 kaniko 验证。

- [ ] **Step 3: Commit**

```bash
git add Dockerfile.nginx
git commit -m "refactor(deploy): parametrize Dockerfile.nginx FROM via BASE_REGISTRY"
```

---

## Phase 2 — 新栈 compose 与配置（repo，无服务器影响）

### Task 4: 新栈 `deploy/docker-compose.prod.yml`（geo-ci 项目 + registry）

**Files:**
- Create/Modify: `deploy/docker-compose.prod.yml`

**Interfaces:**
- Consumes: `.env`（`SERVER_VERSION`/`WEB_VERSION`/`HTTP_PORT`/`MYSQL_*`/`MINIO_*` 等）。
- Produces: 新栈服务 `registry`/`mysql`/`minio`/`dailyhot-api`/`migrate`(profile)/`app`/`worker`/`nginx`，镜像用**裸名** `geo-collab-server`/`geo-collab-web`（deploy.sh 从本地 registry 拉后 retag）。

- [ ] **Step 1: 写 `deploy/docker-compose.prod.yml`**

```yaml
# Geo Collab 新栈生产编排（并行栈，项目名 geo-ci）。镜像裸名由 deploy.sh 从
# 本地 registry 拉取后 retag；版本由 SERVER_VERSION / WEB_VERSION 注入。
# 数据 bind 挂载 ./data/，与老栈物理隔离。切勿 down -v、切勿删 ./data。
# 发布 worker 单实例，不要 --scale worker=N。
services:
  registry:
    image: registry:2
    restart: unless-stopped
    ports:
      - "127.0.0.1:5000:5000"          # 仅回环，不对公网暴露
    volumes:
      - ./data/registry:/var/lib/registry

  mysql:
    image: mysql:8.0
    command: ["--max-connections=200"]
    restart: unless-stopped
    environment:
      MYSQL_ROOT_PASSWORD: ${MYSQL_ROOT_PASSWORD:?required}
      MYSQL_DATABASE: ${MYSQL_DATABASE:-geo_collab}
      MYSQL_USER: ${MYSQL_USER:-geo_user}
      MYSQL_PASSWORD: ${MYSQL_PASSWORD:?required}
    volumes:
      - ./data/mysql:/var/lib/mysql
    healthcheck:
      test: ["CMD", "mysqladmin", "ping", "-h", "localhost"]
      interval: 10s
      timeout: 5s
      retries: 5

  minio:
    image: minio/minio
    restart: unless-stopped
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: ${MINIO_ROOT_USER:?required}
      MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD:?required}
    volumes:
      - ./data/minio:/data

  dailyhot-api:
    image: geo-dailyhot:latest
    restart: unless-stopped

  migrate:
    image: geo-collab-server:${SERVER_VERSION:?required}
    profiles: ["tools"]
    command: ["alembic", "upgrade", "head"]
    depends_on:
      mysql:
        condition: service_healthy
    env_file:
      - .env
    environment:
      GEO_DB_HOST: mysql
      GEO_DB_PORT: 3306
      GEO_DB_USER: ${MYSQL_USER:-geo_user}
      GEO_DB_PASS: ${MYSQL_PASSWORD}
      GEO_DB_NAME: ${MYSQL_DATABASE:-geo_collab}
      GEO_DATA_DIR: /app/data

  app:
    image: geo-collab-server:${SERVER_VERSION:?required}
    restart: unless-stopped
    command: >
      sh -c "python -m server.scripts.seed_users &&
             uvicorn server.app.main:app --host 0.0.0.0 --port 8000"
    depends_on:
      mysql:
        condition: service_healthy
      minio:
        condition: service_started
      dailyhot-api:
        condition: service_started
    env_file:
      - .env
    environment:
      GEO_DB_HOST: mysql
      GEO_DB_PORT: 3306
      GEO_DB_USER: ${MYSQL_USER:-geo_user}
      GEO_DB_PASS: ${MYSQL_PASSWORD}
      GEO_DB_NAME: ${MYSQL_DATABASE:-geo_collab}
      GEO_DATA_DIR: /app/data
      GEO_NGINX_ACCEL: "1"
      GEO_HOTLIST_API_URL: http://dailyhot-api:6688
      GEO_MINIO_ENDPOINT: minio:9000
      GEO_MINIO_ACCESS_KEY: ${MINIO_ROOT_USER}
      GEO_MINIO_SECRET_KEY: ${MINIO_ROOT_PASSWORD}
      # 钉死回环，避免阿里云 ECS 无 hairpin NAT 时 MCP 自调用超时
      GEO_MCP_INTERNAL_API_URL: http://127.0.0.1:8000
    volumes:
      - ./data/app:/app/data

  worker:
    image: geo-collab-server:${SERVER_VERSION:?required}
    restart: unless-stopped
    command: ["python", "-m", "server.worker.executor"]
    depends_on:
      mysql:
        condition: service_healthy
      minio:
        condition: service_started
    env_file:
      - .env
    environment:
      GEO_DB_HOST: mysql
      GEO_DB_PORT: 3306
      GEO_DB_USER: ${MYSQL_USER:-geo_user}
      GEO_DB_PASS: ${MYSQL_PASSWORD}
      GEO_DB_NAME: ${MYSQL_DATABASE:-geo_collab}
      GEO_DATA_DIR: /app/data
      GEO_MINIO_ENDPOINT: minio:9000
      GEO_MINIO_ACCESS_KEY: ${MINIO_ROOT_USER}
      GEO_MINIO_SECRET_KEY: ${MINIO_ROOT_PASSWORD}
      GEO_PUBLISH_NOVNC_WEB_DIR: /usr/share/novnc
      GEO_PUBLISH_REMOTE_BROWSER_HOST: 0.0.0.0
      GEO_PUBLISH_XVFB_PATH: Xvfb
      GEO_PUBLISH_X11VNC_PATH: x11vnc
      GEO_PUBLISH_WEBSOCKIFY_PATH: websockify
    volumes:
      - ./data/app:/app/data

  nginx:
    image: geo-collab-web:${WEB_VERSION:?required}
    restart: unless-stopped
    ports:
      - "${HTTP_PORT:-8081}:80"          # 高端口，避开老栈占用的 80
    depends_on:
      - app
      - worker
    volumes:
      - ./data/app:/app_data:ro
```

- [ ] **Step 2: 校验 compose 语法**

Run（有 Docker 时，用假变量）：
```bash
cd deploy && SERVER_VERSION=x WEB_VERSION=x MYSQL_ROOT_PASSWORD=x MYSQL_PASSWORD=x MINIO_ROOT_USER=x MINIO_ROOT_PASSWORD=x docker compose -f docker-compose.prod.yml config >/dev/null && echo OK
```
Expected: `OK`（无 YAML/变量错误）。无 Docker 时至少用 `python -c "import yaml,sys;yaml.safe_load(open('deploy/docker-compose.prod.yml'))"` 验证 YAML 合法。

- [ ] **Step 3: Commit**

```bash
git add deploy/docker-compose.prod.yml
git commit -m "feat(deploy): geo-ci parallel-stack compose with local registry"
```

### Task 5: 新栈 `.env.example` 与 `VERSION`

**Files:**
- Create/Modify: `deploy/.env.example`、`deploy/VERSION`

**Interfaces:**
- Produces: `deploy/VERSION` 含 `BASE=` / `SERVER=` / `WEB=` 三端；`.env.example` 含 `HTTP_PORT`/`COMPOSE_PROJECT_NAME` 等新栈键。

- [ ] **Step 1: 写 `deploy/VERSION`**

```
BASE=1.0.0
SERVER=1.0.0
WEB=1.0.0
```

- [ ] **Step 2: 在 `deploy/.env.example` 顶部补新栈键**

在文件顶部（`HTTP_PORT` 附近）确保有：

```bash
# 新栈 compose 项目名（隔离老栈 geo；容器名将是 geo-ci-*）
COMPOSE_PROJECT_NAME=geo-ci
# 对外端口：避开老栈占用的 80/443，用高端口
HTTP_PORT=8081
```

其余 MYSQL_*/MINIO_*/GEO_JWT_SECRET/GEO_SEED_USERS 沿用现有 `deploy/dist/.env.example`（复制过来，保留所有 change_me 注释）。

- [ ] **Step 3: Commit**

```bash
git add deploy/VERSION deploy/.env.example
git commit -m "feat(deploy): geo-ci .env.example + 3-tier VERSION (BASE/SERVER/WEB)"
```

---

## Phase 3 — deploy.sh registry 模式 + build.sh base 构建（repo）

### Task 6: `deploy/deploy.sh` 加 `IMAGE_SOURCE=registry` 拉取模式

**Files:**
- Modify: `deploy/deploy.sh`（在"加载镜像"段前后分叉）

**Interfaces:**
- Consumes: 环境变量 `IMAGE_SOURCE`（`tarball` 默认 | `registry`）、`LOCAL_REGISTRY`（如 `127.0.0.1:5000`）、`HARBOR_PROJECT`（如 `geo`）。
- Produces: registry 模式下 `docker pull $LOCAL_REGISTRY/$HARBOR_PROJECT/geo-collab-<img>:<ver>` + `docker tag` 成裸名 `geo-collab-<img>:<ver>`；其余（migrate / versions.env / up -d）不变。

- [ ] **Step 1: 在 `deploy/deploy.sh` 顶部变量区加**

```bash
IMAGE_SOURCE="${IMAGE_SOURCE:-tarball}"          # tarball（默认，docker load）| registry（docker pull 本地仓）
LOCAL_REGISTRY="${LOCAL_REGISTRY:-127.0.0.1:5000}"
HARBOR_PROJECT="${HARBOR_PROJECT:-geo}"
```

- [ ] **Step 2: 把"加载镜像"段改成按 IMAGE_SOURCE 分叉**

将现有：
```bash
if $deploy_server; then echo ""; echo "==> 加载后端镜像"; docker load < "$SERVER_TAR"; fi
if $deploy_web;    then echo ""; echo "==> 加载前端镜像"; docker load < "$WEB_TAR"; fi
```
替换为：
```bash
pull_and_retag() {  # $1=镜像名(geo-collab-server) $2=版本
  local img="$1" ver="$2" src="$LOCAL_REGISTRY/$HARBOR_PROJECT/$1:$2"
  echo "==> 从本地 registry 拉取 $src"
  docker pull "$src"
  docker tag "$src" "$img:$ver"
}

if [[ "$IMAGE_SOURCE" == "registry" ]]; then
  if $deploy_server; then echo ""; pull_and_retag "$SERVER_IMAGE" "$SERVER_VERSION"; fi
  if $deploy_web;    then echo ""; pull_and_retag "$WEB_IMAGE"    "$WEB_VERSION";    fi
else
  if $deploy_server; then echo ""; echo "==> 加载后端镜像"; docker load < "$SERVER_TAR"; fi
  if $deploy_web;    then echo ""; echo "==> 加载前端镜像"; docker load < "$WEB_TAR"; fi
fi
```

> 注意：registry 模式下 `SERVER_TAR`/`WEB_TAR` 的存在性检查应跳过。在 tar 检查段（`find_tar`）外层加 `if [[ "$IMAGE_SOURCE" != "registry" ]]; then ... fi` 包住"缺少镜像包"报错，registry 模式不查 tar。

- [ ] **Step 3: 部署完成尾部加健康检查 hook（可选本地失败即报错）**

在 `docker compose ... up -d` 之后加：
```bash
echo ""; echo "==> 健康检查（本机 http://127.0.0.1:$HTTP_PORT/）"
HTTP_PORT="$(grep -E '^HTTP_PORT=' "$ENV_FILE" | cut -d= -f2- | tr -d '[:space:]' || true)"; HTTP_PORT="${HTTP_PORT:-8081}"
ok=0
for i in $(seq 1 20); do
  code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$HTTP_PORT/" || true)"
  if [[ "$code" =~ ^(200|301|302|401|403)$ ]]; then ok=1; echo "健康检查通过（HTTP $code）"; break; fi
  sleep 3
done
[[ "$ok" == 1 ]] || { echo "❌ 健康检查失败（端口 $HTTP_PORT 无响应）"; exit 2; }
```

- [ ] **Step 4: 校验脚本语法**

Run（有 shellcheck 时）：`shellcheck deploy/deploy.sh` — Expected: 无 error（warning 可接受）。
无 shellcheck 时：`bash -n deploy/deploy.sh` — Expected: 无输出（语法 OK）。

- [ ] **Step 5: Commit**

```bash
git add deploy/deploy.sh
git commit -m "feat(deploy): deploy.sh registry pull mode + health check"
```

### Task 7: `deploy/build.sh` 加 base 构建 + build-arg

**Files:**
- Modify: `deploy/build.sh`

**Interfaces:**
- Consumes: `deploy/VERSION` 的 `BASE=`。
- Produces: 本地可 `bash deploy/build.sh base|server|web|all`；server 构建传 `--build-arg BASE_IMAGE=geo-collab-base:$BASE_VERSION`。

- [ ] **Step 1: 读 BASE 版本 + 加 base 构建分支**

在 `read_ver` 之后加 `BASE_VERSION="$(read_ver BASE)"`，并在 `case` 里允许 `base`：
```bash
case "$TARGET" in
  all|base|server|web) ;;
  *) echo "错误: 未知目标 '$TARGET'（可选 all|base|server|web）"; exit 1 ;;
esac
```
在 server 构建前加 base 构建（`all`/`base` 时）：
```bash
if [[ "$TARGET" == "all" || "$TARGET" == "base" ]]; then
  echo ""; echo "==> 构建基础镜像 geo-collab-base:$BASE_VERSION"
  docker build --platform linux/amd64 \
    -t "geo-collab-base:$BASE_VERSION" -t "geo-collab-base:latest" \
    -f "$ROOT/Dockerfile.base" "$ROOT"
fi
```

- [ ] **Step 2: server 构建传 BASE_IMAGE build-arg**

把 server 的 `docker build` 改为：
```bash
docker build --platform linux/amd64 \
  --build-arg BASE_IMAGE="geo-collab-base:$BASE_VERSION" \
  -t "$SERVER_IMAGE:$SERVER_VERSION" -t "$SERVER_IMAGE:latest" \
  -f "$ROOT/Dockerfile" "$ROOT"
```

- [ ] **Step 3: 校验**

Run: `bash -n deploy/build.sh` — Expected: 无输出。

- [ ] **Step 4: Commit**

```bash
git add deploy/build.sh
git commit -m "feat(deploy): build.sh base image + BASE_IMAGE build-arg"
```

---

## Phase 4 — CI 接线（repo，用 GitLab lint API 验证）

### Task 8: 新建 `ci/deploy.gitlab-ci.yml`（kaniko 构建 + skopeo 搬运 + 部署）

**Files:**
- Create: `ci/deploy.gitlab-ci.yml`

**Interfaces:**
- Consumes: 全部 CI 变量（见 Global Constraints）+ tag。
- Produces: jobs `build:base` / `build:server` / `build:web` / `deploy`（deploy 内含 skopeo 搬运 + ssh deploy.sh + 健康检查 + 飞书）。

- [ ] **Step 1: 写 `ci/deploy.gitlab-ci.yml`**

```yaml
# Tag 驱动构建 + 部署（被 .gitlab-ci.yml include）。参考 .gitlab-ci-hr.yml。
# tag: base-<ver> / server-<ver> / web-<ver> / release-<ver>

.kaniko:
  image:
    name: ${HARBOR_REGISTRY}/${HARBOR_PROJECT}/kaniko-executor:v1.23.2-debug
    entrypoint: [""]
  tags: [docker]
  before_script:
    - mkdir -p /kaniko/.docker
    - echo "$DOCKER_AUTH_CONFIG" > /kaniko/.docker/config.json

build:base:
  extends: .kaniko
  stage: build
  rules:
    - if: '$CI_COMMIT_TAG =~ /^base-/'
  script:
    - VER=${CI_COMMIT_TAG#base-}
    - IMG="$HARBOR_REGISTRY/$HARBOR_PROJECT/geo-collab-base"
    - echo "==> kaniko 构建 base $IMG:$VER"
    - |
      /kaniko/executor \
        --context "$CI_PROJECT_DIR" \
        --dockerfile "$CI_PROJECT_DIR/Dockerfile.base" \
        --build-arg BASE_REGISTRY="$BASE_REGISTRY" \
        --skip-tls-verify-registry "$HARBOR_REGISTRY" \
        --destination "$IMG:$VER" --destination "$IMG:latest"

build:server:
  extends: .kaniko
  stage: build
  rules:
    - if: '$CI_COMMIT_TAG =~ /^(server|release)-/'
  script:
    - VER=${CI_COMMIT_TAG#server-}; VER=${VER#release-}
    - BASE_VER=$(grep -E '^BASE=' deploy/VERSION | cut -d= -f2 | tr -d '[:space:]')
    - IMG="$HARBOR_REGISTRY/$HARBOR_PROJECT/geo-collab-server"
    - BASE_IMAGE="$HARBOR_REGISTRY/$HARBOR_PROJECT/geo-collab-base:$BASE_VER"
    - echo "==> kaniko 构建 server $IMG:$VER (base=$BASE_IMAGE)"
    - |
      /kaniko/executor \
        --context "$CI_PROJECT_DIR" \
        --dockerfile "$CI_PROJECT_DIR/Dockerfile" \
        --build-arg BASE_IMAGE="$BASE_IMAGE" \
        --skip-tls-verify-registry "$HARBOR_REGISTRY" \
        --destination "$IMG:$VER" --destination "$IMG:latest"

build:web:
  extends: .kaniko
  stage: build
  rules:
    - if: '$CI_COMMIT_TAG =~ /^(web|release)-/'
  script:
    - VER=${CI_COMMIT_TAG#web-}; VER=${VER#release-}
    - IMG="$HARBOR_REGISTRY/$HARBOR_PROJECT/geo-collab-web"
    - echo "==> kaniko 构建 web $IMG:$VER"
    - |
      /kaniko/executor \
        --context "$CI_PROJECT_DIR" \
        --dockerfile "$CI_PROJECT_DIR/Dockerfile.nginx" \
        --build-arg BASE_REGISTRY="$BASE_REGISTRY" \
        --skip-tls-verify-registry "$HARBOR_REGISTRY" \
        --destination "$IMG:$VER" --destination "$IMG:latest"

deploy:
  image: ${HARBOR_REGISTRY}/${HARBOR_PROJECT}/skopeo-stable:latest
  stage: deploy
  interruptible: false
  rules:
    - if: '$CI_COMMIT_TAG =~ /^(server|web|release)-/'
  before_script:
    - which ssh || (apk add --no-cache openssh-client curl || true)
    - mkdir -p ~/.ssh && chmod 700 ~/.ssh
    - cp "$DEPLOY_SSH_KEY" ~/.ssh/id_deploy && chmod 600 ~/.ssh/id_deploy
    - |
      printf 'Host deploy\n  HostName %s\n  User %s\n  IdentityFile ~/.ssh/id_deploy\n  StrictHostKeyChecking accept-new\n' \
        "$DEPLOY_SSH_HOST" "$DEPLOY_SSH_USER" > ~/.ssh/config
  script:
    - TAG="$CI_COMMIT_TAG"
    - |
      case "$TAG" in
        release-*) TARGET=all;    VER=${TAG#release-} ;;
        server-*)  TARGET=server; VER=${TAG#server-} ;;
        web-*)     TARGET=web;    VER=${TAG#web-} ;;
      esac
    - echo "==> 部署 $TARGET 版本 $VER 到 $DEPLOY_SSH_HOST"
    # 开 SSH 隧道：本地 5000 → 服务器本地 registry
    - ssh -fN -L 5000:127.0.0.1:5000 deploy
    - |
      copy() { skopeo copy --src-authfile /kaniko/.docker/config.json --dest-tls-verify=false \
        "docker://$HARBOR_REGISTRY/$HARBOR_PROJECT/$1:$VER" \
        "docker://localhost:5000/$HARBOR_PROJECT/$1:$VER"; }
    # DOCKER_AUTH_CONFIG 落文件供 skopeo --src-authfile
    - mkdir -p /kaniko/.docker && echo "$DOCKER_AUTH_CONFIG" > /kaniko/.docker/config.json
    - if [ "$TARGET" = all ] || [ "$TARGET" = server ]; then copy geo-collab-server; fi
    - if [ "$TARGET" = all ] || [ "$TARGET" = web ]; then copy geo-collab-web; fi
    # 远端拉取 + 迁移 + 起服务（deploy.sh registry 模式）
    - |
      ssh deploy bash -s <<EOF
      set -euo pipefail
      cd "$DEPLOY_PATH"
      export IMAGE_SOURCE=registry LOCAL_REGISTRY=127.0.0.1:5000 HARBOR_PROJECT=$HARBOR_PROJECT
      bash deploy.sh $TARGET $VER
      EOF
  after_script:
    - |
      if [ "$CI_JOB_STATUS" != "success" ] && [ -n "$GEO_FEISHU_WEBHOOK_URL" ]; then
        curl -s -X POST "$GEO_FEISHU_WEBHOOK_URL" -H 'Content-Type: application/json' \
          -d "{\"msg_type\":\"text\",\"content\":{\"text\":\"❌ GEO 新栈部署失败 tag=$CI_COMMIT_TAG job=$CI_JOB_URL\"}}" || true
      fi
```

> skopeo 镜像 `skopeo-stable` 与 kaniko 镜像同样需 seed 进 Harbor（Phase 5）。`--src-authfile` 用 kaniko 同款 config.json。

- [ ] **Step 2: 用 GitLab CI lint API 校验（真验证）**

此步先单独确认 include 文件 YAML 合法（完整流水线 lint 在 Task 9 Step 4）：
Run（PowerShell）：
```powershell
python -c "import yaml;yaml.safe_load(open('ci/deploy.gitlab-ci.yml'));print('YAML OK')"
```
Expected: `YAML OK`。

- [ ] **Step 3: Commit**

```bash
git add ci/deploy.gitlab-ci.yml
git commit -m "feat(ci): tag-driven build(kaniko)+bridge(skopeo)+deploy jobs"
```

### Task 9: `.gitlab-ci.yml` 接线（tag 触发 + include + stages）

**Files:**
- Modify: `.gitlab-ci.yml`（`workflow:rules` 加 tag 规则；`stages` 加 build/deploy；加 `include`）

**Interfaces:**
- Consumes: `ci/deploy.gitlab-ci.yml`。
- Produces: 打 `base-/server-/web-/release-*` tag 触发部署流水线；日常分支/MR 行为不变。

- [ ] **Step 1: `workflow:rules` 增 tag 规则**

在现有 `workflow: rules:` 列表末尾加：
```yaml
    - if: '$CI_COMMIT_TAG =~ /^(base|server|web|release)-/'
```

- [ ] **Step 2: `stages` 增 build/deploy**

```yaml
stages:
  - lint
  - test
  - frontend
  - build
  - deploy
```

- [ ] **Step 3: 文件末尾加 include**

```yaml
include:
  - local: ci/deploy.gitlab-ci.yml
```

- [ ] **Step 4: GitLab CI lint 整体校验（真验证）**

Run（PowerShell）：
```powershell
$tok = ((Get-Content .env | ? {$_ -match '^GITLAB_TOKEN='}) -replace '^GITLAB_TOKEN=','').Trim()
$body = @{ content = (Get-Content .gitlab-ci.yml -Raw) } | ConvertTo-Json
Invoke-RestMethod -Uri "https://hlgit.5518game.com/api/v4/projects/30/ci/lint" -Method Post -Headers @{'PRIVATE-TOKEN'=$tok} -ContentType 'application/json' -Body $body | Select-Object valid,errors
```
Expected: `valid : True`，`errors` 为空。若 `valid False` → 按 errors 修 YAML 后重跑。
> 注：项目级 `ci/lint` 会合并 include 文件一起校验。

- [ ] **Step 5: Commit**

```bash
git add .gitlab-ci.yml
git commit -m "feat(ci): wire tag-driven deploy (workflow rules + stages + include)"
```

---

## Phase 5 — Harbor 与 GitLab 变量（Harbor/GitLab UI，无服务器影响）

### Task 10: 建 Harbor `geo` 项目 + robot 账号 + seed 工具镜像

**Files:** 无（Harbor UI / API 操作，记录到 `deploy/README.md`）

- [ ] **Step 1: Harbor 建项目 + robot（找用户在 Harbor UI 操作或提供 admin 凭据）**
  - 新建项目 `geo`。
  - 新建 robot 账号 `robot$geo+ci-rw`，权限 push+pull。
  - 生成 `DOCKER_AUTH_CONFIG`：`{"auths":{"<HARBOR_REGISTRY>":{"auth":"<base64(robot:token)>"}}}`。

- [ ] **Step 2: seed 工具镜像到 `geo` 项目**（runner 需从 Harbor 拉 kaniko/skopeo）
  在一台能连 Docker Hub / quay 且能 push Harbor 的机器：
  ```bash
  docker pull gcr.io/kaniko-project/executor:v1.23.2-debug   # 或从可达源
  docker tag  gcr.io/kaniko-project/executor:v1.23.2-debug $HARBOR_REGISTRY/geo/kaniko-executor:v1.23.2-debug
  docker push $HARBOR_REGISTRY/geo/kaniko-executor:v1.23.2-debug
  docker pull quay.io/skopeo/stable:latest
  docker tag  quay.io/skopeo/stable:latest $HARBOR_REGISTRY/geo/skopeo-stable:latest
  docker push $HARBOR_REGISTRY/geo/skopeo-stable:latest
  ```
  seed skopeo 镜像时最好基于 `quay.io/skopeo/stable` 自建一层、预装 `openssh-clients` 和 `curl`，
  push 成 `$HARBOR_REGISTRY/geo/skopeo-stable:latest`，这样 `deploy` job 的 before_script 安装
  就只是兜底（正常路径命中预装、无需再装）。
  记录到 `deploy/README.md`。

- [ ] **Step 3: 提交 README 记录**
  ```bash
  git add deploy/README.md
  git commit -m "docs(deploy): Harbor geo project + robot + seed images notes"
  ```

### Task 11: 配置 GitLab CI/CD 变量

**Files:** 无（GitLab UI）

- [ ] **Step 1: 在 Settings → CI/CD → Variables 逐个填**（找用户填敏感值）
  `HARBOR_REGISTRY`、`HARBOR_PROJECT=geo`、`DOCKER_AUTH_CONFIG`(Masked)、`HARBOR_USER`、`HARBOR_TOKEN`(Masked)、`DEPLOY_SSH_HOST=47.115.134.13`、`DEPLOY_SSH_USER=root`、`DEPLOY_SSH_KEY`(File)、`DEPLOY_PATH=/root/geo-collab`、`LOCAL_REGISTRY=127.0.0.1:5000`、`BASE_REGISTRY=public.ecr.aws/docker/library`、`GEO_FEISHU_WEBHOOK_URL`。

- [ ] **Step 2: 校验（只读 API）**
  Run（PowerShell）：查变量 key 是否齐（参考本会话早先查询方式，`GET /projects/30/variables`）。
  Expected: 上述 key 全部存在。

---

## Phase 6 — 服务器一次性铺底（**每步动手前找用户确认**）

> ⚠️ 本阶段所有写操作在 `47.115.134.13` 上执行，**逐步向用户确认后再跑**。全程只碰新目录 `/root/geo-collab`，不碰老栈 `geo`/`/root/geo`。

### Task 12: 生成 CI 部署用 SSH 密钥对，公钥入服务器

- [ ] **Step 1: 本地生成专用密钥对（不复用个人 key）**
  Run: `ssh-keygen -t ed25519 -f ./geo-ci-deploy-key -N '""' -C "geo-ci-deploy"`
  产出 `geo-ci-deploy-key`(私钥，填 CI `DEPLOY_SSH_KEY`) + `.pub`。

- [ ] **Step 2:（确认后）公钥追加到服务器 authorized_keys**
  **先与用户确认**，再 Run：
  ```powershell
  $pub = Get-Content ./geo-ci-deploy-key.pub -Raw
  ssh -i C:/Users/19427/.ssh/id_rsa root@47.115.134.13 "grep -qF '$($pub.Trim())' ~/.ssh/authorized_keys || echo '$($pub.Trim())' >> ~/.ssh/authorized_keys; echo done"
  ```
  Expected: `done`。私钥填入 GitLab `DEPLOY_SSH_KEY`（File 类型）。

### Task 13:（确认后）在服务器建新栈目录 + .env + dailyhot + registry

- [ ] **Step 1: 打包新栈部署文件本地→服务器**
  本地 `bash deploy/build.sh`（若本地有 Docker；否则镜像走 CI，只需把 compose/.env.example/deploy.sh/VERSION 传上去）：
  ```powershell
  scp -i C:/Users/19427/.ssh/id_rsa deploy/docker-compose.prod.yml deploy/deploy.sh deploy/.env.example deploy/VERSION root@47.115.134.13:/root/geo-collab/
  ```
  **先确认**再执行（会在服务器建 `/root/geo-collab`）。

- [ ] **Step 2:（确认后）服务器上填 .env**
  登录服务器 `cp .env.example .env && vi .env`，替换所有 `change_me`、确认 `COMPOSE_PROJECT_NAME=geo-ci`、`HTTP_PORT=8081`。**由用户操作或确认后代填**。

- [ ] **Step 3:（确认后）只起 registry 验证不影响老栈**
  ```bash
  cd /root/geo-collab && docker compose -f docker-compose.prod.yml up -d registry
  docker ps --format '{{.Names}}' | grep geo-ci-registry-1
  ss -lntp | grep 127.0.0.1:5000
  docker ps --format '{{.Names}}' | grep '^geo-' | grep -v geo-ci   # 老栈仍在
  ```
  Expected: `geo-ci-registry-1` 起、5000 监听、老栈 `geo-*` 容器不受影响。

- [ ] **Step 4:（确认后）load dailyhot 基础镜像**
  把 `deploy/dist/geo-dailyhot.tar.gz` 传上去 `docker load`（首次）。

---

## Phase 7 — 端到端首发与验证（**确认后执行**）

### Task 14: 打 `base-*` 预热

- [ ] **Step 1: bump `deploy/VERSION` 的 BASE，打 tag**
  ```bash
  git tag base-1.0.0 && git push deploy base-1.0.0    # deploy=hlgit
  ```
  **先与用户确认**（会触发 CI kaniko 构建 base 推 Harbor，占 runner ~构建时长）。

- [ ] **Step 2: 观察 CI**
  GitLab pipeline `build:base` 绿 → Harbor `geo/geo-collab-base:1.0.0` 存在。
  Expected: 构建成功、Harbor 有镜像。失败按日志修 Dockerfile.base / BASE_REGISTRY。

### Task 15: 打 `release-*` 首次全量发新栈

- [ ] **Step 1:（确认后）打 release tag**
  ```bash
  git tag release-1.0.0 && git push deploy release-1.0.0
  ```

- [ ] **Step 2: 观察全链路**
  `build:server` + `build:web` → `deploy`（skopeo 搬运 → ssh deploy.sh → migrate → up -d → 健康检查）全绿。

- [ ] **Step 3: 验证新栈运行、老栈无恙**
  Run（本地 ssh 只读）：
  ```powershell
  ssh -i C:/Users/19427/.ssh/id_rsa root@47.115.134.13 "docker ps --format '{{.Names}}\t{{.Status}}' | grep geo-ci; echo '---老栈---'; docker ps --format '{{.Names}}' | grep '^geo-app\|^geo-worker\|^geo-nginx'; echo '---访问---'; curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8081/"
  ```
  Expected: `geo-ci-*` 全 Up；老栈 `geo-app-1/geo-worker-1/geo-nginx-1` 仍在；`8081` 返回 200/302 等。

- [ ] **Step 4: 冒烟 + 记录**
  浏览器访问 `http://47.115.134.13:8081/` 登录（GEO_SEED_USERS）验证。把入口/版本记录到 `deploy/README.md`，commit。

---

## Self-Review 记录

- **Spec 覆盖**：§4 镜像分层→Task 1-3；§5 registry/skopeo→Task 4/8/6；§6 tag 映射→Task 8-9；§7 变量→Task 11；§8 接线→Task 9；§9 安全网（迁移熔断=deploy.sh set -e + migrate 独立；健康检查=Task 6 Step3 + Task 8 deploy；飞书=Task 8 after_script；回滚=pin 版本，README 记录）；§10 前置→Task 10-13；§1.1 非冲突→Task 4/5/13 全程 geo-ci 隔离。
- **回滚**：改 `deploy/VERSION` 旧版本重打 `server-<旧>`/`release-<旧>`；Harbor + 本地 registry 留旧版本层。alembic 不自动降级——回滚涉及 schema 变更需人工评估（写入 deploy/README.md）。
- **占位符扫描**：无 TBD/TODO；服务器敏感值走确认或用户填，非占位。
- **已知取舍**：新栈用 bind 挂载 `./data/`（非命名卷）实现与老栈隔离，与 spec §1.1「geo-ci_* 卷」等价（隔离目的一致），deploy.sh 已按 `./data/` 预建目录。
