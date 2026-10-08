<!-- Agent brief used on 8 October 2026. $SP was that session's scratch folder; give agents real paths in a new session. -->
# Fixing audited defects — the brief every fix agent follows

SP = /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad

The repo is a Django 5.2 + DRF ERP (Odoo-like) for a woven-sack plant, with
a React office app in frontend/. You have your own git worktree (named in
your prompt), detached at 2335abb, the newest commit. Work and commit only
there.

Never:
- touch another worktree. $SP/gtree is running the release gate: do not even
  run git in it. $SP/htree, ktree, g16tree and the others are not yours.
- push, create branches, or edit /home/user/hello-world.
- run the whole suite (`manage.py test apps`), set DATABASE_URL (PostgreSQL
  belongs to the gate), or run npm build, vite or playwright.
- weaken a guard or adjust an existing assertion to match new output, unless
  you have shown that assertion encoded the defect. Say so in the commit
  message if you do.
- put any model name in code, tests or commit messages, beyond the trailer
  below.

## Read first

Read <worktree>/CLAUDE.md in full: the architecture rules, the ten mistakes
and the pre-flight checklist. Then read <worktree>/.claude/skills/audit/SKILL.md.
They are the house rules and they are strict.

## For each finding, in the order your prompt gives

1. **Verify.** Re-run the finding's probe against your worktree (command
   below) and read the code it names. Decide whether the code is wrong or the
   probe's expectation is (CLAUDE.md mistake 7), and reconstruct the number by
   hand. Do not change code, and report it, if:
   - the probe is wrong;
   - a test or docstring says the behaviour is intended;
   - the fix needs a business decision. Name the decision and the options.
2. **Plan.** Write the scenario table first, with the refusals before the
   happy path. Name the reverse, edit and delete paths of any guard you add,
   and cover them.
3. **Fix.** Fix the root cause with the smallest change that closes the hole.
   - Share code rather than copy it (mistake 5).
   - Guard in save() or the method, not clean() (mistake 3).
   - What a posted document did is a recorded fact, not something to
     recompute (mistake 4).
   - Follow the numbers rules in the checklist: total first, divide last;
     carry 8 places in unit cost; every outbound stock path asks
     cost_of_removing().
4. **Test.** Turn the probe into regression tests in the app's own test
   modules, one test per fact, named after the fact.
   - Reuse existing fixtures (subclass the module's TestCase).
   - Never name a helper or attribute `run`, `order`, or anything a fixture
     already sets on self.
   - Two refusals in one test need two separate builds.
5. **Prove.** With your fix reverted (a temporary edit, then restored), the
   new tests fail. With it in place, they pass.
6. **API.** If an API action or endpoint is involved, drive it through the API
   as a person in the role (call_command("setup_roles"), a user in that
   Group), not a superuser.
7. **Run.** Run the touched app's test modules, plus any other module that
   imports what you changed (grep for it):

       cd <worktree> && PYTHONPATH=$SP nice -n 19 .venv/bin/python manage.py test <labels> --settings=fastsettings --noinput --exclude-tag migration

   Then run `nice -n 19 .venv/bin/python manage.py audit_invariants`.
   Then run `.venv/bin/python manage.py makemigrations --check --dry-run`.
   If you add a migration:
   - send makemigrations output to a file and read it, checking it names the
     model you meant;
   - follow the checklist's migration rules;
   - write a migration test only if it moves data (TransactionTestCase with
     MigrationExecutor, @tag("migration")). The main session runs those.
8. **Frontend.** If you change frontend/, run `cd frontend && nice -n 19 npx tsc -b`.
   That is all; no build. Money and quantities stay strings; every call goes
   through src/api/client.ts.
9. **Commit.** One commit per finding, or per tightly coupled group, with the
   suite of step 7 green. Commit only what the finding needs.

## Two people at once

A method that reads a document's state and then changes it is decorated
`@serialised("fields", ...)` (apps.core.models). A decision over other rows
takes `lock_rows(...)` first. Races cannot be proven on SQLite. If a fix
needs a race proof, add the test to apps/e2e/tests_races.py, using `race()`
like its neighbours, and say so in your report. The main session runs it on
PostgreSQL.

## Commit message

Match the style of `git log -8` in your worktree:
- A title: "Area: what is now true".
- Plain paragraphs saying what was wrong, with the numbers, and what is true
  now.
- A "Tests:" paragraph.

End exactly with:

    Gate: {GATE}

    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    Claude-Session: https://claude.ai/code/session_01XQiMmxK5wWbnQzkh2yM2yK

Commit with `git -c user.name=Claude -c user.email=noreply@anthropic.com commit -F <file>`,
writing the message file in your probe directory, not the worktree.

## Final report (under about 700 words)

For each finding give one of:
- **fixed**, with the commit sha and one line;
- **not fixed**, with why: the probe was wrong, the behaviour is intended, or
  it needs a decision (state the decision and the options).

Then:
- the test labels you ran, with their printed counts;
- anything that needs a PostgreSQL race proof;
- anything you saw but did not touch.

If you run long, stop at a clean commit and list what remains.

## Reading economy

Tokens are the cost here. Read with grep and line ranges (sed -n 'a,bp',
Read with offset and limit), never whole large files: apps/*/models.py
runs to 6,000 lines. Find the function by name, then read it and what it
calls. Do not re-read a file you have already read. Keep the final report
to the length asked; put detail in files, not in the message.
