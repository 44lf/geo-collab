#!/usr/bin/env bash
# build.sh — 构建 Geo Collab 镜像并打包部署产物
# 用法: bash deploy/build.sh [目标] [版本号]
#   目标:   all(默认) | base | server | web
#     server = 后端镜像 geo-collab-server（app + worker 共用，含迁移）
#     web    = 前端镜像 geo-collab-web（nginx 静态 + 反代）
#     all    = server + web + dailyhot 基础设施镜像 + 部署包
#   版本号: 缺省按端读 deploy/VERSION（SERVER= / WEB=）；显式传入仅作用于本次构建的端
# 示例:
#   bash deploy/build.sh                 # 全量，版本取自 VERSION
#   bash deploy/build.sh web             # 只出前端更新包
#   bash deploy/build.sh server 1.2.0    # 只出后端，版本指定 1.2.0
#
# 环境变量:
#   DEPLOY_HOST / DEPLOY_DIR  设置后自动 scp 产物到服务器（DEPLOY_DIR 默认 /data/geo-collab）

if [ -z "${BASH_VERSION:-}" ]; then exec bash "$0" "$@"; fi
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"

TARGET="${1:-all}"
VERSION_OVERRIDE="${2:-}"

case "$TARGET" in
  all|base|server|web) ;;
  *) echo "错误: 未知目标 '$TARGET'（可选 all|base|server|web）"; exit 1 ;;
esac

read_ver() {
  local key="$1" file="$SCRIPT_DIR/VERSION" v=""
  [[ -f "$file" ]] && v="$(grep -E "^${key}=" "$file" | head -n1 | cut -d= -f2- | tr -d '[:space:]' || true)"
  echo "${v:-latest}"
}

BASE_VERSION="$(read_ver BASE)"
SERVER_VERSION="$(read_ver SERVER)"
WEB_VERSION="$(read_ver WEB)"
if [[ -n "$VERSION_OVERRIDE" ]]; then
  [[ "$TARGET" == "all" || "$TARGET" == "server" ]] && SERVER_VERSION="$VERSION_OVERRIDE"
  [[ "$TARGET" == "all" || "$TARGET" == "web" ]]    && WEB_VERSION="$VERSION_OVERRIDE"
fi

OUT_DIR="$SCRIPT_DIR/dist"
SERVER_IMAGE="geo-collab-server"
WEB_IMAGE="geo-collab-web"
DAILYHOT_IMAGE="geo-dailyhot"

build_server=false; build_web=false
[[ "$TARGET" == "all" || "$TARGET" == "server" ]] && build_server=true
[[ "$TARGET" == "all" || "$TARGET" == "web" ]]    && build_web=true

echo "==> 目标:     $TARGET"
$build_server && echo "==> 后端版本: $SERVER_VERSION"
$build_web    && echo "==> 前端版本: $WEB_VERSION"
echo "==> 输出目录: $OUT_DIR"
mkdir -p "$OUT_DIR"

ARTIFACTS=()

# ── 基础镜像（浏览器自动化栈 + 依赖，慢变）───────────────────
if [[ "$TARGET" == "all" || "$TARGET" == "base" ]]; then
  echo ""; echo "==> 构建基础镜像 geo-collab-base:$BASE_VERSION"
  docker build --platform linux/amd64 \
    -t "geo-collab-base:$BASE_VERSION" -t "geo-collab-base:latest" \
    -f "$ROOT/Dockerfile.base" "$ROOT"
fi

# ── 后端镜像（app + worker 共用）─────────────────────────────
if $build_server; then
  echo ""
  echo "==> 构建后端镜像 $SERVER_IMAGE:$SERVER_VERSION"
  docker build --platform linux/amd64 \
    --build-arg BASE_IMAGE="geo-collab-base:$BASE_VERSION" \
    -t "$SERVER_IMAGE:$SERVER_VERSION" -t "$SERVER_IMAGE:latest" \
    -f "$ROOT/Dockerfile" "$ROOT"
  TAR="$OUT_DIR/$SERVER_IMAGE-$SERVER_VERSION.tar.gz"
  echo "==> 导出 -> $(basename "$TAR")"
  docker save "$SERVER_IMAGE:$SERVER_VERSION" | gzip > "$TAR"
  echo "    $(basename "$TAR")  $(du -sh "$TAR" | cut -f1)"
  ARTIFACTS+=("$TAR")
