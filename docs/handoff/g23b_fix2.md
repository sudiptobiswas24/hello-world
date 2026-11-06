# g23b brief: fix_g23b2 — O257 numbering, O371, O376

Read docs/handoff/fix_common.md first; it binds you. Then CLAUDE.md, all of it.

## Setup
- Base: cd30628 (the g23b integration tip, every g23b fix so far). Worktree:
  `git -C /home/user/hello-world worktree add --detach /home/user/work/fix_g23b2 cd30628`
  (never under /tmp). Link .venv and frontend/node_modules as fix_common says.
- Budget: about 140 tool calls. Report: /home/user/work/reports/fix_g23b2.md.
- You own apps/core/lock_order.py, apps/core/models.py's lock_rows and the numbering
  (DocumentSequence, DocumentSequenceYear, next_value), and setup_roles for O376. Two reviewers
  work read-only beside you; nobody else edits code.

## Order: what the next needs
1. **O371** (root cause first: the numbering fix uses it) lock_rows orders the rows it locks by
   LOCK_ORDER rank, then key, not by model label. Take StatutoryRemittance's local workaround out:
   the helper now does it. Sentinel test that fails under the old sort (lock_rows(payment, account)
   takes the account first). Run every race test that reaches lock_rows on PostgreSQL.
2. **O257, numbering** (decided: serialise behind one row; GST Rule 46(b) needs consecutive invoice
   numbers, so numbers issued in their own transaction, with gaps, are out). Every path that takes a
   number first holds one numbering row per company (lock_rows, in a transaction it opened), then its
   sequence row; a nested number in the same transaction already holds it. Give the numbering row its
   rank in LOCK_ORDER and take DocumentSequence and DocumentSequenceYear off UNSEEN. Tests:
   - a race() on PostgreSQL that deadlocks without the fix: two documents of different types, each
     numbering the other's type inside its own transaction;
   - a posting that rolls back leaves no gap: the next document takes the number it would have;
   - the sentinel sees the shape (a test that fails with it put back on UNSEEN).
   One commit naming O257; it closes the row.
3. **O376** (decided) the Controller and the Bookkeeper get read-only inventory.view_stockmovement
   and manufacturing.view_workorder in setup_roles, to tie stock and work in progress to the ledger.
   Read-only: no add, change or delete. Test as the roles over the API: they read the stock-value
   report and a run's costs, list and detail; they cannot post, change or delete either. Browser test:
   neither role's sweep meets a 403 or 404.

## Before reporting
Run core, accounting, sales, purchasing, hr, inventory, manufacturing, assets, e2e, web (fastsettings,
--exclude-tag migration, browser tests after `npm run build`), `--tag migration` for core, and every
race test you added or that reaches lock_rows or numbering on PostgreSQL with your own database
(fix_g23b2). Then audit_invariants and makemigrations --check. Do not edit docs/RISKS.md.
