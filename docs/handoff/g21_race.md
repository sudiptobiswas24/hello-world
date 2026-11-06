# g21 brief: fix_race — two people at once

Read docs/handoff/fix_common.md first; it holds for you, with these changes:
- Base: the commit your prompt names (g19 and g20's work are in it), not
  ef7c0c3. Worktree:
  `git -C /home/user/hello-world worktree add --detach $SP/fix_race <base>`
  (SP=/tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad)
  Link `.venv` and `frontend/node_modules` from /home/user/hello-world.
- Budget: about 180 tool calls. Work the ids in the order below; at the
  budget, commit what is done and report what is left.
- Report: $SP/reports/fix_race.md.
- Probes: docs/handoff/reports/probes/ (audit_acc_* AccountingRaceProbe,
  audit_pur_* races).
- Every race fix has a `race()` test in apps/e2e/tests_races.py on
  PostgreSQL that fails without its lock (fix_common, "Proving each fix").
  SQLite proves nothing here.

## Ids, in order (docs/RISKS.md "Open defects")

Money races already shown:
- O63: a statement closes while one of its lines is unmatched.
- O64: a TDS deduction reversed while a challan pays it.
- O108: an RFQ award and a direct order on one requisition both go
  through: one lock on the requisition line for both.

The sentinel (apps/core/lock_order.py), so the next race is seen in tests:
- O182: rank the unranked models that are locked beside ranked ones,
  `inventory.stockposition` first (taken both before and after orders,
  receipts and bill lines: decide its one place and move the paths that
  disagree).
- O184: let it see locks taken by update(), save() and delete(), joined
  tables under select_related, and a values_list without the pk. Survey
  first (LOCK_ORDER_SURVEY, as fixtrade.md describes): fix what the survey
  finds if it is a handful; if it is many, fix the money paths, keep the
  rest out of the sentinel's view by name with the reason, and list them in
  the report for the register. Never let the sentinel go quiet on what it
  could see before.

Lock order and stale reads:
- O37, O21, O35, O39, O168: each path declares what its save() locks first
  (`locked_before_it()`), reads after the lock, and takes the written order.
- O125: a journal line asks its entry under the entry's lock.
- O36: a line's check against its call-offs runs under the line's lock on
  every path that changes what it checks.
- O16: unmarking `Account.holds_money` holds the account against a payment
  posted through it.
- O17: the nine viewsets without `AuditableViewSetMixin` take it, or hold
  their row from read to write some other way; say which for each.
- O18: the punch-file import reads an attendance day after the employee's
  lock.
- O20: prove or refute that posting a run under the employee's lock closes
  the race with leave and attendance; fix it if it does not.
- O22: admin edits hold their row from read to write (one shared admin
  mixin, as the viewsets have).

## Shared rules

Yours: the lock order (apps/core/lock_order.py's LOCK_ORDER and its
sentinel), the admin mixin. Existing, use them: `@serialised`,
`serialised(held_first=True)` and `locked_before_it()`, `lock_rows`,
`lock_in_turn`, `answer_to_its_document()`, `lock_for_change`, the
viewset mixin's get_object(). A method that locks opens its own
transaction (the sentinel refuses a lock outside one).
