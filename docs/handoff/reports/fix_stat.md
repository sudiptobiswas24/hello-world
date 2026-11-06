# fix_stat: O177-O181 (review_stat2)

Worktree scratchpad/fix_stat, on c7d2407. 4 commits, tip 9aea1af. Not pushed.
Calc script: scratchpad/calc_fix_stat/calc.py. Logs: scratchpad/calc_fix_stat/*.log.

## Commits
- 832fee7 O177, O178: the supplier-note key is the number trimmed and upper-cased, within the April year (no digit-run joining).
  Migration 0063 (edited in place, not yet deployed) has a frozen copy of the key and no live import. Where old rows collide
  under it, the earliest (bill_date, pk) keeps the key; the rest stay unkeyed ("" so the conditional constraint skips them) and
  are printed by number. record_supplier_note refuses a non-string or >64 chars (400).
- 2c2e87b O179: objection/removal/withdrawal stamp updated_by (now in OBJECTION_FIELDS); the API history line carries what
  was keyed (one line in apps/core/audit.py: an action may set `_action_summary`). New permission
  purchasing.object_goodsreceipt (migration 0065, AlterModelOptions on goodsreceipt only) for Warehouse Staff and AP
  Manager; AP Manager also gets view goodsreceipt. Objection text: string, at most 255 chars.
- dc50b1b O180: msme_bills gives one row per delivery part (new keys `amount`, `due_pending`; `days_late` None for a
  standing objection). Home check counts distinct bills. MsmeReport.tsx: row id by position, part column, "Objection stands"
  (tsc --noEmit clean).
- 9aea1af O181: e-invoice buyer block uses the buyer's own billing address when bill-to = ship-to lies outside the GSTIN's
  state (site goes in ShipDtls); Invoice.write_off refuses a day before the last standing payment applied; OutsideMovement.void
  asks the booking month only when there is no entry to reverse.

## Tests that failed without each fix
- O177: apps.purchasing.tests.SupplierNoteKeyMigrationTests.test_old_rows_one_under_the_key_are_reported_not_merged
  (tag migration; the reviewer's MigrationExecutor test extended with "cn-9", " CN-9 ", "CN/009"):
  `IntegrityError: UNIQUE constraint failed: purchasing_bill.vendor_id, purchasing_bill.supplier_note_key`
- O178: apps.gst.tests_gstr2b.SuppliersCreditNoteTests.test_two_notes_whose_digits_run_together_are_two:
  `ValidationError ... already answers KA's credit note X/1/12`; test_a_number_that_is_not_text_or_too_long_is_refused_in_words:
  `AssertionError: 500 != 400`
- O179: apps.purchasing.tests_msme.AcceptedOnDeliveryTests.test_an_objection_is_keyed_by_a_login_on_record_from_stores_or_accounts:
  `403 != 400` (AP Manager); with only audit.py reverted: `[('apmanager', ''), ...] != [('apmanager', 'objection made 2026-06-12'), ...]`
- O180: test_each_delivery_is_late_from_its_own_day_in_the_report, test_a_standing_objection_is_due_pending_not_on_time,
  test_each_delivery_a_bill_covers_is_due_on_its_own_day: `KeyError: 'amount'`
- O181: apps.gst.tests_einvoice.PlaceOfSupplyTests.test_a_registered_buyer_billed_and_delivered_at_its_site_across_the_state_line:
  `MH-REG is in Karnataka, but the registration is in Maharashtra.`; apps.sales.tests_statements...test_a_write_off_is_not_dated_before_the_money_it_settles_against:
  `ValidationError not raised`; apps.gst.tests_itc04...test_a_value_receipt_of_a_closed_month_is_voided_in_an_open_one:
  `Jun is closed: OUT-2026-00001, booked on 2026-06-01, is not voided.`

## Runs (at the tip's content, before committing)
- fastsettings, --exclude-tag migration, parallel 2: apps.purchasing apps.gst apps.sales apps.core apps.web
  apps.manufacturing apps.accounting: Ran 4159, OK (skipped=104).
- apps.purchasing --tag migration (default settings): Ran 3, OK.
- audit_invariants: "No invariant findings." makemigrations --check: "No changes detected".
- Not run: PostgreSQL, Asia/Kolkata, browser tests, npm build.

## Decisions and changed tests (say so to the integrator)
- O177/O178 key is deliberately plainer: "CN-9" and "CN 9" or "CN/009" are now two notes. The old test
  test_one_credit_note_is_answered_once_in_its_financial_year was rewritten to "CN-9" against " cn-9 " (and "CN/1" against
  "cn/1" across the year boundary) because it encoded the joined key the brief removes.
- O179 dates: the bounds the brief names (not after the keying day, not before delivery) already held; the change is the
  actor, the history summary and the role. Backdating within those bounds remains possible by design (the letter's date),
  now attributable. A removed objection can still be withdrawn; its history line keeps the erased dates.
- O180 changes the report's shape (rows per part). test_each_delivery_a_bill_covers_is_due_on_its_own_day's report assertion
  was rewritten to the two rows. The 49 queries were not a one-line prefetch (lots() queries per bill line and per receipt); left.
- O181(c): a void of a closed-month value receipt now changes that month's ITC-04 "received" rows (it reads the movement by
  its date and leaves voided ones out), as before O158. The brief chose this; not re-examined.

## Defects seen and not fixed
1. apps/purchasing/msme.py (msme_bills row loop), statutory/minor, read not run: a part settled wholly by a debit note or TDS
   (no payment) has paid_on None, so days_late runs to as_of: 1,000 received 1 Jun, net 60 (due 16 Jul), fully debited on
   10 Jun, report as of 31 Dec reads unpaid 0.00, days_late 168. Same as the per-bill row did before.
2. apps/purchasing/models.py:3582 Bill.pay_by() / the bill's displayed due_date is still the whole bill's when one delivery is
   objected to (review_stat2 #6, last sentence): with lot 2 objected, pay_by 24 Aug while 600 is due 16 Jul. The payment run
   uses installments() and is right. statutory/display.
3. apps/purchasing/migrations/0063: an unkeyed (collided) draft debit note edited through the API runs clean(), which re-keys it;
   the database constraint then refuses the save (400 via the exception handler, wording generic). minor.
4. apps/purchasing/views.py supplier_note and objection: a JSON body that is a list, not an object, makes request.data.get raise
   AttributeError (500). Read, not run; likely common to every action reading request.data.get. crash/minor.
5. Review_stat2 #11 (auto draw-down day vs apply_deposit's rule) and #12 (RISKS rows for O158-O165 still listed) are not my rows.
