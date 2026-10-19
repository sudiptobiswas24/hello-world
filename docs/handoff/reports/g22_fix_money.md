# fix_money, g22

Worktree: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/fix_money (base e73428e). Nothing pushed.
Calculations: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/calc_fix_money/g22/ (o209.py, o243.py; logs run_*.log, audit_c.log, mkcheck_c.log, mig_c.log).

## Commits (ids closed)

8 commits on e73428e, tip 735b84c:
- 93ec369 O209
- e0fdbc9 O230 (model and API half; screen half not done)
- 54a23ee O77
- 6eee419 O109 (settlement discounts; the row's purchasing items not touched)
- ebb4c4b O243
- 248c477 O245
- 49a127a O24
- 735b84c O81
The commits were split from one tested tree by hunk; ebb4c4b (O243 without O245's fields) was not run as a tree of its own. apps/purchasing is touched by 93ec369 (BillPayment's two fields, migration 0075) and 6eee419 (Bill.take_settlement_discount and BillViewSet call the shared function); each commit says so.

## Lanes (final tree; apps.sales run again after the deposit_total change: OK)
- fastsettings, `apps.accounting apps.sales apps.purchasing apps.core apps.gst apps.web`, --parallel 2: Ran 3227, OK (skipped=116; browser tests skip, no build was run).
- audit_invariants: No invariant findings. makemigrations --check: No changes detected.
- `--tag migration` for sales, purchasing, core, accounting (default settings): still running at hand-back; log /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/calc_fix_money/g22/mig_c.log
- Not run: PostgreSQL, Asia/Kolkata, browser lane (no frontend change was made).

## Each fix and the test that failed without it

- **O209** (93ec369). Proven, not refuted: 100.09 EUR booked at 1.12345 (112.45), receipts at 1.09876 applied 30 + 30 + 40.09; the middle 30 deleted and applied again cleared 33.70 + 45.04 + 33.70 = 112.44, 0.01 left on AR. Re-pointing the middle one away and settling with a fourth receipt: same 0.01. Payment side: one receipt of 100.09 over invoices of 0.01 + 1.57 + 98.51, middle deleted and made again: 0.01. Fix: `InvoicePayment`/`BillPayment` record `document_base` and `payment_base` as applied; `RealisedOnItsOwnDay.document_slice`/`payment_slice` take what was booked for all settled so far less what the others still standing recorded. Test `apps.accounting.tests_fx_settlement.MiddleAllocationUndoneTests` (4). Without the fix: `AssertionError: Tuples differ: (Decimal('33.70'), Decimal('45.04'), Decimal('33.70')) != (Decimal('33.70'), Decimal('45.04'), Decimal('33.71'))`; re-point `(0.00, 0.00, 0.010000000000) != (0, 0, 0)`; payment side `-0.010000000000 != 0`; payable mirror `(0.00, -0.010000000000) != (0, 0)`.
  - Left open in the row: credit notes, drawdowns and write-offs on a document are still counted at their slice of its base, not at what they booked (the row's second half). Allocations made before this commit have no recorded base and fall back to the old slice.
- **O230** (e0fdbc9), model half. The deposit line stays matched; `BankStatement.unreturned()` lists a voided payment the bank showed arriving and no line by the statement's end shows returning; the reconciliation counts it (-amount) until a line is matched as its return (`match()` already matched a return line to a voided payment). A void the bank showed neither way nets to nothing and is not listed. The API's reconciliation rows include "Return of PAY-...". Tests `apps.accounting.tests_reconciliation.VoidedWhileMatchedTests` (one statement; voided 29 Jan with the return on February's statement; never presented; API as the Controller). Without the fix: `AssertionError: Tuples differ: (Decimal('0'), Decimal('0'), Decimal('-1000')) != (Decimal('0'), Decimal('-1000'), Decimal('0'))`; API `'-1000.00' != '0.00'`.
  - Not done: the statement screen (frontend BankStatementForm.tsx) still matches a line only to a payment; matching to a direct entry (`match_entry`, the API's {"entry"}) has no picker, and the "Explained by" column and Unmatch ignore `booked_entry`. That is O156's second half too. A voided payment is already offered by the payment picker (posted=true), so a return line can be matched from the screen.
- **O77** (54a23ee). `committed_in_base`/`outstanding_in_base` in sales/models.py; `credit_limit_breach` and the customer `exposure` endpoint use them. Test `apps.sales.tests_guards.CreditLimitInTheBaseCurrencyTests` (CreditLimitProbe's numbers). Without the fix: `AssertionError: None != (Decimal('1018.00'), Decimal('1000'))`.
- **O109** (6eee419), settlement discounts only. One function `settlement.take_settlement_discount` for both sides; `Invoice.apply_settlement_discount(payment=)`, `Bill.take_settlement_discount(payment=)`; API actions `invoices/{id}/take_settlement_discount` (sales.add_invoicepayment) and `bills/{id}/take_settlement_discount` (purchasing.add_billpayment). Switch `PaymentTerms.discount_reduces_gst` (core migration 0035), default off; on, the step refuses and asks for a credit note. Accountant question added to docs/HANDOFF.md item 3. Tests `apps.sales.tests_audit_round2.SettlementDiscountOnTheReceiptTests` (as the AR Manager), `apps.accounting.tests_fx_settlement.BillSettlementDiscountTests` (100 EUR at 1.12345, 98 paid 10 Jan, 2.25 discount, AP 0). Without the fix: `AssertionError: 404 != 200` (and 404 != 400 on the three refusals); `TypeError: Bill.take_settlement_discount() got an unexpected keyword argument 'payment'`.
  - Not done: the row's other never-called items (`Bill.match_report`, `PurchaseRequisition.suggested_vendors`, `BlanketOrderLine.is_fully_released`) are purchasing's; no screen button for the discount step on InvoiceForm.tsx or the bill screen. The model method still accepts a call without a payment (kept for its existing callers and tests).
- **O243** (ebb4c4b). An advance (down payment invoice) on an order of TCS goods collects TCS at its posting, on its value in the share those goods are of the order; the invoice for the goods counts the advances on its order (oldest first, `TcsAdvanceCount`) and collects on the rest; g21's refusal is gone. `deposit_unapplied()` holds the value, not the TCS. Reverses: the advance refunded gives its TCS back; the invoice credited gives back its own TCS first, then releases what it counted (given back on the same note where the invoice drew that advance down, since the advance can then no longer be refunded); drawn across two invoices. Found on the way and fixed in the same commit: the drawdown's slice read the invoice's booked base without its TCS entry, so 27,000 drawn on a rupee scrap invoice of 106,992 read 26,800.13 and 199.87 of exchange. Tests `apps.sales.tests_tcs.AnAdvanceOnAScrapOrderTests` (5): advance 27,000 collects 270; invoice 106,200 counts 27,000 and collects 792 on 79,200, 1,062 in all; drawn whole, 79,992 due. Without the fix (the advance collecting nothing, as before g21): `AssertionError: Decimal('0') != Decimal('270')`; `ValueError: not enough values to unpack (expected 1, got 0)`; `Tuples differ: (Decimal('90000.00'), Decimal('0')) != (Decimal('90000.00'), Decimal('630'))`. Also fixed in the commit: `deposit_total()` counted the advance's TCS, so the other 63,000 of the order would have been refused (test_the_rest_of_the_order_can_still_be_taken_ahead).
- **O245** (248c477). `TcsCollection.collection_code` and `TcsChallan.section_code`, kept at the event (sales migration 0075 fills existing rows from today's section); the return and the challan API read them; `KEPT_FROM` now names `form_code`, so a live read is an audit finding. Test `apps.sales.tests_tcs.TheReturnAndTheChallanKeepTheirCodesTests`. Without the fix: `AssertionError: Tuples differ: (['6CF'], '206C(1)') != (['6CE'], '206C(1)')`.
- **O24** (49a127a). `Payment.post` refuses a payment through an account kept in a non-base currency (EEFC) in any other currency. An account whose currency is the base still takes foreign receipts converted by the bank. Test `apps.accounting.tests_reconciliation.AnAccountInAnotherCurrencyTests`. Without the fix: `AssertionError: ValidationError not raised`.
- **O81** (735b84c). The dunning mail renders the notice's overdue amount and the day it fell due. Test `apps.sales.tests_audit_round2.TheDunningMailAsksForWhatIsLateTests` (DunningProbe). Without the fix: `AssertionError: False is not true : ... the mail says: 'Dear Acme,\n\nInvoice INV-2026-00001 for 1,000.00 was due on 31 Mar 2026 ...'`.

## Not done (budget)
- O235 (sales debit note: posting, Rule 53 print, GSTR-1 CDNR/CDNUR, e-invoice DBN, reverse): not started.
- O238 (Rule 50 receipt voucher, Rule 51 refund voucher): not started.
- O239 (office screens for the LUT and setup_indian_books' seeded list, browser test as the Controller): not started.
- O244 (four tax-path probes): not started.
- O156 (TDS return "reverses" column; the statement screen's entry match): not started. O230's screen half is the same work.
- No race test was needed for the fixes made (no new read-then-write path; the allocation and discount paths already run under their locks).

## Defects seen and not fixed
- apps/accounting/settlement.py `document_slice` (allocations): `booked_on(document.journal_entry, ...)` leaves out an invoice's TCS entry, as the drawdown did; harmless today only because TCS is refused in a foreign currency and a rupee allocation has no exchange difference. books, latent.
- O209's second half (credit notes, drawdowns, write-offs at their slice, not their booked base) stays as the row says.

# Second round

On top of 735b84c, in the same worktree. Tip d554c46; 7 more commits (15 in all). The machine restarted mid-round; nothing committed was redone, and every check after the restart was re-run in the foreground (or as a long job whose result was read).

## Step 1: the first round's migration lane
`--tag migration` for sales, purchasing, core, accounting (default settings): Ran 15 tests in 3894.710s, OK.

## Commits
- 510eba9 **O235** sales debit note: `Invoice.debit(net, reason, on_date)`, `Invoice.debits`, `InvoiceLine.debits_line` (HSN as frozen), DN- series, print "Debit Note" naming its invoice (Rule 53, from issued_as), GSTR-1 CDNR/CDNUR ntty "D" with a `sign` that adds, Table 13 "Debit Note" (4), e-invoice DBN with RefDtls, reverse = a credit note on it, API `invoices/{id}/debit_note/` (sales.post_invoice, AR Manager). Sales migration 0076. Tests `apps.gst.tests_einvoice.DebitNoteTests` (1,000 on s1 to Karnataka: 976.10 + 23.90, IGST 175.70 + 4.30, 1,180.00). Without it: no `debit` method (AttributeError).
- 8568e07 **O238** a down payment prints "Receipt Voucher" (Rule 50), a credit note on one "Refund Voucher" naming it (Rule 51). Test `apps.gst.tests_advances.VouchersTests`. Without it: `AssertionError: Tuples differ: ('Invoice', Decimal('3000.00'), [...]) != ('Receipt Voucher', ...)`.
- 369b333 **O230 screen half, O156**: "Match an entry" on the statement (GET `bank-statements/{id}/unmatched_entries/`), "Booked, JE-n" and Unmatch for it, the payment picker's hint for a returned cheque; the TDS return's "Reverses" column. Browser test as the Controller `apps.web.tests_browser_statement_entries`.
- 2c71a40 **O239** Settings > Letters of undertaking (list, form; registration defaults to the one in force) and Settings > Seeded defaults (list, "Mark reviewed"); `apps/accounting/lut_api.py`; Controller (and GST Officer for the LUT) in setup_roles; registry permissions as the server's. Browser test as the Controller `apps.web.tests_browser_lut`.
- d70113b **O109 rest**: "Take the discount" on each applied row of a receipt or payment (browser test as the AR Manager, `apps.web.tests_browser_discount`: 20.00 on 980 within 2/10); `Bill.match_report` wired (GET `bills/{id}/match_report/`, quantities at four places on both databases; test as the AP Manager); `PurchaseRequisition.suggested_vendors` wired (GET `requisitions/{id}/suggested_vendors/`; test as the Purchasing Clerk); `BlanketOrderLine.is_fully_released` removed (nothing called it). Touches apps/purchasing for the row's own items.
- 02c4004 **O244** probes, both hold, each test says why (`TaxPathsTheReviewDidNotDriveTests`): a payment between an IRN cancellation's request and the answer is refused at the answer ("has 17700.00 settled against it"), released, the answer is kept; an IRN cancelled in a closed period is refused in words, nothing half kept, recorded once the month is reopened. **Not probed:** a TDS and a TCS challan for one month on one bank (reasoned only: separate sections and locks, each matched by its own entry); an attachment added while a backup runs. Both stay open on the row.
- d554c46 **O209 rest**: `Invoice.settled_by_entries` (a note wholly absorbed, each drawdown) counted at what it booked. Scenario: calc_fix_money/g22/o209b.py (a two-line note can never differ; a note on two of three lines can: 10.01 + 20.78 + 25.37 EUR at 1.12345, note booked 34.60, slice 34.59). Test `NoteCountedAtWhatItBookedTests`; without it `Tuples differ: (Decimal('30.79'), Decimal('0.00'), Decimal('-0.0100000000000')) != (... Decimal('0'), Decimal('0'))`. **Left:** purchasing has no `settled_by_entries`, so a bill's debit notes and prepayment drawdowns stay at their slice (fix_purch's side; the hook is there). A note partly taken as refund also stays at its slice.

## Lanes on d554c46's tree (before the last five commits were split, same content)
- fastsettings `apps.accounting apps.core apps.gst apps.purchasing apps.sales`, --parallel 4: Ran 3061, OK.
- apps.web in four parts (one call could not hold it): the non-browser modules Ran 60 OK (skipped=1); browser half B Ran 79 OK; browser quarter C1 Ran 24 OK; `tests_browser_sweep` (every role opens every screen, three tests) run alone: Ran 3 tests in 975.002s, OK; the rest of quarter C2 again: Ran 12, OK.
- `npm run build`: OK; tsc: no errors.
- audit_invariants: No invariant findings. makemigrations --check: No changes detected.
- Migration tag for sales (0076, this round): Ran 6 tests in 1947.523s, OK.
- Per commit, each checked out alone with its touched apps (and apps.web's non-browser modules where touched): all 15 OK:
  - 93ec369 O209: an allocation's base is what was booked less w | apps.accounting apps.purchasing apps.sales | Ran 2447 tests in 473.051s OK
  - e0fdbc9 O230: a voided payment's reversal is a reconciling i | apps.accounting | Ran 302 tests in 43.509s OK
  - 54a23ee O77: the credit limit weighs exposure in the base cu | apps.sales | Ran 976 tests in 336.515s OK
  - 6eee419 O109: the settlement discount is taken on the paymen | apps.accounting apps.core apps.purchasing apps.sales | Ran 2845 tests in 509.812s OK
  - ebb4c4b O243: TCS is collected on an advance for scrap and c | apps.sales | Ran 985 tests in 247.271s OK
  - 248c477 O245: the TCS return's collection code and a challan | apps.sales | Ran 986 tests in 320.648s OK
  - 49a127a O24: a payment through an account kept in another cu | apps.accounting | Ran 304 tests in 49.673s OK
  - 735b84c O81: the dunning mail asks for what the notice recor | apps.sales | Ran 987 tests in 315.982s OK
  - 510eba9 O235: the sales debit note, CGST section 34(3) | apps.gst apps.sales | Ran 1189 tests in 298.785s OK
  - 8568e07 O238: an advance prints as a Rule 50 receipt voucher | apps.gst apps.sales | Ran 1190 tests in 306.802s OK
  - 369b333 O230, O156: the statement screen matches a line to a | apps.accounting  apps.web-nonbrowser | Ran 366 tests in 85.667s OK (skipped=2)
  - 2c71a40 O239: office screens for the Letter of Undertaking a | apps.accounting apps.core  apps.web-nonbrowser | Ran 754 tests in 161.244s OK (skipped=2)
  - d70113b O109: the settlement discount on the payment screens | apps.purchasing  apps.web-nonbrowser | Ran 1238 tests in 129.377s OK (skipped=2)
  - 02c4004 O244: the tax paths the review did not drive, probed | apps.gst | Ran 205 tests in 25.790s OK
  - d554c46 O209: a credit note and a drawdown count at what the | apps.accounting apps.sales | Ran 1292 tests in 227.342s OK
  ebb4c4b, never run alone in the first round, passes alone (985 OK).
- Not run: PostgreSQL, Asia/Kolkata.

## Defects seen and not fixed (second round)
- apps/purchasing: no `settled_by_entries` on Bill (O209's mirror), above.
- O244's two unprobed paths, above.
- A debit note's lines carry no item, so TCS is not collected on a debit note raised on a scrap invoice (sales/tcs.py `_tcs_lines` reads `line.item`): a price rise billed on scrap escapes TCS. statutory. Not probed.

# Review fixes

On d554c46 in the same worktree, the review's probes cherry-picked first (648b613). Tip 7ec4157; 13 commits (the probe commit, 12 of mine). g22tree not touched.

## Commits
- ac34106 **O209, review F1** (blocker): a drawdown counts with its exchange entry (`settlement.booked_on_together`); notes and drawdowns are one function for both sides (`settlement.entries_that_settled`), `Bill.settled_by_entries` is the one purchasing call. The review's `AnAdvanceInEuros` (four amounts: AR kept 0.99, 6.33, 0.04, 21.33) passes; a bill's debit note on two of three lines: without the call `Tuples differ: (Decimal('30.79'), Decimal('0.00'), Decimal('0.0100000000000'))`.
- 90b1729 **O109, review F2** (blocker), as decided: `settlement.discount_earned_by`, P within the term earns P*d/(1-d), capped at what is due after it; none after the term; both sides; allocation rows carry `discount_earned` and the button shows only where it is above nothing. 100 on 1,000 at 2/10 earns 2.04 (897.96 left), 980 earns 20.00 (calc_fix_money/g22/f2.py). The review's probe asserted "at most 2.00"; it now asserts the decided 2.04. Without the fix: `Tuples differ: (Decimal('20.00'), Decimal('880.00')) != (Decimal('2.04'), Decimal('897.96'))`. Limit: a discount is still taken once per document, so a second payment within the days earns none of its own.
- eece2cf **O243**, as decided: no confirmed rate was already a refusal (`TcsSection.rate_on`); it now names the section and the screen ("under Settings > TCS rates"), through one shared function `accounting.tcs.confirmed_rate_on` that TDS may call. Tests for an invoice and an advance.
- b310c57 **O235/O243**: a debit note on scrap collects TCS (1,000 + 180 GST: 12), its reverse gives it back (sales/tcs.py `_goods_of`).
- f3b979f **O238, O235, review F3 and U2**: receipt vouchers RV- (Table 13 nature 6), refund vouchers RF- (8), sales debit notes SDN- ("Sales Debit Notes", nature 4), apart from purchasing's DN-. Two older gst tests that encoded the down payment in the invoice series now assert the decided series.
- 94d5c3c **O235, review F4, F5, F6**: a debit note is refused on an invoice credited in full or whose IRN was cancelled; no e-way bill for a debit note; a debit line reads the invoice line's unit at no quantity (goods HSN row under KGS, no NA).
- 3c70baf **O230, review F7**: a return line dated before its late void is listed as "Returned by the bank, not yet voided" (+1,000) until the void; January foots and closes, February too.
- 25e7488 **O24, review F8**: an entry keyed by hand (journal entry API post) or a statement line posted to an account kept in another currency is refused in words. Documents still post through the model; the review's probe there only prints.
- 147c967 **review U3** (true): `suggested_vendors` asked view_purchaserequisition, which Employee Self Service holds; it now asks purchasing.add_purchaseorder. Without it: `AssertionError: 200 != 403`.
- e239241 **O244**, the backup: a scan uploaded mid-backup was listed in the manifest and not archived, and restore refused the night's backup (`sha256sum: ./attached-during.pdf: No such file or directory`). Now pg_dump first, then the archive, and the manifest is read from the archive (GNU tar `--to-command`). Test `apps.core.tests_backup ...test_an_attachment_added_while_the_backup_runs_leaves_it_restorable` (PostgreSQL client tools; runs here).
- b34f1f7 **O244**, TDS and TCS challans for one month on one bank: holds (each its own entry and lock; each line matched to its own; posting refused; the statement foots at -1,762). Test `ATdsAndATcsChallanForOneMonthOnOneBankTests`.
- 7ec4157 the probes kept and renamed by what they protect: accounting `tests_settlement_and_reconciliation_facts`, gst `tests_debit_note_and_voucher_facts`, sales `tests_tcs_credit_and_access_facts`, e2e `tests_races_settlement`.

## Lanes on 7ec4157
- fastsettings `apps.accounting apps.core apps.gst apps.purchasing apps.sales` --parallel 4: Ran 3109, OK. Every probe in it passes.
- apps.web whole: non-browser and browser modules on a fresh `npm run build`: Ran 177 OK, then the browser modules again on the rebuilt bundle Ran 115 OK; the sweep alone (on the rebuilt bundle): Ran 3 tests in 929.587s, OK.
- audit_invariants: No invariant findings. makemigrations --check: No changes detected. No migration added this round, so no migration lane.
- PostgreSQL races (`apps.e2e.tests_races_settlement apps.e2e.tests_races`, database fix_money): Ran 99 tests in 999.676s, OK.
- Not run per commit this round: with the probes cherry-picked first, each commit before the one fixing a probe carries that failing probe by design.

## Seen and not fixed
- A discount is taken once per document (above).
- O244's other two paths (IRN request and answer, closed period) were probed in the second round and hold.
