#!/bin/sh
# 仅 API 进程负责数据库引导与种子数据，celery worker 共用同一镜像但跳过，避免并发 DDL 冲突
set -e

case "$1" in
  uvicorn)
    python -m app.db_bootstrap
    # 种子数据幂等（每步检查已存在），首次部署自动完成初始化
    python -m app.core.seed
    ;;
esac

exec "$@"
