#!/bin/sh
# Restores a backup over the database. Everything entered since that
# backup is lost, so it asks first.
#
#   docker compose stop web
#   docker compose run --rm backup restore /backups/erp_2026-10-03_0200.dump
#   docker compose start web
set -eu

file="${1:?usage: restore.sh /backups/erp_YYYY-MM-DD_HHMM.dump}"
[ -f "$file" ] || { echo "no such file: $file" >&2; exit 1; }

pg_restore --list "$file" > /dev/null || { echo "$file is not a readable backup" >&2; exit 1; }

if [ "${RESTORE_CONFIRM:-}" != "yes" ]; then
    printf 'This replaces EVERYTHING in the database with %s.\nType yes to go on: ' "$file"
    read -r answer
    [ "$answer" = "yes" ] || { echo "nothing changed"; exit 1; }
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
