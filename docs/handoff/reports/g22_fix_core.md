# fix_core, g22: report

- **Worktree:** /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/fix_core, base 49459e6, tip 5bf7ddb. The tree is clean and nothing is pushed.
- **Hand figures:** /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/calc_fix_core/ (o213.py, o215.py).
- **Interruptions:** the session stopped once at the usage limit, and the machine restarted twice. Each time I resumed from the last commit; nothing was redone.
- **Register:** docs/RISKS.md is untouched; the integrator closes the rows.

## Commits (oldest first), each ending `Gate: {GATE}`

| Commit | Closes | What |
|---|---|---|
| 966e3cd | O213 | A take-back outside a final run is a deduction beside the period's piece work; PF/ESI read the earning |
| bf090a2 | O215 | A former employee's shortfall is settled by a receipt or the Controller's write-off; both have voids |
| 6535db4 | O32 | `LeaveRequest.cancel` is on `correction_date()` |
| 9351fde | O176 | An expense claim is not paid on a day still to come |
| 724b5cd | O197 | Logins are written through the mixin's transaction; the audit check reads a viewset by its module |
| daab6c1 | O15 | Nine correction steps are on `correction_date()`; three stay excepted, each with its reason |
| ef47e51 | O130 | `scoped()` keeps rows that name no party |
| 6cd8e65 | O246 | `greenwich_dates` reports `.date()` of any moment that may be aware |
| 7f60d5d | O40 | The per-unit check asks `average_cost_at` and inventory/models.py; the shelf check follows calls |
| b7f125c | O46 | `unlocked_open_runs` sees a status compared to RELEASED |
| 2aaf77a | O212 | `reported_without_the_period` reads names from the code, not words from the text |
| 034cb15 | O136 | A unique constraint on `Opportunity.sales_order`; the migration reports clashes and merges nothing |
| 9e439d1 | O229 | The lock-order comment matches LOCK_ORDER |
| 56d4b12 | O25 | RecordScreen's afterCreate comment matches the code |
| 01f8a8a | O132 | IMPORT.md lists no roles column; the leave screen offers Cancel to whoever decides the leave |
| 1871412 | O32 (follow-up) | `Employee.terminate` cancels the leave after the leaving date on the leave's own first day |
| 5bf7ddb | O228 | The sentinel lets go of rows a rolled-back savepoint took, and ranks raw SQL FOR SHARE |

Not done: O226 (see "What I could not do").

## Each fix, and the test that failed without it

### People's money

- **O213.** `ATakeBackOutsideAFinalRunTests` (apps/manufacturing/tests_piecework.py) failed with "Lists differ: [('earning', Decimal('3000.0000'), Decimal('1400.00'))] != [(ComponentKind.DEDUCTION, ...".
  - Scenario, a loom weaver: 4,000 m earns 1,800.00 and 400.00 is taken back. PF is now 216.00 and ESI 14, both on 1,800, and the ECR and ESI files both read 1,800. Before the fix the line was netted to 1,400, with PF 168.00 and ESI 11.
  - **Decision for the integrator.** Outside a final run the take-back still comes only off the period's piece work (O31's rule). It is now also held within `DEDUCTION_CAP`, the cap advance recoveries already use on every run, and what the cap leaves waits for the next run. Without the cap, PF on the full earning plus a full take-back would put the slip below nothing, and the run would be refused.
  - **A test rewritten because it encoded the defect.** `test_more_taken_back_than_earned_waits_for_the_next_run` asserted the netted line (800 m, 470.00). It now reads 1,350.00 earned, 675.00 taken back, and 205.00 taken in the next run. That run's net for the weaver goes from 470.00 to 675.00.
