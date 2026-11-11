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
   Put `GATE_BASE=<pushed base>` in front of it and it runs only the apps
   the range needs: see "A mig lane in proportion" below.
5. `python3 scripts/gate/gateline.py <prefix>` prints the Gate line, or
   what is not green.
6. Write the Gate lines with `python3 scripts/gate/finalize.py <pushed base> <gated tip> <prefix>`.
   Then fast-forward the branch to the new tip and push.

## A mig lane in proportion

The 51 migration-tagged tests take over four hours, and most pushes change
no migration. `python3 scripts/gate/migneeded.py <base> <tip>` prints, for
each app, `need <app>: <why>` or `skip <app>: no migration change since
<base>`. Its docstring holds the rule. In short: a file under an app's
`migrations/`, or a module its migrations import, runs that app and every
app whose migrations depend on it; a changed file of migration-tagged
tests, or a change that reaches what such a file imports from another test
module, runs that app alone; the lock sentinel, `config/`,
`requirements.txt`, `manage.py` or `scripts/gate` (bar this file) runs
them all, and so does a base that cannot be read.

    GATE_BASE=<pushed base> setsid nohup scripts/gate/lane.sh <prefix> mig > <logs>/mig_driver.txt 2>&1 < /dev/null &

- `<pushed base>` is the commit the branch stands at: the last one a whole
  gate passed on. The skip rests on that gate. `finalize.py` refuses a
  lane whose base is after the base it is given, or whose tip is not the
  gated one.
- The driver log says `<app> skipped: no migration change since <base>`
  for each app left out, and `<prefix>_mig/needed.txt` keeps the list.
  `gateline.py` works the list out again from the range, and refuses the
  gate unless every app it names passed on the gated commit. The Gate line
  then reads `migration-tagged 0 of 51 needed: no migration changed since
  <base>`, or `migration-tagged 48 of 51, apps ...; others unchanged since
  <base>`.
- Without `GATE_BASE` every app runs and the line reads as it always has.
  Do that when in doubt: the rule cannot see a migration test broken by
  application code that no migration imports. That code's own tests run in
  the other lanes; the tagged tests run nowhere else.
- The dependencies between the apps' migrations nearly form a ring
  (purchasing on sales, assets on purchasing, inventory on assets, sales on
  inventory), so one migration in almost any app runs almost every app:
  g35's range needs 48 of the 51. The saving is the push with no migration
  at all, which g36 was.
- `python3 -m unittest scripts/gate/tests_migneeded.py` tests the rule. It
  needs git, not Django.
- `.github/workflows/gate.yml` runs the same lanes on GitHub for a push to
  `claude/**`, the mig job one app at a time for the apps `migneeded.py`
  names against the commit the branch stood at.

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
