# audit2_stores: review of a02071d (O269) and the stores lane

Worktree /home/user/work/audit2_stores, base a02071d, probe commit 3ed060e (tests only, unpushed).
Probes: apps/inventory/tests_audit2_stores_{money,roles,migration}.py, apps/purchasing/tests_audit2_stores_money.py,
apps/manufacturing/tests_audit2_stores_money.py, apps/core/tests_audit2_stores_checks.py,
apps/web/tests_audit2_stores_demo.py, apps/e2e/tests_audit2_stores_races.py; hand figures in
calc_audit2_stores/fifo3.py. PostgreSQL (db audit2_stores) gave the same results as SQLite.
audit_invariants: "No invariant findings." makemigrations --check: "No changes detected."

## Part 1. Review of a02071d

Verdict: no blocker. The central claim held under everything I could throw at it. Four findings, none stops go-live
on a fresh install; the first matters only for old subcontract data.

### What held (PASS)
- Random runs, 12 seeds of 30 steps each, over average, FIFO, standard and specific-by-lot items in two
  warehouses: write-ups at 10.005, 95.835051, 7.3333, 3.14159, 0.995, 1.005, 6.665; write-offs; voids of
  random earlier adjustments; transfers of 0.3333 and 1.25 kg. After every step the shelf equals the inventory
  account, the fold equals a full replay (`replay(before_id=big)`), per item and warehouse. PASS.
- Three FIFO layers booked 10.01 / 33.33 / 383.34: five out takes 19.53, six more 119.65, 287.50 left on 3
  (calc_audit2_stores/fifo3.py). PASS.
- Purchasing under average, FIFO and standard: receipts in two parts at 4.01, 3.3333, 95.8351, partial returns,
  write-off, freight landed after part of the goods were written off, freight refunded by debit note, return after
  landing; ordered in cases of 12 at 7.01 a case. Shelf = ledger after every step. PASS.
- Manufacturing under average and FIFO: issues of 700.3333 / 151.1111 kg, a second issue and its void, a return
  against a line, two outputs (333.3333 and 666.6667) with scrap and a byproduct, void of one, close, reopen,
  stock costs of 100.00333 / 97.123456. PASS.
- A fold written, then a movement dated before its boundary, under average, FIFO, standard. PASS.
- PostgreSQL race(): two write-offs of the last two kilos at an average of 10.005 (10.01 and 10.00 land, shelf
  0 / 0, books 0), and a write-off against a transfer out of the same shelf. PASS.
- Demo plant: loaded, then 8 write-offs and write-ups at 0.3337 / 1.0001 / 7.7777 kg and 111.1111 a kilo;
  "Stock agrees with the ledger" stays ok. PASS. (apps.web.tests_load_demo, 3 tests, also OK.)
- Migration 0040 on a mixed old shelf (average with a short shelf and a landed row, FIFO with a shipped layer's
  landed cost and a short, specific with two lots, standard revalued once): runs, every row a whole paisa, the
  average shelf within the rounding of the old replay (tests_audit2_stores_migration, default settings, `--tag
  migration`, 8 s after a 10-minute database build). PASS. It prints "A in warehouse 1: 7 rows, 0 dropped": the
  figure is the net of the drops, so + and - cancel and "0 dropped" can hide rounded rows (see M3).

### Findings

**R1 (books, minor, legacy data only; a regression against the base).** The author's decision for a subcontract
return with no recorded consumption, components back "at what they last came onto the subcontract shelf at"
(`_last_arrival_rate`, purchasing/models.py near 7625), does foot (shelf -12,001.00 = ledger -12,001.00) but books
a price variance nobody paid. Plant figures: frames at the subcontractor arrive 1,000 at 5 then 2,001 at 8,
motors 6,000 at 12; 3,000 assemblies at 5 service are received (consumption 21,000.99 for frames) and the
consumption notes are blanked as in the author's own test; the whole receipt is returned. The frames are restored
at 8 where they went out at 7.0003: purchase price variance balance -2,999 (a gain nothing explains). Recorded
consumption gives 0. At the base (average where it stands) it gave about 0. Right answer, readable from the data:
the assembly's own receipt movement says what was consumed (its `value` less the vendor's charge,
`round(quantity x price)`), shared between components by their last arrival money. Probe:
purchasing tests_audit2_stores_money.LegacySubcontractReturn.test_legacy FAILS (the recorded case passes).
Would not stop go-live: only a receipt from before O253 reaches it.

