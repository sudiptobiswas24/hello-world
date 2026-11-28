# audit2_floor: manufacturing floor lane

Base 987cc53. Worktree /home/user/work/audit2_floor, probe commit 7a406f3 (unpushed). Probes: apps/manufacturing/tests_audit2_floor.py (F1 to F12), tests_audit2_floor_roles.py, tests_audit2_floor_race.py; figures in calc_audit2_floor/expected.py. Every FAIL below fails on the base, on SQLite and on PostgreSQL (checked on both). Nothing in product code or /home/user/hello-world was touched. `audit_invariants` prints "No invariant findings." `makemigrations --check` prints "No changes detected".

## Findings

### Security

**S1. The tablet login can forge the scale (F3). Blocks go-live where a scale is bridged.**
- setup_roles.py:428-432 gives the "Station" role (the shared login on a station tablet) `add_scalereading`. So does Production Supervisor (line 399). The RUNBOOK says the bridge login "can post readings and do nothing else".
- Scenario: the tablet user (login `station-lx1`) posts `/api/manufacturing/scale-readings/ {scale: SC-1, gross_kg: 106.8, stable: true}` and gets 201. It then confirms a roll with source "scale" at a bridged station. The reading counts as what the scale reported, so a weight was typed with no supervisor PIN and no reason. A bridged station exists to prevent exactly this (station_scale.py module docstring).
- Shape: a mirror gap between the typed-weight guard and the door the reading comes through. Kind: security. Probe F3ForgedScaleTests, expects 403, gets 201.
- Fix: a separate "Scale bridge" role holding only `add_scalereading`, taken off Station and Production Supervisor.

**S2. The station PIN lockout resets on any success (F1). Not a blocker, but the docstring is wrong.**
- station.py:121-131 `is_locked` stops counting at the newest success, and a test (`test_four_wrong_then_right_resets_the_count`) holds that deliberately. `issue_pin` says "the station lockout, not the digest, is what stops guessing".
- Scenario: anyone with a valid PIN (the operator, EMP-0142) and the tablet login does 4 wrong guesses, then signs in with their own PIN, and repeats. 80 wrong guesses in one 10-minute window never lock the station. A 6-digit PIN is a million guesses, and the PIN is the whole of the supervisor's approval for typed weights, voids and off-weight bundles.
- Also the digest is an unsalted HMAC of SECRET_KEY, so the database plus the key reverses every PIN. That is documented. Kind: security. Probe F1LockoutTests.
- Fix: count failures in the window regardless of a success between them, or per login.

### Books and stock

**B1. A rework recipe with no line for the failed batch is never refused (F9).**
- bom.py:279 `BillOfMaterials.check_rework` is called by nothing. Its only test calls it by hand, so the test encodes an inert guard (mistake 1).
- WorkOrder._check_rework (orders.py:1306) checks the run, not the recipe.
- Scenario: Process Engineer saves a rework recipe for tape with only 4 kg of virgin polymer per 100 kg. A run for 480 kg with `rework_of=T-BAD` releases. It draws none of T-BAD and books 480 kg of good tape from 19.2 kg of polymer. The failed 500 kg stays on the shelf at its value. This is the case the docstring names ("makes good stock out of nothing").
- Shape: inert feature plus comment-is-not-a-constraint. Kind: books. Needs a mis-built recipe, so not a blocker. Probe F9. Fix: call it from BillOfMaterials.save (or activation) and from release.

**B2. Stock promised to a backflushed run's creel can be re-batched away (F2).**
- `refuse_drawing_what_is_promised` (inventory/tracking.py:187) is called only by MaterialIssue posting (orders.py:2489). Rebatch.post (rebatch.py `free = on_hand - baled`) does not ask it.
- Scenario: 100 kg of D-1 is loaded on L-17's warp creel of a backflushed run. Rebatch D-1 into N-1 (60) and N-2 (40) posts. The run's output later cannot draw D-1, and the roll cannot be booked.
- Other named-lot outbound paths probably share it: transfer, vendor return, adjustment, subcontract send, bale. Those are the stores lane's; I did not probe them. Shape: mirror gap (one of several doors). Probe F2PromisedTapeTests.

### Rule

**R1. A weighed fabric roll can be edited and deleted over the API (F4). Blocks go-live (pay and trace).**
- `FabricRollViewSet` (views.py:1180) is a plain ModelViewSet, and `FabricRollSerializer` (serializers.py:697) lists length_m, width_mm, machine, core_weight_kg, lot, entry and specification as writable. `FabricRoll` (rolls.py:286) checks only the entry's weight in `save`, and has no `delete` guard.
- Production Supervisor holds `full` on fabricroll (setup_roles.py:391-396).
- Scenario: roll FR-260601-D-L17-01, 104.4 kg net, declared 1,006 m, variance +0.60 %. The supervisor PATCHes `length_m` to 2000 and gets 200. The stored variance and `is_metres_exception` stay at +0.60 % and False, though the true variance is now +100 %. `piecework.metres_woven` pays the weaver on `length_m`, so pay follows the edit. The same PATCH can move `machine` onto a contractor's loom. A DELETE returns success: the roll record, its trace and the weaver's credit go while the 104.4 kg stay on the shelf.
- Shape: "a posted record deleted" and "a calculation posted after its inputs moved" (the audit table). The production entry beside it is guarded; I probed it (it held). Kind: rule, with pay. Probes F4WeighedRollTests (two).
- Fix: make the roll's measured fields read-only after creation, and add `delete()` raising. Check the other roll-like models that get a viewset.

