# Review of the statutory fix commits ef7c0c3..81591a7 (O61 O60 O50 O55 O102 O58 O59 O104 O103 O57 O62)

Reviewed at 81591a7 in a detached worktree (SQLite, fastsettings, migration-tagged tests excluded).
My own probe tests are in `scratchpad/reports/review_stat_tests/` (`tests_rv_*.py`; the hr claim variant
of the O62 test was lost when the worktree was removed, the purchasing one is kept). Raw logs are
`review_stat_probes.log`, `review_stat_suite.log` and `review_stat_audit.log` beside this file.

Result: 17 findings, 5 serious. Nothing found against O50 (one minor), O102, O104 or the exchange-difference
arithmetic. The part of the suite I ran (apps.accounting, apps.gst, apps.core, purchasing tds and msme, hr
people, sales writeoff: 853 tests) fails only in the audit probe file I copied in. `audit_invariants` prints
"No invariant findings", and it is blind to finding 1.

Kinds: books / statutory / rule / minor. S marks a serious one.

## Findings

### 1. S, statutory. O61 is incomplete: a job-worker receipt of zero value skips the period check
- Commit a6bfcf4. The hole is apps/manufacturing/outside.py:~192 with apps/manufacturing/orders.py:2036-2037
  (`_post_entry` returns `None, balancing` when every row rounds to nothing). The guard that should have seen it
  is `reported_without_the_period` in apps/core/management/commands/audit_invariants.py.
- ITC-04 Table 5A ("received back") reads `OutsideMovement` rows by `movement_date` through `allocation()` and
  `operation.outside_receipts()` (apps/gst/itc04.py:98-111). An `OutsideMovement` of value 0 posts no entry
  (`journal_entry` None), so `JournalEntry.post` never asks the period, and nothing else does.
- Scenario (tests_rv_o61.py, on the ITC-04 fixture): challans of 30 and 31 May stand, June is closed. Booking 100 kg
  back from the vendor on 1 June at value 1,400 is refused ("Jun is closed"). At value 0 it is accepted with
  `journal_entry_id = None` and the half-year's ITC-04 "received" goes from 0 rows to 1. Voiding that receipt after
  June closed also succeeds and takes the row away again (1 to 0).
- Why the new audit stays green: it follows only models imported *by name* into apps/gst (`JobWorkLine`,
  `JobWorkLoss`; `allocation` is a function and is skipped). `OutsideMovement` is reached through a relationship.
  This is O61's own hole one hop further on.
- Answer to the brief's item 3: this is the one other thing I found that reports without an entry. Looked at and
  held: TDS return rows (deduction and challan both post entries), payroll statutory files (pay runs post), the MSME
  report (bills post), e-way bills (made for documents already posted).

### 2. S, statutory. O57 still cannot record the supplier's credit-note number once the debit note exists
- Commit c807687; apps/purchasing/models.py `create_debit_note`, `_check_supplier_note`; views.py `debit_note`.
- `create_debit_note` posts at once and a posted Bill is immutable ("This bill is posted and immutable", confirmed
  on `.save()` and on PATCH). The number and date can be given only in the call that makes the note. The field's help
  text says "or while it is a draft", but no draft debit note can be made through the action. The usual order, goods
  go back and the supplier's credit note arrives a week later, has no way to record it.
- Scenario: the original probe (debit note first, no number) fails exactly as before: INV-27-12 matched, DN-2026-00001
  "not in 2B", CN-9 "not booked". With the number given at creation it pairs (tests_rv_o57.py: matched
  `['INV-27-12','CN-9']`, nothing left; "cn 9" without a date also pairs, through `normalise`).
- Probe or fix: the probe uses the old path, so its method is stale; the gap it shows is real. It needs a way to
  attach the number to a posted note (a dedicated action that touches no money), or a manual pairing in reconcile.

### 3. S, statutory. O103: a routine inspection moves the Act's date later
- Commit 47e9a3f; apps/purchasing/msme.py `accepted_on`, the `cleared` branch.
- MSMED Act s.2(b) Explanation: acceptance is the day of actual delivery. Only a written objection made within 15 days
  moves it, to the day the supplier removes the objection; with none, deemed acceptance is the day of delivery. The code
  takes *any* accepted `ReceiptInspection` inside 15 days as the acceptance day. An inspection carries no objection
  (its fields are quantity, accepted, inspected_on).
