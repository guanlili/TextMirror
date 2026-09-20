#!/usr/bin/env bash
# TextMirror PostgreSQL 定时备份脚本
# 用法：crontab -e → 0 3 * * * /path/to/backup_db.sh
# 默认保留最近 7 天备份，可通过 BACKUP_RETAIN_DAYS 环境变量覆盖
set -euo pipefail

CONTAINER="${PG_CONTAINER:-textmirror-postgres}"
DB_USER="${POSTGRES_USER:-textmirror}"
DB_NAME="${POSTGRES_DB:-textmirror}"
BACKUP_DIR="${BACKUP_DIR:-$(dirname "$0")/../data/backups}"
RETAIN_DAYS="${BACKUP_RETAIN_DAYS:-7}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/textmirror_${TIMESTAMP}.sql.gz"

mkdir -p "$BACKUP_DIR"

echo "[$(date '+%F %T')] Starting backup → $BACKUP_FILE"

docker exec "$CONTAINER" pg_dump -U "$DB_USER" -d "$DB_NAME" --format=plain | gzip > "$BACKUP_FILE"

SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
echo "[$(date '+%F %T')] Backup complete: $BACKUP_FILE ($SIZE)"

# 清理过期备份
DELETED=$(find "$BACKUP_DIR" -name "textmirror_*.sql.gz" -mtime "+${RETAIN_DAYS}" -delete -print | wc -l)
if [ "$DELETED" -gt 0 ]; then
  echo "[$(date '+%F %T')] Cleaned $DELETED backup(s) older than ${RETAIN_DAYS} days"
fi