**R2. A published cost version and its standards can be edited and deleted (F7).**
- `StandardCost.save` (costing.py:~402) and `CostVersion` have no published guard. The viewsets are plain ModelViewSets.
- Scenario: version V1 is published and revalued stock at standard. A StandardCost row of V1 is then changed from 100 to 999, or the version deleted. `against_actual` and `explain` read the changed figure. The revaluation entry loses its link, since the version holds the pointer (mistake 4).
- Shape: posted is immutable. Kind: rule/books-adjacent. `item.standard_cost`, which stock is valued at, does not change, so the ledger does not move. Probes F7PublishedStandardsTests (two).

**R3. Shift codes that share a first letter stop the second shift weighing (F8). Blocks go-live for any plant naming shifts S1/S2/S3 or A1/A2.**
- Roll, doff, film-roll and process-roll codes use `shift.code[:1]` (station.py:337, station_tape.py:94, station_film.py:87, process_rolls.py:395), while the running count is per shift. Shift.code is unique only in full.
- Scenario: shifts S1 (08:00) and S2 (20:00). The first roll of S1 on L-17 is `FR-260601-S-L17-01`. The first roll of S2 the same shift-day is the same code. The Lot `(item, code)` unique constraint raises IntegrityError, a 500, and no roll can be weighed. conversion.bag_code uses the full code and is fine. Kind: crash. Probe F8.
- Fix: use the full code, or count per shift-date and letter.

**R4. A retired core type is still taken at the tape line, film line and roll-off (F11).**
- station_views.py:516, 643 and 693 look the code up with `get_object_or_404(CoreType, code=...)`. The loom exit (line 221) filters `is_active=True`.
- A doff is netted against a bobbin whose tare is out of date. Kind: rule, minor. Probe F11, returns 201 where 400 is expected.

**R5. A non-numeric bobbin count crashes at a net-frame bridged tape station (F6).**
- station_tape.py:~120 passes `tare_kg=lambda: core_type.tare_kg * int(bobbins)` to `weigh_gross` before the `int(bobbins)` try. `bobbins="eight"` raises ValueError (and None raises TypeError), which `_run` does not catch, so the response is a 500. Float 2.7 is silently cut to 2. Kind: crash, minor. Probe F6.

## Held (probed, fine)

- Production entry behind a roll: PATCH and DELETE by Production Supervisor refused (model guards hold).
- Floor roles (Station, Production Supervisor, Maintenance, Warehouse Staff, Quality Inspector) cannot read cost-versions, standard-costs, material-rates, stage-rates, quote-policies, cost-sheets or order-profitability. The tablet reads only station-reports.
- Two looms confirming on one bridged scale reading at once (PostgreSQL race(), tests_audit2_floor_race.py): exactly one roll.
- Rolling up a version with a regrind loop twice after a price moves: works (F12 passes; my theory was wrong).
- Asia/Kolkata: 405 tests of station, scale, tape, piecework, job work, dispatch, quoting, rebatch, process rolls, conversion, tape loads and outside work pass. dispatch.py reads local time correctly.
- Job-work challan: issue, void, the ceiling against the run, losses and closed-period checks all read right. Rebatch post and void use cost_of_removing and carry the residue.
- Quoting: frozen cost sheet, dated rates that cannot be edited, base-currency-only quote.

## Not reached, with what would prove it

- **Challan job worker against the step's vendor.** Nothing ties `JobWorkChallan.job_worker` to the vendor of the purchase line for the step (the operation names none; the PO line holds the pointer). ITC-04 would report goods to a party that was never paid for the work. To prove: a PO line for vendor B on the outside step, a challan to A, and a posted challan.
- **Piece pay on held or rejected output.** `bags_converted` pays all standing counts, including bundles held for the Quality Manager or later rejected; `metres_woven` caps doubted metres, `kilograms_woven` does not. To prove: a rejected bundle and a pay run (a decision for the owner).
- **Other named-lot outbound doors** against creel promises (B2).
- **Stoppage and scrap minutes** (`book_stoppage`) are unbounded by the shift's length.
- **`correct` flag on the tape count** is read as truthiness (`bool("false")` is True) where `flag()` is the rule, if the station page ever sends a string.
- **PostgreSQL ordering** of loops that number or write: not swept.
- **A plant month** (thirty rolls, partial challans, a correction on the last and first day with a closed period between): not run.
- **Browser flows** as station and supervisor logins: not run.
- **Inert helpers** found by call count and tested only by their own test: `FabricRoll.kilos_for`, `Machine.rate_per_hour`, `StandardCost.share_of`, `BagSpecification.printed_area_sqm`. Minor, besides check_rework (B1).
- Not probed in depth: station_coat, station_film, energy, maintenance, complaints, bales, woven.py spec arithmetic, and the dispatch committing from a stale build (commit re-locks the rows, but placements are decided before the lock).

## Order of fixes

R1, S1, R3 first (go-live). Then B1, B2, R2, S2, and R4/R5 as minor.
