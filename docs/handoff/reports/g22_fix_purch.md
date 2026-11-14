# fix_purch, gate g22: report

Worktree: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/fix_purch (detached HEAD, base e73428e, tip 674ac8a). Nothing pushed.
Calculations: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/calc_fix_purch/ (o91.py, o188_o189.py, o211.py, o233.py, o234.py).

Every commit ends with `Gate: {GATE}` and the two attribution lines.

## Commits, base e73428e (10 commits, 13 ids)

| Commit | Closes | What |
|---|---|---|
| 7aa5347 | O91 | The match's price check compares each side's price net of its discount (`net_unit_price`). The refusal says "ordered at 100 less 10% (90 a unit) but billed at 100". The match report's price variance is net as well. |
| 6a113c1 | O99, O167 | One shared rule, `apps.core.approvals.ChangesWithinItsApproval`, used by both SalesOrder and PurchaseOrder. Purchasing's `policy_breaches()` feeds both `approval_reasons()` and the re-check made on every change to a confirmed order: total, line value and agreed price, deletes included. A line priced above the agreed price is now a reason for approval (O99). Purchasing 0075 adds `PurchaseOrder.approved_figures`. This commit touches apps/sales/models.py (SalesOrder moves onto the shared mixin). |
| 77dbc52 | O95 | `Bill.reference_key` / `vendor_invoice_key()`: case ignored, spaces and `-` `/` `.` removed, one key per vendor per financial year. A partial unique constraint replaces the exact one, and a clash is refused in words beside `reference`. Purchasing 0076 keys existing bills earliest first; collisions are left unkeyed and printed. Tested with MigrationExecutor. |
| a279f1d | O154 | On a posted debit note, the supplier's credit-note number and date are given once, through the existing `supplier_note` action (purchasing.post_bill), and never changed after. GSTR-2B already pairs on it. One test in apps/gst/tests_gstr2b.py encoded the old rule and was changed. |
| d406a19 | O188, O189 | MSME report: debit notes, TDS, prepayment drawdowns and settlement discounts each settle on their own document's date. `pay_by()` reads `installments()`; `pay_by(by=day)` gives the date the payment run shows. |
| a71fad5 | O190 | The supplier-note clash is checked in `_check_supplier_note()`, so clean() refuses it in words. One assertion in apps/gst/tests_gstr2b.py expected the IntegrityError and now expects the worded refusal. |
| efa228d | O227 (refuted) | No code change. `BlanketCallOffRaceTests` (apps/e2e/tests_races.py) runs on PostgreSQL: there is no lock cycle, and the blanket's lock is what holds the agreed total. |
| 7f128aa | O233 | Dated TDS rates. The shared rules live in apps/accounting/dated_rates.py (`DatedRate`, `rate_standing`), and TcsRate and TcsSection now use them (touches accounting/tcs.py; wording unchanged). New `TdsRate` model. Each deduction reads the confirmed rate standing on its own date and keeps that row (`TdsDeduction.rate`). New API `/api/accounting/tds-rates/` with `confirm/`, permission accounting.confirm_tdsrate (Controller only; the AR and AP Managers may read). New office screen "TDS rates"; the TDS section screen no longer shows rates. Accounting 0030 copies each section's two rate figures into a row dated from 1 April 2000, NOT confirmed, before removing the fields (MigrationExecutor test). Purchasing 0077 adds `TdsDeduction.rate`. |
| 6e9531e | O234 | The supplier's TCS on a bill: `tcs_amount`, `tcs_section` and `tcs_account` (a TCS receivable account). `Bill.total()` includes the TCS. Posting books Dr TCS receivable / Cr Payables. A debit note takes back its share, or everything left when it gives back the rest. Refusals are in words. Purchasing 0078 adds the fields; the bill screen shows TCS among the totals. |
| 674ac8a | O211 | `receipts_billed_by()` replays the order line's receipts, returns, bills and debit notes in posting order. `_bill_lines_that_paid_for()` reads which bills hold the returned receipt's goods from that replay. |

