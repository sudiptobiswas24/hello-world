#!/bin/bash
# One lane of the release gate, run as its own background job.
#
#   scripts/gate/lane.sh <prefix> ui|sqlite|ist|pg-a|pg-b|pg-c|mig
#
# GATE_TREE  the worktree to gate (default: this repository's root). Keep it
#            clean and do not edit it while a lane runs.
# GATE_LOGS  where logs go (default: /tmp/gate-logs).
#
# A background job may run two hours at most, and a lane killed at the limit
# has no verdict. Each lane runs two workers on a four-core machine, so run
# two lanes at a time, never more: five at once (g25) ran every one past the
# limit. Beside one other lane, sqlite and ist take about 100 minutes each
# and pg-a about 65. Order, as two slots:
#   slot 1: mig, then pg-c
#   slot 2: ui, then pg-a, sqlite, pg-b, ist
# Start mig at minute zero, detached: it is the longest lane (over three
# hours in g31) and builds nothing the others need. ui must still finish
# before sqlite or ist start: it builds the office app the browser tests
# need. g31 ran ui alone and mig last and took eight and a half hours;
# this order keeps two lanes busy throughout, about five.
# mig counts as a lane. g26 ran sqlite beside ist and mig: sqlite passed
# its own 9000-second limit twice with no verdict, and each rerun cost two
# hours. So mig runs beside at most one other lane.
# A lane run detached (setsid nohup) is not a job the session tracks, and a
# session with no tracked job running is reclaimed with its container: g26
# lost three detached lanes that way. Keep a tracked watcher job running for
# as long as any lane runs:
#   timeout 7000 bash -c 'while pgrep -f "[l]ane.sh <prefix>" >/dev/null; do sleep 60; done'
# and start another when it ends. The bracket matters: unbracketed, the
# pattern matches the watcher's own bash -c line, so it never sees the lane
# end and runs to its timeout (g28 missed pg-a's finish by 40 minutes). Give that job the Bash tool's own timeout
# of 7200000 too: its default ends the job at 30 minutes (g27).
# A test lane refuses to start without the built office app, or while a ui
# lane is building it: g27 started sqlite beside ui, the build emptied
# apps/web/static/web as sqlite began, and 159 browser tests skipped under
# an OK (400 skipped, not 241). Read the skip count, not only the OK.
# A lane run again keeps the last run's log as <log>.prev: read it first. g26
# reran sqlite before reading a line its timed-out log had, and lost it.
# Never edit this file while a lane is running from it: bash reads a
# script as it runs, and the lane then executes shifted lines.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
TREE="${GATE_TREE:-$(cd "$HERE/../.." && pwd)}"
LOGS="${GATE_LOGS:-/tmp/gate-logs}"
mkdir -p "$LOGS"
cd "$TREE" || exit 2
P=$1
PW="PLAYWRIGHT_CHROMIUM_EXECUTABLE=/opt/pw-browsers/chromium"
echo "HEAD $(git rev-parse HEAD) dirty=$(git status --short | wc -l) lane=$2"
built() {  # a test lane needs the office app built, and not being rebuilt under it
    if pgrep -f "lane.sh [^ ]+ ui$" > /dev/null; then
        echo "$1: REFUSED, a ui lane is building the office app; start $1 when it has finished"; exit 3
    fi
    if [ ! -f apps/web/static/web/index.html ]; then
        echo "$1: REFUSED, the office app is not built; run the ui lane first"; exit 3
    fi
}
keep() { [ -f "$1" ] && mv -f "$1" "$1.prev"; true; }  # keep <log>: the last run's log, read before it is lost

pg_half() {  # pg_half <A|B> <database name> <labels...>
    local half=$1 db=$2; shift 2
    built "pg-$half"
    pg_isready -q || service postgresql start > /dev/null
    # Always fresh: a run killed mid-test leaves rows a kept database hands the next run.
    for n in "" _1 _2 _3 _4; do
        su postgres -c "psql -q -c 'DROP DATABASE IF EXISTS test_$db$n WITH (FORCE)'" 2>&1 | grep -v NOTICE
    done
    keep "$LOGS/${P}_pg_$half.log"
    timeout 12000 env $PW DATABASE_URL="postgres://erp:erp@localhost:5432/$db" .venv/bin/python manage.py test "$@" \
        --parallel 2 --keepdb --noinput --exclude-tag migration > "$LOGS/${P}_pg_$half.log" 2>&1
    echo "pg $half exit=$?"; grep -E "^Found|^Ran|^OK|^FAILED" "$LOGS/${P}_pg_$half.log"
}

