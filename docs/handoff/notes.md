# Notes carried over from the session of 8 October 2026

Side findings agents saw but did not fix, decisions made, and gate lessons. Paths like $SP/... were that session's scratch folder and no longer exist.

## Seen by agents, not fixed (for the backlog)
- Account.currency is read by nothing (inert field) — fix_ui report.
- Unmarking Account.holds_money can race a payment post through it (no lock on the mark's check) — needs race() proof.
- RecordScreen afterCreate comment says it also decides where to go after delete; code doesn't.
- Nine viewsets don't use AuditableViewSetMixin and stay unlocked on edit: countries, party tags, saved filters, notes, follow-ups, attachments, GST e-invoice / e-way bill / GSTR-2B deletes.
- Admin edits read without a lock.
- Punch-file import reads an existing attendance day before locking it (lost-update shape).
- Sales line delete on an approved order locks order before line; close-short locks line first (pre-existing lock-order inversion).
- Gate in another cloud session: its Asia/Kolkata lane skipped 154 (browser tests did not run) — always compare skip counts with the local lane (46) before trusting a remote lane.
## Stores fix agent (fix_store, on 2335abb) — seen, not touched
- Same-bill landed cost: Bill._record_landed_cost and _split_across_warehouses still land on the line's destination and always debit inventory (mirror of stores finding 6).
- Manufacturing material return (manufacturing/orders.py ~2306) goes back at issue rate × qty (check against fix_make's work).
- DeliveryAllocation.delete uses self.delivery, which does not exist (AttributeError).
- Adjustment post endpoint returns a stale total_value (lines prefetched before posting).
- Importer reads 8-place opening costs into a 4-place field.
- Cancelling a put-away transfer leaves its receipt route move standing.
- Explicit unit cost on a write-down books that cost, not what the shelf loses.
- Old landed-cost allocations made before the fix now release to COGS; old inventory differences stay.
- Partial returns across route steps take the earliest place first (agent's choice; may be a business decision).
- Needs PG: SalesRaceTests.test_two_orders_do_not_both_ship_the_last_of_the_shelf. Inventory migration 0037 (drops cached folds) has no migration test; nor has 0030.
- New audit_invariants checks: per_unit_withdrawal_rates, movements_written_around_save, shelf_read_before_lock.
## Payroll fix agent (fix_pay, on 2335abb, HEAD 3da2ebc) — open items
- Item 12, decided by me under the user's standing "decide, don't ask" (implement as a follow-up):
  - final pay too small to cover what is taken back: do not refuse the final run (it blocks everyone's pay); post it, and record the shortfall as a receivable from the former employee;
  - roll voided after the final slip was paid: same receivable; do not refuse the void in manufacturing (quality corrections must stay possible);
  - PF/ESI: what is taken back reduces the PF/ESI wages of the run it is taken in.
- S1 (ECR reads the PF ceiling live) still open.
- Editing a working pattern or holiday region restates posted slips' day counts (frozen-fact defect).
- Needs PG: StockAndPlantRaceTests.test_two_fortnights_posted_at_once_do_not_both_pay_the_piece_work. The employee lock in post() may also close the known run.post vs leave/attendance race — unproven.
- A run calculated with handed-in hours before hr 0020 is refused at post until recalculated (upgrade note).
- 17 reversal methods outside hr named as known exceptions to "unchecked reversal date"; codebase-wide home: JournalEntry.create_reversal. 7 deletable posted documents outside hr named in "deletable posted document" (quality.Inspection, 4 manufacturing, 2 inventory).
- New audit_invariants checks: unchecked reversal date, deletable posted document, posted as calculated. Expect merge conflicts in the audit command with fix_store/fix_make/fix_asset checks.
## Gate mechanics lessons (put in scripts/gate README)
- Never reuse (--keepdb) a PostgreSQL test database after a run was killed: it keeps the killed test's rows and the next run errors by the thousand. Every PG lane drops test_<db>{,_1.._4} first (cost a g15 and a g16 run).
- The full PG lane exceeds the 2-hour background limit when agents share the CPU: run it as two halves on separate databases (gate_pg_half.sh A: sales purchasing accounting gst core on erp; B: the other nine apps on erp2). The halves' Ran counts must sum to the SQLite lane's count.
- Run gates with no agents running tests if possible; agents' test runs at nice 19 still roughly doubled lane times.
## Fixed-asset fix agent (fix_asset, on 2335abb) — integration and open items
- INTEGRATION (g17): two agents built the same shared rule twice. fix_pay: reversal_day() in hr/models.py + check "unchecked reversal date"; fix_asset: correction_date()/day_that_has_come() in apps/core/models.py + check corrections_dated_without_the_rule (with DATED_ELSEWHERE exemptions). Keep the core one; move PayRun.void and ExpenseClaim.unpay onto it; drop hr's reversal_day and its check; then migrate the 17 exempted steps onto the core rule one module at a time (each removes its exemption — the check reports stale exemptions).
- Likewise reconcile the two "deletable posted document" style checks (fix_pay's check; fix_make may add one) into one.
- assets migration 0007 fills recorded accounts/method for existing assets; its migration test passed once on full settings.
- Decided (follow-up): refuse closing an accounting period while depreciation for it is uncharged (needs a period-close check registry in accounting, assets registering).
- Open: place_in_service accepts a future in-service date; admin lets status be edited directly; depreciated_before unenforced on hand-made drafts; dispose() reverses a closed-month charge on the disposal date (existing test asserts it — check whether intended); capitalising a bill line has no API action; register not footed against the ledger in health checks.
- Never edit a shell script while a background job is executing it: bash reads scripts as it runs, and the job then executes shifted lines (lost g16's PG half B result). Copy to a new file and edit that.
