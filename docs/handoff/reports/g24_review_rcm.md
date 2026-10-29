# review_rcm: GST reverse charge, fix_rcm d76ef6f..8e81501

Probes committed (unpushed) in /home/user/work/review_rcm: apps/gst/tests_review_rcm.py (12 tests),
apps/e2e/tests_review_rcm_races.py (3, PostgreSQL db review_rcm), apps/web/tests_browser_review_rcm.py (3).
Numbers: /home/user/work/calc_review_rcm/calc.py. Commit trailer says "Gate: g24" (the brief's id; {GATE} was not filled in).
Author's own gst lanes (tests_rcm_cash, tests_reverse_charge) green on the tip with my probes. audit_invariants: "No invariant findings."
makemigrations --check: "No changes detected." Races: 3 OK. Browser: 3 OK.

## Findings (each has a probe that FAILS on 8e81501)

No finding posts the books wrong, leaves a control account with a balance, or breaks the series. 1a-1d, 2, 6, 7 and the
races PASS (see below). Nothing blocks on money; F1 and F2 should be fixed or answered by the adviser before release.

F1 (medium, security, fix before release). apps/gst/views.py cash_ledger action (GstCashPaymentViewSet.cash_ledger) /
reverse_charge.cash_ledger(account): `?account=<any pk>` returns that account's ledger balance. GST Officer (view_gstcashpayment
only) GETs the payables control account and reads -10,000.00. Probe test_8 (prints "LEDGER LEAK status 200 -10000.00").
Fix: refuse an account no challan used as its cash ledger.

F2 (medium, adviser-confirm). apps/gst/returns.py gstr3b 4A3 = paid_in_cash(): credit is claimed on every challan whether or
not a self-invoice stands. Rule 36(1)(b) (the report's own cite) makes the self-invoice the document. Unregistered trucker,
freight 10,000, IGST 500, no self-invoice issued, challan paid: 4(A)(3) igst = 500 (should be 0). Same after a self-invoice
is cancelled after payment (test_4b: 500, should be 0). test_4, test_4b. (Report adviser item 4 covers whether credit must be
reversed; it does not cover credit with no self-invoice at all.)

F3 (medium). apps/gst/reverse_charge.py:received_on, `min(days)` over bill date and receipts. Docstring says the first goods
receipt, else the bill date. Bill dated 1 Sep, goods received 20 Sep: received = 1 Sep, Rule 47A last day 1 Oct instead of 20 Oct;
a self-invoice on 5 Oct (day 15 after receipt) is refused, and the health line shows it overdue. test_9 (unit, receipt dates
mocked; a full GRN fixture was too heavy for the budget). Fix: receipts when there are any, bill date only when there are none.

F4 (low). PaymentVoucher.issue_for rounds per payment: bill 10,000 + IGST 500 paid 3333.33 + 3333.33 + 3333.34 gives vouchers
166.67 + 166.67 + 166.67 = 500.01 (calc.py: 500 x 3333.33 / 10,000 = 166.6665). Vouchers of a fully paid bill exceed its tax by a
paisa; last payment should take the remainder. test_3.

F5 (low). A part debit note leaves the standing self-invoice at the full supply: freight 10,000/IGST 500, half debited on 12 Sep:
self-invoice igst 500, GSTR-3B 3.1(d) 250. test_5. Report adviser items do not list it (it lists cancelling, not amending).

## Passed (probe, scenario with rupees)
- 1a GTA (10,000/500) + unregistered trucker (10,000/500) + local (4,000, 100+100): SI, voucher, one challan 1,200: 2291, 2292, ECL = 0;
  ITC debit 1,200 = 4(A)(3); bank credit 1,200 + 10,000.
- 1b half debit note before the challan: challan 250, 2291/ECL 0, ITC 250 = 4A3.
- 1c USD 2 x 7.40 at 83.3367, new rate 84.1234 from 12 Sep, half note on 20 Sep: 2291 and ECL 0, ITC = 4A3 (note takes the bill's rate).
- 1d whole note before the challan cancels the self-invoice; the other bill's 500 pays; accounts clear.
- 2 Kolkata (TIME_ZONE=Asia/Kolkata, now patched): bill received 10 Sep; 00:30 IST on 10 Oct (day 30) issues; 00:30 IST on 11 Oct (UTC still the 10th) refused with Rule 47A; day 29 issues.
- 3 races on PostgreSQL: one payment vouchered twice at once = one set, no IntegrityError; a self-invoice racing a whole debit note
  (note holding the numbering row first) never stands; a challan voided twice at once = one void. (Author's two-SI and two-challan races not repeated.)
- 4 roles (API): Sales Rep, Warehouse Staff, Purchasing Clerk, AR Manager, HR Admin get 403 on all eight reads and writes. Officer issues
  and cancels; AP Manager cannot issue or cancel; officer cannot vouch, pay or void; clerk and rep cannot void; AP Manager pays and voids.
  Browser: clerk sees no Create/Cancel/Void on the self-invoice, voucher and challan screens; officer no Void/Create on challans; AP Manager no Cancel on a self-invoice.
- 7 challan voided, then paid again under a new CPIN: 2291 0, bank 500, cash-ledger difference 0.

## Not proven / notes
- There is no Bill.void in purchasing (a bill is reversed only by a debit note), so "bill voided" is the whole-note path (1d).
- Clerk opening /app/accounts/gst-cash-ledger or a /new route directly gets the form shell or a 403 on the page's GET; no write is offered. Cosmetic.
- Legacy: migration 0082 fills owed_account but nothing marks reverse charge settled by hand before this release; a challan for an old month would
  clear payable again. Needs a cutover decision; no probe.
- Whole debit note after the challan, nets-negative refusal: the author's item 4. Not re-found.
- Item 5 (3B after a void in a later month): 4A3 and 6.1 follow the challan's `month`, so the figure for a filed month changes on a later
  void (author's known). No new defect found.
