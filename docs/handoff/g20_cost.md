# g20 brief: fix_cost — stock and production cost, landed cost, payroll facts, O187

Read docs/handoff/fix_common.md first; it holds for you, with these changes:
- Base: the commit your prompt names (g19's code and g20's money work are
  in it), not ef7c0c3. Worktree:
  `git -C /home/user/hello-world worktree add --detach $SP/fix_cost <base>`
  (SP=/tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad)
  Link `.venv` and `frontend/node_modules` from /home/user/hello-world.
- Budget: about 170 tool calls. Work the ids in the order below; at the
  budget, commit what is done and report what is left.
- Report: $SP/reports/fix_cost.md.
- Probes: docs/handoff/reports/probes/ (audit_pur_purchasing_*, and the
  others by module). A row with no probe named is built from its own text.

## Ids, in order (docs/RISKS.md "Open defects")

Statutory first:
- O187: a void of a job-worker value receipt belongs on ITC-04 in the
  period the void happens, never rewriting a closed month's return.

Landed cost, one owner (you), one rule:
- O2, O42, O48: landed cost follows the goods it landed on: still on the
  shelf, moved since, sold (cost of sales), or issued to production
  (production cost, the owner's decision of 9 October, "Decisions taken"
  item 20).

Receiving, billing and returns (GRNI must clear; audit shape 3):
- O89: a bill posted before its goods arrive leaves GRNI clear once they do.
- O93: billing one receipt in parts leaves no paisa on GRNI.
- O94: a return is debited to the bill that paid for the goods returned.
- O56: a debit note's lines keep the bill line's cost centre.
- O101: capitalising a received stock line takes it out of stock as well
  as GRNI.
- O38: restored subcontract components carry their remainder (total first).
- O206: fractional subcontract returns leave no paisa between the
  inventory account and the shelf.

Stock and production:
- O3: a write-down books what the shelf loses (`cost_of_removing()`).
- O4: the importer keeps an opening cost's places, or refuses in words.
- O7: release keeps one row per component, whatever units it is listed in.
- O8: the WIP tile reads each run's recorded WIP account.
- O41: `_booked` converts at the factor that stood on each entry's day.
- O47: a tape load stores the kilogrammes it was given, or refuses more
  places than it keeps.
- O205: a posted stock adjustment or stock count cannot be deleted (the
  rest of O10); take both off `DELETABLE_REPORTED`.

Payroll (frozen facts, audit shape "a setting read live where a fact
should be kept"):
- O5: editing a working pattern or holiday region never restates a posted
  slip's day counts.
- O6: the EPFO ECR reads the PF wage ceiling as it stood for the month.
- O31: a leaver's piece-work recovery does not depend on the order the
  leaving date and the final run are entered in.
- O28: the owner's decision (item 20): a leaver's final pay too small for
  what is taken back leaves the shortfall as a receivable from the former
  employee, within the legal deduction cap; PF/ESI wages are not reduced
  by the recovery.

## Shared rules

Yours: landed cost, the GRNI accrual and its clearing, the write-down's
booking, payroll's kept facts. Existing, use them: `cost_of_removing()`,
`StockMovement.unit_cost` (eight places; never round a per-unit cost and
multiply it back), `post_inventory_entry` (mistake 6: write the entry
directly where no argument says what you mean), `correction_date()`,
`AccountingPeriod.refuse_closed()`, `@serialised`, `lock_rows`, a method
that locks opens its own `transaction.atomic`, `AuditableViewSetMixin`
(never replace its writes), the release gate asked with its warehouse.
From g20's money work, already in your base: `settlement.last_of_held`,
`spent_undoing`, `accounting/pricing.py`. Do not change those; if one is
wrong for you, say so in the report.