fi

# ── 前端镜像（nginx）─────────────────────────────────────────
if $build_web; then
  echo ""
  echo "==> 构建前端镜像 $WEB_IMAGE:$WEB_VERSION"
  docker build --platform linux/amd64 \
    -t "$WEB_IMAGE:$WEB_VERSION" -t "$WEB_IMAGE:latest" \
    -f "$ROOT/Dockerfile.nginx" "$ROOT"
  TAR="$OUT_DIR/$WEB_IMAGE-$WEB_VERSION.tar.gz"
  echo "==> 导出 -> $(basename "$TAR")"
  docker save "$WEB_IMAGE:$WEB_VERSION" | gzip > "$TAR"
  echo "    $(basename "$TAR")  $(du -sh "$TAR" | cut -f1)"
  ARTIFACTS+=("$TAR")
fi

# ── 基础设施镜像 dailyhot（仅 all，变动极少；用 latest 标签）──
if [[ "$TARGET" == "all" ]]; then
  echo ""
  echo "==> 构建基础设施镜像 $DAILYHOT_IMAGE:latest"
  docker build --platform linux/amd64 \
    -t "$DAILYHOT_IMAGE:latest" "$ROOT/services/dailyhot-api"
  TAR="$OUT_DIR/$DAILYHOT_IMAGE.tar.gz"
  echo "==> 导出 -> $(basename "$TAR")"
  docker save "$DAILYHOT_IMAGE:latest" | gzip > "$TAR"
  echo "    $(basename "$TAR")  $(du -sh "$TAR" | cut -f1)"
  ARTIFACTS+=("$TAR")
fi

# ── 打包部署文件（compose + deploy.sh + .env.example + VERSION）──
echo ""
echo "==> 打包部署包"
STAGE_DIR="$(mktemp -d)"
trap 'rm -rf "$STAGE_DIR"' EXIT
cp "$SCRIPT_DIR/docker-compose.prod.yml" "$STAGE_DIR/"
cp "$SCRIPT_DIR/deploy.sh"               "$STAGE_DIR/"
cp "$SCRIPT_DIR/.env.example"            "$STAGE_DIR/"
[[ -f "$SCRIPT_DIR/README.md" ]] && cp "$SCRIPT_DIR/README.md" "$STAGE_DIR/"
printf 'SERVER=%s\nWEB=%s\n' "$SERVER_VERSION" "$WEB_VERSION" > "$STAGE_DIR/VERSION"

PKG_TAG="$TARGET"
[[ "$TARGET" == "all" ]] && PKG_TAG="$SERVER_VERSION"
DEPLOY_PKG="$OUT_DIR/geo-collab-deploy-$PKG_TAG.tar.gz"
tar -czf "$DEPLOY_PKG" -C "$STAGE_DIR" .
ARTIFACTS+=("$DEPLOY_PKG")

echo ""
echo "==> 构建完成! 输出文件:"
ls -lh "${ARTIFACTS[@]}"

# ── 可选：上传到服务器 ───────────────────────────────────────
if [[ -n "${DEPLOY_HOST:-}" ]]; then
  REMOTE_DIR="${DEPLOY_DIR:-/data/geo-collab}"
  echo ""
  echo "==> 上传至 $DEPLOY_HOST:$REMOTE_DIR"
  ssh "$DEPLOY_HOST" "mkdir -p $REMOTE_DIR"
  scp "${ARTIFACTS[@]}" "$DEPLOY_HOST:$REMOTE_DIR/"
  echo ""
  echo "==> 上传完成! 登录服务器后执行:"
  echo "  cd $REMOTE_DIR && tar xzf $(basename "$DEPLOY_PKG") && bash deploy.sh $TARGET"
fi
