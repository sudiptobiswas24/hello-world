# fix_stores2 report (g23, O321 to O331)

Worktree /home/user/work/fix_stores2, base a02071d. Calc: /home/user/work/calc_fix_stores2/ (o325.py; run logs).

## Commits (on a02071d)
- b5260ff docs: the g23 register (f645a85 docs/RISKS.md, docs/handoff)
- f06de7d the audit's probe commit 3ed060e, cherry-picked
- 9e7c81f O324 (blocker): an adjustment reason's account must be income, expense or equity
- c708d04 O331: Warehouse Staff and Quality Inspectors see quantities, not money (migration inventory 0041)
- 9727548 O330: costing method frozen once the item has movements
- c8c5c17 O327: a movement's money has the sign of its quantity
- c8ea9fe O326: StockMovement.save refuses a goods movement written by application code without value
- 7d41d31 O321: a value-only movement needs no bin in a binned warehouse
- 4f4163a O325: legacy subcontract return puts back the assembly's money less the charge
- a1ee0ab O322: standard subcontract assembly: the inventory account moves by what the shelf moved
- 28ca090 O328: write-up at the average is a total share; average_cost_at keeps 8 places
- 047acf8 O323: a write-up of a standard item comes at the standard (another cost refused)
- 374b2e8 inventory 0040 summary sums each row's drop (M3, part of O329). O329 stays open, its row narrowed.

Order differs from the brief in one place: O321 was committed before O325 (O325 was waiting on its purchasing run).
All ten fixed rows are off docs/RISKS.md in their own commits.

## Test that failed without each fix (fix reverted, test run, fix restored)
- O324 apps/inventory/tests_roles.py: "AssertionError: 201 != 400 : reason on Bank: 201"; "200 != 400 : posted: payables credited 50.00 with no vendor"
- O331 tests_roles test_warehouse_staff_do_not_read_what_stock_is_worth: "'unit_cost' unexpectedly found ... : Warehouse Staff"
- O330 tests_roles test_an_items_costing_method_cannot_change_under_stock: "200 not greater than or equal to 400" (the probe at base; the test now asserts 400)
- O327 tests_money AMovementsMoneyHasTheSignOfItsQuantity: "ValidationError not raised" (both)
- O326 tests_money EverySpellingIsSeenWhereItIsWritten: 8 x "ValidationError not raised"
- O321 tests_bins ANewStandardInABinnedWarehouseTests: "ValidationError: ['N - North is binned; this movement must say where in it the stock is.']"
- O325 tests_subcontract AReturnFromBeforeConsumptionWasRecordedPutsBackWhatWentInTests: "(Decimal('-12001.00'), Decimal('-12001'), Decimal('-2999')) != (-15000, -15000, 0)"
- O322 tests_subcontract AStandardCostedAssemblyBooksWhatItsShelfMovedTests: "(10.00, 110, -5, -105) != (10.00, 10.00, -5.00, -5.00)"
- O328 tests_money AWriteUpAtTheAverage...: "Decimal('9999.900000') != Decimal('10000.00')"
- O323 tests_money AWriteUpOfAStandardItemComesAtTheStandardTests: "ValidationError not raised"
- 0040 summary: the --tag migration run now prints "A in warehouse 1: 7 rows, 0.0233 rounded away in all, 0 net" (it used to read "0 dropped").

