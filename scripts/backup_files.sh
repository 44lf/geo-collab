#!/bin/bash
# Geo 协作平台 — 文件系统数据备份脚本
#
# 备份内容：
#   1. app_data volume (assets / browser_states / exports / logs)
#   2. minio_data volume (素材图库对象存储)
#
# 不在此脚本范围内：
#   - MySQL 数据 → 用 backup_db.sh
#
# 用法：
#   bash scripts/backup_files.sh                                 # 全部备份
#   BACKUP_SKIP_MINIO=1 bash scripts/backup_files.sh             # 跳过 minio
#   BACKUP_SKIP_BROWSER_PROFILES=1 bash scripts/backup_files.sh  # 跳过 chromium profile（只保留登录态 json）
#
# 注意：
#   - 通过临时 busybox 容器挂载 named volume 来 tar，不依赖宿主机能直接看到数据
#   - browser_states/profile/ 子目录较大且发布期间可能被 Playwright 持有，
#     可通过 BACKUP_SKIP_BROWSER_PROFILES=1 只备份 storage_state.json（登录态核心）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

BACKUP_DIR="$REPO_ROOT/backups"
LOG_FILE="$BACKUP_DIR/backup.log"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-3}"

# Docker compose 项目名（默认是 REPO_ROOT 的目录名）
PROJECT_NAME="${COMPOSE_PROJECT_NAME:-$(basename "$REPO_ROOT")}"
APP_VOLUME="${PROJECT_NAME}_app_data"
MINIO_VOLUME="${PROJECT_NAME}_minio_data"

mkdir -p "$BACKUP_DIR"

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $*" | tee -a "$LOG_FILE"
}

# ── 检查 volume 存在 ──
check_volume() {
    local vol="$1"
    if ! docker volume inspect "$vol" >/dev/null 2>&1; then
        echo "❌ Docker volume '$vol' 不存在" >&2
        echo "   如果 compose 项目名不是 '$PROJECT_NAME'，请设置 COMPOSE_PROJECT_NAME 环境变量" >&2
        echo "   当前可见 volume：" >&2
        docker volume ls --format '   - {{.Name}}' >&2
        exit 1
    fi
}

# ── 备份 app_data ──
backup_app_data() {
    local target="$BACKUP_DIR/app_data_${TIMESTAMP}.tar.gz"
    local exclude_args=()

    if [[ "${BACKUP_SKIP_BROWSER_PROFILES:-0}" == "1" ]]; then
        # 跳过 chromium profile 目录，只保留 storage_state.json 和其他配置
        # 深度无关 + 不锚定：profile 实际埋在 browser_states/users/<uid>/<平台>/<hash>/profile（4 层深），
        # 且归档成员名带 ./ 前缀，旧的 'browser_states/*/*/profile' 层数写死+锚定 → 永不命中（见 issue）
        exclude_args=(--exclude='*/profile' --exclude='*/profile/*')
        log "▶ app_data: 跳过 chromium profile 目录（只保留登录态 storage_state.json）"
    fi

    log "▶ app_data: 开始备份 → $target"

    docker run --rm \
        -v "${APP_VOLUME}:/source:ro" \
        -v "${BACKUP_DIR}:/backup" \
        busybox \
        tar -czf "/backup/app_data_${TIMESTAMP}.tar.gz" \
            "${exclude_args[@]}" \
            -C /source .

    if ! gzip -t "$target" 2>/dev/null; then
        log "❌ app_data 备份损坏，已删除"
        rm -f "$target"
        return 1
    fi

    local size
    size="$(du -h "$target" | cut -f1)"
    log "✓ app_data 备份完成：$target ($size)"
}

# ── 备份 minio_data ──
backup_minio_data() {
    local target="$BACKUP_DIR/minio_data_${TIMESTAMP}.tar.gz"
    log "▶ minio_data: 开始备份 → $target"

    # MinIO 运行时会在 .minio.sys/tmp/.trash/ 下持续新建 / 删除临时文件，tar 读取
    # 期间这些文件可能消失 → tar 以非零码退出（"file changed as we read it"）。
    #   ① 排除易变的 .minio.sys/tmp（纯 scratch，无需备份）消除主要竞态；
    #   ② tar 的非零退出不再直接 abort（`|| rc=$?` 关掉 set -e），改由 gzip 完整性
    #      校验作为唯一成败判据——否则一次良性竞态就会中止整个脚本、连带跳过末尾的
    #      过期清理，导致旧备份永远堆积（见 issue：07-04 起 minio tar 每天报错、清理连跳）。
    local rc=0
    docker run --rm \
        -v "${MINIO_VOLUME}:/source:ro" \
        -v "${BACKUP_DIR}:/backup" \
        busybox \
        tar -czf "/backup/minio_data_${TIMESTAMP}.tar.gz" \
            --exclude='./.minio.sys/tmp' \
            --exclude='./.minio.sys/tmp/*' \
            -C /source . || rc=$?

    if [[ "$rc" -ne 0 ]]; then
        log "⚠ minio_data: tar 返回码 $rc（多为运行时临时文件消失，非致命），以 gzip 校验为准"
    fi

    if ! gzip -t "$target" 2>/dev/null; then
        log "❌ minio_data 备份损坏，已删除"
        rm -f "$target"
        return 1
    fi

    local size
    size="$(du -h "$target" | cut -f1)"
    log "✓ minio_data 备份完成：$target ($size)"
}

# ── 主流程 ──
# 关键：单个备份失败绝不能 abort 掉后面的过期清理，否则旧备份会无限堆积撑爆磁盘。
# 用 `|| log` 兜住每个备份步骤的失败（set -e 在 `||` 左侧自动失效），保证末尾的清理必然执行。
check_volume "$APP_VOLUME"
backup_app_data || log "❌ app_data 备份失败（不中止，继续执行过期清理）"

if [[ "${BACKUP_SKIP_MINIO:-0}" != "1" ]]; then
    check_volume "$MINIO_VOLUME"
    backup_minio_data || log "❌ minio_data 备份失败（不中止，继续执行过期清理）"
else
    log "▶ 跳过 minio_data 备份（BACKUP_SKIP_MINIO=1）"
fi

# ── 清理过期备份 ──
DELETED=$(find "$BACKUP_DIR" \( -name 'app_data_*.tar.gz' -o -name 'minio_data_*.tar.gz' \) \
    -mtime "+${KEEP_DAYS}" -print -delete | wc -l)
log "✓ 清理 ${DELETED} 个超过 ${KEEP_DAYS} 天的旧文件备份"
