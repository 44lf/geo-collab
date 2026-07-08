#!/usr/bin/env bash
# deploy.sh — 服务器端一键部署（前后端可单独部署，自动迁移表结构）
# 用法: bash deploy.sh [目标] [版本号]
#   目标:   all(默认) | server | web
#     server = 后端 app + worker（含 alembic 表结构迁移）
#     web    = 前端 nginx
#     all    = 全量（首次部署必须用 all）
#   版本号: 缺省按端读 VERSION；显式传入仅作用于本次部署的端
#
# 前置（服务器上，与本脚本同目录）:
#   1. Docker + Docker Compose v2 已安装
#   2. 对应镜像包就位:
#        geo-collab-server-<服务端版本>.tar.gz   (server / all)
#        geo-collab-web-<前端版本>.tar.gz        (web / all)
#        geo-dailyhot.tar.gz                      (all, 首次)
#   3. .env 已配置（参考 .env.example，所有 change_me 必须替换）
#
# 数据持久化: MySQL/MinIO/应用数据 bind 挂载在 ./data/ 下，升级镜像不丢。
#   ⚠️ 切勿 `docker compose ... down -v`，也不要删除 ./data/ 目录。

if [ -z "${BASH_VERSION:-}" ]; then exec bash "$0" "$@"; fi
set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${1:-all}"
VERSION_OVERRIDE="${2:-}"

case "$TARGET" in
  all|server|web) ;;
  *) echo "错误: 未知目标 '$TARGET'（可选 all|server|web）"; exit 1 ;;
esac

COMPOSE_FILE="$DEPLOY_DIR/docker-compose.prod.yml"
ENV_FILE="$DEPLOY_DIR/.env"
STATE_FILE="$DEPLOY_DIR/versions.env"
SERVER_IMAGE="geo-collab-server"
WEB_IMAGE="geo-collab-web"
DAILYHOT_IMAGE="geo-dailyhot"

IMAGE_SOURCE="${IMAGE_SOURCE:-tarball}"          # tarball（默认，docker load）| registry（docker pull 本地仓）
LOCAL_REGISTRY="${LOCAL_REGISTRY:-127.0.0.1:5000}"
HARBOR_PROJECT="${HARBOR_PROJECT:-geo}"

read_pkg_ver() {
  local key="$1" file="$DEPLOY_DIR/VERSION" v=""
  [[ -f "$file" ]] && v="$(grep -E "^${key}=" "$file" | head -n1 | cut -d= -f2- | tr -d '[:space:]' || true)"
  echo "$v"
}
read_state_ver() {
  local key="$1" v=""
  [[ -f "$STATE_FILE" ]] && v="$(grep -E "^${key}=" "$STATE_FILE" | head -n1 | cut -d= -f2- | tr -d '[:space:]' || true)"
  echo "$v"
}
# 在 DEPLOY_DIR 及 dist/ 子目录里找镜像包（本地直跑兼容）
find_tar() {
  local name="$1"
  for d in "$DEPLOY_DIR" "$DEPLOY_DIR/dist"; do
    [[ -f "$d/$name" ]] && { echo "$d/$name"; return 0; }
  done
  return 1
}

deploy_server=false; deploy_web=false
[[ "$TARGET" == "all" || "$TARGET" == "server" ]] && deploy_server=true
[[ "$TARGET" == "all" || "$TARGET" == "web" ]]    && deploy_web=true

# ── 确定两端最终版本 ─────────────────────────────────────────
if $deploy_server; then SERVER_VERSION="${VERSION_OVERRIDE:-$(read_pkg_ver SERVER)}"
else                    SERVER_VERSION="$(read_state_ver SERVER_VERSION)"; fi
if $deploy_web; then    WEB_VERSION="${VERSION_OVERRIDE:-$(read_pkg_ver WEB)}"
else                    WEB_VERSION="$(read_state_ver WEB_VERSION)"; fi

if [[ -z "$SERVER_VERSION" || -z "$WEB_VERSION" ]]; then
  echo "错误: 无法确定完整版本号 (后端=$SERVER_VERSION 前端=$WEB_VERSION)。"
  echo "      单端部署需 versions.env 已记录另一端；首次请执行: bash deploy.sh all"
  exit 1
fi

echo "==> 部署目标: $TARGET"
echo "==> 后端版本: $SERVER_VERSION  $($deploy_server && echo '(本次部署)' || echo '(保持运行)')"
echo "==> 前端版本: $WEB_VERSION  $($deploy_web && echo '(本次部署)' || echo '(保持运行)')"
echo "==> 工作目录: $DEPLOY_DIR"

# ── 必要文件检查 ─────────────────────────────────────────────
[[ -f "$COMPOSE_FILE" ]] || { echo "错误: 缺少 $COMPOSE_FILE"; exit 1; }
if [[ ! -f "$ENV_FILE" ]]; then
  if [[ -f "$DEPLOY_DIR/.env.example" ]]; then
    cp "$DEPLOY_DIR/.env.example" "$ENV_FILE"
    echo "警告: 未找到 .env，已从 .env.example 复制。"
    echo "      ⚠️  请编辑 .env 替换所有 change_me 后重新运行！"
    exit 1
  fi
  echo "错误: 缺少 .env（参考 .env.example）"; exit 1
fi

