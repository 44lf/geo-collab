#!/usr/bin/env python3
"""GitLab CI 手动 MinIO 缓存助手（fail-open）。

背景：本项目的 GitLab runner 未配 [runners.cache] 的 S3 后端（config.toml 不归我们管），
原生 cache 段永远 miss。此脚本绕开原生缓存，直接用 S3 协议把 uv 的 wheel 下载目录
（.uv-cache）在 MinIO 上跨流水线持久化，从而把「每条流水线重装依赖」降到近乎离线秒装。

用法：
    minio_cache.py get <object> <localpath>   # 拉缓存，成功 exit 0
    minio_cache.py put <object> <localpath>   # 传缓存（幂等建桶）

约定：任何失败（缺环境变量 / 连不上 / 对象不存在 / 桶不存在）一律静默 exit 1，
调用方用 `... || true` / `&& HIT=1 || HIT=0` 回落到「不缓存」，绝不拖垮流水线。

读取的环境变量：
    MINIO_CACHE_ENDPOINT   host:port，如 172.25.20.57:9000（非 URL，不带 http://）
    MINIO_CACHE_AK         access key（GitLab CI/CD 变量，masked）
    MINIO_CACHE_SK         secret key（GitLab CI/CD 变量，masked）
    MINIO_CACHE_BUCKET     桶名，默认 gitlab-runner-cache
    MINIO_CACHE_SECURE     "true" 走 HTTPS；其它 / 不设 = HTTP（内网默认）
"""
from __future__ import annotations

import os
import sys


def _client():
    from minio import Minio

    return Minio(
        os.environ["MINIO_CACHE_ENDPOINT"],
        access_key=os.environ["MINIO_CACHE_AK"],
        secret_key=os.environ["MINIO_CACHE_SK"],
        secure=os.environ.get("MINIO_CACHE_SECURE", "").lower() == "true",
    )


def main() -> int:
    op = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        obj, path = sys.argv[2], sys.argv[3]
    except IndexError:
        print("[minio_cache] 用法: minio_cache.py get|put <object> <localpath>", file=sys.stderr)
        return 2

    # 缺任一必要凭据 → 直接让位，让调用方走「不缓存」分支
    if not all(os.environ.get(k) for k in ("MINIO_CACHE_ENDPOINT", "MINIO_CACHE_AK", "MINIO_CACHE_SK")):
        print("[minio_cache] 未配置 MINIO_CACHE_* 凭据，跳过缓存", file=sys.stderr)
        return 1

    bucket = os.environ.get("MINIO_CACHE_BUCKET", "gitlab-runner-cache")
    try:
        c = _client()
        if op == "get":
            c.fget_object(bucket, obj, path)
        elif op == "put":
            if not c.bucket_exists(bucket):
                c.make_bucket(bucket)
            c.fput_object(bucket, obj, path)
        else:
            print(f"[minio_cache] 未知操作 {op!r}", file=sys.stderr)
            return 2
    except Exception as exc:  # noqa: BLE001 —— 有意吞掉一切，缓存永远不该拖垮 CI
        print(f"[minio_cache] {op} 跳过（{type(exc).__name__}: {exc}）", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
