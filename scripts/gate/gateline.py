"""
Print a gate's Gate line from its lane logs, or say what is missing.

    python3 scripts/gate/gateline.py <prefix>

Reads $GATE_LOGS (default /tmp/gate-logs). Every lane must say "Ran N
tests" and "OK". The three PostgreSQL parts must add up to the SQLite
lane's count, so an app left out of every part cannot pass unnoticed.
Asia/Kolkata must skip no more than SQLite: a lane whose browser tests
skipped (no build, no Chromium) looks green otherwise.
"""
import os
import re
import sys

LOGS = os.environ.get("GATE_LOGS", "/tmp/gate-logs")
P = sys.argv[1]


def result(name):
    if not os.path.exists(os.path.join(LOGS, name)):
        raise SystemExit(f"{name}: not run")
    text = open(os.path.join(LOGS, name)).read()
    ran = re.search(r"^Ran (\d+) tests?", text, re.M)
    # "OK", "OK (skipped=N)" or "OK (skipped=N, expected failures=M)": g24 read the last as 0 skipped.
    ok = re.search(r"^OK(?: \((?:skipped=(\d+))?[^)]*\))?$", text, re.M)
    if not ran or not ok or re.search(r"^FAILED", text, re.M):
        raise SystemExit(f"{name}: not green")
    return int(ran.group(1)), int(ok.group(1) or 0)


sq, sq_skip = result(f"{P}_suite.log")
pg_a, _ = result(f"{P}_pg_A.log")
pg_b, _ = result(f"{P}_pg_B.log")
pg_c, _ = result(f"{P}_pg_C.log")
ist, ist_skip = result(f"{P}_ist.log")
# The mig lane runs one app at a time (O429): each app's log must be green, they must add up to the
# count taken when the lane began, and the tests SQLite skipped must have passed on PostgreSQL.
counts = dict(line.split() for line in open(os.path.join(LOGS, f"{P}_mig", "count.txt")).read().split("\n") if line)
mig_total = int(counts.pop("total"))
mig = sum(result(os.path.join(f"{P}_mig", f"{app}.log"))[0] for app in counts)
if mig != mig_total:
    raise SystemExit(f"migration apps ran {mig}, the lane counted {mig_total}")
pg_only = [line for line in open(os.path.join(LOGS, f"{P}_mig", "skips.txt")).read().split("\n") if line]
mig_pg, mig_pg_skip = result(os.path.join(f"{P}_mig", "pg.run.log")) if pg_only else (0, 0)
if mig_pg != len(pg_only) or mig_pg_skip:
    raise SystemExit(f"PostgreSQL ran {mig_pg} of the {len(pg_only)} migration tests SQLite skips, skipping {mig_pg_skip}")
out = open(os.path.join(LOGS, f"gate_{P}.out")).read()
vitest = re.search(r"Tests\s+(\d+) passed", out)
problems = []
if pg_a + pg_b + pg_c != sq:
    problems.append(f"PostgreSQL parts ran {pg_a}+{pg_b}+{pg_c}, SQLite {sq}: an app is missing from a part")
if ist != sq or ist_skip > sq_skip:
    problems.append(f"Asia/Kolkata ran {ist} skipping {ist_skip}; SQLite ran {sq} skipping {sq_skip}")
if not vitest or "UI FAILED" in out:
    problems.append("UI lane not clean")
if "names: clean" not in out:
    problems.append("names check not clean")
if "No invariant findings" not in out or "No changes detected" not in out:
    problems.append("audit_invariants or makemigrations --check not clean")
if problems:
    raise SystemExit("; ".join(problems))
print(f"Gate: SQLite {sq} tests, {sq_skip} skipped; PostgreSQL {sq}; Asia/Kolkata {ist}, {ist_skip} skipped; "
      f"migration-tagged {mig}, one app at a time, the {mig_pg} PostgreSQL-only on PostgreSQL; vitest {vitest.group(1)}; names clean; audit_invariants no findings; "
      f"makemigrations --check no changes.")
