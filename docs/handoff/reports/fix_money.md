# fix_money, gate g20

Worktree: $SP/fix_money, from 784af1c. Not pushed. Calculations: $SP/calc_fix_money/
(fx.py, commission.py, pricing.py, give_back.py).

## Commits (oldest first)

- e6ed2d9 O52: a past bank statement reconciles to the facts as they stood on its day
- a8a4fba O151: a claim paid again names its own payment, every entry kept
- 9864812 O49 O51 O70 O105 O53 O54 O74 O75 O76 O80 O201: money at settlement
- a335a14 O78 O79 O97 O98: a price restated in the terms of the line it prices
- 829d5c6 O200: sales 0067 matches a charge line by its charge's name (tip)

Closed: O49 O51 O52 O53 O54 O70 O74 O75 O76 O78 O79 O80 O97 O98 O105 O151 O200 O201.
Not closed: O199 (see "Could not do").

## Shared rules added (owner: fix_money)

- `accounting.settlement.settlement_difference`: each side's booked base, each rounded, subtracted.
- `accounting.settlement.held_base_left` + `post_drawdown(held_left=...)`: the last drawdown takes the base left.
- `accounting.settlement.standing_allocations` / `standing_paid`: the allocations that still stand (voided payments skipped). Sales and purchasing `amount_paid`, purchasing `refund_due`, msme `_standing`, the write-off date check and commission all ask it.
- `RealisedOnItsOwnDay.outstanding_for_it()`: the ceiling for an allocation, adding back its stored amount only on the same document.
- `owed_beyond(notes=(rows, pointer, *undoing))`: what a note spent undoing a settlement is not credit.
- `accounting.pricing.per_line_unit` / `quantity_in_price_unit`: a price restated per the line's unit in the line's currency; breaks in the price's unit. Sales `resolve_price(uom=)` and purchasing `applicable_prices/resolve_purchase_price(uom=)` both use it.
- `refuse_correcting_another_line` (mixins, existing) now also holds a note line to its line's revenue/expense account.
- audit_invariants: `allocations_counted_past_a_void` (READS_EVERY_ALLOCATION lists `sales.customer_statement`, which shows the bounce), `drawdowns_from_todays_setting`. Planted-instance tests: sales tests_money `TheAuditAsksItTests`.

