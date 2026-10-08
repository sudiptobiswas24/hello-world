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
3. Start these as three background jobs: `lane.sh <prefix> sqlite`,
   `lane.sh <prefix> pg-a` and `lane.sh <prefix> pg-b`.
4. Then start `ist` and `mig` as two more background jobs.
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
  lane runs them on SQLite.

## Other tools

- `failgroups.py <log>` groups a test log's failures by module and last
  error line.
- `names.py` is a crude unbound-name check.
- `fastsettings.py` skips migrations in test databases. It is used for
  quick runs and for the Asia/Kolkata lane:
  `PYTHONPATH=scripts/gate manage.py test ... --settings=fastsettings`.
