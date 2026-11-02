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
# and pg-a about 65. Run ui first: it builds the office app the browser
# tests need. Then sqlite with pg-a, ist with pg-b, then pg-c, then mig
# alone (three workers).
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

pg_half() {  # pg_half <A|B> <database name> <labels...>
    local half=$1 db=$2; shift 2
    pg_isready -q || service postgresql start > /dev/null
    # Always fresh: a run killed mid-test leaves rows a kept database hands the next run.
    for n in "" _1 _2 _3 _4; do
        su postgres -c "psql -q -c 'DROP DATABASE IF EXISTS test_$db$n WITH (FORCE)'" 2>&1 | grep -v NOTICE
    done
    rm -f "$LOGS/${P}_pg_$half.log"
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
    timeout 9000 env $PW .venv/bin/python manage.py test apps --exclude-tag migration --parallel 2 > "$LOGS/${P}_suite.log" 2>&1
    echo "sqlite exit=$?"; grep -E "^Found|^Ran|^OK|^FAILED" "$LOGS/${P}_suite.log"
    ;;
ist)
    timeout 9000 env $PW DJANGO_TIME_ZONE=Asia/Kolkata PYTHONPATH="$HERE" DJANGO_SETTINGS_MODULE=fastsettings \
        .venv/bin/python manage.py test apps --parallel 2 --exclude-tag migration --settings=fastsettings > "$LOGS/${P}_ist.log" 2>&1
    echo "ist exit=$?"; grep -E "^Found|^Ran|^OK|^FAILED" "$LOGS/${P}_ist.log"
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
    # Three workers: each loads every app's migration state and grew to 4.4 GB in g25, and a fourth
    # was killed for memory on a 16 GB machine; the runner then waited for its tests for good.
    timeout 12000 env $PW .venv/bin/python manage.py test apps --tag migration --parallel 3 > "$LOGS/${P}_mig.log" 2>&1
    echo "mig exit=$?"; grep -E "^Found|^Ran|^OK|^FAILED" "$LOGS/${P}_mig.log"
    ;;
*)
    echo "unknown lane: $2"; exit 2
    ;;
esac
