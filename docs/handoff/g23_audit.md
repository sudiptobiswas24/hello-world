# g23 brief: the second read-only audit, one module group per agent

Read CLAUDE.md and .claude/skills/audit/SKILL.md first, all of both. Then
docs/RISKS.md, "Open defects": what is listed there is known. Do not find
it again; a new instance of a listed shape is a finding.

SP=/tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad

## Why a second audit
The 8 October audit found over forty defects with a green suite, in three
areas. Since then g18 to g22 added more than they fixed: two-factor
sign-in, dated tax rates, debit notes and vouchers, the books' opening,
the scale bridge, shortfall settlements, pooled re-batch costing and the
page-wide release gate, among others. Each was reviewed once, by someone
looking at its own diff. Nobody has read a whole module since. This audit
reads whole modules, the old code and the new together.

## Setup
- Base: the pushed g22 tip your prompt names. Worktree:
  `git -C /home/user/hello-world worktree add --detach $SP/audit2_<lane> <base>`.
  Link `.venv` and `frontend/node_modules` from /home/user/hello-world.
- Read-only for product code. Write probes as tests in
  `apps/<module>/tests_audit2_<topic>.py` that assert what SHOULD be true,
  and commit only them, unpushed.
- The commit message ends exactly with `Gate: {GATE}`, a blank line, then
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_01XQiMmxK5wWbnQzkh2yM2yK`.
  Name no model anywhere else.
- PostgreSQL for race() probes: `postgres://erp:erp@localhost:5432/audit2_<lane>`;
  drop test_audit2_<lane> first.

## The lanes, two at a time
1. **floor**: manufacturing whole: stations and the station PIN, the
   scale endpoint and deploy/scale_bridge, tape lines and creels, looms,
   lamination, printing, conversion, process rolls, piece work booked at
   the machines, job-work challans, re-batches, costing and roll_up,
   woven.py's bag specs, quoting.py's cost sheets, dispatch.
   **stores**: inventory whole, quality whole, and the purchasing paths
   that move stock: receipts, inspection, put-away routes, returns to
   vendor, landed cost, lot trace and recall, counts, adjustments,
   consignment, the page-wide release gate.
2. **money**: accounting and gst whole: settlement (FX, installments,
   drawdowns, discounts), bank statements and reconciliation, period
   close and its checks, BooksOpening, TDS and TCS on dated rates,
   challans and returns, e-invoice and its cancellation, e-way bills,
   GSTR-1, GSTR-2B, ITC-04, Table 13, the LUT, setup_indian_books.
   **trade**: sales and purchasing documents: quotes, orders and their
   approvals, price lists and agreed prices, blanket orders, RFQ,
   requisitions, deliveries and drop-ship, invoices, bills, credit and
   debit notes, vouchers, advances, credit limits, MSME days, dunning,
   the dispatch schedule.
3. **people**: hr whole: employees, leave, attendance, shifts, pay runs,
   piece rates, overtime, advances, take-backs, shortfall settlements,
   PF, ESI, PT, LWF, EPS past 58, the bank file, payslips, expense
   claims, contract labour, the leaver.
   **core**: core, assets, planning and imports whole, and the office
   application: roles and setup_roles, two-factor, sessions, API keys,
   history, attachments, backups, document numbering, the working-day
   registry, the lock-order sentinel, audit_invariants itself, MRP, every
   screen in frontend/src/app/registry.ts against the permission its API
   checks.

## What to look for, in this order
1. The eight shapes in the audit skill, mirror gap first. For every guard
   you find, name its mirror (forward and reverse, create, edit and
   delete, sales and purchasing, generated and keyed by hand) and probe it.
2. The audit table in CLAUDE.md (mistake 11), one probe per row, against
   this module's documents.
3. What no audit here has looked for yet, so none has been found:
   - two people at once, with race() on PostgreSQL;
   - the module as each of its roles over the API (apps/core/tests_roles.py),
     never as a superuser;
   - the plant's day in Asia/Kolkata (`DJANGO_TIME_ZONE=Asia/Kolkata`),
     a document made between 18:30 and 24:00 UTC;
   - PostgreSQL's order: any loop over a queryset with no ORDER BY that
     writes or numbers.
4. Inert features, asked the other way round: for each setting, model and
   helper the module has, who calls it? A probe cannot see this shape.
5. A plant month's worth of one thing: thirty of a document, a partial at
   each step, a correction on the last day of the month and on the first
   of the next, a closed period in between.

## Method
- A plain-Python script in calc_audit2_<lane>/ works out every expected
  number first; the probe's figures come from it, never from the output.
- One probe per claim, stated as a fact about money, stock or who may do
  what, with PASS or FAIL.
- A finding needs a probe that fails on the base. A suspicion without one
  is listed as unproven, with what would prove it.
- For each finding: a number, file:line, a scenario with the plant's
  figures and the logins involved, its shape, its kind (books, race,
  rule, security, statutory, crash, minor), and whether it would stop a
  go-live.
- Run audit_invariants and makemigrations --check, and say what they print.

## Budget and report
- About 120 tool calls. Run long jobs in the foreground with a long
  timeout; restarts are frequent here.
- Report to $SP/reports/audit2_<lane>.md: the findings by kind, books and
  security first; then what you probed that held; then what you did not
  reach.
- Send 10 lines back: the findings by severity, go-live blockers first.
