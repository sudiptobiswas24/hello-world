# g22 brief: review_stores (an adversarial, read-only review of fix_stores)

Read CLAUDE.md and .claude/skills/audit/SKILL.md first, all of both.

SP=/tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad

## Setup
- Worktree: `git -C /home/user/hello-world worktree add --detach $SP/review_stores 162b5ca`. Link `.venv` and `frontend/node_modules` from /home/user/hello-world.
- Never edit product code. Write probes (tests that assert what SHOULD be true) and commit only them, unpushed.
- The commit message ends exactly with `Gate: {GATE}`, a blank line, then:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_01XQiMmxK5wWbnQzkh2yM2yK`
- Name no model anywhere else.

## What you review
fix_stores' 25 commits, f2fb5de..162b5ca. The author's report is $SP/reports/fix_stores.md (three rounds). The brief was docs/handoff/g22_stores.md.

The plant runs tape lines, looms, lamination, printing and bag conversion. It weighs at the stations and buys freight-heavy polymer. Lead with what makes the shelf disagree with the ledger, lets a held lot move, or locks two people against each other.

1. **O210 and the rebatch in `follow_value`.**
   - Landed cost released after goods were inspected, moved, re-batched, split, partly consumed, partly returned, or sold.
   - Does the shelf foot to the inventory account to the paisa, item by item, after each?
   - Vary the order (audit shape 3: vary one input and assert it still foots).
2. **O133 and MRP.** Tape on a backflushed run's creels: allocated, half drawn, the run closed early, the run voided. Do allocate() and MRP agree with what is free?
3. **O29.** A period close with depreciation uncharged, with depreciation charged and then reversed, and an asset disposed of mid-period. Reopening.
4. **O34 and the opening-balance asset (BooksOpening).**
   - Set it once.
   - Try changing it after something posts on or before its day.
   - An asset typed in on, before and after the opening day.
   - Remove an opening asset after a depreciation run.
   - The imports with a disagreeing --date.
   - Does the register foot to the ledger after each? Does the health check say so?
5. **O12, O13, O14 and O26.** Assets, by API and by admin, as each role (apps/core/tests_roles.py).
6. **O232.** A coating check tied to its roll: two rolls on the coater, a check between them, a roll remounted, a check withdrawn. Which bundles wait?
7. **O231, the page-wide gate.**
   - Does it give the same words as the single gate for every hold: a recall, an advisory wait, an expiry, a quarantine bay?
   - Count the queries for 25 and 100 lots.
8. **O242, O241, O203 and O45/O127.**
   - Lock order on PostgreSQL: run `race()` with `DATABASE_URL=postgres://erp:erp@localhost:5432/review_stores`, after dropping test_review_stores.
   - The screens, as the Quality Inspector and the Quality Manager.
   - The working days across a plant holiday and a Sunday.
   - Every posted document refused in the admin.
9. **O225, the scale bridge in deploy/.**
   - Recorded frames: unstable, a weight repeated, a dropped port, a malformed frame, a negative or zero weight, a weight with a different unit.
   - Is any weight posted twice, or one never posted?
   - Does it use the station's token safely (not in logs)?
10. **O11, O23, O27 and O134**, briefly.

## Method
- One probe per claim, stated as a fact about stock, money or state, with PASS or FAIL for each.
- Number the findings. Give each its file:line, a scenario with quantities and rupees, and whether it blocks.
- A finding needs a probe that fails on the tip. List a suspicion without one as unproven.
- Run audit_invariants and makemigrations --check, and say what they print.

## Budget and report
- About 80 tool calls. Run long jobs in the foreground with a long timeout; restarts are frequent here.
- Report to $SP/reports/review_stores.md.
- Send 10 lines back: findings by severity, blockers first.