Migration numbers as they fall: purchasing 0075, 0076, 0077, 0078; accounting 0030. fix_money also has a purchasing 0075, so the integrator renumbers.

## Tests that failed without each fix (failure line)

- **O91**: ThePriceLegReadsTheDiscountTests.test_a_bill_at_the_gross_price_without_the_agreed_discount_is_refused: "ValidationError not raised".
- **O99**: APriceTypedOverTheAgreedOneTests.test_typed_at_fifty_it_needs_approval: "Lists differ: [] != ["'WDG-1 - Widget' is priced at 50, above the 5 agreed with V-1 - Supplier."]".
- **O167**: AConfirmedOrderChangesWithinItsApprovalTests.
  - Four tests failed with "ValidationError not raised": raised past the limit, raised past what was covered, a line added, re-priced over the agreed price.
  - The cut-within-approval test failed with "unexpectedly None".
- **O95**: OneVendorInvoiceInItsYearTests.test_the_same_number_in_another_case_or_spacing_is_refused: "ValidationError not raised" for each of inv-77, INV 77, inv/77, I.N.V-77 and " INV77 ".
- **O154**: AReturnsDebitNoteRecordsTheSuppliersCreditNoteTests: "200 != 400". Driven through the API as the AP Manager; a Purchasing Clerk gets 403.
- **O188**: PaidOnTheDayOfWhatSettledItTests: "(16 Jul, None, 168, 0.00, 0.00) != (16 Jul, 10 Jun, 0, 0, 0)".
- **O189**: DueDeliveryByDeliveryTests: "(2026-08-24, 600.00) != (2026-07-16, 600.00)".
- **O190**: AnUnkeyedNoteEditedAsADraftTests: "KeyError: 'supplier_note_number'". Driven through the API as the AP Manager.
- **O227**: BlanketCallOffRaceTests, with the agreement's lock in the line's save removed: "2 != 1 : ['done', 'done']" (110 called off against 100 agreed).
- **O233**: DatedRateTests.test_each_deduction_takes_the_rate_standing_on_its_day_and_keeps_it, with today's rate read instead of the dated one: "[(600, 1.5000, 194C 1.5% from 2026-02-01)] != [(800.00, 2.0000, 194C 2% from 2000-04-01)]".
  - Also: an unconfirmed rate refuses the deduction, and a rate already taken at cannot be changed or deleted.
  - The API was driven as the Controller (confirm: 200) and the AP Manager (confirm: 403, read: 200).
- **O234**: TcsTheSupplierCollectedTests.
  - Without the take-back: "(0, 300000.00, 12000) != (3000.00, 303000.00, 9000.00)".
  - Without the fields: "Bill() got unexpected keyword arguments: 'tcs_amount', ...".
  - The API was driven as the AP Manager.
- **O211**: AReturnBeforeTheBillsTests: "[(1, 10.00)] != [(2, 9.60)]".

## Runs

All on SQLite with fastsettings and `--exclude-tag migration`, unless the line says otherwise.

- After O233: apps.purchasing, apps.accounting, apps.sales, apps.core, apps.e2e: 3065 OK (141 skipped).
- After O234: apps.purchasing, apps.accounting, apps.gst, apps.e2e, apps.inventory, apps.assets: 2479 OK.
- After O211: apps.purchasing, apps.sales, apps.e2e: 2387 OK.
- PostgreSQL: BlanketCallOffRaceTests and PurchasingRaceTests.test_two_bills_do_not_both_catch_up_the_year: OK.
- Migration tag, default settings:
  - apps.purchasing and apps.sales at efa228d: 11 OK.
  - apps.purchasing and apps.accounting at 674ac8a: 8 OK.
- Office application at 674ac8a: `npm run build` OK; vitest 42 passed.
- apps.web whole, Playwright with a fresh build: 177 OK at efa228d, and 177 OK at 674ac8a (2034 s, none skipped).
- audit_invariants reported nothing and `makemigrations --check` found no changes before every commit.

