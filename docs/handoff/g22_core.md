# g22 brief: fix_core — core and hr's open rows, the audit's blind spots

Read docs/handoff/fix_common.md first; it binds you. Then read CLAUDE.md, all of it.

- Base: the commit your prompt names. Worktree:
  `git -C /home/user/hello-world worktree add --detach $SP/fix_core <base>`
  (SP=/tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad)
- Budget: about 150 tool calls. Work in the order below. At the budget, commit what is done and report what is left.
- Report: $SP/reports/fix_core.md.

## Your rows, in order

Read each row in docs/RISKS.md, "Open defects".

### People's money first

1. **O213.** Outside a final run, a take-back for earlier runs is netted into the period's piece-work earning. PF/ESI wages drop by it.
   - Keep the take-back as its own line, against the earning it takes back.
   - Show that each statutory base reads what was earned in the period.
   - Work the numbers with a scenario table first, for a loom weaver.
2. **O215.** A leaver's shortfall becomes a receivable from the former employee (O28), but nothing settles it.
   - Build the receipt against it, and the write-off. The write-off is decided by the Controller, with a reason.
   - Build both reverses.
3. **O32.** `LeaveRequest.cancel(on_date)` takes any date. Put it on `correction_date()`.
4. **O176.** Paying an expense claim may be dated ahead.
   - Refuse a payment dated after today. Use the plant's day, `timezone.localdate()`.
   - Fix the existing test that relies on paying ahead.
5. **O197.** `UserViewSet.perform_create` and `perform_update` run without a transaction. A create refused while giving roles leaves the login made.
   - Put them inside the mixin's transaction.
   - Never replace the mixin's writes (CLAUDE.md, "The API").

### The rule that dates corrections

6. **O15.** 17 correction steps outside hr and assets are not on `correction_date()`. They are named in `DATED_ELSEWHERE`.
   - Put each on the rule and take it off the list.
   - A step that cannot be is a question: say why in its own line in the report. Never take a step off the list without reading it.

### Core

7. **O130.** `scoped()` filters `path__in`, so rows whose party path is NULL drop out of a rep's view.
8. **O136.** `Opportunity.sales_order` has no unique constraint. Add it. The data migration reports collisions and does not merge them. Test it with MigrationExecutor.

### The audit's blind spots

Each of these makes `audit_invariants` see a shape. Each needs a test that a planted instance is reported.

9. **O246.** It finds `timezone.now().date()` but not `.date()` of an aware timestamp handed in.
10. **O40.** `per_unit_withdrawal_rates` skips inventory/models.py and `average_cost_at`. `shelf_read_before_lock` sees a read and a lock only inside one function.
11. **O46.** `unlocked_open_runs` sees only `.is_open()`.
12. **O212.** `reported_without_the_period` maps every word of a followed function, docstrings included, to a model.

### Locks

13. **O226.** Nine lock shapes the sentinel is told not to see (UNSEEN in apps/core/lock_order.py).
    - For each: put its writes in key order and take it off UNSEEN, with a `race()` test where two at once can meet; or say in UNSEEN why it cannot.
    - Run the migration tag after touching the sentinel.
14. **O228.** The sentinel keeps a row held after a savepoint that rolled back. It does not see raw SQL `FOR SHARE`.
15. **O229.** The lock-order comment in apps/core/models.py is no longer true.

### Words

16. **O25.** RecordScreen's afterCreate comment does not match the code.
17. **O132.** docs/IMPORT.md still documents a roles column; the leave screen does not offer Cancel. This doc edit is yours.

## Shared rules

- **Yours:** the audit checks above, the lock order and its sentinel, `correction_date()`'s reach, the leaver's receivable.
- **Existing; use them:** `hold_the_employee`, PAY_INPUTS, `@serialised`, `lock_rows`, `AccountingPeriod.refuse_closed()`, `AuditableViewSetMixin`.
- Another agent (fix_stores) builds a working-day registry in core for quality's escalation. Do not build one.
- Keep to core, hr, and the audit command.