## Choices made, for review (not owner decisions, but worth a look)
- O324: the Controller now gets add/change on adjustment reasons. A reason's account needs accounting.post_journalentry and no role held both, so otherwise only a superuser could make a reason. The Stores Manager can no longer create one (the account is required), only rename it or change its direction. Posting asks again, so a legacy reason left on a control account cannot book.
- O331: new permission inventory.view_stock_value. Every role that read the ledger keeps it (Stores Manager, Production Supervisor, Maintenance, Production Planner, Quality Manager) except Warehouse Staff and Quality Inspector. Without it: no money fields on stock movements, valuation, slow-moving, negative stock or an item's stock action; reconciliation and the movement summary answer 403. Registry: Stock value, Against the books and Slow-moving now ask for view_stock_value. Stock on hand hides its money columns.
- O326: the rule is a stack walk. The writer is the first frame outside Django and inventory/models.py, and the fallback is allowed only when that frame is a test module (tests*.py, test_*.py, /tests/). The static audit check stays as the early warning.
- O323: I mirrored the write-down rule (O3) and refuse the cost, rather than posting the difference to variance.
- O325: legacy components are shared by their need times the last arrival rate before the receipt. A standard-costed legacy assembly keeps the last-arrival fallback, because its movement holds the standard, not what went in.
- O322: post_inventory_entry now posts a negative pair the other way round. That only happens for a standard subcontract assembly whose components cost more than its standard.

## Owner questions
- O331: should Maintenance and Production Supervisor read stock money? I kept their reach as it was. Warehouse Staff still see purchase prices on goods receipt lines and item standard_cost/sale_price through the item API. The decision named only the movement and valuation reads, so I left these alone.

## Not done
- O329 stays open: (M1) 0040 still gives old rows the replayed figure, not the documents' posted_value / DeliveryAllocation.cost; (M2) the health check nets by inventory account. The ledger has no per-item split, so a per-item check needs a design decision.
- Runs: see the end of this file.

## Defects seen and not fixed
- Shared git stash: worktrees share refs/stash. My `git stash pop` in fix_stores2 at 20:24 popped another agent's O314 deploy/backup.sh stash (466f26b, "WIP on d2ca4c7 O314", pushed 5 s after mine). I restored my tree. That stash commit is now unreachable but still exists. Whoever was on O314 should check that deploy/backup.sh in their tree has its "failed()" function (the 7 lines in 466f26b). Fix agents should not use git stash. Kind: process.
- apps/purchasing/models.py PurchaseOrderLine.component_cost multiplies a rate back up (round_money(quantity_per x average_cost_at)): an estimate for reading, not booked. Kind: rule (minor).

## Runs
All on tip 374b2e8, plus 246ea49 (a browser test only):
- inventory, purchasing, manufacturing, quality, accounting, core (fastsettings, --exclude-tag migration, --parallel 1): 4,667 tests OK (5 skipped).
- apps.web with browser tests (npm run build first, PLAYWRIGHT_CHROMIUM_EXECUTABLE set): 196 tests OK, none skipped. The first web run, on 374b2e8, failed test_stock_on_hand_reads_the_ledger. It signed in as Warehouse Staff and expected the 2,000.00 worth that O331 takes away, so the test encoded the old behaviour. It is rewritten in 246ea49, and apps.web.tests_browser_purchasing (11 tests) and the whole web lane passed after that.
- inventory --tag migration (default settings; 0040 changed, 0041 new): 5 OK.
- PostgreSQL (database fix_stores2, test databases dropped first): apps.e2e.tests_races and tests_audit2_stores_races, 102 OK.
- audit_invariants: "No invariant findings." makemigrations --check: "No changes detected".
- Before the restart, a run of inventory, purchasing, sales, manufacturing, quality, assets, imports, planning, e2e and web passed 5,822 tests with only the open probes failing. That run tested O327 and O326.
- The browser sweep tests take about 10 to 20 minutes each on this shared machine. The worker restart and the 2-hour limit killed two earlier attempts, so the lanes were re-run separately.