- **O215.** New: `hr.ShortfallSettlement` (apps/hr/shortfalls.py), migration hr 0028, the API at /api/hr/shortfall-settlements/ with void/, and the screen "Owed back by former employees". The Controller has add, view, write-off and void.
  - A receipt is a posted payment in, checked as an advance's payment out is. It must come from the former employee's party. It must be booked against the account the run owed the shortfall into, read off the run's entry line. Currency and amount must match, and it must be dated neither before the pay date nor ahead.
  - A write-off needs a reason and `hr.write_off_shortfall`. A Payroll Officer gets 403. It is charged to the company's bad-debt account, and that account is kept on the record.
  - What is still owed is worked out, not stored. Over-settling is refused while the run and then the person are held.
  - Reverses: voiding a receipt voids its payment; a payment voided on its own (a bounced cheque) makes the amount owed again; voiding a write-off reverses its entry. `PayRun.void` is refused while a settlement stands.
  - Failed without the PayRun.void guard: `AFormerEmployeesShortfallIsSettledTests.test_a_bounced_receipt_is_owed_again_and_the_run_stands_while_a_settlement_does`, "AssertionError: ValidationError not raised".
- **O32.** The original is the leave's first day.
  - Failed without the fix: `ApprovalHasAReverseTests.test_a_cancel_dated_before_the_leave_gives_back_nothing_already_taken`, "ValidationError not raised".
  - Two tests that leaned on the old reading were adjusted. One cancelled on a day still to come and now runs with the plant's day patched to that day. The other reached the posted-run refusal by cancelling before the leave, which is the O32 shape itself; it is now dated on the leave's day.
  - Follow-up 1871412: `Employee.terminate(cancel_future=True)` passed the leaving date, a day before the leave, and was refused. It now dates the cancel on the leave's own first day, which keeps the old result: a whole cancellation, with the same paid-run check. Failed: `TerminationDealsWithWhatComesAfterTests.test_terminate_can_cancel_it_and_says_so`. I first missed it because my O32 run did not cover the whole of hr.
- **O176.** Failed without the fix: `ClaimTests.test_a_payment_is_not_dated_on_a_day_still_to_come` (it also drives the API), "ValidationError not raised". `hr.ExpenseClaim.pay` is off DATED_ELSEWHERE.
- **O197.** Failed without the fix: `UsersApiTests.test_a_login_refused_its_roles_is_not_made`, "True is not false".
  - The audit check `writes_around_the_mixin` now reads a viewset whose model is not ours (here `auth.User`) by its module. `UserViewSet.perform_destroy`, which only refuses, is excepted with that reason. Planted instance: `test_a_viewset_of_a_model_not_ours_is_read_by_its_module`.

### The rule that dates corrections (O15)

Nine steps are on the rule now: Payment.void, BankStatementLine.reverse_posting, StockAdjustment.void, CustomerTds.reverse, TdsDeduction.reverse, WorkOrder.reopen, MaterialIssue.void, ProductionEntry.void and TimeBooking.void. In each, the original is the date of the entry it reverses.

- Failed with the manufacturing four reverted: `EveryCorrectionAsksTheRuleTests` (apps/core/tests_audit_dates.py), "correction dated anywhere ... manufacturing.WorkOrder.reopen".
- The four manufacturing steps and the inventory one sit in fix_stores' modules; each edit there is one line.
- Payment.void's refusal now reads "A payment is not voided on <day>: it was made on <day>". One assertion in tests_reconciliation asks the new wording.

Three steps stay on DATED_ELSEWHERE. Each is a question, not a fix:
- `accounting.RecurringJournal.generate_one` posts a schedule's run forward on its own day and corrects nothing.
- `sales.RecurringInvoice.generate_one`: the same.
- `accounting.RealisedOnItsOwnDay.release_exchange_difference` is dated by its caller, Payment.void (which now asks the rule), or on the realised day when that is later.

### Core

