"""
Print a gate's Gate line from its lane logs, or say what is missing.

    python3 scripts/gate/gateline.py <prefix>

Reads $GATE_LOGS (default /tmp/gate-logs). Every lane must say "Ran N
tests" and "OK". The two PostgreSQL halves must add up to the SQLite
lane's count, so an app left out of both halves cannot pass unnoticed.
Asia/Kolkata must skip no more than SQLite: a lane whose browser tests
skipped (no build, no Chromium) looks green otherwise.
"""
import os
import re
import sys

LOGS = os.environ.get("GATE_LOGS", "/tmp/gate-logs")
P = sys.argv[1]


def result(name):
    text = open(os.path.join(LOGS, name)).read()
    ran = re.search(r"^Ran (\d+) tests?", text, re.M)
    ok = re.search(r"^OK( \(skipped=(\d+)\))?", text, re.M)
    if not ran or not ok or re.search(r"^FAILED", text, re.M):
        raise SystemExit(f"{name}: not green")
    return int(ran.group(1)), int(ok.group(2) or 0)


sq, sq_skip = result(f"{P}_suite.log")
pg_a, _ = result(f"{P}_pg_A.log")
pg_b, _ = result(f"{P}_pg_B.log")
ist, ist_skip = result(f"{P}_ist.log")
mig, _ = result(f"{P}_mig.log")
out = open(os.path.join(LOGS, f"gate_{P}.out")).read()
vitest = re.search(r"Tests\s+(\d+) passed", out)
problems = []
if pg_a + pg_b != sq:
    problems.append(f"PostgreSQL halves ran {pg_a}+{pg_b}, SQLite {sq}: an app is missing from a half")
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
      f"migration-tagged {mig}; vitest {vitest.group(1)}; names clean; audit_invariants no findings; "
      f"makemigrations --check no changes.")
