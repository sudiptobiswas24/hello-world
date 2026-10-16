#!/bin/sh
# Restores a backup over the database and the attachments (the archive
# with the same stamp beside the dump, into MEDIA_DIR). Everything entered
# or attached since that backup is lost, so it asks first.
#
#   docker compose stop web
#   docker compose run --rm backup restore /backups/erp_2026-10-03_0200.dump
#   docker compose start web
set -eu

file="${1:?usage: restore.sh /backups/erp_YYYY-MM-DD_HHMM.dump}"
[ -f "$file" ] || { echo "no such file: $file" >&2; exit 1; }

pg_restore --list "$file" > /dev/null || { echo "$file is not a readable backup" >&2; exit 1; }

MEDIA_DIR="${MEDIA_DIR:-/media}"
media="${file%.dump}.media.tar.gz"
# Absolute: it is read from inside the unpacked files.
manifest="$(cd "$(dirname "$file")" && pwd)/$(basename "${file%.dump}").media.sha256"
staging="$MEDIA_DIR/.restoring.$$"
if [ -f "$media" ]; then
    tar -tzf "$media" > /dev/null || { echo "$media is not a readable archive" >&2; exit 1; }
    [ -d "$MEDIA_DIR" ] || { echo "no attachments directory at $MEDIA_DIR: mount the media volume" >&2; exit 1; }
    # Unpacked and checked before anything is touched: an archive whose files are not what the
    # manifest says they were leaves the database and the attachments as they are.
    rm -rf "$staging"
    mkdir "$staging"
    tar -C "$staging" -xzf "$media"
    if [ -f "$manifest" ]; then
        (cd "$staging" && sha256sum --check --quiet --strict "$manifest") >&2 || {
            rm -rf "$staging"
            echo "$media does not match $manifest: an attachment is not what was backed up; nothing changed" >&2
            exit 1
        }
    else
        echo "no manifest beside it ($manifest): the attachments' content is not checked" >&2
    fi
elif [ "${RESTORE_WITHOUT_MEDIA:-}" != "yes" ]; then
    echo "no attachments archive beside it ($media): every attached file would be left as it is now," >&2
    echo "not as it was. RESTORE_WITHOUT_MEDIA=yes restores the database alone." >&2
    exit 1
fi

if [ "${RESTORE_CONFIRM:-}" != "yes" ]; then
    printf 'This replaces EVERYTHING in the database with %s.\nType yes to go on: ' "$file"
    read -r answer
    [ "$answer" = "yes" ] || { rm -rf "$staging"; echo "nothing changed"; exit 1; }
fi

# The whole schema goes, not just what the backup holds: a table a later
# version added would otherwise outlive the restore and stop the old
# version's migrations. Dropping and reloading are one transaction, so a
# reload that fails leaves the database as it was, never empty.
{
    echo "SET client_min_messages = warning;"
    echo "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
    pg_restore --no-owner --file=- "$file" | grep -v '^CREATE SCHEMA public;$'
} | psql --quiet --no-psqlrc --set=ON_ERROR_STOP=1 --single-transaction "$DATABASE_URL" > /dev/null
echo "restored from $file"

if [ -f "$media" ]; then
    # Unpacked and checked beside the attachments first, then swapped in: an
    # archive that fails half way leaves the files as they were, not half of each.
    find "$MEDIA_DIR" -mindepth 1 -maxdepth 1 ! -name "$(basename "$staging")" -exec rm -rf {} +
    find "$staging" -mindepth 1 -maxdepth 1 -exec mv {} "$MEDIA_DIR"/ \;
    rmdir "$staging"
    echo "attachments restored from $media ($(find "$MEDIA_DIR" -type f | wc -l) files)"
fi
