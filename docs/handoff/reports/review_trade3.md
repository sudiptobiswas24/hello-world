# Review of the lock-order sentinel and the trading fixes O169-O175 (da765b1, ad45168, 0cdf3a9, 5b7257f)

Reviewed at 5b7257f in a detached worktree. Nothing was fixed. Probes are in `review_trade3_tests/` (copy each into its app to run it):
`tests_probe_trade3_sales.py` (apps/sales), `tests_probe_trade3_sentinel.py` (apps/core), `tests_probe_trade3_purchasing_pg_landed_cost_deadlock.py` (apps/purchasing, PostgreSQL), `tests_probe_trade3_demo.py` (apps/web), `strict_sentinel_save_counts_as_lock.patch`, `planted_reversals.patch`, and the logs.

## Baseline
- The previous review's probes (docs/handoff/reports/probes): the five rows they described are closed. Credit note against a credit note and debit note against a debit note are now refused when the note is created (the purchasing probe now ERRORS at `Bill.objects.create`, earlier than the probe expected). A claim then a whole-invoice credit is refused. A typed note at ten times the price is refused. A typed note with no order line frees the order line (passes). The drop-ship re-point is refused. The two-returns race on PostgreSQL gives `['done', 'done']`. The two approved-figures probes still fail: one is the by-design retyped line, one simulates a NULL `approved_figures` without running 0064.
- Touched modules once (fastsettings, nice 19, --parallel 2, --exclude-tag migration): `apps.sales apps.purchasing apps.core apps.accounting apps.imports apps.web`, 2431 tests, 104 skipped, 4 failures, all review probes (3 of the earlier review's, 1 of mine that asserts the demo loads with the check on). No product test fails. Log: `review_trade3_tests/touched_run.log`.
- PostgreSQL (erp_revtrade3, one run, migrations on): `apps.e2e.tests_races`, the earlier two-returns race, `apps.core.tests_lock_order`: 78 tests OK.
- Not run: `audit_invariants`, the full PostgreSQL lanes, the IST lane.

## 1. The sentinel itself
Sound where it looks:
- It hooks `SQLCompiler.execute_sql`, so every ORM `select_for_update` is ranked: `lock_rows`, `lock_in_turn`, `@serialised`, `row_lock_query` with `no_key=True` (what `lock_for_change` takes), `.iterator()`. S1 and S2 raise.
- Planted reversals fail the suite. In a scratch copy I (a) made `Invoice.locked_before_it` list the credited invoice and the deposits before the orders, and (b) dropped the sales order from `GoodsReceipt.locked_before_it`. `apps.sales apps.purchasing` under `--parallel 2`: 26 failures, every one a `LockOrderViolation` (22 "sales.salesorder locked after purchasing.goodsreceipt", 4 "locked after sales.invoice"). Patch: `planted_reversals.patch`.
- Off in production: `DJANGO_ENV=production` with argv `test` gives False; `runserver` gives False; development with argv `test` gives True. `apps.ready()` installs only when the setting is true. On in the gate's lanes: sqlite, pg-a, pg-b run config.settings under `manage.py test`; ist sets it True in fastsettings unconditionally. `test_it_is_on_under_tests` fails if someone exports `LOCK_ORDER_SENTINEL=0`, but that test lives in apps.core, which only pg-a runs.

What it does not see:
- **S3. Unranked models skip the key-order check as well as the rank** (`if rank is None: continue`). That is what hides finding 2.
- S3b. A locking query that does not select the pk (`values_list("code")`) records no key: the same model locked in reverse key order passes.
- S4. `update()`, `save()` and `delete()` take row locks the sentinel never records. Probe: lock party 2, then `update()`/`save()` party 1: not seen. To see whether anything hides there I made a scratch copy count every `save()` of an existing row as a lock (`strict_sentinel_save_counts_as_lock.patch`) and ran `apps.sales apps.purchasing` (1666 tests) in survey mode: 0 violations. So the blind spot is real, and nothing is in it today for `save()`. `update()` and `delete()` were not measured. Production code has one `.update()` on a ranked model (`SalesOrder.approve`, under its own lock).
- S5. `select_related()` + `select_for_update()` locks the joined table on PostgreSQL; only the base model is recorded. No production lock query joins (grep).
- S6. A savepoint that rolls back keeps its rows "held": lock b in a savepoint that fails, then a, raises a violation PostgreSQL would not have had. A false positive, not a hole.
- Raw SQL `FOR UPDATE`: none in production code (grep). FOR KEY SHARE locks taken by an insert on its parents (a delivery row on its order) are not modelled; `lock_rows` takes FOR UPDATE, so a customer lock against a document insert is a possible pair. Not run.
- The prose in apps/core/models.py still says "deliveries and receipts, then invoices and bills"; LOCK_ORDER ranks receipt, delivery, bill, invoice. Cosmetic, but it is the comment-is-not-a-constraint shape.

## 2. Unranked models (O182): a deadlock on PostgreSQL, proven
**Two landed-cost allocations onto the same two receipt lines, listed the opposite ways round, deadlock on `inventory_stockposition`.**
- `BillLine.allocate_landed_cost(receipt_lines)` (apps/purchasing/models.py:4425) applies the cost one receipt line at a time; `_apply_landed_cost` (:4512) takes that line's positions with `lock_positions`, which sorts only within one call. The lines are used in the order the caller gave them.
- Probe `TwoLandedCostsOnTheSameTwoReceiptLines` (RaceCase, `race(StockMovement, ...)`): freight charge 1 onto [X, Y] and charge 2 onto [Y, X], items X and Y at one warehouse. Result: `['refused: deadlock detected ... CONTEXT: while locking tuple (0,26) in relation "inventory_stockposition"', 'done']`.
- Not from these commits; it predates them. The cycle is between two positions, not a position and a document, so ranking the model against documents is not needed to catch it: a key-order check inside any model, ranked or not, would have raised in the survey of the suite. Its other pairs from the survey do not yet make a cycle I could show: `inventory.stockposition -> sales.invoice` (a return takes the position, then the invoices) has no path that holds an invoice and wants a position, and `stockposition -> purchasing.billline` looks like a test's outer transaction rather than `create_debit_note` itself (nothing in it locks a position).
- Kind: race. Add to O182.

## 3. The note-line check
Closed and sound: a note on a note (sales and purchasing, at save), 10 at 1,000 against 10 at 100 (refused), the order line filled from the line given back, a partial then a whole credit (P5 posts, AR 0), a typed note's own tax (ignored, the note takes the original's: P4 AR 0, tax 0), a claim then a whole credit (refused: "900.00 left to give back; this note gives back 1000.00").

