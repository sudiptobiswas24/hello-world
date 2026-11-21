# review_core (g22): an adversarial, read-only review of fix_core, 49459e6..5bf7ddb

- **Worktree:** /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/review_core (detached at 5bf7ddb). A second, base worktree at 49459e6 (review_core_base) was used only to tell a regression from an old defect. No product code was edited; nothing is pushed.
- **Probes (committed, unpushed, in review_core):** apps/manufacturing/tests_review_core_pay.py, apps/sales/tests_review_core_scope.py, apps/sales/tests_review_core_migration.py, apps/hr/tests_review_core_leave.py, apps/accounting/tests_review_core_dates.py, apps/core/tests_review_core_audit.py, apps/core/tests_review_core_sentinel.py, apps/e2e/tests_review_core_races.py.
- **Hand figures:** scratchpad/calc_review_core/o213.py (plain Python, independent of the Django code). Scratch cross-module scan: scratchpad/calc_review_core/crossmodule.py.
- **Known, not re-found:** O226 not done; StockCount.post, GoodsReceipt._restore_components and WorkOrderOperation._check_only_the_assignment_moved.

## Verdict

One blocker (finding 1, O130: a rep can now change the plant's own address). The money paths the brief put first (O213, O215, O15's Payment.void, O228's savepoint) held under every probe I could build: no finding on any of them. The rest are low and non-blocking, in the audit's blind spots and in a model method no caller reaches yet.

## audit_invariants and makemigrations

- `python manage.py audit_invariants` on the tip prints `No invariant findings.` (exit 0).
- `python manage.py makemigrations --check --dry-run` prints `No changes detected` (exit 0).
- The new migrations name what the commits say: hr 0028 creates ShortfallSettlement only; sales 0077 adds the one partial unique constraint on Opportunity.sales_order.

## Findings

### 1. BLOCKS. O130: a rep may now change the plant's own address (and every party-less row)

- **Where:** apps/core/scoping.py:87 (`scoped()` now ORs in `path__isnull=True`) with apps/core/views.py:280 (`PartyScopedMixin._check_party` returns early when the party is None, so the refusal "Only your own customers can be changed from here." is never asked).
- **Scenario:** rep-a (Sales Rep, carries Acme) sends `PATCH /api/core/addresses/<plant>/ {"line1": "Anywhere"}` for the plant's own address (party NULL, "Plot 4, Uluberia"). Tip: 200 and the address is changed. Base 49459e6: 404, the row was out of sight, so out of reach. The Sales Rep role holds `core.change_address`.
- **Why it blocks:** O130 meant to let a rep read the rows that name no party. It also gave the lowest-privilege login write access to them. The plant's address is the plant's, on its own documents.
- **Probe:** apps/sales/tests_review_core_scope.py `test_a_rep_may_not_change_the_plants_own_address`: FAIL on the tip (200, line1 now "Anywhere"), PASS on the base.
- **Two old defects the same probe file shows (fail on the base too, not caused by fix_core, but O130 makes them worse):** a rep may create an address naming no party (201), and may detach their own customer's address from the customer (party set to null). Both leave a row every rep now reads, and the first is one every rep can then edit.
- **Direction:** a limited login may not write to, or move, a row that names no party: ask `refuse_change` of the party-less case in `_check_party`, as it is asked of a party. A limited login is refused by `SalesScope.refuse_change` for a party-less row today only because the early `return` skips it.

### 2. Low. O32 is closed only for "before the first day"

- **Where:** apps/hr/models.py:1006, `LeaveRequest.cancel`.
- **Scenario:** a leave taken 28 to 24 days ago. `cancel(by=person)` is refused ("ended ... cannot be cancelled"). `cancel(on_date=<its first day>, by=person)` gives all five days back, days already gone with them. The same for a leave under way, cancelled on its first day. The docstring promises "never before the leave began, which would give back days already taken".
- **Reach:** none over the API today: apps/hr/views.py:209 passes no `on_date`. `Employee.terminate` passes the first day on purpose (hr/models.py:339). So low, but the first caller that passes a date inherits it.
- **Probes:** apps/hr/tests_review_core_leave.py `test_a_leave_wholly_past_cannot_be_given_back_by_dating_the_cancel_on_its_first_day` and `test_a_leave_under_way_cannot_be_wholly_given_back_by_dating_the_cancel_on_its_first_day`: FAIL on the tip. The other three (not begun: default and first day cancel whole; a day before it or a later day to come is refused; under way is cut short, days taken stay) PASS.

### 3. Low. O46: the check is blind to a work order held under another name

- **Where:** apps/core/management/commands/audit_invariants.py:358, `RUN_OR_ORDER = (order|run)$`.
- **Scenario:** `if wo.status == WorkOrderStatus.RELEASED:` in a function that does not hold `wo` is not reported; neither are `job`, `jwo` or `o`. A scan of the live tree finds none today (only `self.status` at manufacturing/orders.py:731 and :1414, both on the order itself), so no instance is hidden now.
- **Probe:** apps/core/tests_review_core_audit.py `OpenRuns.test_released_asked_of_an_order_kept_under_another_name_is_reported`: FAIL. The planted forms that end in order or run (`order`, `run`, `self.work_order`, `op.work_order`, `plan.production_run`, and a tuple with RELEASED) are all reported, and the same with `lock_rows(order)` is not: PASS.

### 4. Low. O246: one `is_aware(` excuses every `.date()` in the function

- **Where:** apps/core/management/commands/audit_invariants.py:1113.
- **Scenario:** `def f(a, b): if is_aware(a): a = localtime(a); return a.date(), b.date()` reports nothing; `b.date()` is the UTC day of a moment never asked about.
- **Probe:** `Greenwich.test_an_is_aware_question_about_one_moment_does_not_excuse_the_date_of_another`: FAIL. Six planted spellings (astimezone().date(), fromtimestamp(tz=utc), attribute moment, getattr moment, module level parse_datetime, `(a or b)`) are all reported and four near-misses (localtime, strptime, make_naive, a docstring) are not: PASS.

### 5. Low. O228: three things the sentinel still does not see about FOR SHARE

- **(a)** A row shared and then taken FOR UPDATE reads as "already held" (lock_order.py `taken`: `(label, pk) in state["held"]`), so a share-to-update upgrade, the shape that deadlocks two payments through one bank against an unmarking, is invisible. Probe `RawShareTests.test_a_share_then_an_update_of_the_same_account_row_...`: FAIL. Before O228 it was invisible too; nothing regressed.
- **(b)** A table written by schema, `"public"."sales_invoice"`, is not ranked (silently). Probe: FAIL. `hold_money_account` quotes the bare table name, so nothing reaches it today.
- **(c)** `accounting.account` is UNRANKED (lock_order.py:95), so the one raw share in the code, `hold_money_account`, is recorded but compared with nothing. The ranking has teeth on ranked models only (proved with `sales_invoice` shared, then `sales.salesorder`). On PostgreSQL `RealShareTests` proves the real call is seen with its key: PASS.
- **Held up under probe, so the false positive is gone and a real inversion is still caught:** a row taken in a savepoint that rolled back is let go; the same twice over; a savepoint inside a savepoint keeps the outer's rows; a savepoint released and a block with `savepoint=False` keep what they took; an inversion against a row held before the savepoint is still refused after it rolled back; five spellings of raw FOR SHARE / FOR KEY SHARE (quoted, unquoted, lower case, `IN (...)`, `NOWAIT`) and `ANY(%s)` are ranked: all PASS.

## Unproven (no probe fails; do not treat as findings)

- **U1. O40's call-following is per module.** A scratch closure across all modules (crossmodule.py) flags eight functions the shipped check does not: `StockMovement._check_on_hand`, `StockMovement._check_tracking`, `StockTransferStep.reverse` (inventory/transfers.py:554, reads `on_hand_at` at 569), `ReorderRule.projected`, `GoodsReceipt._consume_components` (purchasing/models.py:7616, reads at 7634; the mirror of the known `_restore_components`), `_issue_roll` (manufacturing/process_rolls.py:198, reads at 201 and issues that quantity), `Inspection._sampling_for`, `planning/mrp.opening_balance`. Name collisions make some of these false, and a caller above may hold the shelf. I proved no race. They are worth one look by whoever owns inventory and stores.
- **U2. O15, Payment.void's original is the payment's date only.** `undo_what_it_settled(payment, on_date)` (sales/models.py:3744) hands the void's day to `withdraw_unearned_discount` (sales/models.py:2096), which takes it with no rule; a payment dated 5 January, allocated and its discount taken on 20 January, then voided on 10 January, would date the withdrawal before the discount. The FX release is guarded (`max(on_date, fx_entry.date)`); this one is not. Not probed.
- **U3. O176 and O197.** Read, not probed beyond the author's tests. I found nothing wrong in the reading: the pay date goes through `correction_date` then is refused past today; the login create and update now run inside the mixin's transaction, and the stamps are dropped in the serializer.
- **U4. O213 and advances.** The take-back is now taken first inside the cap, so an advance instalment on the same run gets what is left of the room and may wait. Not probed.

## What held, claim by claim (PASS/FAIL)

Hand figures are in o213.py. All pass on the tip unless marked.

### 1. O213, a take-back outside a final run (apps/manufacturing/tests_review_core_pay.py, 26 tests, all PASS)

Weaver on 0.45 a metre, PF 12% of piece work, ESI 0.75% up to the rupee, 400.00 to take back, deductions at most half the earning.
- PASS. 1,500 m (675.00): PF 81.00, ESI 6, take-back 250.50 (half is 337.50, less 87.00), net 337.50, 149.50 waits.
- PASS. The next run (2,000 m, 900.00): PF 108.00, ESI 7, takes the 149.50, net 635.50. The one after (1,000 m) is a plain 450.00, PF 54.00, ESI 4, net 392.00. Wages end at 2,025.00, which is 675 + 900 + 450: the 400.00 is taken back whole.
- PASS. A run voided after it took part (250.50) is calculated again to the same lines and the same wages (400.00 stands meanwhile).
- PASS. A run voided after it took all (149.50) leaves wages at 824.50 and takes 149.50 again when calculated again.
- PASS. The earlier run voided under the later one: the next run pays 874.50 by difference (675 + 900 + 450 earned, less 400.00 and 750.50 paid) and wages end at 2,025.00.
- PASS. A leaver after a partial take-back: 100 m (45.00), PF 5.40, ESI 1, half is 22.50 so 16.10 is taken and 133.40 is owed, net 22.50. Paid less owed is 720.00, which is what was earned (675 + 45), and the receivable, by party and in all, is 133.40.
- PASS. A leaver who wove nothing owes all 400.00; PF, ESI and net are zero; the receivable by party is 400.00.
- PASS. A final run voided and calculated again gives the same 133.40 and the receivable clears on the void.
- PASS. A salaried employee (10,000 fixed, PF 1,200.00, ESI 75, net 8,725.00) and an hourly shift worker (120 h at 100, PF 1,440.00, ESI 90, net 10,470.00) are unmoved on an ordinary run and a final one, `recoverable` zero.
- The ECR and ESI files read 1,800 for the 4,000 m case (the author's own test passes).

### 2. O215, a leaver's shortfall settled (12 probes, all PASS)

- PASS. Receipt 150.00 then write-off 250.00 through the API as the Controller: owed 0.00; the receivable is 0.00 on the account and by the weaver's party; bad debts 250.00; bank 150.00.
- PASS. A write-off of 400.00 alone clears it.
- PASS. Voids in either order (receipt first, with the write-off standing: owed 150.00; then the write-off: owed 400.00) put it all back and bank to 0.00; then the run may be voided and the receivable goes with it.
- PASS. More than is owed is refused each way (400.01, then 250.01 after a 150.00 receipt, then 0.01 after the lot).
- PASS. A write-off with no reason, "", "   " or null is 400. Only the Controller holds write-off, void and view of settlements (no other role holds any of the three).
- PASS. A write-off is dated by the rule: 29 September (before the pay date) and tomorrow refused, 30 September accepted; a closed period refuses it with the owed amount unchanged; its void is refused on 29 September and tomorrow, accepted on the day. A receipt voided on a day before its payment is refused and the receipt stands.
- PASS. One payment cannot settle twice (400, not a crash). A payment voided on its own, then a second receipt: owed again, then cleared.

### 3. O15, correction dates

- PASS (SQLite). Payment.void: a day before the payment refused, tomorrow refused, a closed period refused, each writing nothing and leaving the payment standing; the payment's own day accepted and carried by the reversal; the default is today. These error at fixture time on PostgreSQL (a payment created inside the TestCase's own block is refused by the lock sentinel), so run them on SQLite.
- PASS (read). The three left excepted have true reasons: both `generate_one`s date a forward posting like any hand-typed one and post into an open period only (a closed one is refused at post); the exchange release is dated by `undo_what_it_settled(payment, on_date)` after Payment.void's rule, and by the day it was realised if later. See U2 for the one dependent step the rule does not reach.
- The other eight steps were read (diff daab6c1); each takes the date of the entry it reverses. `getattr(self.journal_entry, "date", None)` makes the original None for a document with no entry, which skips the "before" refusal; only the entry is dated by it, so no harm found.

### 4. O32, O176, O197: see findings 2 and U3.

### 5. O130, scoped(): see finding 1. PASS: a rep reads the plant's address and their own customers' only; the AR Manager reads all three; another rep's customer's address by id is 404; a rep's delete of the plant's address is 403 (no delete permission).

### 6. O136, one opportunity an order (apps/sales/tests_review_core_migration.py)

PASS (SQLite, real migrations, 8m13s; MigrationExecutor in a TransactionTestCase tagged migration, so not under fastsettings). Three opportunities on one order and two on another: the migration refuses with a RuntimeError that names both orders and all five opportunities (alpha, bravo, charlie, delta, echo) and neither the lone one (foxtrot) nor the two with no order (golf, hotel). Nothing is merged (the three still stand on the first order, the two are still NULL), and the recorded state is still 0076 (not half applied). After all but one of each are unlinked it applies; putting a second opportunity back on an order then raises IntegrityError; reversed, the constraint is gone and the clash may stand again. Not run: PostgreSQL (the author's own test and this one take minutes there).

### 7. The audit checks (apps/core/tests_review_core_audit.py, 15 probes: 13 PASS, 2 FAIL as findings 3 and 4)

- PASS. O40: a read two calls before the lock is reported; a read made by a helper before the lock is reported; a lock then a read, and a helper that locks then reads, are not; `lock_positions` with a keyword is reported; the rate asked in any function but the named one of the same file is reported, and `removal_unit_cost` is not.
- PASS. O212: a comment and a string name no model; an attribute or a name does.
- O46 and O246: see findings 3 and 4.

### 8. Races on PostgreSQL (postgres://erp:erp@localhost:5432/review_core; test_review_core dropped first), apps/e2e/tests_review_core_races.py, 5 PASS

- PASS. A receipt of 300.00 and a write-off of 300.00 at once on 400.00 owed: exactly one stands, 100.00 is left, the receivable by party agrees.
- PASS. Two write-offs of 250.00 at once: one stands. Two receipts of 250.00 at once: one stands.
- PASS. A write-off and the voiding of the run that made the shortfall at once: not both go through.
- PASS. A leave cancelled as the run posts: never a posted run with the leave cancelled.