Migrations: sales 0066 (InvoiceWriteOff.recovered_amount, nullable: blank = a recovery before it was kept, which put back the whole), sales 0067 (O200, RunPython), purchasing 0066 (VendorPrice.uom, nullable: blank = the item's unit, the old meaning). Each makemigrations output read: each names the model meant.

## Each fix, the test that failed without it

Proved by reverting the fix (stash or a face-value stand-in) and running:
- O49, O51, O70 mirror: accounting tests_fx_settlement `EachSidesBookedBaseTests`, 5/5 failed: `2.47 != 2.48`; AR `0.01 != 0`; AP `-0.01 != 0`; deposits `(-0.01, 0) != (0, -0.01)`; refunded debit note `(4.94, 0) != (0, 2.47)`.
- O70: sales tests_money `RefundedCreditNoteExchangeTests`: AR `40 != 0.00`.
- O105: sales `DepositHeldWhereItWasTakenTests` `(-300, 300) != (0, 0)`; purchasing `PrepaymentHeldWhereItWasPaidTests` `(15, -15) != (0, 0)`. (The euro prepayment with a debit note passed before too; it guards held_left with a note.)
- O53: purchasing tests_money `BouncedRefundTests`: `(0.00, 300) != (300, 300)`.
- O74, O75, O76: sales `CommissionTests`, 5 of 6 failed: bounced `(1000.00, 100.00) != (0.00, 0.00)`; up front `(700, 70) != (1000, 100)`; base `(1000, 100) != (1100, 110)` invoiced and collected; drawn day `(0, 0) != (300, 30)`. The unpaid-deposit case (700) passed before and after: it guards against counting a deposit never paid.
- O54: sales `RepointedAllocationTests` (model: ValidationError not raised; API as AR Manager: `200 != 400`), purchasing `RepointedBillAllocationTests` (ValidationError not raised).
- O52: accounting tests_reconciliation `PastStatementAsItStoodTests`: `(0, -400) != (-400, 0)` for both the presented and the cancelled cheque.
- O201: sales `NoteLineBookedWhereItsLineWasTests`: ValidationError not raised; blank account errored (no default revenue).
- O80: sales `WriteOffRecoveredInPartTests`: before the fix the recovery was refused ("Credit notes have undone 300..."); after the first half of the fix, amount_due read `0.00 != 300.00` (the note counted twice), which led to the amount_due/amount_absorbed/owed_beyond change.
- O151: hr tests_people `test_paid_unpaid_and_paid_again_keeps_every_entry_and_names_its_own`: `('paid', JE-2 Reversal of JE-1) != (PAID, None)`.
- O78, O79, O97, O98: sales and purchasing `PricedInTheLinesTermsTests`, with the rule at face value 6/7 failed (`10.00 != 9.09`, `(10, 20) != (120, 240)`, `100.00 != 100000.00`, `90000.00 != 90.00`); with the release's discount dropped, `1000.00 != 950.00`. AP Manager drives a vendor price per kg through the API (201, and 400 for a unit the item cannot be counted in).
- O200: sales tests_seed_approved_figures `AChargeLineCutBeforeTheSeedKeepsItsApprovalTests` (migration tag; MigrationExecutor in a TransactionTestCase). Run with real migrations (not fastsettings, whose MIGRATE is off): 3 tests OK, the two existing 0065 tests included. Without 0067 the figure stays the 10.00 the test plants as 0065's seed; the test asserts 20.00 after 0067.

## Gate in the worktree

At the tip's content, before the commits were split: `manage.py test apps.accounting apps.sales apps.purchasing apps.hr apps.assets apps.core --settings=fastsettings --exclude-tag migration --parallel 2`: 2882 tests, 1 error, in hr `ClaimTests.test_submitted_decided_by_the_manager_and_paid_as_one_journal`. That test took "the first reversal" from `claim.voided_entry` after the claim was paid again, which is the stale pointer O151 removes (the test encoded the defect, mistake 7): it now takes it right after the unpay. apps.hr.tests_people then ran OK. Migration-tagged sales tests_seed_approved_figures: 3 OK. `audit_invariants`: no findings. `makemigrations --check`: no changes.
The intermediate commit 9864812 was staged by script (pricing hunks and pricing test classes held back) and not run on its own; only the tip's content was run.

## Could not do

- O199 (priced_to_give_back, fractional quantity). Worked in calc_fix_money/give_back.py. The cap
  (`refuse_correcting_past_what_it_holds`, `value_left`) is unrounded while each note line is booked
  rounded, and unit prices hold 2 places, so for a fractional quantity no 2-place price meets both: e.g.
  0.5 kg left at 10.005 unrounded gives back 0.5 x 20.01 = 10.005, which books 10.01 (half a paisa over
  the unrounded cap, yet exactly what the original line booked); keeping under the cap in both views
  leaves up to 1.5 paisa short at q = 2.3. The fix is to hold the cap, and what is left, in booked
  (rounded per line) terms, which changes fix_trade's O171/O185 rule; not done here. Code unchanged.
- O200 is closed for what the history can tell: a charge line is now matched by its charge's name.
  An approval whose note names no figures, and a total/margin the note does not name, are left as 0065
  left them: core.RecordEvent records which fields a change touched, not their values, so nothing says
  what they were. The migration's docstring says so.
- O151: the claims_paid/recorded_by half was already closed by O164's bank_entries; what remained was
  the stale voided_entry on a claim paid again, fixed. No separate payment-history model was added.
- Commits are grouped by root more coarsely than one per id: the settlement commit carries eleven ids
  (exchange, money held, what stands, re-point, write-off, note account), because their edits share
  settlement.py and the two models files; the message names each id with its root and failing test.
- No PostgreSQL run, no Kolkata-timezone run (no race fixes in this brief).

## Defects seen, not fixed

1. accounting/settlement.py post_drawdown and post_settlement_fx, document side: the document's base for a
   part is round(part x document rate), and the last settlement of a document does not take what is left
   of its booked base. 100.09 EUR at 1.12345 (112.45 booked) settled 50 + 50.09 clears 56.17 + 56.27 =
   112.44: 0.01 stays on the control account. Same on the payment side for a payment split over documents.
   Kind: books (rounding).
2. purchasing/pricing.py preferred_vendor (line ~70): compares unit_price across currencies and units:
   V-1 300.00 in base vs V-2 EUR 4 at 90 (360) picks V-2 (audit_pur AgreementTests probe). The new
   per_line_unit rule is the fix. Not in this brief. Kind: books/decision.
3. purchasing Bill.amount_due / amount_absorbed: the mirror of O80's second half. A debit note's
   reversed_discount is still counted as debit against the bill too (offset = min(amount_debited, ...)),
   so once a later change frees capacity (a payment voided), the note counts twice. Sales now subtracts
   credit_spent_undoing(); purchasing does not. Kind: books, mistake 5.
4. sales commission_report, credit notes on the collected basis: a credit note's refund is counted at the
   note's own rate, and a note that only cleared an unpaid invoice (no refund) reduces nothing collected,
   as before. Not probed. Kind: books, low.
5. sales/pricing.py applicable_price_lists: a price list with no currency is "currency-agnostic" and its
   figure is used as is in any currency: 5 on a no-currency list prices a EUR line at 5 EUR and a USD line
   at 5 USD (the O78 shape for lists). Left: it is a stated design choice. Kind: decision.

## Review fixes (review_money.md, 829d5c6..6f64dae)

Commits (each trailer `Gate: {GATE}`); final tree tested whole, intermediates checked for undefined names (ruff F821/F811):
- 0c3719e O49 O51 (F1, F2): `settlement.slice_of` over `booked_on` (document's and payment's own entries): each side's base is its slice of what it booked, telescoping, so the last part of a document or of a payment takes what is left; a drawdown's document side the same. `settlement.last_of_held` is the one rule every taker of money held asks: drawdowns and the notes that give back the rest (`_credit_deposit`, `_debit_prepayment`), which book it through `InvoiceLine/BillLine.held_base` (sales 0068, purchasing 0067, nullable). `post_settlement_fx` folded into `settlement_difference(document_base=, payment_base=)`. My deposit-halves test's expectation changed by design (first half 0.01 exchange loss; calc_fix_money/review/f1f2.py).
- c24a167 O80 (F3): `settlement.spent_undoing` shared; purchasing amount_due, amount_absorbed, owed_beyond now subtract a debit note's undone discount as sales does.
- c230d18 O98 O100 (F4, F5, item 2, item 5): vendor breaks sized in the item's unit; preferred_vendor in base per the item's unit; a vendor price asked in no currency restated in base; a price list with no currency is base and converted (the integrator's decision, said in the commit). Sales breaks have no unit of their own (entries are per the item's unit), so no shared change was needed there beyond the conversion rule already shared.
- fffeb29 O201 (F6): a note line naming no line is held to the accounts its original's lines were booked to (refused otherwise).
- 27134bd O199 (F10): the cap rule changed and says what it allows: a part at its unrounded share (may book up to a paisa under); the last of a line at what is left as booked, to the paisa, priced in lines that book it exactly (2.5 at 3.33: 4.16 then 4.17 as 0.25 at 3.33 + 1 at 3.34). O171/O185 tests pass (the refusal kept its wording "has X left to give back; this note gives back Y").
- af2afab O80 (F7): bad_debt_report reads `amount_recovered()` and an `undone_by_notes` column; net equals bad debt.
- 3866f69 O75 (F8, item 4): deposit money counts on the later of its drawdown and the day it came in (receipts cover drawdowns oldest first). Item 4, reading taken: refunds come off at the invoice's rate, as collection counts (O76); a note that only cleared what was unpaid takes nothing: both held already, now tested (770.00/77.00; 700.00/70.00).
- 6f64dae (F11): both checks widened (any `payment_allocations.<all|filter|exclude|aggregate|annotate|values|values_list|iterator|order_by>(`; a Company held in a variable, or a module helper that reads the deposit/prepayment setting), and methods keyed by class (`label.Class.method`, shown as `label.method (Class)`). Planted tests in sales tests_money `TheAuditAsksItTests`.

Each fix's test failed with the fix reverted or its rule disabled: F1 `PartSettlementTakesWhatIsLeftTests` AR 0.01/AP -0.01; F2 `HeldMoneyLeavesAtTheBaseLeftTests` 4 of 8 (Deposits -0.01, Prepaid 0.01); F3 `(-45, 44.90, -0.10) != (-45.00, 45.00, 0.00)`; F4/F5/item 2/item 5 `100000.00 != 90000.00`, `V-1 != V-2`, `10.00 != 11.00`, `6.00 != 5.45`; F6 ValidationError not raised; O199 `(4.16, 4.16) != (4.16, 4.17)`; F7 KeyError undone_by_notes; F8 `(1000.00, 100.00) != (700.00, 70.00)`; F11 4 of 6 planted tests reported nothing on the old checks.

Gate on the final tree: accounting, sales, purchasing, core, inventory, manufacturing on fastsettings, --exclude-tag migration, --parallel 2: 4606 tests; failures were only the two review probes below and one O171 test whose wording my first draft changed (restored, rerun OK). `audit_invariants`: no findings. `makemigrations --check`: no changes. Migration-tagged tests not rerun this round (only nullable AddFields added).

Reviewer's probes at 6f64dae (copied in, not committed): 68 of 70 pass.
- `NoteLineAccountProbe.test_a_note_line_naming_no_original_line_credits_any_revenue_account`: errors with the refusal, which its own message allows ("or is refused"); my test asserts the refusal and the posting to 4000.
- `PricingProbe.test_a_thousand_euro_units_total_what_the_base_price_gives` (F9): still 9090.00, expected 9090.91. Not done: a 2-place unit price cannot carry 10 USD / 1.1 to the line; exactness needs the unit-price columns on sales order, quotation and invoice lines widened (6 places), which changes every API rendering of those prices (100.000000 on PostgreSQL) and the note rules that quantize prices to the paisa. That needs a decision; not taken here.

Not done / left: F9 (above). The F1 slices telescope on the order settlements happen; deleting or re-pointing a middle allocation re-states only its own slice, so a paisa can reappear after such an edit (not probed). Credit notes on an invoice still book their own lines' bases, not a slice of the invoice's; mixed with part payments, a paisa can remain (not probed). Ids to register (the integrator's): F1's mixed-path residue above, F9.
