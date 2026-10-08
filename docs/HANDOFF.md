# Handoff — start here

This is an ERP for a woven polypropylene sack plant in India: Django 5.2,
DRF, React, Odoo-like. The owner wants every module as deep as Odoo's,
and correct before wide. This file says where things stand and what comes
next. It was written at the end of a long session on 8 October 2026, so
that a new session can start small.

## 0. Read, then verify, before doing anything

First, look for interrupted work and resume it from where it stopped (CLAUDE.md, "Interrupted work"). At handoff nothing was interrupted: all six fix branches are finished and pushed.

1. Read, in order:
   - `CLAUDE.md`: house rules and the mistakes this project keeps making.
   - this file.
   - `scripts/gate/README.md`: how a release is gated.
   - `docs/handoff/findings.md`: the last audit and what each fix branch holds.
   - `docs/handoff/notes.md`: side findings, decisions, gate lessons.
   - `.claude/skills/audit/SKILL.md`: before any audit.
2. Verify every branch below exists, with `git ls-remote origin 'refs/heads/claude/erp-*'`.
   If any is missing, stop and tell the owner.

| Branch | What it is |
|---|---|
| `claude/erp-creation-guidance-ox5afo` | **The product.** Gated and pushed through 9c67116, plus this handoff commit. PR #4 (the owner's, into master) tracks it. Push only here, and only after a gate. |
| `claude/erp-fix-store` | 15 commits: stores fixes. Not gated. |
| `claude/erp-fix-pay` | 14 commits: payroll fixes. Not gated. |
| `claude/erp-fix-asset` | 8 commits: fixed-asset fixes. Not gated. |
| `claude/erp-fix-make` | 19 commits: manufacturing fixes, finished (tip b2b6a7c). Not gated. |
| `claude/erp-fix-race` | 2 commits: lost-update races, proven on PostgreSQL. Not gated. |
| `claude/erp-fix-ui` | 1 commit: the account form on screen. Not gated. |
| `claude/erp-leftover-{ctree,htree,ltree}` | Uncommitted edits left by sessions before 8 October, saved unverified. Probably superseded; diff against the product branch before discarding. |
| `claude/erp-gate-scratch`, `claude/erp-gate-results` | Scratch, from running two gate lanes in another cloud session. Safe to delete. |

All six fix branches are based on 2335abb, which has the same tree as the
product's 9c67116, so they cherry-pick cleanly onto it.

## 1. Environment, once per new container

```
cd <repo>
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # if .venv is missing
(cd frontend && npm ci && npm run build)                               # the browser tests open this build
pg_isready -q || service postgresql start                              # role erp/erp, as the gate uses it
```

- Chromium for Playwright is at `/opt/pw-browsers/chromium`. Pass
  `PLAYWRIGHT_CHROMIUM_EXECUTABLE=/opt/pw-browsers/chromium`, or the
  browser tests skip and the count looks right.
- Quick test runs: `PYTHONPATH=scripts/gate .venv/bin/python manage.py test <labels> --settings=fastsettings --exclude-tag migration`.
- A background job may run two hours at most, so gate lanes run separately
  (scripts/gate/README.md).
- Odoo 19 Community source, read-only, for comparison work:
  `git clone --depth 1 --branch 19.0 --filter=blob:none --sparse https://github.com/odoo/odoo.git`,
  then `git sparse-checkout set addons/<module> ...`.

## 2. Next steps, in order

1. **Integrate the fix branches as gate g17.**
   - Make a worktree at the product tip.
   - Cherry-pick in this order: race, ui, asset, pay, store, make.
   - Expect conflicts:
     - in `apps/core/management/commands/audit_invariants.py`: several
       agents added checks. Keep each as its own function.
     - in `apps/e2e/tests_races.py`.
     - possibly in migration numbering. Read every migration's header
       after resolving.
   - Review every diff. A fix is accepted only if:
     - it has a test that fails without it (spot-check by reverting);
     - it closes the pattern everywhere, not one instance;
     - it has an `audit_invariants` check where the shape can be
       detected from code.
   - Unify the duplicated rules:
     - The "correction dated before the original or in the future" rule
       exists twice. Keep the core one, `correction_date()` /
       `day_that_has_come()` in apps/core/models.py. Move PayRun.void and
       ExpenseClaim.unpay onto it. Drop hr's `reversal_day()` and its
       check.
     - The "deletable posted document" check may also exist twice
       (payroll and manufacturing). Merge it into one.
   - Run the new race tests on PostgreSQL. findings.md lists them.
   - Gate per scripts/gate/README.md. Finalize. Push.