Not run: the whole suite on PostgreSQL, and the Asia/Kolkata lane.

## Not done

- O234: TCS can be entered through the API only. The bill screen shows the TCS but has no fields to enter it.

## For the integrator and fix_money

- **Merge risk (O233).** `TdsSection.rate_percent` and `no_pan_rate_percent` are removed. Any test or code from fix_money that makes a TDS section with rates, or deducts TDS, needs a confirmed `TdsRate`. The fixture helper is `apps.purchasing.tests_tds.confirmed_rate(section, rate, no_pan)`.
- **TDS will stop deducting in the plant's data (O233).** Accounting 0030 leaves every TDS rate unconfirmed, so every deduction is refused in words until the accountant confirms each section's rate on the TDS rates screen. That follows "seed nothing as confirmed"; the go-live notes need to say it.
- **Sales tests changed (O167).** apps/sales/models.py moved onto the shared approval mixin. The commit says so; the sales suite passes.
- **Other files I edited in fix_money's area:**
  - apps/gst/tests_gstr2b.py: two assertions (O154, O190);
  - apps/sales/tests_tds.py and tests_screens_api.py, apps/web/tests_browser_tds.py, apps/accounting/tests_money_accounts.py: rate arguments removed from section fixtures (O233);
  - apps/accounting/tcs.py: moved onto the shared dated-rate rules (O233).

## Defects seen and not fixed

