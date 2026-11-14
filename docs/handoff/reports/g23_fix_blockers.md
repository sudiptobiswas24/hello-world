# fix_blockers: O280, O313, O314, O300, O292, O293

Worktree /home/user/work/fix_blockers, from 4823661 with the five probe commits cherry-picked
(d11546a, 89546af, 8815e9c, 8f16bb3, 802a2ff). Not pushed.

## Commits

| commit | ids | what |
|---|---|---|
| 7f7645b | O280, O313 | one once-only guard, `apps.imports.base.once_only()`, asked by every opening kind before it writes |
| 484a59e | O314 | deploy/backup.sh: each step checks its own status; a failure says so and retires nothing |
| 14a33fa | O300 | a leaver's login stops after the leaving date; nobody who has left approves |
| a979412 | O292 | `apps.core.api.posts_its_notes()`: a return's notes post only for whoever may post what they correct |
| 1199be3 | O293 | `apps.accounting.settlement.in_base()`: limits, margin and budgets weigh a document in the base |

Every commit ends with the literal `Gate: {GATE}` and the two attribution lines.

## Each fix, and the test that failed without it

**O280, O313.** `once_only(what, standing, undo, column)` in apps/imports/base.py is shared by importer.py
and cutover.py. The kinds that ask it: opening_balances (an entry with the reference "Opening balances" that
is posted, not reversed and not a reversal), opening_stock (a posted, not voided adjustment for the same
item, warehouse and lot whose reason posts to the same account as this run's reason, so a second reason code
does not get past it), and open invoices, bills, sales orders and purchase orders (by party and reference, as
before, now through the shared guard). The refusal comes before anything is written. `importer.OPENING` lists
the kinds, and a new audit_invariants check, `opening_kinds_brought_in_twice`, reports a handler that does not
call once_only(), or a kind named open... that is not listed.
Tests: apps.imports.tests.TheOpeningPositionComesInOnceTests (the probes with their numbers: bank 2,50,000
once; 1,000 kg at 3.50 on a 1,500 kg shelf stays 1,500 and the opening account -3,500.00; plus the reverse
paths: reverse the entry, or void the adjustment, and the file comes in again; plus the planted check).
Without the fix:
`AssertionError: True is not false` (balances) and `Tuples differ: (True, []) != (False, ['sku'])` (stock).
The probe files apps/imports/tests_audit2_money.py and tests_audit2_core.py were moved into tests.py.

**O314.** `failed()` prints `BACKUP FAILED: <why>; older backups kept` and returns 1. dump() checks pg_dump,
an empty dump, the tar, reading the archive back, the manifest pipe and the renames. Retention runs only
after all three files are in place. `backup.sh now` exits 1 on a failure. Tests:
apps.core.tests_backup.AFailedBackupKeepsTheOlderOnesTests (the probe's stubbed pg_dump and sleep, a failing
tar, the now path, and a good night still retiring old backups). Without the fix:
`'BACKUP FAILED: pg_dump exited 1; older backups kept' not found in "... backup written: ...dump () ..."`.