- **O130.** Failed without the fix: `ARowWithNoPartyStaysInViewTests` (apps/core/tests_party_scope.py), "Lists differ: ['1 Mill Road'] != ['1 Mill Road', 'Plot 4, Uluberia']".
- **O136.** `OneOpportunityAnOrderMigrationTests` (apps/sales/tests_crm.py, tagged migration, MigrationExecutor in a TransactionTestCase) passes. With the migration's refusal step removed (in a scratch worktree, since deleted), it failed with a bare "IntegrityError: UNIQUE constraint failed: sales_opportunity.sales_order_id" that names nothing.

### The audit's blind spots

| Row | Test | Failed on the old check with |
|---|---|---|
| O246 | `TheAuditSeesTheUtcDayOfAnyMomentTests` | "Lists differ: [] != ['gst/example.py:3']" |
| O40 | `TheAuditFollowsTheShelfAndTheRateTests` | "[] != ['inventory/models.py:3']" and "[] != ['sales/deliveries.py:Delivery.post']" |
| O46 | `TheAuditSeesAReleasedRunAskedUnheldTests` | "[] != ['manufacturing/example.py:3']" |
| O212 | `TheAuditReadsCodeNotWordsTests` | "Lists differ: [\"manufacturing.ProcessRoll is read by a G...\"] != []" |

### Locks and words

- **O228.** Failed without the fix, in `TheSentinelForgetsASavepointRolledBackAndSeesForShareTests` (apps/core/tests_lock_order.py):
  - the savepoint test with "LockOrderViolation: sales.salesorder 7 locked after sales.invoice 3";
  - the FOR SHARE test with "cannot import name 'raw_shared_lock'".
  - Shared rows are ranked against other models, but not held to key order among rows of one model, since two shares never wait for each other (a transfer from bank 5 to bank 3).
- **O229, O25.** These are comments, so no test reads them.
- **O132.** Failed without the fix: `LeaveInTheBrowserTests.test_the_manager_who_decides_it_is_offered_cancel`, "TimeoutError: Locator.click" on Cancel it.

## Runs on the tip (5bf7ddb or its parent)

- **fastsettings:** core and hr, 894 tests OK. Accounting, sales and inventory, 1,786 OK. Manufacturing, 1,756 OK.
  - Earlier, purchasing (run for O15) had one failure that is already on the base (defect 1).
- **apps.web, with Playwright on a fresh build:** every module passes except `tests_browser_sweep`. The sweep walks every role through every screen and cannot finish in the 10-minute foreground limit here.
  - In its place I ran a temporary, uncommitted subclass that sweeps payroll for the Controller, Line Manager and Payroll Officer. It passed, the Controller opened /app/payroll/owed-back, and no request failed. The full sweep is for the gate's ui lane.
- **Races on PostgreSQL:** all seven race modules in apps/e2e, 134 tests, OK (with a database of my own, flushed).
  - An earlier attempt showed four errors. They came from rows left by two of my own runs that the time limit killed mid-test. They were not in the code.
- **Migration tag:**
  - Before O228: all seven of sales' (with 0077 in place) and hr's one (with 0028) passed.
  - After O228's sentinel change: 19 of the 22 passed.
  - Not run after O228: `core.tests_numbering_years.CountersCarryAcrossTests`, which runs past 10 minutes even alone. Also sales' reps and tolerance tests, which passed before O228 but were not rerun after it.
- **Before every commit:** `audit_invariants` showed no findings and `makemigrations --check` showed no changes.
- **Not run:** the Kolkata lane.

## What I could not do

**O226** (nine lock shapes the sentinel is told not to see, the UNSEEN list). Each shape needs its writes in key order and a PostgreSQL race() test. Eight of the nine are in manufacturing, assets and inventory, which fix_stores is editing now. I left UNSEEN unchanged. Writing a reason there would claim a shape cannot be fixed, and that is not so. Plan per shape:

- **Shape A, document numbering (core.documentsequence, documentsequenceyear).**
  - Problem: the order between sequence rows is by document type, and the pk says nothing about that.
  - Fix: give the journal entry's sequence the last place, since every post numbers its document and then its entry. Have the sentinel rank sequence rows by that. Then look for any path that numbers a document after an entry.
