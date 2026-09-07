#!/bin/sh
# 仅 API 进程负责数据库引导，celery worker 共用同一镜像但跳过，避免并发 DDL 冲突
set -e

case "$1" in
  uvicorn)
    python -m app.db_bootstrap
    ;;
esac

exec "$@"
