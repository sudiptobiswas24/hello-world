"""
Print a gate's Gate line from its lane logs, or say what is missing.

    python3 scripts/gate/gateline.py <prefix>

Reads $GATE_LOGS (default /tmp/gate-logs). Every lane must say "Ran N
tests" and "OK". The three PostgreSQL parts must add up to the SQLite
lane's count, so an app left out of every part cannot pass unnoticed.
Asia/Kolkata must skip no more than SQLite: a lane whose browser tests
skipped (no build, no Chromium) looks green otherwise.

A mig lane run with GATE_BASE leaves needed.txt: the apps it ran and the
ones it skipped. The list is worked out again here from the same range
($GATE_TREE, default this repository), and every app it names must have
passed on the gated commit. A skipped app is printed as skipped.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import migneeded  # noqa: E402

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
MIG = os.path.join(LOGS, f"{P}_mig")
pg_only = [line for line in open(os.path.join(MIG, "skips.txt")).read().split("\n") if line]
unchanged = ""
if os.path.exists(os.path.join(MIG, "needed.txt")):
    # The lane ran part of the apps (GATE_BASE). Its list is not taken on trust: the same range is read
    # again, an app the range needs must have passed on the gated commit, and the count says "of".
    base, tip, need, skip = migneeded.read(open(os.path.join(MIG, "needed.txt")).read())
    tree = os.environ.get("GATE_TREE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
    try:
        again = set(migneeded.needed(base, tip, tree)[0])
    except migneeded.Unreadable as problem:
        raise SystemExit(f"the migration lane's range cannot be read again: {problem}")
    if again != need:
        raise SystemExit(f"the migration lane ran for {sorted(need)}; {base[:7]}..{tip[:7]} needs {sorted(again)}")
    unlisted = sorted(set(counts) - need - skip)
    if unlisted:
        raise SystemExit(f"the migration lane neither ran nor skipped {unlisted}")
    ran = sorted(set(counts) & need)
    mig = 0
    for app in ran:
        got = result(os.path.join(f"{P}_mig", f"{app}.log"))[0]
        done = os.path.join(MIG, f"{app}.done")
        if got != int(counts[app]):
            raise SystemExit(f"migration app {app} ran {got}, the lane counted {counts[app]}")
        if not os.path.exists(done) or open(done).read().strip() != tip:
            raise SystemExit(f"migration app {app} is needed and has not passed on {tip[:7]}")
        mig += got
    every = [line for line in open(os.path.join(MIG, "skips.all.txt")).read().split("\n") if line]
    if pg_only != [test for test in every if test.split(".")[1] in need]:
        raise SystemExit("the PostgreSQL-only migration tests listed are not those of the apps that need them")
    if set(counts) - need:
        unchanged = (f"0 of {mig_total} needed: no migration changed since {base[:7]}" if not ran else
                     f"{mig} of {mig_total}, apps {','.join(ran)}; others unchanged since {base[:7]}")
else:
    mig = sum(result(os.path.join(f"{P}_mig", f"{app}.log"))[0] for app in counts)
    if mig != mig_total:
        raise SystemExit(f"migration apps ran {mig}, the lane counted {mig_total}")
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
migration = f"{mig}, one app at a time, the {mig_pg} PostgreSQL-only on PostgreSQL"
if unchanged:
    migration = unchanged + (f", the {mig_pg} PostgreSQL-only on PostgreSQL" if mig_pg else "")
print(f"Gate: SQLite {sq} tests, {sq_skip} skipped; PostgreSQL {sq}; Asia/Kolkata {ist}, {ist_skip} skipped; "
      f"migration-tagged {migration}; vitest {vitest.group(1)}; names clean; audit_invariants no findings; "
      f"makemigrations --check no changes.")
