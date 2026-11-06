# review_money, g22: adversarial review of fix_money (e73428e..d554c46)

Tip reviewed: d554c46, worktree /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/review_money.
Probes committed there, unpushed, as e9627a9 (four test files, no product code touched):
- apps/accounting/tests_review_money_probes.py
- apps/gst/tests_review_money_probes.py
- apps/sales/tests_review_money_probes.py
- apps/e2e/tests_review_money_races.py (PostgreSQL)

Every probe prints PASS or FAIL with the numbers. Logs: reports/review_money_probe_run.log (43 probes, PostgreSQL review_money with migrations, DJANGO_TIME_ZONE=Asia/Kolkata) and reports/review_money_probe_run2.log (the probes added or corrected after it, same database). Iteration used fastsettings on SQLite; the results agree on both.

Gates on the tip: `audit_invariants` prints "No invariant findings."; `makemigrations --check --dry-run` prints "No changes detected" and exits 0. `npm run build` is OK. The three new browser tests (tests_browser_statement_entries, tests_browser_lut, tests_browser_discount) pass: Ran 3, OK.

## Findings, blockers first

### Blockers

**1. O209, d554c46: a drawdown of a foreign-currency advance is counted without its exchange entry; the receivable keeps it. BLOCKS.**
- Where: apps/accounting/settlement.py:239-247 (`document_slice` takes `settled_by_entries` and reads `booked_on(entry, control_account)`), apps/sales/models.py:2588 (`Invoice.settled_by_entries` returns each `DepositApplication.journal_entry`).
- Why: `post_drawdown` books the drawdown as one entry (Dr Deposits / Cr AR at the rate the advance came in at) and the gap to the invoice as a second entry (the exchange difference). `booked_on` reads only the first, so the drawdown is counted at 44.98 where it nets 43.99.
- Scenario (probe `AnAdvanceInEuros`, all numbers read from the ledger): order EUR 100.09; 5 Jan advance 40% = EUR 40.04 at 1.12345 = 44.98 (Dr AR, Cr Deposits). 15 Feb invoice EUR 100.09 at 1.09876 = 109.97, which draws the advance on posting: Dr Deposits 44.98 / Cr AR 44.98, then exchange Dr AR 0.99 / Cr 4900 0.99, so the drawdown nets 43.99 on AR. 15 Mar receipt EUR 60.05 at 1.15012 = 69.06. Correct exchange on the receipt: invoice base left 109.97 - 43.99 = 65.98 against 69.06 paid = Dr AR 3.08. Posted: Dr AR 4.07 (it reads 64.99 left). Result: invoice due 0.00, the AR account still holds 0.99 beyond the unpaid advance, and the gain account reads 5.06 where 4.07 is true.
- Size: grows with advance times rate gap. Four runs, same shape: 100.09 EUR (40%) leaves 0.99; 777.77 (33%) 6.33; 13.37 (10%) 0.04; 1,234.56 (70%) 21.33.
- Regression: with `settled_by_entries` returning nothing (the behaviour before d554c46) the same four runs leave 0.01, 0, 0, 0.01. The commit closed a paisa on credit notes and opened this one on advances, which an export plant takes every month. Probe fails on PostgreSQL and SQLite.
- Fix direction: count the drawdown as its two entries together, or add the exchange entry's AR lines in `settled_by_entries`.

**2. O109: the settlement discount is taken on the whole invoice whatever the payment paid. BLOCKS (the screen invites it).**
- Where: apps/accounting/settlement.py:627 (`amount = document.settlement_discount()`, the full percentage of the whole total) inside `take_settlement_discount` (639); the check on the named payment (standing, applied, within the days) never compares what it paid to what earns the discount. Frontend: PaymentForm.tsx adds "Take the discount" on every applied row of every posted receipt or payment.
- Scenario (probes `SettlementDiscountOnAPartPayment`, sales and bill): INV 1,000 on 2/10 net 30; receipt of 100 on day 5, applied; Take the discount books 20.00 (Dr discount allowed 20.00 / Cr AR 20.00). The customer has paid 100 and owes 880. Expected: refused until the payments within the days cover the invoice less the discount, or 2.00 (2% of the 100). Bill side: same, 20.00 discount received.
- The 490 + 490 + 20 case settles once correctly, and a receipt applied elsewhere or outside the days is refused, so the checks that exist work; the amount is the gap.

