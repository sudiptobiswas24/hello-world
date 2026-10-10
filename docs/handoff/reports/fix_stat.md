# fix_stat: O158-O164 (statutory follow-up)

Worktree scratchpad/fix_stat, on top of 81591a7 (the 11 statutory commits untouched). SQLite, fastsettings,
migration-tagged tests excluded. PostgreSQL not used: no race fix was made, so erp_fixstat was not needed.

## Commits (tip ae9aeb2)
| commit | ids | what |
|---|---|---|
| 4ce3411 | O162, O161 | place_of_supply split into place + refusal; refused at save/post, never at read; bill-to = ship-to is the buyer's own address |
| 8ac1c83 | O158 | OutsideMovement post/void ask the period; reported_without_the_period follows imported functions, methods and reverse accessors |
| ae9aeb2 | O159 | supplier credit note recorded on a posted debit note (model + API action); uniqueness by 2B key within the FY |

O161 and O162 share one commit: both are the same function (`_placed` in apps/accounting/gst.py), rewritten once.

## Tests that failed without each fix
- O162 `apps.gst.tests_einvoice.PlaceOfSupplyTests.test_a_draft_whose_ship_to_cannot_be_placed_still_lists_and_opens`:
  `AssertionError: 400 != 200` (GET /api/sales/invoices/ as a Controller). Also
  `test_an_address_of_no_party_is_not_placed_by_a_guess` now expects its refusal at the line's save:
  `ValidationError not raised` without the fix. That test was changed in intent only where it said when: the
  owner's rule moves the refusal from post to save.
- O161 `...PlaceOfSupplyTests.test_a_registered_buyer_billed_and_shipped_at_one_address_across_the_state_line`:
  `([('CGST9', 90.00), ('SGST9', 90.00)], '27') != ([('IGST18', 180.00)], '29')`
- O158 `apps.gst.tests_itc04.AReceiptOfNoValueAsksThePeriodTests` (both tests): `ValidationError not raised`;
  `TheAuditAsksTheCloseTests.test_a_receipt_reached_through_a_function_and_a_relation_is_reported`:
  `Lists differ: [] != ['manufacturing.OutsideMovement']` (planted: outside.py with `_refuse_closed(` removed).
- O159 `apps.gst.tests_gstr2b.SuppliersCreditNoteTests.test_recorded_on_the_posted_note_when_it_arrives`:
  `AssertionError: 404 != 403`; `test_one_credit_note_is_answered_once_in_its_financial_year`:
  `ImportError: cannot import name 'supplier_note_key'`.

Runs: sales+gst+accounting 4ce3411 OK; gst+manufacturing+core 2086 tests OK (skipped=1) at 8ac1c83;
purchasing+gst+accounting 1294 tests OK at ae9aeb2. audit_invariants "No invariant findings" and
makemigrations --check "No changes detected" before each commit.

## Notes on the fixes
- O162: `check_place()` on PlacedWhereTheGoodsGo is asked by Invoice.save (draft, not adding), Invoice.post,
  SalesOrder.save (stored row a draft, so confirm too), Quotation.save, and the goods-line saves of all three
  (invoice and quotation lines inside an atomic block; the order line after save, as its credit-limit refusal
  already does). A credit note keeps its invoice's place and is not asked. Reading falls back to the principal
  place, so a draft whose address is edited bad later still opens; post refuses it.
- O161: an address that is the bill-to as well is the buyer's own (s.10(1)(a)) and places at its state. When
  that shared address has no readable state it still stands at the buyer's state on record, as before (not a
  refusal), to keep the old behaviour for buyers whose only address lacks a state.
- O158: the widened walk is in `reported_without_the_period`; it adds about nothing to the audit's 65 s run.
  A nullable journal entry no longer counts as asking. Four documents a return reaches are exempt with reasons
  in `POSTS_AN_ENTRY_EVERY_TIME` (Invoice: post refuses a total of nothing; Bill: likewise; Payment: check
  constraint amount > 0; Delivery: read only by the e-way bill). Delivery's reason must be revisited if
  delivery challans go into table 13.
