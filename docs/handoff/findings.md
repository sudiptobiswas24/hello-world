# Audit of 8 October 2026: findings and where each stands

On 8 October 2026 three read-only audit agents probed stores,
manufacturing, and payroll with fixed assets. Each finding was shown by a
probe: a test that printed FAIL with the wrong numbers. Fix agents then
worked each area on its own branch, all based on 2335abb (the same tree
as the pushed 9c67116):

| Branch | Area | Commits | State |
|---|---|---|---|
| `claude/erp-fix-store` | stores | 15 | done; report below |
| `claude/erp-fix-pay` | payroll | 14 | done; report below |
| `claude/erp-fix-asset` | fixed assets | 8 | done; report below |
| `claude/erp-fix-make` | manufacturing | 18+ | see the end of this file |
| `claude/erp-fix-race` | two lost-update races | 2 | done, proven on PostgreSQL |
| `claude/erp-fix-ui` | account form on screen | 1 | done |

None of these is gated or pushed to the main branch yet. That is the
next session's first job (HANDOFF.md, step 2).

Every fix has regression tests. The fixing agent reverted each fix and
watched its tests fail; I have not repeated that. Review every diff before
it joins the gate.

## Stores (`claude/erp-fix-store`)

| # | Finding | Fix |
|---|---|---|
| 1 | A return to vendor had no on-hand check | c46bec7, shared `check_available()` under the position lock |
| 2 | A customer return was priced at the shipment's blended 4-place rate | debf108: each allocation keeps the exact total its movement took (sales 0062) |
| 3 | A write-down was booked at a 4-place unit cost | 4846b81: `posted_value` keeps the `cost_of_removing` total (inventory 0036) |
| 4 | An item's inventory account could change with stock on the shelf | 84619ce: frozen once there are movements; the company default too |
| 5 | A partial drop-ship return reversed the whole sales delivery | 311d8ed |
| 6 | Landed cost landed where the goods are not | 970e0b3: `holds_value()`; what the shelf won't hold goes to COGS |
| 7-9 | Accept, return and reject took goods from the wrong route step; put-away and clearance couldn't land in a binned warehouse | b31d060 |
| 10-11 | Reservations shrank after a partial shipment and ignored warehouses | 444b934 |
| 12 | Below zero, the FIFO and specific-ID replays disagreed with `cost_of_removing` | cd70195 (migration 0037 drops stale folds; it has no migration test) |
| 13 | Consignment stock was valued under standard costing | 9a8818d |
| 14 | Overlapping count lines; a named bin could go negative | e204ada |
| 15 | Race: Delivery.post checked stock before taking its locks | f0bebb1. Needs a PostgreSQL race proof: `SalesRaceTests.test_two_orders_do_not_both_ship_the_last_of_the_shelf` |

Patterns closed:
- `move_stock()` (6d7b195);
- `StockMovement.save` refuses taking a warehouse below zero unless it allows it (2c1baec);
- three new `audit_invariants` checks (9bdddc6): `per_unit_withdrawal_rates`, `movements_written_around_save`, `shelf_read_before_lock`.

## Payroll (`claude/erp-fix-pay`)

| # | Finding | Fix |
|---|---|---|
| 1 | A mid-month rate change paid both rates in full (9,900 for 4,950) | 3ea2979: each rate row is paid for its own days |
| 2, 5 | Piece work paid twice across two runs; post() posted stale figures | 760d6f8: post locks the employees and recalculates, refusing a changed slip (hr 0020). Needs a PostgreSQL proof: `StockAndPlantRaceTests.test_two_fortnights_posted_at_once_do_not_both_pay_the_piece_work` |
| 3 | Someone hired and gone within a period was unpaid | 3e687af |
| 4 | A negative slip posted | 302b8f0: refused, naming the person |
| 6 | Deleting a remittance reopened the void hole | 9eb461b |
| 7 | A void could be dated before the run or in the future | b2a78d2 |
| 8 | An empty slip blocked the period | bbc15bf |
| 9 | A back-dated leaving date was accepted under a posted run | 4a0dc5d |
| 10 | `remitted()` ignored as_of | 090cdb8, 3da2ebc |
| 11 | Leave could be deleted, moved or re-credited | 7335055 |
| 12 | A leaver's piece-rate overpayment was never recovered | 440f6d8, in part. The follow-up is decided (notes.md): the shortfall becomes a receivable from the former employee; the final run is not refused; PF/ESI wages drop by what is taken back |

New `audit_invariants` checks: unchecked reversal date, deletable posted
document, posted as calculated (273259d, eeea508).

## Fixed assets (`claude/erp-fix-asset`)

| # | Finding | Fix |
|---|---|---|
| 1 | Disposal read the category's accounts live after they changed | 99f2b20: the asset records its accounts and method in service (assets 0007, with a migration test) |
| 2 | A capitalised draft could be repriced or recategorised | 2b12671 |
| 3, 6 | Corrections dated before the original or in the future; depreciation through a future date | ad20fcd: shared `correction_date()` / `day_that_has_come()` in apps/core/models.py |
| 4 | An in-service date in a closed month blocked the whole month-end run | b57274c: refused at the source. The demo loader had this defect too |
| 5 | Capitalisation's reverse paths dead-ended | 1a4dbe6 |
| 7 | The register left out capitalised drafts | 37a507f |
| 8 | Depreciation left 0.01 for an extra month | 30daa61 |

New `audit_invariants` checks: `corrections_dated_without_the_rule` (with
17 named exemptions in other modules), `kept_settings_read_live`,
`entries_kept_past_the_edit_guard`.

## Races (`claude/erp-fix-race`)

- **14c9743, two people editing one record at once.** The second save
  undid the first. Now `AuditableViewSetMixin` locks the row for update
  and destroy (FOR NO KEY UPDATE). A model whose `save()` locks other
  rows first declares them in `locked_before_it()`. Proven on PostgreSQL.
- **621f823, call-offs racing past their blanket line.** Proven on
  PostgreSQL, the reverse path too.

## Manufacturing (`claude/erp-fix-make`)

The agent was stopped twice by usage limits and resumed. Its branch holds
what it committed. The findings, in its order:
1. posted floor documents deletable;
2. material return not checked against its issue line;
3. output, by-products and returns in another unit, about 1,000 times
   off the ledger (also stores audit finding 1);
4. a released order's item, BOM and rework could be changed by PATCH;
5. a backflush issue voided alone;
6. a run closed with a clock running;
7. `void_bags` voided a bundle in a sealed bale;
8. TimeBooking.void didn't re-check the next step;
9. job-work challan sibling quantities;
10. the WIP-account guard against reopen;
11. a re-batch void re-priced at today's average;
12. voids booked at the posted cost, not `cost_of_removing`;
13. backflushed runs couldn't use lot-tracked components;
14. one substitute serving two components.

It also has a race family: voids that read the order as open without
taking its lock. Read the branch's log, and the agent's report if one
arrived (notes.md), before integrating.