### Medium (non-blocking, fix before the next release of the module)

**3. O238: Table 13 reports a receipt voucher as an invoice and a refund voucher as a credit note; both take the invoice and credit note series.**
- Where: apps/gst/returns.py:467-500 (`DOCUMENT_NATURES`, `_documents_issued`: `invoices` is every numbered invoice with no `credits`/`debits`), numbering at apps/sales/models.py:2898-2907 (a down payment takes `sales.invoice`; its refund takes `sales.credit_note`).
- Scenario (probe `VouchersInTheReturn`): job-work advance 3,000 + 540 GST on 5 Oct prints "Receipt Voucher" INV-2026-00001; a 1,180 refund prints "Refund Voucher" CN-2026-00001. GSTR-1 October Table 13: `invoices` 1 (INV-2026-00001) and `credit_notes` 1 (CN-2026-00001); no natures 6 or 8. The print says voucher, the return says invoice. Table 11A is right: net 2,000 + 360 for the month (PASS).
- The Rule 50/51 content itself is complete on the print (amount, rate, tax, reverse charge, receipt voucher number and date): PASS.

**4. O235: a debit note is raised on an invoice that is credited in full and on one whose IRN the portal cancelled.**
- Where: apps/sales/models.py:3084-3140 (`Invoice.debit`). The refusals there are not-posted, a note, a down payment, an opening balance. A credit note checks `quantity_creditable`; the debit note has no mirror of it.
- Scenario (probes in `DebitNoteReversePathsAndGuards`): s1 (148,090 with its GST) credited in full, then `debit("1000")` returns DN-2026-00001 for 1,180.00: a customer is billed more on a sale the books reversed. Same on an invoice whose IRN cancellation was recorded: the DN references an invoice that is off GSTR-1 and gone from the portal.

**5. O235: an e-way bill is built for a debit note.**
- Where: apps/gst/ewaybill.py:300 guards `is_credit_note()` only.
- Scenario (probe `DebitNoteAndTheEwayBill`): DN of 100,000 on s1: `_invoice_payload(note)` returns a payload with docType INV, docNo DN-2026-00001. No goods move on a supplementary invoice.

**6. O235: the debit note's line falls in its own HSN row under UQC "NA", and its e-invoice item says Unit "NA" on a goods line.**
- Where: the line has no item (apps/sales/models.py `Invoice.debit` creates `InvoiceLine` without `item`), so apps/gst/returns.py:132-138 `_uqc` returns "NA".
- Scenario (probe `DebitNoteInTheReturn`): s1 plus a 1,000 DN: HSN rows are 63053300/KGS/18% 122,500.00 (qty 1,000), 63053300/NA/18% 976.10 (qty 0), 996511/NA/18% 3,023.90. Quantity and totals are right; the goods HSN is split under a unit that Table 12 reserves for services. The DBN item prints "Unit": "NA", "Qty": 1.0, IsServc "N". Whether the portal rejects either is unproven (no schema here).

### Low

**7. O230: a return line dated before the void it is matched to leaves the statement 1,000 out with nothing named.**
- Where: apps/accounting/models.py:1374-1396 (`_unseen` treats a payment as voided only when the void's entry date is on or before the statement's end) and match() at 1596+ which accepts the return line whatever the void's date.
- Scenario (probe `BouncedChequeAcrossStatements`): cheque 1,000 deposited 5 Jan, matched; bank returns it 28 Jan (closing 0); the void is booked 2 Feb. Return line matched. January: ledger 1,000, bank 0, difference 1,000, no item listed (0 unpresented, 0 unreturned). Workaround: date the void on the bank's day. Everything else in O230 holds (below).