**R2 (rule, minor; the guard is weaker than its claim).** `movements_without_their_money` sees only the spellings
`StockMovement(` and `StockMovement.objects.create(` with no `value=` keyword. It passes, unseen: `value=None`,
`**fields`, `item.movements.create(...)`, `StockMovement.objects.get_or_create(...)`, a module-qualified class
(`inventory_models.StockMovement...`), an alias (`Movement = StockMovement`; load_demo.py:271 is one, written
correctly). The planted case and the near-miss (value= given) behave. A writer that slips past gets the save()
fallback, which for an inbound movement is quantity x rate: the very multiply-back O269 closed. Probes:
apps/core/tests_audit2_stores_checks, six FAIL, two PASS. Fix: scan every call whose attribute chain ends in
`.create`/`get_or_create` on `movements` or a name bound to StockMovement, and treat `value=None` and `**` as
unproven; or make `_settle_money` refuse a missing `value` for a goods row outside tests.

**R3 (rule, minor).** A movement's money is not checked against its quantity's sign: an outbound movement with
value +10.00 and an inbound one with -10.00 are both written (probe: tests_audit2_stores_money.ASignIsGuarded,
two FAIL). A writer that books a return the wrong way round is then taken as a fact. Refuse it in
`_settle_money` (quantity < 0 -> value <= 0; quantity > 0 -> value >= 0), the value-only rows excepted.

**R4 (books, minor; the claim in the report is not true as written).** "Average costs are still read to eight
places for pricing": `Item.average_cost_at` quantizes to four (models.py 468), and a write-up with no cost given
(adjustments.py 499, a count that finds stock) takes `quantity x that 4-place rate`. 9,000 worth 30,000.00 average
3.33333333; 3,000 found with a blank cost are booked 9,999.90, not 10,000.00. Shelf and ledger still agree (both
9,999.90), so it is a valuation error, not a drift: 0.10 on 3,000. The mistake-list shape "a rate multiplied back
up". Probe: tests_audit2_stores_money.AWriteUpAtTheAverageIsATotalNotARateMultipliedBack FAILS. It predates O269.

### Smaller points on the review (no probe, or a decision for the owner)
- M1 (unproven). Migration 0040 reads no document: it gives each row the replay's exact figure rounded half up,
  not what the entry booked, although posted_value (adjustments, material issue lines, production entries) and
  DeliveryAllocation.cost are kept. So a plant upgraded with old rows keeps whatever paisas its shelf and books
  already parted by; the health check would show them on day one. Proving it needs old journals booked at the old
  rounding; I did not build one. Would be proven by: an old database whose "Stock agrees with the ledger" differs
  after the migration.
- M2 (design). The health check "Stock agrees with the ledger" nets by inventory account (reconcile_to_ledger):
  +0.01 on one item and -0.01 on another sharing the account read as agreeing. The new rule is exact per
  movement, so a per-item reconciliation would be a sharper check.
- M3 (minor). The migration's summary prints the net dropped per shelf; use the sum of absolute drops.
- M4 (decision). `Item.costing_method` is not frozen under stock (models.py `COUNTED_AS` has uom and
  track_inventory, the inventory account is separate): a Stores Manager (item change) can PATCH an item holding
  10 kg from average to FIFO (probe: roles test_an_items_costing_method_cannot_change_under_stock, 200 where I
  expected a refusal). Since O269 the shelf keeps its money, so nothing drifts; but moving to standard leaves a
  shelf that is not quantity x standard and posts no variance. The owner to say whether a switch is allowed.
