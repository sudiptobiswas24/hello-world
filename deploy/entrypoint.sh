#!/bin/sh
# Starts the web process: wait for the database, bring its schema up to
# the code, record the posted figures older documents lack (only the
# first start after an update finds any), publish the stylesheets,
# refresh the role groups. A one-off
# command (`docker compose run --rm web python manage.py ...`) skips
# all of that and runs against the database as it stands.
set -e

python deploy/wait_for_db.py

if [ "$1" = "gunicorn" ]; then
    python manage.py migrate --noinput
    python manage.py record_posted_totals
    python manage.py collectstatic --noinput --verbosity 0
    python manage.py setup_roles --verbosity 0
fi

exec "$@"
