# Rules for every fix agent in this round (read this first)

You fix defects listed in docs/RISKS.md "Open defects", by their ids. You do not fix anything else. Anything else you find goes into your report, not into code.

## Your worktree
- Make your own worktree at ef7c0c3. The command is in your brief. Link the venv: `ln -s /home/user/hello-world/.venv <worktree>/.venv`.
- Never edit /home/user/hello-world, another agent's worktree, or docs/.
- Keep your worktree when you finish.

## Before writing code
- Read CLAUDE.md "Mistakes this project keeps making" (all of 1-11) and the pre-flight checklist. They are about 300 lines; read them once.
- For each id, read its row in docs/RISKS.md and the auditor's probe. The probe file path is in your brief. The probe shows the scenario and the numbers. Copy it into your worktree as a regression test, named after the fact it protects, in the module's normal test file. Keep the probe's numbers.
- Work out every expected number in a separate plain-Python script, kept in YOUR OWN folder: a folder of your own in the scratchpad, calc_<your-agent-name>/. Never use scratchpad/calc/ itself; another agent uses it.
- Fix the root cause once, shared, so the next instance cannot be written. If the shape can be seen in code, add or widen an `audit_invariants` check, with a test that it reports a planted instance.
- Shared rules have ONE owner each; your brief says which ones are yours. If you need a rule another agent owns, do not build your own: write your fix against the smallest local check, and say so in the report.

## Proving each fix
- Each fix has a test that fails without it. Revert the fix, watch the test fail, restore it. Report the failure line.
- Race fixes need a race() test in apps/e2e/tests_races.py, run on PostgreSQL with your own database:
  - first, for n in "" _1 _2; do su postgres -c "psql -q -c 'DROP DATABASE IF EXISTS test_<db>$n WITH (FORCE)'"; done
  - then DATABASE_URL=postgres://erp:erp@localhost:5432/<db> nice -n 19 .venv/bin/python manage.py test apps.e2e.tests_races.<Class> --noinput
  - each race test must fail without its lock.
- Run the touched modules' tests with: PYTHONPATH=scripts/gate nice -n 19 .venv/bin/python manage.py test <labels> --settings=fastsettings --exclude-tag migration --parallel 2. Never run the whole suite.
- Before each commit, run `audit_invariants` (no findings) and `makemigrations --check` (no changes). After any makemigrations, redirect the output to a file and read it: every operation must name the model you meant (mistake 8).
- Drive new or changed API behaviour through the API, as a person in the role (apps/core/tests_roles.py), not as a superuser.

## Committing
- One commit per register id, or per root cause when one fix closes several ids; the message names every id it closes.
- Commit with `git -c user.name=Claude -c user.email=noreply@anthropic.com commit -F <file>`.
- The message says what was wrong, what is true now, and which test failed without the fix. It ends exactly with:

      Gate: {GATE}

      Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
      Claude-Session: https://claude.ai/code/session_01XQiMmxK5wWbnQzkh2yM2yK

- Never mention a model name anywhere else. Do not push.

## Report
- Write the full report to a file of your own in the scratchpad, reports/<your-agent-name>.md:
  - commits with the ids each closes;
  - for each fix, the test that failed without it, with its failure line;
  - what you could not do;
  - defects seen and not fixed, each with file:line, a scenario with numbers, and its kind.
- Return at most 10 lines: the commit count and tip, the ids closed, anything that blocks, and the report file's path. The integrator reads the file only when it needs the detail.
- No praise.
