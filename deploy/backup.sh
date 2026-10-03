#!/bin/sh
# Nightly backup, run by the `backup` service. Sleeps until BACKUP_AT
# (plant time), dumps the whole database in PostgreSQL's custom format,
# keeps BACKUP_KEEP_DAYS of them, and goes back to sleep.
#
#   docker compose run --rm backup now                 a backup this minute
#   docker compose run --rm backup restore <file>      see restore.sh
#
# A backup on the same disk as the database survives a bad import or a
# dropped table. It does not survive the disk. Copy /backups off the
# machine; see RUNBOOK.md.
set -eu

BACKUP_AT="${BACKUP_AT:-02:00}"
BACKUP_KEEP_DAYS="${BACKUP_KEEP_DAYS:-30}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"

dump() {
    stamp=$(date +%Y-%m-%d_%H%M)
    target="${BACKUP_DIR}/erp_${stamp}.dump"
    pg_dump --format=custom --no-owner --file="${target}.partial" "$DATABASE_URL"
    # Renamed only once complete, so a half-written file is never taken
    # for a backup.
    mv "${target}.partial" "$target"
    echo "$(date '+%F %T') backup written: $target ($(du -h "$target" | cut -f1))"
    find "$BACKUP_DIR" -name 'erp_*.dump' -mtime +"$BACKUP_KEEP_DAYS" -delete
    find "$BACKUP_DIR" -name '*.partial' -mmin +60 -delete
}

case "${1:-}" in
    now) dump; exit 0 ;;
    restore) shift; exec /deploy/restore.sh "$@" ;;
    "") ;;
    *) echo "usage: backup.sh [now | restore /backups/<file>.dump]" >&2; exit 2 ;;
esac

while true; do
    now=$(date +%s)
    next=$(date -d "today $BACKUP_AT" +%s)
    [ "$next" -le "$now" ] && next=$(date -d "tomorrow $BACKUP_AT" +%s)
    echo "$(date '+%F %T') next backup at $(date -d "@$next" '+%F %T')"
    sleep $((next - now))
    dump || echo "$(date '+%F %T') BACKUP FAILED" >&2
done
