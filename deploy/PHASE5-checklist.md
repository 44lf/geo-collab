# Phase 5 Checklist —— Harbor + GitLab 变量

> 目标：让 CI 有地方推镜像（Harbor `agent-geo-collab` 项目）、有权限拉/推（robot 账号）、
> 有全部占位变量。做完这步，Phase 6-7 才能真跑。
>
> **真值（本环境）**：
> - Harbor 项目：`agent-geo-collab`
> - robot 账号：`robot$agent-geo-collab+geo-bot`
> - 镜像仓库前缀：`harbor.5518game.com:6443/agent-geo-collab/*`
> - CI 里一切都走变量 `$HARBOR_PROJECT` / `$HARBOR_USER`，不硬编码；下面把值填进 GitLab 变量即可。

## A. Harbor（`harbor.5518game.com:6443`）

- [x] **A1. 项目**已建：`agent-geo-collab`
- [x] **A2. robot 账号**已建：`robot$agent-geo-collab+geo-bot`（需对该项目有 **Push + Pull** 权限；确认勾了）
      → 其 token 填 GitLab 的 `HARBOR_TOKEN`
- [ ] **A3. 生成 `DOCKER_AUTH_CONFIG`**（kaniko/skopeo 拉推鉴权用）。在能跑 base64 的机器上：
  ```bash
  # 注意 robot 名含 $，用单引号
  echo -n 'robot$agent-geo-collab+geo-bot:<粘贴 robot token>' | base64 -w0
  ```
  得到 `<b64>` 后，`DOCKER_AUTH_CONFIG` 的值就是这段 JSON（压成一行）：
  ```json
  {"auths":{"harbor.5518game.com:6443":{"auth":"<b64>"}}}
  ```
- [ ] **A4. seed 工具镜像到 `agent-geo-collab` 项目**（runner 非特权、连不上公网 gcr/quay，
      得先把 kaniko/skopeo 放进内网 Harbor）。在一台**既能连公网、又能连 Harbor** 的机器上：
  ```bash
  docker login harbor.5518game.com:6443 -u 'robot$agent-geo-collab+geo-bot' -p '<token>'

  # kaniko
  docker pull gcr.io/kaniko-project/executor:v1.23.2-debug
  docker tag  gcr.io/kaniko-project/executor:v1.23.2-debug \
              harbor.5518game.com:6443/agent-geo-collab/kaniko-executor:v1.23.2-debug
  docker push harbor.5518game.com:6443/agent-geo-collab/kaniko-executor:v1.23.2-debug

  # skopeo —— 预装 ssh/curl（deploy job 要用；避免每次现装）
  cat > /tmp/skopeo.Dockerfile <<'EOF'
  FROM quay.io/skopeo/stable:latest
  RUN (microdnf install -y openssh-clients curl-minimal || dnf install -y openssh-clients curl) && \
      (microdnf clean all || dnf clean all) || true
  EOF
  docker build -t harbor.5518game.com:6443/agent-geo-collab/skopeo-stable:latest \
               -f /tmp/skopeo.Dockerfile /tmp
  docker push harbor.5518game.com:6443/agent-geo-collab/skopeo-stable:latest
  ```
  > 若这台机器也拉不到 gcr/quay，换可达的镜像源即可（kaniko 也有 dockerhub 镜像
  > `docker.io/kaniko-project/...` 的其它托管；告知我可帮换）。

## B. GitLab CI/CD 变量（项目 `geo/agent-geo-collab`，id 30 → Settings → CI/CD → Variables）

逐个 Add variable。**Masked** = 日志打码；**File** = 作为文件注入（SSH key 用 File）。

| Key | Value | 标记 |
|---|---|---|
| `HARBOR_REGISTRY` | `harbor.5518game.com:6443` | 普通 |
| `HARBOR_PROJECT` | `agent-geo-collab` | 普通 |
| `DOCKER_AUTH_CONFIG` | A3 那段 JSON | **Masked** |
| `HARBOR_USER` | `robot$agent-geo-collab+geo-bot` | 普通 |
| `HARBOR_TOKEN` | robot token | **Masked** |
| `DEPLOY_SSH_HOST` | `47.115.134.13` | 普通 |
| `DEPLOY_SSH_USER` | `root` | 普通 |
| `DEPLOY_SSH_KEY` | （Phase 6 生成的新栈专用私钥） | **File** |
| `DEPLOY_PATH` | `/root/geo-collab` | 普通 |
| `LOCAL_REGISTRY` | `127.0.0.1:5000` | 普通 |
| `BASE_REGISTRY` | `public.ecr.aws/docker/library` | 普通 |
| `GEO_FEISHU_WEBHOOK_URL` | 飞书告警 webhook | **Masked** |

> - **`DEPLOY_SSH_KEY` 先留空**——Phase 6 给新栈单独生成的专用私钥，那步再填。其余 11 个可现在配。
> - ⚠️ **别勾 "Protected"**：除非把 `base-*`/`server-*`/`web-*`/`release-*` tag 与 `feat/tag-driven-deploy`
>   分支都设成 protected ref，否则勾了 Protected 的变量在 tag 流水线里取不到 → 部署因缺变量失败。
>   保持"非 Protected"最省心。
> - `DOCKER_AUTH_CONFIG` 若 Masked 报"值不满足 masking 要求"（JSON 含特殊字符），可改用
>   "Masked and hidden" 关闭或换普通变量——它本身是 base64 凭据、别进日志即可。

## C. 快速自检（配完）

- [ ] Harbor `agent-geo-collab` 项目下能看到 `kaniko-executor` 和 `skopeo-stable` 两个 artifact。
- [ ] CI 变量列表里上面 11 个 key 都在（`DEPLOY_SSH_KEY` 待 Phase 6）。
- [ ] `HARBOR_PROJECT` 值确为 `agent-geo-collab`（镜像会推到 `harbor.../agent-geo-collab/geo-collab-*`）。

---

配完回一声，即进 **Phase 6**（服务器铺底，每步动手前确认）：生成专用 SSH key → 建
`/root/geo-collab` → 填 `.env` → 单起 `registry:2` 验证不扰老栈。