- O159: permission is purchasing.post_bill (the one that issues debit notes); a reader with view_bill gets 403.
  The 2B guard lives in apps/gst (registered in GstConfig.ready, as the challan guard is), so purchasing does
  not import gst. Migration purchasing 0063 backfills `supplier_note_key` with RunPython before the new
  constraint; it imports `supplier_note_key` from models (a pure function), not tested with MigrationExecutor.

## Not done (budget reached at ~70 calls)
- O160 (MSME acceptance), O163 (recover_write_off and the seven exempted steps onto correction_date; allocation
  edit keeping its date), O164 (post_to refusal, claim paid/unpaid twice, table 13). Untouched; rows stay open.
  O160 pointer: apps/purchasing/msme.py:27-56 `accepted_on` takes the latest accepted ReceiptInspection within
  15 days as acceptance; it needs an objection fact (e.g. objected_on / objection_removed_on on the receipt,
  objection within 15 days of delivery), acceptance = delivery day otherwise, and `pay_by` per delivery (a
  schedule of (date, amount)) read by the payment run, aging and the MSME report.
- O58 (negative rows), asked to be left: left as is. The return is a JSON report whose quarters tie to the
  ledger; a reversal in a later quarter is shown where it happened. The statutory form is a correction
  statement of the original quarter; that matters only when an FVU export is built, which must then turn a
  negative row into a correction of the quarter it reverses. Nothing files from it today.
- Table 13 (O164 part 3), read only, no code: Rule 55 challans are job work (nature 9, counted), supply on
  approval, liquid gas, and movement other than by way of supply. A sales Delivery that moves goods sold is
  covered by its tax invoice (s.31(1)(a): invoice at or before removal), so it is not a table-13 challan. What
  looks wrong instead is apps/gst/ewaybill.py:~500 giving a sales delivery `docType "CHL"`; an owner decision.

## Defects seen and not fixed
1. apps/manufacturing/outside.py `OutsideMovement.void` dates the void `to_date(on_date) or
   timezone.localdate()` without `correction_date()`: a receipt of 1 June can be voided "on" 20 May (before it
   was booked) if May is open. Kind: rule (audit shape "a correction dated anywhere"). The period is now asked;
   the ordering rule is not.