1. apps/purchasing/models.py, `PurchaseOrder.confirm()` (near the class's confirm): `@serialised("status")` is applied twice. Harmless (a nested lock and re-read). rule/minor.
2. O95 against O178: the decided key strips "/", "-" and ".", so a vendor's "X/1/12" and "X/11/2" in one year are one invoice, and the second is refused (naming the first). O178 rejected exactly this folding for supplier credit notes. Decision as briefed; the accountant should confirm, and also whether `accounting.gst.document_number_key` (GSTR-2B) folds the same way. statutory/question.
3. An approval given before purchasing 0075 has no kept figures. Each change to such an order is held to the order as it stood just before that change, so a second change can be refused even though the approval covered it. Errs on the side of refusing. rule/minor.
4. `PurchaseOrderLine.save` (the `if self.pk:` block): a new line created in code with `blanket_line` on an existing order is not checked against the agreement's remaining quantity or status. The API cannot reach it (`blanket_line` is not in the serializer). rule/minor.
5. A debit note made by `create_return()` is dated the day it is made, not the return's date: a January return gave a note dated 2026-10-16 in a test. books/question.
6. O233: a lower-deduction certificate's rate (`PartyTaxProfile.tds_rate_percent`) is still one undated figure on the profile. A certificate's validity period is not kept, so a lapsed certificate goes on deducting at its rate. statutory/minor.
7. O234: whether a supplier's GST invoice value, and so GSTR-2B's comparison, takes in income-tax TCS needs the accountant's confirmation. `Bill.total()` now includes it, and gstr2b `_figures` reads the document's value. statutory/question.
8. O234: TCS on a prepayment is refused, not built. The supplier collects 206C(1H)-style TCS on receipt of payment, which is no longer law since April 2025, so only 206C(1F) and similar on the bill are built. statutory/minor.

## Review fixes (review_purch.md; probes ea87dd6, cherry-picked as f9c8ef8 on 674ac8a)

Every commit names its row and its finding, and ends with `Gate: {GATE}` and the two attribution lines.

| Commit | Row, finding | What |
|---|---|---|
| 7bc0d0a | O233, F1, plus the decision "no confirmed rate is a refusal" | TDS falls due on the bill's own day, or on a later day given (its payment's). A day before the bill, or one not yet come, is refused under `correction_date()`. A bill its vendor's section taxes, with no confirmed rate in force that day, is refused at posting: "... no confirmed 194C rate stands on 10 Mar 2026: nothing would be deducted. The accountant confirms it under TDS rates". The year's arithmetic is one function (`_owed`). |
| 1dc624c | O211, F2 | A return debits only the bills that hold the returned receipt's goods, each for what it holds. Goods no bill holds come off GRNI alone, whatever `debit_bills` says. Probe ReturnsInPostingOrder test_01 billed 10 where only the defect made room for it; it now bills 6. |
| 2664a74 | O234, F8 | Return and reject post their debit notes only for a person with `purchasing.post_bill`. Anyone else's notes are drafts, carrying their TCS, for the AP side to post. PartialCorrectionsTests encoded the defect and was updated. |
| 84bd692 | O211, item B | A return, and its debit notes, are dated on the day the goods went back (`on_date` / the API's `date`), under `correction_date()`. A note is never dated before its bill. |
| 248bf1d | O167, F3 | `apps.core.approvals.asks_its_policy_when_taxes_change()` asks the policy again when a confirmed line's taxes change, for both purchase and sales order lines; the m2m write rolls back with the refusal. The sales probe is driven as the AR Manager. Touches apps/sales/models.py. |
| 0e5e7ba | O233, F4 and F9 | `deduct()` locks the rate row and reads it again; `DatedRate.save()` locks before it asks `taken()`. `confirm()` is given the figures that were read, and refuses a row that changed since, or one already confirmed. The API and both rate screens send the figures. Shared with TCS. apps/sales/tests_tcs.py now sends the figures. |
| 237cf09 | O95, F5, F6, F7, item A | One normaliser, `accounting.gst.document_number()`: NFKC, Cf characters dropped, every dash and minus to "-", whitespace trimmed and collapsed, upper case, separators kept. The vendor-invoice key, the credit-note key and the GSTR-2B hint all read it. A number longer than 64 characters as compared, or one with no letter or digit, is refused in words. Purchasing 0079 re-keys every row; collisions are printed and left unkeyed (MigrationExecutor test). Touches apps/accounting/gst.py. |
| ecc9d38 | O154, Q1 decision | A posted note's supplier credit-note number is corrected only with a reason, by a person with post_bill, and the history line keeps the old and new numbers and the reason. It is never cleared. The decision says "Controller", but in setup_roles the Controller does not hold post_bill; the AP Manager does, so the test runs as the AP Manager. |
| 18ab86c | O234, F10 | The bill screen keys TCS collected, its section and the receivable account. Browser test as the AP Manager. |
| eb4b1fa | (all) | Probe classes renamed by what they protect. |
| 6f7ff18 | O95 | The invoice-number race probe now races two writings of one invoice ("INV-77" against "inv-77"). |

Probes corrected because they encoded a reversed decision or the reviewer's own arithmetic (each is said in its commit):
- InvoiceNumber 01, 02, 03, 07, 09: the old folding.
- TdsDatedRate 08 and race test_03: the refusal now comes at posting.
- TdsDatedRate 09 and RoleProbes 08: these now expect the refusal.
- RoleProbes 01: sends the figures it read.
- Match 01: 99.45 less 5% is accepted.
- ReturnsInPostingOrder 01: bills the 6 held.
- Race test_01: "inv-77".
- SupplierNote 05: rewritten to the Q1 decision.
- **Q2 (InvoiceNumber 06) is not decided.** It is kept as a record that a bill fully debited still holds its number. For the accountant.

Runs on the review-fix tip (all on SQLite unless the line says otherwise):
- purchasing, accounting, sales, core, inventory, gst and e2e (fastsettings): 3804 OK, 147 skipped.
- PostgreSQL: the 6 review races, BlanketCallOffRaceTests, PurchasingRaceTests and SalesRaceTests: OK.
- audit_invariants: nothing reported. `makemigrations --check`: no changes.
- Migration tag (purchasing and accounting, default settings) at 6f7ff18: 11 OK.
- apps.web whole, Playwright with a fresh build, at 6f7ff18 after the machine restart: 179 OK, none skipped.
- Not run: the whole suite on PostgreSQL, and the Asia/Kolkata lane.

Review-fix tip: 6f7ff18, 12 commits on 674ac8a.