Defects:
- **3a. A claim, then the goods come back: the return is refused outright (workflow, medium).** P1: invoice 10 x 100, `credit_claim(100)`, then `Delivery.create_return()` of all 10. `_credit_returned_goods` calls `create_credit_note`, the new value check refuses (900.00 left, 1000.00asked), and the whole return rolls back: stock stays shipped (on hand 490). P2: after the claim, a return of 5 posts, the next 5 is refused (400.00 left, 500.00 asked). The check is right that 1,000 cannot be credited; the return flow is wrong to ask for it. The only way through is `credit_invoices=False` and a typed note for the remainder. The scenario is ordinary (price claim, then rejection), so the check or the return needs to clamp to what is left. Purchasing mirror not run.
- **3b. The cap is the line's total value, not its price (low).** P3: 1 unit at 1,000 on a 10 x 100 line posts (AR 0); the other 9 units then cannot be credited at all (0.00 left). P6: 5 units at 150 posts, the other 5 can then be credited only up to 250. O171's example (10 at 1,000) is closed; a unit price above the line's is not.
- **3c. A claim ignores what the line has already given back (low).** P7: two-line invoice 2 x 1,000; line 1 credited whole by a note, then a 1,000 claim, spread by value over both lines: line 1 now shows 1,500 given back of 1,000. The invoice total is right (AR 0) but the per-line revenue and tax are not, and line 2's own goods can then be credited for only 500. `credit_claim` caps at the invoice (`left = subtotal - credited`), not per line.
- 3d. P8 (not changed by these commits): a typed note line may credit any revenue account, not the one the line used.

## 4. The O174 backfill (0064)
- **Orders already cut before the seed are seeded at the cut figures (medium-low).** P9: approved at 20% over a 15% policy, confirmed, cut to 10%, `approved_figures` NULL (as before 0063), run the migration's `seed`: seeded `{'1': '10.00'}`; putting the line back to 20 is refused ("needs approval beyond what it has"). That is the exact symptom of O174, left for any order cut between its approval and the deploy. The migration's test covers only an order that was not cut. The history to reconstruct it is not in the order; if nothing else is available the row should say so.
- The seed re-derives the margin and total from today's costs and taxes (`figures_as_kept()` on the live model), not the approver's: the mistake-4 shape. It can only be wider or narrower than the approval, never the approval itself.
- The migration imports the live `SalesOrder` and runs `policy_figures()` (lines, taxes, charges, item costs). A later migration that adds a column the live models read, in sales or any app the margin touches, will fail this one when a database with approved orders upgrades across both. Not run; the test runs it at head.

## 5. O183, the demo loader
- The row describes the wrong check. `unchecked()` is `apps.core.lock_order.unchecked`, the lock-order bypass; the note value check is not bypassed, and the demo's one credit note (2,400 sacks) passes it.
- With `unchecked` made a no-op (`tests_probe_trade3_demo.py`), one section fails: "Three months of orders, deliveries, invoices and money received", `core.party 5 locked after core.party 7` at load_demo.py:439 (`o.confirm()` inside `sell()`): customers confirmed one after another in a single transaction, in no key order. With that one violation logged and skipped, the rest of the demo loads with no further violation. So the check is not wrong and no scenario reverses a real path; the boundary is: many customers in one transaction, which no request does. The bypass wraps every section (`step()`), so a real reversal added to the demo later is hidden. Narrow it to that section, or give each `sell()` its own transaction (the sentinel cannot tell a savepoint from a transaction, so it needs `forget()` between them).

## 6. Smaller points
- `Delivery.locked_before_returning` and its mirror read which invoices billed the lines before any lock is taken. An invoice posted in between (billing the line from another order) is not in the held set; `_credit_returned_goods` then takes it, and its other orders, after the position. Narrow, three-way, not run.
- `Invoice.locked_before_it` now also locks the earlier deposits of the order for every method held first, a credit note on a posted invoice included. More contention, no cycle.
- O173: the freeze at CONFIRMED means `drop_ship_for` cannot be set on a confirmed order that was not a drop-ship, or corrected before any receipt; cancel and remake, the same as vendor. No code writes it, so nothing else breaks.

## Numbers to carry into RISKS.md
Add to O182: the proven landed-cost deadlock (finding 2) and the key-order-within-unranked-models gap. New rows: 3a (return after a claim), 3b/3c, 4 (cut-before-seed), the demo bypass scope (O183's wording).