- Scenario (tests_rv_o103.py): goods received 10 Jun, routine QC pass 20 Jun, net-60 bill 25 Jun. Code: `pay_by()` =
  4 Aug (20 Jun + 45). Act: 10 Jun + 45 = 25 Jul. Ten days late on the Act's reading, while the payment run, aging
  and MSME report all call it on time. That is s.16 interest (three times the bank rate, compounded monthly) and a
  s.43B(h) add-back the report does not flag.
- Re-derived the rest and agree: unagreed = acceptance + 15 (the appointed day is the day after the 15th, payment is
  "before" it); agreed = min(agreed date, acceptance + 45); 1 Jun net 60 = 16 Jul; net 30 = 1 Jul; debit notes and
  prepayments are not capped.

### 4. statutory (medium). O103: one bill for several deliveries runs from the last one
- apps/purchasing/msme.py `accepted_on` ("the latest of its order lines' receipts on or before its date").
- Scenario (tests_rv_o103.py): 6 received 1 Jun, 4 received 20 Jun, one bill of 10 on 25 Jun, net 60. `pay_by()` =
  4 Aug. The first six are due 16 Jul under the Act. Billed lot by lot the dates are right (16 Jul and 4 Aug).

### 5. S, statutory. O60: a registered buyer with one address for bill-to and ship-to is placed at its GSTIN's state
- Commit 6200fcb; apps/accounting/gst.py `place_of_supply`:
  `if ship_to is None or (bill_to is not None and ship_to.pk == bill_to.pk): return principal`.
- When both are the same address the function never reads that address's state. For a registered buyer `principal`
  is the GSTIN's state. A Maharashtra-GSTIN buyer whose only address (billing and shipping) is in Karnataka is
  taxed CGST 90 + SGST 90, place 27, for goods that end in Karnataka (tests_rv_o60.py, S2). The commit's own rule (a)
  says "any address of its own, registered or not: the ship-to's state". The unregistered case works only because
  there `principal` comes from the bill-to address.
- The invoice, GSTR-1 and e-invoice agree with each other here (27); the e-way bill goes to state 29 with the invoice
  at 27. The tax head is wrong in law.

### 6. S, rule (availability). O60: one bad ship-to address makes the invoice list return 400 for everyone
- apps/accounting/gst.py `place_of_supply` raises `ValidationError` when the buyer's own ship-to has no recognisable
  state. It is reached from `total()` through `place_for`, not only from `post()`.
- Scenario (tests_rv_o60.py, S8): a draft invoice whose ship-to is the buyer's own address with a blank state.
  `GET /api/sales/invoices/` returns 400 with the address message (every invoice, not just that one), and
  `GET /api/sales/invoices/<id>/` does too, so the draft cannot be opened to fix it. Quotation and order share
  `PlacedWhereTheGoodsGo` and the same path (not run). The refusal belongs in `post()` and on the address.

### 7. books, rule. O55: the reverse path is still open, and the write-off is not tied to what settled the invoice
- Commit 1224540; apps/sales/models.py `recover_write_off` (~2025: `entry_date=to_date(on_date) or
  timezone.localdate()`), exempted in audit_invariants.py `DATED_ELSEWHERE` ("sales.Invoice.recover_write_off").
- (a) Invoice 10 Sep, written off 20 Sep, recovered on 12 Sep: accepted (tests_rv_o55.py). The fix guards the
  forward step only (CLAUDE.md mistake 2).
- (b) Invoice of 1,000 on 10 Sep, 600 received and applied 25 Sep, write-off of the remaining 400 dated 15 Sep:
  accepted; receivables on 20 Sep read 600 while 1,000 was owed. The rule is "not before the invoice" only. The
  amount written off is what is due *now*, so the earliest honest day is the last allocation or credit.

