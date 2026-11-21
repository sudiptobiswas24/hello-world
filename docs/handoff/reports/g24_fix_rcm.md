# fix_rcm: GST reverse charge after the books (g24)

Worktree /home/user/work/fix_rcm, base d76ef6f. Brief: /home/user/work/g24tree/docs/handoff/g24_rcm.md.
Numbers worked in /home/user/work/calc_fix_rcm/rcm.py before the tests were written.

## Commits
Round 1: 1 commit, ce51818 (on d76ef6f): "gst: reverse charge self-invoice, payment voucher, cash payment and GSTR-3B 6.1
(RISKS limit 16)". Not pushed. Worktree kept.

Closes RISKS known limit 16 (no register id; the brief names none). docs/RISKS.md not edited, as the brief says:
the integrator should rewrite limit 16 (see "What is left" below for its new wording).

Round 2 (coordinator's follow-up):
- 831938b purchasing: a debit note gives reverse charge back into the account its bill owed it into (mistake 4).
  owed_account_at_posting(line, tax) is the one reader; BillLineTax declares KEPT_FROM/KEPT_BY; audit_invariants'
  kept_settings_read_live now sees a key read (`_id`) and lets a keeper name its event function. Failed without:
  "(Decimal('500'), Decimal('-250')) != (Decimal('250.00'), Decimal('0.00'))". The planted-read test proves the check
  reports both the old read and a key read.
- d27a2af purchasing: reverse charge booked in base tax by tax, as the challan pays it (rounding at a different point).
  0.37 + 0.37 USD at 83.3367: rows 30.83 + 30.83 = 61.66, old entry 61.67 (calc_fix_rcm/fx_drift.py). Failed without:
  "(Decimal('61.67..'), ..) != (Decimal('61.66'), Decimal('1233.38'))"; payable, cash ledger and bank now foot.
- 8e81501 frontend: self-invoices (issue from a bill, cancel), payment vouchers (issue for a payment), GST cash
  challans (pay, void), GST cash ledger; registered under accounts with the permissions the server checks. Payment
  vouchers moved to the AP Manager (they need the payment, which only the payer reads); the GST Officer reads them.
  The voucher POST now returns the first voucher made with `issued` listing all. Browser test
  apps/web/tests_browser_reverse_charge.py (3, as GST Officer and AP Manager, no 403/404). vitest 55 passed, build ok.
  The NO_SCREEN exemptions of round 1 are removed.
Round 2 lanes on 8e81501's tree: gst+purchasing+accounting+web, fastsettings, --exclude-tag migration, browser tests
after npm run build: 2147 tests OK, none skipped. audit_invariants: no findings. makemigrations --check: no changes.
`apps.gst --tag migration` ran 0 tests: gst has no migration-tagged test. Round 2 added no migration; purchasing
0082's migration lane passed in round 1 (10 OK). Not pushed; worktree kept.

Round 3 (review_rcm findings, /home/user/work/reports/review_rcm.md):
- 02c0b24 review_rcm probes cherry-picked from dc73ef7, trailer reworded to `Gate: {GATE}`.
- c5b0fa0 F1: cash_ledger/?account= answers only for a GST cash ledger or a reverse-charge payable account, 400
  otherwise (probe test_8 printed "LEDGER LEAK status 200 -10000.00" before). CashLedgerReadTests: officer 200/200/400/400,
  Purchasing Clerk 403.
- 2502198 F3: received_on = first goods receipt when there is one, else the bill date (service). Probe test_9 (1 Sep
  != 20 Sep before) passes; ReceivedOnTests covers a service billed 3 Sep and self-invoiced 3 Oct.
- fcd75a2 F4: the voucher that brings a bill's standing vouchers to its total takes the remainder: 166.67 + 166.67 +
  166.66 = 500.00 (probe test_3 gave 500.01). Bills are held with the payment.
- f7a8c05 F2: credit only once paid in cash AND the document stands (s.16(2)(a), Rule 36(1)(b)). New
  Tax.reverse_charge_waiting_account (accounting 0032); bill posts Dr waiting / Cr owed; BillLineTax keeps owed,
  waiting, credit accounts (purchasing 0083, old rows waiting = credit = paid account); ReverseChargeCredit facts
  (gst 0008): claimed at the challan for a registered supplier's bill, at the later of challan and self-invoice
  otherwise; taken back by its own entry on a challan void or a self-invoice cancellation. 4(A)(3) reads standing
  credits. Probes test_4, test_4b (500 before) pass. Probe test_1d edited: it now issues the second bill's
  self-invoice, which its 1390 = 500 expectation needs under F2. Probe test_5 (F5, not mine) marked
  expectedFailure. Every RCM tax now needs a waiting account: existing ones in production must be given one
  before their next bill posts (posting refuses with a sentence) - a cutover step for the integrator.
  4(B) not built: a cancellation after filing drops the credit from the recompiled month and the books reverse it
  on the cancellation day; whether the portal wants 4(B)(2) of the cancellation month with interest under s.50(3)
  is for the adviser (cited, not confirmed).
- f2dd859 web: the self-invoice browser test waited for the "Cancelled on" label, not the cancellation; it read the
  database before the POST answered on a slow machine ("None != 2026-09-11"). It now waits for the Cancel action to go.
Round 3 lanes after the container restart, on f2dd859: build ok, vitest ok; gst+purchasing+accounting+web fastsettings
--exclude-tag migration with browser tests: OK (1 expected failure, probe test_5 = F5). Reviewer's races
(tests_review_rcm_races) and ReverseChargeRaceTests on PostgreSQL (fix_rcm): 6 OK. audit_invariants: no findings.
makemigrations --check: no changes. gst+purchasing+accounting --tag migration: 12 OK.

## What is built (as the brief decided)

New module apps/gst/reverse_charge.py, models re-exported from apps/gst/models.py, migration gst 0007.

1. **Self-invoice** `SelfInvoice` (series `gst.self_invoice`, SI-YYYY-NNNNN through DocumentSequence, consecutive,
   numbered under O257). `SelfInvoice.issue(bill, on_date)`: holds the bill (lock_rows), refuses a draft, a debit note,
   a prepayment or opening bill, a bill with no reverse-charge tax, a bill whose supplier had a GSTIN when it posted
   ("the supplier's own tax invoice is the document"), a second standing one (also a partial UniqueConstraint), a
   bill wholly debited back, a day before receipt, a day to come, and a day past receipt + 30 days with a sentence
   naming Rule 47A and the last day. Receipt day = earliest goods receipt of an order line the bill bills, else the
   bill date; kept on the self-invoice. Figures (taxable, IGST/CGST/SGST/cess in base) frozen from the bill's
   recorded tax rows. Reverse path: `cancel(reason, on_date)` under correction_date(), @serialised, @takes_back;
   save() refuses changing any fixed field, delete() refuses. A replacement takes the next number.
   Health line `self_invoices` (apps/web/health.py): every reverse-charge bill from a supplier unregistered when it
   posted, not wholly debited back, with no standing self-invoice; rows say the due day and "(overdue)".
2. **Payment voucher** `PaymentVoucher` (series `gst.payment_voucher`, PV-): s.31(3)(g) is still in force, so
   `PaymentVoucher.issue_for(payment)` issues one per reverse-charge bill a standing payment settles, dated the payment
   date, amount = allocation in base, tax by head = bill's RCM tax x allocation / bill total, rounded per head.
   A payment void cancels its vouchers (accounting.register_void_follow_up). Health line `payment_vouchers`.
3. **Tax paid in cash** `GstCashPayment` (challan PMT-06: CPIN 14 digits, unique; month; date paid; bank; the account
   standing for the electronic cash ledger) with `GstCashPaymentRow` facts (each BillLineTax paid, head, kept account,
   signed base amount). `pay(month, date, bank, cash_ledger_account, cpin)` pays everything owed under reverse charge in
   the month and not yet paid (as TdsChallan.pay): numbering_first, rows held FOR NO KEY UPDATE in pk order and read
   again; refused if nothing waits, dated before the last bill, a day to come, a head nets negative ("more was taken
   back than owed"), the bank holds no money, or the cash ledger is not an asset of its own.
   Entry: Dr cash ledger / Cr bank (deposit, by head) and Dr each kept reverse-charge account / Cr cash ledger
   (set-off, by head). Never against input credit. `void(on_date)` reuses accounting.challans.void_challan (shared with
   TDS/TCS: correction_date, reversal); rows then wait again. Registered as bank movements for reconciliation.
   `cash_ledger(account)`: deposited, set off, balance by head against the account's ledger balance; health line
   `gst_cash_ledger` reports any difference (a hand journal on the account).
   purchasing: `BillLineTax.owed_account` keeps the account the tax was owed into at posting (a debit note's row takes
   its bill row's); the payment clears that, never the tax's account as it stands (mistake 4). Migration purchasing
   0082 adds it and fills existing reverse-charge rows from the tax's current account (the only record there is).
4. **GSTR-3B**: `4A3` is now what standing challans for the period paid, by head (Rule 36(1)(b)), not what bills
   owed; new `6.1` = {"reverse_charge": {"payable" (= 3.1(d) by head), "paid_through_itc" (always 0), "paid_in_cash"
   (read from the challans)}}; a warning per head where owed and paid differ. NOT_BUILT now names only 6.1(A)
   (set-off of tax other than reverse charge), interest and late fee.
5. **Both ways**: whole debit note after a self-invoice cancels it on the note's date (new
   purchasing.register_debit_note_follow_up, called inside Bill.post for a debit note); a part note leaves it;
   challan void; debit note before the challan pays less (450), after it the next month nets negative and is refused;
   supplier registering mid-month (bill before: self-invoice required and issued; bill after: refused).
6. **API** (/api/gst/): self-invoices/ (POST {bill, date}; cancel/; awaiting/), payment-vouchers/ (POST {payment};
   awaiting/), cash-payments/ (pay/, void/, cash_ledger/). Roles (setup_roles): GST Officer adds/changes self-invoices,
   adds vouchers, reads challans; AP Manager pays challans; void needs purchasing.post_bill (as a TDS challan).
   No screens built: the office application has none for these yet.

## Law, with citations (verify the starred ones)
- s.31(3)(f) as amended by Finance (No. 2) Act 2024 s.117, from 1 Nov 2024 (notification 17/2024-CT*): self-invoice
  for supply from a supplier "not registered on the date of receipt".
- Rule 47A, notification 20/2024-CT dated 8 Oct 2024, from 1 Nov 2024: within 30 days of receipt. Confirmed from
  secondary sources (ICAI Navi Mumbai compilation, EY alert, Taxguru); the Gazette text was not fetched.
- Rule 46 (contents, consecutive series), Rule 52 (payment voucher contents), s.31(3)(g) (voucher at payment).
- s.2(82) output tax excludes reverse charge; s.49(4) credit ledger pays output tax only: so cash. s.49(1), Rule 87,
  PMT-06 (CPIN). Rule 36(1)(b): credit on a self-invoice "subject to the payment of tax"; s.16(2)(c).
- Circular 211/5/2024-GST (26 Jun 2024): s.16(4) year for RCM from unregistered = year of the self-invoice; late
  self-invoice carries interest and s.122 penalty exposure.
- GSTN advisory (2025) on the "RCM liability/ITC statement" on the portal: 3.1(d) vs 4A(2)/(3) tracked by the portal.

## For the tax adviser (not confirmed, not built)
1. Time of supply: 3.1(d) and the challan's month use the bill date. Under s.12(3)/13(3) (amended from 1 Nov 2024)
   the time of supply for a self-invoice case is the self-invoice date; for GTA, the earlier of payment or 60 days
   after the supplier's invoice. A self-invoice issued in October for a September bill is reported in September here.
2. Discharge date: the books set the liability off on the deposit date; on the portal the set-off happens on filing
   GSTR-3B. Deposit and set-off are one document here, of equal amounts; any excess deposit is not modelled.
3. Registration "on the date of receipt": decided by the GSTIN the bill froze when it posted, not a dated
   registration (the profile has no effective date). A receipt before registration billed after it is missed.
4. Whether a self-invoice may be cancelled after its tax is reported/paid, and what a whole debit note after payment
   does: here the self-invoice is cancelled and the next month's 3.1(d) goes negative and the challan is refused;
   whether credit already taken must be reversed in 4(B)(2) is for the adviser.
5. Consolidated monthly self-invoice (Rule 46 proviso) not built: one per bill.
6. Penalty and interest on a late self-invoice or late payment: refused here with a sentence, not computed.
7. Notification 17/2024-CT and the Rule 47A Gazette text not seen first-hand. 57th Council e-invoicing of
   self-invoices (reported Oct 2026) is not law; not built.
8. Payment voucher tax on a part payment pro rata: Rule 52 asks "amount of tax"; pro rata is our reading.

## Tests (each failed without its fix; mutation, then restored)
New: apps/gst/tests_rcm_cash.py (SelfInvoiceTests, CashPaymentTests, PaymentVoucherTests, ReverseChargeApiTests as
GST Officer / AP Manager / Purchasing Clerk), apps/e2e/tests_races.py ReverseChargeRaceTests (3, PostgreSQL).
- Rule 47A refusal removed: test_the_thirtieth_day_is_the_last -> "AssertionError: ValidationError not raised".
- 4(A)(3) read from payable instead of the challans: test_paid_in_cash_... -> "{'igst': Decimal('500.00'), ...} != {'igst': Decimal('0'), ...}".
- Payment clears the tax's current account instead of the kept one: test_it_clears_the_account_... ->
  "(Decimal('500'), Decimal('-500')) != (Decimal('0.00'), Decimal('0.00'))".
- Debit-note follow-up unregistered: test_a_whole_debit_note_cancels_it... -> "None != datetime.date(2026, 10, 26)".
- Void follow-up unregistered: PaymentVoucherTests -> "None != datetime.date(2026, 9, 18)".
- lock_rows(bill) removed in SelfInvoice.issue, on PostgreSQL: test_one_bill_self_invoiced_twice... -> "'has a
  self-invoice standing' not found in 'refused: duplicate key value violates unique constraint
  \"one_standing_self_invoice_per_bill\"'". (The two-bills and two-challans races are also held by the O257
  numbering row, so they pass without the local lock too; they prove consecutive numbers and one payment.)

Lanes on ce51818's tree: gst+purchasing+accounting+sales+e2e+web fastsettings --exclude-tag migration, browser tests
after npm run build: 3513 tests, 2 failures, both mine (web NO_SCREEN list and the health probe list did not name the
new collections and probes), fixed and their classes re-run with apps.gst: 288 OK. audit_invariants: no findings.
makemigrations --check: no changes. apps.gst + apps.purchasing --tag migration: 10 OK. ReverseChargeRaceTests on
PostgreSQL (fix_rcm): 3 OK. The first run of the lane script was stopped at the 2-hour background limit during the
migration lane and was re-run detached after the session restart.

## What I could not do / limits
- Screens built in round 2. Health links still point at /accounts/gst and the bill/payment.
- Existing tests in apps/gst/tests_reverse_charge.py asserted 4(A)(3) from the bill alone; rewritten to 0 until paid,
  per decision 2 (they encoded the old behaviour, mistake 7).
- Edited apps/core/management/commands/setup_roles.py (role grants) and audit_invariants.py (two listed exemptions:
  GoodsReceipt read only for the receipt day; GstCashPaymentRow.amount signed by design). Neither is fix_audit's or
  fix_statutory's named file, but setup_roles may count as "seeds": the integrator should check for a clash.

## Defects seen and not fixed
1. FIXED in 831938b. apps/purchasing/models.py, Bill._build_journal_entry (the `tax_totals[tax.reverse_charge_account] -= amount` line):
   a debit note reads the tax's reverse-charge account live. Bill 10,000 IGST 5% owes 500 into 2291; the tax is moved
   to 2299; a half debit note posts Dr 2299 250 instead of 2291. 2291 holds 500, 2299 -250; the challan (250, kept
   account 2291) leaves 2291 at 250 and 2299 at -250. Kind: a setting read live where a fact should be kept (shape 11).
2. FIXED in d27a2af. Foreign-currency reverse charge (import of services billed in USD): the bill's entry rounds the base per account
   total, the return and the challan per row; a paisa can be left on the payable. Kind: rounding at a different point.
3. returns.gstr3b reads challans "as they stand today": a challan voided after the return was filed changes that
   month's 6.1 and 4(A)(3). Kind: a return recomputed after its inputs moved.

## Round 4: F2 cutover (6a6f811)
setup_indian_books makes 1319 "Reverse-charge credit awaiting payment" and sets it on every reverse-charge tax lacking
one; it creates no reverse-charge tax itself. Migration accounting 0033 sets 1319 where that account exists and its
summary names the taxes it could not set. go_live_check fails "Reverse charge: credit waiting account" while any
lacks it. A bill refused for want of it names the tax and says the Controller sets it in accounting setup.
Tests: apps/gst/tests_rcm_cutover.py (4). The migration step is called directly with the live app registry, not through
MigrationExecutor. Lanes: gst+purchasing+accounting+imports fastsettings 2014 OK (1 expected failure, F5);
accounting --tag migration 2 OK; audit_invariants no findings; makemigrations --check no changes.
- b4e515e: accounting 0033 tested through MigrationExecutor (WaitingAccountCutoverMigrationTests, @tag migration). The test
  migrates to 0032, then to 0033 without account 1319 (RC1 not set and named in the summary; RC2 kept), back to 0032,
  makes 1319, and forward again (RC1 waits in 1319). accounting --tag migration: 3 OK on SQLite, 3 OK on PostgreSQL (fix_rcm).
