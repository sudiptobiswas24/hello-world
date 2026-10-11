# fix_trade report (worktree scratchpad/fix_trade, on 88a8dd9)

## Commits (tip 5b7257f)
- da765b1 Every row lock is ranked against the written lock order under tests: O169, O175 (plus the sentinel and the four other paths it found)
- ad45168 A note corrects an original, by value and with its order line: O170, O171, O172
- 0cdf3a9 A drop-ship purchase order keeps the sales order it delivers to: O173
- 5b7257f Orders approved before sales 0063 are given the figures they hold: O174

Gate at the tip: full suite under the sentinel (fastsettings, --exclude-tag migration, --parallel 2): Ran 5726, OK (skipped=174).
audit_invariants: "No invariant findings." makemigrations --check: "No changes detected".
Migration test (normal settings): apps.sales.tests_seed_approved_figures OK. PostgreSQL (erp_fixtrade): TwoReturnsOnAConsolidatedInvoiceTests OK.
The four commits were split by hunk from one tree; only the tip was run, not each commit alone.

## 1. The sentinel (apps/core/lock_order.py)
- LOCK_ORDER is the written order as model labels; every model is its own rank, and rows of one model are taken in key order.
- settings.LOCK_ORDER_SENTINEL: on for `manage.py test` (config/settings.py) and in fastsettings; off in production; LOCK_ORDER_SENTINEL=0 in the environment turns it off, to watch a reverted race deadlock.
- Installed in CoreConfig.ready(): wraps SQLCompiler.execute_sql (any select_for_update: lock_rows, lock_in_turn, serialised, the rest) and reads the locked pks from the result; Atomic.__exit__ forgets at the end of the code's own outermost transaction (TestCase's atomics are ignored). A row already held, or made (post_save created) in the same transaction, is not checked. `unchecked(why)` switches it off for a block; used only by the demo loader.
- Tests: apps/core/tests_lock_order.py (planted reversals: rank, key order, plain select_for_update; the order and held rows pass).
- Survey (LOCK_ORDER_SURVEY=dir, record without raising) over the whole suite found 6 violations, all fixed in da765b1:
  1. Invoice.post held the invoice, then an older deposit (apply_deposit) -> Invoice/Bill.locked_before_it name older deposits/prepayments; apply_available_* take the rest in key order first (O175 D8).
  2. imports/cutover.py confirmed many orders in one transaction (party, then the next order) -> every order then every customer held first.
  3. purchasing receipt post of a drop-ship took the sales order after the receipt (and 4. after the delivery) -> GoodsReceipt.locked_before_it adds drop_ship_for.
  5. PurchaseOrderLine.reopen took the sales order after the line -> PurchaseOrderLine.locked_before_it adds the sales line's order.
  6. purchaseorderline before purchaseorder in _hold_with_its_orders: gone after 5 (not separately traced).
  Then in raise mode: a return debiting two bills locked the second new note after lines (fixed by the made-in-this-transaction rule); the demo loader (one transaction, many customers): unchecked, with the reason.
- Unranked models seen beside the ranked ones: 54 (accounting, hr, manufacturing, inventory, quality, assets...). Recorded with reasons in UNRANKED for the six that meet trading paths. Not ranked: see open defects.

## Fixes and the test that failed without each
- O169: Delivery/GoodsReceipt.locked_before_returning (every order of every invoice/bill billing its lines, then the drop-ship sales order), serialised(held_first="<method>"); invoices/bills locked in key order before the first note.
  - PostgreSQL, lock reverted, sentinel off: `AssertionError: Lists differ: ['refused: deadlock detected\nDETAIL: ...'] != ['done', 'done']` (TwoReturnsOnAConsolidatedInvoiceTests).
  - SQLite under the sentinel: test_a_return_on_the_second_order_of_a_consolidated_invoice: `LockOrderViolation: sales.salesorder 1 locked after sales.delivery 3`.
- O175 D8 (deposit): test_an_invoice_takes_the_older_deposit_it_draws_before_itself: `LockOrderViolation: sales.invoice 1 locked after sales.invoice 2`. Not raced on PostgreSQL.
- O175 D7 (line moved between draft orders): lock_for_change(instance, incoming) via core/audit.py get_object (as_it_would_be). test_a_line_moved_between_draft_orders_holds_both_orders_first: `500 != 200` (the API handler turned the violation into a 500). Not raced on PostgreSQL; driven as a superuser (the subject is lock order, not permission).
- O170: refuse_correcting_a_note (mixins) in both _check_kind and per line. test_a_note_against_a_credit_note_is_refused: `'is itself a note' not found in ''`; test_a_debit_note_against_a_debit_note_is_refused: `ValidationError not raised`.
- O171: refuse_correcting_past_what_it_holds(..., on=) checks value (unrounded, all posted notes on the line incl. claims). test_a_typed_note_at_ten_times_the_price_is_refused: `'1000.00 left to give back; this note gives back 10000.00' not found in ''`.
- O172: refuse_correcting_another_line(fill=True) takes the original's order line on save; posting refuses none/another (claims exempt). test_a_typed_note_line_takes_the_order_line_it_gives_back: `None != 1`; test_a_note_line_naming_no_order_line_does_not_post: `'this line names None' not found in ''`.
- O173: "drop_ship_for": CONFIRMED in PurchaseOrder.FROZEN_ONCE_MOVED. test_a_received_drop_ship_order_is_not_re_pointed: ValidationError not raised.
- O174: sales 0064 seeds approved_figures via SalesOrder.figures_as_kept() (shared with approve()); live model used only when there is an order to seed. MigrationExecutor test with the seed disabled: `AssertionError: None != {'discounts': {'1': '20.00'}, 'total': '800.00', 'margin': '95.00'}`.

## Not done
- O167 (purchasing approval-figure rule): not started, budget.
- O168: not addressed by design; the same read-before-lock shape is in the new locked_before_it (deposits) and locked_before_returning (orders read, then locked).
- The reviewer's probe files were not copied verbatim; their scenarios and numbers are in the tests above.

## Defects seen, not fixed (for docs/RISKS.md)
1. race: inventory.stockposition is unranked and is taken both ways round trading documents (survey pairs: stockposition before sales.salesorder, purchasing.purchaseorder, purchasing.goodsreceipt; and after them, and after purchasing.billline). Each is a possible deadlock path the sentinel cannot see until it is ranked. Source pairs: scratchpad/calc_fixtrade/survey/*.jsonl, graph.py.
2. race: core.documentsequence and accounting.journalentry also appear both ways (BOTH WAYS list from graph.py); per-type sequence rows and new entries make most harmless, unverified.
3. tooling: apps.core.api.exception_handler turns LockOrderViolation (an AssertionError) into a 500; an API test that does not check the status would miss a violation. Kind: test.
4. books/rule, touches O69: the value check counts claims, so after a claim of 100 on an invoice of 1,000, create_credit_note() for the whole invoice (1,000) is now refused at post instead of crediting 1,100. O69's remake (credit at what is left) is still open.
5. books, minor: notes are posted at rounded line values; three notes of 1 on a line of 3 x 33.33 less 10% (89.99) credit 3 x 30.00 = 90.00. Pre-existing; the value check compares unrounded so it does not refuse them.
6. race, minor: the demo loader runs unchecked; the cutover's purchase order book holds orders but not vendors up front (no violation seen).
