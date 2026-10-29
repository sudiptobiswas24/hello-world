# Review of the statutory follow-up commits 81591a7..c7d2407 (O162+O161, O158, O159, O160, O163, O165, O164)

Reviewed at c7d2407 in a detached worktree (SQLite, fastsettings, nice 19; migration tests on the default settings
with `--tag migration`). PostgreSQL and Asia/Kolkata were not run. Probes and logs are in
`scratchpad/reports/review_stat2_tests/` and beside this file (`review_stat2_*.log`).

Result: 1 serious finding (a migration that crashes on the data it exists for), 5 medium, 4 minor. The first review's
probes (tests_rv_o50/55/57/61/62/102/103) all pass or fail only for stated reasons. `audit_invariants`: "No invariant
findings". `makemigrations --check`: "No changes detected". Migration numbers do not collide (one leaf per app;
purchasing 0062 -> 0063 -> 0064, hr 0020 -> 0021). Touched-module run: 4,462 tests (accounting, gst, hr,
manufacturing, purchasing, sales, core; `--exclude-tag migration`, parallel 2): 12 failures + 6 errors, every one in my
own probe files or in the old `apps/accounting/tests_probe_audit.py` I copied in so tests_rv_o55 would import (listed at
the end). None in the repo's own tests.

Kinds: books / statutory / rule / crash / minor. S marks the serious one.

## Findings

### 1. S, books (deploy-blocking). Migration purchasing 0063 crashes on the very data review #12 describes
- Commit ae9aeb2. apps/purchasing/migrations/0063_a_supplier_credit_note_is_one_in_its_year.py:7-14 (the RunPython
  `key_the_notes_recorded`) then :51-62 (`AddConstraint` on `(vendor, supplier_note_key)`).