### 8. rule (process). O55: seven steps of the same shape were found and only exempted
- audit_invariants.py `DATED_ELSEWHERE`, comment "Seen when the check was widened to steps that post afresh (O55), not
  yet fixed": `Invoice.apply_settlement_discount`, `Invoice.apply_deposit`, `Invoice.credit_old_supply`,
  `Bill.apply_prepayment`, `Bill.take_settlement_discount`, `Bill.debit_old_supply`, `ExpenseClaim.pay`.
  docs/RISKS.md has no row for them (O109 names two of the discount methods for another reason). CLAUDE.md: a defect
  seen and not fixed goes into RISKS.md. I did not confirm each can be backdated; the widened check says so.

### 9. minor, books. O62: a claim paid, unpaid, paid, unpaid again drops its first pair from the bank movements
- apps/hr/expenses.py `claims_paid` keeps the claim's current `journal_entry`, its `voided_entry` and that entry's
  `reverses`; `unpay()` overwrites `voided_entry`. After a second unpay the first payment and its reversal leave
  `bank_movements`, so a bank line for the first return cannot be matched and would be posted, booking it twice.
  By reading. The single pay, unpay, pay cycle works (run: the return still matches V1).

### 10. minor, books. O62: `post_to` still double-books a line that a challan or claim already booked
- apps/accounting/models.py `post_to`. Nothing stops or warns when a line equals an unpresented entry. The probe
  `ChallanOnTheStatementProbe` still fails with 700.00 != 0 (TDS payable 700, bank -1,400) because it takes `post_to`
  as "the only way". Its premise is out of date (`match_entry` exists); the fix leaves the old road open. A refusal
  or warning when an unpresented entry of the same amount and date exists would close it.

### 11. minor, statutory. O58: reversals are listed as negative rows
- apps/purchasing/tds.py `tds_return`. A deduction of April-June reversed in July appears as base -35,000 / tax -700
  in July-September. A deduction statement has no negative rows; the April-June return gets a correction statement.
  It is a JSON report today, so nothing is rejected, but a future FVU export built from it would be. The figures tie
  to the ledger each quarter. Re-derived: deducted 10 Jun and reversed 5 Jul gives Q1 +700, Q2 -700; reversed in the
  same quarter nets out.

### 12. minor, rule. O57: the unique constraint is exact, and permanent
- `one_debit_note_per_supplier_credit_note` on (vendor, supplier_note_number), apps/purchasing/models.py.
- "CN-9" then "cn-9" are both accepted (run): two debit notes for what GSTR-2B sees as one credit note. Reconcile
  normalises, the constraint does not. Conversely a supplier whose numbering restarts each financial year (legal) is
  refused "CN/1" in the second year. Two notes with no number are accepted (by design).

### 13. minor, statutory. O59: delivery challans are not in Table 13
- apps/gst/returns.py `_documents_issued` lists natures 1, 5 and 9. The e-way bill already treats a `Delivery` as a
  challan (`docType: "CHL"`, apps/gst/ewaybill.py:500), so its number is one a series gave out (natures 10-12). Reading
  only. Also `total` counts documents found, so a gap in a series is invisible.
- Held: invoices never cancel (no `Invoice.void`, so `cancelled` 0 is right), a down payment is counted among
  invoices, credit notes keep their own CN series, a voided challan is counted as cancelled and in `total`, and the
  JSON layout (`doc_num`, `doc_typ`, `docs[].num/from/to/totnum/cancel/net_issue`) matches the offline tool. I read
  this one; I did not run a month-spanning series.

### 14. minor, rule. O50: an edit silently moves the allocation's `date` to the edit's day
- apps/accounting/settlement.py `day_applied`: `given = self.date if self.date != stored else None`. A PATCH of
  `amount` alone on an allocation of 15 Feb makes its `date` today. It matches "re-stated on the day of the edit", and
  nothing reads `date` but the difference, but a field changes though it was not sent.

### 15. minor, rule. O60: smaller edges
- A ship-to of no party whose state cannot be read falls back to the buyer's state without a word (S4b). A goods
  and service invoice places every line by the goods rule (`moves_goods` is per document). `moves_goods` runs a query
  per line whenever a total is asked.

### 16. minor, process. The eleven commits do not take their rows off docs/RISKS.md
- `git diff --stat ef7c0c3 81591a7 -- docs` is empty; O50, O55, O57-O62 and O102-O104 are all still listed at
  81591a7. CLAUDE.md: a row comes off in the commit that fixes it. If the parent does it later, ignore; findings 1, 2,
  3, 5, 6 and 7 mean several should stay on anyway.