## Round 2: the review (review_stores2.md, probes 4676219)
Commits on 246ea49, after the cherry-picked probes 4676219:
- 8afc958 O331: every serializer carrying stock money leaves it out for the floor (item 1)
- ec1c0d4 O331: a Quality Inspector reads no money on production entries either (item 6)
- 4a7146d O324: an inventory account is an asset account kept for stock, chosen by the accountant (item 2)
- 69a2eff O324: the frozen-account test reads the accountant's refusal first (tests_valuation; found by the run)
- 6ca0578 O330: the freeze holds the item's row; template costing method frozen under variant stock (item 3)
- f827f1a O323: an adjustment reads its items again once their shelves are held (item 4)
- d39e5f6 O327: a revaluation cannot take a shelf holding stock below nothing (item 5)
- 0e7f0ae O324, O323, O326: the review's smaller points (item 7: admin clean, rename of a legacy reason, empty standard shelf, is_test_source relative to the repository)
- db03213 O323: the review's stale-standard probe expects the refusal (that probe now raises the refusal)

### Shared rules added
- HidesStockMoney (apps/inventory/serializers.py): a serializer names its stock money in stock_money, and those fields are left out without inventory.view_stock_value. Used by the movement, adjustment, adjustment line and production entry serializers.
- stock_account_refusal / check_stock_account (apps/inventory/valuation.py): an inventory account is an asset account, not a money account and not any other company account. It is asked in Item.save, in ItemTemplate.save, in a company check on default_inventory_account, and again in inventory_account_for. Choosing or changing the account over the API needs accounting.post_journalentry; blank stays the keeper's.
- O330: Item.save is atomic and reads its row FOR UPDATE before the freeze. An item's first movement takes the row FOR NO KEY UPDATE and refuses a movement worked out under a method that has since changed.

### Failed without each fix
- item 1: TheFloorReadsNoStockMoneyAnywhere: "Lists differ: [('/api/inventory/stock-adjustment-lines/' ...] != []"
- item 6: same test: "[('/api/manufacturing/production-entries/' ...] != []"
- item 2: AnInventoryAccountIsAStockAccount: "200 != 400 : Payable: 200", "ValidationError not raised" (template), "200 != 400" (post through a legacy account)
- item 3: race on PostgreSQL: "AssertionError: 2 != 1 : ['done', 'done']"; with the fix: switch done, receipt refused. Template: "200 != 400".
- item 4: AWriteUpReadsTheItemAfterItsShelfIsHeldTests: "ValidationError not raised"
- item 5: ARevaluationCannotTakeAShelfBelowNothingTests: "ValidationError not raised"
- item 7: TheReviewsSmallerPoints, all 4: admin form valid, rename 400, empty standard shelf refused, /srv/tests/... counted as a test

### Runs (round 2)
- inventory, purchasing, manufacturing, quality, accounting, core on 0e7f0ae: 4,744 tests, one error. The error was the review's StaleRead probe, raising the refusal f827f1a introduced; db03213 makes it assert the refusal. After db03213, the touched inventory test files (roles, money, valuation, bins, variants, adjustments, review doors, review urls) passed 248 OK.
- PostgreSQL on 0e7f0ae: tests_races (with the O330 race), tests_audit2_stores_races and tests_review_stores2_races, 104 OK. These ran with --keepdb on the test database from earlier the same day; this round added no migrations.
- Browser: npm run build, then apps.web.tests_browser_purchasing (the Stock on hand tests): 11 OK.
- Not run in round 2, stopped on the integrator's instruction (machine overloaded): the whole apps.web lane, and audit_invariants. makemigrations --check: no changes.

### Not done, and owner questions
- Item standard_cost is still visible to the floor roles. For a standard-costed item, standard_cost x quantity is its worth (review item 7).
- Work orders' planned_unit_cost and conversion_cost are still visible to the Quality Inspector.
- A Stores Manager can still set an item's cogs_account to any account. It is the same shape as the inventory-account hole, on the outbound side. Not registered (no id); seen, not fixed.
- Legacy standard-costed subcontract assembly: O325's last-arrival fallback leaves a price variance (-3.17 in the review's figures). Disclosed; the owner to say whether that fallback should change.
- The first-movement lock takes an item row after the position locks. The suite and the races found no deadlock and no lock-order violation. Two first movements of different new items in one document are taken in line order, not key order.