**8. O24: only `Payment.post` checks the account's currency; a manual journal entry in base money posts to an account kept in euros.** Probe `AEEFCAccount` FAIL. apps/accounting/models.py:1122-1141 is the one check. The row said payments; listed so the next reader knows the other doors (manual entry, challans, claims) are open. Observation more than defect.

### Unproven (no failing probe)

- U1. DBN item Unit "NA" on a goods line, and HSN rows under NA, may be rejected by the IRP and the GSTR-1 portal (finding 6); no schema to test against.
- U2. DN- is the prefix of both the sales debit note series (apps/sales/models.py:2904) and purchasing's (apps/purchasing/models.py:4043): two different documents can read DN-2026-00001 (journal references, search). Seen by reading, not driven.
- U3. `GET requisitions/{id}/suggested_vendors/` passes for any holder of view_purchaserequisition, so Employee Self Service and Production Planner reach it (role matrix: 404 on a missing id, not 403). It returns vendor names per line, no prices. Not driven against another employee's requisition.
- U4. O77 weighs a booked invoice at its booked rate and an unbilled order at today's rate (`committed_in_base`). A design choice; the probe confirms an invoice's weight does not move when the rate does, and that the exposure endpoint reads the same figure as the check.
- U5. Allocations made before 93ec369 keep `document_base` null, so a document with one of them falls back to the old slice for every later allocation: a document in flight at migration keeps the one-paisa defect. No backfill.
- U6. A debit note copies the original's payment terms, so it can earn its own settlement discount.
- Known and not re-found: TCS on a debit note raised on a scrap invoice; O244's two unprobed paths; O209's purchasing side (the bill's debit notes and prepayment drawdowns still count at their slice).

## What held (probe, scenario, result)

1. O209, allocations. All PASS on PostgreSQL and SQLite.
   - Invoice EUR 100.09 at 1.12345 (112.45), receipts 30 + 30 + 40.09 at three rates (1.09876, 1.15012): every order of application (6) times a delete-and-apply-again of each one or of all three in every order (9) = 54 runs, sales side. Control account clears every time and the exchange account holds exactly the invoice base less the receipts' bases.
   - Purchasing mirror, 54 runs: same.
   - One receipt of 100.09 over invoices 0.07 + 1.57 + 98.45, each order, one deleted and made again: clears.
   - Receipt, then a credit note on one of three lines, then the receipt for the rest, then the first receipt's allocation deleted and applied again: AR 0.
   - A 2/10 discount taken last over 120 random invoice amounts with two receipts at two rates: AR 0 every time.
   Finding 1 is the exception, and it is a drawdown, not an allocation.
2. O230.
   - One statement: deposit matched, bounced, voided, return matched, closed: foots at every step (author's test; mine adds unmatch).
   - Unmatch each line in turn (deposit line off: difference -1,000 with one unresolved line, the deposit line back: 0; unmatching the return while the deposit is matched is refused in words): PASS.
   - Void dated 25 Jan, before the bank showed the deposit on 3 Feb: January, February and January again all foot: PASS. Voided 27 Feb with the return on 2 Mar, three statements: PASS.
   - The exception is finding 7.
3. O243. All PASS except the by-design item.
   - Rate that stood on the day: 1% on 5 Sep, rate 2% from 1 Oct: the advance keeps 270 at 1%, the 5 Oct invoice for the order collects 1,584 on 79,200 at 2%, payable 1,854; the whole goods invoice credited gives 1,854 back on one note (1,584 + 270), payable 0.
   - Part refund: 27,000 advance (270), 9,000 refunded (90 back, payable 180), then the invoice: it counts the 18,000 left and collects 882; payable 1,062.
   - Two invoices drawing one advance: PASS (author's test and the PostgreSQL race below).
   - 30 random sequences of invoice, credit and refund on advances of 10 to 55 percent: after each step the TCS payable is never negative and the customer's outstanding balance equals the receivable account; wound back (every invoice credited, the advance refunded), the TCS payable is 0 every time.
   - Challan: the September challan pays 1,062 and the account reads 0. A give-back in October after the September challan leaves -1,062; October's challan is refused in words ("set against a later month's") and the debit is carried. By design, as before this round; recorded so nobody reads it as a leak.
4. O235 beyond findings 4 to 6. All PASS.
   - Rule 53 print: heading "Debit Note", names the invoice and its date, the HSN and the tax debited, no "Tax Invoice" heading.
   - 1,000 net on s1 to Karnataka: 976.10 + 23.90 with IGST 175.70 + 4.30, 1,180.00; e-invoice DBN: Version, TranDtls, DocDtls (Typ DBN), SellerDtls, BuyerDtls, ItemList, ValDtls present, every item has the required keys, values foot (1,180.00), RefDtls names the invoice.
   - GSTR-1: CDNR ntty D with value 1,180.00 and the invoice as original; JSON `ntty` "D"; in October when raised in October and absent from September; GSTR-3B October 3.1(a) IGST 180.00; a debit note on a small unregistered sale adds 500 to B2CS (15,000.00 to 15,500.00), not CDNUR.
   - Export under LUT: zero tax, CDNUR EXPWOP with ntty D, e-invoice EXPWOP, rate of the note's own day.
   - Reverse: a credit note on the debit note is ntty C naming the debit note, 1,180.00; a DBN whose IRN is cancelled is reversed in the books (AR 18,880 to 17,700), off CDNR, Table 13 DN cancelled 1.
5. O238. Table 11A nets a same-month refund (2,000 + 360). A receipt voucher is refused by the e-invoice builder. Finding 3 is the exception.
6. O109 beyond finding 2. A receipt outside the days and one applied to another invoice are refused; the discount is taken once. Reverse: a cheque that earned a discount and bounces (void) puts the invoice back to 1,000 and the discount to 0.00 standing. The switch (`discount_reduces_gst`) refuses in words and posts nothing (author's test); with it off the entry is Dr discount / Cr receivable with no tax line.
7. O77. Booked exposure does not move with the rate; the exposure endpoint equals the check's figure.
8. O24. A base-currency payment through a euro account is refused naming both currencies; EUR through EUR posts at 110.00 (author's test).
9. O245. After the section's name and code are edited, the TCS return's rows (section, collection code, base, rate, amount) are unchanged.
10. Who may (API, every group after setup_roles; a missing id answers 403 if refused, 404 if let through):

| endpoint | allowed |
|---|---|
| invoices debit_note | AR Manager (same as credit_note) |
| invoices take_settlement_discount | AR Manager |
| bills take_settlement_discount | AP Manager |
| bills match_report | AP Manager, Purchasing Clerk, Controller, GST Officer |
| bank-statements unmatched_entries, reconciliation | Controller, Bookkeeper |
| LUT list / create | Controller, GST Officer |
| seeded-defaults list / review | Controller |
| tcs-collections | AR Manager, Controller |
| Sales Rep | none of them |

   Nothing a Sales Rep or an HR/stores role reaches. The Controller cannot post a debit note or take a discount (no sales.post_invoice), as for a credit note. See U3.
11. Races on PostgreSQL (`race()`, review_money): a discount taken as the last 20 of an invoice is allocated: one done, one refused, due 0.00; two goods invoices posting at once against one advance: both done in turn, counted 27,000.00 (not twice), payable 1,062.00; a statement line matched as its payment is voided: both done, difference 0.00, the void listed as unreturned. All hold.
12. Browser: the three new browser tests pass (Ran 3, OK). The statement screen's "Match an entry" and Unmatch cover `booked_entry`; "Take the discount" appears with the permission `sales.add_invoicepayment` or `purchasing.add_billpayment`, matching the API. The button shows on every applied row whatever the terms (finding 2).

## Notes on method

- The commit trailer names the model the brief gave (Claude Opus 5.5); the session actually ran on another model. Stated here so the record is straight.
- Probe artifacts corrected before the commit: a "payment with no currency through a euro account" probe was wrong (the payment takes the party's currency); the "TCS account whole again after October" probe encoded a refusal that is by design and now asserts the design.
- Not done in the budget: manual driving of the new screens as each role in the browser beyond the three tests; the full suite on PostgreSQL; the four migration-tagged tests.