### 17. minor, process. "Gate: {GATE}" is still a placeholder in all eleven commit messages.

## What I tried and could not break
- O50, sales and purchasing (tests_rv_o50.py): 1,000 EUR invoice on 1 Jan at 1.20, receipt on 15 Feb at 1.10 (loss
  100), February closed. Allocating in an open month posts the 100 on today. Edit 1,000 to 600 to 0.01: old reversed
  and new posted on the edit's day, AR 40 then 100 (= 1,200 - 1,100), P&L nets to nil. Delete: reversal dated today, AR
  100. Voiding the payment with an allocation across the closed month: release on today, AR 1,200, bank 0. Refused:
  an allocation date before the payment (1 Feb, 10 Feb), a day to come, a date in the closed month; an edit to an
  earlier date ("last applied on"). The bill side gives the same on AP (-60 after a 400 edit, -100 after delete,
  -1,200 after the void). The data migrations (sales 0063, purchasing 0060) I read, not ran: `date =
  payment.payment_date`, the day the old entry was posted on.
- O60: a credit note raised after the customer's default address changed keeps place 29 (it copies the invoice's two
  addresses, and `record_taxes` copies the original's place; S1). After the invoice's ship-to address (of no party)
  was edited from 27 to 29, the credit note still posts at 27 with CGST/SGST, the same as its invoice (S7). Held.
- O102, re-derived from s.194C(5): 20,000 then 35,000 gives 700 on 35,000 and leaves the 20,000. Run: 20k, 35k, 40k,
  10k gives nil, 700, 800, then 600 on 30,000 (the 10,000 plus the 20,000 caught up when the year passes 1,00,000);
  total 2,100 = 2% of 1,05,000. 20k, 35k reversed, 90k: base 145,000, tax 2,900. I agree with the code.
- O104: the category is stamped at post, copied onto a debit note, and the migration backfills posted bills from the
  vendor as it stands (the only source; the migration says so). Reclassifying the vendor later leaves the bill listed.
- O62 (tests_rv_o62.py): unmatch moves reconciliation from ledger -700 / unpresented 0 / diff 0 to unpresented -700,
  1 unresolved line, diff 700; on a closed statement unmatch is refused; a -700 line cannot be matched to the +700
  reversal (sign refused); a challan voided after its line was matched and the statement closed reads ledger 0,
  unpresented +700, diff 0 (a payment does the same; not new).

## Probes still failing
Of the probes named in the brief:
- `Gstr2bNoteProbe.test_a_debit_note_meets_the_suppliers_credit_note` (O57): stale in method, fix incomplete in fact
  (finding 2). With the number given it passes.
- `ChallanOnTheStatementProbe.test_a_challan_on_the_bank_statement_can_be_explained_and_closed` (O62): the probe's
  premise is out of date; the fix leaves `post_to` open (finding 10). `match_entry` gives difference 0 and TDS payable 0.
Passing: the FxResidueProbe closed-month test, DatedAnywhereProbe (both), ClosedMonthChallanProbe (but see finding 1:
it only tests a challan), TdsReturnProbe, DocumentsIssuedProbe, PlaceOfSupplyProbe, purchasing MsmeTests and
TdsProbeTests.
Failing for reasons that belong to other rows, not these eleven: FxResidueProbe x3 (0.01 rounding residue on full
settlement and on a deposit drawn in halves), StatementProbe x2 (cheque presented or cancelled next month),
RepointProbe x3 (an allocation re-pointed to a smaller invoice or bill is accepted: `clean()` adds the old
allocation's amount to the new document's room whichever document it was on; O50 rewrote `save()` beside it and left it),
DebitNoteRefundProbe, CostCentreProbe, and the purchasing probes for GRNI, matching, order edits, agreements,
capitalising and prepayment.

## Not done
PostgreSQL, Asia/Kolkata, races, and the `@tag("migration")` tests. My suite run did not cover apps.sales or
apps.purchasing beyond the modules named at the top. Findings 9, 13, 15 and the second half of finding 7 (the other
exempt steps) are from reading, not running.
