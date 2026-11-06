# audit2_money: accounting and gst, whole (base ab857b1)

Probes: /home/user/work/audit2_money (commit 7f7d5bd, unpushed), apps/accounting/tests_audit2_money*.py,
apps/gst/tests_audit2_money.py, apps/imports/tests_audit2_money.py; expected figures in calc_audit2_money/figures.py.
14 probes fail on the base (SQLite, fastsettings) and 1 race probe fails on PostgreSQL. No go-live blocker found in
settlement, FX, bank reconciliation, period close, TDS/TCS dated rates or the e-invoice cancellation; the books-side
blocker candidates are M17 and (arguably) M3.

## Findings, books first

M17 (books, rule; fix before go-live) apps/imports/importer.py:379 `_opening_balances`. No once-only guard: running the
same opening-balances file twice (a double click on Import, or a re-run after a failed cutover step) posts a second
"Opening balances" entry; bank 250,000 becomes 500,000 against equity. Every other opening kind refuses a repeat
(open invoices/bills by customer+reference). Probe: apps/imports/tests_audit2_money.py. Shape: mirror gap / double
processing. Guard: refuse when an opening-balances entry stands (or key it to BooksOpening), and say how to reverse it.

M3 (books, rule; decide) BooksOpening (accounting/models.py:1806) freezes the opening day against changes but nothing
refuses a later entry dated BEFORE it: a journal entry or invoice dated 15 Mar posts when the books opened 1 Apr, moving
a position the opening import fixed. Open invoices/bills legitimately carry old dates, so the rule would need to exempt
`is_opening_balance` documents. Probe fails on the base. Could be intended; the owner to say.

M2 (books, rule) accounting/models.py `Account._check_money/_check_tree`. Currency is the one account fact not frozen
once posted: the Bookkeeper (crud account) can set 1010 Bank to USD after rupee postings; payments through it are then
refused as "kept in USD" and the posted rupees are read as dollars. holds_money and account_type are frozen; currency is not.

M14/M15/M16 (inert features, rule) `Account.is_active` is read for money accounts only (money.py:70); `CostCentre.is_active`
and `Tax.is_active` are read by no posting or document. Switching off a retired revenue account, cost centre or the old 12%/28%
tax does nothing: new invoices still tax at it. Probes: one each. Also `FiscalPosition.is_active`, `ChargeType.is_active`
look the same (not probed).

M4 (rule) accounting/recurring.py:93 `generate_one(on_date=...)`: no correction_date rule; an auto-posting schedule posts an
entry 400 days ahead (also before the schedule's start) from the API (`generate` with on_date). Mirror of "a correction dated anywhere".

## Statutory

M6 (statutory) gst/returns.py `gstr1_json` cdnr block: every credit/debit note is filed `inv_typ: "R"`; a note on an SEZ
invoice (b2b type SEWOP/SEWP) goes out "R", which the offline tool rejects or mis-files. Probe: SezNoteTests.

M1 (statutory) gst/returns.py `_documents_issued`: Table 13 counts the old system's opening-balance invoices (they take an
INV- number from the series though reported in no table) and plain credits of them (CN-). With 2 real invoices and 1
opening invoice in the month it says 3 issued. Needs `is_opening_balance` / plain-credit exclusion like `_invoices`.

M10 (statutory, kept-setting-read-live) `GstSettings.b2cl_limit` is read live by `_section`: raising it after the month is
filed moves a 150,000 inter-state sale from B2CL to B2CS on recompile. The module's own promise is that a return compiled
twice gives the same figures. Fix: keep the section on the document or refuse editing the limit.

M5 (statutory, minor) gst/gstr2b.py `reconcile`: a bill matched to a 2B line the portal marks ITC not available (itcavl N)
adds its 1,800 to totals.matched (the "claimable" figure); only `filed` honours the flag.