**O300.** core.models gains `register_login_end()` and `login_ended(user, day)`. hr registers `leaver_login`
in apps.py; core imports nothing new. These ask it: core.auth.LockoutModelBackend.user_can_authenticate (sign-in, and every
request of an open session through ModelBackend.get_user, so HTTP basic too), ApiKeyAuthentication, and
ApprovableMixin.approve (sales and purchasing order approvals). hr's `check_still_working(by)` (is_working_on
today) is asked by leave check_approver, the timesheet check_approver and the expense claim _check_decider.
Employee.save switches `user.is_active` off once the leaving date has passed. It switches it back on when the
leaving date is withdrawn or moved past today, but only for a login it would itself have switched off. Tests:
apps.hr.tests_pay_outputs.ALeaverNoLongerSignsInOrDecides (5 tests: the two probes, the date withdrawn, a login
switched off for another reason staying off, a future leaving date and an open session) and
apps.sales.tests_approvals ...test_somebody_who_has_left_approves_nothing. Without the fix:
`AssertionError: True is not false` (is_active) and `ValidationError not raised` (a leaver's leave approval).

**O292.** `posts_its_notes(user, corrected)` returns `user.has_perm("<app>.post_<model>")` of the corrected model.
These ask it: sales customer_return (Invoice), and purchasing return_receipt and reject (Bill). They had
each spelled `has_perm("purchasing.post_bill")` themselves. Delivery.create_return(post_notes=) ->
_credit_returned_goods(post=) -> Invoice.create_credit_note(post=), as Bill.create_debit_note(post=) already
works. The return's allocation now subtracts credit notes still waiting as drafts from what a line can take,
so a second return credits the next invoice rather than the same line again. Tests:
apps.sales.tests_partial_returns.WhoMayCreditByReturningTests (S6; the AR side posting the draft, which only
then cuts the receivable; a poster's return; two returns over two invoices). Without the fix:
`AssertionError: 1 != 0`; without the draft count alone: `Lists differ: [1, 1] != [1, 2]`.

**O293.** `in_base(amount, currency, on_date, rate=None)`: at the rate the document posted at, else at its
currency's rate on its own date, rounded to money. `in_the_base(document)` gives the wording. Sales:
policy_figures, the order limit and the margin's revenue (`total_in_base()`). Purchasing: policy_figures, order
and line limits, approver tiers, the budget reason at confirm, Budget.spent (each bill at its posted
exchange_rate) and Budget.committed. Tests: apps.sales.tests_approvals.AForeignOrderIsWeighedInTheBaseTests
(S9: 45,00,000 against 10,00,000; S10: margin 95.56, worked in /home/user/work/calc_fix_blockers/o293.py) and
apps.purchasing.tests_budgets.AForeignOrderIsWeighedInTheBaseTests (P5 with the line limit; 10 x 10 EUR commits
9,000.00 of 10,000, 4 billed is 3,600.00 spent and 5,400.00 committed, and 20 EUR more asks against the
1,000.00 left). Without the fix: `'The order is 4500000.00 in the base currency' not found in ''`,
`Decimal('-300.00') != Decimal('95.56')`, `Tuples differ: (Decimal('100.00'), Decimal('9900.00')) != ...`.

The probes for these rows were moved out of the tests_audit2_* files into the normal test files. The other
agents' probes are left where they are, still failing.

## Runs

- hr + core (fastsettings, --parallel 1) on the O300 tree: 1,065 tests, 17 failures, all other rows' probes
  (15 in hr/tests_audit2_people.py, core/tests_audit2_followup.py, core/tests_audit2_paper.py).
- Final: imports, hr, sales, purchasing, accounting, core, web, browser tests included after `npm run build`: 3,959 tests in 6,738 s, failures=35, skipped=6; all 35 are other rows' probes (accounting/tests_audit2_money 6 and its races 1, core followup 1 and paper 1, hr/tests_audit2_people 15, purchasing/tests_audit2_trade 5, sales/tests_audit2_trade 6). Output: /home/user/work/calc_fix_blockers/final.txt
- audit_invariants on the tip: "No invariant findings."
- makemigrations --check: no changes (no migration in any commit, so no `--tag migration` lane).
- Races on PostgreSQL (database fix_blockers): apps.e2e.tests_races, 100 tests, OK (/home/user/work/calc_fix_blockers/races.txt)

## Questions for the owner (nothing decided)

1. Leaving day: the login is refused from the day after the termination date, because is_employed_on counts
   the leaving day as worked. Should it stop on the leaving day itself?
2. A leaving date still to come leaves `is_active` on after the date passes. login_ended refuses the login
   anyway, but the Logins screen still shows it active until the Employee is saved again. Is a morning job
   to switch those off wanted?
3. `check_still_working` uses is_working_on, which needs status ACTIVE: a manager whose status is On Leave
   cannot approve leave, hours or claims. Intended?
4. Withdrawing a leaving date switches the login back on, if the leaving had switched it off. Should HR
   switch it on by hand instead?
5. A second opening-balances file is refused while the first entry stands, even if it holds other accounts.
   The balances therefore come in one file, or the first entry is reversed. Is that right?
6. The credit-limit breach (sales/models.py, credit_limit_breach) still converts at today's rate,
   `rate_on()`, not at the order's date as in_base does. Same root as O293; left alone, because
   committed_in_base would also have to move.

## Defects seen and not fixed

- D1 (double processing, mirror of the O292 draft count): apps/purchasing/models.py:5351
  `quantity_debited` counts posted debit notes only, and `_debit_returned_goods` (~7849) allocates by it.
  Bills A (5) and B (5) for two receipts of 5; a login without post_bill returns both receipts. Both draft
  debit notes take bill A, and nothing is drafted against B. Read from the code, not run. Whether posting both
  over-debits A depends on a post-time ceiling. The T7 probe P3 says a typed debit line of 5,000 posts against
  a 1,000 bill, so likely yes. Sales now counts the drafts; purchasing should share that rule.
- D2 (double processing): on both sides a draft note from a return is not counted by
  `quantity_creditable()` / `quantity_debitable()`. While a store hand's draft waits, AR can credit the same
  invoice line by hand in full; posting the draft then credits it twice, unless a post-time ceiling exists
  (T7 says it does not). apps/sales/models.py:3390, apps/purchasing/models.py:5351.
- D3 (data, rule): `approved_figures` kept before O293 for an approved, confirmed foreign-currency order hold
  document-currency figures. After this change they are compared with base figures, so any edit of such an
  order is refused as past its approval. No effect before go-live; with live data it needs a data migration
  or a re-approval. apps/core/approvals.py refuse_going_past_the_policy.
- D4 (scope): Budget.requested() (apps/purchasing/models.py ~1520) adds `estimated_price` with no currency.
  The requisition carries none, so it is taken as base; I did not check whether a requisition can be in
  another currency.

## Not done

- No race fix was in these rows, so no race() test was added; the existing races were run on PostgreSQL.
- The Kolkata time-zone lane and the full PostgreSQL suite were not run (not in the brief's list).

---

# Second round: the review (/home/user/work/reports/review_blockers.md), O332 and O334

The review probes (634e900, from ff7bf05) were cherry-picked first. Eleven commits since, one per item:

| commit | item | what |
|---|---|---|
| d2ca4c7 | 1, O314 | no pipe in the manifest step: each hash's status is asked, sort is a step of its own; the set is unpacked and `sha256sum --check --strict` against the manifest before any rename; retention only after |
| 5221838 | 5, O314 | failed() removes the night's .partial files and its check directory |
| 5989ebe | 2, O280/O313 | importer.run() takes the company row (lock_rows) for every kind in importer.OPENING before any handler or once_only() asks; race() on PostgreSQL for balances and stock |
| 9f51484 | 7, O280 | vendor_prices: once_only() on vendor, item, currency, break and start |
| 0c210b3 | 3, O300 | hr.LeaverLoginSwitch (migration hr 0029) keeps who switched the login off and when, and who switched it back on; a withdrawn date switches on only a login with an open switch |
| ddf8db2 | O332 | check_still_working: being employed that day is enough (a manager On Leave decides); a leaver is told "has left (leaving date dd-mm-yyyy)" |
| 2bdcd63 | 6, O300 | core.models.check_login_acts(by), the one rule for login-keyed decisions: ApprovableMixin.approve, PurchaseRequisition._decide, BomChangeOrder.apply/reject, Inspection.decide; login_ended(cached=True) answers the backend once per request |
| e4bb25e | 4, O293 | in_base() refuses in words ("No exchange rate for GBP on 01-03-2026"), and both confirm() methods ask it; readers use base_or_none(); Budget.committed() leaves such an order out, and Budget.unweighed() names it (figures `not_counted`, the budget reason, the order's approval_reasons) |
| 8fc08f4 | O334 | credit_limit_breach adds total_in_base() (the order's date) instead of today's rate |
| 8135dad | O300 | hr.LeaverLoginSwitch listed in NOT_PAY_INPUTS (audit_invariants "pay input unclassified") |

Failures without each fix (the test reverted to the code before it):
- d2ca4c7: `AssertionError: 0 != 1 : ... backup written: .../erp_..._2024.dump (4.0K) ...` (both new tests)
- 5221838: `Lists differ: ['erp_2026-10-20_2024.dump.partial'] != []` (the dump case; the archive case also left `.media.tar.gz.partial`)
- 5989ebe (PostgreSQL, without the lock): `Lists differ: [True, True] != [False, True]`, balances and stock alike
- 9f51484: `Tuples differ: ([], 2) != ([(2, 'sku')], 1)`
- 0c210b3: `AssertionError: True is not false` (admin-disabled login back on after the date was withdrawn)
- ddf8db2: the On Leave manager's approval raised; the old wording `'... is not working here on 20-10-2026 (terminated) ...'`
- 2bdcd63: `ValidationError not raised` (requisition, change); `200 != 400` (quality decide)
- e4bb25e: `ValidationError not raised` (confirm); an ERROR from `No exchange rate for GBP effective on 2026-03-01.` (budget read); `'No exchange rate for GBP on 01-03-2026' not found in ''` (sales)
- 8fc08f4: `(Decimal('12000.00'), Decimal('10000.00')) is not None`

Worked figures: /home/user/work/calc_fix_blockers/o293_gap.py (10,000 budget; a base order of 10 x 100 commits 1,000.00; 9,000.00 left). For O334, 10 x 10 EUR at 90 is 9,000 against a 10,000 limit, and 12,000 at April's 120.

A worker restart interrupted the round after ddf8db2. Work resumed from the uncommitted edits of item 6, which were intact; nothing was started over.

## Second round: questions and defects seen

- Q7 (owner): committed_in_base still converts other confirmed orders at the day's rate, while this order's own share now uses its date (O334 as decided). One exposure figure built from two kinds of rate.
- Q8 (owner): the opening imports serialise on the company row, so an opening file waits behind any other opening file, and behind any code holding that row.
- Q9 (owner): `login_ended(cached=True)` holds the answer for one request. A request that sets a leaving date and then signs the same login in again sees the old answer until the next request. Decisions never use the cache.
- D5 (scope): deploy/backup.sh's check runs `cd "$check"` with the manifest path built from BACKUP_DIR, so BACKUP_DIR must be absolute (it is /backups in the compose file).
- D6 (cost): the nightly check unpacks the whole attachments archive beside the backups once more, so the disk needs room for a second copy of the media while it runs.

## Second round: runs (tip 8135dad)

- Lane: imports, hr, sales, purchasing, accounting, core, web (browser tests, after `npm run build`), plus manufacturing and quality, which item 6 touched. fastsettings, --parallel 1. Result: 5,983 tests in 7,347 s, failures=34, errors=1, skipped=8.
  - All 34 failures are other rows' audit probes: accounting 6, core followup 1, core paper 1, hr people 15, purchasing trade 5, sales trade 6.
  - The one error is the review's print-only probe `tests_review_blockers.Probes.test_order_dated_before_first_rate_breaks_budget_reads`. Its setup confirms a EUR order dated before EUR's first rate, and item 4 now refuses that confirm: "No exchange rate for EUR on 01-12-2025". That is the fix working; the scenario can no longer be built.
  - Output: /home/user/work/calc_fix_blockers/final2.txt
- `--tag migration` for hr (migration 0029): 1 test OK.
- Races on PostgreSQL (database fix_blockers, test databases dropped first): 102 tests OK, including the two new OpeningImportRaceTests. Output: /home/user/work/calc_fix_blockers/races2.txt
- audit_invariants: the first run reported "pay input unclassified: hr.LeaverLoginSwitch". After 8135dad: "No invariant findings."
- makemigrations --check: no changes.
