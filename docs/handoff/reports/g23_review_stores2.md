# review_stores2: fix_stores2 a02071d..246ea49 (O321 to O331)

Worktree /home/user/work/review_stores2, probes committed on 246ea49 (tests_review_stores2_*.py in inventory, purchasing, e2e). Run as roles over the API, SQLite (fastsettings) and PostgreSQL race().
Author's own tests: inventory tests_roles, tests_money, tests_bins, tests_audit2_stores_money: 72 OK here. makemigrations --check: no changes.

## Findings

1. BLOCKS O331. Warehouse Staff still read stock money through the adjustment endpoints. Role holds view stockadjustment and stockadjustmentline (apps/core/management/commands/setup_roles.py:356). apps/inventory/serializers.py:230-271 (StockAdjustmentLineSerializer `unit_cost`, `revaluation`; StockAdjustmentSerializer `total_value`) are not stripped. Scenario: post an adjustment with a +2 at 9.00 and a revaluation of 7; GET /api/inventory/stock-adjustments/, /{id}/ and /stock-adjustment-lines/ as Warehouse Staff return revaluation, total_value, unit_cost (200). Quality Inspector gets 403 there. Fix: strip those fields without inventory.view_stock_value (as MOVEMENT_MONEY), or drop the view from the role. Probe: tests_review_stores2_doors.FloorReadsAdjustments.

2. BLOCKS go-live, mirror of O324 (not in RISKS). A Stores Manager points an item's inventory account at the payables. PATCH /api/inventory/items/{id}/ {"inventory_account": <2000 Payable>} on an item with no movements returns 200 (no type check, apps/inventory/serializers.py:79 field, models.py `_check_counting_frozen` only freezes after movements). The same person then posts a stock adjustment: payables net debit 50.00 with no vendor. Same shape as the O324 hole, other door. Check the type (asset) wherever an inventory account is set (item, warehouse, company default). Probe: Mirror.test_item_inventory_account.

3. Does not block, should be registered. O330 is not race-safe, and not frozen at the template. (a) PostgreSQL: race((Item, StockMovement), switch method to fifo, first receipt) both finish; item ends fifo with a movement booked under average (models.py:379 `_check_counting_frozen` reads movements.exists() with no lock; the receipt path locks the position, not the item). Probe apps/e2e/tests_review_stores2_races.py fails by design. Same shape as the existing uom freeze. (b) PATCH /item-templates/{id}/ {"costing_method":"fifo"} returns 200 while a variant holding stock stays average; the variant can no longer be saved (`_check_matches_template`), and the template total mixes methods. Mirror gap.

4. Does not block. O323 reads a stale item. adjustments.py:526-545: `lines` are loaded with select_related item before `lock_positions`, so a standard set by set_standard_cost between the read and the lock is not seen. Reproduced by running a set_standard_cost inside the window: write-up of 2 at the old standard 3, then standard 4 already set: shelf reads 7 kg worth 26.00, 7 x 4 = 28. Check made before the lock (audit shape 9). Re-read the item after lock_positions.

5. Does not block, but a lock-up. O327 makes every outbound refuse on a shelf with positive quantity and negative worth. A revaluation of -60 on 10 kg worth 50 is accepted (10 kg, -10.00); a write-off of 4 then fails: "Stock that leaves moves its money the same way; -4 ... cannot move 4.00" (cost_of_removing returns a negative; models.py:889). Deliveries and issues would fail the same way. Either refuse a revaluation that takes a shelf below nothing, or have the removal clamp. Probe: Edge.test_negative_reval_then_write_off.

6. Does not block, disclosed by the author but real. O325 for a legacy standard-costed assembly keeps the last-arrival fallback. Awkward figures (frames 7 at 3.337 and 5 at 4.1119, motors 20 at 1.0033, 7 assemblies, standard 3.3333, service 0.3333; returns 3, 3, 1): shelf and inventory account return to where they were but the price variance is left at -3.17 instead of 0 (recorded consumption returns every account to 0). Average and FIFO legacy, partial receipts and 3-part returns all net to 0 (O325 and O322 hold for those). Probe: purchasing/tests_review_stores2_sub.py.

7. Does not block. Quality Inspector reads manufacturing money of the stock it made: /api/manufacturing/production-entries/ carries posted_value and unit_cost (200 for the role), work orders planned_unit_cost and conversion_cost. Same class as O331 (what a shelf is worth), outside its stated scope. Owner decision: strip or accept. Item standard_cost is also visible to both floor roles (author listed it); for a standard-costed item standard_cost x quantity is the worth.

8. Minor. AdjustmentReason has no clean(): the admin form accepts a payables account (form valid) and save_model then raises ValidationError, a 500 page, not a field error (adjustments.py:140-147). A Stores Manager cannot even rename a legacy reason on a control account (PATCH name returns 400 from validate_account), and cannot repoint it (needs post_journalentry), so a Controller must repair each one.

9. Minor. O323 contradicts O3-style advice on an empty standard shelf: a blank cost says "give an explicit unit cost", any other cost than the standard says "leave the cost blank"; only a cost equal to the standard works, and neither message says so (adjustments.py:529-545).

10. Minor. models.py:~660 `is_test_source` matches `/tests/` anywhere in the absolute path: a plant installed under a directory named tests turns the O326 guard off. Match relative to the apps package. Also average_cost_at at eight places now fills a drop-ship purchase line's unit_price in memory (purchasing/models.py:2238) before the column rounds it.

## Held up (tried to break, could not)

- O324 doors: Stores Manager PUT/PATCH of account to another account, to null, to payables: 400 each; unchanged account PUT 200; Controller to payables 400, to equity 200. Account retyped to liability after the reason: post refuses; legacy reason on payables: post, set_standard_cost refuse; void of an already posted adjustment still allowed (no payables movement). queryset update is code only and post re-asks.
- O330 doors: PUT, PATCH, PATCH to standard, code save under stock all 400/refused; same value PATCH 200; item with no movements may still change (fifo ok, specific refused with untracked).
- O326/O327 writers from a non-test file (exec with an apps path): create, get_or_create, item.movements.create, instance save, **kwargs, value=None, value_adjustment only, outbound with unit_cost only: all refused; wrong signs refused both ways; bulk_create raises NOT NULL (no save()); value 0 with a quantity is accepted (a stated zero, not a guess).
- O331 endpoints as Warehouse Staff and Quality Inspector (lists, details, valuation incl. group_by, slow-moving, negative, item stock, ledger, lots, trail, summary grouped by item, history): no money keys; reconciliation and movements summary 403; ordering by value ignored; no csv/xlsx export exists. Stores Manager still sees all. Crawl of 42 and 67 URLs on the demo plant for both roles: probe tests_review_stores2_crawl.
- O328: 9,000 kg worth 30,000.00, +3,000 found blank: 10,000.00; 0.0001 found books 0.00 and the shelf agrees. O321, O322 (standard above and below actual), O325 (average/FIFO, recorded and legacy, partial receipts, parts 3-3-1): net to zero.
- 0040 summary change reads correctly; 0041 only adds the permission; deploy/entrypoint.sh runs setup_roles, so existing roles gain it (a custom role outside setup_roles does not).

## Not run

Browser sweep and the full suite (shared CPUs); other PostgreSQL races beyond O330. audit_invariants finished with exit 0 on the tip (output tail lost to the 120 s wrapper; makemigrations --check clean).
