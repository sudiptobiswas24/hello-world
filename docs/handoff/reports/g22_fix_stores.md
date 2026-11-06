# fix_stores (g22): report

Worktree: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/fix_stores, based on f2fb5de. Nothing pushed.
Calculations and logs: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/calc_fix_stores/

## Commits (oldest first)

| Commit | Ids | What |
|---|---|---|
| 18fd723 | O210 | `StockMovement.moved_from` (migration 0039, with backfill); move_stock() and a step's reversal record it; follow_value follows it whoever moved the goods; audit check `arrivals_without_their_departure` |
| 85aad23 | O133 | inventory registry `register_lot_commitment`/`committed_elsewhere`; manufacturing registers `on_creels_undrawn(lot, warehouse)`; allocate() subtracts it |
| 6e0afbe | O134 | allocate()'s shortfall message normalised |
| fe30555 | O29 | `CLOSE_CHECKS`/`register_close_check` in accounting; assets registers `depreciation_owed`; `AccountingPeriod.close()` refuses |
| 530ee45 | O34 | `assets.register_against_ledger`; health probe "Fixed assets agree with the ledger"; demo loader brings its typed-in assets in as opening balances |
| 0ffe4b0 | O12, O13, O14 | in service only from a day that has come; go-live depreciation checked and fixed after creation; status changes only through the lifecycle methods, admin read-only |
| 1940ab7 | O11 | `StandingRouteMoves`: a receipt's hop counts only while its transfer stands |
| e0e94be | O23 | adjustment post/void answer with the adjustment read again |
| cc7b26c | O26 | `POST /api/purchasing/bills/{id}/capitalise/ {"line", "category", ...}`, `accounting.post_journalentry` |
| 4b13acf | O225 | deploy/scale_bridge (program, RUNBOOK.md, example config, systemd unit, recorded frames); tests in apps/manufacturing/tests_scale_bridge.py |
| fdd25ce | O27 | apps/inventory/tests_migrations.py, 0030 and 0037 classes (both passed: 0030 in 840 s, 0037 in the first run) |

O12, O13 and O14 are one commit: three rules in the same save()/clean()/place_in_service region of apps/assets/models.py, listed as one item in the brief.

## The test that failed without each fix

- O210, purchasing.tests_returns_and_landed.LandedWhereTheGoodsAreTests:
  - `test_released_after_the_goods_were_cleared_out_of_inspection_it_comes_off_the_stock_room`: `(Decimal('130.00'), Decimal('0.00')) != (Decimal('50.00'), Decimal('0.00'))`
  - `test_freight_on_goods_cleared_out_of_inspection_and_issued_to_a_run_is_production_cost`: `(Decimal('52'), Decimal('30'), Decimal('48')) != (Decimal('52.00'), Decimal('78.00'), Decimal('0'))`
  - Backfill: inventory.tests_migrations.EachArrivalNamesItsDepartureTests (passed). Audit check: core.tests_audit_stock_shapes.ArrivalsWithoutTheirDepartureTests.
- O133, manufacturing.tests_tape_loads...test_a_pick_naming_no_doff_does_not_choose_one_on_a_creel: `Lists differ: [('D-1', Decimal('50'))] != [('D-2', Decimal('50'))]`
- O134, ...test_the_shortfall_is_said_in_figures (figures as str()): `'Only 300 of' not found in "['Only 300.0 of TAPE-FOR-F-60 ...; 0.5 short.']"`
- O29, assets.tests.APeriodClosesWithItsDepreciationTests: `ValidationError not raised`; through the API as the Controller: `200 != 400`
- O34, assets.tests.TheRegisterFootsToTheLedgerTests: `StopIteration` (no such check)
- O12/O13/O14, assets.tests NotIntoServiceFromADayToComeTests, DepreciationBroughtInAtGoLiveTests (3), ItsStatusIsWhatHappenedToItTests: each `ValidationError not raised`
- O11, purchasing.tests_receipt_routing...test_a_put_away_cancelled_puts_them_back_in_the_bay: `Decimal('0.0000') != Decimal('40')`
- O23, inventory.tests_adjustments...test_the_store_head_posts_and_voids_it_through_the_api: `Tuples differ: (True, '0.00') != (True, '-3333.33')`
- O26, assets.tests_capitalisation.CapitalisedThroughTheApiTests: `404 != 403`
- O225: no "without"; mutations killed: unstable frames let through (2 tests fail), a weight sent again (`['50', '50'] != ['50']`, and the end-to-end readings differ), OSError not retried (3 errors).
- O27: test-only. 0037 passed; 0030 passed when re-run after a wrong migration name in the first run (NodeNotFoundError).