M7, M8 (rule, minor) `EwayBill.record` (ewaybill.py:158) and `EInvoice.record` (einvoice.py:147) accept a generation / ack
time five days ahead; `record_cancellation` already refuses a future time. A future ack makes the IRN uncancellable until
then (`moment < ack_date`) and a future e-way bill is cancellable "within 24 hours" forever.

## Race (PostgreSQL)

M9 (race, minor) accounting/bank_import.py:`import_lines`. The lines already on the statement are read before any lock;
two simultaneous commit=true imports of the same 2-row file leave 4 lines (statement_difference then non-zero until one is
deleted by hand). Probe apps/accounting/tests_audit2_money_races.py with race(); failed on PostgreSQL 16 (4 != 2).
Fix: hold the statement row (`lock_rows`) around the read and the inserts.

## Crash

M11 (crash, minor) gst `month()` (returns.py): `?period=99999-01` answers 500 (date out of range after the try). Behind
gst.compile_returns only. All other bad-date inputs to the statements, ledger, GSTR-1/3B/2B and ITC-04 answer 400.

## Unproven / noted, not findings
- Role matrix (asked as each of 20 roles): no role beyond Bookkeeper, Controller, GST Officer reads the GL or returns. Quality
  Inspector, Warehouse Staff, Maintenance and others read `party-tax-profiles` (every party's PAN, GSTIN, TDS section and
  lower-deduction rate). Business PANs, but TDS rate/certificate detail is more than they need. GST Officer reads the whole
  ledger and the statements (view_journalentry), payroll postings included; set deliberately in setup_roles.
- Maker and checker: the Controller types a TDS/TCS rate and confirms it (confirm_tdsrate / confirm_tcsrate); nothing needs a
  second person. Unproven as a defect (the brief names "the accountant's confirmation").
- `TcsDeclaration.covering` does not compare the declaration's PAN with the buyer's PAN on file.
- `gstr2b.reconcile` pairs lines with `setdefault` across statements in pk order: an amended invoice kept in a later month's
  2B (same number) loses to the earlier line. Needs a two-month fixture to prove.
- Recurring `generate/` without `next_run_date` in the body has no concurrency guard (two clicks take two).

## Probed and held (PASS)
- 40 random invoices (fractional kg, discounts, 5/18% and split-tax pairs): document total = receivable debit = GSTR-1
  value = items + tax, to the paisa (RoundingAgreesEverywhereTests).
- 12 EUR 1,000.07 invoices paid in 3 to 5 parts at moving rates, every other one with a part bounced and paid again:
  amount due 0 and the receivable nets to exactly 0 (ForeignInvoiceCycleTests).
- Bad-date inputs to 15 report endpoints: 400, except M11.
- Read in full and found guarded: Payment post/void, journal reversal (once, hand reversal of a document's entry refused),
  period close/reopen/delete, bank statement close/line locks/return-of-payment matching, challan pay_over, dated TDS/TCS
  rates (taken rates frozen, confirmation vouches for what was read), IRN cancellation (24h, settled, TCS paid over, e-way
  bill standing), settlement discount dating (correction_date on both sides), ITC-04 voids across periods, setup_indian_books.

## Not reached
PostgreSQL race for period close against a posting, and for two settlement discounts at once; DJANGO_TIME_ZONE=Asia/Kolkata
run of the whole probe set (the probes use fixed dates; no `now().date()` was found in accounting or gst by grep);
TDS thresholds across a financial-year boundary (purchasing/tds.py is the trade lane); budgets and analytic reports;
documents.py PDFs; the frontend's accounting screens in a browser; the full month-of-thirty-documents scenario with a
close between.

## Gates
`makemigrations --check --dry-run`: "No changes detected".
`audit_invariants`: started at 16:14 on SQLite (fastsettings); still running after 15 minutes of CPU when this report was written, nothing printed. Its output (and `exit=`) is appended to /home/user/work/reports/audit2_money_invariants.txt when it ends; not read by this report.
