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
