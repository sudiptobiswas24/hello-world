# g20 brief: fix_money — exchange, settlement, pricing, commission

Read docs/handoff/fix_common.md first; it holds for you, with these changes:
- Base: the commit your prompt names, not ef7c0c3. Worktree:
  `git -C /home/user/hello-world worktree add --detach $SP/fix_money <base>`
  (SP=/tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad)
- Budget: about 160 tool calls. Work the ids in the order below; at the
  budget, commit what is done and report what is left.
- Report: $SP/reports/fix_money.md.
- Probes: docs/handoff/reports/probes/ (audit_acc_accounting_*,
  audit_sales_sales_*, audit_pur_purchasing_*). A row with no probe named
  is built from its own text and the report it cites.

## Ids, in order (docs/RISKS.md "Open defects")

Exchange and settlement (accounting/settlement.py is yours alone):
- O49, O51 `FxResidueProbe`: an exchange difference is each side's booked
  base subtracted, never the amount times the rate difference; the last
  drawdown takes what is left (CLAUDE.md "Numbers").
- O70 `ForeignDepositCycleProbe`: a credit note refunded at another rate.
- O105: a drawdown credits the account it was held in, not today's
  setting (audit shape "a setting read live where a fact should be kept").
- O53: a debit note's `refund_due` counts a refund whose payment was
  voided; the sales mirror skips it. One rule for both.
- O54 `RepointProbe`: re-pointing an allocation is checked against the new
  document's ceiling.
- O52 `StatementProbe`: reconciliation of a past statement reads the facts
  as they stood on its day.
- O199, O201: the per-unit note cap for a fractional quantity; a typed
  note line credits the revenue account its original line used.
- O80 `WriteOffRecoveryProbe`: a write-off partly undone by a credit note
  is recoverable for what is left.
- O151: an expense claim paid, unpaid and paid again keeps every entry it
  made (posted facts are recorded, never overwritten).

Pricing, both sides (share it; mistake 5):
- O78, O79 `PricingProbe`: a price in another currency is converted; a
  price and its volume breaks are in the line's unit.
- O97: a blanket release keeps the agreed discount. O98: a vendor price
  has a unit. Sales and purchasing price through one rule where they can.

Commission:
- O74, O75, O76 `CommissionProbe`: a voided receipt counts nothing; money
  taken as a deposit counts; commission is worked in the base currency.

Last:
- O200: migration sales 0064 seeds an approval whose note names no
  figures. Never edit a pushed migration: correct it with a new one, tested
  with MigrationExecutor in a TransactionTestCase (CLAUDE.md "Migrations").

## Shared rules

Yours: accounting/settlement.py, the pricing rule, commission. Existing,
use them: `settlement.post_drawdown`, `settlement_difference`,
`RealisedOnItsOwnDay`, `priced_to_give_back()`, `value_left()`,
`refuse_naming_another_order_line()` (accounting/mixins.py),
`correction_date()`, `AccountingPeriod.refuse_closed()`, `@serialised`,
`lock_rows`, `AuditableViewSetMixin` (pass your own fields through
`super().perform_create(serializer, ...)`; never replace it).

Another agent (fix_cost) will later work on purchasing, inventory,
manufacturing and payroll costing. Keep your purchasing edits to the ids
above.
