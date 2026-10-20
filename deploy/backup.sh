#!/bin/sh
# Nightly backup, run by the `backup` service. Sleeps until BACKUP_AT
# (plant time), dumps the whole database in PostgreSQL's custom format and
# archives the attachments beside it (the `media` volume, at MEDIA_DIR),
# keeps BACKUP_KEEP_DAYS of them, and goes back to sleep.
#
# One backup is two files with one stamp:
#   erp_YYYY-MM-DD_HHMM.dump            the database
#   erp_YYYY-MM-DD_HHMM.media.tar.gz    every attached file
# The database says a file is attached; only the archive holds it.
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
MEDIA_DIR="${MEDIA_DIR:-/media}"

# Says why tonight's backup stopped, and stops it. Every step of dump() asks this by hand rather
# than leaning on `set -e`: the loop calls dump as `dump || ...`, and a shell ignores `set -e` in a
# function called that way, so a failed pg_dump went on to print "backup written" and the retention
# lines deleted every older backup with no new one made (O314).
#
# A failed night takes its own half-written files with it: kept, a disk-full failure added more
# every night until a later success swept them.
failed() {
    echo "$(date '+%F %T') BACKUP FAILED: $*; older backups kept" >&2
    if [ -n "${stamp:-}" ]; then
        rm -rf -- "${target}.partial" "${media}.partial" "${manifest}.partial" "${manifest}.unsorted.partial" \
            "${check}"
    fi
    return 1
}

dump() {
    stamp=$(date +%Y-%m-%d_%H%M)
    target="${BACKUP_DIR}/erp_${stamp}.dump"
    media="${BACKUP_DIR}/erp_${stamp}.media.tar.gz"
    manifest="${BACKUP_DIR}/erp_${stamp}.media.sha256"
    check="${BACKUP_DIR}/erp_${stamp}.check.partial"
    [ -d "$MEDIA_DIR" ] || { failed "no attachments directory at $MEDIA_DIR: mount the media volume"; return 1; }
    # The database first, then the files (O244): an attachment's file is written before its record is
    # committed, so every record the dump holds has its file by then, and the archive taken after it holds
    # them all. Each part stays .partial until all three are written, so a dump with no archive beside it is
    # never left looking complete.
    pg_dump --format=custom --no-owner --file="${target}.partial" "$DATABASE_URL" \
        || { failed "pg_dump exited $?"; return 1; }
    [ -s "${target}.partial" ] || { failed "pg_dump wrote nothing"; return 1; }
    tar -C "$MEDIA_DIR" -czf "${media}.partial" . || { failed "archiving the attachments exited $?"; return 1; }
    tar -tzf "${media}.partial" > /dev/null || { failed "the attachments archive does not read back"; return 1; }
    # What each archived file is, byte for byte, read from the archive itself: read from the live
    # directory, a scan uploaded between the two was listed and not archived, and the night's backup
    # could not be restored at all. The restore checks every file it puts back against it.
    # No pipe anywhere here: a pipe reports only its last stage, and a sha256sum that failed inside
    # one wrote a manifest of empty hashes that said "backup written" (review of O314). Each file's
    # hash is taken with its status asked, tar fails when one is not, and sort is a step of its own.
    tar -xzf "${media}.partial" --to-command='h=$(sha256sum) || exit 1; printf "%s  %s\n" "${h%% *}" "$TAR_FILENAME"' \
        > "${manifest}.unsorted.partial" || { failed "hashing the archived attachments exited $?"; return 1; }
    sort -k2 -o "${manifest}.partial" "${manifest}.unsorted.partial" || { failed "sorting the manifest exited $?"; return 1; }
    rm -f -- "${manifest}.unsorted.partial"
    # The night's set counts only once it verifies as the restore will check it: every archived file
    # put back and matched against the manifest, strictly.
    mkdir -p "$check" && tar -C "$check" -xzf "${media}.partial" \
        || { failed "the attachments archive does not unpack for checking"; return 1; }
    (cd "$check" && sha256sum --check --quiet --strict "${manifest}.partial") > /dev/null \
        || { failed "the manifest does not verify against the archive"; return 1; }
    rm -rf -- "$check"
    # Renamed only once complete, so a half-written file is never taken
    # for a backup.
    mv "${manifest}.partial" "$manifest" && mv "${media}.partial" "$media" && mv "${target}.partial" "$target" \
        || { failed "renaming tonight's files into place"; return 1; }
    echo "$(date '+%F %T') backup written: $target ($(du -h "$target" | cut -f1)), $media ($(du -h "$media" | cut -f1), $(tar -tzf "$media" | grep -vc '/$') files)"
    # Only now, with tonight's three files in place and verified, does anything older go.
    find "$BACKUP_DIR" -name 'erp_*.dump' -mtime +"$BACKUP_KEEP_DAYS" -delete
    find "$BACKUP_DIR" -name 'erp_*.media.tar.gz' -mtime +"$BACKUP_KEEP_DAYS" -delete
    find "$BACKUP_DIR" -name 'erp_*.media.sha256' -mtime +"$BACKUP_KEEP_DAYS" -delete
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
    # dump() has said why on stderr; the loop goes on to tomorrow's attempt.
    dump || true
done