# ── .env 预检：常见致命配置 ──────────────────────────────────
ENV_MYSQL_USER="$(grep -E '^MYSQL_USER=' "$ENV_FILE" | head -n1 | cut -d= -f2- | tr -d '[:space:]' || true)"
if [[ "$ENV_MYSQL_USER" == "root" ]]; then
  echo "错误: .env 里 MYSQL_USER=root —— mysql 镜像禁止把 root 当作 MYSQL_USER，会崩溃重启。"
  echo "      请改成非 root 用户名（如 geo_user）。应用以该用户连库，自动获得对 MYSQL_DATABASE 的完整授权，无需 root。"
  exit 1
fi
if grep -qE '=change_me' "$ENV_FILE"; then
  echo "错误: .env 仍有未替换的 change_me 占位符，请先填入真实值。"
  grep -nE '=change_me' "$ENV_FILE" || true
  exit 1
fi

SERVER_TAR=""; WEB_TAR=""
if [[ "$IMAGE_SOURCE" != "registry" ]]; then
  if $deploy_server; then
    SERVER_TAR="$(find_tar "$SERVER_IMAGE-$SERVER_VERSION.tar.gz")" \
      || { echo "错误: 缺少后端镜像包 $SERVER_IMAGE-$SERVER_VERSION.tar.gz"; exit 1; }
  fi
  if $deploy_web; then
    WEB_TAR="$(find_tar "$WEB_IMAGE-$WEB_VERSION.tar.gz")" \
      || { echo "错误: 缺少前端镜像包 $WEB_IMAGE-$WEB_VERSION.tar.gz"; exit 1; }
  fi
fi

# 未部署的一端，其镜像必须已在本机
if ! $deploy_server && ! docker image inspect "$SERVER_IMAGE:$SERVER_VERSION" >/dev/null 2>&1; then
  echo "错误: 后端镜像 $SERVER_IMAGE:$SERVER_VERSION 不在本机，无法只部署前端。请执行 all。"; exit 1
fi
if ! $deploy_web && ! docker image inspect "$WEB_IMAGE:$WEB_VERSION" >/dev/null 2>&1; then
  echo "错误: 前端镜像 $WEB_IMAGE:$WEB_VERSION 不在本机，无法只部署后端。请执行 all。"; exit 1
fi

# ── 预创建数据目录 ───────────────────────────────────────────
echo ""
echo "==> 预创建数据目录 (./data/{mysql,minio,app})"
mkdir -p "$DEPLOY_DIR/data/mysql" "$DEPLOY_DIR/data/minio" "$DEPLOY_DIR/data/app"

# ── 加载镜像 ─────────────────────────────────────────────────
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
# dailyhot 基础设施镜像：首次（镜像不存在）时从包加载
if ! docker image inspect "$DAILYHOT_IMAGE:latest" >/dev/null 2>&1; then
  if DH_TAR="$(find_tar "$DAILYHOT_IMAGE.tar.gz")"; then
    echo ""; echo "==> 加载基础设施镜像 dailyhot"; docker load < "$DH_TAR"
  else
    echo "警告: $DAILYHOT_IMAGE:latest 不在本机且无 $DAILYHOT_IMAGE.tar.gz —— 热榜功能将不可用（首次请用 all 部署）"
  fi
fi

cd "$DEPLOY_DIR"
export SERVER_VERSION WEB_VERSION

# ── 表结构迁移（仅涉及后端时；统一单点执行，避免 app/worker 竞态）──
if $deploy_server; then
  echo ""
  echo "==> 迁移数据库表结构 (alembic upgrade head)"
  docker compose -f "$COMPOSE_FILE" run --rm migrate
fi

# ── 持久化版本状态 ───────────────────────────────────────────
printf 'SERVER_VERSION=%s\nWEB_VERSION=%s\n' "$SERVER_VERSION" "$WEB_VERSION" > "$STATE_FILE"

# ── 启动 / 更新服务 ──────────────────────────────────────────
echo ""
echo "==> 启动服务 (仅重建版本变更的服务)"
docker compose -f "$COMPOSE_FILE" up -d --remove-orphans

echo ""
docker compose -f "$COMPOSE_FILE" ps

HTTP_PORT="$(grep -E '^HTTP_PORT=' "$ENV_FILE" | cut -d= -f2- | tr -d '[:space:]' || true)"
HTTP_PORT="${HTTP_PORT:-80}"
HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}' || echo '<server-ip>')"

echo ""
echo "==> 部署完成!"
echo "    前端访问: http://${HOST_IP}:${HTTP_PORT}/"
echo "    后端 API: http://${HOST_IP}:${HTTP_PORT}/api/"
echo ""
echo "    查看日志: docker compose -f $COMPOSE_FILE logs -f"
echo "    停止服务: docker compose -f $COMPOSE_FILE down   (注意: 不要加 -v，会删数据)"

echo ""; echo "==> 健康检查（本机 http://127.0.0.1:$HTTP_PORT/）"
HTTP_PORT="$(grep -E '^HTTP_PORT=' "$ENV_FILE" | cut -d= -f2- | tr -d '[:space:]' || true)"; HTTP_PORT="${HTTP_PORT:-8081}"
ok=0
for i in $(seq 1 20); do
  code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$HTTP_PORT/" || true)"
  if [[ "$code" =~ ^(200|301|302|401|403)$ ]]; then ok=1; echo "健康检查通过（HTTP $code）"; break; fi
  sleep 3
done
[[ "$ok" == 1 ]] || { echo "❌ 健康检查失败（端口 $HTTP_PORT 无响应）"; exit 2; }
