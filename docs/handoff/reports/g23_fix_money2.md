# fix_money2 report

Worktree /home/user/work/fix_money2, base 7420512, register from 453e6b2 (docs commit 5a40e59), probes cherry-picked
from 7f7d5bd (ca2f32d). Calc folder /home/user/work/calc_fix_money2.

## Commits (each takes its row off docs/RISKS.md)

| commit | id | what |
|---|---|---|
| 7b94403 | O281 | JournalEntry.post refuses an entry dated before BooksOpening.opened_on unless `opening_balance=True`; Invoice.post / Bill.post pass `is_opening_balance` |
| 0ede680 | O282 | Account._check_currency: currency frozen once a posted line stands (unset = base, so naming the base is no change) |
| 86e26f2 | O283 | refuse_switched_off in JournalEntry.post (account, cost centre) and JournalLine.save (centre); record_taxes refuses a switched-off tax, fiscal position, charge type on a new document. Reversals (`reverses` set) and credit/debit notes (`post(correcting=True)`) are exempt |
| 7fdb72b | O284 | RecurringJournal.generate_one dates by correction_date (original = start_date) then day_that_has_come |
| (tip) | O284 | its DATED_ELSEWHERE exemption removed (audit_invariants reported it stale) |
| 9dbaba0 | O287 | Invoice.b2cl_limit kept at posting (migration sales 0078, nullable, no backfill: blank reads the live setting as before); _section reads it |
| e1e6cbe | O285 | _b2b_type shared by B2B rows and CDNR notes (the note takes its invoice's type); INVOICE_TYPES maps both in gstr1_json |
| 830d8b2 | O286 | table 13 excludes is_opening_balance invoices and plain credits of them (same exclusion as _invoices) |
| dd10036 | O288 | gstr2b.reconcile: matched total only for itc_available lines; a note names the bill otherwise |
| eb55dd9 | O289 | moment_that_has_come() in apps/core/models.py (new shared rule, beside day_that_has_come); EwayBill.record, EInvoice.record and record_cancellation ask it |
| 36f322f | O291 | gst month(): dates built inside the try (99999-01, 0000-01 now 400) |
| f66440d | O342 | committed_in_base: each confirmed order through settlement.in_base at its own order_date; unused on_date param removed |
| 7a8ad7d | O290 | import_lines holds the statement (lock_rows, re-read) in its own transaction from the closed check through the last insert |

## Failed without the fix (each reverted, run, restored)

- O281 apps.accounting.tests.NothingBeforeTheBooksOpenedTests (refused / reversal): "AssertionError: ValidationError not raised"
- O282 apps.accounting.tests.CurrencyFrozenOncePostedTests: "ValidationError not raised"; API as Bookkeeper "200 != 400"
- O283 apps.accounting.tests.SwitchedOffTakesNothingNewTests (account, cost centre): "ValidationError not raised"; the
  gst tax case is the auditor's probe SwitchedOffTaxTests, failing on the base. The fiscal-position and charge tests
  (apps.gst.tests.SwitchedOffSettingsTests) were not run against a reverted fix.
- O284 apps.accounting.tests_recurring.DatedByTheRuleTests (4): "ValidationError not raised"; API "200 != 400"
  (entry dated 2027-11-25)
- O287 apps.gst.tests.TheB2clLimitIsKeptAtPostingTests: "Tuples differ: (0, [...]) != (1, [])", note "0 != 1"
- O285 apps.gst.tests.NotesAreFiledAsTheirInvoiceTests: "Lists differ: ['R'] != ['SEWOP']" and "['R'] != ['SEWP']"
- O286 apps.gst.tests.Table13CountsWhatThisSystemIssuedTests: "[('invoices', 3), ('credit_notes', 1)] != [('invoices', 2)]"
- O288 apps.gst.tests_gstr2b.CreditNotAvailableIsNotMatchedTests: "(Decimal('1800.00'), Decimal('0'), 1) != (Decimal('0'), ...)"
- O289 apps.gst.tests_einvoice.PortalAnswerFromTomorrowTests (2): "ValidationError not raised"
- O291 apps.gst.tests.MonthTests.test_not_a_month (99999-01, 0000-01: ERROR from date());
  ABadPeriodIsAnswered400Tests as GST Officer: 500 != 400
- O342 apps.sales.tests_approvals...test_every_confirmed_order_is_weighed_at_its_own_date: "Decimal('12000.00') != Decimal('9000.00')"
- O290 PostgreSQL apps.e2e.tests_races.BankImportRaceTests without lock_rows: "AssertionError: 4 != 2 : ['done', 'done']";
  OK with it (database fix_money2, --keepdb).

## Runs on the tip

- Lane A at 7a8ad7d (fastsettings, --parallel 1): accounting, gst, purchasing, imports, assets: Ran 2085 tests, OK.
- Lane B at 7a8ad7d: sales, core, web (browser tests, after `npm run build`, PLAYWRIGHT_CHROMIUM_EXECUTABLE set):
  Ran 1756, failures=1, skipped=4. The one failure was apps.core.tests_audit_dates (the stale O284 exemption);
  fixed in the O284 follow-up (exemption removed, the test's list updated); apps.core.tests_audit_dates OK on the tip.
- `--tag migration` apps.sales (default settings, migration 0078): Ran 8, OK.
- PostgreSQL race: BankImportRaceTests OK with the lock, 4 != 2 without. Only my race was run on PostgreSQL, not the
  whole tests_races file (other agents shared the machine).
- audit_invariants on the tip: "No invariant findings." makemigrations --check: "No changes detected".
- Not run: the whole suite; PostgreSQL and DJANGO_TIME_ZONE=Asia/Kolkata lanes of the modules.

## Questions for the owner (not decided here)

1. O283: "switched off" is read as "takes nothing new". Reversals and credit/debit notes still post to it, but voids
   that build their own entry (payment void, stock and payroll voids: most set no `reverses`) are refused while the
   account or cost centre is off; the message says to switch it on again. Should every void be exempt? That needs a
   marker on each void path.
2. O283: a party on a switched-off fiscal position is refused (not taxed as if it had none). Confirm.
3. O289: no clock-skew tolerance: a portal time even a second ahead of the server's clock is refused, as
   record_cancellation already did.
4. O287: invoices posted before 0078 keep reading the live limit. A backfill at today's limit is possible if wanted.

## Defects seen and not fixed (not in the register yet)

- apps/sales/models.py RecurringInvoice.generate_one (listed in DATED_ELSEWHERE in
  apps/core/management/commands/audit_invariants.py): the same shape as O284, mirrored (mistake 5): on_date has no
  correction-date rule. Kind: rule.
- apps/accounting/recurring.py generate_one: `next_run_date` advances from the schedule's next run, not from an
  on_date given; taking March with on_date in February moves the schedule as if March were taken. Not probed. Kind: rule.
- O283 does not reach a switched-off account chosen on a draft document's line (refused only at post): fine, but the
  screens still offer it (frontend pickers filter? not checked).

## Second round (integrator's decisions), tip 3d7dc23

| commit | id | what |
|---|---|---|
| f9361fe | O283 | every void exempt: refuse_switched_off asks in_a_correction(), true when a step named void/revers*/cancel/undo (CORRECTING_STEPS, apps/accounting/models.py) is on the call stack; asked only when a switched-off account or centre is on the entry. New postings are still refused. Fiscal position refusal kept as built |
| c0caea7 | O289 | moment_that_has_come allows PORTAL_CLOCK_AHEAD = 5 minutes (portal clock skew), no more; docstring says so |
| b684d49 | O284 | sales.RecurringInvoice.generate_one dated by the same rule: both schedules ask apps.core.recurrence.run_date (new, shared); the audit's correction-date check counts run_date() as asking the rule; its exemption is gone (DATED_ELSEWHERE now holds one) |
| 3d7dc23 | O287 | sales 0079 fills b2cl_limit on posted invoices that have none from the active GstSettings at migration time, drafts left blank, prints the count |

Failed without each:
- O283 voids: apps.accounting.tests.EveryVoidTakesBackWhatASwitchedOffAccountHolds
  "ValidationError: ['5100 - Rent is switched off; it takes nothing new. ...']" (the same entry made outside a void is
  still refused). The fiscal-position and charge-type tests (apps.gst.tests.SwitchedOffSettingsTests), refusal reverted:
  both "AssertionError: ValidationError not raised".
- O289 skew: apps.gst.tests_einvoice.PortalClockSkewTests with the allowance at nothing: 4:59 ahead
  "ValidationError: ['... has not come; ...']"; 5:01 refused with it. timezone.now patched, so machine speed decides neither.
- O284 invoice: apps.sales.tests_lifecycle.RecurringInvoiceTests.test_an_invoice_is_not_taken_for_a_day_that_has_not_come
  "AssertionError: ValidationError not raised".
- O287 fill: apps.sales.tests_b2cl_migration (MigrationExecutor, tag migration) with the fill disabled
  "Tuples differ: (None, None, None) != (Decimal('250000.00'), Decimal('250000.00'), None)".

Note on in_a_correction: it goes by the step's name, as the audit's correction checks do. A step that takes something back
under any other name (a return, a release) is not exempt. One that is named void but books new business would be.
Say if a marker on each path is wanted instead.

Runs on 3d7dc23:
- Lane A at 3d7dc23 (accounting, gst, purchasing, imports, assets; fastsettings, --parallel 1): Ran 2089, OK.
- Whole apps.e2e.tests_races on PostgreSQL at 3d7dc23: Ran 103, OK.
- audit_invariants at 3d7dc23: "No invariant findings."; makemigrations --check: "No changes detected".
- Lane B (sales, core, web) and sales --tag migration: started, then killed on the integrator's word (machine
  overloaded) and lost in the reboot; not rerun whole. The integrator runs the whole suites.
- After the reboot, only what my commits add or touch: apps.accounting.tests, tests_recurring, tests_bank_import,
  tests_audit2_money; apps.gst.tests, tests_gstr2b, tests_einvoice, tests_audit2_money; apps.sales.tests_approvals,
  tests_lifecycle; apps.core.tests_audit_dates: Ran 284, OK. apps.sales.tests_b2cl_migration (--tag migration,
  default settings): Ran 1, OK. BankImportRaceTests on PostgreSQL (test_fix_money2 dropped first): Ran 1, OK.
- Not run since the reboot: apps.web browser tests on this tip (the O281/O283 rules reach the demo loader;
  lane B at 7a8ad7d had run web green before the O283 void exemption, which only widens what posts).
