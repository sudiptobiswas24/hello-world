# The release gate

Nothing is pushed to `claude/erp-creation-guidance-ox5afo` until one full
gate has passed on the newest commit of what is being pushed. Each pushed
commit carries a `Gate:` line saying so.

## Commits before the gate

Commit work locally with the placeholder line `Gate: {GATE}` where the
Gate line will go, followed by the trailers:

    Gate: {GATE}

    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    Claude-Session: <the session's URL>

Commit as `git -c user.name=Claude -c user.email=noreply@anthropic.com commit -F <file>`.

## Running it

1. Make a clean worktree of the tip: `git worktree add --detach /tmp/gtree <tip>`.
   Link `.venv` and `frontend/node_modules` into it, then `export GATE_TREE=/tmp/gtree`.
2. `scripts/gate/lane.sh <prefix> ui`. This runs tsc, the vite build,
   vitest, names, `audit_invariants` and `makemigrations --check`. Run it
   first: the build is what the browser tests open.
3. Run the lanes two at a time, each a background job (a job may run two
   hours at most): `sqlite` with `pg-a`, then `ist` with `pg-b`, then `pg-c`.
4. Then `mig` alone and detached, since it runs about four hours:
   `setsid nohup scripts/gate/lane.sh <prefix> mig > <logs>/mig_driver.txt 2>&1 < /dev/null &`.
   It runs one app at a time and records each app that passes on this HEAD,
   so after a restart run the same line again and it carries on. It ends
   with "mig finished".
5. `python3 scripts/gate/gateline.py <prefix>` prints the Gate line, or
   what is not green.
6. Write the Gate lines with `python3 scripts/gate/finalize.py <pushed base> <gated tip> <prefix>`.
   Then fast-forward the branch to the new tip and push.

## Lessons that each cost a gate run

- **A failing test is never a flake to re-run.** A test the machine's speed
  can decide is a defect (CLAUDE.md). The browser harness once let a test
  end while the server still answered a request. The flush then
  deadlocked on PostgreSQL. That is fixed: apps/web/tests_browser.py
  InFlight.
- **Drop the PostgreSQL test databases before every PostgreSQL lane.** A
  run killed mid-test leaves rows, and `--keepdb` hands them to the next
  run, which then errors by the thousand. lane.sh does this.
- **Split the PostgreSQL lane.** Whole, it exceeds the two-hour limit
  when anything else uses the CPU. lane.sh runs it as two halves on
  separate databases. gateline.py checks that the halves add up to the
  SQLite count.
- **Never edit a script a running job is executing.** Bash reads it as
  it runs, and the job executes shifted lines.
- **Check skip counts before trusting a lane.** A lane run elsewhere (in
  another cloud session) once skipped its browser tests and still looked
  green: 154 skipped against 46. gateline.py refuses an Asia/Kolkata lane
  that skips more than SQLite.
- **Keep agents off the machine during a gate.** Agents running tests,
  even at nice 19, roughly doubled lane times.
- **The migration-tagged tests take minutes each on PostgreSQL.** The mig
  lane runs them on SQLite, then runs on PostgreSQL only the ones SQLite
  skips (`migcount.py --skips`): until g25 one of them had run in no gate.
- **The mig lane runs one app at a time on two workers (O429).** Three
  workers outgrew the memory limit in g25: one was killed at 5.5 GB and the
  runner waited for it for good, using no CPU. Whole, the lane outran both
  the job limit and the container's uptime. gateline.py checks the apps'
  logs add up to the count `migcount.py` took when the lane began.
- **A commit that changes only docs/ may carry**
  `Gate: documentation only (docs/). No application code or test changed.`
  in place of the placeholder. finalize.py keeps it, and refuses it on a
  commit that changes anything else.

## Other tools

- `failgroups.py <log>` groups a test log's failures by module and last
  error line.
- `names.py` is a crude unbound-name check.
- `structcheck.py` compares each merged test file's classes and methods with the
  branches they came from. Run it after any integration whose conflicts were kept
  both ways: a conflict inside a class, kept both ways, still parses.
- `registercheck.py <pushed base> <tip>` fails when a commit subject in the
  range names a register row that is still open at the tip (the ui lane runs
  it). A commit touching only docs/ is skipped: registering names rows too.
- Integrate by cherry-picking, never by merging: `finalize.py` replays the
  range with `cherry-pick`, which cannot replay a merge commit (g19 was
  merged first and rebuilt).
- `fastsettings.py` skips migrations in test databases. It is used for
  quick runs and for the Asia/Kolkata lane:
  `PYTHONPATH=scripts/gate manage.py test ... --settings=fastsettings`.