- The old constraint was exact on `(vendor, supplier_note_number)`, so "CN-9" and "cn-9" for one vendor could both be
  saved (review #12 showed it). The migration keys every row, then adds the unique constraint; two such rows get one key.
- Scenario (tests_rv2_migrations.py, Purchasing0063.test_old_rows_that_differ_only_in_case_or_separators): vendor V1, one
  bill, two debit notes with "CN-9" (10 Jun 2026) and "cn-9" (11 Jun 2026). Migrating forward from 0062 raises
  `IntegrityError: UNIQUE constraint failed: purchasing_bill.vendor_id, purchasing_bill.supplier_note_key`. The same holds
  for "CN/009" against "CN-9". Nothing detects or reports the duplicates first, and the data is not fixable from the
  application because the posted notes are immutable. On PostgreSQL the migration would roll back (not run), leaving the
  database on 0062 with code that expects the new field.
- Smaller: the migration imports live code (`from apps.purchasing.models import supplier_note_key`, which reaches
  `apps.accounting.gst.document_number_key` and `financial_year`), so changing either later changes what this old
  migration computes.
- Held: for data that can exist, the keys are right (test Purchasing0063.test_keys_each_note_in_its_year): "CN-9" dated
  10 Jun 2026 -> `2026:CN9`; "CN 9" dated 1 May 2027 -> `2027:CN9`; "CN/007" with no date on a debit note of 10 Feb 2027
  -> `2026:CN7`; "X 5" dated 31 Mar 2027 -> `2026:X5`; "X 6" dated 1 Apr 2027 -> `2027:X6`; same number on another vendor
  allowed; no number and a plain bill -> "".

### 2. medium, crash. `supplier_note` answers 500 to a number that is not a string
- Commit ae9aeb2. apps/purchasing/views.py:318-330 passes `request.data.get("supplier_note_number") or ""` raw;
  apps/purchasing/models.py:3166 `number = (number or "").strip()`.
- Scenario: AP Manager POSTs `{"supplier_note_number": 123}` or `["x"]` to `/api/purchasing/bills/<debit note>/supplier_note/`:
  500 "Something went wrong on the server. It has been logged as E-X2KLX". `objection` (views.py:410) does `str(...)`, so a
  list is saved as the text "['a']" (200) instead: junk accepted.
- Held: a bad date string is a 400 ("'bad' is not a date"), a future date a 400, a date before the bill a 400, a plain bill
  a 400.

### 3. medium, crash (PostgreSQL only; read, not run). No length check on the two new text fields
- Commit 0e3bfc3 and ae9aeb2. apps/purchasing/models.py:5836 stores `text` into `objection` (max_length 255);
  models.py:3166-3175 stores the supplier note number (max_length 64). Both save with `update_fields`, which does not run
  `full_clean()`.
- Scenario: a 300-character objection and a 65-character note number are accepted with 200 on SQLite (run). PostgreSQL
  enforces varchar(n): `DataError`, a 500. Not run here (no PostgreSQL in this container).

### 4. medium, rule. The credit-note key joins digit runs, so two different supplier notes read as one and the second is refused
- Commit ae9aeb2. apps/accounting/gst.py:259-268 `document_number_key`; used by `supplier_note_key` and the unique
  constraint.
- Scenario (tests_rv2_o159.py test_d): one vendor, two debit notes. "X/1/12" dated 20 Sep 2026 is recorded (key `2026:X112`).
  "X/11/2" dated 25 Sep 2026 is a different note on the supplier's paper (11th month, 2nd) but has the same key; it is
  refused with "already answers KA's credit note X/1/12; one credit note is answered by one debit note". Month/serial
  numbering across one financial year hits it; there is no override. Run: `k("CN/11/2")` and `k("CN/1/12")` are both
  `2026:CN112`. The same normalisation was only a pairing hint in GSTR-2B before; it is now a hard constraint.
- Also: "---" (separators only) is accepted and stored with key "" so the uniqueness does not apply to it.
- Held: "cn 9", "CN/009", "CN9" against "CN-9" in one year are refused (as intended); "CN/1" in two financial years is
  allowed by the key function.

### 5. medium, statutory (control). An objection and its removal can be keyed after the fact on any dates, with no actor on the row
- Commit 0e3bfc3. apps/purchasing/models.py:5814-5870 (`record_objection`, `remove_objection`, `withdraw_objection`),
  OBJECTION_FIELDS at :5799, view at apps/purchasing/views.py:410, role at
  apps/core/management/commands/setup_roles.py:305.
- Scenario (tests_rv2_o160.py test_backdated_objection_erases_lateness): 10 units received 10 Jun, bill 25 Jun net 60
  (Act date 25 Jul), paid in full 20 Sep. Report as of 30 Sep: due 25 Jul, days_late 57. On 30 Sep a Warehouse Staff
  login keys an objection dated 12 Jun (inside the 15 days) and "removed 10 Aug": due becomes 24 Aug, days_late 27. The
  paid bill's lateness is rewritten after the event. DELETE withdraws an objection (even a removed one) and erases it with
  no record. After the call the receipt shows `updated_by` None (the actor is not in `update_fields`); the history table
  was not checked.
- Roles (run, API): POST `/api/purchasing/goods-receipts/<id>/objection/` is 200/400 for Warehouse Staff and 403 for AP
  Manager, Purchasing Clerk, Quality Inspector, Controller, GST Officer, Bookkeeper and anonymous. So stores staff can
  move a statutory payment date of a payable and the accounts-payable role cannot. Whether that is the intended owner is
  a question for the brief.
- Held: bad date -> 400; day before delivery -> 400; 15th day -> 200 (inclusive), 16th -> 400; empty text -> 400; a second
  objection -> 400; removal before the objection or in the future -> 400; PATCH of a posted receipt still 400; a
  routine inspection moves nothing (pay_by stays 25 Jul).

### 6. medium, statutory. The MSME report row still runs from the last delivery; a standing objection blanks the row
- Commit 0e3bfc3. apps/purchasing/msme.py:187 (`due = msme_due(bill)` = the last part's day) and :196
  (`late = max((last - due).days, 0) if due else 0`); `at_risk` (the new loop, :198-207) is per part and right.
- Scenario (tests_rv2_o160.py test_two_deliveries_one_bill): 6 received 1 Jun, 4 received 20 Jun, one bill of 1,000 on
  25 Jun net 60. `installments()` is right: 600.00 due 16 Jul, 400.00 due 4 Aug. Paid in full on 10 Aug the report row says
  due 4 Aug, days_late 6, but the 600 was 25 days late. Interest-bearing days are understated per part.
- Objection standing: net 30 bill of 25 Jun, objection of 12 Jun never removed, as of 31 Dec: `due` null, days_late 0,
  at_risk 0, unpaid 1,000.00 (nothing says why, and there is no limit on how long an objection can stand). With only lot 2
  objected, `pay_by()` is 24 Aug while 600 is due 16 Jul; the payment run itself uses `installments()` and is right
  (models.py:5702), only the displayed `due_date` (:5716) is the whole bill's.

### 7. medium, minor. Query cost of the new schedule
- apps/purchasing/msme.py:41-100 `lots`, called through `Bill.act_schedule()` from `installments()` and `pay_by()`.
- Run (tests_rv2_o160.py test_query_counts): one `installments()` on a micro bill with 2 deliveries is 49 queries; a
  fresh `pay_by()` is 21. Aging, dunning and the payment run call these per bill. I did not count the old `accepted_on`.

### 8. minor, statutory. O161 posts an invoice whose e-invoice then cannot be built, and the invoice's address cannot be edited
- Commit 4ce3411. apps/accounting/gst.py `_placed` (billed_there branch); apps/gst/einvoice.py `build`.
- Scenario (tests_rv2_place.py): registered Maharashtra buyer (GSTIN 27...), one Karnataka address as bill-to and ship-to,
  2 sacks at 1,000: posts at place 29 with IGST 360.00 (GSTR-1 place 29, e-way bill to state 29). `build(invoice)` refuses:
  "MH-REG is in Karnataka, but the registration is in Maharashtra". The address cannot then be edited
  (Address.save: "A posted invoice prints this address"), so the way out is a credit note and a new invoice. Not caught
  at post. The refusal is old; O161 newly creates the invoices that reach it.

### 9. minor, rule. Review #7(b) is still open and has no row of its own
- Commit 8cea3c3 guards `recover_write_off` only (apps/sales/models.py, `correction_date(on_date, write_off.journal_entry.date, ...)`).
- Run (tests_rv_o55): invoice of 1,000 on 10 Sep, 600 received and applied 25 Sep, write-off of 400 dated 15 Sep:
  accepted; receivables on 20 Sep read 600 while 1,000 was owed. The recover probe now passes ("not recovered on
  2026-09-12: it was written off on 2026-09-20"). RISKS.md has O55 and O80 touching write-offs for other reasons.

### 10. minor, rule (decision). A value receipt booked in a closed month can no longer be voided at all
- Commit 8ac1c83 / 4b2383e. apps/manufacturing/outside.py:240-241 (`_refuse_closed(self.movement_date, ...)` for every
  void, not only the zero-value ones).
- Run (tests_rv2_o165.py): receipt OUT-2026-00001 of value 1,400 dated 1 Jun, June closed: `void()` today is refused
  "Jun is closed: OUT-2026-00001, booked on 2026-06-01, is not voided". Before, its reversal went into the open month. The
  commit chose this because ITC-04 reads by movement date; worth saying so in the brief, since "corrections are reversals".
  Held: a void dated before the booking, or a day to come, is refused (O165).

### 11. minor, minor. Auto draw-down compares the given day with the deposit's day only
- apps/sales/models.py:2225 and apps/purchasing/models.py:3324-3325: `day = max(on_date, deposit.invoice_date)`, while
  `apply_deposit` (:2145) refuses anything before the later of the invoice's and the deposit's days.
- Run: invoice 6 Mar, deposit 1 Mar, `apply_available_deposits(on_date=1 Feb)` raises "is not applied to INV-2 on
  2026-03-01: the later of the two is dated on 2026-03-06". Safe from `post()` (it passes the invoice's own date), so
  only a direct call sees it.

### 12. minor, process
- None of the seven commits touches docs/RISKS.md (`git diff 81591a7 c7d2407 --stat -- docs` is empty); O158-O165 are all
  still listed. All seven commit messages still contain the "{GATE}" placeholder (7 of 7).

## Requested checks, with numbers

### Migrations (MigrationExecutor in a TransactionTestCase, `--tag migration`; tests_rv2_migrations.py)
- hr 0021: passes. Claims built as old code left them: paid once (entry E1) -> {E1}; paid and unpaid (E2, reversal R2) ->
  {E2, R2}; paid, unpaid, paid (current E3b, last reversal R3a which reverses E3a) -> {E3a, R3a, E3b}; approved never paid
  and draft -> {}; paid, unpaid, paid, unpaid (current E5b, R5b) -> {E5b, R5b} only: the first pair is already gone from
  the old columns and cannot be recovered, so a bank line for it stays unmatchable. After migrating, `claims_paid()` returns
  exactly the eight expected entries.
- purchasing 0064: passes. A posted and a draft receipt keep `objected_on` None, `objection` "", `objection_removed_on`
  None; `acceptance()` of the posted one is its receipt date (10 Jun 2026).
- purchasing 0063: finding 1.

### O160 through the API (Warehouse Staff; seeds as above)
- Net 60 bill of 25 Jun for goods received 10 Jun, pay_by 25 Jul. Objection of 12 Jun recorded: pay_by 24 Aug (the terms'
  date; the Act's clock has not started). Removed on 20 Jun: 4 Aug (20 Jun + 45). Withdrawn: 25 Jul again. The bill was
  posted before the objection each time, and its schedule followed. Several deliveries: 600.00 / 400.00 by their own days
  (16 Jul, 4 Aug). A property run of `earlier_of` (3,000 random schedules against random Act parts, including parts with
  no day): amounts always add to the total, dates never decrease.

### O159
- AP Manager (`purchasing.post_bill`): 200. Purchasing Clerk, Bookkeeper, Controller, GST Officer, Warehouse Staff: 403.
  A typo "CN-8" is put right to "CN-9" (200), as the commit's own test shows. Uniqueness: see findings 4 and 1.

### O161 / O162 / credit note after an edit
- Held. `Address.save` refuses to change an address a posted invoice prints, so a ship-to edit after posting is only
  possible with a queryset `update()`. Done that way: invoice 2 sacks, credit note for 1 after the state was changed to 27
  and again to blank: place 29, IGST 180.00 each time, posted. GSTR-1 stays at 29; e-invoice stays at 29; the e-way bill
  reads the live address (toStateCode 29, actToStateCode 27; after blank it raises) which Address.save normally prevents.
- Draft with an unplaceable ship-to: list and detail answer 200; PATCH of another field 400 with the address sentence;
  deleting a line 204; correcting the address then lets it save. An order line and a quotation line for a bad ship-to are
  refused with no row left.

### O163
- Deposit draw-down against the rate rule: 300 EUR deposit invoice dated 10 Mar at 85 (25,500.00), final invoice of 1,000 EUR
  dated 6 Mar at 83. Posting the final invoice draws down on 10 Mar (the later of the two). Deposit account ends at 0;
  the draw-down entry credits AR 25,500.00 (the rate it came in at); the exchange entry is Dr AR 600.00 / Cr gain 600.00;
  AR is 83,600.00 = 700 EUR x 83 (58,100.00) + the unpaid deposit invoice (25,500.00). The rule holds: the date moves no rate.
- Explicit `apply_deposit` on 5 Mar refused, 6 Mar applied, 2099-01-01 refused ("that day has not come").
- Not probed one by one (budget): `apply_settlement_discount`, `take_settlement_discount`, `credit_old_supply`,
  `debit_old_supply`, `apply_prepayment`, `ExpenseClaim.pay`. I read them; the commit's own tests and the green suite
  cover before-original and `LATER`. `recover_write_off` before the write-off: refused (run). Allocation edit keeping its
  day: tests_rv_o50 shows edits at today's stored date; the commit's own test shows March refusing an undated edit.

## What I tried and could not break
- hr claim bank entries (O164): migration and `claims_paid()` as above; `bank_entries.add` is inside the pay/unpay atomic.
- O158/O165: tests_rv_o61 and tests_rv2_o165 (day before, day to come, closed month, zero value) all as the commits say.
- O159 guard: after a GSTR-2B line carries "CN-9", recording "CN-10" is refused (the commit's test), a plain bill is refused.
- `makemigrations --check`, `audit_invariants`: clean.

## Probe failures in the touched-module run (all mine or old probes, none in repo tests)
- tests_rv_o60 x3 (test_rv_blank_state_own_address, test_rv_draft_api_blank_state, test_rv_no_party_ka_registered): they
  build the unplaceable draft the commit now refuses; the fix working.
- tests_rv_o103.test_edit_vendor_category_and_debit_note: my fixture bills before any receipt ("this order is billed on
  receipt"); not run at 81591a7, so I do not say it is new.
- tests_rv2_place.Place2.test_d (gstin shape in my fixture), tests_rv2_o163.test_explicit_dates (the last call raises, which is
  finding 11).
- tests_probe_audit.py (old audit probes, copied from the audit_acc snapshot for tests_rv_o55's import): FxResidueProbe x3,
  StatementProbe x2, RepointProbe x3, DebitNoteRefundProbe, CostCentreProbe, ChallanOnTheStatementProbe,
  Gstr2bNoteProbe. Still failing exactly as the first review listed, for rows other than these seven.
