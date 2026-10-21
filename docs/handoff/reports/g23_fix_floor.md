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