- **Shape B, a walk that writes one model's rows in an order of its own** (woven bag BOMs and plans, roll_up's standards, costing.publish's items, depreciate_all's assets, dispatch commit's operations).
  - Fix: hold every row it will write first, in key order, with `lock_rows(*rows)`. Then walk in the order the walk needs. The sentinel skips rows already held.
  - Then take each model off UNSEEN, and add a race() test of two walks over the same rows.
- **Shape C, a void that reverses its children in the order taken** (WorkOrder.void's entries, a roll mount's issues, the station clock's bookings).
  - Fix: the same as shape B. Hold the children, or their entries, in key order before reversing.
  - journalentry is also UNRANKED and written by every posting, so take it off UNSEEN last, after a run of every app.

## Defects seen and not fixed

1. **Base, purchasing (test vs. message).** `apps/purchasing/tests_tds.py:825`, `NoConfirmedRateRefusesThePostingTests.test_a_taxed_bill_waits_for_the_accountant_and_an_untaxed_one_posts`.
   - The test expects "The accountant confirms it under TDS rates", but the message says "under Settings > TDS rates".
   - The wording came from integration commit f2fb5de. The test fails on 49459e6 without my edits.
2. **Inventory (race).** `apps/inventory/adjustments.py`, `StockCount.post`, lines 745 and 777. It asks each line's `current_system_quantity()` before `adjustment.post()` holds the shelves.
   - Scenario: books 10, counted 9. A delivery of 2 commits between the stale check and the lock, and the count posts -1 against a shelf the stale check never saw move. The rule is to refuse a sheet once stock has moved.
   - Found by the widened O40 check. Named in READ_BEFORE_LOCK_ON_PURPOSE as reported.
3. **Purchasing (books).** `apps/purchasing/models.py:7578`, `GoodsReceipt._restore_components`. Its pre-consumption branch books a component back at `average_cost_at` (four places) times the quantity.
   - Scenario: 4,000 kg at an average of 12.34567 is booked at 12.3457 × 4,000 = 49,382.80 against 49,382.68.
   - Named in PER_UNIT_RATE_ALLOWED as reported.
4. **Purchasing (dead code).** `apps/purchasing/models.py:2930`, `PurchaseOrderLine.component_cost`, is called by nothing. If it is ever used to post, it rounds quantity times a four-place rate per component.
5. **Manufacturing (race).** `apps/manufacturing/orders.py:2049`, `WorkOrderOperation._check_only_the_assignment_moved`, reads the order's status as RELEASED without holding the order. A machine change saved while the order closes is let through onto a closed run.
   - Found by the widened O46 check. Named in OPEN_RUN_READ_ELSEWHERE as reported.
6. **Imports (words).** `apps/imports/api.py:25` describes the employees file as "login and roles", but the import refuses a roles cell. This is the other half of O132, outside my modules.
7. **hr (O215 coverage).** A leaver's advance left unrecovered stays a receivable on the advance (advances.py). The new settlement settles only the final slip's `recoverable`; an advance's remainder still has no receipt or write-off.
8. **hr (O213 decision).** See O213 above: the deduction cap now applies to take-backs on every run. If the accountant reads the cap differently for overpayments, `_within_the_deduction_cap` is the one place to change.

## Review fixes (after review_core)

On top of 5bf7ddb in the fix_core worktree. I cherry-picked the review commit first (656ccdc, its probes), then made eight commits. The tip is 7098b96, the tree is clean, and nothing is pushed. Every commit ends `Gate: {GATE}` with the two trailers.

### Commits

| Commit | Row, finding | What | Failed without the fix |
|---|---|---|---|
| f5e8e41 | O130, review F1 (blocker) | One rule for reading and one for writing party-scoped rows. A rep reads the plant's own records and writes none of them. | `ThePlantsOwnRecordsAreKeptByWhoKeepsThemTests` (apps/sales/tests_reps.py): "Tuples differ: (True, 'Plot 5', None) != (False, 'Plot 4, Uluberia', 1)". The review's three probes now pass. |
| 223ea68 | O32, review F2 | A cancel gives back only the days not yet taken on the cancel's day. | The review's two CancelDatingTests: "ValidationError not raised". |
| 79e10c2 | O46, review F3 | A status compared with WorkOrderStatus.RELEASED is asked of any receiver. | The review's OpenRuns probe; planted instance and near-misses in `TheAuditSeesARunUnderAnyNameTests`. |
| 3a48220 | O246, review F4 | `is_aware(x)` excuses only a `.date()` of something that names x. | The review's Greenwich probe; planted instance and near-miss in `AnAwarenessQuestionExcusesOnlyItsOwnMomentTests`. |
| db67405 | O228, review F5 | The sentinel sees a share upgraded to an update, and a table named with its schema. | The review's two RawShareTests; planted instances and near-misses in `TheSentinelSeesAShareUpgradedAndASchemaNamedTableTests`. |
| fda9d7b | O253 | A component put back by a return of a receipt from before consumption was recorded comes back at its average's eight places. | `AReturnFromBeforeConsumptionWasRecordedFootsTests`: "(-15000.15, -15000.15, …) != (-15000, -15000, 0)". |
| de49e7b | O252 | A stock count holds its shelves before the stale check. | On PostgreSQL, `StockAndPlantRaceTests.test_a_count_is_not_posted_over_a_movement_that_landed_after_its_check`: "(['done', 'done'], 5, 4)". |
| 7098b96 | O254, and O226's dispatch shape | An operation holds its run before its status decides. Dispatch commit holds runs, then operations, in key order. | `AnOperationHoldsItsRunTests`: "ValidationError not raised". On PostgreSQL, `test_two_dispatch_commits_of_the_same_runs_in_opposite_orders_both_go_through`: "refused: deadlock detected". |

### Details per commit

- **f5e8e41, O130, the blocker.**
  - `scoped(write=True)` reaches only a limited login's own parties' rows. Every party-scoped action except list, retrieve and metadata reads through it.
  - `refuse_write(user, party)` is the one rule for every write. `party=None` means the plant's own record, and only a login the scope does not limit may write it.
  - `_check_party` asks it of the row as it stands and of the party it is given; `perform_destroy` asks it too.
  - Over the API, the Sales Rep reads the plant's address and is refused all three writes: make 400, change 404, move to no party 400. The AR Manager is allowed all three: 201, 200, 200.
- **223ea68, O32.**
  - The cancel's day is today, or the first day of a leave not yet begun.
  - `Employee.terminate` passes the leaving day with `leaving=True`, so the same rule applies with the leaving day as the cancel's day.
  - Two tests that asserted the old wording were adjusted. `test_unpaid_leave_already_docked_is_not_sent_back_or_cancelled` is now refused on the day rule, before the posted-run check: a past day is taken. That check is no longer reachable by cancelling a past leave.
- **79e10c2, O46.**
  - A bare RELEASED still needs a receiver named order or run, so a lot's `ReleaseStatus.RELEASED` is not taken for a run.
  - Widening it surfaced the order's own `close()`, which is `@serialised`, and `is_open()`. A `@serialised` method or `is_open` itself now counts as holding self.
- **3a48220, O246.** `to_date()` and a shift's day still pass, and the tree has no finding.
- **db67405, O228.**
  - (a) A share then an update of the same row is now a LockOrderViolation naming the upgrade.
  - (b) `"public"."sales_invoice"` is read as sales_invoice.
  - (c) Not done. `accounting.account` stays unranked. Its place against the documents that hold money needs a survey of every pair it is taken in, and the share is PostgreSQL-only. A guessed rank would refuse paths that cannot deadlock. The survey can be run with `LOCK_ORDER_SURVEY` over the PostgreSQL lanes.
- **fda9d7b, O253.**
  - Scenario: 3,001 frames at 8.12345 at the subcontractor, 3,000 assemblies, the consumption made to read as from before it was recorded, then all 3,000 returned.
  - Before: frames came back at 8.1234 (half-even), 24,370.20 of 24,370.35. The shelf and the ledger both moved −15,000.15, and 0.15 was booked to price variance that nobody paid.
  - Now: −15,000 each, and nothing to variance. The name is off PER_UNIT_RATE_ALLOWED. Figures: calc_fix_core/o253.py.
- **de49e7b, O252.** Unfixed, both posted and the count's movement landed after the other's. The name is off READ_BEFORE_LOCK_ON_PURPOSE, which is empty again.
- **7098b96, O254 and the dispatch shape.**
  - The operation holds its run and reads its stored status without re-reading the object in hand. Re-reading clobbered a release's unsaved routing.
  - `manufacturing.workorderoperation` is off UNSEEN, and the manufacturing suite (1,783 tests) takes operation writes in order.
  - A race of an operation's change against its run's close ends in the same rows whichever goes first. So O254 is proved with a run closed under an operation in hand, not with race().

### Not done

- **O226, the other eight shapes.** These are document numbering (core.documentsequence and documentsequenceyear), WorkOrder.void's entries, depreciate_all, costing.publish, the woven bag's BOMs and plans, a roll mount's void, roll_up and the station clock's void. They are out of budget. The plan is the one in "What I could not do" above; the dispatch shape shows the pattern (hold in key order first, then write in walk order, then the race test).
- **O40's cross-module call-following.** Not built. A sound version needs a call graph resolved across modules: which `post` or `reverse` a call means. Matching by bare name, as the review's scratch closure does, flags `StockMovement._check_on_hand` and seven others, some of them name collisions. The review's eight (U1) are worth a reading by the owner of inventory and stores; I have not proved any of them.
- **Migration tag after this round's sentinel edits.** The edits are the upgrade and schema rules, which are PostgreSQL-only, and the operation off UNSEEN. Manufacturing's four migration tests did not finish within a 10-minute call on this machine, with four lanes under load. They are the gate's mig lane. After O228's first commit, 19 of the 22 had passed.
- **The full browser sweep.** As before, it runs past the 10-minute limit.

### Runs on the tip, all fastsettings except where noted

- core, hr and accounting: 1,260 tests OK.
- sales, inventory and purchasing: 2,739 tests, one failure: the known base failure at `tests_tds.py:825` (wording from f2fb5de).
- manufacturing: 1,783 OK, on the final orders.py and dispatch.py.
- apps.web with Playwright, every module except the full sweep: 180 tests, one failure.
  - It was `tests_browser_review_purch.RatesAndTcsInTheBrowserTests.test_01_the_ap_manager_reads_a_pending_rate_and_cannot_confirm_the_controller_can`. It passes alone.
  - Line 34 reads `confirmed_on` from the database right after "Confirmed" shows, and under four workers it read None. A test the machine's speed can decide is a defect, not mine.
- PostgreSQL: every race module (including the review's and the two new ones) plus the sentinel tests (including RealShareTests): 176 OK.
- `audit_invariants` showed no findings and `makemigrations --check` no changes before each commit.

### Defects seen this round and not fixed

1. **apps/web/tests_browser_review_purch.py:34.** After clicking Confirm it waits for "Confirmed" on the page, then reads `pending.confirmed_on`, which was None under parallel load. The text the test waits for may show before the write commits, or may match something else on the page. It is purchasing review's test.
2. **Review U2, sales/models.py:3744 → 2096.** `undo_what_it_settled(payment, on_date)` hands the void's day to `withdraw_unearned_discount` with no rule. A discount taken on 20 January and voided on 10 January would date the withdrawal before the discount. I did not probe it, and it is sales'.