## Runs

- inventory, purchasing, manufacturing, quality (fastsettings, --exclude-tag migration): 3572 tests OK (skipped=1).
- web, core, assets, accounting, imports: 1063 tests, 1 failure, the demo loader's books (the new O34 check found the demo's typed-in assets on an empty plant account); fixed in the demo, tests_load_demo re-run OK. The whole lane was not run again after that fix.
- audit_invariants: no findings (after adding `assets.register_against_ledger` to CALLED_FROM_OUTSIDE: its caller, apps/web/health.py, is not audited). makemigrations --check: no changes.
- Not run: PostgreSQL; the browser tests (no frontend change); `--tag migration` beyond my own inventory.tests_migrations (0039 added: the integrator's mig lane should run it all).
- Audit runs, makemigrations --check and the module runs were on the final tree, not on each commit. Each intermediate commit was built from its own file versions (a script), and the per-fix tests ran while those files stood as they are in that commit.

## What I could not do (budget, about 150 calls)

Not started: O232 (coating check to its roll), O231 (page-wide gate, N+1), O241 (inspection screen offers by role, browser tests), O242 (Inspection.post lock order, race()), O203 (core working-day registry), O202 (created_by on cost sheet, certificate, price-variation bill), O45 and O127 (duplicate admin mixins, FROZEN_AT_RELEASE). The shared rules the brief gives me for those (the page-wide gate, the core working-day registry, the one posted-document admin rule) are not built. Nobody else should build them in the meantime.

## Seen and not fixed

1. books: a rebatch (apps/manufacturing/rebatch.py `_take_out`/`_bring_in`) writes TRANSFER_OUT/TRANSFER_IN pairs on one shelf with no link. follow_value stops at the rebatch's TRANSFER_OUT and calls the value gone, so landed freight on a batch that was rebatched and later issued to a run is split as cost of sales. Example: 80 of freight on 10 kg rebatched into a new lot, 6 kg of that lot issued to a run, then the freight is released: the run's 48 comes back from cost of sales. The new audit check exempts rebatch.py, with its reason. It needs a pooled-follow rule (value in proportion to what each output lot got).
2. rule: `planning.mrp` `lots_at()` users (apps/planning/mrp.py:141, :165) still count tape on a backflushed run's creels as cover. allocate() now subtracts it (O133); planning does not. Kind: rule.
3. rule: `on_creels_undrawn(lot)` without a warehouse (the load check, apps/manufacturing/tape_loads.py:110) still subtracts creels anywhere from the on-hand in the station's warehouse. Two stations in different warehouses loading one lot cannot happen while a lot is in one place. Minor.
4. design: a fixed asset typed in by hand and put into service posts nothing (apps/assets/models.py `place_in_service`). Its cost reaches the ledger only through an opening-balance journal written by hand. The new footing reports it, as it reported the demo plant (register 24,450,000.00, ledger 0.00). Should putting a typed-in asset into service ask for the account its cost comes from? That is a question for the owner.
5. O13: `FIXED_AT_GO_LIVE` now refuses any edit of depreciated_before/opening_depreciation on a hand draft after it is created. A typo on an imported asset is corrected by registering it again. That is my reading of the field's own help text ("never after"); the integrator should confirm it.

## Shared rules

- Mine and built: `follow_value`'s reach (`moved_from`), and the scale bridge.
- Mine and not built: the page-wide gate, the core working-day registry, the one posted-document admin rule.
- New, and needing an owner: `inventory.tracking.register_lot_commitment` (what of a batch is promised and not drawn) and `accounting.models.register_close_check` (the period-close check the brief named).
- Touched in core (fix_core's area), one place each:
  - apps/core/management/commands/audit_invariants.py: the new check, and one CALLED_FROM_OUTSIDE line;
  - apps/core/tests_audit_stock_shapes.py: a class appended.

# Second round

Same worktree, on top of fdd25ce. Tip c70d2b8. Nothing pushed. Commits, oldest first:

| Commit | Ids | What |
|---|---|---|
| 87e4866 | O232 | `CoatingCheck.mount` (migration 0084, blank on old rows = the old run-wide hold); `off_coatings(order, mount)`: only checks taken on a roll in the converted roll's chain, or tied to none, hold a bundle |
| a4f4513 | O231 | page-wide gate `inventory.tracking.held_because_page` + `LOT_PAGE_QUESTIONS`; quality's `prefill_page`; provider page forms (`for_page`) for rebatch sources, held inputs, assemblies; lot list sums on hand once. 117 queries/10 batches and 305/30 before; 43 and 41 after |
| bb97940 | O242 | `Inspection.post` holds its batches first (`held_for_post`, key order), as void and escalate do; race on PostgreSQL |
| 56a0549 | O241 | `releasing_is_theirs` on the inspection; the Decision box offers concession, and a release over a holding verdict, only to `quality.decide_inspection`; browser test as both roles |
| cdd9199 | O203 | core `register_working_day` / `is_working_day` / `next_working_day`; hr registers `plant_works_on`; quality asks core |
| 3e5e95c | O202 | `by=` on quoting.cost, certificates.issue, bill_variation; views pass request.user |
| c70d2b8 | seen #2 (MRP) | `inventory.tracking.free_lots_at`, shared by allocate() and MRP's unusable_on_hand/expiring_unused |

## The test that failed without each fix

- O232, manufacturing.tests_process_rolls.ACoatingCheckHoldsItsOwnRollTests: `(True, Disposition.AWAITING) != (True, Disposition.ACCEPT)`
- O231, manufacturing.tests_review_qrecall.TheColumn.test_a_page_of_batches_asks_the_gate_once_for_the_page: `305 not less than or equal to 119 : 117 queries for 10 batches, 305 for 30`
- O242, e2e.tests_races.QualityDecisionRaceTests.test_a_draft_posted_as_it_is_voided_is_not_a_deadlock (PostgreSQL): `['refused: deadlock detected ...']`
- O241, web.tests_browser_quality.TheDecisionOfferedIsTheServersTests on the old screen: `[..., 'Accepted by concession', 'Held for rework', 'Rejected'] != [..., 'Held for rework', 'Rejected']`
- O203, quality.tests_decisions.APlantHolidayIsNotAWorkingDayTests without hr's registration: `[('QC-2026-00001', '2026-06-02')] != [('QC-2026-00001', '2026-06-03')]`
- O202, without the views passing the person: `'NoneType' object has no attribute 'username'` (cost sheet), `False is not true` (certificate, as the Quality Inspector), `unexpectedly None` (price-variation bill)
- MRP: manufacturing.tests_tape_loads...test_planning_does_not_count_a_doff_on_a_creel_as_cover: `Decimal('0') != Decimal('140')`

First lane asked for: web, core, assets, accounting, imports on fdd25ce: 1063 tests OK (skipped=119).

## Resumed after the usage limit and a restart: three more commits on c70d2b8

| Commit | Ids | What |
|---|---|---|
| 6114988 | O45 | core's PostedImmutableAdminMixin (+ inline, `posted_field`) is the one rule. Inventory's, manufacturing's two, and quality's ReadingInline copies are gone, and every caller is still refused. New audit check `posted_admin_rule_copied`; on the old tree it reports inventory/admin.py:115, :120 and manufacturing/admin.py:660-677 |
| 51f8b4f | O127 | WorkOrder declares FROZEN_ONCE_MOVED and asks refuse_changing_what_moved; FROZEN_AT_RELEASE on the run is gone |
| 04aa19f | seen #9 (rebatch) | `inventory.costing.register_pooled_move`; manufacturing registers `rebatch.pooled_into`; follow_value follows a rebatch's take-out into the batches made, by value share. Failed without it: `Lists differ: ['gone'] != []` |

O241's browser test ran and passed in the first part of this round (TheDecisionOfferedIsTheServersTests, 4 browser tests OK, chromium, fresh build). It is committed in 56a0549.

## Not done

- Seen #8, an asset typed in by hand as an opening balance (the integrator's decision): not started, out of budget.
  - Nothing stores the books' opening date or an opening-balances equity account. The opening balances import takes both per run, as options (apps/imports/importer.py `_opening_balances`, `--date`). So the rule first needs a home for both: a Company field (core, fix_core's area) or an assets-side setting. That is the integrator's call.
  - The demo still brings its machines in by the journal added in 530ee45.
- O127, second half: the no-order-line branch of BillLine.accrual() (purchasing) is still there, unreachable.
- O45: InspectionAdmin itself is not read-only once posted; only its readings are. That was never one of the copies.
- Seen #9: no end-to-end release through purchasing on a re-batched receipt. The test asks where_value_is, which the release takes.

## Notes for the integrator

- O232: a check taken with no roll recorded on the coater, or a bundle counted with no roll mounted at the converter, still holds every bundle of the run (the safe old meaning). Migration 0084 is AddField only, so no data migration test.
- O231: `open_recall` is read off the page only when the page filled it. A recall that writes recalls inside its own question still reads them fresh.
- O241: on a re-inspection of a batch whose verdict holds it, an Inspector is offered only rework or reject. Blank would default to accept on a pass, and the server refuses that release.
- O202: the cost sheet and price-variation API tests run as a superuser (existing fixtures). The certificate runs as the Quality Inspector.
- O203 touches core (models.py) and hr (apps.py, calendars.py), fix_core's area, as the brief gave me the registry.

## Shared rules

- Mine:
  - `follow_value`'s reach (`StockMovement.moved_from`);
  - the page-wide gate (`held_because_page`, `register_lot_page_question`, `provider.for_page`);
  - the core working-day registry (`register_working_day`);
  - the scale bridge;
  - `inventory.tracking.register_lot_commitment` / `committed_elsewhere` / `free_lots_at`;
  - `accounting.models.register_close_check`.
  - the one posted-document admin rule (core.admin_mixins.PostedImmutableAdminMixin, built in 6114988);
  - `inventory.costing.register_pooled_move`.

## Runs on c70d2b8

- inventory, manufacturing, quality, assets, accounting, planning, core, apps.web (fastsettings, browser tests with chromium and a fresh build): 3780 tests OK.
- --tag migration, inventory and assets: 4 tests OK.
- apps.e2e.tests_races on PostgreSQL: 98 tests OK.
- audit_invariants no findings, makemigrations --check no changes, at each commit.

## Runs on 04aa19f (the tip)

- inventory, manufacturing, quality, assets, accounting, planning, core, web, purchasing, sales, hr (fastsettings, --exclude-tag migration, browser tests with chromium and a fresh `npm run build`): 6473 tests OK.
- apps.e2e.tests_races on PostgreSQL: 98 tests OK.
- --tag migration, inventory and assets: 4 tests OK on c70d2b8. Not re-run: nothing since touches a migration.
- audit_invariants no findings; makemigrations --check no changes.

# Third round

On top of 04aa19f; tip 162b5ca. Nothing pushed.

| Commit | Item | What |
|---|---|---|
| 48d2f06 | 1, an asset typed in by hand is an opening balance | `accounting.BooksOpening` (migration 0031): the day the books opened and the opening-balances equity account. Set once; changed only while nothing is posted on or before the old or new day. `place_in_service` of a hand asset posts Dr asset / Cr opening at cost and Dr opening / Cr accumulated for opening depreciation, on that day; one acquired after it is refused, pointing to the bill's Capitalise. Reverse `remove_opening` (API `remove-opening`) works until anything else is posted against it. The opening-balance and asset-register imports read the setting and refuse a disagreeing `--date`; the opening-balance import refuses the asset categories' accounts. The demo opens its books on 30 June 2026, and the hand journal of 530ee45 is gone |
| a8f43c6 | 3, InspectionAdmin read-only once posted | under PostedImmutableAdminMixin |
| 8861be8 | 2, O127's dead branch | removed, and replaced by a refusal in words |
| 162b5ca | 4, end-to-end release on a re-batched receipt | purchasing test |

## The test that failed without each

- Item 1: assets.tests TypedInIsAnOpeningBalanceTests (the four lines; the refusal; the reverse through the API as the Controller, refused after depreciation) and TheBooksOpenOnceTests; imports OpeningBalancesTests and tests_cutover refusals.
  - Without the posting, the demo's health check read register 24,450,000.00 against ledger 0.00, the first-round finding.
  - Changed tests, each because it encoded the defect or typed in an asset after the books opened:
    - tests_audit, the reinstated asset: plant 0 is now 12,000;
    - my O34 test no longer posts a journal by hand;
    - three tests open their books later (the May press, and the two closed-quarter assets).
  - asset_register lists a hand asset from its acquisition date: its entry is the books' opening, not its own capitalisation.
- Item 2: no "without". Proof that nothing reaches the branch:
  - the argument: accrual() is called only when clears_grni() is true, and such a line is refused by _refuse_paying_for_goods_on_order while goods wait unbilled;
  - a tripwire (AssertionError in the branch) through purchasing, assets and the demo: 1336 tests OK, 0 hits.
- Item 3: core.tests_audit_stock_shapes.OnePostedDocumentAdminRuleTests: `'Inspection' not found in [...]`.
- Item 4: purchasing.tests_returns_and_landed...test_released_after_the_goods_were_rebatched_it_comes_off_the_batches_made, failed without rebatch.pooled_into registered: `Decimal('130.00') != Decimal('50.00')`.

## Notes

- BooksOpening is set through the admin and in code (the demo, the tests); it has no API or screen yet. A fresh install must set it before typing in an asset or importing opening balances, and both refusals say so.
- remove_opening reverses on the opening day, or on `on_date` (correction_date) when that month has closed.
- Shared rule added: `accounting.models.BooksOpening` (one row, `BooksOpening.get()`).

## Runs on 162b5ca

- assets, accounting, quality, purchasing, inventory, web (whole, browser tests with chromium and a fresh `npm run build`), core and imports: 2895 tests OK, none skipped.
- --tag migration for assets and accounting (accounting 0031 added): 3 tests OK.
- audit_invariants no findings and makemigrations --check no changes, at each commit.
- Not re-run this round: the races on PostgreSQL. 98 OK on 04aa19f. Since then the lock-taking code changed only in remove_opening (serialised, like the asset's other lifecycle methods).

# Review fixes

On top of 162b5ca, with the review's probes cherry-picked first (a0e83e7). Tip 33ca523. Nothing pushed.

| Commit | Finding | What |
|---|---|---|
| 41dd71d | F1, F10 (the blocker) | `pooled_into` follows a re-batch's take-out whether or not it was voided since, and a void's take-out back into its sources (the void's bring-in, under the re-batch's number). A value-only movement is no use of a batch, so a re-batch voids after a release |
| a1237af | F2 | MRP nets `on_its_creels(order, item)` (tape_loads.on_the_creels, the loaded-less-drawn figure allocate() takes off) from a backflushed run's own demand |
| e183397 | F4 | a hand asset in service before the books opened carries what its schedule owed up to that day into `opening_depreciation` (posted in the opening entry), and `depreciated_before` becomes the opening day |
| 450f481 | F5 | BooksOpening is refused when first set on or after a day something is posted |
| 3ca2b63 | F6 | planning registers `plant_is_open` (PlanningSettings.working_days and holiday_region, through plant_calendar) into core's working-day registry. Any False shuts a day, a True opens it, otherwise Monday to Friday. hr's company-wide holiday now answers False or nothing |
| d8731a6 | F7, F8, F9 | the bridge posts `gross_kg` (GS) or `net_kg` (NT), keyed by scale, run, sequence, kind and weight. ScaleReading keeps net_kg and a unique key (migration 0085), and a key it has kept is answered with the first reading. gross_weight adds the caller's tare to a net reading, so it is not tared twice. poster() refuses anything but https or http to localhost |
| 33ca523 | F3 | `promised_to` / `refuse_drawing_what_is_promised` in inventory.tracking (the registry now answers [(holder, quantity)]); a material issue to a run refuses a named doff on another run's creels, naming the run |

Every one of the review's probes passes. The failing ones are renamed by what they protect.

Probes changed beyond a rename:
- assets a7: under F4, the asset in service on 31 December carries December's 250 in its opening balance. Opening is now -23,750.00, with accumulated -250.00; the probe asserted -24,000.00 from before that decision.
- The scale harness (`Feed.send`) answers a kept key as the server now does.
- The harness's `frames()` lists gross posts only.
- s6 also asserts the net post.
- The F6 own-region probe also files a holiday in another region, which does not shut the plant.
- q2 asserts the run's name.

Own tests changed:
- tests_scale_bridge: grammes are posted as gross; an NT frame as net; one key per settled weight.
- tests_station_scale: new tests for a net reading not tared again, and for a keyed re-post answered as the first.

## Runs on 33ca523

- inventory, manufacturing, planning, quality, assets, accounting, core, hr, web (whole, browser tests with chromium and a fresh build), purchasing and imports: 5580 tests, 1 failure.
  - The failure is web.tests_browser_lut...test_the_lut_is_kept_and_what_was_seeded_is_reviewed (O239, not mine). It passes alone (1 OK).
  - Seen, not fixed: under the parallel run its `expect(...).to_contain_text("Reviewed")` can be met before the "Mark reviewed" POST lands, so the database read that follows is decided by speed (apps/web/tests_browser_lut.py:41-44).
- --tag migration, manufacturing (0085 added): 4 tests OK.
- The races on PostgreSQL: 98 OK.
- audit_invariants no findings; makemigrations --check no changes.
- Not re-run: --tag migration for assets and accounting. No migration of theirs changed since 162b5ca, where they were 3 OK.