- The three gaps the author listed (binned warehouses O321, standard subcontract O322, standard write-up O323)
  are already registered; I did not re-probe them.

## Part 2. The stores lane (inventory, quality, purchasing's stock paths)

### Findings

**S1 (books and security, go-live blocker in my view).** A Stores Manager can credit or debit any account through
a stock adjustment. The role holds `inventory.add_adjustmentreason`/`change_adjustmentreason`,
`add_stockadjustment` and `change_stockadjustment` (post), and no accounting permission. `AdjustmentReason.account`
takes any account (inventory/adjustments.py 94-110; no type check, serializer lists `account` plainly). Scenario,
over the API as "Stores Manager": POST adjustment-reasons {account: Accounts Payable (liability), direction:
increase} answers 201; POST a stock adjustment on it, a line of 4 at 12.50, POST .../post/ answers 200; the
payables control is credited 50.00 with no vendor, bill or journal permission (the Bank account is accepted as
a reason account too, 201). Shape: the O303 shape (a line takes any account) on the stores side; mirror of the
checks the journal API makes. Probes: roles test_a_stores_manager_cannot_point_a_reason_at_the_payables_or_the_bank
and test_a_stores_manager_cannot_credit_the_payables_control_through_a_stock_adjustment FAIL. Fix: a reason's
account must be an income, expense or equity account (the revaluation and shrinkage accounts), never a control
or money account, and changing it needs `accounting.post_journalentry` as set_standard_cost does
(views.py 130).

**S2 (security, minor; owner's decision).** Warehouse Staff and Quality Inspectors hold `view_stockmovement` and
so read `unit_cost` on every movement, and the stock valuation, reconciliation and aging reports (they require the
same permission). Floor roles reading what the stock is worth. Probe: roles
test_warehouse_staff_do_not_read_what_stock_is_worth FAILS (unit_cost present). Same family as O299.

### What I probed that held
- Kolkata day: no `date.today()`, `datetime.now()`, `now().date()` or `utcnow` anywhere in inventory, quality or
  purchasing (grep); `__date` filters run in the project time zone. Not run under DJANGO_TIME_ZONE with a document
  made between 18:30 and 24:00 UTC (not reached).
- Inert features, the other way round: every field of the inventory and quality models is read by some code except
  descriptive ones (Lot.supplier_reference, Instrument.serial_number, Calibration.certificate_reference and
  traceable_to, StockValuationSnapshot.folded_at). `concessions_need_a_second_person`,
  `needs_calibrated_instrument`, `reversed_step` are read.
- The release gate: check_released is asked by deliveries, process rolls, bales, tape loads, material issue; the
  subcontract consumption path has its own held-batch rules (O135, O148); transfers deliberately do not ask
  (owner's decision of 9 October); a re-batch carries the hold through the lineage registry.
- Two documents at once on one shelf (PostgreSQL race(): two write-offs, a write-off and a transfer): the money is
  taken after the lock; both go through and agree.
- Counts post through an adjustment, so a count shares every figure with it (fuzz and PASS above).
- Folds: a fold followed by a movement dated before its boundary reads as a full replay.
- Roles: Warehouse Staff post transfers and counts but not adjustments or count differences; Stores Manager posts
  adjustments; `set_standard_cost` needs accounting.post_journalentry. As intended.

### Not reached
- A plant month's worth of stores documents (thirty receipts, a correction on the last day of a month, a closed
  period in between) beyond the random runs above.
- Lot trace and recall walks, FEFO ties under PostgreSQL ordering, expiry on the plant's day.
- Consignment draw-down and customer material money beyond reading the paths (value 0 both ways).
- The quality module's inspection post/void/decide paths beyond reading their guards; earlier reviews
  (tests_review_stores_probes, qrecall races) cover them.
- Browser drive of the stores screens as their people.
- Migration 0040 against an old database with real journals (M1).
