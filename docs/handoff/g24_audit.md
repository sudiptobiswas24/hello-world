# g24 brief: fix_audit — an edit log that keeps old and new values and cannot be bypassed

Read docs/handoff/fix_common.md first; it binds you. Then CLAUDE.md, all of it.
Then docs/handoff/reports/odoo19_gap_analysis_oct26.md, item 6.

## Setup
- Base: d76ef6f (g23b pushed). Worktree:
  `git -C /home/user/hello-world worktree add --detach /home/user/work/fix_audit d76ef6f`
  (never under /tmp). Link .venv and frontend/node_modules as fix_common says.
- Budget: about 160 tool calls. Report: /home/user/work/reports/fix_audit.md.
- You own apps/core/history.py, apps/core/audit.py, the new log and its triggers, and any
  middleware that records who is acting. Two other agents work beside you: fix_statutory
  (hr/statutory.py, the seeds, go_live_check, TAN, the 2025 Act's names) and fix_rcm (GST reverse
  charge). Do not touch their files. Shared rules that exist, use them: AuditModel,
  AuditableViewSetMixin (never replace its perform_create/perform_update), RecordEvent, the
  numbering-first lock (O257: every transaction that locks a row takes the company's numbering row
  first; models marked `numbering_exempt` do not).

## Why
Companies (Accounts) Rules 2014, rule 3(1) proviso: accounting software must keep an audit trail of
every transaction, an edit log of each change with the date it was made, and the trail must not be
able to be disabled. Records are kept 8 years (Companies Act s.128(5)). Today RecordEvent keeps the
names of the fields a change touched, written by the API layer only. An admin, shell, script or raw
SQL change leaves nothing (history.py:8-10, RISKS known limit 35). Check the rule's current wording
and cite it; anything you cannot confirm goes to the report for the accountant.

## Decided (do not reopen)
1. **Two layers.**
   - **The application layer.** Every create, change and delete of an audited model writes one
     event with each changed field's old and new value, who did it, when, and through what (API,
     admin, a management command by name, the shell). It is written at the model layer (signals,
     or the base model's save and delete), so it covers every path that goes through the ORM, not
     only the API. Bulk QuerySet.update() and .delete() on an audited model write their events too,
     or are refused with a sentence; say which you chose, and grep every product use.
   - **The database layer, on PostgreSQL.** Triggers on the books-of-account tables (journal entries
     and lines, invoices, bills, notes, payments and their allocations, stock movements, pay runs and
     slips, assets and depreciation, the accounting periods) write every row's INSERT, UPDATE and
     DELETE, old and new row as JSON, into an append-only table. The trigger reads the acting person
     from a transaction setting the app sets (SET LOCAL); a change with none is recorded as
     unattributed. The log table refuses UPDATE and DELETE by trigger, and TRUNCATE by revoking it
     from the app's role in the migration. A migration creates all of it, idempotently, and its
     reverse drops only what it made. SQLite has no such layer: the test lanes on SQLite skip those
     tests, which run on PostgreSQL.
2. **Audited models:** every AuditModel subclass, and every model the triggers cover. Models marked
   `numbering_exempt` (sign-in and access records) are audited by the application layer only, and
   never block a sign-in.
3. **Values:** money and quantities as exact text; foreign keys as id and the related object's label;
   secrets (the second factor's sealed secret, API key hashes, password hashes) never, only "changed".
4. **Reading it:** the record's history panel shows each change's old and new values. A new report,
   "Edit log", lists every change in a date range by model and person, for the Controller and an
   auditor role (read-only). Test it as the roles.
5. **Kept:** never pruned. audit_invariants reports any audited model a code path writes without an
   event, with a planted-instance test. The health check reports unattributed database-layer rows.

## Tests that must fail without the fix
An admin change, a shell or management-command change, a bulk update, a delete, and a raw SQL UPDATE
on PostgreSQL each leave an event with old and new values. A delete or update of the log table is
refused on PostgreSQL. The edit log report shows a change to a posted invoice's draft-time field, as
the auditor.

## Before reporting
Run core, accounting, sales, purchasing, inventory, hr, assets, e2e and web (fastsettings,
--exclude-tag migration, browser tests after `npm run build`), `--tag migration` for core and anything
you migrated, your PostgreSQL tests with your own database (fix_audit), and every race in
apps/e2e/tests_races*.py on PostgreSQL (the triggers add writes to every locking transaction).
Then audit_invariants and makemigrations --check. Do not edit docs/RISKS.md.