case "$2" in
ui)
    {
        echo "HEAD: $(git rev-parse HEAD) dirty=$(git status --short | wc -l)"
        echo "UI:"; (cd frontend && npx tsc -b && npx vite build > /dev/null && npx vitest run 2>&1 | grep -E "Tests |Test Files") || echo "UI FAILED"
        python3 "$HERE/names.py" apps/core/tests_roles.py apps/core/tests_settings.py
        python3 "$HERE/registercheck.py" origin/claude/erp-creation-guidance-ox5afo HEAD || echo "REGISTER FAILED"
        echo "AUDIT:"; .venv/bin/python manage.py audit_invariants 2>&1 | tail -3
        .venv/bin/python manage.py makemigrations --check --dry-run 2>&1 | tail -2
    } > "$LOGS/gate_${P}.out" 2>&1
    cat "$LOGS/gate_${P}.out"
    ;;
sqlite)
    built sqlite
    keep "$LOGS/${P}_suite.log"
    timeout 9000 env $PW .venv/bin/python manage.py test apps --exclude-tag migration --parallel 2 > "$LOGS/${P}_suite.log" 2>&1
    rc=$?; echo "sqlite exit=$rc"; [ $rc = 124 ] && echo "sqlite: TIMED OUT, no verdict"; grep -E "^Found|^Ran|^OK|^FAILED" "$LOGS/${P}_suite.log"
    ;;
ist)
    built ist
    keep "$LOGS/${P}_ist.log"
    timeout 9000 env $PW DJANGO_TIME_ZONE=Asia/Kolkata PYTHONPATH="$HERE" DJANGO_SETTINGS_MODULE=fastsettings \
        .venv/bin/python manage.py test apps --parallel 2 --exclude-tag migration --settings=fastsettings > "$LOGS/${P}_ist.log" 2>&1
    rc=$?; echo "ist exit=$rc"; [ $rc = 124 ] && echo "ist: TIMED OUT, no verdict"; grep -E "^Found|^Ran|^OK|^FAILED" "$LOGS/${P}_ist.log"
    ;;
pg-a)
    pg_half A erp apps.sales apps.purchasing apps.accounting apps.gst apps.core
    ;;
pg-b)
    pg_half B erp2 apps.manufacturing apps.inventory apps.hr apps.planning
    ;;
pg-c)
    # Split from pg-b in g25: 4,471 tests at two workers ran past the two-hour limit beside one other lane.
    pg_half C erp3 apps.quality apps.assets apps.imports apps.e2e apps.web
    ;;
mig)
    # One app at a time on two workers (O429). Whole, the lane took about four hours: past the two-hour job
    # limit, and past the container's uptime three times in g25. Three workers outgrew the memory limit (one
    # killed at 5.5 GB, the runner then waiting for it with no CPU). Run this lane detached
    # (setsid nohup ... &) and run it again after any restart: an app that passed on this HEAD is not re-run.
    # The tests SQLite skips are PostgreSQL's own and run there after: before g25 one had run in no gate.
    M="$LOGS/${P}_mig"; mkdir -p "$M"; HEAD=$(git rev-parse HEAD)
    .venv/bin/python scripts/gate/migcount.py > "$M/count.txt" || { echo "mig: counting failed"; exit 1; }
    .venv/bin/python scripts/gate/migcount.py --skips > "$M/skips.txt" || { echo "mig: listing skips failed"; exit 1; }
    for app in $(grep -v '^total ' "$M/count.txt" | cut -d' ' -f1); do
        if [ "$(cat "$M/$app.done" 2>/dev/null)" = "$HEAD" ]; then echo "$app passed already on this HEAD"; continue; fi
        timeout 7000 env $PW .venv/bin/python manage.py test "apps.$app" --tag migration --parallel 2 \
            > "$M/$app.log" 2>&1
        rc=$?; echo "$app exit=$rc $(grep -E '^Ran|^OK|^FAILED' "$M/$app.log" | tr '\n' ' ')"
        [ $rc = 0 ] && echo "$HEAD" > "$M/$app.done"
    done
    rm -f "$M/pg.log"
    if [ -s "$M/skips.txt" ]; then
        if [ "$(cat "$M/pg.done" 2>/dev/null)" != "$HEAD" ]; then
            pg_isready -q || service postgresql start > /dev/null
            su postgres -c "psql -q -c 'DROP DATABASE IF EXISTS test_erp9 WITH (FORCE)'" 2>&1 | grep -v NOTICE
            timeout 3000 env DATABASE_URL="postgres://erp:erp@localhost:5432/erp9" .venv/bin/python manage.py test \
                $(cat "$M/skips.txt") --tag migration --noinput > "$M/pg.run.log" 2>&1
            rc=$?; [ $rc = 0 ] && echo "$HEAD" > "$M/pg.done"
            echo "PostgreSQL-only exit=$rc $(grep -E '^Ran|^OK|^FAILED' "$M/pg.run.log" | tr '\n' ' ')"
        fi
    fi
    echo "mig finished"
    ;;
*)
    echo "unknown lane: $2"; exit 2
    ;;
esac
