# fix_floor: O271 to O279

Worktree /home/user/work/fix_floor, base 32a07ec. First commit is the audit's probe commit 7a406f3,
cherry-picked, gate line rewritten to `Gate: {GATE}`, tree unchanged (61b0b4a). Nothing pushed.

## Commits (one per id, in order)

| commit | id | what is true now |
|---|---|---|
| 68324bb | O271 | add_scalereading is the "Scale bridge" role's alone; Station and Production Supervisor keep view only; the endpoint refuses any login that may weigh at a station even with the right by hand. Data migration manufacturing 0086 moves the right on existing databases and back on reverse (MigrationExecutor test, tag migration). |
| 767942a | O272 | A roll booked against an entry or weighed at a station refuses changes to every measured or decided field (notes excepted) and is not deleted while its booking is posted and unvoided or its stock stands. audit_invariants: `weighed record written over the api`. |
| ca6e7ee | O273 | `shifts.batch_code()` builds roll, doff, film and process-roll codes with the whole shift code as kept. |
| 8a1a614 | O274 | `is_locked` counts every failure in the ten-minute window; the test that held the reset is rewritten; the F1 probe tolerates the lock between rounds (assertion unchanged). |
| 6a9a33c | O275 | `check_rework` asked at release, when a saved bill is turned into or out of rework, and when a rework bill's last line for its batch would be deleted or changed away. |
| f6fc5d0 | O276 | `refuse_drawing_what_is_promised` asked by re-batch (post and void), move_stock (transfer, clearance, subcontract send), decreasing adjustment line and its reversal, transfer sent back, vendor return (goods-receipt return), a delivery line naming its batch, voided output (_take_back_off), capitalising from stock, subcontract component consumption. audit_invariants: `named draw past the promise` (any function pricing a named batch's removal must ask it; three listed exemptions). StockMovement values and inventory/costing.py untouched. |
| ad91516 | O277 | A published CostVersion refuses changes (notes excepted) and deletion; its StandardCost rows are not changed, added, moved or deleted; API answers 400. The posted-document audit check reads published_at as the flag. |
| 4fa4466 | O278 | One `_core_in_use` lookup for the loom exit, tape line, film line and roll-off; a retired core is refused in words. |
| bddf02c | O279 | `whole_bobbins()` reads the count before the scale is asked; non-whole counts, None and booleans are refused. |

## Each fix: the test that failed without it (reverted the commit's source files, ran, restored)

- O271 OnlyTheBridgePostsReadingsTests: `AssertionError: 201 != 403` (tablet, supervisor, weighing login with the right by hand); `Group.DoesNotExist` for Scale bridge.
- O272 AWeighedRollStandsAsWeighedTests: `AssertionError: 200 != 400` for length_m, width_mm, machine; `204 != 400` for the delete.
- O273 ShiftsThatShareALetterTests: `IntegrityError: UNIQUE constraint failed: inventory_lot.item_id, inventory_lot.code`.
- O274 StationLockTests.test_a_right_pin_between_wrong_ones_does_not_reset_the_count: `AssertionError: ValidationError not raised`; probe F1: `80 not less than 10`.
- O275 AReworkRecipeEatsItsBatchTests (3 tests) and probe F9: `AssertionError: ValidationError not raised`.
- O276 PromisedTapeStaysForItsRunTests (re-batch, move, write-off) and probe F2: `AssertionError: ValidationError not raised`.
- O277 APublishedVersionStandsTests: `ValidationError not raised`; API `[200, 204, 200, 204] != [400, 400, 400, 400]`; probes F7 both fail.
- O278 ARetiredCoreIsNotTakenTests: `AssertionError: 201 != 400`; probe F11 `201 not greater than or equal to 400`.
- O279 BobbinsAreCountedWholeTests: `ValueError: invalid literal for int() with base 10: 'eight'`, TypeError for None, `ValidationError not raised` for 2.7.
- Planted-instance tests for the three audit checks: apps/core/tests_audit_posted.py.

## How the series was built (so the integrator can trust it)

My first pass staged split hunks with `git apply --cached --unidiff-zero`; pure insertions landed at the
wrong line in the O271, O272 and O276 commits (O272 and O276 did not parse). I rebuilt the whole series
from whole-file states: each shared file at each commit is the final file minus the later ids' edits
(script /home/user/work/calc_fix_floor/build.py). Every .py file each commit touches parses at that commit.
The tree at the tip is the tested one. A later fix to O275 (the phantom case, below) was folded in with
commit-tree, no files changed in the worktree. Committer is Claude; the probe commit keeps its author.

O275 amendment: tests_effectivity `test_nor_is_it_a_rework` holds that the database constraint refuses a
phantom turned into rework; my save check refused first with a ValidationError. The check now skips
phantoms on that flag change (the constraint is the rule there); the release check still asks every bill.

## Checks run

- Each regression test and probe, before and after (above). All 13 probe failures in tests_audit2_floor now pass.
- manufacturing 0086 with MigrationExecutor (tag migration): OK.
- audit_invariants on the tip: "No invariant findings." makemigrations --check: "No changes detected".

## Owner's decisions (I decided none of them; current behaviour stated)

1. Lock-out (O274) counts per station: five wrong PINs from anyone in ten minutes lock the tablet for
   everyone, the supervisor included, until the window passes. Per-login or per-person counting is a choice
   between how many guesses an attacker gets and how often a shift is stopped by fumbling.
2. A weighed roll (O272) may be deleted once its booking is voided and its stock is zero. Whether the
   record of a voided weighing is ever deleted is the owner's.
3. O271 creates a "Scale bridge" role. Who is put in it and which integration logins hold it is the
   owner's. A bridge login given add_scalereading directly keeps working; one that also holds
   weigh_at_station is now refused.
4. O276: tape on a run's creels is now refused at every door that takes a named batch off, a vendor return
   and the void of the doff's own production entry included. Whether a supervisor can override that
   (withdraw the load first is the only way now) is the owner's.

## Defects seen and not fixed

- apps/manufacturing/shifts.py batch_code (was in each builder): machine codes "L-17" and "L17" give the
  same batch code (hyphen stripped), so two machines named that way collide like O273. Kind: crash, minor.
- apps/inventory/picking.py:62-68 plan_issue/pick_list: a batch named by hand is asked lot_held but not the
  promise. The doors that draw now ask it themselves; a pick list can still offer promised tape. Kind: rule, minor.
- apps/manufacturing/rolls.py:60 FabricRoll.lot is on_delete=CASCADE: deleting the Lot removes the roll
  without asking its delete(). Not probed (a Lot with movements is probably protected). Kind: rule, unprobed.
- From the audit's "not reached" list, untouched: challan job worker vs the step's vendor; piece pay on
  held or rejected output; stoppage minutes unbounded by shift length; the tape count's `correct` flag read
  as truthiness rather than apps.core.api.flag.

## Gate results, all on tip bddf02c

- apps.manufacturing apps.inventory apps.quality apps.core apps.purchasing apps.sales (fastsettings,
  --exclude-tag migration): Ran 5328 tests, OK (skipped=6).
- apps.web after `npm run build`, PLAYWRIGHT_CHROMIUM_EXECUTABLE set: Ran 194 tests, OK, none skipped.
- PostgreSQL (database fix_floor, test_fix_floor* dropped first): apps.e2e.tests_races and
  apps.manufacturing.tests_audit2_floor_race: Ran 101 tests, OK.
- apps.manufacturing --tag migration (default settings): Ran 5 tests, OK.
- audit_invariants: "No invariant findings."  makemigrations --check: "No changes detected".
- Not run: the suite under DJANGO_TIME_ZONE=Asia/Kolkata; the whole suite on PostgreSQL (races only).
- A worker restart killed my background jobs once; every lane above was re-run to completion on bddf02c.

---

# Review round (review_floor.md, probes cfc5114)

The review's probe commit is cherry-picked (9e33762, gate line `Gate: {GATE}`). Before it, the nine O271
to O279 commits were rewritten (plumbing; trees otherwise unchanged) so that each takes its own row off
docs/RISKS.md: my first round had left all nine open. New ids: O271 64f7563, O272 c8924bb, O273 eb54bdb,
O274 1b6915a, O275 c9e6c70, O276 4f2c210, O277 0044f3f, O278 a94930e, O279 77e2fa3.

O338 to O341 were added on the main line (fbdda07) after my base 32a07ec, so this worktree's
docs/RISKS.md has no such rows: their commits name them and say so, and the rows come off at the merge.
The registercheck will see them open until then.

## Commits, in order

| commit | item | what is true now | failed without it |
|---|---|---|---|
| 15119bb | 1, O275 | A line's save and delete ask every rework bill they touch (the one it leaves and the one it joins); a change that takes the last eating line off a bill that had one is refused; a bill asks check_rework when its item or flag changes; ProductionEntry.post asks it of a rework run's bill before it books output. | AReworkBillKeepsItsBatchThroughEveryDoorTests: `200 != 400` (line moved; bill's item), output refused for the lot instead of "has no line for" |
| d5ae1d4 | 2, O338 | A weighed roll is never deleted (its reading stays tied to it); batch_code numbers one past the highest number used under its stem. | AWeighedRollStandsVoidedOrNotTests: `ValidationError not raised`; `('...-01', '...-03') != ('...-01', '...-06')` |
| b84c6f4 | 4, O272 | A roll with a weight is weighed: a hand roll's measures are fixed and it is not deleted. One roll test that edited a core weight after creation now builds the roll with it. | AHandRollIsWeighedTooTests: `200 != 400` |
| 9183bdc | 3, O272 O277 | FixedOnceAdminMixin: read-only change page once weighed or published (notes excepted), delete view 403, bulk delete through each delete() with words; a published version's standards inline takes no add, change or delete. | admin tests: notes refused with the form; `costs-0-material` found on the page |
| aa0b52b | O340 | plan_issue asks the promise for a batch named by hand (pick_list, delivery, subcontract send). | APlanForAPromisedBatchIsRefusedTests: `ValidationError not raised` |
| ca830fa | 5 | The three checks read the AST: removals by keyword or position and direct outbound StockMovements, each paired with a guard on the same batch expression earlier in the function; the weighed and published checks need an `if` naming the flag whose body raises. Delivery.post is listed as asked in plan_issue. | TheChecksReadStructureNotWordsTests: `[] != [...]` for every near-miss |
| 336a542 | 6 (and review items 7, 8, 10) | bag_code goes through batch_code; batch_code escapes anything that is not an ASCII letter or digit as _XX_ (Code 128 set B); check_core_in_use() in record_roll, record_tape, record_film, weigh_roll; RUNBOOK names the Scale bridge role and what 0086 does to a bridge that held the right through a group. | OneRuleForABatchsNameTests: `'FR-260601-Früh 1-L17-01' != 'FR-260601-Fr_FC_h_20_1-L17-01'` |
| be82c64 | O339 | Machine.save refuses a code that reads like another machine's inside a batch code (shifts.machine_part). | MachinesThatReadAlikeTests: `ValidationError not raised` |
| ea97845 | O341 | FabricRoll.lot is PROTECT (migration 0087, one AlterField of fabricroll.lot, read). | `ProtectedError not raised` |
| b91487a | 7, O276 | move_stock opens its own transaction and takes both positions (lock_positions) before it reads; StockTransferLine.move locks before its shelf read (audit_invariants' shelf_read_before_lock saw the order once move_stock locked). Callers checked: transfer posting and the subcontract send already lock both. | PostgreSQL AMoveTakesItsOwnPositionsRaceTests: `[True, True] != [False, True]` |

## Owner's questions, this round

- A 0.0001 kg eating line counts as eating (review P5). Whether a rework bill must eat some minimum share
  of its batch is the owner's.
- A count (StockCount.post) short on promised tape is refused through the adjustment's guard until the
  load is withdrawn (review item 9, not run): whether a physical shortage may be booked against tape on a
  creel is the owner's.
- Shift codes with characters outside ASCII letters and digits now print escaped (S 1 is S_20_1).
  Whether to restrict Shift.code itself instead is the owner's.

## Seen, not fixed

- A rework bill's eating line removed by a queryset delete (BomComponent.objects.filter(...).delete())
  passes the line's guard; output booking now refuses it, release refuses it. Kind: rule, covered.
- Two other agents' test runs (fix_stores2, fix_blockers) shared the machine; nothing of theirs was touched.
- tests_review_floor P6Promise.test_count_door built its count line without system_quantity (NOT NULL), so
  it never ran; 3db0b9a gives it 100 and it now shows the count refused (test only).
- apps.manufacturing.tests_review_qrecall.TheColumn.test_a_page_of_batches_asks_the_gate_once_for_the_page
  failed once with TransactionManagementError in a --parallel 2 run on b91487a. It passed alone (25 of 25),
  in the next full run on the same tip, and in the bddf02c run. I did not find what decides it: run order
  or the machine's speed. Kind: an order or speed-dependent test (a defect, not a flake). Not in RISKS.md.

## Gate results, review round

- Six modules (fastsettings, --exclude-tag migration, --parallel 2) on b91487a: Ran 5361 tests,
  1 error (the review's count probe, fixed in 3db0b9a, which then passes; no product code differs).
  A first run on the same tip stopped at the qrecall error above.
- apps.web after `npm run build` on b91487a: Ran 194, OK.
- PostgreSQL (test_fix_floor* dropped first): apps.e2e.tests_races, tests_audit2_floor_race,
  tests_review_floor_race on b91487a: Ran 103, OK. AMoveTakesItsOwnPositionsRaceTests fails without its fix.
- apps.manufacturing --tag migration (0086, 0087) on b91487a: Ran 5, OK.
- audit_invariants: "No invariant findings." makemigrations --check: "No changes detected".
- Not run: the Asia/Kolkata suite; the whole suite on PostgreSQL.