2. **Record the patterns.** Add the patterns the audit found to CLAUDE.md
   "Mistakes this project keeps making" and to
   .claude/skills/audit/SKILL.md. They are:
   - posted records deletable;
   - corrections dated wrong;
   - document-unit quantities with stock-unit costs;
   - per-unit rounding;
   - voids at the posted cost rather than `cost_of_removing`;
   - calculations posted after their inputs changed;
   - rate rows not prorated;
   - checks made before the lock;
   - lock-order inversions;
   - settings read live where a posted fact should have been recorded.

   For each, name the audit check that now guards it.
3. **Decided follow-ups** (notes.md has the detail):
   - A leaver's shortfall becomes a receivable from the former employee;
     PF/ESI wages drop by what is taken back.
   - A period is not closed while its depreciation is uncharged.
   - Move the 17 exempted correction steps onto the core date rule, one
     module at a time.
4. **Side findings** in notes.md. Fix them by pattern, smallest first.
   Each needs a test that fails before its fix.
5. **The Odoo 19 depth comparison.** Do one module at a time, using
   `docs/handoff/odoo_brief.md`. Start with sales, then purchasing,
   inventory, manufacturing, accounting, people. Each produces a ranked
   gap list. Build the high-value gaps with the fix brief's discipline.
6. Pending from the older backlog: E2 (users and roles kept from the
   office), depth passes on every vertical, MB (matured-bug hunt).

## 3. The owner's standing instructions

- Go deeper, not wider. Look for matured bugs, not only decimal places.
  Fix issues so they do not recur: patterns, shared rules, audit checks,
  and CLAUDE.md lessons.
- Decide; do not ask, unless a choice is truly theirs and blocking. Say
  in a line what was decided.
- Run work in parallel with subagents, at most three or four at once.
  Keep the Opus model. Write narrow briefs: grep and line ranges, never
  whole large files, capped reports. Tokens are a concern; avoid many
  small turns.
- More compute means more cloud sessions, each with its own machine.
  Check a remote lane's skip count before trusting it.
- No pull requests unless asked. PR #4 is the owner's; watch it, do not
  rewrite it.
- Commit as `git -c user.name=Claude -c user.email=noreply@anthropic.com`.
  No model names in code or commit messages beyond the trailer. Every
  commit ends with the Gate line, then the trailers
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and
  `Claude-Session: <the session URL>`.
- Push only to `claude/erp-creation-guidance-ox5afo`, after a gate.
  Scratch branches `claude/erp-*` are allowed for backups and other
  sessions.
- Be direct; lead with what is wrong. No praise without reasons.

## 4. Decisions already made (do not reopen)

- **Money moves only through accounts marked "bank, cash or card"**
  (`Account.holds_money`). Receivables, payables, tax accounts and
  company settings refuse a marked account. The mark is fixed once an
  account is posted to. The account form on screen sets it.
- **Piece-rate overpayment is recovered from the next run.** A leaver's
  is recovered in the final run. A shortfall becomes a receivable (to
  build).
- **Agents and gates.** Fix agents work on their own branches. One
  integrator reviews and gates. A gate runs on the newest commit and
  covers everything pushed with it.

## 5. Loose ends

- The previous session's PR #4 check-ins stopped, since nothing was
  pushed. After pushing g17, set one about four hours out.
- Cloud session session_01JhBJNMa8eEqsnUwAeQ5Zhy ran two g16 lanes. It
  can be archived.
