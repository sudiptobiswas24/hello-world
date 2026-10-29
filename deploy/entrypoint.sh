#!/bin/sh
# Starts the web process: wait for the database, bring its schema up to
# the code, record the posted figures older documents lack (only the
# first start after an update finds any), publish the stylesheets,
# refresh the role groups. A one-off
# command (`docker compose run --rm web python manage.py ...`) skips
# all of that and runs against the database as it stands.
#
# The schema is brought up as the role that owns the tables (DATABASE_OWNER_URL): the application itself
# connects as a role that owns nothing (DATABASE_URL), so it cannot alter a table, disable the books' change
# log's triggers or touch the log (the audit trail, Companies (Accounts) Rules 2014, rule 3(1)). The migrate
# makes that role if it is missing and gives it rows to read and write, and no more (apps/core/ledger_log.py).
set -e

python deploy/wait_for_db.py

if [ "$1" = "gunicorn" ]; then
    ERP_DATABASE_AS_OWNER=1 python manage.py migrate --noinput
    python manage.py record_posted_totals
    python manage.py collectstatic --noinput --verbosity 0
    python manage.py setup_roles --verbosity 0
fi

exec "$@"