2. apps/accounting/gst.py `_placed`: a goods-and-service invoice places every line by the goods rule
   (`moves_goods` is per document); and `moves_goods` runs a query per line whenever a total is asked
   (review #15, still true). Kind: statutory / minor.
3. A buyer's shared bill-to/ship-to address with a blank state places at the state on record without a word
   (kept deliberately, see O161 above). Kind: minor.

# Second pass: O160, O163, O165, O164 (tip c7d2407)

| commit | ids | what |
|---|---|---|
| 0e3bfc3 | O160 | written objection recorded on a receipt; acceptance = delivery day or the objection's removal; each delivery's share due on its own day |
| 8cea3c3 | O163 | recover_write_off and six of the seven steps on correction_date; allocation edit keeps its day |
| 4b2383e | O165 | OutsideMovement.void on correction_date |
| c7d2407 | O164 | post_to refuses a line already booked; a claim keeps every entry it posted |

## Tests that failed without each fix
- O160 `apps.purchasing.tests_msme.AcceptedOnDeliveryTests`: routine inspection `date(2026, 8, 4) != date(2026, 7, 25)`;
  several deliveries `[(2026-08-04, 1000.00)] != [(2026-07-16, 600.00), (2026-08-04, 400.00)]`; objection API
  `404 != 403`. `test_two_bills_for_two_deliveries` passed before and after (a guard).
- O163 `apps.sales.tests_credit_after_settlement...test_steps_that_post_afresh_are_dated_on_the_rule`,
  `apps.purchasing.tests_audit...test_not_taken_before_the_bill_nor_a_prepayment_applied`,
  `apps.gst.tests_old_supply...test_neither_note_is_dated_before_what_it_corrects`,
  `apps.hr.tests_people.ClaimTests.test_not_paid_before_it_was_claimed`: `ValidationError not raised`.
  With only settlement.py reverted: `(400.00, date(2026, 10, 10)) != (400.00, date(2026, 3, 1))`.
- O165 `apps.gst.tests_itc04.AReceiptOfNoValueAsksThePeriodTests.test_not_voided_before_it_was_booked_nor_ahead`:
  `ValidationError not raised`.
- O164 `apps.hr.tests_people.ClaimOnTheStatementTests.test_the_claims_line_is_not_posted_a_second_time`:
  `ValidationError not raised`; `...test_paid_and_unpaid_twice_keeps_every_pair`: `Items in the second set but not the first`.

Gate runs, each with audit_invariants clean and makemigrations --check clean: purchasing 866 OK (O160);
sales+purchasing+accounting+hr+gst 2429 OK (O163); manufacturing+gst 1812 OK (O165);
accounting+hr+purchasing+sales+e2e+core 2678 OK, 63 skipped (O164).

## Decisions and questions for the coordinator
- O163, `ExpenseClaim.pay`: not fully on correction_date. It refuses a day before its claim, but a day ahead
  stands, because `test_a_payment_dated_ahead_is_reversed_on_its_own_day_and_no_other` relies on paying ahead
  (unpay then cancels on that day). The step is back on DATED_ELSEWHERE with that reason. If the owner wants
  pay-ahead refused, change that test and swap in correction_date.
- O163, deposits and prepayments: the automatic draw-down at posting now uses the later of the document's day
  and the deposit's or prepayment's day. 28 fixtures post a final invoice in March against a deposit made
  today; refusing those posts would have been the alternative.
- O163, tests changed because they encoded the defect: fx `test_an_edit_restates_it_on_the_day_of_the_edit`
  (an edit with no day into closed March is now refused; with today given it re-states today); the hr API
  claim test paid a claim made today on 3 June; gst old-supply `LATER` was 12 Oct, two days ahead of today,
  and is now 1 Oct.
- O164, test changed: `ClaimTests.test_submitted_decided_by_the_manager_and_paid_as_one_journal` asserted the
  first reversal "belongs to nothing" after a second unpay; it now belongs to the claim. `recorded_by()` skips
  many-to-many tables so it returns the claim, not the link row.
- O164 post_to: it refuses only a same-day, same-amount registered movement that no line is matched to yet. A
  cheque that clears days later is not caught; that would need a wider window and risks false refusals.
- O160: objections are fields on GoodsReceipt (`objected_on`, `objection`, `objection_removed_on`), one per
  receipt, recorded under `purchasing.post_goodsreceipt` (Warehouse Staff). Whether purchasing should hold
  that permission instead is for the owner. Returns (`reverses`) are not netted out of a delivery's share.
  Charges, and quantity billed ahead of its goods, run from the bill's date.
- Data migrations purchasing 0063 and 0064 and hr 0021 were read and run under the suite's migrate, but not
  tested with MigrationExecutor. Nothing was run on PostgreSQL or Asia/Kolkata.
- O166 (e-way bill "CHL") was left untouched, as told.

## Defects seen and not fixed
1. apps/purchasing/msme.py `lots()`: return receipts are left out, so a returned quantity still counts in the
   delivery it came from. Example: 10 received 1 Jun, 4 returned 5 Jun, 4 re-delivered 20 Jun, all 10 billed
   25 Jun. The first 10 units are drawn from the 1 Jun lot, so the whole 1,000 is due 16 Jul. The 400 that
   came back on 20 Jun is owed by 4 Aug. The error runs early (it pays sooner), not late. Kind: statutory, minor.
2. apps/sales/models.py `write_off` (review #7b, not in the brief): a write-off can still be dated before a
   later allocation, so receivables read 600 on a day 1,000 was owed. Kind: rule.
